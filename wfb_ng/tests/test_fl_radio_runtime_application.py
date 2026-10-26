"""射频配置必须进入真实进程命令和后续数据面参数。"""
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
import threading
import subprocess
import json
import socket
import struct

import pytest

from wfb_ng.fl.errors import FLRuntimeError
from wfb_ng.fl.server_daemon import ServerDaemon, ServerDaemonConfig
from wfb_ng.tests.test_fl_client_daemon import MockNetworkAdapter
from wfb_ng.tests.test_fl_console import register_idle
from wfb_ng.tests import test_fl_radio_rest as radio_rest


class ObservedNetworkAdapter(MockNetworkAdapter):
    def __init__(self):
        super().__init__(['wlx-test'])
        self.tun_events = []

    def setup_tun(self, name, cidr):
        self.tun_events.append(('setup', name, cidr))
        super().setup_tun(name, cidr)

    def teardown_tun(self, name):
        self.tun_events.append(('teardown', name))
        super().teardown_tun(name)


def tun_snapshot(daemon):
    # 同时观察网卡调用，防止删除后重建同名 TUN 被误判为保留。
    return daemon.adapter.tun_interfaces.copy(), list(daemon.adapter.tun_events)


@contextmanager
def running_server(tmp_path, reject_updates=(), sender_finished=None, **config):
    # 模拟 v6 的本机 UDP 端点，生产 updater 原样参与测试。
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind(('127.0.0.1', 0))
    udp.settimeout(0.1)
    updates = []
    stopped = threading.Event()

    def serve():
        while not stopped.is_set():
            try:
                data, address = udp.recvfrom(4096)
            except socket.timeout:
                continue
            request = struct.unpack('!IB7B', data)
            updates.append(request[1:])
            status = 22 if len(updates) in reject_updates else 0
            udp.sendto(struct.pack('!II', request[0], status), address)

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    daemon = ServerDaemon(ServerDaemonConfig(ipc_port=0, enable_control_plane=False,
                         enable_link_process=True, air_interface='wlx-test',
                         radio_control_port=udp.getsockname()[1],
                         model_library_dir=str(tmp_path / 'models'), work_dir=str(tmp_path), **config),
                         ObservedNetworkAdapter())
    commands = []
    uftp_started = threading.Event()
    class Process:
        def __init__(self, pid, exited=None):
            self.pid = pid
            self.exited = threading.Event() if exited is None else exited
        def poll(self):
            return 0 if self.exited.is_set() else None
        def terminate(self):
            self.exited.set()
        kill = terminate
        def wait(self, timeout=None):
            if not self.exited.wait(timeout):
                raise subprocess.TimeoutExpired('test-process', timeout)
            return 0
    def spawn(command, **kwargs):
        commands.append(command)
        if '-R' in command:
            uftp_started.set()
        else:
            daemon.adapter.setup_tun(daemon.config.tun_name, daemon.config.tun_cidr)
        return Process(len(commands), sender_finished if '-R' in command else None)
    with patch('wfb_ng.fl.server_daemon.shutil.which', return_value='/test/wfb_v6_uplink'), patch(
            'wfb_ng.fl.server_daemon.subprocess.Popen', side_effect=spawn):
        daemon.start()
        register_idle(daemon)
        try:
            yield daemon, commands, uftp_started, updates
        finally:
            daemon.stop()
            stopped.set()
            worker.join(2)
            udp.close()


@pytest.mark.parametrize('fail_at,expected_status,expected_mcs', [
    (None, 'finalized', [4]),
    ('RADIO_SWITCH_FINALIZED', 'rolled_back', [4, 3]),
    ('RADIO_SWITCH_CONFIRMED', 'radio_error', [4]),
])
def test_radio_transaction_applies_server_mcs_before_final_barrier_and_restores_on_rollback(
        tmp_path, fail_at, expected_status, expected_mcs):
    with running_server(tmp_path) as (daemon, commands, _, updates):
        peer = radio_rest.TestRadioReconfigureREST()
        peer.daemon = daemon
        initial_link = daemon.console.snapshot()['server']['link_process'].copy()
        initial_tun = tun_snapshot(daemon)
        current = daemon.console.radio_configuration()['config']
        config = {key:current[key] for key in ('channel','bandwidth_mhz','radio_txpower_dbm','downlink_mcs','uplink_mcs','uftp_rate_kbps')}
        config.update(downlink_mcs=4, uftp_rate_kbps=22000)
        checked = daemon.console.validate_radio_configuration({'config':config})
        def respond(message):
            if message['type'] == fail_at:
                raise OSError('最终屏障广播失败')
            if message['type'] == 'RADIO_SWITCH_FINALIZED':
                assert updates[-1] == (2, 0, 0, 1, 40, 4, 0, 1)
            peer.protocol_peer(message)
        with patch.object(daemon.control_plane, 'broadcast_downlink', side_effect=respond):
            result = daemon.console.apply_radio_configuration({
                'confirmation_token':checked['confirmation']['token'], 'confirm_risk':False})
        assert result['status'] == expected_status
        assert [update[5] for update in updates] == expected_mcs
        assert len(commands) == 1
        assert daemon.console.snapshot()['server']['link_process'] == initial_link
        assert tun_snapshot(daemon) == initial_tun
        assert daemon.console.snapshot()['server']['link_process']['running']
        expected_phase = {'RADIO_SWITCH_FINALIZED':'FINALIZED', 'RADIO_SWITCH_CONFIRMED':'CONFIRMED'}.get(fail_at)
        assert result['failed_phase'] == expected_phase
        state = daemon.console.snapshot()['server']
        assert state['radio']['downlink_mcs'] == (3 if expected_status == 'rolled_back' else 4)
        assert state['state'] == ('radio_error' if expected_status == 'radio_error' else 'idle')


