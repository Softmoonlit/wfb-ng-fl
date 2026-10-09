"""Transaction and final-barrier tests through control protocol datagrams.

Server tests inject uplink datagrams at the public reconfigure_radio seam.
Client tests dispatch full transactions through the public broadcast entrypoint;
test_fl_radio_switch additionally covers complete real UDP delivery.
"""
import json
import threading
import time
import unittest
import threading
from unittest.mock import Mock, patch

from wfb_ng.fl.control import ControlPlaneClient, ControlPlaneServer
from wfb_ng.fl.radio import RadioConfig
from wfb_ng.fl.errors import FLRuntimeError


class TestBroadcastCallbackLocking(unittest.TestCase):
    def test_callback_can_wait_for_control_operation_from_another_thread(self):
        client = ControlPlaneClient(1, "10.80.0.11")
        finished = threading.Event()
        workers = []
        observed = []

        def callback(message):
            def update():
                client.apply_alignment({})
                finished.set()
            worker = threading.Thread(target=update)
            workers.append(worker)
            worker.start()
            observed.append(finished.wait(0.5))

        client.register_broadcast_handler("TEST_CALLBACK", callback)
        try:
            client.handle_broadcast({"type": "TEST_CALLBACK"})
            for worker in workers:
                worker.join(1)
            self.assertEqual(observed, [True])
        finally:
            client.stop()


