"""作业期射频接线：只使用回环动态端口与真实沙箱进程。"""
import json
import sys

import pytest

from wfb_ng.fl.client_daemon import ClientDaemon, ClientDaemonConfig
from wfb_ng.tests._client_test_network import client_network_kwargs, isolated_client_network
from wfb_ng.tests.test_fl_client_daemon import MockNetworkAdapter, _make_client_job


def test_task_link_receives_daemon_radio_control_endpoint(tmp_path):
    config = ClientDaemonConfig(**client_network_kwargs(), node_id=1, tun_ip='10.80.0.11',
        work_dir=str(tmp_path), enable_link_process=False)
    daemon = ClientDaemon(config, network_adapter=MockNetworkAdapter(['wlan0']))
    daemon.sandbox.evidence_dir = str(tmp_path / 'evidence')
    daemon.sandbox._command_prefix = [sys.executable, '-c', 'import time; time.sleep(30)']
    daemon.poll_hardware_once()
    try:
        process = daemon.trigger_job(_make_client_job(job_id='radio-job'))
        role = json.loads((tmp_path / 'job_radio-job' / 'client_role.json').read_text())
        args = role['link_args']
        assert '--radio-control-port' in args, '任务链路必须接收宿主配置的在线更新端点'
        assert args[args.index('--radio-control-port') + 1] == str(config.radio_control_port)
        assert process.poll() is None
    finally:
        daemon.stop()


def test_task_v6_endpoint_uses_original_updater_without_changing_owner(tmp_path):
    import threading
    import time
    from wfb_ng.fl.link_radio import apply_link_radio
    config = ClientDaemonConfig(**client_network_kwargs(), node_id=1, tun_ip='10.80.0.11',
        work_dir=str(tmp_path), enable_link_process=False)
    adapter = MockNetworkAdapter(['wlan0'])
    daemon = ClientDaemon(config, network_adapter=adapter)
    daemon.sandbox.evidence_dir = str(tmp_path / 'evidence')
    # 真实子进程模拟当前任务 v6 link，按角色配置监听动态回环端口。
    program = '''
import json, pathlib, socket, struct, sys
work = pathlib.Path(sys.argv[1])
args = json.loads((work / 'client_role.json').read_text())['link_args']
port = int(args[args.index('--radio-control-port') + 1])
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
    udp.bind(('127.0.0.1', port))
    (work / 'v6-ready').touch()
    while True:
        request, peer = udp.recvfrom(4096)
        values = struct.unpack('!IB7B', request)
        (work / 'v6-update.json').write_text(json.dumps(list(values[1:])))
        udp.sendto(request[:4] + struct.pack('!I', 0), peer)
'''
    work = tmp_path / 'job_radio-job'
    daemon.sandbox._command_prefix = [sys.executable, '-c', program, str(work)]
    daemon.poll_hardware_once()
    try:
        process = daemon.trigger_job(_make_client_job(job_id='radio-job'))
        adapter.setup_tun(config.tun_name, config.tun_cidr)
        deadline = time.monotonic() + 3
        while not (work / 'v6-ready').exists() and time.monotonic() < deadline:
            assert process.poll() is None, 'fake v6 不得提前退出'
            threading.Event().wait(.01)
        assert (work / 'v6-ready').exists()
        owner = (process.pid, dict(adapter.tun_interfaces))
        apply_link_radio(config.radio_control_port, 40, 5)
        assert json.loads((work / 'v6-update.json').read_text()) == [2, 0, 0, 1, 40, 5, 0, 1]
        assert (process.pid, adapter.tun_interfaces) == owner
        assert process.poll() is None
        assert not daemon.is_idle_link_running
    finally:
        daemon.stop()


