"""
Tests for Two-Phase Radio Reconfiguration Engine and Lease Watchdog (Ticket 04).
"""

import json
import socket
import threading
import time
import unittest
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock

from wfb_ng.fl.control import (
    DEFAULT_BENCHMARK_CHANNEL,
    FORBIDDEN_CHANNELS,
    LEASE_TIMEOUT_DEFAULT_SECONDS,
    PREPARE_TIMEOUT_DEFAULT_SECONDS,
    COMMIT_DELAY_DEFAULT_SECONDS,
    COMMIT_TIMEOUT_DEFAULT_SECONDS,
    NEW_CHANNEL_PING_INTERVAL_SECONDS,
    ClientNodeState,
    NodeReadiness,
    NodeHeartbeat,
    HeartbeatAck,
    PrepareAck,
    CommitSuccess,
    RadioSwitchResult,
    LeaseWatchdog,
    ControlPlaneServer,
    ControlPlaneClient,
)
from wfb_ng.fl.radio import (
    RadioConfig,
    normalize_radio_patch,
    validate_radio_config,
    validate_radio_patch,
)
from wfb_ng.tests.test_fl_client_daemon import MockNetworkAdapter


class TestRadioPatchNormalizationAndDataclasses(unittest.TestCase):
    """Test patch normalization aliases and radio switch datagram serialization."""

    def test_normalize_radio_patch_aliases(self):
        patch = {
            "target_channel": 149,
            "txpower_dbm": 12,
            "uplink_mcs": 6,
        }
        normalized = normalize_radio_patch(patch)
        self.assertEqual(normalized["channel"], 149)
        self.assertEqual(normalized["radio_txpower_dbm"], 12)
        self.assertEqual(normalized["uplink_mcs"], 6)
        self.assertNotIn("target_channel", normalized)
        self.assertNotIn("txpower_dbm", normalized)

    def test_normalize_radio_patch_conflict_raises(self):
        with self.assertRaises(ValueError):
            normalize_radio_patch({"target_channel": 149, "channel": 153})
        with self.assertRaises(ValueError):
            normalize_radio_patch({"txpower_dbm": 12, "radio_txpower_dbm": 15})

    def test_prepare_ack_serialization_and_validation(self):
        ack = PrepareAck(
            node_id=1,
            session_id="switch_123",
            ok=True,
            timestamp_ms=1000,
        )
        raw = ack.to_bytes()
        parsed = PrepareAck.from_bytes(raw)
        self.assertEqual(parsed.node_id, 1)
        self.assertEqual(parsed.session_id, "switch_123")
        self.assertTrue(parsed.ok)
        self.assertIsNone(parsed.error)
        self.assertEqual(parsed.timestamp_ms, 1000)

        # PrepareAck with seq_id
        ack_seq = PrepareAck(
            node_id=1,
            session_id="switch_123",
            seq_id="switch_123",
            ok=True,
        )
        parsed_seq = PrepareAck.from_bytes(ack_seq.to_bytes())
        self.assertEqual(parsed_seq.seq_id, "switch_123")
        self.assertEqual(parsed_seq.session_id, "switch_123")

        # Failure ACK
        fail_ack = PrepareAck(
            node_id=2,
            session_id="switch_123",
            ok=False,
            error="channel 161 is forbidden",
        )
        parsed_fail = PrepareAck.from_bytes(fail_ack.to_bytes())
        self.assertFalse(parsed_fail.ok)
        self.assertEqual(parsed_fail.error, "channel 161 is forbidden")

    def test_commit_success_serialization(self):
        cs = CommitSuccess(
            node_id=2,
            session_id="switch_456",
            current_channel=149,
            timestamp_ms=2000,
        )
        parsed = CommitSuccess.from_bytes(cs.to_bytes())
        self.assertEqual(parsed.node_id, 2)
        self.assertEqual(parsed.session_id, "switch_456")
        self.assertEqual(parsed.current_channel, 149)
        self.assertEqual(parsed.timestamp_ms, 2000)

    def test_radio_switch_result_truthiness(self):
        res_ok = RadioSwitchResult(
            success=True,
            session_id="s1",
            target_channel=149,
            applied_patch={"channel": 149},
        )
        self.assertTrue(bool(res_ok))

        res_fail = RadioSwitchResult(
            success=False,
            session_id="s2",
            target_channel=153,
            applied_patch={"channel": 153},
            error_message="timeout",
            failed_phase="PREPARE",
            unresponsive_nodes=[2],
        )
        self.assertFalse(bool(res_fail))
        self.assertEqual(res_fail.failed_phase, "PREPARE")
        self.assertEqual(res_fail.unresponsive_nodes, [2])