@pytest.mark.parametrize('reject_updates,status', [((1,), 'rolled_back'), ((1,2), 'radio_error')])
def test_server_update_rejection_restores_old_mcs_or_fails_closed(tmp_path, reject_updates, status):
    with running_server(tmp_path, reject_updates=reject_updates) as (daemon, commands, _, updates):
        peer = radio_rest.TestRadioReconfigureREST()
        peer.daemon = daemon
        initial_link = daemon.console.snapshot()['server']['link_process'].copy()
        initial_tun = tun_snapshot(daemon)
        payload = {'confirmed':True, 'patch':{'downlink_mcs':4}, 'target_nodes':[1]}
        with patch.object(daemon.control_plane, 'broadcast_downlink', side_effect=peer.protocol_peer):
            result = daemon.reconfigure_radio(payload)
        assert result['status'] == status
        assert result['failed_phase'] == 'COMMIT'
        assert [update[5] for update in updates] == [4, 3]
        assert len(commands) == 1
        assert daemon.console.snapshot()['server']['link_process'] == initial_link
        assert tun_snapshot(daemon) == initial_tun
        state = daemon.console.snapshot()['server']
        if status == 'rolled_back':
            assert state['radio']['downlink_mcs'] == 3
            assert state['state'] == 'idle'
            assert state['link_process']['running']
        else:
            assert state['radio'] is None
            assert state['state'] == 'radio_error'
            assert state['link_process']['running']


def test_channel_165_ht40_is_rejected_before_hardware_or_link_mutation(tmp_path):
    with running_server(tmp_path) as (daemon, commands, _, updates):
        initial_tun = tun_snapshot(daemon)
        initial_channels = daemon.adapter.channel_history.copy()
        with pytest.raises(FLRuntimeError) as rejected:
            daemon.reconfigure_radio({'confirmed': True, 'patch': {'channel': 165}, 'target_nodes': [1]})
        assert rejected.value.error_code == 'invalid_radio_request'
        assert updates == []
        assert len(commands) == 1
        assert daemon.adapter.channel_history == initial_channels
        assert tun_snapshot(daemon) == initial_tun


def test_controlled_ht20_calibration_updates_hardware_and_link_without_restart(tmp_path):
    with running_server(tmp_path) as (daemon, commands, _, updates):
        peer = radio_rest.TestRadioReconfigureREST()
        peer.daemon = daemon
        initial_link = daemon.console.snapshot()['server']['link_process'].copy()
        initial_tun = tun_snapshot(daemon)
        with patch.object(daemon.control_plane, 'broadcast_downlink', side_effect=peer.protocol_peer):
            result = daemon.calibrate_radio({'confirmed': True, 'target_nodes': [1],
                'patch': {'bandwidth_mhz': 20, 'downlink_mcs': 4, 'uftp_rate_kbps': 1000}})
        assert result['status'] == 'finalized'
        assert updates == [(2, 0, 0, 1, 20, 4, 0, 1)]
        assert daemon.adapter.wireless_states['wlx-test']['channel_width'] == 'HT20'
        assert len(commands) == 1
        assert daemon.console.snapshot()['server']['link_process'] == initial_link
        assert tun_snapshot(daemon) == initial_tun


def _rate_client_entry(runtime, config, context):
    """仅供服务端装配检查，速率测试不执行客户端计算。"""
    raise AssertionError('server must not execute the deployed client entry')


def _rate_aggregate_entry(current_model, updates, output_model, config, context):
    """本用例在发布命令启动后停止，不应进入无 update 的聚合阶段。"""
    raise AssertionError('aggregation must not run before client updates arrive')


def _rate_evaluate_entry(model_path, config, context):
    """检查本用例自己的有限模型格式，返回合法的初始评估信封。"""
    context.check_stop()
    assert json.loads(Path(model_path).read_text()) == dict(config)
    return {'metrics': {'fixture_score': 0.0},
            'preparation': {'model_format': config['model_format'], 'ready': True},
            'verdict': None, 'reason': None}