@pytest.fixture
def running_job_radio(tmp_path, monkeypatch, request):
    import subprocess
    import threading
    from wfb_ng.fl.control import ControlPlaneClient
    config = ClientDaemonConfig(**client_network_kwargs(), node_id=1, tun_ip='10.80.0.11',
        work_dir=str(tmp_path), channel=153, enable_link_process=False)
    adapter = MockNetworkAdapter(['wlan0'])
    daemon = ClientDaemon(config, network_adapter=adapter)
    daemon.sandbox.evidence_dir = str(tmp_path / 'evidence')
    daemon.poll_hardware_once()
    monkeypatch.setattr(ControlPlaneClient, 'start', lambda self: None)
    daemon.start_control_plane()
    notice = threading.Event()
    ack = threading.Event()

    kind = getattr(request, 'param', 'prepared')

    def ready(*args, **kwargs):
        notice.set()
        assert ack.wait(4)

    monkeypatch.setattr(daemon.control_plane, 'notify_task_ready',
                        ready if kind == 'prepared' else lambda *a, **kw: None)
    monkeypatch.setattr(daemon.control_plane, 'notify_round_complete', ready)
    monkeypatch.setattr(daemon.control_plane, 'notify_task_error', lambda *a, **kw: None)
    program = '''
import json, os, pathlib, socket, struct, sys, threading
from wfb_ng.fl.service import AlgorithmNotifications
role = json.loads(pathlib.Path(sys.argv[1]).read_text())
work = pathlib.Path(role['work_dir'])
args = role['link_args']
port = int(args[args.index('--radio-control-port') + 1])
udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
udp.bind(('127.0.0.1', port))
def v6():
    while True:
        request, peer = udp.recvfrom(4096)
        (work / 'v6-update.json').write_text(json.dumps(list(struct.unpack('!IB7B', request)[1:])))
        udp.sendto(request[:4] + struct.pack('!I', 0), peer)
threading.Thread(target=v6, daemon=True).start()
n = AlgorithmNotifications(os.environ['WFB_FL_NOTIFY_SOCKET'], 'radio-job', threading.Event(), timeout=8)
n.prepared({'ready': True})
if sys.argv[2] == 'round_complete':
    round_dir = work / 'rounds' / 'round-0001'
    round_dir.mkdir(parents=True)
    (round_dir / 'round-state.json').write_text(json.dumps(dict(schema_version=1,
        round_id='round-0001', role='client', state='succeeded')))
    n.round_complete(1)
threading.Event().wait()
'''
    real_popen = subprocess.Popen

    def spawn(command, **kwargs):
        return real_popen([sys.executable, '-c', program, command[command.index('--config') + 1], kind], **kwargs)

    monkeypatch.setattr(subprocess, 'Popen', spawn)
    daemon.control_plane.handle_broadcast(dict(type='TASK_ANNOUNCE', job_id='radio-job', run_id='run1',
        target_nodes=[1], rounds=2, preparation_timeout_seconds=10,
        algorithm='fixture:run', algorithm_config={}, io_timeout_seconds=8,
        live_observation=False, server_http_host='127.0.0.1', server_http_port=config.server_control_port,
        uftp_port=config.broadcast_port + 1, link_id=config.link_id))
    assert notice.wait(3)
    adapter.setup_tun(config.tun_name, config.tun_cidr)
    try:
        yield daemon, adapter, tmp_path / 'job_radio-job'
    finally:
        ack.set()
        daemon.stop()


def test_prepared_notice_before_report_ack_allows_current_task_update(running_job_radio):
    from dataclasses import replace
    daemon, adapter, work = running_job_radio
    before = daemon._effective_radio
    target = replace(before, uplink_mcs=5)
    process = daemon.sandbox._process
    owner = (process.pid, dict(adapter.tun_interfaces))
    assert not daemon.sandbox._prepared, '服务端准备报告 ACK 尚未返回'
    daemon.control_plane.on_radio_prepare('radio-job', target)
    daemon.control_plane.on_radio_applied(target)
    assert json.loads((work / 'v6-update.json').read_text()) == [2, 0, 0, 1, 40, 5, 0, 1]
    assert (daemon.sandbox._process.pid, adapter.tun_interfaces) == owner
    assert process.poll() is None
    assert not daemon.is_idle_link_running


@pytest.mark.parametrize('state', ['model_received', 'submitting_update', 'failed'])
def test_runtime_busy_phase_rejects_stale_prepared_boundary(running_job_radio, state):
    from dataclasses import replace
    from wfb_ng.fl.errors import FLRuntimeError
    daemon, adapter, work = running_job_radio
    round_dir = work / 'rounds' / 'round-0001'
    round_dir.mkdir(parents=True)
    (round_dir / 'round-state.json').write_text(json.dumps(dict(schema_version=1,
        round_id='round-0001', role='client', state=state)))
    with pytest.raises(FLRuntimeError) as error:
        daemon.control_plane.on_radio_prepare('radio-job', replace(daemon._effective_radio, uplink_mcs=5))
    assert error.value.error_code == 'radio_job_busy'
    assert not (work / 'v6-update.json').exists()


