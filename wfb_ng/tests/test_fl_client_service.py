"""客户端宿主事件通过私有 socket 传递，并等待 Daemon 确认。"""
import json
from wfb_ng.tests._client_test_network import client_network_kwargs, isolated_client_network
import socket
import threading

import pytest

from wfb_ng.fl.errors import FLRuntimeError
from wfb_ng.fl.service import AlgorithmNotifications


def test_prepared_waits_for_daemon_acceptance(tmp_path):
    address = str(tmp_path / 'notify.sock')
    daemon = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    daemon.bind(address)
    daemon.settimeout(1)
    accepted = threading.Event()
    errors = []

    def report():
        try:
            AlgorithmNotifications(address, 'job1', threading.Event(), timeout=1).prepared({'sample_count': 60000})
            accepted.set()
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=report)
    worker.start()
    payload, sender = daemon.recvfrom(4097)
    assert json.loads(payload) == {'job_id': 'job1', 'event': 'prepared', 'report': {'sample_count': 60000}}
    assert not accepted.is_set()
    daemon.sendto(b'{"accepted":true}', sender)
    worker.join(2)
    daemon.close()
    assert not errors
    assert accepted.is_set()


def test_local_event_size_limit_rejects_before_sending():
    with pytest.raises(FLRuntimeError) as error:
        AlgorithmNotifications('/unused', 'job1', threading.Event()).prepared({'text': 'x' * 4096})
    assert error.value.error_code == 'control_message_too_large'


def test_job_context_uses_local_dataset_and_keeps_algorithm_config_unchanged(tmp_path):
    from wfb_ng.fl.client_daemon import ClientDaemonConfig, ClientJobConfig, JobSandbox
    from wfb_ng.tests.test_fl_client_daemon import MockNetworkAdapter
    config = ClientDaemonConfig(**client_network_kwargs(), node_id=1, tun_ip='10.80.0.11', mnist_data_dir='/local/mnist')
    sandbox = JobSandbox(str(tmp_path), MockNetworkAdapter(),
                         _command_prefix=['/bin/sleep', '30'],
                         local_config={'mnist_data_dir': config.mnist_data_dir})
    job = ClientJobConfig(job_id='job1', run_id='run1', node_id=1,
                          tun_name='fl-c1', tun_ip='10.80.0.11',
                          io_timeout_seconds=20, live_observation=False,
                          target_nodes=(1, 2), rounds=2, preparation_timeout_seconds=17,
                          algorithm='wfb_ng.fl.mnist:client_entry', algorithm_config={'seed': 42})
    try:
        sandbox.start(job, 'wlan0')
        context = json.loads((tmp_path / 'job_job1' / 'job_context.json').read_text())
        assert context['target_nodes'] == [1, 2]
        assert context['rounds'] == 2
        assert context['local_config'] == {'mnist_data_dir': '/local/mnist'}
        assert context['preparation_timeout_seconds'] == 17
        assert json.loads((tmp_path / 'job_job1' / 'algorithm_config.json').read_text()) == {'seed': 42}
    finally:
        sandbox.abort(timeout=0.05)


