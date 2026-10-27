"""客户端终态广播、真实任务进程与恢复事实的公开接缝检查。"""
import os
from wfb_ng.tests._client_test_network import client_network_kwargs, isolated_client_network
import sys
import threading

import pytest

from wfb_ng.fl.client_daemon import ClientDaemon, ClientDaemonConfig
from wfb_ng.fl.control import ControlPlaneClient
from wfb_ng.tests.test_fl_client_daemon import MockNetworkAdapter, _make_client_job


@pytest.fixture
def host(tmp_path, monkeypatch):
    adapter = MockNetworkAdapter(['wlan0'])
    daemon = ClientDaemon(ClientDaemonConfig(**client_network_kwargs(), node_id=1, tun_ip='10.80.0.11',
        work_dir=str(tmp_path), enable_link_process=False), network_adapter=adapter)
    daemon.sandbox.evidence_dir = str(tmp_path / 'evidence')
    daemon.poll_hardware_once()
    monkeypatch.setattr(ControlPlaneClient, 'start', lambda self: None)
    daemon.start_control_plane()
    reports = []
    reported = threading.Event()

    def report(job_id, recovery_epoch, recovery_state, **kwargs):
        reports.append((job_id, recovery_epoch, recovery_state, kwargs))
        reported.set()

    monkeypatch.setattr(daemon.control_plane, 'notify_job_recovery', report, raising=False)
    yield daemon, adapter, reports, reported
    daemon.stop()


def test_terminal_does_not_block_downlink_and_replays_same_recovery(host, tmp_path):
    daemon, adapter, reports, reported = host
    entered = tmp_path / 'terminating'
    release = tmp_path / 'release'
    ready = tmp_path / 'started'
    daemon.sandbox._command_prefix = [sys.executable, '-c', f'''
import pathlib, signal, time
entered, release, ready = map(pathlib.Path, {list(map(str, (entered, release, ready)))!r})
def stop(*args):
    entered.touch()
    while not release.exists():
        time.sleep(.01)
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
ready.touch()
while True:
    signal.pause()
''']
    proc = daemon.trigger_job(_make_client_job(job_id='job1'))
    # 文件产生是子进程已装好 TERM handler 的证据。
    from time import monotonic
    until = monotonic() + 3
    while not ready.exists() and monotonic() < until:
        threading.Event().wait(.01)
    assert ready.exists()
    dispatched = threading.Event()
    daemon.control_plane.register_broadcast_handler('PROBE', lambda msg: dispatched.set())

    def downlink():
        daemon.control_plane.handle_broadcast({'type': 'JOB_ABORT', 'job_id': 'job1'})
        daemon.control_plane.handle_broadcast({'type': 'PROBE'})

    receiver = threading.Thread(target=downlink)
    receiver.start()
    try:
        assert dispatched.wait(.5), '任务回收不能占满唯一下行接收线程'
        assert not reports, '任务进程仍存活时不能报告 ready'
        release.touch()
        assert reported.wait(4)
        assert reports[0][:3] == ('job1', 1, 'ready')
        assert proc.poll() is not None
        assert adapter.is_tun_ready('fl-c1', '10.80.0.11/24', '10.80.0.1')
        reported.clear()
        daemon.control_plane.handle_broadcast({'type': 'JOB_COMPLETED', 'job_id': 'job1'})
        assert reported.wait(2)
        assert reports[1] == reports[0], '重复终态只重报同一 epoch 的事实'
    finally:
        release.touch()
        receiver.join(4)


def test_live_process_group_is_blocked_and_retains_cleanup_identity(host, monkeypatch):
    daemon, adapter, reports, reported = host
    daemon.sandbox._command_prefix = [sys.executable, '-c', 'pass']
    proc = daemon.trigger_job(_make_client_job(job_id='job1'))
    proc.wait(timeout=3)
    pgid = proc.pid
    real_killpg = os.killpg

    def live_group(group, sig):
        if group == pgid:
            return  # 模拟内核仍能看到未退出后代，SIGKILL 返回不代表退出。
        return real_killpg(group, sig)

    monkeypatch.setattr(os, 'killpg', live_group)
    daemon.control_plane.handle_broadcast({'type': 'JOB_COMPLETED', 'job_id': 'job1'})
    assert reported.wait(5)
    assert reports[0][:3] == ('job1', 1, 'blocked')
    assert reports[0][3]['error_code'] == 'task_process_group_not_exited'
    assert daemon.sandbox._pgid == pgid, '未确认退出必须保留实际清理身份'
    assert daemon._current_job_id == 'job1'
    monkeypatch.setattr(os, 'killpg', real_killpg)


