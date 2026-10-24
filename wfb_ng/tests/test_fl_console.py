"""Issue 01 console service and HTTP behavior at the public seams."""
import http.client
import json
import socket
import time
import threading
from unittest.mock import patch

import pytest

from wfb_ng.fl.server_daemon import ServerDaemon, ServerDaemonConfig, ServerState
from wfb_ng.tests.test_fl_client_daemon import MockNetworkAdapter


def make_daemon(**config):
    return ServerDaemon(ServerDaemonConfig(ipc_port=0, enable_link_process=False,
                        enable_control_plane=False, **config), MockNetworkAdapter())


def test_snapshot_detached_and_version_tracks_facts_not_time():
    daemon = make_daemon()
    first = daemon.console.snapshot()
    assert first['execution_result'] == 'none'
    assert first['recovery_state'] == 'not_required'
    assert daemon.console.snapshot()['state_version'] == first['state_version']
    daemon.server_state = ServerState.IDLE
    second = daemon.console.snapshot()
    assert second['state_version'] > first['state_version']
    second['server']['radio']['channel'] = 149
    assert daemon.console.snapshot()['server']['radio']['channel'] == 157
    daemon.publish_event({'type': 'NODE_HEARTBEAT', 'node_id': 1})
    assert daemon.console.snapshot()['events'] == []


def test_web_handler_does_not_expose_ipc_writes_and_unknown_methods_are_json():
    from wfb_ng.fl.console_http import ConsoleRequestHandler
    daemon = make_daemon()
    from wfb_ng.fl.console_http import ConsoleHTTPServer
    server = ConsoleHTTPServer(('127.0.0.1', 0), ConsoleRequestHandler)
    server.application = daemon.console
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        for method, path, expected, code in [
            ('POST', '/api/v1/jobs/start', 410, 'ROUTE_REPLACED'),
            ('POST', '/api/v1/radio/reconfigure', 410, 'ROUTE_REPLACED'),
            ('POST', '/api/v1/jobs/abort', 410, 'ROUTE_REPLACED'),
            ('GET', '/api/v1/unknown', 404, 'NOT_FOUND'),
            ('BREW', '/api/v1/unknown', 404, 'NOT_FOUND'),
            ('PATCH', '/api/v1/state', 405, 'METHOD_NOT_ALLOWED'),
        ]:
            conn = http.client.HTTPConnection(*server.server_address)
            conn.request(method, path)
            response = conn.getresponse()
            assert response.status == expected
            assert response.getheader('Content-Type').startswith('application/json')
            assert json.loads(response.read())['error']['code'] == code
            conn.close()
    finally:
        server.shutdown()
        worker.join()
        server.server_close()


def test_snapshot_blocks_missing_runtime_resources_but_not_management_web():
    daemon = make_daemon()
    daemon.server_state = ServerState.IDLE
    register_idle(daemon)
    state = daemon.console.snapshot()
    assert state['server']['can_start_job']
    assert state['server']['management_web']['status'] == 'management_web_unavailable'
    from dataclasses import replace
    daemon.config = replace(daemon.config, enable_link_process=True)
    blockers = daemon.console.snapshot()['server']['start_blockers']
    assert {'TUN_NOT_READY', 'LINK_NOT_READY'} <= {b['code'] for b in blockers}


def _heartbeat():
    from wfb_ng.fl.control import NodeHeartbeat
    return NodeHeartbeat(node_id=1, state='IDLE', elapsed_ms=0,
                         current_channel=157, txpower_dbm=12, uplink_mcs=6)


def test_snapshot_recovery_observes_existing_terminal_steps_without_client_barrier():
    from unittest.mock import Mock
    daemon = make_daemon()
    daemon.server_state = ServerState.RUNNING
    daemon.active_job = {'job_id': 'test-job', 'target_nodes': [1]}
    role = Mock()
    observed = []
    role.close.side_effect = lambda: observed.append(daemon.console.snapshot())
    daemon.server_role = role
    diagnostic = daemon.event_bus.subscribe()
    with patch.object(daemon.control_plane, 'broadcast_downlink',
                      side_effect=lambda message: observed.append(daemon.console.snapshot())):
        daemon.abort_job()
    assert all(state['recent_job']['execution_result'] == 'aborted' for state in observed)
    assert all(state['recent_job']['recovery_state'] == 'recovering' for state in observed)
    assert all(not state['server']['can_start_job'] for state in observed)
    final = daemon.console.snapshot()
    assert final['recent_job']['recovery_state'] == 'ready'
    assert daemon.server_state == ServerState.IDLE
    # Recovery.ready describes existing local cleanup, not a Client release barrier.
    assert not final['server']['can_start_job']  # no ready nodes
    assert diagnostic.get_nowait()['type'] == 'JOB_ABORTED'
    assert diagnostic.empty()
    daemon.active_job = {'job_id': 'fail-job', 'target_nodes': [1]}
    daemon.server_state = ServerState.RUNNING
    with patch.object(daemon.control_plane, 'broadcast_downlink', side_effect=OSError('wireless gone')):
        with pytest.raises(Exception):
            daemon.abort_job()
    blocked = daemon.console.snapshot()
    assert blocked['recent_job']['execution_result'] == 'aborted'
    assert blocked['recent_job']['recovery_state'] == 'blocked'
    assert not blocked['server']['can_start_job']
    assert diagnostic.empty()


