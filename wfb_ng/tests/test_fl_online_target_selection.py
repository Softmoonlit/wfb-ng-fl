"""票 02 的受影响入口检查：真实守护进程、Web 快照与 HTTP 作业受理。

受控心跳和角色、链路边界只验证软件行为，不代表无线或训练实测。
本文件可独立运行，不依赖已停用的全仓回归体系。
"""
from contextlib import contextmanager
from dataclasses import replace
import http.client
import ipaddress
import socket
import io
import json
import threading
import time
from unittest.mock import Mock, patch
from typing import Any, Dict
from pathlib import Path

import pytest

from wfb_ng.fl.client_daemon import NetworkAdapter
from wfb_ng.fl.console_http import ConsoleHTTPServer, ConsoleRequestHandler
from wfb_ng.fl.control import NodeHeartbeat
from wfb_ng.fl.errors import FLRuntimeError
from wfb_ng.fl.server_daemon import ServerDaemon, ServerDaemonConfig, ServerState, load_server_config



@pytest.fixture(autouse=True)
def isolated_socket_boundary(monkeypatch):
    """只在真实 socket 出站边界拦截：本文件绝不访问板卡或共享控制端口。"""
    sendto = socket.socket.sendto
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex

    def require_isolated(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            host, port = address[:2]
            assert ipaddress.ip_address(host).is_loopback, f'禁止测试访问非 loopback 地址: {address}'
            assert port not in (9000, 9001, 9002, 9200), f'禁止测试访问共享控制端口: {address}'

    def isolated_sendto(sock, data, *args):
        require_isolated(sock, args[-1])
        return sendto(sock, data, *args)

    def isolated_connect(sock, address):
        require_isolated(sock, address)
        return connect(sock, address)

    def isolated_connect_ex(sock, address):
        require_isolated(sock, address)
        return connect_ex(sock, address)

    monkeypatch.setattr(socket.socket, 'sendto', isolated_sendto)
    monkeypatch.setattr(socket.socket, 'connect', isolated_connect)
    monkeypatch.setattr(socket.socket, 'connect_ex', isolated_connect_ex)


@pytest.fixture
def isolated_control_address(isolated_socket_boundary):
    """持有 OS 分配的 loopback UDP 接收端，不复用实机默认端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sink:
        sink.bind(('127.0.0.1', 0))
        yield sink.getsockname()

def selection_client(runtime, config, context):
    """部署入口只参与装配，本文件不执行客户端计算。"""
    raise AssertionError('server must not execute client entry')


def selection_aggregate(current, updates, output, config, context):
    """选择测试停止在发布等待，不应进入聚合。"""
    raise AssertionError('selection test must not aggregate updates')


def selection_evaluate(model_path, config, context):
    """按选择夹具自己的格式检查模型，提供有限准备事实。"""
    context.check_stop()
    assert Path(model_path).read_bytes() == b'model'
    return {'metrics': {'selection_fixture': 1.0}, 'preparation': {'ready': True},
            'verdict': None, 'reason': None}


class SelectionRuntime:
    """保留活动作业供快照观察，停止时显式唤醒发布等待。"""
    def __init__(self):
        self.cancelled = threading.Event()

    def publish_model(self, model_path):
        if not self.cancelled.wait(5):
            raise FLRuntimeError('fixture_timeout', '测试没有停止活动作业')
        raise FLRuntimeError('aborted', '选择测试作业已停止')

    def wait_for_updates(self):
        raise AssertionError('selection test must not collect updates')

    def abort(self, reason='aborted'):
        self.cancelled.set()

    def close(self):
        self.cancelled.set()


def ready_taskpeer(daemon, message):
    """通过正式报告和相关确认释放所选完整节点集合的准备门禁。"""
    if message['type'] in ('JOB_ABORT', 'JOB_COMPLETED'):
        for node in daemon.get_status_report()['recent_job']['target_nodes']:
            replies = daemon.control_plane.handle_datagram(json.dumps({
                'type': 'JOB_RECOVERY', 'node_id': node, 'job_id': message['job_id'],
                'report_id': f"recovery-{message['job_id']}-{node}",
                'report': {'recovery_epoch': 1, 'recovery_state': 'ready'},
            }).encode(), (f'127.0.0.{10 + node}', daemon.config.control_broadcast_port))
            assert replies and json.loads(replies[0])['ok'] is True
        return
    if message['type'] != 'TASK_ANNOUNCE':
        return
    for node in message['target_nodes']:
        replies = daemon.control_plane.handle_datagram(json.dumps({
            'type': 'TASK_READY', 'node_id': node, 'job_id': message['job_id'],
            'report_id': f"prepared-{message['job_id']}-{node}", 'report': {'ready': True},
        }).encode(), (f'127.0.0.{10 + node}', daemon.config.control_broadcast_port))
        assert replies and json.loads(replies[0])['ok'] is True


@pytest.fixture
def daemon(tmp_path, isolated_control_address):
    adapter = Mock(spec=NetworkAdapter)
    adapter.is_tun_active.return_value = True
    result = ServerDaemon(ServerDaemonConfig(
        enable_link_process=False, enable_control_plane=False, io_timeout_seconds=0.15,
        ipc_host='127.0.0.1', ipc_port=0, control_bind_host='127.0.0.1', control_bind_port=0,
        control_broadcast_addr=isolated_control_address[0],
        control_broadcast_port=isolated_control_address[1], radio_control_port=isolated_control_address[1],
        client_entry=f'{__name__}:selection_client',
        aggregate_entry=f'{__name__}:selection_aggregate',
        evaluate_entry=f'{__name__}:selection_evaluate', algorithm_config={'fixture': 'selection'},
        model_library_dir=str(tmp_path / 'models'), work_dir=str(tmp_path)), adapter,
        runtime_factory=lambda job, payload: SelectionRuntime())
    result.server_state = ServerState.IDLE
    yield result
    result.stop()


def heartbeat(daemon, node_id, state='IDLE', error=None):
    hb = NodeHeartbeat(node_id=node_id, state='HUNTING', elapsed_ms=0,
                       current_channel=157, txpower_dbm=12, uplink_mcs=6)
    registry = daemon.control_plane.registry
    registry.update_heartbeat(hb, ('127.0.0.11', daemon.config.control_broadcast_port), daemon.radio_config)
    registry.update_heartbeat(replace(hb, state=state, error_code=error),
                              ('127.0.0.11', daemon.config.control_broadcast_port), daemon.radio_config)


def body(daemon, targets):
    model = daemon.console.upload_model(io.BytesIO(b'model'), 5, 'initial.bin')['model']
    return {'model_sha256': model['sha256'], 'target_nodes': targets, 'rounds': 1}


def payload(daemon, targets):
    request = body(daemon, targets)
    return dict(request, run_id='selection-check', job_id='selection-job', mode='sync',
                model_path=str(daemon.console.models.artifact_path(request['model_sha256'])),
                model_size_bytes=5, io_timeout_seconds=120, live_observation=False)


@contextmanager
def http_server(daemon):
    server = ConsoleHTTPServer(('127.0.0.1', 0), ConsoleRequestHandler)
    server.application = daemon.console
    worker = threading.Thread(target=server.serve_forever)
    worker.start()

    def request(method, path, data=None, key='selection-request'):
        host, port = server.server_address[:2]
        conn = http.client.HTTPConnection(str(host), int(port), timeout=5)
        conn.request(method, path, body=json.dumps(data) if data is not None else None,
                     headers={'Content-Type': 'application/json', 'Idempotency-Key': key})
        response = conn.getresponse()
        result = response.status, json.loads(response.read())
        conn.close()
        return result

    try:
        yield request
    finally:
        server.shutdown()
        worker.join()
        server.server_close()


def test_preparation_timeout_is_positive_and_frozen():
    raw_timeout: Any = '12'
    config = ServerDaemonConfig(preparation_timeout_seconds=raw_timeout)
    assert config.preparation_timeout_seconds == 12.0
    with pytest.raises(FLRuntimeError) as caught:
        ServerDaemonConfig(preparation_timeout_seconds=0)
    assert caught.value.error_code == 'invalid_preparation_timeout'


def test_load_server_config_validates_preparation_timeout(tmp_path):
    config_path = tmp_path / 'server.json'
    config_path.write_text('{"preparation_timeout_seconds": null}', encoding='utf-8')
    with pytest.raises(FLRuntimeError) as caught:
        load_server_config(str(config_path))
    assert caught.value.error_code == 'invalid_preparation_timeout'


def test_snapshot_preserves_radio_slots_and_uses_registry_eligibility(daemon):
    daemon.control_plane.registry.offline_threshold_seconds = 30
    now = time.monotonic()
    with patch('wfb_ng.fl.control.time.monotonic', return_value=now - 20):
        heartbeat(daemon, 3)
    heartbeat(daemon, 2, error='LINK_ERROR')
    heartbeat(daemon, 4, state='RUNNING')
    heartbeat(daemon, 5, state='HUNTING')
    with http_server(daemon) as request:
        status, state = request('GET', '/api/v1/state')
    assert status == 200
    assert [node['node_id'] for node in state['nodes']] == list(range(1, 11))
    nodes = {node['node_id']: node for node in state['nodes']}
    assert nodes[3]['online'] and nodes[3]['can_select']
    assert nodes[3]['selection_blockers'] == []
    assert not nodes[2]['can_select']
    assert nodes[2]['selection_blockers'] == [{'code': 'NODE_ERROR', 'message': 'LINK_ERROR'}]
    assert not nodes[4]['can_select'] and not nodes[5]['can_select']
    assert not nodes[1]['online'] and not nodes[1]['can_select']
    assert state['server']['can_start_job']
    # 超过注册表的心跳阈值后，在线与可选资格同时失效。
    with patch('wfb_ng.fl.control.time.monotonic', return_value=now + 31):
        expired = daemon.console.snapshot()
    assert not any(node['can_select'] for node in expired['nodes'])
    assert not expired['server']['can_start_job']


def test_server_address_is_never_a_candidate(daemon):
    daemon.config = replace(daemon.config, tun_ip='10.80.0.11')
    heartbeat(daemon, 1)
    with http_server(daemon) as request:
        status, state = request('GET', '/api/v1/state')
    assert status == 200
    assert [node['node_id'] for node in state['nodes']] == list(range(2, 11))
    assert not state['server']['can_start_job']
    with pytest.raises(FLRuntimeError) as caught:
        daemon.start_job(payload(daemon, [1]))
    assert caught.value.details['unready_nodes']['1']['selection_blockers'][0]['code'] == 'SERVER_NODE'


def test_daemon_rejects_every_invalid_target_before_any_admission(daemon):
    heartbeat(daemon, 1)
    heartbeat(daemon, 2, error='BROKEN')
    heartbeat(daemon, 3, state='RUNNING')
    heartbeat(daemon, 4)
    # 即使基础状态为 READY，非 IDLE 的上报状态仍须拒绝。
    daemon.control_plane.registry.get_node(4).reported_state = 'PREPARING'
    with pytest.raises(FLRuntimeError) as caught:
        daemon.start_job(payload(daemon, [1, 2, 3, 4, 10]))
    assert caught.value.error_code == 'preflight_target_nodes_not_ready'
    reasons = caught.value.details['unready_nodes']
    assert set(reasons) == {'2', '3', '4', '10'}
    assert all(reasons[node]['selection_blockers'] for node in reasons)
    assert reasons['2']['selection_blockers'][0]['code'] == 'NODE_ERROR'
    assert daemon.console.snapshot()['current_job'] is None
    assert daemon.console.snapshot()['recent_job'] is None



def test_disconnection_events_stay_with_the_job_that_observed_them(daemon):
    daemon.control_plane.offline_threshold_seconds = 0.08
    daemon.control_plane.registry.offline_threshold_seconds = 0.08
    heartbeat(daemon, 1)
    daemon.console.snapshot()
    first = payload(daemon, [1])
    with patch.object(daemon.control_plane, 'broadcast_downlink', side_effect=lambda msg: ready_taskpeer(daemon, msg)):
        daemon.start_job(first)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            state = daemon.console.snapshot()
            recent = state['recent_job']
            if recent and recent['recovery_state'] == 'ready':
                break
            time.sleep(0.005)
    assert recent and recent['execution_result'] == 'failed'
    assert recent['participants'][0]['disconnection_events']
    daemon.control_plane.offline_threshold_seconds = 30
    daemon.control_plane.registry.offline_threshold_seconds = 30
    heartbeat(daemon, 1)
    second = payload(daemon, [1])
    second['job_id'] = 'second-selection-job'
    with patch.object(daemon.control_plane, 'broadcast_downlink', side_effect=lambda msg: ready_taskpeer(daemon, msg)):
        daemon.start_job(second)
    participant = daemon.console.snapshot()['current_job']['participants'][0]
    assert participant['disconnection_events'] == []


def test_target_is_rechecked_at_the_admission_boundary(daemon):
    heartbeat(daemon, 1)

    def build_runtime(_job, _payload):
        heartbeat(daemon, 1, state='RUNNING')
        return Mock()

    daemon.runtime_factory = build_runtime
    with pytest.raises(FLRuntimeError) as caught:
        daemon.start_job(payload(daemon, [1]))
    assert caught.value.error_code == 'preflight_target_nodes_not_ready'
    assert caught.value.details['unready_nodes']['1']['selection_blockers'][0]['code'] in {'NODE_NOT_READY', 'NODE_NOT_IDLE'}
    assert daemon.console.snapshot()['current_job'] is None
    assert daemon.console.snapshot()['recent_job'] is None


def test_daemon_admits_the_exact_set_using_registry_heartbeat_threshold(daemon):
    daemon.control_plane.registry.offline_threshold_seconds = 30
    now = time.monotonic()
    with patch('wfb_ng.fl.control.time.monotonic', return_value=now - 20):
        heartbeat(daemon, 3)
        heartbeat(daemon, 1)
    with patch.object(daemon.control_plane, 'broadcast_downlink', side_effect=lambda msg: ready_taskpeer(daemon, msg)):
        accepted = daemon.start_job(payload(daemon, [3, 1]))
    assert accepted['status'] == 'accepted'
    assert accepted['target_nodes'] == [3, 1]
    assert daemon.console.snapshot()['current_job']['target_nodes'] == [3, 1]
    with patch('wfb_ng.fl.control.time.monotonic', return_value=now + 31):
        participants = daemon.console.snapshot()['current_job']['participants']
    assert [participant['node_id'] for participant in participants] == [3, 1]
    assert all(not participant['online'] for participant in participants)
    assert all(participant['disconnection_events'] for participant in participants)



def test_role_preparation_timeout_requests_resource_close(daemon):
    daemon.config = replace(daemon.config, enable_link_process=True, preparation_timeout_seconds=0.05)
    daemon.runtime_factory = None
    heartbeat(daemon, 1)
    release_role = threading.Event()
    role = Mock()
    role.poll_failure.return_value = None
    role.runtime = SelectionRuntime()
    role.start.side_effect = lambda: release_role.wait(2)
    role.close.side_effect = lambda: release_role.set()

    with patch('wfb_ng.fl.role.ServerRole', return_value=role):
        with pytest.raises(FLRuntimeError) as caught:
            daemon.start_job(payload(daemon, [1]))

    assert caught.value.error_code == 'preflight_preparation_timeout'
    role.close.assert_called()
    assert daemon.server_state == ServerState.IDLE


def test_role_start_does_not_hold_daemon_lock_against_stop(daemon):
    daemon.config = replace(daemon.config, enable_link_process=True, preparation_timeout_seconds=30.0)
    daemon.runtime_factory = None
    heartbeat(daemon, 1)
    role_started = threading.Event()
    release_role = threading.Event()
    listener_open = False
    result: Dict[str, Any] = {}

    role = Mock()
    role.poll_failure.return_value = None
    role.runtime = SelectionRuntime()
    def start_role():
        nonlocal listener_open
        role_started.set()
        release_role.wait(2)
        listener_open = True
    role.start.side_effect = start_role
    def close_role():
        nonlocal listener_open
        listener_open = False
        release_role.set()
    role.close.side_effect = close_role

    with patch('wfb_ng.fl.role.ServerRole', return_value=role), \
            patch.object(daemon, '_stop_link_process'):
        def run_start():
            try:
                result['start'] = daemon.start_job(payload(daemon, [1]))
            except Exception as exc:
                result['start'] = exc
        starter = threading.Thread(target=run_start)
        starter.start()
        assert role_started.wait(2)
        stopper = threading.Thread(target=daemon.stop)
        stopper.start()
        stopper.join(1)
        assert not stopper.is_alive()
        release_role.set()
        starter.join(2)

    assert not starter.is_alive()
    assert isinstance(result['start'], FLRuntimeError)
    assert result['start'].error_code == 'job_preparation_cancelled'
    assert not listener_open


def test_daemon_rejects_new_job_while_recovery_is_blocked(daemon):
    heartbeat(daemon, 1)
    daemon._recent_job = {'job_id': 'previous-job', 'target_nodes': [1], 'recovery_state': 'blocked'}
    with pytest.raises(FLRuntimeError) as caught:
        daemon.start_job(payload(daemon, [1]))
    assert caught.value.error_code == 'preflight_engine_conflict'
    assert daemon.console.snapshot()['current_job'] is None



def test_readiness_wait_releases_daemon_lock_for_abort(daemon):
    daemon.config = replace(daemon.config, enable_link_process=True)
    daemon.runtime_factory = None
    heartbeat(daemon, 1)
    wait_started = threading.Event()
    result: Dict[str, Any] = {}

    def observe_announcement(message):
        if message['type'] == 'TASK_ANNOUNCE':
            wait_started.set()
        else:
            ready_taskpeer(daemon, message)

    role = Mock()
    role.poll_failure.return_value = None
    role.runtime = SelectionRuntime()
    with patch('wfb_ng.fl.role.ServerRole', return_value=role), \
            patch.object(daemon.control_plane, 'broadcast_downlink', side_effect=observe_announcement), \
            patch.object(daemon, '_stop_link_process'), \
            patch.object(daemon, '_start_link_process'):
        request_body = body(daemon, [1])
        starter = threading.Thread(target=lambda: result.setdefault(
            'start', daemon.console.start_job(request_body, 'readiness-lock')))
        starter.start()
        assert wait_started.wait(2)

        aborter = threading.Thread(target=lambda: result.setdefault('abort', daemon.abort_job('operator_requested')))
        aborter.start()
        aborter.join(1)
        assert not aborter.is_alive()
        starter.join(2)

    assert not starter.is_alive()
    assert result['start']['status'] == 'accepted'
    assert result['abort']['status'] == 'aborted'
    assert daemon.console.snapshot()['recent_job']['execution_result'] == 'aborted'


def test_http_submission_race_rejects_whole_set_and_reports_each_node(daemon):
    heartbeat(daemon, 1)
    heartbeat(daemon, 2)
    request_body = body(daemon, [1, 2, 3])
    with http_server(daemon) as request:
        assert request('GET', '/api/v1/state')[1]['nodes'][1]['can_select']
        heartbeat(daemon, 2, error='BROKEN')
        status, result = request('POST', '/api/v1/jobs', request_body)
        assert status == 409
        assert set(result['error']['details']['unready_nodes']) == {'2', '3'}
        assert request('GET', '/api/v1/state')[1]['current_job'] is None


@pytest.mark.parametrize('recovery_fails', [False, True])
def test_http_accepted_startup_timeout_is_a_recent_failed_job(daemon, recovery_fails):
    daemon.runtime_factory = None
    heartbeat(daemon, 1)
    request_body = body(daemon, [1])
    daemon.config = replace(daemon.config, enable_link_process=True, preparation_timeout_seconds=0.15)
    role = Mock()
    role.poll_failure.return_value = None
    role.runtime = SelectionRuntime()
    with patch('wfb_ng.fl.role.ServerRole', return_value=role), \
            patch.object(daemon.control_plane, 'broadcast_downlink', side_effect=lambda message:
                         ready_taskpeer(daemon, message) if message['type'] != 'TASK_ANNOUNCE' and not recovery_fails else None), \
            http_server(daemon) as request:
        status, result = request('POST', '/api/v1/jobs', request_body)
        assert status == 202 and result['status'] == 'accepted'
        assert result['target_nodes'] == [1]
        # 使用同一幂等键重试已完成请求，不能重新受理失败作业。
        assert request('POST', '/api/v1/jobs', request_body) == (status, result)
        state = request('GET', '/api/v1/state')[1]
    assert state['current_job'] is None
    recent = state['recent_job']
    assert recent['job_id'] == result['job_id'] and recent['target_nodes'] == [1]
    assert recent['execution_result'] == 'failed'
    assert recent['reason'] == 'client_startup_timeout'
    assert recent['error']
    assert recent['recovery_state'] == ('unconfirmed' if recovery_fails else 'ready')
    assert daemon.get_status_report()['recent_job']['recovery_missing_nodes'] == ([1] if recovery_fails else [])


def test_http_accepted_announcement_failure_becomes_recent_failed_job(daemon):
    heartbeat(daemon, 1)
    request_body = body(daemon, [1])
    with patch.object(daemon.control_plane, 'broadcast_downlink', side_effect=RuntimeError('announce failed')), \
            http_server(daemon) as request:
        status, result = request('POST', '/api/v1/jobs', request_body)
        assert status == 202
        assert result['status'] == 'accepted'
        state = request('GET', '/api/v1/state')[1]
    assert state['current_job'] is None
    assert state['recent_job']['execution_result'] == 'failed'
    assert state['recent_job']['reason'] == 'job_preparation_failed'
    assert 'announce failed' in state['recent_job']['error']
    assert state['recent_job']['recovery_state'] == 'blocked'