def _ready_taskpeer(daemon, message):
    """经公开控制面提交带稳定报告身份的有限准备事实，并核对确认。"""
    for node in message['target_nodes']:
        replies = daemon.control_plane.handle_datagram(json.dumps({
            'type': 'TASK_READY', 'node_id': node, 'job_id': message['job_id'],
            'report_id': f"prepared-{message['job_id']}-{node}",
            'report': {'ready': True, 'model_format': 'radio-rate-test'},
        }).encode(), ('127.0.0.1', 10000 + node))
        assert replies
        ack = json.loads(replies[0])
        assert ack['type'] == 'TASK_READY_ACK' and ack['ok'] is True, ack


@pytest.mark.parametrize('fail_at,status,expected_rate', [
    (None, 'finalized', '17000'),
    ('RADIO_SWITCH_FINALIZED', 'rolled_back', '15000'),
])
def test_next_job_uses_final_effective_uftp_rate_in_sender_command(tmp_path, fail_at, status, expected_rate):
    sender_finished = threading.Event()
    with running_server(tmp_path, tun_ip='127.0.0.1', sender_finished=sender_finished,
                        client_entry=f'{__name__}:_rate_client_entry',
                        aggregate_entry=f'{__name__}:_rate_aggregate_entry',
                        evaluate_entry=f'{__name__}:_rate_evaluate_entry',
                        algorithm_config={'model_format': 'radio-rate-test'}) as (daemon, commands, uftp_started, updates):
        peer = radio_rest.TestRadioReconfigureREST()
        peer.daemon = daemon
        initial_link = daemon.console.snapshot()['server']['link_process'].copy()
        initial_tun = tun_snapshot(daemon)

        def respond(message):
            if message['type'] == fail_at:
                raise OSError('最终屏障广播失败')
            if message['type'] == 'TASK_ANNOUNCE':
                _ready_taskpeer(daemon, message)
            else:
                peer.protocol_peer(message)
        with patch.object(daemon.control_plane, 'broadcast_downlink', side_effect=respond):
            result = daemon.reconfigure_radio({'confirmed':True, 'patch':{'uftp_rate_kbps':17000}, 'target_nodes':[1]})
            assert result['status'] == status
            assert result['effective_config']['uftp_rate_kbps'] == int(expected_rate)
            assert daemon.console.snapshot()['server']['link_process'] == initial_link
            assert tun_snapshot(daemon) == initial_tun
            assert len(commands) == 1
            model = tmp_path / 'model.bin'
            model.write_text(json.dumps({'model_format': 'radio-rate-test'}))
            daemon.start_job({'job_id':'rate-job', 'run_id':'rate-run', 'target_nodes':[1],
                              'model_path':str(model), 'model_size_bytes':model.stat().st_size, 'rounds':1,
                              'server_http_port':0, 'io_timeout_seconds':5, 'live_observation':False})
            try:
                assert uftp_started.wait(3)
                send = next(command for command in commands if '-R' in command)
                assert send[send.index('-R')+1] == expected_rate
            finally:
                # 命令检查结束后释放假 sender，断言失败也能正常回收。
                sender_finished.set()
            daemon.abort_job()


def test_mcs_update_is_acknowledged_before_new_channel_ping(tmp_path):
    with running_server(tmp_path) as (daemon, commands, _, updates):
        peer = radio_rest.TestRadioReconfigureREST()
        peer.daemon = daemon
        initial_link = daemon.console.snapshot()['server']['link_process'].copy()
        initial_tun = tun_snapshot(daemon)
        pings = []

        def respond(message):
            if message['type'] == 'NEW_CHANNEL_PING':
                assert updates == [(2, 0, 0, 1, 40, 4, 0, 1)]
                assert daemon.console.snapshot()['server']['link_process'] == initial_link
                assert tun_snapshot(daemon) == initial_tun
                pings.append(message)
            peer.protocol_peer(message)

        with patch.object(daemon.control_plane, 'broadcast_downlink', side_effect=respond):
            result = daemon.reconfigure_radio({'confirmed': True, 'patch': {'downlink_mcs': 4}, 'target_nodes': [1]})
        assert result['status'] == 'finalized'
        assert pings
        assert len(commands) == 1


@pytest.mark.parametrize('failure', ['timeout', 'netlink-error'])
def test_server_readiness_failure_releases_link_and_log(tmp_path, failure):
    with running_server(tmp_path) as (daemon, _, _, _):
        daemon._stop_link_process()
        with patch.object(daemon.adapter, 'is_tun_ready', return_value=False,
                          side_effect=OSError('netlink query failed') if failure == 'netlink-error' else None):
            if failure == 'timeout':
                with patch('wfb_ng.fl.server_daemon.time.monotonic', side_effect=[0, 4]):
                    with pytest.raises(FLRuntimeError, match='控制路由未就绪'):
                        daemon._start_link_process()
            else:
                with pytest.raises(OSError, match='netlink query failed'):
                    daemon._start_link_process()
        assert daemon._link_process is None
        assert daemon._link_log_file is None
        assert not daemon.adapter.is_tun_active('fl-s')