def test_natural_return_keeps_binding_until_server_terminal(host, tmp_path, monkeypatch):
    import subprocess
    daemon, adapter, reports, reported = host
    completed = threading.Event()
    monkeypatch.setattr(daemon.control_plane, 'notify_task_ready', lambda *a, **kw: None)
    monkeypatch.setattr(daemon.control_plane, 'notify_round_complete', lambda *a, **kw: completed.set())
    real_popen = subprocess.Popen
    processes = []

    def spawn(command, **kwargs):
        program = '''
import os, threading
from wfb_ng.fl.service import AlgorithmNotifications
n = AlgorithmNotifications(os.environ['WFB_FL_NOTIFY_SOCKET'], 'job1', threading.Event(), timeout=3)
n.prepared({'ready': True})
n.round_complete(1)
'''
        proc = real_popen([sys.executable, '-c', program], **kwargs)
        processes.append(proc)
        return proc

    monkeypatch.setattr(subprocess, 'Popen', spawn)
    announce = dict(type='TASK_ANNOUNCE', job_id='job1', run_id='run1', target_nodes=[1],
        rounds=1, preparation_timeout_seconds=10, algorithm='wfb_ng.fl.mnist:client_entry',
        algorithm_config={}, io_timeout_seconds=10, live_observation=False,
        server_http_host='10.80.0.1', server_http_port=8080, uftp_port=1044, link_id=7669206)
    daemon.control_plane.handle_broadcast(announce)
    assert completed.wait(3)
    daemon._startup_thread.join(3)
    assert not daemon._startup_thread.is_alive()
    assert daemon._current_job_id == 'job1', '本地自然返回不丢失等待服务器终态的绑定'
    daemon.control_plane.handle_broadcast({**announce, 'job_id': 'job2'})
    assert len(processes) == 1
    daemon.control_plane.handle_broadcast({'type': 'JOB_COMPLETED', 'job_id': 'job1'})
    assert reported.wait(2)
    assert reports[0][:3] == ('job1', 1, 'ready')
    assert daemon._current_job_id is None


def test_unready_idle_tun_is_blocked_and_duplicate_terminal_does_not_reset(host, monkeypatch):
    daemon, adapter, reports, reported = host
    daemon.sandbox._command_prefix = [sys.executable, '-c', 'import time; time.sleep(30)']
    daemon.trigger_job(_make_client_job(job_id='job1'))
    monkeypatch.setattr(adapter, 'is_tun_ready', lambda *a: False)
    daemon.control_plane.handle_broadcast({'type': 'JOB_ABORT', 'job_id': 'job1'})
    assert reported.wait(3)
    assert reports[0][:3] == ('job1', 1, 'blocked')
    assert reports[0][3]['error_code'] == 'idle_link_recovery_failed'
    assert daemon._current_job_id == 'job1'
    # 重复终态期间不允许重新创建已清理的任务资源。
    reported.clear()
    monkeypatch.setattr(adapter, 'setup_tun', lambda *a: pytest.fail('重复终态不应重新 reset'))
    daemon.control_plane.handle_broadcast({'type': 'JOB_ABORT', 'job_id': 'job1'})
    assert reported.wait(2)
    assert reports[1] == reports[0]


def test_real_cleanup_retry_advances_epoch_after_blocked(host, monkeypatch):
    daemon, adapter, reports, reported = host
    daemon.sandbox._command_prefix = [sys.executable, '-c', 'import time; time.sleep(30)']
    daemon.trigger_job(_make_client_job(job_id='job1'))
    real_ready = adapter.is_tun_ready
    monkeypatch.setattr(adapter, 'is_tun_ready', lambda *a: False)
    daemon.control_plane.handle_broadcast({'type': 'JOB_ABORT', 'job_id': 'job1'})
    assert reported.wait(3)
    assert reports[0][:3] == ('job1', 1, 'blocked')
    # 只有显式再次执行真实清理并重新检查，才能形成新的恢复事实。
    reported.clear()
    monkeypatch.setattr(adapter, 'is_tun_ready', real_ready)
    daemon.abort_job()
    assert reported.wait(2)
    assert reports[-1][:3] == ('job1', 2, 'ready')
    assert daemon._current_job_id is None