@pytest.mark.parametrize('rounds', [1, 5])
def test_sandbox_ack_follows_server_acceptance_and_monitors_final_round(tmp_path, monkeypatch, rounds):
    import os
    import subprocess
    import sys
    from wfb_ng.fl.client_daemon import ClientJobConfig, JobSandbox, DaemonState
    from wfb_ng.tests.test_fl_client_daemon import MockNetworkAdapter
    real_popen = subprocess.Popen
    program = '''
import os, threading
from wfb_ng.fl.service import AlgorithmNotifications
n = AlgorithmNotifications(os.environ['WFB_FL_NOTIFY_SOCKET'], 'job1', threading.Event(), timeout=3)
n.prepared({'sample_count': 60000})
for index in range(1, ROUND_COUNT + 1):
    n.round_complete(index)
'''
    program = program.replace('ROUND_COUNT', str(rounds))
    processes = []

    def spawn(command, **kwargs):
        process = real_popen([sys.executable, '-c', program], **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, 'Popen', spawn)
    sandbox = JobSandbox(str(tmp_path), MockNetworkAdapter(), evidence_dir=str(tmp_path / 'evidence'))
    job = ClientJobConfig(job_id='job1', run_id='run1', node_id=1,
                          tun_name='fl-c1', tun_ip='10.80.0.11',
                          io_timeout_seconds=20, live_observation=False,
                          preparation_timeout_seconds=3, target_nodes=(1,), rounds=rounds)
    seen = []
    prepared = threading.Event()
    release = threading.Event()
    errors = []

    def forward(event):
        seen.append(event['event'])
        if event['event'] == 'prepared':
            prepared.set()
            assert release.wait(2)

    def monitor():
        try:
            sandbox.monitor(forward, threading.Event())
        except Exception as exc:
            errors.append(exc)

    try:
        sandbox.start(job, 'wlan0')
        assert sandbox.state == DaemonState.PREPARING
        worker = threading.Thread(target=monitor)
        worker.start()
        assert prepared.wait(2)
        assert seen == ['prepared']
        release.set()
        worker.join(4)
        assert not worker.is_alive()
        assert not errors
        assert seen == ['prepared'] + ['round_complete'] * rounds
        assert len(processes) == 1, '多轮通知和等待复用同一任务角色进程'
    finally:
        release.set()
        sandbox.abort(timeout=0.05)


def test_service_rejects_legacy_two_argument_entry_and_reports_error(tmp_path, monkeypatch):
    import sys
    import types
    from wfb_ng.fl import service
    module = types.ModuleType('legacy_client_entry')
    module.run = lambda runtime, config: None
    monkeypatch.setitem(sys.modules, 'legacy_client_entry', module)
    role_path = tmp_path / 'role.json'
    # 角色 JSON 读取已有独立覆盖，此处从 CLI 与算法入口接缝验证。
    monkeypatch.setattr(service, '_read_config', lambda path: {'role': 'client'})
    context_path = tmp_path / 'context.json'
    context_path.write_text(json.dumps(dict(job_id='job1', node_id=1, target_nodes=[1], rounds=1,
        artifact_dir=str(tmp_path / 'artifacts'), local_config={'mnist_data_dir': '/local/mnist'},
        preparation_timeout_seconds=1)))
    config_path = tmp_path / 'algorithm.json'
    config_path.write_text('{}')
    address = str(tmp_path / 'notify.sock')
    daemon = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    daemon.bind(address)
    daemon.settimeout(2)
    monkeypatch.setenv('WFB_FL_NOTIFY_SOCKET', address)
    monkeypatch.setattr(sys, 'argv', ['service', '--config', str(role_path),
        '--algorithm', 'legacy_client_entry:run', '--algorithm-config', str(config_path),
        '--job-context', str(context_path)])
    received = []

    def accept_error():
        payload, sender = daemon.recvfrom(4097)
        received.append(json.loads(payload))
        daemon.sendto(b'{"accepted":true}', sender)

    worker = threading.Thread(target=accept_error)
    worker.start()
    try:
        assert service.main('client') == 1
        worker.join(2)
        assert received[0]['event'] == 'error'
        assert received[0]['code'] == 'invalid_configuration'
    finally:
        daemon.close()


@pytest.mark.parametrize('algorithm_config', [
    {'mnist_data_dir': '/server/mnist'},
    {'nested': [{'source': '/server/mnist'}]},
    {'note': 'x' * 2049},
])
def test_daemon_rejects_remote_dataset_path_without_starting_role(tmp_path, algorithm_config):
    from unittest import mock
    from wfb_ng.fl.client_daemon import ClientDaemon, ClientDaemonConfig
    from wfb_ng.fl.control import ControlPlaneClient, ClientNodeState
    from wfb_ng.tests.test_fl_client_daemon import MockNetworkAdapter
    daemon = ClientDaemon(ClientDaemonConfig(**client_network_kwargs(), node_id=1, tun_ip='10.80.0.11',
        work_dir=str(tmp_path), enable_link_process=False, mnist_data_dir='/local/mnist'),
        network_adapter=MockNetworkAdapter(['wlan0']))
    daemon.poll_hardware_once()
    with mock.patch.object(ControlPlaneClient, 'start'):
        daemon.start_control_plane()
    reported = threading.Event()
    daemon.control_plane.notify_task_error = lambda *args, **kwargs: reported.set()
    try:
        daemon.control_plane.handle_broadcast(dict(type='TASK_ANNOUNCE', job_id='job1', run_id='run1',
            target_nodes=[1], rounds=1, preparation_timeout_seconds=10,
            algorithm='wfb_ng.fl.mnist:client_entry', algorithm_config=algorithm_config,
            io_timeout_seconds=10, live_observation=False, server_http_host='10.80.0.1',
            server_http_port=8080, uftp_port=1044, link_id=7669206))
        assert reported.wait(1), 'configuration rejection must reach the server as TASK_ERROR'
        assert not (tmp_path / 'job_job1').exists()
    finally:
        daemon.stop()


