"""Ticket 07: terminal recovery through the real control datagram entry point.

No sockets, hardware, sleeps, or registry internals are needed: only the server
clock is controlled. Verified identity survives offline liveness projections.
"""

import json
import unittest
from unittest.mock import patch

from wfb_ng.fl.control import (
    ClientNodeState,
    ControlPlaneServer,
    HeartbeatAck,
    NodeHeartbeat,
    NodeReadiness,
)
from wfb_ng.fl.radio import RadioConfig


class TestTerminalRegistry(unittest.TestCase):
    def setUp(self):
        self.clock = patch("wfb_ng.fl.control.time.monotonic", return_value=100.0)
        self.now = self.clock.start()
        self.addCleanup(self.clock.stop)
        self.server = ControlPlaneServer(
            active_radio_config=RadioConfig(
                channel=157, radio_txpower_dbm=12, downlink_mcs=3, uplink_mcs=6,
            ),
        )
        self.addr = ("10.80.0.11", 50000)

    def heartbeat(self, state, timestamp_ms):
        replies = self.server.handle_datagram(
            NodeHeartbeat(
                node_id=1, state=state.value, elapsed_ms=0,
                current_channel=157, txpower_dbm=12, uplink_mcs=6,
                timestamp_ms=timestamp_ms,
            ).to_bytes(),
            self.addr,
        )
        self.assertEqual(replies, [HeartbeatAck().to_bytes()])
        return self.server.registry.get_node(1)

    def establish(self, active=False):
        self.heartbeat(ClientNodeState.HUNTING, 1000)
        self.assertEqual(
            self.heartbeat(ClientNodeState.IDLE, 1001).readiness,
            NodeReadiness.READY,
        )
        if active:
            self.assertEqual(
                self.heartbeat(ClientNodeState.RUNNING, 1002).readiness,
                NodeReadiness.ACTIVE,
            )

    def go_offline(self):
        self.now.return_value = 111.0
        record = self.server.registry.get_node(1)
        self.assertEqual(record.readiness, NodeReadiness.OFFLINE)
        return record

    def assert_idle_recovers(self, active):
        self.establish(active=active)
        self.go_offline()
        observed = []
        # Every packet is fresh, ACKed, and within the next offline deadline.
        # Check repeated heartbeats as well as the first recovery packet.
        for index in range(6):
            self.now.return_value = 111.0 + index * 5.0
            record = self.heartbeat(ClientNodeState.IDLE, 2000 + index)
            observed.append(record.readiness.value)
        self.assertEqual(observed, [NodeReadiness.READY.value] * 6)
        self.assertTrue(self.server.registry.is_node_ready(1))

    def test_ready_node_recovers_after_offline_with_acknowledged_idle(self):
        self.assert_idle_recovers(active=False)

    def test_active_node_recovers_after_offline_with_acknowledged_idle(self):
        self.assert_idle_recovers(active=True)

    def test_offline_view_preserves_replay_timestamp(self):
        self.establish(active=True)
        self.assertEqual(self.go_offline().timestamp_ms, 1002)

    def test_stale_running_cannot_revive_offline_node(self):
        self.establish(active=True)
        self.go_offline()
        record = self.heartbeat(ClientNodeState.RUNNING, 1001)
        self.assertEqual(record.readiness, NodeReadiness.OFFLINE)
        self.assertEqual(record.timestamp_ms, 1002)
        self.assertEqual(record.last_heartbeat_time, 100.0)

    def test_stale_hunting_cannot_reopen_offline_identity_gate(self):
        self.establish()
        self.go_offline()
        record = self.heartbeat(ClientNodeState.HUNTING, 999)
        self.assertEqual(record.readiness, NodeReadiness.OFFLINE)
        self.assertEqual(record.timestamp_ms, 1001)
        self.assertEqual(record.last_heartbeat_time, 100.0)

    def test_fresh_hunting_then_idle_recovers_offline_node(self):
        self.establish(active=True)
        self.go_offline()
        self.assertEqual(
            self.heartbeat(ClientNodeState.HUNTING, 2000).readiness,
            NodeReadiness.CONNECTING,
        )
        self.assertEqual(
            self.heartbeat(ClientNodeState.IDLE, 2001).readiness,
            NodeReadiness.READY,
        )
        self.assertTrue(self.server.registry.is_node_ready(1))

    def test_unverified_idle_remains_connecting(self):
        for timestamp_ms in (1000, 1001, 1002):
            self.assertEqual(
                self.heartbeat(ClientNodeState.IDLE, timestamp_ms).readiness,
                NodeReadiness.CONNECTING,
            )
        self.assertFalse(self.server.registry.is_node_ready(1))

    def test_unverified_running_does_not_grant_persistent_verified_identity(self):
        self.assertEqual(
            self.heartbeat(ClientNodeState.RUNNING, 1000).readiness,
            NodeReadiness.ACTIVE,
        )
        self.assertEqual(
            self.heartbeat(ClientNodeState.IDLE, 1001).readiness,
            NodeReadiness.CONNECTING,
        )
        self.go_offline()
        self.assertEqual(
            self.heartbeat(ClientNodeState.IDLE, 1002).readiness,
            NodeReadiness.CONNECTING,
        )
        self.assertFalse(self.server.registry.is_node_ready(1))

    def test_invalid_timestamp_rejected_before_registry_update(self):
        self.establish(active=True)
        self.go_offline()
        before = self.server.registry.get_node(1).to_dict()
        invalid = (None, True, False, 0, -1, 1.5, "1003", [], {})
        for state in (ClientNodeState.HUNTING, ClientNodeState.IDLE,
                      ClientNodeState.RUNNING):
            for value in ("missing",) + invalid:
                with self.subTest(state=state.value, timestamp=value):
                    payload = NodeHeartbeat(
                        node_id=1, state=state.value, elapsed_ms=0,
                        current_channel=157, txpower_dbm=12, uplink_mcs=6,
                        timestamp_ms=1003,
                    ).to_dict()
                    if value == "missing":
                        del payload["timestamp_ms"]
                    else:
                        payload["timestamp_ms"] = value
                    with self.assertRaisesRegex(ValueError, "timestamp_ms"):
                        NodeHeartbeat.from_dict(payload)
                    with self.assertRaisesRegex(ValueError, "timestamp_ms"):
                        self.server.handle_datagram(json.dumps(payload).encode(), self.addr)
                    self.assertEqual(self.server.registry.get_node(1).to_dict(), before)
        self.assertEqual(
            self.heartbeat(ClientNodeState.IDLE, 1003).readiness,
            NodeReadiness.READY,
        )

    def test_positive_integer_timestamp_parses(self):
        payload = NodeHeartbeat(1, "HUNTING", 0, 157, 12, 6,
                                timestamp_ms=1).to_dict()
        self.assertEqual(NodeHeartbeat.from_dict(payload).timestamp_ms, 1)