def test_missing_report_ack_replays_fact_without_recovering_again(host, monkeypatch):
    daemon, adapter, reports, reported = host
    daemon.sandbox._command_prefix = [sys.executable, '-c', 'import time; time.sleep(30)']
    daemon.trigger_job(_make_client_job(job_id='job1'))
    send = daemon.control_plane.notify_job_recovery
    failed = threading.Event()

    def lose_ack(*args, **kwargs):
        send(*args, **kwargs)
        failed.set()
        raise TimeoutError('模拟恢复 ACK 丢失')

    monkeypatch.setattr(daemon.control_plane, 'notify_job_recovery', lose_ack)
    daemon.control_plane.handle_broadcast({'type': 'JOB_ABORT', 'job_id': 'job1'})
    assert failed.wait(3)
    teardown = adapter.teardown_tun
    monkeypatch.setattr(adapter, 'teardown_tun', lambda *a: pytest.fail('ACK 丢失不得再次清理 TUN'))
    monkeypatch.setattr(daemon.control_plane, 'notify_job_recovery', send)
    reported.clear()
    daemon.control_plane.handle_broadcast({'type': 'JOB_ABORT', 'job_id': 'job1'})
    try:
        assert reported.wait(2)
        assert reports[1] == reports[0]
    finally:
        monkeypatch.setattr(adapter, 'teardown_tun', teardown)