def test_address_ownership_rejects_wireless_and_tun_and_accepts_ethernet():
    from wfb_ng.fl.console_http import validate_management_address
    class Message(dict):
        def get_attr(self, key):
            return self.get(key)
    for interface, kind, accepted in [('wlx123', None, False), ('other-radio', None, False),
                                      ('virtual0', 'tun', False), ('fl-s', None, False),
                                      ('eth0', None, True)]:
        with patch('wfb_ng.fl.console_http.IPRoute') as iproute:
            ip = iproute.return_value.__enter__.return_value
            ip.get_addr.return_value = [Message(index=2, IFA_ADDRESS='192.0.2.1')]
            ip.get_links.return_value = [Message(flags=0, IFLA_IFNAME=interface,
                IFLA_LINKINFO=Message(IFLA_INFO_KIND=kind))]
            if accepted:
                validate_management_address('192.0.2.1', 'fl-s', 'other-radio')
            else:
                with pytest.raises(OSError):
                    validate_management_address('192.0.2.1', 'fl-s', 'other-radio')


def register_idle(daemon):
    hb = _heartbeat()
    from dataclasses import replace
    hb = replace(hb, state='HUNTING')
    daemon.control_plane.registry.update_heartbeat(hb, ('10.80.0.11', 9001), daemon.radio_config)
    hb = replace(hb, state='IDLE')
    daemon.control_plane.registry.update_heartbeat(hb, ('10.80.0.11', 9001), daemon.radio_config)


def test_lifecycle_changes_advance_version_even_between_reads():
    daemon = make_daemon()
    daemon.server_state = ServerState.IDLE
    before = daemon.console.snapshot()['state_version']
    daemon.server_state = ServerState.SURVEYING
    daemon.server_state = ServerState.IDLE
    assert daemon.console.snapshot()['state_version'] >= before + 2
    daemon.server_state = ServerState.PREPARING
    preparing = daemon.console.snapshot()['state_version']
    daemon.server_state = ServerState.SURVEYING
    assert daemon.console.snapshot()['state_version'] > preparing


def test_web_retries_after_bind_failure_and_stop_releases_socket():
    from wfb_ng.fl.console_http import ManagementWebListener
    blocker = socket.socket()
    blocker.bind(('127.0.0.1', 0))
    blocker.listen()
    port = blocker.getsockname()[1]
    failed, ready = threading.Event(), threading.Event()
    def status(state, error):
        if state == 'management_web_unavailable':
            failed.set()
        if state == 'ready':
            ready.set()
    listener = ManagementWebListener('127.0.0.1', port, make_daemon().console,
                                     lambda host: None, status)
    listener.start()
    try:
        assert failed.wait(3)
        blocker.close()
        assert ready.wait(3)
        conn = http.client.HTTPConnection('127.0.0.1', port)
        conn.request('GET', '/api/v1/state')
        assert conn.getresponse().status == 200
        conn.close()
    finally:
        blocker.close()
        listener.stop()
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(('127.0.0.1', port))


def test_stop_racing_bind_cannot_leave_listener_socket():
    from wfb_ng.fl.console_http import ManagementWebListener
    import socket
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    validating, release = threading.Event(), threading.Event()
    def validate(host):
        validating.set()
        assert release.wait(3)
    listener = ManagementWebListener('127.0.0.1', port, make_daemon().console,
                                     validate, lambda state, error: None)
    listener.start()
    assert validating.wait(3)
    stopping = threading.Thread(target=listener.stop)
    stopping.start()
    release.set()
    stopping.join(3)
    assert not stopping.is_alive()
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', port))