def test_preparation_deadline_is_enforced_for_role_that_never_reports(tmp_path, monkeypatch):
    import subprocess
    import sys
    from wfb_ng.fl.client_daemon import ClientJobConfig, JobSandbox
    from wfb_ng.tests.test_fl_client_daemon import MockNetworkAdapter
    real_popen = subprocess.Popen
    monkeypatch.setattr(subprocess, 'Popen', lambda command, **kw: real_popen(
        [sys.executable, '-c', 'import time; time.sleep(30)'], **kw))
    sandbox = JobSandbox(str(tmp_path), MockNetworkAdapter(), evidence_dir=str(tmp_path / 'evidence'))
    job = ClientJobConfig(job_id='job1', run_id='run1', node_id=1, tun_name='fl-c1',
        tun_ip='10.80.0.11', io_timeout_seconds=10, live_observation=False,
        target_nodes=(1,), preparation_timeout_seconds=0.1)
    try:
        sandbox.start(job, 'wlan0')
        with pytest.raises(FLRuntimeError) as error:
            sandbox.monitor(lambda event: pytest.fail('no algorithm reports expected'), threading.Event())
        assert error.value.error_code == 'preparation_timeout'
    finally:
        sandbox.abort(timeout=0.05)


def test_daemon_launch_does_not_block_control_and_stop_joins_supervisor(tmp_path, monkeypatch):
    import subprocess
    import sys
    from unittest import mock
    from wfb_ng.fl.client_daemon import ClientDaemon, ClientDaemonConfig
    from wfb_ng.fl.control import ControlPlaneClient
    from wfb_ng.tests.test_fl_client_daemon import MockNetworkAdapter
    daemon = ClientDaemon(ClientDaemonConfig(**client_network_kwargs(), node_id=1, tun_ip='10.80.0.11',
        work_dir=str(tmp_path), enable_link_process=False), network_adapter=MockNetworkAdapter(['wlan0']))
    daemon.poll_hardware_once()
    with mock.patch.object(ControlPlaneClient, 'start'):
        daemon.start_control_plane()
    real_popen = subprocess.Popen
    program = '''
import os, threading
from wfb_ng.fl.service import AlgorithmNotifications
AlgorithmNotifications(os.environ['WFB_FL_NOTIFY_SOCKET'], 'job1', threading.Event(), timeout=4).prepared({'samples': 60000})
'''
    monkeypatch.setattr(subprocess, 'Popen', lambda cmd, **kw: real_popen([sys.executable, '-c', program], **kw))
    entered = threading.Event()
    release = threading.Event()
    stopped = threading.Event()

    def accept(*args, **kwargs):
        entered.set()
        assert release.wait(3)

    monkeypatch.setattr(daemon.control_plane, 'notify_task_ready', accept)
    daemon.control_plane.handle_broadcast(dict(type='TASK_ANNOUNCE', job_id='job1', run_id='run1',
        target_nodes=[1], rounds=1, preparation_timeout_seconds=10,
        algorithm='wfb_ng.fl.mnist:client_entry', algorithm_config={},
        io_timeout_seconds=10, live_observation=False, server_http_host='10.80.0.1',
        server_http_port=8080, uftp_port=1044, link_id=7669206))
    stopper = threading.Thread(target=lambda: (daemon.stop(), stopped.set()))
    try:
        assert entered.wait(2), 'background supervisor must receive preparation without blocking broadcast caller'
        stopper.start()
        assert not stopped.wait(0.05), 'stop must await the active supervisor'
        release.set()
        stopper.join(4)
        assert stopped.is_set()
        assert not daemon.sandbox.is_running
    finally:
        release.set()
        daemon.stop()
        if stopper.ident is not None:
            stopper.join(4)