def test_idle_link_start_wait_keeps_downlink_available(host, monkeypatch):
    import subprocess
    from dataclasses import replace
    daemon, adapter, reports, reported = host
    daemon.sandbox._command_prefix = [sys.executable, '-c', 'import time; time.sleep(30)']
    daemon.trigger_job(_make_client_job(job_id='job1'))
    daemon.config = replace(daemon.config, enable_link_process=True)
    checking = threading.Event()
    allow_ready = threading.Event()
    original_ready = adapter.is_tun_ready

    def ready(*args):
        checking.set()
        return allow_ready.is_set() and original_ready(*args)

    real_popen = subprocess.Popen

    def link(command, **kwargs):
        adapter.setup_tun('fl-c1', '10.80.0.11/24')
        return real_popen([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)

    daemon._link_process_factory = link
    monkeypatch.setattr(adapter, 'is_tun_ready', ready)
    monkeypatch.setattr('wfb_ng.fl.client_daemon.shutil.which', lambda *a: sys.executable)
    daemon.control_plane.handle_broadcast({'type': 'JOB_ABORT', 'job_id': 'job1'})
    assert checking.wait(3)
    assert not reports
    dispatched = threading.Event()
    daemon.control_plane.register_broadcast_handler('PROBE', lambda msg: dispatched.set())
    receiver = threading.Thread(target=lambda: daemon.control_plane.handle_broadcast({'type': 'PROBE'}))
    receiver.start()
    try:
        assert dispatched.wait(.5), '等待待命 TUN/路由时下行仍能处理消息'
        allow_ready.set()
        assert reported.wait(3)
        assert reports[0][:3] == ('job1', 1, 'ready')
        assert daemon.is_idle_link_running
    finally:
        allow_ready.set()
        receiver.join(3)


def test_task_tun_cleanup_failure_stays_blocked_until_real_retry(host, monkeypatch):
    daemon, adapter, reports, reported = host
    daemon.sandbox._command_prefix = [sys.executable, '-c', 'import time; time.sleep(30)']
    daemon.trigger_job(_make_client_job(job_id='job1'))
    adapter.setup_tun('fl-c1', '10.80.0.11/24')
    teardown = adapter.teardown_tun
    monkeypatch.setattr(adapter, 'teardown_tun', lambda *a: None)
    daemon.control_plane.handle_broadcast({'type': 'JOB_ABORT', 'job_id': 'job1'})
    assert reported.wait(3)
    assert reports[0][:3] == ('job1', 1, 'blocked')
    assert reports[0][3]['error_code'] == 'task_tun_cleanup_failed'
    reported.clear()
    monkeypatch.setattr(adapter, 'teardown_tun', teardown)
    daemon.abort_job()
    assert reported.wait(3)
    assert reports[-1][:3] == ('job1', 2, 'ready')


def test_terminal_during_spawn_cancels_without_blocking_downlink(host, monkeypatch):
    import subprocess
    daemon, adapter, reports, reported = host
    spawning = threading.Event()
    release = threading.Event()
    processes = []
    real_popen = subprocess.Popen

    def spawn(command, **kwargs):
        spawning.set()
        assert release.wait(3)
        proc = real_popen([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
        processes.append(proc)
        return proc

    monkeypatch.setattr(subprocess, 'Popen', spawn)
    announce = dict(type='TASK_ANNOUNCE', job_id='job1', run_id='run1', target_nodes=[1],
        rounds=1, preparation_timeout_seconds=10, algorithm='wfb_ng.fl.mnist:client_entry',
        algorithm_config={}, io_timeout_seconds=10, live_observation=False,
        server_http_host='10.80.0.1', server_http_port=8080, uftp_port=1044, link_id=7669206)
    daemon.control_plane.handle_broadcast(announce)
    assert spawning.wait(2)
    dispatched = threading.Event()

    def receive():
        daemon.control_plane.handle_broadcast({'type': 'JOB_ABORT', 'job_id': 'job1'})
        dispatched.set()

    receiver = threading.Thread(target=receive)
    receiver.start()
    try:
        assert dispatched.wait(.5), '启动期间接收线程必须仍能处理停止'
        assert not reports
        release.set()
        assert reported.wait(4)
        assert reports[0][:3] == ('job1', 1, 'ready')
        assert len(processes) == 1
        assert processes[0].poll() is not None
    finally:
        release.set()
        receiver.join(3)


def test_rejected_task_reports_recovery_for_its_server_terminal(host, monkeypatch):
    daemon, adapter, reports, reported = host
    rejected = threading.Event()
    monkeypatch.setattr(daemon.control_plane, 'notify_task_error', lambda *a, **kw: rejected.set())
    daemon.control_plane.handle_broadcast(dict(type='TASK_ANNOUNCE', job_id='badjob', run_id='run1',
        target_nodes=[1], rounds=1, preparation_timeout_seconds=10,
        algorithm='wfb_ng.fl.mnist:client_entry', algorithm_config={'local_dir': 'forbidden'},
        io_timeout_seconds=10, live_observation=False, server_http_host='10.80.0.1',
        server_http_port=8080, uftp_port=1044, link_id=7669206))
    assert rejected.wait(2)
    daemon.control_plane.handle_broadcast({'type': 'JOB_ABORT', 'job_id': 'badjob'})
    assert reported.wait(2), '已拒绝且未启动进程的参与者也必须交付恢复事实'
    assert reports[0][:3] == ('badjob', 1, 'ready')


def test_unexited_idle_transport_prevents_task_spawn(host, monkeypatch):
    from dataclasses import replace
    from wfb_ng.fl.errors import FLRuntimeError
    daemon, adapter, reports, reported = host

    class UnexitedLink:
        returncode = None

        def terminate(self):
            pass

        def kill(self):
            pass

        def wait(self, timeout=None):
            raise TimeoutError('内核尚未确认退出')

        def poll(self):
            return self.returncode

    link = UnexitedLink()
    daemon.config = replace(daemon.config, enable_link_process=True)

    def start_link(command, **kwargs):
        adapter.setup_tun('fl-c1', '10.80.0.11/24')
        return link

    daemon._link_process_factory = start_link
    monkeypatch.setattr('wfb_ng.fl.client_daemon.shutil.which', lambda *a: sys.executable)
    daemon.start_idle_link()
    try:
        with pytest.raises(FLRuntimeError) as error:
            daemon.trigger_job(_make_client_job(job_id='job1'))
        assert error.value.error_code == 'idle_link_not_exited'
        assert daemon.is_idle_link_running, '退出未确认时保留句柄，不能启动冲突任务'
        assert not os.path.exists(os.path.join(daemon.config.work_dir, 'job_job1'))
    finally:
        link.returncode = -9


def test_fractional_io_budget_survives_task_announce_and_role_config(host, monkeypatch):
    import json
    import subprocess
    daemon, adapter, reports, reported = host
    spawned = threading.Event()
    configs = []
    real_popen = subprocess.Popen

    def spawn(command, **kwargs):
        with open(command[command.index('--config') + 1], encoding='utf-8') as source:
            configs.append(json.load(source))
        process = real_popen([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
        spawned.set()
        return process

    monkeypatch.setattr(subprocess, 'Popen', spawn)
    daemon.control_plane.handle_broadcast(dict(type='TASK_ANNOUNCE', job_id='fractional', run_id='run1',
        target_nodes=[1], rounds=1, preparation_timeout_seconds=10.5,
        algorithm='wfb_ng.fl.mnist:client_entry', algorithm_config={},
        io_timeout_seconds=.2, live_observation=False, server_http_host='10.80.0.1',
        server_http_port=8080, uftp_port=1044, link_id=7669206))
    assert spawned.wait(2), '正的小数 I/O 预算必须能启动任务'
    assert configs[0]['io_timeout_seconds'] == .2, '冻结并下发原始小数，不能截断成整数'
    daemon.control_plane.handle_broadcast({'type': 'JOB_ABORT', 'job_id': 'fractional'})
    assert reported.wait(3)
    assert reports[0][:3] == ('fractional', 1, 'ready')


@pytest.mark.parametrize('budget', [True, False, 0, -.2, float('nan'), float('inf'), -float('inf'), '0.2', None])
def test_invalid_io_budget_is_rejected_by_config_and_task_announce(host, monkeypatch, budget):
    from wfb_ng.fl.errors import FLRuntimeError
    with pytest.raises(FLRuntimeError) as error:
        _make_client_job(io_timeout_seconds=budget)
    assert error.value.error_code == 'invalid_io_timeout'
    daemon, adapter, reports, reported = host
    rejected = threading.Event()
    monkeypatch.setattr(daemon.control_plane, 'notify_task_error', lambda *a, **kw: rejected.set())
    daemon.control_plane.handle_broadcast(dict(type='TASK_ANNOUNCE', job_id='invalidbudget', run_id='run1',
        target_nodes=[1], rounds=1, preparation_timeout_seconds=10,
        algorithm='wfb_ng.fl.mnist:client_entry', algorithm_config={},
        io_timeout_seconds=budget, live_observation=False, server_http_host='10.80.0.1',
        server_http_port=8080, uftp_port=1044, link_id=7669206))
    assert rejected.wait(2)
    assert not os.path.exists(os.path.join(daemon.config.work_dir, 'job_invalidbudget'))


def test_unacknowledged_ready_keeps_binding_and_idle_gate(host, monkeypatch):
    from wfb_ng.fl.control import ClientNodeState
    daemon, adapter, reports, reported = host
    daemon.sandbox._command_prefix = [sys.executable, '-c', 'import time; time.sleep(30)']
    daemon.trigger_job(_make_client_job(job_id='job1'))
    failed = threading.Event()

    def fail(*args, **kwargs):
        failed.set()
        raise TimeoutError('恢复事实未获服务端确认')

    monkeypatch.setattr(daemon.control_plane, 'notify_job_recovery', fail)
    daemon.control_plane.handle_broadcast({'type': 'JOB_ABORT', 'job_id': 'job1'})
    assert failed.wait(3)
    worker = daemon._recovery_thread
    if worker is not None:
        worker.join(2)
    assert daemon._current_job_id == 'job1', '本地 ready 不能替代服务器 ACK 解锁接单'
    assert daemon.control_plane.state != ClientNodeState.IDLE
    from wfb_ng.fl.client_daemon import DaemonState
    assert daemon.state == DaemonState.ABORTING
    daemon.control_plane.handle_broadcast(dict(type='TASK_ANNOUNCE', job_id='job2', run_id='run1',
        target_nodes=[1], rounds=1, preparation_timeout_seconds=10,
        algorithm='wfb_ng.fl.mnist:client_entry', algorithm_config={},
        io_timeout_seconds=1, live_observation=False, server_http_host='10.80.0.1',
        server_http_port=8080, uftp_port=1044, link_id=7669206))
    assert daemon._last_accepted_job_id == 'job1'
    from wfb_ng.fl.errors import FLRuntimeError
    with pytest.raises(FLRuntimeError) as error:
        daemon.trigger_job(_make_client_job(job_id='job2'))
    assert error.value.error_code == 'job_recovery_pending'


def test_control_rebuild_retries_same_report_id_without_resource_reset(host, monkeypatch):
    daemon, adapter, reports, reported = host
    daemon.sandbox._command_prefix = [sys.executable, '-c', 'import time; time.sleep(30)']
    daemon.trigger_job(_make_client_job(job_id='job1'))
    sent = []
    failed = threading.Event()

    def lose_ack(job_id, epoch, state, **kwargs):
        sent.append((job_id, epoch, state, kwargs))
        failed.set()
        raise TimeoutError('模拟第一次 ACK 丢失')

    monkeypatch.setattr(daemon.control_plane, 'notify_job_recovery', lose_ack)
    daemon.control_plane.handle_broadcast({'type': 'JOB_ABORT', 'job_id': 'job1'})
    assert failed.wait(3)
    worker = daemon._recovery_thread
    if worker is not None:
        worker.join(2)
    assert daemon._current_job_id == 'job1'
    first_control = daemon.control_plane
    daemon.start_control_plane()
    assert daemon.control_plane is not first_control
    acknowledged = threading.Event()

    def acknowledge(job_id, epoch, state, **kwargs):
        sent.append((job_id, epoch, state, kwargs))
        acknowledged.set()

    monkeypatch.setattr(daemon.control_plane, 'notify_job_recovery', acknowledge)
    teardown = adapter.teardown_tun
    monkeypatch.setattr(adapter, 'teardown_tun', lambda *a: pytest.fail('更换 control 后不得重置已恢复资源'))
    try:
        daemon.control_plane.handle_broadcast({'type': 'JOB_ABORT', 'job_id': 'job1'})
        assert acknowledged.wait(2)
        worker = daemon._recovery_thread
        if worker is not None:
            worker.join(2)
        assert sent[1] == sent[0]
        assert isinstance(sent[0][3]['report_id'], str) and sent[0][3]['report_id']
        assert daemon._current_job_id is None
    finally:
        monkeypatch.setattr(adapter, 'teardown_tun', teardown)
        first_control.stop()


def test_rebuilt_control_encodes_host_cached_report_id(host, monkeypatch):
    daemon, adapter, reports, reported = host
    daemon.sandbox._command_prefix = [sys.executable, '-c', 'import time; time.sleep(30)']
    daemon.trigger_job(_make_client_job(job_id='job1'))
    envelopes = []
    sent = threading.Event()

    def wire(control, payload, *args, **kwargs):
        envelopes.append(payload)
        sent.set()
        if len(envelopes) == 1:
            raise TimeoutError('服务器已收到但 ACK 丢失')

    # 真实 ControlPeer 编码，仅在网络报告边界控制 ACK。
    monkeypatch.delattr(daemon.control_plane, 'notify_job_recovery')
    monkeypatch.setattr(ControlPlaneClient, '_notify_report', wire)
    first = daemon.control_plane
    daemon.control_plane.handle_broadcast({'type': 'JOB_ABORT', 'job_id': 'job1'})
    assert sent.wait(3)
    worker = daemon._recovery_thread
    if worker is not None:
        worker.join(2)
    assert daemon._current_job_id == 'job1'
    sent.clear()
    daemon.start_control_plane()
    try:
        assert daemon.control_plane is not first
        daemon.control_plane.handle_broadcast({'type': 'JOB_ABORT', 'job_id': 'job1'})
        assert sent.wait(3)
        worker = daemon._recovery_thread
        if worker is not None:
            worker.join(2)
        assert envelopes[0] == envelopes[1]
        assert envelopes[0]['report_id'] == daemon._recovery_fact[5]
        assert daemon._current_job_id is None
    finally:
        first.stop()


@pytest.mark.parametrize('address', [('10.80.0.1', 9001), ('255.255.255.255', 9002),
                                     ('127.0.0.1', 9200), ('127.0.0.1', 9001)])
def test_socket_boundary_rejects_external_or_live_control_endpoint(address):
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as peer:
        with pytest.raises(AssertionError):
            peer.sendto(b'forbidden-test-packet', address)


def test_socket_boundary_rejects_wildcard_listener():
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as peer:
        with pytest.raises(AssertionError):
            peer.bind(('0.0.0.0', 0))