class TestLeaseWatchdog(unittest.TestCase):
    """Test 15-second Lease Watchdog lifecycle, refresh, and timeout fallback."""

    def test_watchdog_arm_and_disarm(self):
        expired_called = threading.Event()
        wd = LeaseWatchdog(
            timeout_seconds=0.1,
            on_expired=lambda: expired_called.set(),
        )
        self.assertFalse(wd.is_armed)
        self.assertFalse(wd.is_expired)

        wd.arm()
        self.assertTrue(wd.is_armed)
        time.sleep(0.02)
        wd.disarm()
        self.assertFalse(wd.is_armed)

        # Wait longer than timeout; must NOT expire because it was disarmed
        time.sleep(0.15)
        self.assertFalse(expired_called.is_set())
        self.assertFalse(wd.is_expired)

    def test_watchdog_refresh_extends_deadline(self):
        expired_called = threading.Event()
        wd = LeaseWatchdog(
            timeout_seconds=0.1,
            on_expired=lambda: expired_called.set(),
        )
        wd.arm()

        # Refresh at 50ms intervals for 200ms (longer than 100ms timeout)
        for _ in range(4):
            time.sleep(0.05)
            refreshed = wd.refresh()
            self.assertTrue(refreshed)
            self.assertTrue(wd.is_armed)
            self.assertFalse(expired_called.is_set())

        # Now let it expire
        time.sleep(0.15)
        self.assertTrue(expired_called.is_set())
        self.assertTrue(wd.is_expired)
        self.assertFalse(wd.is_armed)

    def test_watchdog_timeout_fires_callback(self):
        expired_called = threading.Event()
        wd = LeaseWatchdog(
            timeout_seconds=0.08,
            on_expired=lambda: expired_called.set(),
        )
        wd.arm()
        self.assertTrue(expired_called.wait(timeout=0.3))
        self.assertTrue(wd.is_expired)
        self.assertFalse(wd.is_armed)