def test_no_management_address_does_not_stop_daemon_ipc():
    daemon = make_daemon()
    daemon.start()
    try:
        conn = http.client.HTTPConnection('127.0.0.1', daemon.actual_ipc_port)
        conn.request('GET', '/api/v1/status')
        response = conn.getresponse()
        state = json.loads(response.read())
        assert response.status == 200
        assert state['server_state'] == 'IDLE'
        assert state['management_web']['error'] == 'WEB_HOST_NOT_CONFIGURED'
        conn.close()
    finally:
        daemon.stop()


def test_packaged_page_renders_errors_retry_and_refresh_with_existing_node():
    import shutil
    import subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    node = shutil.which('node')
    assert node, 'Existing Node dependency required for packaged page behavioral test'
    result = subprocess.run([node, str(Path(__file__).with_name('console_page_test.js')),
                             str(root / 'wfb_ng/fl/static/console.js')],
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


def test_local_recovery_failure_does_not_overwrite_execution_error_or_core_idle():
    from unittest.mock import Mock
    daemon = make_daemon()
    daemon.server_state = ServerState.RUNNING
    daemon.active_job = {'job_id': 'failed-execution', 'target_nodes': [1]}
    daemon.server_role = Mock()
    daemon.server_role.close.side_effect = OSError('cleanup failure')
    with patch.object(daemon.control_plane, 'broadcast_downlink'):
        daemon._on_job_failed('failed-execution', RuntimeError('original execution failure'))
    job = daemon.console.snapshot()['recent_job']
    assert job['execution_result'] == 'failed'
    assert job['recovery_state'] == 'blocked'
    assert job['error'] == 'original execution failure'
    assert job['recovery_error'] == 'ROLE_CLOSE_FAILED'
    assert daemon.server_state == ServerState.IDLE


def test_offline_transition_advances_version_and_records_one_key_event():
    import time
    daemon = make_daemon()
    register_idle(daemon)
    online = daemon.console.snapshot()
    with patch('wfb_ng.fl.control.time.monotonic', return_value=time.monotonic() + 20):
        offline = daemon.console.snapshot()
        again = daemon.console.snapshot()
    assert offline['state_version'] > online['state_version']
    assert again['state_version'] == offline['state_version']
    assert [e['type'] for e in offline['events']] == ['NODE_OFFLINE']
    assert offline['nodes'][0]['online'] is False


def test_console_job_excludes_local_runtime_paths_and_snapshots_are_independent():
    daemon = make_daemon()
    daemon.active_job = {'job_id': 'job', 'model_path': '/private/model.bin', 'target_nodes': [1], 'rounds': 3}
    first = daemon.console.snapshot()
    assert 'model_path' not in first['job']
    first['job']['target_nodes'].append(2)
    assert daemon.console.snapshot()['job']['target_nodes'] == [1]


@pytest.mark.parametrize('host', ['0.0.0.0', '127.0.0.1', '127.1.2.3', '10.80.0.1', 'not-an-address'])
def test_web_config_rejects_forbidden_addresses(host):
    from wfb_ng.fl.errors import FLRuntimeError
    with pytest.raises(FLRuntimeError):
        ServerDaemonConfig(web_host=host)


def test_web_port_is_fixed_even_without_host():
    from wfb_ng.fl.errors import FLRuntimeError
    with pytest.raises(FLRuntimeError):
        ServerDaemonConfig(web_port=9090)


def test_http_static_schema_host_and_method_boundaries():
    from wfb_ng.fl.console_http import ConsoleHTTPServer, ConsoleRequestHandler
    server = ConsoleHTTPServer(('127.0.0.1', 0), ConsoleRequestHandler)
    server.application = make_daemon().console
    server.expected_host = f'127.0.0.1:{server.server_port}'
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    def request(method, path, headers=None):
        conn = http.client.HTTPConnection(*server.server_address)
        conn.request(method, path, headers=headers or {})
        response = conn.getresponse()
        payload = response.read()
        result = response.status, dict(response.getheaders()), payload
        conn.close()
        return result
    try:
        for path, content_type in [('/', 'text/html'), ('/assets/console.js', 'text/javascript'),
                                    ('/assets/console.css', 'text/css')]:
            status, headers, body = request('GET', path)
            assert status == 200 and headers['Content-Type'].startswith(content_type)
            assert body
            assert 'Access-Control-Allow-Origin' not in headers
        status, headers, body = request('GET', '/api/v1/state')
        state = json.loads(body)
        assert status == 200 and headers['Cache-Control'] == 'no-store'
        assert {'schema_version','instance_id','state_version','generated_at','server','nodes','job','events'} <= state.keys()
        status2, _, body2 = request('GET', '/api/v1/status')
        assert status2 == 200
        assert json.loads(body2)['state_version'] == state['state_version']
        for path in ['/api/v1/state/', '/assets/../console.py', '/assets/%2e%2e/console.py', '/unknown.html']:
            assert request('GET', path)[0] == 404
        status, headers, body = request('CONNECT', '/api/v1/state')
        assert status == 405 and headers['Allow'] == 'GET'
        assert json.loads(body)['error']['request_id']
        assert request('GET', '/api/v1/state', {'Host':'evil.example'})[0] == 400
        with patch.object(server.application, 'snapshot', side_effect=RuntimeError('/private/path')):
            status, _, body = request('GET', '/api/v1/state')
        assert status == 503
        assert json.loads(body)['error']['code'] == 'STATE_UNAVAILABLE'
        assert b'/private/path' not in body
    finally:
        server.shutdown()
        worker.join()
        server.server_close()


def test_snapshot_read_and_publish_is_atomic_with_domain_mutation():
    from wfb_ng.fl.console import ConsoleApplicationService
    daemon = make_daemon()
    source_lock = threading.RLock()
    reading, release, writer_started = threading.Event(), threading.Event(), threading.Event()
    raw = daemon.get_status_report()
    first_read = True
    def read():
        nonlocal first_read
        if first_read:
            first_read = False
            reading.set()
            assert release.wait(3)
        return raw
    application = ConsoleApplicationService(read, source_lock)
    results = []
    reader = threading.Thread(target=lambda: results.append(application.snapshot()))
    reader.start()
    assert reading.wait(3)
    def mutate():
        writer_started.set()
        with source_lock:
            raw['server_state'] = 'IDLE'
            results.append(application.snapshot())
    writer = threading.Thread(target=mutate)
    writer.start()
    assert writer_started.wait(3)
    release.set()
    reader.join(3)
    writer.join(3)
    assert not reader.is_alive() and not writer.is_alive()
    assert [s['server']['state'] for s in results] == ['starting', 'idle']
    assert results[1]['state_version'] > results[0]['state_version']
    assert application.snapshot()['server']['state'] == 'idle'


def test_instance_restart_clears_job_and_events_and_key_history_is_bounded():
    first = make_daemon()
    first.active_job = {'job_id':'old-job', 'target_nodes':[1]}
    first.publish_event({'type':'JOB_STARTED', 'job_id':'old-job'})
    first.publish_event({'type':'JOB_STARTED', 'job_id':'old-job'})
    assert len(first.console.snapshot()['events']) == 1
    for index in range(105):
        first.publish_event({'type':'ROUND_STARTED', 'job_id':'old-job', 'round_id':index})
    previous = first.console.snapshot()
    assert len(previous['events']) == 100
    assert previous['events'][0]['round_id'] == 5
    restarted = make_daemon().console.snapshot()
    assert restarted['instance_id'] != previous['instance_id']
    assert restarted['job'] is None and restarted['events'] == []
    assert restarted['execution_result'] == 'none'


def test_unassigned_management_address_is_retried_when_it_becomes_available():
    from wfb_ng.fl.console_http import ManagementAddressError, ManagementWebListener
    import socket
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    available, unavailable, ready = threading.Event(), threading.Event(), threading.Event()
    statuses = []
    def validate(host):
        if not available.is_set():
            raise ManagementAddressError('WEB_ADDRESS_NOT_ASSIGNED', 'unassigned')
    def status(state, error):
        statuses.append((state,error))
        (ready if state == 'ready' else unavailable).set()
    listener = ManagementWebListener('127.0.0.1', port, make_daemon().console, validate, status)
    listener.start()
    try:
        assert unavailable.wait(3)
        assert statuses[0] == ('management_web_unavailable', 'WEB_ADDRESS_NOT_ASSIGNED')
        available.set()
        assert ready.wait(3)
    finally:
        listener.stop()


def test_role_close_failure_is_blocked_and_preserves_successful_execution():
    daemon = make_daemon()
    daemon.server_state = ServerState.RUNNING
    daemon.active_job = {'job_id':'success-with-cleanup-error', 'target_nodes':[1]}
    from unittest.mock import Mock
    role = Mock()
    role.close.side_effect = OSError('role failed to close')
    daemon.server_role = role
    with patch.object(daemon.control_plane, 'broadcast_downlink'):
        daemon._on_job_completed('success-with-cleanup-error', {})
    job = daemon.console.snapshot()['recent_job']
    assert job['execution_result'] == 'succeeded'
    assert job['recovery_state'] == 'blocked'
    assert job['recovery_error'] == 'ROLE_CLOSE_FAILED'


def test_daemon_stop_retains_aborted_job_and_blocks_unconfirmed_recovery():
    daemon = make_daemon()
    daemon.active_job = {'job_id':'stopped-job', 'target_nodes':[1]}
    daemon.server_state = ServerState.RUNNING
    daemon.stop()
    state = daemon.console.snapshot()
    assert state['current_job'] is None
    assert state['recent_job']['job_id'] == 'stopped-job'
    assert state['execution_result'] == 'aborted'
    assert state['recovery_state'] == 'blocked'
    assert state['recent_job']['recovery_error'] == 'DAEMON_STOPPED'


def test_invalid_tun_and_web_configuration_maps_to_domain_error():
    from wfb_ng.fl.errors import FLRuntimeError
    with pytest.raises(FLRuntimeError) as error:
        ServerDaemonConfig(tun_ip='not-an-address', web_host='192.0.2.1')
    assert error.value.error_code == 'invalid_tun_ip'


def test_real_management_ipv4_validation_bind_and_http_service():
    from pyroute2 import IPRoute
    from wfb_ng.fl.console_http import validate_management_address
    with IPRoute() as ip:
        ip.get_timeout = 1.0
        ip.get_timeout_exception = TimeoutError
        ip.settimeout(1.0)
        candidates = [address.get_attr('IFA_LOCAL') or address.get_attr('IFA_ADDRESS')
                      for address in ip.get_addr(family=socket.AF_INET)]
    host = None
    for candidate in candidates:
        try:
            validate_management_address(candidate, 'fl-s', None)
        except OSError:
            continue
        host = candidate
        break
    if host is None:
        pytest.skip('No assigned non-wireless management IPv4 for real listener integration')
    daemon = make_daemon(web_host=host)
    daemon.start()
    try:
        deadline = time.monotonic() + 4
        while True:
            ipc = http.client.HTTPConnection('127.0.0.1', daemon.actual_ipc_port, timeout=2)
            ipc.request('GET', '/api/v1/state')
            state = json.loads(ipc.getresponse().read())
            ipc.close()
            if state['server']['management_web']['status'] == 'ready':
                break
            assert time.monotonic() < deadline, state['server']['management_web']
            threading.Event().wait(0.01)
        for path in ('/', '/assets/console.js', '/api/v1/state'):
            conn = http.client.HTTPConnection(host, 8080, timeout=2)
            conn.request('GET', path)
            response = conn.getresponse()
            body = response.read()
            assert response.status == 200
            if path == '/api/v1/state':
                web_state = json.loads(body)['server']['management_web']
                assert web_state == {'status':'ready', 'host':host, 'port':8080, 'error':None}
            conn.close()
    finally:
        daemon.stop()
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind((host, 8080))


def test_web_stop_closes_listener_without_waiting_for_slow_snapshot_request():
    from wfb_ng.fl.console_http import ManagementWebListener
    ready, reading, release = threading.Event(), threading.Event(), threading.Event()
    daemon = make_daemon()
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    def snapshot():
        reading.set()
        assert release.wait(5)
        return {'schema_version':1}
    listener = ManagementWebListener('127.0.0.1', port, daemon.console,
                                     lambda host: None,
                                     lambda state,error: ready.set() if state == 'ready' else None)
    client_done = threading.Event()
    def request():
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=4)
        try:
            conn.request('GET', '/api/v1/state')
            conn.getresponse().read()
        finally:
            conn.close()
            client_done.set()
    with patch.object(daemon.console, 'snapshot', side_effect=snapshot):
        listener.start()
        assert ready.wait(3)
        client = threading.Thread(target=request)
        client.start()
        try:
            assert reading.wait(3)
            before = time.monotonic()
            listener.stop()
            assert time.monotonic() - before < 2
            # Socket ownership is released even while the existing request waits.
            with socket.socket() as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind(('127.0.0.1', port))
        finally:
            release.set()
            client.join(3)
            listener.stop()
        assert client_done.is_set() and not client.is_alive()