def test_wrong_job_and_channel_rejected_before_task_link_update(running_job_radio):
    from dataclasses import replace
    from wfb_ng.fl.errors import FLRuntimeError
    daemon, adapter, work = running_job_radio
    for job_id, target, expected in [
        ('stale', replace(daemon._effective_radio, uplink_mcs=5), 'radio_job_mismatch'),
        ('radio-job', replace(daemon._effective_radio, channel=157), 'radio_job_channel_forbidden'),
    ]:
        with pytest.raises(FLRuntimeError) as error:
            daemon.control_plane.on_radio_prepare(job_id, target)
        assert error.value.error_code == expected
    assert not (work / 'v6-update.json').exists()


@pytest.mark.parametrize('running_job_radio', ['round_complete'], indirect=True)
def test_round_notice_before_round_ready_ack_allows_task_update(running_job_radio):
    from dataclasses import replace
    daemon, adapter, work = running_job_radio
    process = daemon.sandbox._process
    owner = (process.pid, dict(adapter.tun_interfaces))
    assert daemon.sandbox._completed_round == 0, 'ROUND_READY 的 ACK 仍未返回'
    target = replace(daemon._effective_radio, uplink_mcs=5)
    daemon.control_plane.on_radio_prepare('radio-job', target)
    daemon.control_plane.on_radio_applied(target)
    assert json.loads((work / 'v6-update.json').read_text()) == [2, 0, 0, 1, 40, 5, 0, 1]
    assert (process.pid, adapter.tun_interfaces) == owner
    assert not daemon.is_idle_link_running


def test_ordinary_ht20_without_table_rejected_before_task_update(running_job_radio):
    daemon, adapter, work = running_job_radio
    before = daemon._effective_radio
    daemon.control_plane.handle_broadcast(dict(type='CONFIG_RADIO_PREPARE',
        session_id='ordinary20', job_id='radio-job', target_nodes=[1],
        patch={'bandwidth_mhz': 20, 'downlink_mcs': 3, 'uftp_rate_kbps': 1000}))
    datagrams = daemon.control_plane._uplink_sock.datagrams
    ack = json.loads(datagrams[-1][0])
    assert ack['type'] == 'PREPARE_ACK' and ack['ok'] is False
    assert daemon._effective_radio == before
    assert not (work / 'v6-update.json').exists()
    assert not daemon.is_idle_link_running


@pytest.fixture
def radio_wire(running_job_radio):
    """真实回环 UDP 输入原广播解析循环；ACK 出口仍是 FakeControlPeer。"""
    import socket
    import threading
    from wfb_ng.tests._client_test_network import FakeControlPeer
    daemon, adapter, work = running_job_radio
    control = daemon.control_plane
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(('127.0.0.1', 0))
    receiver.settimeout(.05)
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sender.bind(('127.0.0.1', 0))
    messages = []
    changed = threading.Event()
    delivered = threading.Event()
    control.register_broadcast_handler('TEST_DELIVERY_BARRIER', lambda message: delivered.set())
    peer = FakeControlPeer()
    send = peer.sendto

    def capture(data, address):
        messages.append(json.loads(data))
        send(data, address)
        changed.set()

    peer.sendto = capture
    control._uplink_sock = peer
    control._broadcast_listener_sock = receiver
    worker = threading.Thread(target=control._broadcast_loop)
    worker.start()

    def exchange(message, expected_type=None):
        changed.clear()
        offset = len(messages)
        sender.sendto(json.dumps(message).encode(), receiver.getsockname())
        if expected_type is not None:
            assert changed.wait(2), f'未收到 {expected_type}'
            result = next(item for item in messages[offset:] if item['type'] == expected_type)
            return result
        delivered.clear()
        sender.sendto(b'{"type":"TEST_DELIVERY_BARRIER"}', receiver.getsockname())
        assert delivered.wait(2), '原广播循环必须消费先前帧'

    try:
        yield daemon, adapter, work, exchange, messages
    finally:
        control._stop_event.set()
        worker.join(2)
        assert not worker.is_alive()
        receiver.close()
        sender.close()
        control._broadcast_listener_sock = None