class TestRadioTransaction(unittest.TestCase):

    def run_switch(self, drop=None, fail=None, adapter=None, ack_transform=None):
        server = ControlPlaneServer(RadioConfig(channel=157, radio_txpower_dbm=12),
                                    network_adapter=adapter, air_interface="wlx-test")
        messages = []

        def broadcast(msg):
            kind = msg["type"]
            messages.append(kind)
            if kind == fail:
                raise OSError("injected broadcast failure")
            if kind == drop:
                return
            reply_type = {"CONFIG_RADIO_PREPARE": "PREPARE_ACK",
                          "NEW_CHANNEL_PING": "COMMIT_SUCCESS",
                          "RADIO_SWITCH_FINALIZED": "RADIO_SWITCH_FINALIZED_ACK",
                          "RADIO_SWITCH_CONFIRMED": "RADIO_SWITCH_CONFIRMED_ACK"}.get(kind)
            if reply_type:
                reply = {"type": reply_type, "node_id": 1,
                         "session_id": msg["session_id"], "current_channel": 149,
                         "channel": 149, "ok": True}
                if ack_transform:
                    reply = ack_transform(reply)
                server.handle_datagram(json.dumps(reply).encode(), ("10.80.0.11", 9001))
        server.broadcast_downlink = broadcast
        result = server.reconfigure_radio({"channel": 149, "radio_txpower_dbm": 18}, [1],
            prepare_timeout_seconds=.01, commit_delay_seconds=0,
            commit_timeout_seconds=.02, ping_interval_seconds=.005)
        return result, server, messages

    def test_complete_barriers(self):
        result, server, messages = self.run_switch()
        self.assertEqual(result.status, "finalized")
        self.assertEqual(result.effective_config, server.active_radio_config)
        self.assertIn("RADIO_SWITCH_CONFIRMED", messages)

    def test_barrier_loss(self):
        for drop, status, channel, phase in [
            ("CONFIG_RADIO_PREPARE", "rolled_back", 157, "PREPARE"),
            ("NEW_CHANNEL_PING", "rolled_back", 157, "COMMIT"),
            ("RADIO_SWITCH_FINALIZED", "rolled_back", 157, "FINALIZED"),
            ("RADIO_SWITCH_CONFIRMED", "radio_error", 149, "CONFIRMED")]:
            with self.subTest(drop=drop):
                result, server, _ = self.run_switch(drop=drop)
                self.assertEqual(result.status, status)
                self.assertEqual(result.failed_phase, phase)
                self.assertEqual(result.unresponsive_nodes, [1])
                self.assertEqual(result.effective_config.channel, channel)
                self.assertEqual(server.active_radio_config.channel, channel)

    def test_broadcast_exceptions(self):
        for stage in ["CONFIG_RADIO_PREPARE", "CONFIG_RADIO_COMMIT", "NEW_CHANNEL_PING",
                      "RADIO_SWITCH_FINALIZED", "RADIO_SWITCH_CONFIRMED"]:
            with self.subTest(stage=stage):
                result, server, messages = self.run_switch(fail=stage)
                committed = stage == "RADIO_SWITCH_CONFIRMED"
                self.assertEqual(result.status, "radio_error" if committed else "rolled_back")
                self.assertEqual(result.unresponsive_nodes, [1])
                self.assertEqual(server.active_radio_config.channel, 149 if committed else 157)
                if committed:
                    self.assertNotIn("CONFIG_RADIO_ABORT", messages)

    def test_partial_hardware_failure_restores_every_parameter(self):
        adapter = Mock()
        adapter.set_txpower.side_effect = [OSError("partial apply"), None]
        result, server, _ = self.run_switch(adapter=adapter)
        self.assertEqual(result.status, "rolled_back")
        self.assertEqual(result.unresponsive_nodes, [1])
        self.assertEqual(result.effective_config.radio_txpower_dbm, 12)
        self.assertEqual(adapter.set_channel.call_args.kwargs["channel"], 157)
        self.assertEqual(adapter.set_txpower.call_args.kwargs["txpower_dbm"], 12)

    def test_failed_hardware_rollback_is_unknown(self):
        adapter = Mock()
        adapter.set_channel.side_effect = OSError("hardware unavailable")
        result, _, _ = self.run_switch(adapter=adapter)
        self.assertEqual(result.status, "radio_error")
        self.assertIsNone(result.effective_config)

    def test_barrier_ack_requires_matching_session_node_channel_and_phase(self):
        mutations = [{"session_id": "stale"}, {"node_id": 2}, {"channel": 153},
                     {"type": "RADIO_SWITCH_CONFIRMED_ACK"}]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                def transform(reply):
                    if reply["type"] == "RADIO_SWITCH_FINALIZED_ACK":
                        return dict(reply, **mutation)
                    return reply
                result, _, _ = self.run_switch(ack_transform=transform)
                self.assertEqual(result.status, "rolled_back")
                self.assertEqual(result.failed_phase, "FINALIZED")
                self.assertEqual(result.unresponsive_nodes, [1])

    def test_radio_error_disables_alignment_even_after_transaction_cleanup(self):
        from wfb_ng.fl.control import NodeHeartbeat
        for hardware_unknown in (False, True):
            with self.subTest(hardware_unknown=hardware_unknown):
                adapter = Mock() if hardware_unknown else None
                if adapter:
                    adapter.set_channel.side_effect = OSError("hardware unknown")
                result, server, _ = self.run_switch(drop="RADIO_SWITCH_CONFIRMED", adapter=adapter)
                self.assertEqual(result.status, "radio_error")
                self.assertTrue(server.radio_error)
                hb = NodeHeartbeat(1, "IDLE", 0, 157, 15, 5, timestamp_ms=2000)
                replies = server.handle_datagram(hb.to_bytes(), ("10.80.0.11", 9001))
                self.assertEqual(len(replies), 1)

    def test_prepare_failure_from_nonbenchmark_notifies_before_fallback(self):
        server = ControlPlaneServer(RadioConfig(channel=149, radio_txpower_dbm=12))
        sent = []
        server.broadcast_downlink = sent.append
        result = server.reconfigure_radio({"channel": 153}, [1], prepare_timeout_seconds=.001)
        self.assertEqual(result.status, "rolled_back")
        self.assertEqual(result.effective_config.channel, 157)
        aborts = [msg for msg in sent if msg["type"] == "CONFIG_RADIO_ABORT"]
        self.assertEqual(aborts[0]["channel"], 149)
        self.assertEqual(aborts[-1]["channel"], 157)


    def test_failed_transaction_can_be_followed_by_successful_public_reconfigure(self):
        _, server, _ = self.run_switch(drop="RADIO_SWITCH_FINALIZED")

        def respond(msg):
            kind = {"CONFIG_RADIO_PREPARE": "PREPARE_ACK",
                    "NEW_CHANNEL_PING": "COMMIT_SUCCESS",
                    "RADIO_SWITCH_FINALIZED": "RADIO_SWITCH_FINALIZED_ACK",
                    "RADIO_SWITCH_CONFIRMED": "RADIO_SWITCH_CONFIRMED_ACK"}.get(msg["type"])
            if kind:
                server.handle_datagram(json.dumps({"type": kind, "node_id": 1,
                    "session_id": msg["session_id"], "ok": True,
                    "current_channel": 149, "channel": 149}).encode(), ("10.80.0.11", 9001))

        server.broadcast_downlink = respond
        result = server.reconfigure_radio({"channel": 149}, [1],
            prepare_timeout_seconds=.01, commit_delay_seconds=0,
            commit_timeout_seconds=.02, ping_interval_seconds=.005)
        self.assertEqual(result.status, "finalized")

    def test_radio_error_latches_and_rejects_followup_reconfigure(self):
        _, server, _ = self.run_switch(drop="RADIO_SWITCH_CONFIRMED")
        with self.assertRaises(FLRuntimeError):
            server.reconfigure_radio({"channel": 157}, [1])


