"""
Tests for WFB-ng Stage 2 Symmetric UDP Control Plane, Three-Tier Hunting,
and Anti-Desync State Machine (Ticket 03).
"""

import json
import socket
import threading
import time
import unittest
from typing import Dict, List, Optional, Tuple
from unittest.mock import MagicMock

from wfb_ng.fl.control import (
    CANDIDATE_CHANNELS,
    DEFAULT_BENCHMARK_CHANNEL,
    FORBIDDEN_CHANNELS,
    HUNTING_ATTEMPT_TIMEOUT_SECONDS,
    HUNTING_RETRIES_PER_CHANNEL,
    IDLE_HEARTBEAT_INTERVAL_SECONDS,
    IDLE_MISSED_ACK_THRESHOLD,
    ACTIVE_HEARTBEAT_INTERVAL_SECONDS,
    NODE_OFFLINE_THRESHOLD_SECONDS,
    SERVER_CONTROL_BROADCAST_ADDR,
    SERVER_CONTROL_BROADCAST_PORT,
    CLIENT_UPLINK_DEFAULT_ADDR,
    CLIENT_UPLINK_DEFAULT_PORT,
    ClientNodeState,
    NodeReadiness,
    NodeHeartbeat,
    HeartbeatAck,
    NodeRecord,
    NodeHorizonRegistry,
    ControlPlaneServer,
    ControlPlaneClient,
    build_hunting_ladder,
)
from wfb_ng.fl.radio import RadioConfig, validate_radio_config
from wfb_ng.fl.client_daemon import (
    ClientDaemon,
    ClientDaemonConfig,
    DaemonState,
    NetworkAdapter,
)
from wfb_ng.tests.test_fl_client_daemon import MockNetworkAdapter