def test_task_acceptance_binds_control_and_rebuild_keeps_job(running_job_radio):
    daemon, adapter, work = running_job_radio
    control = daemon.control_plane
    assert not control._accepts_job_transaction('another-job'), '宿主受理后必须绑定ControlPeer'
    assert control._accepts_job_transaction('radio-job')
    daemon.start_control_plane()
    assert daemon.control_plane is not control
    assert not daemon.control_plane._accepts_job_transaction('another-job')
    control.stop()


@pytest.mark.parametrize('running_job_radio', ['prepared', 'round_complete'], indirect=True)
def test_wire_transaction_updates_task_link_before_notice_ack(radio_wire):
    from dataclasses import replace
    import threading
    import time
    daemon, adapter, work, exchange, messages = radio_wire
    process = daemon.sandbox._process
    owner = (process.pid, dict(adapter.tun_interfaces))
    expected = replace(daemon._effective_radio, uplink_mcs=5, radio_txpower_dbm=14)
    patch = {'uplink_mcs': 5, 'radio_txpower_dbm': 14}
    base = dict(job_id='radio-job', session_id='wire-task', target_nodes=[1])
    ack = exchange(dict(base, type='CONFIG_RADIO_PREPARE', patch=patch), 'PREPARE_ACK')
    assert ack['ok'] is True and ack['job_id'] == 'radio-job'
    assert not (work / 'v6-update.json').exists(), 'PREPARE不得提前修改物理配置'
    exchange(dict(base, type='CONFIG_RADIO_COMMIT', patch=patch, delay_ms=0))
    deadline = time.monotonic() + 2
    while not (work / 'v6-update.json').exists() and time.monotonic() < deadline:
        threading.Event().wait(.01)
    assert (work / 'v6-update.json').exists()
    assert daemon.control_plane.radio_switch_pending  # 同步等待 COMMIT 工作线程释放状态锁。
    final = dict(base, channel=expected.channel, config=expected.to_dict(), calibration=False)
    success = exchange(dict(final, type='NEW_CHANNEL_PING'), 'COMMIT_SUCCESS')
    assert success['job_id'] == 'radio-job'
    exchange(dict(final, type='RADIO_SWITCH_FINALIZED'), 'RADIO_SWITCH_FINALIZED_ACK')
    exchange(dict(final, type='RADIO_SWITCH_CONFIRMED'), 'RADIO_SWITCH_CONFIRMED_ACK')
    assert not daemon.control_plane.radio_switch_pending
    assert daemon._effective_radio == expected
    assert adapter.wireless_states['wlan0']['txpower_dbm'] == 14
    assert json.loads((work / 'v6-update.json').read_text()) == [2, 0, 0, 1, 40, 5, 0, 1]
    assert (process.pid, adapter.tun_interfaces) == owner
    assert process.poll() is None
    assert not daemon.is_idle_link_running


@pytest.mark.parametrize('state', ['model_received', 'submitting_update', 'failed'])
def test_wire_busy_prepare_rejected_before_ack_and_physical_change(radio_wire, state):
    daemon, adapter, work, exchange, messages = radio_wire
    round_dir = work / 'rounds' / 'round-0001'
    round_dir.mkdir(parents=True)
    (round_dir / 'round-state.json').write_text(json.dumps(dict(schema_version=1,
        round_id='round-0001', role='client', state=state)))
    before = (daemon._effective_radio, dict(adapter.tun_interfaces),
              list(adapter.channel_history), dict(adapter.wireless_states['wlan0']))
    base = dict(job_id='radio-job', session_id='busy-task', target_nodes=[1],
                patch={'uplink_mcs': 5, 'radio_txpower_dbm': 14})
    ack = exchange(dict(base, type='CONFIG_RADIO_PREPARE'), 'PREPARE_ACK')
    assert ack['ok'] is False and ack['job_id'] == 'radio-job'
    exchange(dict(base, type='CONFIG_RADIO_COMMIT', delay_ms=0))
    assert not daemon.control_plane.radio_switch_pending
    assert (daemon._effective_radio, adapter.tun_interfaces,
            adapter.channel_history, adapter.wireless_states['wlan0']) == before
    assert not (work / 'v6-update.json').exists()