def test_bare_ready_cannot_pass_algorithm_preparation(tmp_path, monkeypatch):
    import subprocess
    import sys
    from wfb_ng.fl.client_daemon import ClientJobConfig, JobSandbox
    from wfb_ng.tests.test_fl_client_daemon import MockNetworkAdapter
    real_popen = subprocess.Popen
    program = '''
import os, socket, uuid
with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
    sock.bind('\\0wfb-ready-test-' + uuid.uuid4().hex)
    sock.connect(os.environ['WFB_FL_NOTIFY_SOCKET'])
    sock.send(b'READY=1')
    sock.settimeout(2)
    sock.recv(4096)
'''
    monkeypatch.setattr(subprocess, 'Popen', lambda cmd, **kw: real_popen([sys.executable, '-c', program], **kw))
    sandbox = JobSandbox(str(tmp_path), MockNetworkAdapter(), evidence_dir=str(tmp_path / 'evidence'))
    job = ClientJobConfig(job_id='job1', run_id='run1', node_id=1, tun_name='fl-c1',
        tun_ip='10.80.0.11', io_timeout_seconds=10, live_observation=False,
        target_nodes=(1,), preparation_timeout_seconds=2)
    try:
        sandbox.start(job, 'wlan0')
        with pytest.raises(FLRuntimeError) as error:
            sandbox.monitor(lambda event: pytest.fail('bare READY is not an algorithm fact'), threading.Event())
        assert error.value.error_code == 'algorithm_report_rejected'
    finally:
        sandbox.abort(timeout=0.05, outcome='failed')


@pytest.mark.parametrize('prepared', [False, True])
def test_service_error_is_accepted_by_real_control_ack_with_actual_round(tmp_path, monkeypatch, prepared):
    import subprocess
    import sys
    import types
    from wfb_ng.fl import control
    from wfb_ng.fl.client_daemon import ClientJobConfig, JobSandbox
    from wfb_ng.fl.radio import RadioConfig
    from wfb_ng.tests.test_fl_client_daemon import MockNetworkAdapter
    server = control.ControlPlaneServer(active_radio_config=RadioConfig(channel=157))
    server.prepare_task_readiness('job1', [1])
    client = control.ControlPlaneClient(node_id=1, tun_ip='10.80.0.11',
        network_adapter=MockNetworkAdapter(), air_interface='wlan0')

    class ReportPeer:
        def sendto(self, data, address):
            self.reply = server.handle_datagram(data, ('10.80.0.11', 9001))[0]
        def recvfrom(self, size):
            return self.reply, ('10.80.0.1', 9001)
        def settimeout(self, timeout):
            pass
        def close(self):
            pass

    # 保留 service 的真实 UNIX socket，仅替换 UDP 系统边界。
    monkeypatch.setattr(control, 'socket', types.SimpleNamespace(
        socket=lambda *args: ReportPeer(), AF_INET=socket.AF_INET, SOCK_DGRAM=socket.SOCK_DGRAM,
        timeout=socket.timeout))
    real_popen = subprocess.Popen
    program = '''
import sys, types, threading
from wfb_ng.fl import service
from wfb_ng.fl.errors import FLRuntimeError
module = types.ModuleType('host_error_fixture')
def run(runtime, config, context):
    if PREPARED:
        context.report_prepared({'sample_count': 60000})
        context.report_round_complete(1)
    raise FLRuntimeError('fixture_failure', '计算失败')
module.run = run
sys.modules[module.__name__] = module
class Role:
    def start(self): return object()
    def wait(self, interval): threading.Event().wait(interval)
    def close(self): pass
service.load_role_service = lambda *args, **kwargs: Role()
sys.exit(service.main('client'))
'''.replace('PREPARED', repr(prepared))
    monkeypatch.setattr(subprocess, 'Popen', lambda command, **kw: real_popen(
        [sys.executable, '-c', program, *command[3:]], **kw))
    sandbox = JobSandbox(str(tmp_path), MockNetworkAdapter(), evidence_dir=str(tmp_path / 'evidence'))
    job = ClientJobConfig(job_id='job1', run_id='run1', node_id=1, tun_name='fl-c1',
        tun_ip='10.80.0.11', io_timeout_seconds=2, live_observation=False,
        target_nodes=(1,), rounds=2, preparation_timeout_seconds=2,
        algorithm='host_error_fixture:run', algorithm_config={})
    events = []

    def forward(event):
        events.append(event)
        if event['event'] == 'prepared':
            client.notify_task_ready('job1', event['report'], timeout=1)
            assert server.wait_for_task_readiness(0)
            server.prepare_round_completion('job1', 1)
        elif event['event'] == 'round_complete':
            client.notify_round_complete('job1', event['round_index'], timeout=1)
            assert server.wait_for_round_completion('job1', 1, 0)
            server.prepare_round_completion('job1', 2)
        else:
            client.notify_task_error('job1', event['phase'], event['code'], event['reason'],
                                     round_index=event['round_index'], timeout=1)

    try:
        sandbox.start(job, 'wlan0')
        sandbox.monitor(forward, threading.Event())
        error = events[-1]
        assert error['event'] == 'error'
        assert error['phase'] == ('round' if prepared else 'prepare')
        assert error['round_index'] == (2 if prepared else None)
        with pytest.raises(FLRuntimeError) as failure:
            if prepared:
                server.wait_for_round_completion('job1', 2, 0)
            else:
                server.wait_for_task_readiness(0)
        assert failure.value.error_code == 'fixture_failure'
    finally:
        sandbox.abort(timeout=0.05, outcome='failed')