class TestTwoPhaseRadioSwitchNetworkLoop(unittest.TestCase):
    """
    End-to-end socket testing of Two-Phase Radio Reconfiguration Engine:
    - Normal atomic switch across Server, Client 1, and Client 2 (current test topology).
    - Incremental patch handling (txpower only, mcs only).
    - Phase 1 prepare timeout and failure rollback.
    - Phase 2 commit timeout, Server rollback to 157, and Client Lease Watchdog self-healing.
    """

    def setUp(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s1:
            s1.bind(("127.0.0.1", 0))
            self.server_port = s1.getsockname()[1]
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s2:
            s2.bind(("127.0.0.1", 0))
            self.broadcast_port = s2.getsockname()[1]

        self.server_adapter = MockNetworkAdapter()
        self.server_radio = RadioConfig(
            channel=DEFAULT_BENCHMARK_CHANNEL,  # 157
            radio_txpower_dbm=12,
            downlink_mcs=3,
            uplink_mcs=6,
        )
        self.server = ControlPlaneServer(
            active_radio_config=self.server_radio,
            network_adapter=self.server_adapter,
            air_interface="wlan_srv",
            bind_host="127.0.0.1",
            bind_port=self.server_port,
            broadcast_addr="255.255.255.255",
            broadcast_port=self.broadcast_port,
        )
        self.server.start()

        # Clients for node 1 (vm1) and node 2 (vm2)
        self.client1_adapter = MockNetworkAdapter()
        self.client1 = ControlPlaneClient(
            node_id=1,
            tun_ip="10.80.0.11",
            network_adapter=self.client1_adapter,
            air_interface="wlan1",
            initial_channel=DEFAULT_BENCHMARK_CHANNEL,
            cached_channel=DEFAULT_BENCHMARK_CHANNEL,
            txpower_dbm=12,
            uplink_mcs=6,
            server_host="127.0.0.1",
            server_port=self.server_port,
            broadcast_port=self.broadcast_port,
            attempt_timeout_seconds=0.1,
        )

        self.client2_adapter = MockNetworkAdapter()
        self.client2 = ControlPlaneClient(
            node_id=2,
            tun_ip="10.80.0.12",
            network_adapter=self.client2_adapter,
            air_interface="wlan2",
            initial_channel=DEFAULT_BENCHMARK_CHANNEL,
            cached_channel=DEFAULT_BENCHMARK_CHANNEL,
            txpower_dbm=12,
            uplink_mcs=6,
            server_host="127.0.0.1",
            server_port=self.server_port,
            broadcast_port=self.broadcast_port,
            attempt_timeout_seconds=0.1,
        )

    def tearDown(self):
        self.client1.stop()
        self.client2.stop()
        self.server.stop()

    def _bring_clients_online(self):
        """Helper to establish initial IDLE state and Server READY recognition on Channel 157."""
        self.client1.start()
        self.client2.start()

        # Wait for both clients to lock channel 157 and register as READY
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            n1 = self.server.registry.get_node(1)
            n2 = self.server.registry.get_node(2)
            if (
                n1 and n1.readiness == NodeReadiness.READY
                and n2 and n2.readiness == NodeReadiness.READY
            ):
                return
            time.sleep(0.05)
        self.fail("客户端未能成功上线至 READY 状态")

    def test_normal_atomic_two_phase_switch_to_channel_149(self):
        """
        Full two-phase reconfiguration from Channel 157 to Channel 149:
        - Server broadcasts CONFIG_RADIO_PREPARE.
        - Clients 1 & 2 return PREPARE_ACK.
        - Server broadcasts CONFIG_RADIO_COMMIT (delay=0.1s, lease=2.0s).
        - Clients and Server switch physical adapter to 149.
        - Server broadcasts NEW_CHANNEL_PING on 149.
        - Clients refresh watchdog and send COMMIT_SUCCESS.
        - Server broadcasts RADIO_SWITCH_FINALIZED.
        - Assert all nodes locked on 149 with disarmed watchdogs.
        """
        self._bring_clients_online()

        res = self.server.reconfigure_radio(
            patch={"target_channel": 149},
            target_nodes=[1, 2],
            prepare_timeout_seconds=1.0,
            commit_delay_seconds=0.1,
            commit_timeout_seconds=2.0,
            lease_timeout_seconds=2.0,
            ping_interval_seconds=0.08,
        )

        self.assertTrue(res.success, f"Reconfiguration failed: {res.error_message}")
        self.assertEqual(res.target_channel, 149)
        self.assertEqual(self.server.active_radio_config.channel, 149)

        # Give small window for client finalized processing
        time.sleep(0.1)

        self.assertEqual(self.client1.current_channel, 149)
        self.assertEqual(self.client1.locked_channel, 149)
        self.assertFalse(self.client1.lease_watchdog.is_armed)

        self.assertEqual(self.client2.current_channel, 149)
        self.assertEqual(self.client2.locked_channel, 149)
        self.assertFalse(self.client2.lease_watchdog.is_armed)

        # Verify adapter calls
        self.assertEqual(self.server_adapter.wireless_states["wlan_srv"]["channel"], 149)
        self.assertEqual(self.client1_adapter.wireless_states["wlan1"]["channel"], 149)
        self.assertEqual(self.client2_adapter.wireless_states["wlan2"]["channel"], 149)

    def test_incremental_patch_txpower_and_mcs_without_channel_hop(self):
        """
        Incremental PATCH: only txpower (15 dBm) or only uplink MCS (4).
        Channel remains 157, parameters are updated atomically.
        """
        self._bring_clients_online()

        # 1. Reconfigure txpower only
        res_pwr = self.server.reconfigure_radio(
            patch={"txpower_dbm": 15},
            target_nodes=[1, 2],
            prepare_timeout_seconds=1.0,
            commit_delay_seconds=0.05,
            commit_timeout_seconds=1.5,
            lease_timeout_seconds=2.0,
            ping_interval_seconds=0.08,
        )
        self.assertTrue(res_pwr.success)
        self.assertEqual(self.server.active_radio_config.channel, 157)
        self.assertEqual(self.server.active_radio_config.radio_txpower_dbm, 15)

        time.sleep(0.1)
        self.assertEqual(self.client1.txpower_dbm, 15)
        self.assertEqual(self.client2.txpower_dbm, 15)
        self.assertEqual(self.client1_adapter.wireless_states["wlan1"]["txpower_dbm"], 15)
        self.assertEqual(self.client2_adapter.wireless_states["wlan2"]["txpower_dbm"], 15)

        # 2. Reconfigure MCS only
        res_mcs = self.server.reconfigure_radio(
            patch={"uplink_mcs": 4},
            target_nodes=[1, 2],
            prepare_timeout_seconds=1.0,
            commit_delay_seconds=0.05,
            commit_timeout_seconds=1.5,
            lease_timeout_seconds=2.0,
            ping_interval_seconds=0.08,
        )
        self.assertTrue(res_mcs.success)
        self.assertEqual(self.server.active_radio_config.channel, 157)
        self.assertEqual(self.server.active_radio_config.uplink_mcs, 4)

        time.sleep(0.1)
        self.assertEqual(self.client1.uplink_mcs, 4)
        self.assertEqual(self.client2.uplink_mcs, 4)

    def test_phase1_missing_node_aborts_switch(self):
        """
        If any target node fails to return PREPARE_ACK within prepare_timeout_seconds,
        Server aborts Phase 1, broadcasts CONFIG_RADIO_ABORT, and stays on original channel.
        """
        self._bring_clients_online()

        # Suppress client 2's PREPARE_ACK by unregistering handler
        self.client2._broadcast_handlers["CONFIG_RADIO_PREPARE"] = []

        res = self.server.reconfigure_radio(
            patch={"target_channel": 153},
            target_nodes=[1, 2],
            prepare_timeout_seconds=0.25,
            commit_delay_seconds=0.1,
            commit_timeout_seconds=1.0,
        )

        self.assertFalse(res.success)
        self.assertEqual(res.failed_phase, "PREPARE")
        self.assertEqual(res.unresponsive_nodes, [2])

        # Cluster stays on original channel 157
        self.assertEqual(self.server.active_radio_config.channel, 157)
        self.assertEqual(self.client1.current_channel, 157)
        self.assertEqual(self.client2.current_channel, 157)
        self.assertFalse(self.client1.lease_watchdog.is_armed)

    def test_phase2_timeout_triggers_server_revert_and_client_watchdog_fallback(self):
        """
        Anomaly & split-brain prevention exercise:
        - Server and Client 1 switch to target Channel 149.
        - Client 2 is isolated / fails to report on 149.
        - Server times out on 149, ceases pings, and reverts to Channel 157!
        - Client 1's lease watchdog expires (since Server disappeared from 149).
        - Client 1's watchdog forces physical adapter back to Channel 157 and resumes hunting!
        - Client 1 rediscovers Server on 157 and re-registers as READY!
        - Cluster completely reunites on Channel 157 with zero brain split.
        """
        self._bring_clients_online()

        # Inhibit Client 2 from Phase 2: Client 2 acknowledges PREPARE but drops COMMIT
        self.client2._broadcast_handlers["CONFIG_RADIO_COMMIT"] = []

        res = self.server.reconfigure_radio(
            patch={"target_channel": 149},
            target_nodes=[1, 2],
            prepare_timeout_seconds=1.0,
            commit_delay_seconds=0.05,
            commit_timeout_seconds=0.35,  # Short commit timeout
            lease_timeout_seconds=0.55,   # Short lease watchdog
            ping_interval_seconds=0.08,
        )

        # Server Phase 2 must fail due to Client 2's absence
        self.assertFalse(res.success)
        self.assertEqual(res.failed_phase, "COMMIT")
        self.assertEqual(res.unresponsive_nodes, [2])

        # Server must immediately revert to Channel 157
        self.assertEqual(self.server.active_radio_config.channel, DEFAULT_BENCHMARK_CHANNEL)
        self.assertEqual(self.server_adapter.wireless_states["wlan_srv"]["channel"], DEFAULT_BENCHMARK_CHANNEL)

        # Client 1 was temporarily on 149 with armed watchdog
        # Now Server has ceased pings on 149.
        # Wait for Client 1's watchdog to expire and self-heal back to 157
        deadline = time.monotonic() + 2.5
        recovered = False
        while time.monotonic() < deadline:
            node1 = self.server.registry.get_node(1)
            if (
                self.client1.current_channel == DEFAULT_BENCHMARK_CHANNEL
                and self.client1.locked_channel == DEFAULT_BENCHMARK_CHANNEL
                and node1
                and node1.readiness == NodeReadiness.READY
                and node1.current_channel == DEFAULT_BENCHMARK_CHANNEL
            ):
                recovered = True
                break
            time.sleep(0.08)

        self.assertTrue(
            recovered,
            f"Client 1 未能通过看门狗自愈回退至 Channel 157! 当前信道: {self.client1.current_channel}, 状态: {self.client1.state}",
        )
        self.assertEqual(self.client1_adapter.wireless_states["wlan1"]["channel"], DEFAULT_BENCHMARK_CHANNEL)
        self.assertFalse(self.client1.lease_watchdog.is_armed)

    def test_phase1_negative_ack_aborts_switch(self):
        """If a client returns PREPARE_ACK(ok=False), Server immediately aborts Phase 1."""
        self._bring_clients_online()

        # Client 2 intercepts PREPARE and returns ok=False
        def _failing_prepare(msg):
            ack = PrepareAck(
                node_id=2,
                session_id=msg.get("session_id", ""),
                ok=False,
                error="simulated hardware failure",
            )
            self.client2._send_unicast_datagram(ack.to_bytes())

        self.client2._broadcast_handlers["CONFIG_RADIO_PREPARE"] = [_failing_prepare]

        res = self.server.reconfigure_radio(
            patch={"target_channel": 153},
            target_nodes=[1, 2],
            prepare_timeout_seconds=0.3,
            commit_delay_seconds=0.1,
            commit_timeout_seconds=1.0,
        )

        self.assertFalse(res.success)
        self.assertEqual(res.failed_phase, "PREPARE")
        self.assertEqual(res.unresponsive_nodes, [2])
        self.assertEqual(self.server.active_radio_config.channel, DEFAULT_BENCHMARK_CHANNEL)

    def test_forbidden_channel_161_fails_closed(self):
        """Specifying forbidden Channel 161 fails closed before Phase 1 begins."""
        self._bring_clients_online()
        with self.assertRaises(ValueError):
            self.server.reconfigure_radio(
                patch={"target_channel": 161},
                target_nodes=[1, 2],
            )

    def test_auto_discovery_of_target_nodes(self):
        """When target_nodes is None, reconfigure_radio auto-discovers online READY nodes."""
        self._bring_clients_online()

        res = self.server.reconfigure_radio(
            patch={"target_channel": 149},
            target_nodes=None,  # Auto-discover [1, 2]
            prepare_timeout_seconds=1.0,
            commit_delay_seconds=0.05,
            commit_timeout_seconds=1.5,
            lease_timeout_seconds=2.0,
            ping_interval_seconds=0.08,
        )
        self.assertTrue(res.success)
        self.assertEqual(res.target_channel, 149)
        self.assertEqual(self.server.active_radio_config.channel, 149)
        time.sleep(0.1)
        self.assertEqual(self.client1.locked_channel, 149)
        self.assertEqual(self.client2.locked_channel, 149)

    def test_stale_session_id_and_non_target_node_ignored(self):
        """Messages with stale session_id or excluding the node are safely ignored."""
        self._bring_clients_online()

        # 1. Non-target node in PREPARE
        prep_non_target = {
            "type": "CONFIG_RADIO_PREPARE",
            "session_id": "switch_test_stale",
            "patch": {"channel": 149},
            "target_nodes": [2],  # Excludes node 1
        }
        self.client1._handle_radio_prepare(prep_non_target)
        self.assertIsNone(self.client1._pending_switch_session_id)

        # 2. COMMIT with mismatched session_id is ignored
        self.client1._pending_switch_session_id = "real_session_456"
        stale_commit = {
            "type": "CONFIG_RADIO_COMMIT",
            "session_id": "stale_session_123",
            "patch": {"channel": 149},
            "target_nodes": [1],
        }
        self.client1._handle_radio_commit(stale_commit)
        self.assertFalse(self.client1.lease_watchdog.is_armed)

        # 3. PING with mismatched session_id does not trigger COMMIT_SUCCESS
        stale_ping = {
            "type": "NEW_CHANNEL_PING",
            "session_id": "stale_session_123",
            "channel": 157,
            "target_nodes": [1],
        }
        self.client1._handle_new_channel_ping(stale_ping)
        self.assertFalse(self.client1.lease_watchdog.is_armed)

    def test_full_parameter_rollback_on_phase2_timeout(self):
        """
        When Phase 2 times out, both Server and Client roll back ALL parameters
        (channel, txpower, MCS) atomically, not just the channel.
        """
        self._bring_clients_online()

        # Inhibit Client 2 from Phase 2
        self.client2._broadcast_handlers["CONFIG_RADIO_COMMIT"] = []

        # Target: change channel to 149 AND txpower to 18 dBm AND uplink MCS to 5
        res = self.server.reconfigure_radio(
            patch={"target_channel": 149, "txpower_dbm": 18, "uplink_mcs": 5},
            target_nodes=[1, 2],
            prepare_timeout_seconds=1.0,
            commit_delay_seconds=0.05,
            commit_timeout_seconds=0.35,
            lease_timeout_seconds=0.55,
            ping_interval_seconds=0.08,
        )

        self.assertFalse(res.success)
        self.assertEqual(res.failed_phase, "COMMIT")

        # Server must roll back physical channel to 157 AND txpower to 12 dBm
        self.assertEqual(self.server.active_radio_config.channel, DEFAULT_BENCHMARK_CHANNEL)
        self.assertEqual(self.server.active_radio_config.radio_txpower_dbm, 12)
        self.assertEqual(self.server_adapter.wireless_states["wlan_srv"]["channel"], DEFAULT_BENCHMARK_CHANNEL)
        self.assertEqual(self.server_adapter.wireless_states["wlan_srv"]["txpower_dbm"], 12)

        # Client 1 lease watchdog expires and must roll back channel to 157, txpower to 12 dBm, mcs to 6
        deadline = time.monotonic() + 2.5
        recovered = False
        while time.monotonic() < deadline:
            node1 = self.server.registry.get_node(1)
            if (
                self.client1.current_channel == DEFAULT_BENCHMARK_CHANNEL
                and self.client1.txpower_dbm == 12
                and self.client1.uplink_mcs == 6
                and node1
                and node1.readiness == NodeReadiness.READY
            ):
                recovered = True
                break
            time.sleep(0.08)

        self.assertTrue(recovered, "Client 1 未能完整回滚全部射频参数 (信道、发射功率与MCS)")
        self.assertEqual(self.client1_adapter.wireless_states["wlan1"]["channel"], DEFAULT_BENCHMARK_CHANNEL)
        self.assertEqual(self.client1_adapter.wireless_states["wlan1"]["txpower_dbm"], 12)
