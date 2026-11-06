"""Client radio transactions observed at UDP and link-process boundaries."""
import json
from wfb_ng.tests._client_test_network import client_network_kwargs, isolated_client_network
from dataclasses import replace
import os
import socket
import struct
from pathlib import Path
import sys
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

from wfb_ng.fl.client_daemon import ClientDaemon, ClientDaemonConfig, JobSandbox
from wfb_ng.fl.errors import FLRuntimeError
from wfb_ng.fl.link_radio import apply_link_radio
from wfb_ng.fl.radio import RadioConfig
from wfb_ng.fl.control import ClientNodeState, ControlPlaneClient, HeartbeatAck, NodeHeartbeat
from wfb_ng.tests.test_fl_client_daemon import MockNetworkAdapter, _make_client_job


class TestClientRadioApplication(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        identity_path = Path(work.name) / 'build_identity.json'
        identity_path.write_text(json.dumps({'schema_version': 1, 'commit': 'a' * 40}))
        identity = mock.patch('wfb_ng.fl.evidence.BUILD_IDENTITY_PATH', identity_path)
        identity.start()
        self.addCleanup(identity.stop)
        self.commands = []
        self.processes = []
        self.spawned = threading.Event()
        self.fail_update = False
        self.update_gate = None
        self.adapter = MockNetworkAdapter(['wlx-test'])

        def spawn(cmd, **kwargs):
            self.commands.append(cmd)
            proc = mock.Mock()
            proc.poll.return_value = None
            proc.terminate.side_effect = lambda: setattr(proc.poll, 'return_value', 0)
            self.processes.append(proc)
            self.adapter.setup_tun('fl-c1', '10.80.0.11/24')
            self.spawned.set()
            return proc

        self.updates = []

        def update(port, bandwidth_mhz, mcs):
            self.updates.append((port, bandwidth_mhz, mcs))
            self.spawned.set()
            if self.update_gate is not None and not self.update_gate.wait(2):
                raise TimeoutError('test did not release radio update')
            if self.fail_update:
                raise FLRuntimeError('link_radio_update_failed', 'injected update failure')

        updater = mock.patch('wfb_ng.fl.client_daemon.apply_link_radio', side_effect=update)
        updater.start()
        self.addCleanup(updater.stop)
        # native 进程已由本夹具模拟，重武装同样在系统边界记录，不连接真实服务。
        self.rearms = []
        rearm = mock.patch('wfb_ng.fl.client_daemon.rearm_link_uplink',
                           side_effect=lambda port, epoch: self.rearms.append((port, epoch)))
        rearm.start()
        self.addCleanup(rearm.stop)
        self.daemon = ClientDaemon(
            ClientDaemonConfig(**client_network_kwargs(), node_id=1, tun_ip='10.80.0.11', work_dir=work.name,
                               uplink_mcs=3, radio_txpower_dbm=12),
            network_adapter=self.adapter, _link_process_factory=spawn,
        )
        self.addCleanup(self.daemon.stop)
        binary = mock.patch('wfb_ng.fl.client_daemon.shutil.which', return_value='/usr/bin/wfb_v6_uplink')
        binary.start()
        self.addCleanup(binary.stop)
        self.daemon.poll_hardware_once()
        self.daemon.start_idle_link()
        with mock.patch.object(ControlPlaneClient, 'start'):
            self.daemon.start_control_plane()
        self.control = self.daemon.control_plane
        self.datagrams = []
        self.control._uplink_sock = mock.Mock()
        self.control._uplink_sock.sendto.side_effect = lambda data, addr: self.datagrams.append(json.loads(data))
        self.spawned.clear()

    def test_idle_nonparticipant_accepts_job_bound_wire_transaction(self):
        """在线待命节点同样参加任务事务，继续复用已有 idle link。"""
        process = self.processes[0]
        owner = dict(self.adapter.tun_interfaces)
        patch = {'uplink_mcs': 5, 'radio_txpower_dbm': 14}
        base = dict(job_id='other-participants-job', session_id='job-idle', target_nodes=[1])
        self.control.handle_broadcast(dict(base, type='CONFIG_RADIO_PREPARE', patch=patch))
        ack = next(item for item in self.datagrams if item['type'] == 'PREPARE_ACK')
        self.assertTrue(ack['ok'])
        self.assertEqual(ack['job_id'], base['job_id'])
        self.control.handle_broadcast(dict(base, type='CONFIG_RADIO_COMMIT', patch=patch, delay_ms=0))
        self.assertTrue(self.spawned.wait(2))
        self.assertTrue(self.control.radio_switch_pending)
        config = RadioConfig(channel=self.control.current_channel,
            bandwidth_mhz=self.control.bandwidth_mhz, radio_txpower_dbm=self.control.txpower_dbm,
            uplink_mcs=self.control.uplink_mcs, downlink_mcs=self.control.downlink_mcs,
            uftp_rate_kbps=self.control.uftp_rate_kbps)
        final = dict(base, channel=config.channel, config=config.to_dict(), calibration=False)
        for kind in ('NEW_CHANNEL_PING', 'RADIO_SWITCH_FINALIZED', 'RADIO_SWITCH_CONFIRMED'):
            self.control.handle_broadcast(dict(final, type=kind))
        self.assertFalse(self.control.radio_switch_pending)
        self.assertIsNone(self.daemon._current_job_id)
        self.assertEqual(self.daemon._effective_radio, config)
        self.assertEqual(self.adapter.tun_interfaces, owner)
        self.assertEqual(self.processes, [process])
        process.terminate.assert_not_called()
        self.assertEqual(self.updates[-1], (self.daemon.config.radio_control_port, 40, 5))

    def commit(self, patch=None):
        if patch is None:
            patch = {'uplink_mcs': 5, 'radio_txpower_dbm': 14}
        self.control.handle_broadcast({'type': 'CONFIG_RADIO_PREPARE', 'session_id': 'radio-1', 'target_nodes': [1], 'patch': patch})
        self.control.handle_broadcast({'type': 'CONFIG_RADIO_COMMIT', 'session_id': 'radio-1', 'target_nodes': [1],
                                       'patch': patch, 'delay_ms': 0})
        self.assertTrue(self.spawned.wait(1), 'COMMIT must update the actual idle link')
        # The public getter synchronizes with the pending COMMIT application.
        self.assertTrue(self.control.radio_switch_pending)

    def dispatch(self, kind):
        config = RadioConfig(channel=self.control.current_channel,
                             bandwidth_mhz=self.control.bandwidth_mhz,
                             radio_txpower_dbm=self.control.txpower_dbm,
                             uplink_mcs=self.control.uplink_mcs,
                             downlink_mcs=self.control.downlink_mcs,
                             uftp_rate_kbps=self.control.uftp_rate_kbps)
        self.control.handle_broadcast({'type': kind, 'session_id': 'radio-1', 'target_nodes': [1],
                                       'channel': self.control.current_channel,
                                       'config': config.to_dict(),
                                       'calibration': self.control.calibration})

    def mcs_commands(self):
        return [self.commands[0][self.commands[0].index('--radio-mcs-index') + 1]] + [str(item[2]) for item in self.updates]

    def test_controlled_ht20_updates_hardware_and_link_and_abort_restores_both(self):
        patch = {'bandwidth_mhz': 20, 'downlink_mcs': 3, 'uftp_rate_kbps': 1000,
                 'uplink_mcs': 5}
        for kind in ('CONFIG_RADIO_PREPARE', 'CONFIG_RADIO_COMMIT'):
            self.control.handle_broadcast({'type': kind, 'session_id': 'radio-1', 'target_nodes': [1],
                                           'patch': patch, 'delay_ms': 0, 'calibration': True})
        self.assertTrue(self.spawned.wait(1))
        self.assertTrue(self.control.radio_switch_pending)
        self.assertEqual(self.updates[-1], (self.daemon.config.radio_control_port, 20, 5))
        self.assertEqual(self.adapter.wireless_states['wlx-test']['channel_width'], 'HT20')
        self.dispatch('CONFIG_RADIO_ABORT')
        self.assertEqual(self.updates[-1], (self.daemon.config.radio_control_port, 40, 3))
        self.assertEqual(self.adapter.wireless_states['wlx-test']['channel_width'], 'HT40+')
        self.assertEqual(len(self.processes), 1)
        self.processes[0].terminate.assert_not_called()
        self.assertTrue(self.adapter.is_tun_active('fl-c1'))

    def _rebuild_control_and_receive_idle(self):
        # 此检查需要真实心跳往返；两端均显式回环与临时端口。
        real_udp = mock.patch('wfb_ng.fl.control.socket', socket)
        real_udp.start()
        self.addCleanup(real_udp.stop)
        peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        peer.bind(('127.0.0.1', 0))
        peer.settimeout(.05)
        self.daemon.config = replace(self.daemon.config, server_control_host='127.0.0.1',
                                     server_control_port=peer.getsockname()[1], broadcast_port=0)
        session = None
        received = []
        errors = []
        idle = threading.Event()
        stopped = threading.Event()

        def receive():
            nonlocal session
            while not stopped.is_set():
                try:
                    raw, addr = peer.recvfrom(4096)
                except socket.timeout:
                    continue
                try:
                    heartbeat = NodeHeartbeat.from_bytes(raw)
                    received.append(heartbeat)
                    if heartbeat.session is None:
                        session = session or 'test-session'
                        confirmed = False
                        ack_session = session
                    else:
                        self.assertEqual(heartbeat.session, session)
                        confirmed = True
                        ack_session = session
                    peer.sendto(HeartbeatAck(
                        heartbeat_timestamp_ms=heartbeat.timestamp_ms,
                        channel=heartbeat.current_channel,
                        boot_id=heartbeat.boot_id,
                        session=ack_session,
                        confirmed=confirmed,
                    ).to_bytes(), addr)
                    if heartbeat.state == 'IDLE' and confirmed:
                        idle.set()
                except Exception as exc:
                    errors.append(str(exc))

        worker = threading.Thread(target=receive)
        worker.start()
        try:
            old_control = self.control
            self.adapter.set_interfaces([])
            self.assertIsNone(self.daemon.poll_hardware_once())
            self.assertIsNone(self.daemon.control_plane)
            self.adapter.set_interfaces(['wlx-test'])
            self.assertEqual(self.daemon.poll_hardware_once(), 'wlx-test')
            self.daemon.start_idle_link()
            self.daemon.start_control_plane()
            self.control = self.daemon.control_plane
            self.assertIsNot(self.control, old_control)
            self.assertTrue(idle.wait(2), f'重建控制面必须能发送合法心跳并重新入网: {errors}')
            self.assertEqual(errors, [])
            return next(hb for hb in received if hb.state == 'IDLE')
        finally:
            if self.daemon.control_plane is not None:
                self.daemon.control_plane.stop()
            stopped.set()
            worker.join(1)
            peer.close()

    def test_ht20_calibration_survives_public_hardware_rebuild(self):
        patch = {'bandwidth_mhz': 20, 'downlink_mcs': 4, 'uftp_rate_kbps': 7000,
                 'uplink_mcs': 5, 'radio_txpower_dbm': 16}
        for kind in ('CONFIG_RADIO_PREPARE', 'CONFIG_RADIO_COMMIT'):
            self.control.handle_broadcast({'type': kind, 'session_id': 'radio-1', 'target_nodes': [1],
                                           'patch': patch, 'delay_ms': 0, 'calibration': True})
        self.assertTrue(self.spawned.wait(1))
        self.assertTrue(self.control.radio_switch_pending)
        self.dispatch('RADIO_SWITCH_FINALIZED')
        self.dispatch('RADIO_SWITCH_CONFIRMED')
        self.assertFalse(self.control.radio_switch_pending)
        heartbeat = self._rebuild_control_and_receive_idle()
        self.assertTrue(heartbeat.calibration)
        self.assertEqual((heartbeat.bandwidth_mhz, heartbeat.downlink_mcs,
                          heartbeat.uftp_rate_kbps, heartbeat.uplink_mcs, heartbeat.txpower_dbm),
                         (20, 4, 7000, 5, 16))
        self.assertEqual(self.adapter.wireless_states['wlx-test']['channel_width'], 'HT20')
        args = self.commands[-1]
        self.assertEqual(args[args.index('--radio-bandwidth') + 1], '20')

    def test_normal_config_survives_public_hardware_rebuild(self):
        self.control.apply_alignment({'downlink_mcs': 4, 'uftp_rate_kbps': 23000,
                                      'uplink_mcs': 5, 'radio_txpower_dbm': 15})
        heartbeat = self._rebuild_control_and_receive_idle()
        self.assertFalse(heartbeat.calibration)
        self.assertEqual((heartbeat.bandwidth_mhz, heartbeat.downlink_mcs,
                          heartbeat.uftp_rate_kbps, heartbeat.uplink_mcs, heartbeat.txpower_dbm),
                         (40, 4, 23000, 5, 15))

    def test_idle_link_waits_for_address_and_server_route(self):
        self.daemon._stop_idle_link()
        checks = []
        def ready(name, cidr, destination):
            checks.append((name, cidr, destination))
            return len(checks) >= 3
        with mock.patch.object(self.adapter, 'is_tun_ready', side_effect=ready, create=True):
            self.daemon.start_idle_link()
        self.assertGreaterEqual(len(checks), 3)
        self.assertTrue(all(item == ('fl-c1', '10.80.0.11/24', self.daemon.config.server_control_host) for item in checks))

    def test_task_role_uses_confirmed_effective_mcs_and_power(self):
        self.commit()
        self.dispatch('RADIO_SWITCH_FINALIZED')
        self.assertTrue(self.control.lease_watchdog.is_armed)
        self.dispatch('RADIO_SWITCH_CONFIRMED')
        self.daemon.sandbox = JobSandbox(
            work_dir=self.daemon.config.work_dir,
            network_adapter=self.adapter,
            _command_prefix=[sys.executable, '-c', 'import time; time.sleep(30)'],
            evidence_dir=os.path.join(self.daemon.config.work_dir, 'evidence'),
        )
        self.control._uplink_sock.recvfrom.side_effect = BlockingIOError
        spawned = threading.Event()
        real_popen = subprocess.Popen

        def spawn(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            spawned.set()
            return process

        with mock.patch('wfb_ng.fl.client_daemon.subprocess.Popen', side_effect=spawn):
            self.control.handle_broadcast({
                'type': 'TASK_ANNOUNCE', 'run_id': 'run-1', 'job_id': 'job-1',
                'rounds': 1, 'target_nodes': [1], 'preparation_timeout_seconds': 120,
                'algorithm': 'wfb_ng.fl.mnist:client_entry',
                'algorithm_config': {}, 'server_http_host': '10.80.0.1',
                'server_http_port': 8080, 'uftp_port': 1044, 'link_id': 7669206,
                'io_timeout_seconds': 120, 'live_observation': False,
            })
            self.assertTrue(spawned.wait(2), 'task role must start in the background')
        path = os.path.join(self.daemon.config.work_dir, 'job_job-1', 'client_role.json')
        with open(path, encoding='utf-8') as stream:
            role = json.load(stream)
        args = role['link_args']
        self.assertEqual(args[args.index('--radio-mcs-index') + 1], '5')
        self.assertEqual(role['radio_txpower_dbm'], 14)
        self.assertEqual(role['channel'], 157)

    def test_failed_update_cannot_ack_commit_or_final_barriers_and_abort_recovers(self):
        self.fail_update = True
        self.commit()
        self.dispatch('NEW_CHANNEL_PING')
        self.dispatch('RADIO_SWITCH_FINALIZED')
        self.dispatch('RADIO_SWITCH_CONFIRMED')
        self.assertEqual([d['type'] for d in self.datagrams], ['PREPARE_ACK'])
        self.assertTrue(self.control.lease_watchdog.is_armed)
        self.fail_update = False
        self.dispatch('CONFIG_RADIO_ABORT')
        self.assertEqual(self.mcs_commands(), ['3', '5', '3'])
        self.assertTrue(self.daemon.is_idle_link_running)
        self.assertFalse(self.control.lease_watchdog.is_armed)

    def test_lease_expiration_restores_mcs_and_power_after_proposal(self):
        self.commit()
        self.dispatch('RADIO_SWITCH_FINALIZED')
        self.spawned.clear()
        self.control.lease_watchdog.arm(0.02)
        self.assertTrue(self.spawned.wait(1), 'expired lease must restore the old radio parameters')
        self.assertFalse(self.control.radio_switch_pending)
        self.assertEqual(self.mcs_commands(), ['3', '5', '3'])
        self.assertEqual(self.adapter.wireless_states['wlx-test']['txpower_dbm'], 12)

    def test_supervisor_retries_expired_lease_rollback_until_update_recovers(self):
        self.commit()
        self.assertEqual(self.mcs_commands(), ['3', '5'])
        self.fail_update = True
        # 广播通过直接分发注入；控制 UDP 由测试边界隔离。
        self.control._uplink_sock.recvfrom.side_effect = socket.timeout
        self.control.start()
        self.addCleanup(self.control.stop)
        self.assertTrue(self.control.is_running)
        self.control.lease_watchdog.arm(0.02)

        deadline = time.monotonic() + 2
        while len(self.updates) < 4 and time.monotonic() < deadline:
            threading.Event().wait(0.01)
        # One watchdog callback cannot account for three failed restores:
        # the real supervisor must have retried while the saved config persisted.
        self.assertGreaterEqual(len(self.updates), 4)
        self.assertTrue(self.control.lease_watchdog.is_expired)
        self.assertTrue(self.control.radio_switch_pending)
        self.assertTrue(self.daemon.is_idle_link_running)
        rollback_mcs = self.mcs_commands()[2:]
        self.assertEqual(rollback_mcs, ['3'] * len(rollback_mcs))

        self.fail_update = False
        deadline = time.monotonic() + 2
        while self.control.radio_switch_pending and time.monotonic() < deadline:
            threading.Event().wait(0.01)
        self.assertFalse(self.control.radio_switch_pending)
        self.assertEqual(self.control.state, ClientNodeState.HUNTING)
        self.assertTrue(self.daemon.is_idle_link_running)
        self.assertEqual(self.mcs_commands()[-1], '3')
        self.assertEqual(self.adapter.wireless_states['wlx-test']['txpower_dbm'], 12)

    def test_failed_abort_restore_preserves_lease_and_can_retry(self):
        self.commit()
        self.fail_update = True
        self.dispatch('CONFIG_RADIO_ABORT')
        self.assertTrue(self.control.lease_watchdog.is_armed)
        self.assertTrue(self.control.radio_switch_pending)
        self.dispatch('NEW_CHANNEL_PING')
        self.dispatch('RADIO_SWITCH_FINALIZED')
        self.dispatch('RADIO_SWITCH_CONFIRMED')
        self.assertEqual([d['type'] for d in self.datagrams], ['PREPARE_ACK'])
        self.fail_update = False
        self.dispatch('CONFIG_RADIO_ABORT')
        self.assertTrue(self.daemon.is_idle_link_running)
        self.assertEqual(self.mcs_commands(), ['3', '5', '3', '3'])
        self.assertFalse(self.control.radio_switch_pending)

    def test_alignment_updates_actual_idle_link(self):
        self.control.apply_alignment({'uplink_mcs': 4, 'radio_txpower_dbm': 13})
        self.assertEqual(self.mcs_commands(), ['3', '4'])
        self.assertEqual(self.adapter.wireless_states['wlx-test']['txpower_dbm'], 13)

    def test_channel_165_does_not_silently_downgrade_bandwidth(self):
        self.control.handle_broadcast({
            'type': 'CONFIG_RADIO_PREPARE', 'session_id': 'radio-1', 'target_nodes': [1],
            'patch': {'channel': 165},
        })
        self.assertEqual(self.control.current_channel, 157)
        self.assertEqual(self.updates, [])
        self.assertEqual(len(self.processes), 1)

    def test_pending_apply_serializes_daemon_and_control_without_early_acks(self):
        self.update_gate = threading.Event()
        patch = {'uplink_mcs': 5}
        self.control.handle_broadcast({'type': 'CONFIG_RADIO_PREPARE', 'session_id': 'radio-1', 'target_nodes': [1], 'patch': patch})
        self.control.handle_broadcast({'type': 'CONFIG_RADIO_COMMIT', 'session_id': 'radio-1', 'target_nodes': [1],
                                       'patch': patch, 'delay_ms': 0})
        self.assertTrue(self.spawned.wait(1))
        daemon_entered = threading.Event()
        daemon_done = threading.Event()
        control_done = threading.Event()

        def daemon_operation():
            daemon_entered.set()
            self.daemon.start_idle_link()
            daemon_done.set()

        def control_operation():
            self.dispatch('NEW_CHANNEL_PING')
            control_done.set()

        worker = threading.Thread(target=daemon_operation, daemon=True)
        receiver = threading.Thread(target=control_operation, daemon=True)
        worker.start()
        receiver.start()
        try:
            self.assertTrue(daemon_entered.wait(1))
            self.assertFalse(daemon_done.is_set())
            self.assertFalse(control_done.is_set())
            self.assertEqual([d['type'] for d in self.datagrams], ['PREPARE_ACK'])
        finally:
            self.update_gate.set()
        self.assertTrue(daemon_done.wait(1), 'daemon/control lock order must make progress')
        self.assertTrue(control_done.wait(1), 'COMMIT apply must release the control receiver')
        worker.join(1)
        receiver.join(1)
        self.assertIn('COMMIT_SUCCESS', [d['type'] for d in self.datagrams])
        # Even after apply, task handoff must await the final confirmation.
        with self.assertRaises(FLRuntimeError) as rejected:
            self.daemon.trigger_job(_make_client_job(job_id='pending', node_id=1,
                                                    tun_name='fl-c1', tun_ip='10.80.0.11'))
        self.assertEqual(rejected.exception.error_code, 'radio_switch_pending')

    def test_commit_updates_idle_link_before_success_and_abort_restores_it(self):
        self.commit()
        self.assertEqual(self.mcs_commands(), ['3', '5'])
        self.processes[0].terminate.assert_not_called()
        self.assertEqual(len(self.processes), 1)
        self.assertTrue(self.adapter.is_tun_active('fl-c1'))
        self.dispatch('NEW_CHANNEL_PING')
        self.assertIn('COMMIT_SUCCESS', [d['type'] for d in self.datagrams])
        self.dispatch('CONFIG_RADIO_ABORT')
        self.assertEqual(self.mcs_commands(), ['3', '5', '3'])
        self.assertEqual(self.adapter.wireless_states['wlx-test']['txpower_dbm'], 12)
        self.assertFalse(self.control.lease_watchdog.is_armed)


class TestLinkRadioUDP(unittest.TestCase):
    def exchange(self, reply):
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(udp.close)
        udp.bind(('127.0.0.1', 0))
        udp.settimeout(2)
        requests = []

        def serve():
            data, address = udp.recvfrom(4096)
            requests.append(data)
            udp.sendto(reply(data), address)

        worker = threading.Thread(target=serve, daemon=True)
        worker.start()
        self.addCleanup(worker.join, 2)
        return udp.getsockname()[1], requests

    def test_full_fixed_phy_payload_and_matching_success(self):
        # 隔离 socket 系统边界，只验证完整配置报文，不绑定本机端口。
        with mock.patch('wfb_ng.fl.link_radio.socket.socket') as socket_factory:
            udp = socket_factory.return_value.__enter__.return_value
            udp.recv.side_effect = lambda size: udp.send.call_args.args[0][:4] + struct.pack('!I', 0)
            apply_link_radio(9200, 20, 5)
        request = struct.unpack('!IB7B', udp.send.call_args.args[0])
        self.assertEqual(request[1:], (2, 0, 0, 0, 20, 5, 0, 1))

    def test_rejected_or_unrelated_or_malformed_response_cannot_succeed(self):
        responses = (
            lambda data: data[:4] + struct.pack('!I', 22),
            lambda data: struct.pack('!II', struct.unpack('!I', data[:4])[0] ^ 1, 0),
            lambda data: b'broken',
        )
        for response in responses:
            with self.subTest(response=response):
                port, _ = self.exchange(response)
                with self.assertRaises(FLRuntimeError) as failed:
                    apply_link_radio(port, 40, 3)
                self.assertEqual(failed.exception.error_code, 'link_radio_update_failed')