def test_web_stop_failure_cannot_prevent_core_cleanup():
    from dataclasses import replace
    from unittest.mock import Mock
    daemon = make_daemon()
    daemon.config = replace(daemon.config, enable_control_plane=True, enable_link_process=True)
    coordinator, role = Mock(), Mock()
    daemon.coordinator = coordinator
    daemon.server_role = role
    control_stopped, link_stopped = threading.Event(), threading.Event()
    with patch.object(daemon._web, 'stop', side_effect=RuntimeError('Web I/O deadline exceeded')), \
         patch.object(daemon.control_plane, 'stop', side_effect=control_stopped.set), \
         patch.object(daemon, '_stop_link_process', side_effect=link_stopped.set):
        daemon.stop()
    assert daemon.server_state == ServerState.STOPPED
    assert control_stopped.is_set() and link_stopped.is_set()
    coordinator.abort.assert_called_once_with(reason='daemon_stopped')
    coordinator.wait.assert_called_once()
    role.close.assert_called_once()


def test_repeated_web_retry_status_preserves_version_and_key_event_history():
    daemon = make_daemon()
    daemon.console.record_event({'type':'JOB_STARTED', 'job_id':'important-job'})
    daemon.console.record_web_status('management_web_unavailable', 'WEB_ADDRESS_NOT_ASSIGNED')
    before = daemon.console.snapshot()
    for _ in range(105):
        daemon.console.record_web_status('management_web_unavailable', 'WEB_ADDRESS_NOT_ASSIGNED')
    after = daemon.console.snapshot()
    assert after['state_version'] == before['state_version']
    assert after['events'] == before['events']
    assert [event['type'] for event in after['events']] == ['JOB_STARTED', 'MANAGEMENT_WEB_CHANGED']
    daemon.console.record_web_status('management_web_unavailable', 'WEB_BIND_FAILED')
    changed = daemon.console.snapshot()
    assert changed['state_version'] > after['state_version']
    assert len(changed['events']) == 3