class FaultInjectingControlPlaneServer(ControlPlaneServer):
    """Test double for ControlPlaneServer allowing simulated packet loss."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.drop_downlink_acks = False
        self.packet_filter = None

    def handle_datagram(self, data: bytes, client_addr: Tuple[str, int]) -> Optional[List[bytes]]:
        if self.drop_downlink_acks:
            # Process internally to update registry horizon, but suppress downlink transmission
            super().handle_datagram(data, client_addr)
            return None
        if self.packet_filter is not None:
            hb = NodeHeartbeat.from_bytes(data)
            if not self.packet_filter(hb):
                return None
        return super().handle_datagram(data, client_addr)


class TestHuntingLadder(unittest.TestCase):
    """Test three-tier hunting ladder generation, monotonicity, and constraints."""

    def test_default_ladder_without_cache(self):
        ladder = build_hunting_ladder(None)
        self.assertEqual(ladder, [157, 149, 153, 165])
        self.assertEqual(len(ladder), 4)

    def test_ladder_with_cached_157(self):
        ladder = build_hunting_ladder(157)
        self.assertEqual(ladder, [157, 149, 153, 165])
        self.assertEqual(len(ladder), 4)

    def test_ladder_with_cached_149(self):
        ladder = build_hunting_ladder(149)
        self.assertEqual(ladder, [149, 157, 153, 165])
        self.assertEqual(len(ladder), 4)

    def test_ladder_with_cached_153(self):
        ladder = build_hunting_ladder(153)
        self.assertEqual(ladder, [153, 157, 149, 165])
        self.assertEqual(len(ladder), 4)

    def test_ladder_with_cached_165(self):
        ladder = build_hunting_ladder(165)
        self.assertEqual(ladder, [165, 157, 149, 153])
        self.assertEqual(len(ladder), 4)

    def test_forbidden_161_channel_strictly_excluded(self):
        # Channel 161 has known driver kernel crash defect and must be excluded
        ladder = build_hunting_ladder(161)
        self.assertEqual(ladder, [157, 149, 153, 165])
        self.assertNotIn(161, ladder)

    def test_invalid_channel_falls_back_to_benchmark(self):
        ladder = build_hunting_ladder(999)
        self.assertEqual(ladder, [157, 149, 153, 165])


class TestControlMessagesSerialization(unittest.TestCase):
    """Test JSON datagram schemas, ACK size bounds, and serialization."""

    def test_heartbeat_ack_boolean_size_bound(self):
        # Spec requirement: single boolean receipt {"ack": true} (< 20 bytes)
        ack = HeartbeatAck(ack=True)
        raw_bytes = ack.to_bytes()
        self.assertLess(len(raw_bytes), 20)
        self.assertEqual(raw_bytes, b'{"ack": true}')

    def test_protocol_fail_closed_validation(self):
        """Test strict fail-closed validation on protocol schemas without permissive fallbacks."""
        with self.assertRaises(ValueError):
            HeartbeatAck.from_dict({})
        with self.assertRaises(ValueError):
            HeartbeatAck.from_dict({"ack": "not_a_bool"})

        with self.assertRaises(ValueError):
            NodeHeartbeat.from_dict({})
        with self.assertRaises(ValueError):
            NodeHeartbeat.from_dict({
                "node_id": 99,
                "state": "IDLE",
                "elapsed_ms": 0,
                "current_channel": 157,
                "txpower_dbm": 12,
                "uplink_mcs": 6,
            })
        with self.assertRaises(ValueError):
            NodeHeartbeat.from_dict({
                "node_id": 1,
                "state": "INVALID_STATE",
                "elapsed_ms": 0,
                "current_channel": 157,
                "txpower_dbm": 12,
                "uplink_mcs": 6,
            })
        # Forbidden channel 161 fails closed
        with self.assertRaises(ValueError):
            NodeHeartbeat.from_dict({
                "node_id": 1,
                "state": "IDLE",
                "elapsed_ms": 0,
                "current_channel": 161,
                "txpower_dbm": 12,
                "uplink_mcs": 6,
            })
        # Invalid txpower fails closed
        with self.assertRaises(ValueError):
            NodeHeartbeat.from_dict({
                "node_id": 1,
                "state": "IDLE",
                "elapsed_ms": 0,
                "current_channel": 157,
                "txpower_dbm": 99,
                "uplink_mcs": 6,
            })

    def test_heartbeat_ack_strict_minimal_schema(self):
        """ADR-0012 minimalism: HeartbeatAck is strictly b'{"ack": true}' (< 20 bytes)."""
        ack = HeartbeatAck(ack=True)
        raw = ack.to_bytes()
        self.assertEqual(raw, b'{"ack": true}')
        self.assertEqual(len(raw), 13)
        self.assertLess(len(raw), 20)

        parsed = HeartbeatAck.from_bytes(raw)
        self.assertTrue(parsed.ack)

    def test_node_heartbeat_serialization_roundtrip(self):
        hb = NodeHeartbeat(
            node_id=1,
            state=ClientNodeState.HUNTING.value,
            elapsed_ms=150,
            current_channel=157,
            txpower_dbm=12,
            uplink_mcs=6,
            error_code=None,
            timestamp_ms=1000,
        )
        raw_bytes = hb.to_bytes()
        parsed = NodeHeartbeat.from_bytes(raw_bytes)
        self.assertEqual(parsed.node_id, 1)
        self.assertEqual(parsed.state, "HUNTING")
        self.assertEqual(parsed.elapsed_ms, 150)
        self.assertEqual(parsed.current_channel, 157)
        self.assertEqual(parsed.txpower_dbm, 12)
        self.assertEqual(parsed.uplink_mcs, 6)


class TestNodeHorizonRegistryAndAntiDesyncGate(unittest.TestCase):
    """Test server-side registry, two-army anti-desync gate, and alignment dispatch."""

    def setUp(self):
        self.registry = NodeHorizonRegistry()
        self.server_radio = RadioConfig(
            channel=157,
            radio_txpower_dbm=12,
            downlink_mcs=3,
            uplink_mcs=6,
        )

    def test_hunting_state_keeps_node_in_connecting(self):
        hb = NodeHeartbeat(
            node_id=1,
            state=ClientNodeState.HUNTING.value,
            elapsed_ms=100,
            current_channel=157,
            txpower_dbm=12,
            uplink_mcs=6,
        )
        ack, patch = self.registry.update_heartbeat(
            hb, client_addr=("10.80.0.11", 50000), server_radio=self.server_radio
        )
        self.assertTrue(ack.ack)
        self.assertIsNone(patch)

        node = self.registry.get_node(1)
        self.assertIsNotNone(node)
        self.assertEqual(node.reported_state, "HUNTING")
        # Anti-desync gate: MUST be CONNECTING, NEVER READY
        self.assertEqual(node.readiness, NodeReadiness.CONNECTING)
        self.assertFalse(self.registry.is_node_ready(1))

    def test_idle_state_transitions_node_to_ready(self):
        # First client sends HUNTING
        hb_hunting = NodeHeartbeat(
            node_id=1,
            state=ClientNodeState.HUNTING.value,
            elapsed_ms=100,
            current_channel=157,
            txpower_dbm=12,
            uplink_mcs=6,
        )
        self.registry.update_heartbeat(
            hb_hunting, client_addr=("10.80.0.11", 50000), server_radio=self.server_radio
        )
        self.assertEqual(self.registry.get_node(1).readiness, NodeReadiness.CONNECTING)

        # After client receives ACK and transitions to IDLE, it immediately reports IDLE
        hb_idle = NodeHeartbeat(
            node_id=1,
            state=ClientNodeState.IDLE.value,
            elapsed_ms=0,
            current_channel=157,
            txpower_dbm=12,
            uplink_mcs=6,
        )
        self.registry.update_heartbeat(
            hb_idle, client_addr=("10.80.0.11", 50000), server_radio=self.server_radio
        )
        node = self.registry.get_node(1)
        self.assertEqual(node.reported_state, "IDLE")
        # Anti-desync gate: only now is the ready green light on!
        self.assertEqual(node.readiness, NodeReadiness.READY)
        self.assertTrue(self.registry.is_node_ready(1))

    def test_unsolicited_idle_held_in_connecting(self):
        """
        An unsolicited IDLE heartbeat for an unknown node with no preceding
        HUNTING observation must NOT be granted immediate READY status.
        It must be held in CONNECTING.
        """
        hb_unsolicited_idle = NodeHeartbeat(
            node_id=3,
            state=ClientNodeState.IDLE.value,
            elapsed_ms=0,
            current_channel=157,
            txpower_dbm=12,
            uplink_mcs=6,
        )
        self.registry.update_heartbeat(
            hb_unsolicited_idle,
            client_addr=("10.80.0.13", 50000),
            server_radio=self.server_radio,
        )
        node = self.registry.get_node(3)
        self.assertIsNotNone(node)
        self.assertEqual(node.readiness, NodeReadiness.CONNECTING)
        self.assertFalse(self.registry.is_node_ready(3))

    def test_radio_mismatch_triggers_alignment_directive(self):
        # Client sends heartbeat with txpower 20 dBm and uplink_mcs 3 (mismatch)
        hb_mismatch = NodeHeartbeat(
            node_id=2,
            state=ClientNodeState.HUNTING.value,
            elapsed_ms=50,
            current_channel=157,
            txpower_dbm=20,
            uplink_mcs=3,
        )
        ack, patch = self.registry.update_heartbeat(
            hb_mismatch, client_addr=("10.80.0.12", 50000), server_radio=self.server_radio
        )
        self.assertTrue(ack.ack)
        self.assertIsNotNone(patch)
        # Server automatically computes alignment patch
        self.assertEqual(patch.get("radio_txpower_dbm"), 12)
        self.assertEqual(patch.get("uplink_mcs"), 6)
        # Channel is NOT in alignment patch per rule #89
        self.assertNotIn("channel", patch)

    def test_node_offline_timeout(self):
        # Establish node through legitimate HUNTING -> IDLE handshake
        hb_hunt = NodeHeartbeat(
            node_id=1,
            state=ClientNodeState.HUNTING.value,
            elapsed_ms=0,
            current_channel=157,
            txpower_dbm=12,
            uplink_mcs=6,
        )
        self.registry.update_heartbeat(
            hb_hunt, client_addr=("10.80.0.11", 50000), server_radio=self.server_radio
        )
        hb_idle = NodeHeartbeat(
            node_id=1,
            state=ClientNodeState.IDLE.value,
            elapsed_ms=0,
            current_channel=157,
            txpower_dbm=12,
            uplink_mcs=6,
        )
        self.registry.update_heartbeat(
            hb_idle, client_addr=("10.80.0.11", 50000), server_radio=self.server_radio
        )
        self.assertTrue(self.registry.is_node_ready(1))

        # Fast forward time beyond 10s threshold
        node = self.registry.get_node(1)
        node.last_heartbeat_time -= 11.0

        self.assertFalse(self.registry.is_node_ready(1))
        self.assertEqual(self.registry.get_node(1).readiness, NodeReadiness.OFFLINE)

    def test_reordered_and_replayed_heartbeats_idempotency_and_monotonicity(self):
        """
        Verify that out-of-order and replayed stale heartbeats are ignored,
        and an old replayed IDLE packet cannot falsely grant READY status.
        """
        # Node sends HUNTING at t=1000
        hb_hunt = NodeHeartbeat(
            node_id=4,
            state=ClientNodeState.HUNTING.value,
            elapsed_ms=0,
            current_channel=157,
            txpower_dbm=12,
            uplink_mcs=6,
            timestamp_ms=1000,
        )
        self.registry.update_heartbeat(
            hb_hunt, client_addr=("10.80.0.14", 50000), server_radio=self.server_radio
        )
        self.assertEqual(self.registry.get_node(4).readiness, NodeReadiness.CONNECTING)

        # Stale replayed IDLE packet from a prior session with t=500 (< 1000)
        hb_stale_idle = NodeHeartbeat(
            node_id=4,
            state=ClientNodeState.IDLE.value,
            elapsed_ms=0,
            current_channel=157,
            txpower_dbm=12,
            uplink_mcs=6,
            timestamp_ms=500,
        )
        self.registry.update_heartbeat(
            hb_stale_idle, client_addr=("10.80.0.14", 50000), server_radio=self.server_radio
        )
        # Must still be CONNECTING! Not READY!
        self.assertEqual(self.registry.get_node(4).readiness, NodeReadiness.CONNECTING)
        self.assertFalse(self.registry.is_node_ready(4))

        # Fresh valid IDLE packet at t=1050 (>= 1000)
        hb_valid_idle = NodeHeartbeat(
            node_id=4,
            state=ClientNodeState.IDLE.value,
            elapsed_ms=50,
            current_channel=157,
            txpower_dbm=12,
            uplink_mcs=6,
            timestamp_ms=1050,
        )
        self.registry.update_heartbeat(
            hb_valid_idle, client_addr=("10.80.0.14", 50000), server_radio=self.server_radio
        )
        # Now validly READY!
        self.assertEqual(self.registry.get_node(4).readiness, NodeReadiness.READY)
        self.assertTrue(self.registry.is_node_ready(4))

        # Replay stale HUNTING from t=900 (should not downgrade READY to CONNECTING)
        hb_old_hunt = NodeHeartbeat(
            node_id=4,
            state=ClientNodeState.HUNTING.value,
            elapsed_ms=0,
            current_channel=157,
            txpower_dbm=12,
            uplink_mcs=6,
            timestamp_ms=900,
        )
        self.registry.update_heartbeat(
            hb_old_hunt, client_addr=("10.80.0.14", 50000), server_radio=self.server_radio
        )
        self.assertEqual(self.registry.get_node(4).readiness, NodeReadiness.READY)


class TestControlPlaneNetworkLoop(unittest.TestCase):
    """End-to-end socket testing of UDP control plane, hunting, and anti-desync."""

    def setUp(self):
        # Use loopback and dynamic ports for test isolation
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s1:
            s1.bind(("127.0.0.1", 0))
            self.server_port = s1.getsockname()[1]
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s2:
            s2.bind(("127.0.0.1", 0))
            self.client_broadcast_port = s2.getsockname()[1]

        self.server_radio = RadioConfig(
            channel=153,  # Server is on candidate channel 153
            radio_txpower_dbm=12,
            downlink_mcs=3,
            uplink_mcs=6,
        )
        self.server = FaultInjectingControlPlaneServer(
            active_radio_config=self.server_radio,
            bind_host="127.0.0.1",
            bind_port=self.server_port,
            broadcast_addr="255.255.255.255",
            broadcast_port=self.client_broadcast_port,
        )
        self.server.start()

    def tearDown(self):
        self.server.stop()

    def test_unidirectional_drop_two_army_simulation(self):
        """
        Simulate 'uplink works, downlink drops' two-army scenario:
        - Client sends HUNTING to Server.
        - Downlink drop filter drops Server's ACK.
        - Assert Client stays in HUNTING and never sends IDLE.
        - Assert Server stays in CONNECTING, never lights up READY green light.
        - Downlink restored: Client receives ACK, transitions to IDLE, sends IDLE.
        - Assert Server transitions to READY!
        """
        adapter = MockNetworkAdapter()
        client = ControlPlaneClient(
            node_id=1,
            tun_ip="10.80.0.11",
            network_adapter=adapter,
            air_interface="wlan0",
            initial_channel=153,
            cached_channel=153,
            txpower_dbm=12,
            uplink_mcs=6,
            server_host="127.0.0.1",
            server_port=self.server_port,
            broadcast_port=self.client_broadcast_port,
            attempt_timeout_seconds=0.1,
            max_attempts_per_channel=2,
        )

        # 1. Enable simulated downlink drop on server
        self.server.drop_downlink_acks = True

        # Run one hunting probe attempt on 153
        found = client.hunt_once(ladder=[153])
        self.assertFalse(found)
        self.assertEqual(client.state, ClientNodeState.HUNTING)

        # Server received HUNTING, but ACK was dropped
        node = self.server.registry.get_node(1)
        self.assertIsNotNone(node)
        self.assertEqual(node.reported_state, "HUNTING")
        self.assertEqual(node.readiness, NodeReadiness.CONNECTING)
        self.assertFalse(self.server.registry.is_node_ready(1))

        # 2. Restore downlink
        self.server.drop_downlink_acks = False

        # Run hunting probe again
        found = client.hunt_once(ladder=[153])
        self.assertTrue(found)
        self.assertEqual(client.state, ClientNodeState.IDLE)
        self.assertEqual(client.locked_channel, 153)

        # Wait briefly for instant IDLE heartbeat to be processed by server
        time.sleep(0.05)

        node = self.server.registry.get_node(1)
        self.assertEqual(node.reported_state, "IDLE")
        self.assertEqual(node.readiness, NodeReadiness.READY)
        self.assertTrue(self.server.registry.is_node_ready(1))

        client.stop()

    def test_three_tier_hunting_sweep_progression_and_timing(self):
        """
        Server is on channel 153.
        Client starts with cached channel 157.
        Progression order must be: 157 (retries) -> 149 (retries) -> 153 (found!).
        Channel 165 must not be probed.
        """
        adapter = MockNetworkAdapter()
        client = ControlPlaneClient(
            node_id=2,
            tun_ip="10.80.0.12",
            network_adapter=adapter,
            air_interface="wlan0",
            initial_channel=157,
            cached_channel=157,
            txpower_dbm=12,
            uplink_mcs=6,
            server_host="127.0.0.1",
            server_port=self.server_port,
            broadcast_port=self.client_broadcast_port,
            attempt_timeout_seconds=0.08,
            max_attempts_per_channel=2,
        )

        # When channel != 153, drop ACK to simulate RF mismatch on wrong channels
        def channel_filter(hb: NodeHeartbeat) -> bool:
            return hb.current_channel == 153

        self.server.packet_filter = channel_filter

        t0 = time.monotonic()
        found = client.hunt_once()
        elapsed = time.monotonic() - t0

        self.assertTrue(found)
        self.assertEqual(client.locked_channel, 153)
        self.assertEqual(client.state, ClientNodeState.IDLE)

        # Verify monotonic channel progression
        self.assertEqual(adapter.channel_history, [157, 149, 153])
        # Total time took ~2 channels of timeout + fast success on 3rd
        self.assertLess(elapsed, 1.0)

        client.stop()

    def test_full_pool_sweep_under_two_seconds(self):
        """
        When server is unreachable, all 4 channels in the pool must be swept in < 2 seconds.
        Verify production default parameters strictly satisfy the 2-second constraint.
        """
        from wfb_ng.fl.control import HUNTING_ATTEMPT_TIMEOUT_SECONDS
        self.assertEqual(HUNTING_ATTEMPT_TIMEOUT_SECONDS, 0.23)

        adapter = MockNetworkAdapter()
        client = ControlPlaneClient(
            node_id=3,
            tun_ip="10.80.0.13",
            network_adapter=adapter,
            air_interface="wlan0",
            initial_channel=157,
            cached_channel=None,
            txpower_dbm=12,
            uplink_mcs=6,
            server_host="127.0.0.1",
            server_port=self.server_port,
            broadcast_port=self.client_broadcast_port,
        )

        # Server drops all packets to simulate no server
        self.server.drop_downlink_acks = True

        t0 = time.monotonic()
        found = client.hunt_once()
        elapsed = time.monotonic() - t0

        self.assertFalse(found)
        self.assertEqual(adapter.channel_history, [157, 149, 153, 165])
        # Must sweep all 4 channels within 2 seconds
        self.assertLess(elapsed, 2.0)
        self.assertGreaterEqual(elapsed, 1.5)

        client.stop()

    def test_radio_alignment_applied_by_client(self):
        """
        Client starts with txpower=20 dBm and uplink_mcs=3.
        Server expects txpower=12 dBm and uplink_mcs=6.
        Client receives alignment directive, updates hardware adapter and internal state,
        and its next heartbeat confirms aligned parameters.
        """
        adapter = MockNetworkAdapter()
        client = ControlPlaneClient(
            node_id=1,
            tun_ip="10.80.0.11",
            network_adapter=adapter,
            air_interface="wlan0",
            initial_channel=153,
            cached_channel=153,
            txpower_dbm=20,  # mismatch
            uplink_mcs=3,   # mismatch
            server_host="127.0.0.1",
            server_port=self.server_port,
            broadcast_port=self.client_broadcast_port,
            attempt_timeout_seconds=0.1,
            max_attempts_per_channel=2,
        )

        found = client.hunt_once(ladder=[153])
        self.assertTrue(found)

        # Verify client applied alignment
        self.assertEqual(client.txpower_dbm, 12)
        self.assertEqual(client.uplink_mcs, 6)
        self.assertEqual(adapter.wireless_states["wlan0"]["txpower_dbm"], 12)

        # Allow instant IDLE heartbeat to reach server
        time.sleep(0.05)
        node = self.server.registry.get_node(1)
        self.assertEqual(node.txpower_dbm, 12)
        self.assertEqual(node.uplink_mcs, 6)
        self.assertEqual(node.readiness, NodeReadiness.READY)

        client.stop()

    def test_instant_heartbeat_on_state_transition(self):
        """
        When client transitions state (e.g. IDLE -> RUNNING), it must immediately
        send a heartbeat with 0 delay.
        """
        adapter = MockNetworkAdapter()
        client = ControlPlaneClient(
            node_id=1,
            tun_ip="10.80.0.11",
            network_adapter=adapter,
            air_interface="wlan0",
            initial_channel=153,
            cached_channel=153,
            txpower_dbm=12,
            uplink_mcs=6,
            server_host="127.0.0.1",
            server_port=self.server_port,
            broadcast_port=self.client_broadcast_port,
            attempt_timeout_seconds=0.1,
            idle_interval_seconds=5.0,
            active_interval_seconds=2.0,
        )

        client.hunt_once(ladder=[153])
        time.sleep(0.05)
        self.assertEqual(self.server.registry.get_node(1).reported_state, "IDLE")

        # Instant state transition
        t0 = time.monotonic()
        client.notify_state_change(ClientNodeState.RUNNING)
        time.sleep(0.05)
        elapsed = time.monotonic() - t0

        self.assertLess(elapsed, 0.2)
        node = self.server.registry.get_node(1)
        self.assertEqual(node.reported_state, "RUNNING")
        self.assertEqual(node.readiness, NodeReadiness.ACTIVE)

        client.stop()

    def test_missed_acks_in_idle_triggers_fallback_to_hunting(self):
        """
        In IDLE, missing 2 consecutive ACKs (10s threshold) triggers fallback
        to HUNTING state.
        """
        adapter = MockNetworkAdapter()
        client = ControlPlaneClient(
            node_id=1,
            tun_ip="10.80.0.11",
            network_adapter=adapter,
            air_interface="wlan0",
            initial_channel=153,
            cached_channel=153,
            txpower_dbm=12,
            uplink_mcs=6,
            server_host="127.0.0.1",
            server_port=self.server_port,
            broadcast_port=self.client_broadcast_port,
            attempt_timeout_seconds=0.05,
            idle_interval_seconds=0.05,  # fast interval for test
        )

        client.hunt_once(ladder=[153])
        self.assertEqual(client.state, ClientNodeState.IDLE)

        # Drop ACKs
        self.server.drop_downlink_acks = True

        # Send heartbeat once (miss 1)
        client.send_heartbeat_once(timeout=0.05)
        self.assertEqual(client.state, ClientNodeState.IDLE)
        self.assertEqual(client.missed_acks, 1)

        # Send heartbeat second time (miss 2 -> threshold reached)
        client.send_heartbeat_once(timeout=0.05)
        self.assertEqual(client.state, ClientNodeState.HUNTING)

        client.stop()

    def test_server_broadcast_downlink_received_by_client(self):
        """
        Server broadcasts to port 19000 (production 9000).
        Client background listener receives the broadcast and invokes handlers.
        """
        adapter = MockNetworkAdapter()
        client = ControlPlaneClient(
            node_id=1,
            tun_ip="10.80.0.11",
            network_adapter=adapter,
            air_interface="wlan0",
            initial_channel=153,
            cached_channel=153,
            txpower_dbm=12,
            uplink_mcs=6,
            server_host="127.0.0.1",
            server_port=self.server_port,
            broadcast_port=self.client_broadcast_port,
        )
        client.start_broadcast_listener()

        received_events = []
        client.register_broadcast_handler(
            "TASK_ANNOUNCE", lambda msg: received_events.append(msg)
        )

        self.server.broadcast_downlink({
            "type": "TASK_ANNOUNCE",
            "task_id": "job-123",
            "rounds": 2,
        })

        time.sleep(0.1)
        self.assertEqual(len(received_events), 1)
        self.assertEqual(received_events[0]["task_id"], "job-123")

        client.stop()

    def test_multi_client_broadcast_reception(self):
        """
        Verify that multiple clients (e.g. node 1 and node 2) receive
        server broadcast downlinks simultaneously via SO_REUSEPORT/SO_BROADCAST.
        """
        adapter1 = MockNetworkAdapter(["wlan0"])
        client1 = ControlPlaneClient(
            node_id=1,
            tun_ip="10.80.0.11",
            network_adapter=adapter1,
            air_interface="wlan0",
            initial_channel=153,
            cached_channel=153,
            server_host="127.0.0.1",
            server_port=self.server_port,
            broadcast_port=self.client_broadcast_port,
        )
        adapter2 = MockNetworkAdapter(["wlan1"])
        client2 = ControlPlaneClient(
            node_id=2,
            tun_ip="10.80.0.12",
            network_adapter=adapter2,
            air_interface="wlan1",
            initial_channel=153,
            cached_channel=153,
            server_host="127.0.0.1",
            server_port=self.server_port,
            broadcast_port=self.client_broadcast_port,
        )

        client1.start_broadcast_listener()
        client2.start_broadcast_listener()

        c1_events = []
        c2_events = []
        client1.register_broadcast_handler("TASK_ANNOUNCE", lambda m: c1_events.append(m))
        client2.register_broadcast_handler("TASK_ANNOUNCE", lambda m: c2_events.append(m))

        self.server.broadcast_downlink({
            "type": "TASK_ANNOUNCE",
            "task_id": "broadcast_multi_client",
        })

        time.sleep(0.15)
        self.assertEqual(len(c1_events), 1)
        self.assertEqual(len(c2_events), 1)
        self.assertEqual(c1_events[0]["task_id"], "broadcast_multi_client")
        self.assertEqual(c2_events[0]["task_id"], "broadcast_multi_client")

        client1.stop()
        client2.stop()

    def test_client_daemon_control_plane_integration(self):
        """
        Verify that ClientDaemon orchestrates ControlPlaneClient:
        - When daemon starts and finds interface, control plane starts.
        - Client hunts and reaches server, transitions to IDLE.
        - Daemon reports IDLE, server reports READY.
        """
        adapter = MockNetworkAdapter(["wlan0"])
        config = ClientDaemonConfig(
            node_id=1,
            tun_ip="10.80.0.11",
            channel=153,
            server_control_host="127.0.0.1",
            server_control_port=self.server_port,
            broadcast_port=self.client_broadcast_port,
            enable_control_plane=True,
            enable_link_process=False,
            poll_interval_seconds=0.05,
        )
        daemon = ClientDaemon(config, network_adapter=adapter)

        # Start daemon supervisory loop in background thread
        daemon_thread = threading.Thread(target=daemon.run, daemon=True)
        daemon_thread.start()

        # Wait for control plane to hunt and lock
        max_wait = 2.0
        start_t = time.monotonic()
        while time.monotonic() - start_t < max_wait:
            if daemon.state == DaemonState.IDLE and self.server.registry.is_node_ready(1):
                break
            time.sleep(0.05)

        self.assertEqual(daemon.state, DaemonState.IDLE)
        self.assertTrue(self.server.registry.is_node_ready(1))

        node = self.server.registry.get_node(1)
        self.assertEqual(node.reported_state, "IDLE")
        self.assertEqual(node.readiness, NodeReadiness.READY)

        daemon.stop()
        daemon_thread.join(timeout=1.0)

    def test_client_daemon_task_announce_and_abort_handling(self):
        """
        Verify that ClientDaemon receives TASK_ANNOUNCE and JOB_ABORT over control plane:
        - TASK_ANNOUNCE transitions daemon to PREPARING / triggers job.
        - JOB_ABORT broadcasts abort and restores IDLE.
        """
        import sys
        adapter = MockNetworkAdapter(["wlan0"])
        config = ClientDaemonConfig(
            node_id=1,
            tun_ip="10.80.0.11",
            channel=153,
            server_control_host="127.0.0.1",
            server_control_port=self.server_port,
            broadcast_port=self.client_broadcast_port,
            enable_control_plane=True,
            enable_link_process=False,
            poll_interval_seconds=0.05,
        )
        daemon = ClientDaemon(config, network_adapter=adapter)
        stub_script = "import time; time.sleep(10)"
        daemon.sandbox._command_prefix = [sys.executable, "-c", stub_script]

        daemon_thread = threading.Thread(target=daemon.run, daemon=True)
        daemon_thread.start()

        # Wait for daemon to be ready in IDLE
        start_t = time.monotonic()
        while time.monotonic() - start_t < 2.0:
            if daemon.state == DaemonState.IDLE and self.server.registry.is_node_ready(1):
                break
            time.sleep(0.05)

        self.assertEqual(daemon.state, DaemonState.IDLE)

        # Broadcast TASK_ANNOUNCE targeted to node 1
        self.server.broadcast_downlink({
            "type": "TASK_ANNOUNCE",
            "job_id": "job_fl_001",
            "target_nodes": [1, 2],
            "rounds": 1,
            "algorithm": "wfb_ng.fl.issue41_algorithm:client_main",
            "algorithm_config": {},
            "server_http_host": "10.80.0.1",
            "server_http_port": 8080,
            "uftp_port": 1044,
            "link_id": 7669206,
        })

        # Wait for job to start
        start_t = time.monotonic()
        while time.monotonic() - start_t < 2.0:
            if daemon.state == DaemonState.RUNNING:
                break
            time.sleep(0.05)

        self.assertEqual(daemon.state, DaemonState.RUNNING)

        # Broadcast JOB_ABORT
        self.server.broadcast_downlink({"type": "JOB_ABORT"})

        # Wait for job to be aborted and return to IDLE
        start_t = time.monotonic()
        while time.monotonic() - start_t < 2.0:
            if daemon.state == DaemonState.IDLE:
                break
            time.sleep(0.05)

        self.assertEqual(daemon.state, DaemonState.IDLE)

        daemon.stop()
        daemon_thread.join(timeout=1.0)

    def test_hardware_reconfiguration_failure_fails_closed(self):
        """
        When physical hardware reconfiguration fails, control plane operations
        must fail closed and NOT falsely record state as changed.
        """
        class BrokenAdapter(MockNetworkAdapter):
            def set_channel(self, iface, channel, channel_width="HT40+"):
                raise OSError("Physical device busy or removed")

            def set_txpower(self, iface, txpower_dbm):
                raise OSError("Device driver error")

        adapter = BrokenAdapter(["wlan0"])
        client = ControlPlaneClient(
            node_id=1,
            tun_ip="10.80.0.11",
            network_adapter=adapter,
            air_interface="wlan0",
            initial_channel=157,
            txpower_dbm=12,
        )

        with self.assertRaises(OSError):
            client._apply_channel_switch(149)
        self.assertEqual(client.current_channel, 157)  # Unchanged!

        with self.assertRaises(OSError):
            client._apply_txpower_switch(20)
        self.assertEqual(client.txpower_dbm, 12)  # Unchanged!

    def test_client_daemon_persists_locked_channel_across_hotplug(self):
        """
        Verify that ClientDaemon retains the discovered locked channel across
        hardware disconnect/reconnect cycles instead of reverting to initial config.
        """
        adapter = MockNetworkAdapter(["wlan0"])
        config = ClientDaemonConfig(
            node_id=1,
            tun_ip="10.80.0.11",
            channel=157,  # Initial config is 157
            server_control_host="127.0.0.1",
            server_control_port=self.server_port,
            broadcast_port=self.client_broadcast_port,
            enable_control_plane=True,
            poll_interval_seconds=0.05,
        )
        # Simulate RF: server only receives packets when client sweeps to 153
        self.server.packet_filter = lambda hb: hb.current_channel == 153

        daemon = ClientDaemon(config, network_adapter=adapter)
        daemon.poll_hardware_once()
        daemon.start_control_plane()

        # Wait for control plane background loop to hunt and lock 153
        start_t = time.monotonic()
        while time.monotonic() - start_t < 2.0:
            if daemon.control_plane.locked_channel == 153:
                break
            time.sleep(0.05)
        self.assertEqual(daemon.control_plane.locked_channel, 153)

        # Unplug interface
        daemon._on_interface_lost()
        self.assertIsNone(daemon.current_interface)
        self.assertIsNone(daemon.control_plane)
        # Verify daemon preserved the locked channel!
        self.assertEqual(daemon._last_locked_channel, 153)

        # Replug interface
        adapter.set_interfaces(["wlan0"])
        daemon.poll_hardware_once()
        daemon.start_control_plane()
        # Verify the new control plane uses 153 as Tier 1 cached channel!
        self.assertEqual(daemon.control_plane.cached_channel, 153)

        daemon.stop()


if __name__ == "__main__":
    unittest.main()