def test_wire_different_job_ignored_before_physical_change(radio_wire):
    daemon, adapter, work, exchange, messages = radio_wire
    before = len(messages)
    base = dict(job_id='another-job', session_id='wrong-job', target_nodes=[1],
                patch={'uplink_mcs': 5, 'radio_txpower_dbm': 14})
    exchange(dict(base, type='CONFIG_RADIO_PREPARE'))
    exchange(dict(base, type='CONFIG_RADIO_COMMIT', delay_ms=0))
    assert len(messages) == before
    assert not daemon.control_plane.radio_switch_pending
    assert not (work / 'v6-update.json').exists()


def test_wire_reversible_failure_restores_operator_channel_and_same_owner(radio_wire):
    import threading
    import time
    daemon, adapter, work, exchange, messages = radio_wire
    before = daemon._effective_radio
    assert before.channel == 153
    process = daemon.sandbox._process
    owner = (process.pid, dict(adapter.tun_interfaces))
    base = dict(job_id='radio-job', session_id='rollback-job', target_nodes=[1])
    patch = {'uplink_mcs': 5, 'radio_txpower_dbm': 14}
    ack = exchange(dict(base, type='CONFIG_RADIO_PREPARE', patch=patch), 'PREPARE_ACK')
    assert ack['ok']
    exchange(dict(base, type='CONFIG_RADIO_COMMIT', patch=patch, delay_ms=0))
    deadline = time.monotonic() + 2
    while not (work / 'v6-update.json').exists() and time.monotonic() < deadline:
        threading.Event().wait(.01)
    assert (work / 'v6-update.json').exists()
    assert daemon.control_plane.radio_switch_pending
    daemon._job_stop_event.set()  # 已成立事务停止时仍须允许提交前回滚。
    exchange(dict(base, type='CONFIG_RADIO_ABORT'), 'RADIO_SWITCH_ROLLBACK_ACK')
    assert daemon._effective_radio == before
    assert adapter.wireless_states['wlan0']['channel'] == 153
    assert adapter.wireless_states['wlan0']['txpower_dbm'] == before.radio_txpower_dbm
    assert json.loads((work / 'v6-update.json').read_text()) == [2, 0, 0, 1, 40, before.uplink_mcs, 0, 1]
    assert (process.pid, adapter.tun_interfaces) == owner
    assert process.poll() is None
    assert not daemon.is_idle_link_running


def test_control_binding_clears_only_after_ready_report_ack(tmp_path, monkeypatch):
    import threading
    from wfb_ng.fl.control import ControlPlaneClient
    config = ClientDaemonConfig(**client_network_kwargs(), node_id=1, tun_ip='10.80.0.11',
        work_dir=str(tmp_path), enable_link_process=False)
    daemon = ClientDaemon(config, network_adapter=MockNetworkAdapter(['wlan0']))
    daemon.poll_hardware_once()
    monkeypatch.setattr(ControlPlaneClient, 'start', lambda self: None)
    daemon.start_control_plane()
    daemon.sandbox.evidence_dir = str(tmp_path / 'evidence')
    daemon.sandbox._command_prefix = [sys.executable, '-c', 'import time; time.sleep(30)']
    daemon.trigger_job(_make_client_job(job_id='radio-job'))
    report = threading.Event()
    acknowledged = threading.Event()

    def acknowledge(*args, **kwargs):
        report.set()
        assert acknowledged.wait(3)

    monkeypatch.setattr(daemon.control_plane, 'notify_job_recovery', acknowledge)
    try:
        daemon.control_plane.handle_broadcast(dict(type='JOB_COMPLETED', job_id='radio-job'))
        assert report.wait(3)
        assert not daemon.control_plane._accepts_job_transaction('next-job')
        assert daemon._current_job_id == 'radio-job'
        acknowledged.set()
        worker = daemon._recovery_thread
        if worker is not None:
            worker.join(3)
            assert not worker.is_alive()
        assert daemon._current_job_id is None
        assert daemon.control_plane._accepts_job_transaction('next-job')
    finally:
        acknowledged.set()
        daemon.stop()
