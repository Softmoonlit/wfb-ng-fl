"""射频配置必须进入真实进程命令和后续数据面参数。"""
from contextlib import contextmanager
from unittest.mock import patch
import threading
import subprocess
import json

import pytest

from wfb_ng.fl.server_daemon import ServerDaemon, ServerDaemonConfig
from wfb_ng.tests.test_fl_client_daemon import MockNetworkAdapter
from wfb_ng.tests.test_fl_console import register_idle
from wfb_ng.tests import test_fl_radio_rest as radio_rest


@contextmanager
def running_server(tmp_path, fail_spawns=(), **config):
    daemon = ServerDaemon(ServerDaemonConfig(ipc_port=0, enable_control_plane=False,
                         enable_link_process=True, air_interface='wlx-test',
                         model_library_dir=str(tmp_path / 'models'), work_dir=str(tmp_path), **config),
                         MockNetworkAdapter(['wlx-test']))
    commands = []
    uftp_started = threading.Event()
    class Process:
        def __init__(self, pid):
            self.pid = pid
            self.exited = threading.Event()
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
        if len(commands) in fail_spawns:
            raise OSError('链路进程启动失败')
        if '-R' in command:
            uftp_started.set()
        else:
            daemon.adapter.setup_tun(daemon.config.tun_name, daemon.config.tun_cidr)
        return Process(len(commands))
    with patch('wfb_ng.fl.server_daemon.shutil.which', return_value='/test/wfb_v6_uplink'), patch(
            'wfb_ng.fl.server_daemon.subprocess.Popen', side_effect=spawn):
        daemon.start()
        register_idle(daemon)
        try:
            yield daemon, commands, uftp_started
        finally:
            daemon.stop()


@pytest.mark.parametrize('fail_at,expected_status,expected_mcs', [
    (None, 'finalized', ['3','4']),
    ('RADIO_SWITCH_FINALIZED', 'rolled_back', ['3','4','3']),
    ('RADIO_SWITCH_CONFIRMED', 'radio_error', ['3','4']),
])
def test_radio_transaction_applies_server_mcs_before_final_barrier_and_restores_on_rollback(
        tmp_path, fail_at, expected_status, expected_mcs):
    with running_server(tmp_path) as (daemon, commands, _):
        peer = radio_rest.TestRadioReconfigureREST()
        peer.daemon = daemon
        current = daemon.console.radio_configuration()['config']
        config = {key:current[key] for key in ('channel','radio_txpower_dbm','downlink_mcs','uplink_mcs','uftp_rate_kbps')}
        config.update(downlink_mcs=4, uftp_rate_kbps=22000)
        checked = daemon.console.validate_radio_configuration({'config':config})
        def respond(message):
            if message['type'] == fail_at:
                raise OSError('最终屏障广播失败')
            if message['type'] == 'RADIO_SWITCH_FINALIZED':
                assert commands[-1][commands[-1].index('--radio-mcs-index') + 1] == '4'
            peer.protocol_peer(message)
        with patch.object(daemon.control_plane, 'broadcast_downlink', side_effect=respond):
            result = daemon.console.apply_radio_configuration({
                'confirmation_token':checked['confirmation']['token'], 'confirm_risk':False})
        assert result['status'] == expected_status
        assert [cmd[cmd.index('--radio-mcs-index')+1] for cmd in commands] == expected_mcs
        assert daemon.console.snapshot()['server']['link_process']['running']
        expected_phase = {'RADIO_SWITCH_FINALIZED':'FINALIZED', 'RADIO_SWITCH_CONFIRMED':'CONFIRMED'}.get(fail_at)
        assert result['failed_phase'] == expected_phase
        state = daemon.console.snapshot()['server']
        assert state['radio']['downlink_mcs'] == (3 if expected_status == 'rolled_back' else 4)
        assert state['state'] == ('radio_error' if expected_status == 'radio_error' else 'idle')


@pytest.mark.parametrize('fail_spawns,status', [((2,), 'rolled_back'), ((2,3), 'radio_error')])
def test_server_link_apply_failure_restores_old_mcs_or_fails_closed(tmp_path, fail_spawns, status):
    with running_server(tmp_path, fail_spawns=fail_spawns) as (daemon, commands, _):
        peer = radio_rest.TestRadioReconfigureREST()
        peer.daemon = daemon
        payload = {'confirmed':True, 'patch':{'downlink_mcs':4}, 'target_nodes':[1]}
        with patch.object(daemon.control_plane, 'broadcast_downlink', side_effect=peer.protocol_peer):
            result = daemon.reconfigure_radio(payload)
        assert result['status'] == status
        assert result['failed_phase'] == 'COMMIT'
        assert [cmd[cmd.index('--radio-mcs-index')+1] for cmd in commands] == ['3','4','3']
        state = daemon.console.snapshot()['server']
        if status == 'rolled_back':
            assert state['radio']['downlink_mcs'] == 3
            assert state['state'] == 'idle'
            assert state['link_process']['running']
        else:
            assert state['radio'] is None
            assert state['state'] == 'radio_error'
            assert not state['link_process']['running']


def test_channel_165_updates_server_link_bandwidth_with_same_mcs(tmp_path):
    with running_server(tmp_path) as (daemon, commands, _):
        peer = radio_rest.TestRadioReconfigureREST()
        peer.daemon = daemon
        with patch.object(daemon.control_plane, 'broadcast_downlink', side_effect=peer.protocol_peer):
            result = daemon.reconfigure_radio({'confirmed':True, 'patch':{'channel':165}, 'target_nodes':[1]})
        assert result['status'] == 'finalized'
        assert [cmd[cmd.index('--radio-bandwidth')+1] for cmd in commands] == ['40', '20']


def test_next_job_uses_confirmed_uftp_rate_in_sender_command(tmp_path):
    with running_server(tmp_path, tun_ip='127.0.0.1') as (daemon, commands, uftp_started):
        peer = radio_rest.TestRadioReconfigureREST()
        peer.daemon = daemon
        def respond(message):
            if message['type'] == 'TASK_ANNOUNCE':
                daemon.control_plane.handle_datagram(json.dumps({
                    'type':'TASK_READY', 'node_id':1, 'job_id':message['job_id']}).encode(), ('127.0.0.1',10001))
            else:
                peer.protocol_peer(message)
        with patch.object(daemon.control_plane, 'broadcast_downlink', side_effect=respond):
            result = daemon.reconfigure_radio({'confirmed':True, 'patch':{'uftp_rate_kbps':17000}, 'target_nodes':[1]})
            assert result['status'] == 'finalized'
            model = tmp_path / 'model.bin'
            model.write_bytes(b'ordinary model')
            daemon.start_job({'job_id':'rate-job', 'run_id':'rate-run', 'target_nodes':[1],
                              'model_path':str(model), 'model_size_bytes':14, 'rounds':1,
                              'server_http_port':0, 'io_timeout_seconds':5, 'live_observation':False})
            assert uftp_started.wait(3)
            send = next(command for command in commands if '-R' in command)
            assert send[send.index('-R')+1] == '17000'
            daemon.abort_job()