def test_daemon_role_exit_reports_next_uncompleted_round_to_real_control(tmp_path, monkeypatch):
    import subprocess
    import sys
    import types
    from unittest import mock
    from wfb_ng.fl import control
    from wfb_ng.fl.client_daemon import ClientDaemon, ClientDaemonConfig
    from wfb_ng.fl.radio import RadioConfig
    from wfb_ng.tests.test_fl_client_daemon import MockNetworkAdapter
    server = control.ControlPlaneServer(active_radio_config=RadioConfig(channel=157))
    server.prepare_task_readiness('job1', [1])
    accepted_error = threading.Event()
    received = []

    class ReportPeer:
        def __init__(self):
            self.blocking = True
            self.reply = None
        def setblocking(self, value):
            self.blocking = value
        def sendto(self, data, address):
            payload = json.loads(data)
            received.append(payload)
            self.reply = server.handle_datagram(data, ('10.80.0.11', 9001))[0]
            if payload['type'] == 'TASK_READY':
                assert server.wait_for_task_readiness(0)
                server.prepare_round_completion('job1', 1)
            elif payload['type'] == 'ROUND_READY':
                assert server.wait_for_round_completion('job1', 1, 0)
                server.prepare_round_completion('job1', 2)
            elif payload['type'] == 'TASK_ERROR' and json.loads(self.reply)['ok']:
                accepted_error.set()
        def recvfrom(self, size):
            if not self.blocking or self.reply is None:
                raise BlockingIOError()
            return self.reply, ('10.80.0.1', 9001)
        def settimeout(self, timeout): pass
        def close(self): pass

    daemon = ClientDaemon(ClientDaemonConfig(**client_network_kwargs(), node_id=1, tun_ip='10.80.0.11',
        work_dir=str(tmp_path), enable_link_process=False), MockNetworkAdapter(['wlan0']))
    daemon.poll_hardware_once()
    with mock.patch.object(control.ControlPlaneClient, 'start'):
        daemon.start_control_plane()
    monkeypatch.setattr(control, 'socket', types.SimpleNamespace(
        socket=lambda *args: ReportPeer(), AF_INET=socket.AF_INET, SOCK_DGRAM=socket.SOCK_DGRAM,
        timeout=socket.timeout, error=socket.error))
    real_popen = subprocess.Popen
    program = '''
import os, threading
from wfb_ng.fl.service import AlgorithmNotifications
notify = AlgorithmNotifications(os.environ['WFB_FL_NOTIFY_SOCKET'], 'job1', threading.Event(), timeout=2)
notify.prepared({'sample_count': 60000})
notify.round_complete(1)
raise SystemExit(42)
'''
    monkeypatch.setattr(subprocess, 'Popen', lambda command, **kw: real_popen(
        [sys.executable, '-c', program], **kw))
    try:
        daemon.control_plane.handle_broadcast(dict(type='TASK_ANNOUNCE', job_id='job1', run_id='run1',
            target_nodes=[1], rounds=2, preparation_timeout_seconds=3,
            algorithm='wfb_ng.fl.mnist:client_entry', algorithm_config={'data_seed': 2026},
            io_timeout_seconds=2, live_observation=False, server_http_host='10.80.0.1',
            server_http_port=8080, uftp_port=1044, link_id=7669206))
        assert accepted_error.wait(3), received
        error = next(payload for payload in received if payload['type'] == 'TASK_ERROR')
        assert error['phase'] == 'round'
        assert error['round_index'] == 2
        assert error['error_code'] == 'algorithm_incomplete'
    finally:
        daemon.stop()