class TestClientFinalBarrier(unittest.TestCase):
    def setUp(self):
        self.client = ControlPlaneClient(1, "10.80.0.11", initial_channel=149, txpower_dbm=12)
        self.sent = []
        self.online = threading.Event()
        self.finalized = Mock()
        self.client.on_radio_finalized = self.finalized

        def record(data):
            message = json.loads(data)
            self.sent.append(message)
            if message["type"] == "COMMIT_SUCCESS":
                self.online.set()

        # Inject only the uplink transport; the full downlink protocol is public.
        self.client._send_unicast_datagram = record
        self.msg = {"session_id": "session", "channel": 149, "target_nodes": [1]}
        self.client.handle_broadcast({"type": "CONFIG_RADIO_PREPARE",
            "session_id": "session", "target_nodes": [1],
            "patch": {"channel": 149, "radio_txpower_dbm": 18}})
        self.client.handle_broadcast({"type": "CONFIG_RADIO_COMMIT",
            "session_id": "session", "target_nodes": [1],
            "patch": {"channel": 149, "radio_txpower_dbm": 18},
            "delay_ms": 0, "lease_timeout_seconds": .5})
        deadline = time.monotonic() + 1
        while not self.online.is_set() and time.monotonic() < deadline:
            self.dispatch("NEW_CHANNEL_PING")
            self.online.wait(.001)
        self.assertTrue(self.online.is_set())
        self.sent.clear()

    def dispatch(self, kind, **fields):
        self.client.handle_broadcast(dict(self.msg, type=kind, **fields))

    def tearDown(self):
        self.client.stop()

    def test_finalized_callback_can_wait_for_another_control_thread(self):
        finished = threading.Event()
        observed = []
        workers = []

        def callback(config):
            def update():
                self.client.apply_alignment({})
                finished.set()
            worker = threading.Thread(target=update)
            workers.append(worker)
            worker.start()
            observed.append(finished.wait(0.5))

        self.client.on_radio_finalized = callback
        self.dispatch("RADIO_SWITCH_FINALIZED")
        self.dispatch("RADIO_SWITCH_CONFIRMED")
        for worker in workers:
            worker.join(1)
        self.assertEqual(observed, [True])

    def test_proposal_preserves_lease_confirmation_can_be_reacked(self):

        self.dispatch("RADIO_SWITCH_FINALIZED")
        self.assertTrue(self.client.lease_watchdog.is_armed)
        self.assertEqual(self.sent[-1]["type"], "RADIO_SWITCH_FINALIZED_ACK")
        self.dispatch("RADIO_SWITCH_CONFIRMED")
        self.assertFalse(self.client.lease_watchdog.is_armed)
        self.assertEqual(self.sent[-1]["type"], "RADIO_SWITCH_CONFIRMED_ACK")
        self.dispatch("RADIO_SWITCH_CONFIRMED")
        self.assertEqual(len(self.sent), 3)
        self.finalized.assert_called_once()

    def test_confirmation_requires_proposal_and_matching_session(self):
        self.dispatch("RADIO_SWITCH_CONFIRMED")
        self.assertTrue(self.client.lease_watchdog.is_armed)
        self.dispatch("RADIO_SWITCH_FINALIZED", session_id="stale")
        self.assertFalse(self.sent)

    def test_no_transaction_rejects_unsolicited_messages(self):
        client = ControlPlaneClient(2, "10.80.0.12", initial_channel=149)
        send = Mock()
        client._send_unicast_datagram = send
        try:
            for kind in ("RADIO_SWITCH_FINALIZED", "RADIO_SWITCH_CONFIRMED",
                         "NEW_CHANNEL_PING", "CONFIG_RADIO_ABORT"):
                client.handle_broadcast(dict(self.msg, type=kind, target_nodes=[2]))
            self.assertEqual(client.current_channel, 149)
            self.assertFalse(client.lease_watchdog.is_armed)
            send.assert_not_called()
        finally:
            client.stop()

    def test_confirmation_after_deadline_cannot_win_before_callback(self):
        self.dispatch("RADIO_SWITCH_FINALIZED")
        # Advance the public clock while the watchdog worker is asleep; a
        # confirmation must still reject an elapsed lease before callback delivery.
        with patch("wfb_ng.fl.control.time.monotonic", return_value=time.monotonic() + 10):
            self.dispatch("RADIO_SWITCH_CONFIRMED")
        self.assertIsNone(self.client.locked_channel)
        self.assertNotIn("RADIO_SWITCH_CONFIRMED_ACK", [m["type"] for m in self.sent])

    def test_transaction_suspends_hunting_and_alignment(self):
        self.client.apply_alignment({"radio_txpower_dbm": 12})
        self.assertEqual(self.client.txpower_dbm, 18)
        self.assertFalse(self.client.hunt_once([157]))
        self.assertEqual(self.client.current_channel, 149)

    def test_prepare_abort_from_nonbenchmark_returns_to_benchmark(self):
        client = ControlPlaneClient(2, "10.80.0.12", initial_channel=149)
        client._send_unicast_datagram = Mock()
        try:
            client.handle_broadcast({"type": "CONFIG_RADIO_PREPARE",
                "session_id": "prepare-only", "target_nodes": [2], "patch": {"channel": 153}})
            client.handle_broadcast({"type": "CONFIG_RADIO_ABORT",
                "session_id": "prepare-only", "target_nodes": [2], "channel": 149})
            self.assertEqual(client.current_channel, 157)
            self.assertFalse(client.lease_watchdog.is_armed)
        finally:
            client.stop()


