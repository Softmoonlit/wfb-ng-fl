"""Client radio transactions observed at UDP and link-process boundaries."""
import json
import os
import socket
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from wfb_ng.fl.client_daemon import ClientDaemon, ClientDaemonConfig, JobSandbox
from wfb_ng.fl.errors import FLRuntimeError
from wfb_ng.fl.control import ClientNodeState, ControlPlaneClient
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
        self.fail_spawn = False
        self.spawn_gate = None
        self.adapter = MockNetworkAdapter(['wlx-test'])

        def spawn(cmd, **kwargs):
            self.commands.append(cmd)
            if self.fail_spawn:
                self.spawned.set()
                raise OSError('injected link startup failure')
            proc = mock.Mock()
            proc.poll.return_value = None
            proc.terminate.side_effect = lambda: setattr(proc.poll, 'return_value', 0)
            self.processes.append(proc)
            self.adapter.setup_tun('fl-c1', '10.80.0.11/24')
            self.spawned.set()
            if self.spawn_gate is not None and not self.spawn_gate.wait(2):
                raise TimeoutError('test did not release link startup')
            return proc

        self.daemon = ClientDaemon(
            ClientDaemonConfig(node_id=1, tun_ip='10.80.0.11', work_dir=work.name,
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

    def commit(self, patch=None):
        if patch is None:
            patch = {'uplink_mcs': 5, 'radio_txpower_dbm': 14}
        self.control.handle_broadcast({'type': 'CONFIG_RADIO_PREPARE', 'session_id': 'radio-1', 'patch': patch})
        self.control.handle_broadcast({'type': 'CONFIG_RADIO_COMMIT', 'session_id': 'radio-1',
                                       'patch': patch, 'delay_ms': 0})
        self.assertTrue(self.spawned.wait(1), 'COMMIT must rebuild the actual idle link')
        # The public getter synchronizes with the pending COMMIT application.
        self.assertTrue(self.control.radio_switch_pending)

    def dispatch(self, kind):
        self.control.handle_broadcast({'type': kind, 'session_id': 'radio-1',
                                       'channel': self.control.current_channel})

    def mcs_commands(self):
        return [cmd[cmd.index('--radio-mcs-index') + 1] for cmd in self.commands]

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
        udp = mock.Mock()
        blocking = [True]
        udp.setblocking.side_effect = lambda value: blocking.__setitem__(0, value)

        def recv(*args):
            if not blocking[0]:
                raise BlockingIOError()
            return b'{"ack": true}', ('10.80.0.1', 9001)

        udp.recvfrom.side_effect = recv
        self.control._uplink_sock = udp
        with mock.patch('wfb_ng.fl.control.socket.socket', return_value=udp):
            self.control.handle_broadcast({
                'type': 'TASK_ANNOUNCE', 'run_id': 'run-1', 'job_id': 'job-1',
                'rounds': 1, 'algorithm': 'wfb_ng.fl.issue41_algorithm:client_main',
                'algorithm_config': {}, 'server_http_host': '10.80.0.1',
                'server_http_port': 8080, 'uftp_port': 1044, 'link_id': 7669206,
                'io_timeout_seconds': 120, 'live_observation': False,
            })
        path = os.path.join(self.daemon.config.work_dir, 'job_job-1', 'client_role.json')
        with open(path, encoding='utf-8') as stream:
            role = json.load(stream)
        args = role['link_args']
        self.assertEqual(args[args.index('--radio-mcs-index') + 1], '5')
        self.assertEqual(role['radio_txpower_dbm'], 14)
        self.assertEqual(role['channel'], 157)

    def test_failed_link_start_cannot_ack_commit_or_final_barriers_and_abort_recovers(self):
        self.fail_spawn = True
        self.commit()
        self.dispatch('NEW_CHANNEL_PING')
        self.dispatch('RADIO_SWITCH_FINALIZED')
        self.dispatch('RADIO_SWITCH_CONFIRMED')
        self.assertEqual([d['type'] for d in self.datagrams], ['PREPARE_ACK'])
        self.assertTrue(self.control.lease_watchdog.is_armed)
        self.fail_spawn = False
        self.dispatch('CONFIG_RADIO_ABORT')
        self.assertEqual(self.mcs_commands(), ['3', '5', '3'])
        self.assertTrue(self.daemon.is_idle_link_running)
        self.assertFalse(self.control.lease_watchdog.is_armed)

    def test_lease_expiration_restores_mcs_and_power_after_proposal(self):
        self.commit()
        self.dispatch('RADIO_SWITCH_FINALIZED')
        self.spawned.clear()
        self.control.lease_watchdog.arm(0.02)
        self.assertTrue(self.spawned.wait(1), 'expired lease must rebuild the old link')
        self.assertFalse(self.control.radio_switch_pending)
        self.assertEqual(self.mcs_commands(), ['3', '5', '3'])
        self.assertEqual(self.adapter.wireless_states['wlx-test']['txpower_dbm'], 12)

    def test_supervisor_retries_expired_lease_rollback_until_link_spawn_recovers(self):
        self.commit()
        self.assertEqual(self.mcs_commands(), ['3', '5'])
        self.fail_spawn = True
        # Keep the real broadcast socket independent of the injected uplink.
        self.control.broadcast_port = 0
        self.control._uplink_sock.recvfrom.side_effect = socket.timeout
        self.control.start()
        self.addCleanup(self.control.stop)
        self.assertTrue(self.control.is_running)
        self.control.lease_watchdog.arm(0.02)

        deadline = time.monotonic() + 2
        while len(self.commands) < 5 and time.monotonic() < deadline:
            threading.Event().wait(0.01)
        # One watchdog callback cannot account for three failed restores:
        # the real supervisor must have retried while the saved config persisted.
        self.assertGreaterEqual(len(self.commands), 5)
        self.assertTrue(self.control.lease_watchdog.is_expired)
        self.assertTrue(self.control.radio_switch_pending)
        self.assertFalse(self.daemon.is_idle_link_running)
        rollback_mcs = self.mcs_commands()[2:]
        self.assertEqual(rollback_mcs, ['3'] * len(rollback_mcs))

        self.fail_spawn = False
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
        self.fail_spawn = True
        self.dispatch('CONFIG_RADIO_ABORT')
        self.assertTrue(self.control.lease_watchdog.is_armed)
        self.assertTrue(self.control.radio_switch_pending)
        self.dispatch('NEW_CHANNEL_PING')
        self.dispatch('RADIO_SWITCH_FINALIZED')
        self.dispatch('RADIO_SWITCH_CONFIRMED')
        self.assertEqual([d['type'] for d in self.datagrams], ['PREPARE_ACK'])
        self.fail_spawn = False
        self.dispatch('CONFIG_RADIO_ABORT')
        self.assertTrue(self.daemon.is_idle_link_running)
        self.assertEqual(self.mcs_commands(), ['3', '5', '3', '3'])
        self.assertFalse(self.control.radio_switch_pending)

    def test_alignment_rebuilds_actual_idle_link(self):
        self.control.apply_alignment({'uplink_mcs': 4, 'radio_txpower_dbm': 13})
        self.assertEqual(self.mcs_commands(), ['3', '4'])
        self.assertEqual(self.adapter.wireless_states['wlx-test']['txpower_dbm'], 13)

    def test_channel_165_rebuilds_bandwidth_and_abort_restores_it(self):
        self.commit({'channel': 165, 'uplink_mcs': 5})
        self.dispatch('CONFIG_RADIO_ABORT')
        widths = [cmd[cmd.index('--radio-bandwidth') + 1] for cmd in self.commands]
        self.assertEqual(widths, ['40', '20', '40'])
        self.assertEqual(self.control.current_channel, 157)

    def test_pending_apply_serializes_daemon_and_control_without_early_acks(self):
        self.spawn_gate = threading.Event()
        patch = {'uplink_mcs': 5}
        self.control.handle_broadcast({'type': 'CONFIG_RADIO_PREPARE', 'session_id': 'radio-1', 'patch': patch})
        self.control.handle_broadcast({'type': 'CONFIG_RADIO_COMMIT', 'session_id': 'radio-1',
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
            self.spawn_gate.set()
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

    def test_commit_rebuilds_idle_link_before_success_and_abort_restores_it(self):
        self.commit()
        self.assertEqual(self.mcs_commands(), ['3', '5'])
        self.processes[0].terminate.assert_called_once()
        self.dispatch('NEW_CHANNEL_PING')
        self.assertIn('COMMIT_SUCCESS', [d['type'] for d in self.datagrams])
        self.dispatch('CONFIG_RADIO_ABORT')
        self.assertEqual(self.mcs_commands(), ['3', '5', '3'])
        self.assertEqual(self.adapter.wireless_states['wlx-test']['txpower_dbm'], 12)
        self.assertFalse(self.control.lease_watchdog.is_armed)