def test_service_passes_recursively_readonly_config_to_client_entry(tmp_path, monkeypatch):
    import sys
    import types
    from wfb_ng.fl import service
    module = types.ModuleType('readonly_client_fixture')
    observed = []

    def run(runtime, config, context):
        from operator import setitem
        mutations = (lambda: setitem(config, 'extra', True),
                     lambda: setitem(config['optimizer'], 'rate', 1),
                     lambda: config['labels'].append(3))
        for mutate in mutations:
            try:
                mutate()
            except (TypeError, AttributeError):
                pass
            else:
                raise RuntimeError('algorithm configuration must reject mutation')
        observed.append((config['optimizer']['rate'], tuple(config['labels'])))
        context.report_prepared({'ready': True})
        context.report_round_complete(1)

    module.run = run
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(service, '_read_config', lambda path: {'role': 'client'})

    class Role:
        def start(self): return object()
        def wait(self, interval): threading.Event().wait(interval)
        def close(self): pass

    monkeypatch.setattr(service, 'load_role_service', lambda *args, **kwargs: Role())
    context_path = tmp_path / 'context.json'
    context_path.write_text(json.dumps(dict(job_id='job1', node_id=1, target_nodes=[1], rounds=1,
        artifact_dir=str(tmp_path / 'artifacts'), local_config={}, preparation_timeout_seconds=1)))
    config_path = tmp_path / 'algorithm.json'
    config_path.write_text(json.dumps({'optimizer': {'rate': 0.1}, 'labels': [1, 2]}))
    address = str(tmp_path / 'notify.sock')
    daemon = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    daemon.bind(address)
    daemon.settimeout(2)
    monkeypatch.setenv('WFB_FL_NOTIFY_SOCKET', address)
    monkeypatch.setattr(sys, 'argv', ['service', '--config', '/role.json',
        '--algorithm', f'{module.__name__}:run', '--algorithm-config', str(config_path),
        '--job-context', str(context_path)])
    received = []

    def acknowledge():
        while True:
            payload, sender = daemon.recvfrom(4097)
            event = json.loads(payload)
            received.append(event)
            daemon.sendto(b'{"accepted":true}', sender)
            if event['event'] in ('round_complete', 'error'):
                return

    worker = threading.Thread(target=acknowledge)
    worker.start()
    try:
        assert service.main('client') == 0
        worker.join(2)
        assert observed == [(0.1, (1, 2))]
        assert [event['event'] for event in received] == ['prepared', 'round_complete']
        assert json.loads(config_path.read_text()) == {'optimizer': {'rate': 0.1}, 'labels': [1, 2]}
    finally:
        daemon.close()
        worker.join(2)