class TestHeartbeatIsolation(unittest.TestCase):
    def test_server_does_not_align_target_during_session(self):
        from dataclasses import replace
        from wfb_ng.fl.control import NodeHeartbeat
        server = ControlPlaneServer(RadioConfig(channel=157, radio_txpower_dbm=12))
        hb = NodeHeartbeat(1, "IDLE", 0, 149, 15, 5, timestamp_ms=2000)
        observed = []

        def heartbeat():
            nonlocal hb
            hb = replace(hb, timestamp_ms=hb.timestamp_ms + 1)
            return server.handle_datagram(hb.to_bytes(), ("10.80.0.11", 9001))

        def broadcast(msg):
            observed.append(heartbeat())
            kind = {"CONFIG_RADIO_PREPARE": "PREPARE_ACK",
                    "NEW_CHANNEL_PING": "COMMIT_SUCCESS",
                    "RADIO_SWITCH_FINALIZED": "RADIO_SWITCH_FINALIZED_ACK",
                    "RADIO_SWITCH_CONFIRMED": "RADIO_SWITCH_CONFIRMED_ACK"}.get(msg["type"])
            if kind:
                server.handle_datagram(json.dumps({"type": kind, "node_id": 1,
                    "session_id": msg["session_id"], "ok": True,
                    "current_channel": 149, "channel": 149}).encode(), ("10.80.0.11", 9001))

        server.broadcast_downlink = broadcast
        result = server.reconfigure_radio({"channel": 149, "radio_txpower_dbm": 18}, [1],
            prepare_timeout_seconds=.01, commit_delay_seconds=0, commit_timeout_seconds=.02)
        self.assertEqual(result.status, "finalized")
        self.assertTrue(all(len(replies) == 1 for replies in observed))
        replies = heartbeat()
        self.assertEqual(json.loads(replies[-1])["type"], "CONFIG_RADIO_ALIGN")