def test_failed_optional_snapshot_refresh_cannot_interrupt_core_mutations_or_cleanup():
    from dataclasses import replace
    from unittest.mock import Mock
    daemon = make_daemon()
    daemon.config = replace(daemon.config, enable_control_plane=True, enable_link_process=True)
    diagnostic = daemon.event_bus.subscribe()
    coordinator, role = Mock(), Mock()
    daemon.coordinator = coordinator
    daemon.server_role = role
    control_stopped, link_stopped, web_stopped = threading.Event(), threading.Event(), threading.Event()
    with patch.object(daemon.adapter, 'is_tun_active', side_effect=OSError('TUN query unavailable')), \
         patch.object(daemon.control_plane, 'stop', side_effect=control_stopped.set), \
         patch.object(daemon._web, 'stop', side_effect=web_stopped.set), \
         patch.object(daemon, '_stop_link_process', side_effect=link_stopped.set):
        daemon.server_state = ServerState.IDLE
        assert daemon.server_state == ServerState.IDLE
        event = {'type':'DOMAIN_FACT', 'message':'A real domain event'}
        daemon.publish_event(event)
        assert diagnostic.get_nowait() == event
        daemon.stop()
        assert daemon.server_state == ServerState.STOPPED
        assert control_stopped.is_set() and link_stopped.is_set() and web_stopped.is_set()
        coordinator.abort.assert_called_once_with(reason='daemon_stopped')
        coordinator.wait.assert_called_once()
        role.close.assert_called_once()
        # Optional refresh isolation must not turn explicit snapshot failure
        # into a stale or invented successful response.
        with pytest.raises(OSError, match='TUN query unavailable'):
            daemon.console.snapshot()


def test_http_snapshot_hardware_query_failure_returns_503_without_fallback():
    from wfb_ng.fl.console_http import ConsoleHTTPServer, ConsoleRequestHandler
    daemon = make_daemon()
    server = ConsoleHTTPServer(('127.0.0.1', 0), ConsoleRequestHandler)
    server.application = daemon.console
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        with patch.object(daemon.adapter, 'is_tun_active', side_effect=OSError('/private/tun-query')):
            conn = http.client.HTTPConnection(*server.server_address, timeout=2)
            conn.request('GET', '/api/v1/state')
            response = conn.getresponse()
            body = response.read()
            conn.close()
        assert response.status == 503
        assert json.loads(body)['error']['code'] == 'STATE_UNAVAILABLE'
        assert b'/private/tun-query' not in body
        assert 'server' not in json.loads(body)
    finally:
        server.shutdown()
        worker.join()
        server.server_close()