@pytest.mark.parametrize('terminal', ['JOB_COMPLETED', 'JOB_ABORT', 'natural'])
def test_finished_job_cannot_be_restarted_by_delayed_announce(tmp_path, monkeypatch, terminal):
    """带内终态和自然返回后，迟到的启动重试不得重新执行同一作业。"""
    import subprocess
    import sys
    from unittest import mock
    from wfb_ng.fl.client_daemon import ClientDaemon, ClientDaemonConfig
    from wfb_ng.fl.control import ControlPlaneClient
    from wfb_ng.tests.test_fl_client_daemon import MockNetworkAdapter

    daemon = ClientDaemon(ClientDaemonConfig(**client_network_kwargs(), node_id=1, tun_ip='10.80.0.11',
        work_dir=str(tmp_path), enable_link_process=False),
        network_adapter=MockNetworkAdapter(['wlan0']))
    daemon.poll_hardware_once()
    with mock.patch.object(ControlPlaneClient, 'start'):
        daemon.start_control_plane()
    real_popen = subprocess.Popen
    processes = []
    contexts = []
    daemon.sandbox.evidence_dir = str(tmp_path / 'evidence')
    program = '''
import os, threading, time
from wfb_ng.fl.service import AlgorithmNotifications
n = AlgorithmNotifications(os.environ['WFB_FL_NOTIFY_SOCKET'], 'job1', threading.Event(), timeout=4)
n.prepared({'ready': True})
n.round_complete(1)
'''
    if terminal != 'natural':
        program += 'time.sleep(30)\n'

    def spawn(cmd, **kwargs):
        from pathlib import Path
        context = json.loads(Path(cmd[cmd.index('--job-context') + 1]).read_text())
        contexts.append(context)
        proc = real_popen([sys.executable, '-c', program.replace("'job1'", repr(context['job_id']))], **kwargs)
        processes.append(proc)
        return proc

    monkeypatch.setattr(subprocess, 'Popen', spawn)
    completed = threading.Event()
    recovered = {'job1': threading.Event(), 'job2': threading.Event()}
    recoveries = []

    def recovery(job_id, epoch, state, **kwargs):
        recoveries.append((job_id, epoch, state))
        recovered[job_id].set()

    monkeypatch.setattr(daemon.control_plane, 'notify_job_recovery', recovery)
    monkeypatch.setattr(daemon.control_plane, 'notify_task_ready', lambda *a, **kw: None)
    monkeypatch.setattr(daemon.control_plane, 'notify_round_complete', lambda *a, **kw: completed.set())
    announce = dict(type='TASK_ANNOUNCE', job_id='job1', run_id='run1', target_nodes=[1],
        rounds=1, preparation_timeout_seconds=10, algorithm='wfb_ng.fl.mnist:client_entry',
        algorithm_config={}, io_timeout_seconds=10, live_observation=False,
        server_http_host='10.80.0.1', server_http_port=8080, uftp_port=1044, link_id=7669206)
    try:
        daemon.control_plane.handle_broadcast(announce)
        assert completed.wait(3)
        if terminal != 'natural':
            daemon.control_plane.handle_broadcast(dict(type=terminal, job_id='job1'))
        daemon._startup_thread.join(3)
        assert not daemon.sandbox.is_running
        daemon.control_plane.handle_broadcast(dict(type='JOB_COMPLETED', job_id='job1'))
        assert recovered['job1'].wait(3), '新作业必须在服务器终态触发的恢复事实后启动'
        assert recoveries[-1] == ('job1', 1, 'ready')
        daemon.control_plane.handle_broadcast(announce)
        daemon._startup_thread.join(3)
        assert len(processes) == 1, '终态后重复启动消息不能再次创建任务角色'
        completed.clear()
        daemon.control_plane.handle_broadcast({**announce, 'job_id': 'job2'})
        assert completed.wait(3), '新的作业身份应创建新的执行上下文'
        daemon.control_plane.handle_broadcast(dict(
            type='JOB_COMPLETED' if terminal == 'natural' else terminal, job_id='job2'))
        assert recovered['job2'].wait(3)
        assert recoveries[-1] == ('job2', 1, 'ready')
        daemon._startup_thread.join(3)
        assert len(processes) == 2
        assert [context['job_id'] for context in contexts] == ['job1', 'job2']
        assert contexts[0]['artifact_dir'] != contexts[1]['artifact_dir']
    finally:
        daemon.stop()
