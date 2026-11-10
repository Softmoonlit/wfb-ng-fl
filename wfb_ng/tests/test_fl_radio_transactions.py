"""Transaction and final-barrier tests through control protocol datagrams.

Server tests inject uplink datagrams at the public reconfigure_radio seam.
Client tests dispatch full transactions through the public broadcast entrypoint;
test_fl_radio_switch additionally covers complete real UDP delivery.
"""
from dataclasses import replace
import json
import threading
import time
import unittest
import threading
from unittest.mock import Mock, patch

from wfb_ng.fl.control import ClientNodeState, ControlPlaneClient, ControlPlaneServer
from wfb_ng.fl.radio import RadioConfig
from wfb_ng.fl.errors import FLRuntimeError


def _session_heartbeat(server, channel):
    """先通过真实心跳协议建立会话，再验证射频对齐策略。"""
    from wfb_ng.fl.control import NodeHeartbeat, HeartbeatAck
    addr = ("10.80.0.11", 9001)
    request = NodeHeartbeat(1, "HUNTING", 0, server.active_radio_config.channel,
                            15, 5, timestamp_ms=1999)
    challenge = HeartbeatAck.from_bytes(server.handle_datagram(request.to_bytes(), addr)[0])
    assert not challenge.confirmed
    request = replace(request, session=challenge.session)
    ack = HeartbeatAck.from_bytes(server.handle_datagram(request.to_bytes(), addr)[0])
    assert ack.confirmed
    return replace(request, state="IDLE", current_channel=channel, timestamp_ms=2000)


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

    def run_switch(self, drop=None, fail=None, adapter=None, ack_transform=None, fec_n=14):
        server = ControlPlaneServer(RadioConfig(channel=157, radio_txpower_dbm=12, fec_n=fec_n),
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
                          "RADIO_SWITCH_CONFIRMED": "RADIO_SWITCH_CONFIRMED_ACK",
                          "CONFIG_RADIO_ABORT": "RADIO_SWITCH_ROLLBACK_ACK"}.get(kind)
            if reply_type:
                reply = {"type": reply_type, "node_id": 1,
                         "session_id": msg["session_id"], "current_channel": 149,
                         "channel": msg.get("channel", 149), "ok": True,
                         "config": msg["config"], "calibration": msg["calibration"]}
                if ack_transform:
                    reply = ack_transform(reply)
                server.handle_datagram(json.dumps(reply).encode(), ("10.80.0.11", 9001))
        server.broadcast_downlink = broadcast
        result = server.reconfigure_radio({"channel": 149, "radio_txpower_dbm": 18}, [1],
            prepare_timeout_seconds=.01, commit_delay_seconds=0,
            commit_timeout_seconds=.02, lease_timeout_seconds=.03, ping_interval_seconds=.005)
        return result, server, messages

    def test_fec12_survives_transaction_and_rejects_online_change(self):
        result, server, messages = self.run_switch(fec_n=12)
        self.assertTrue(result.success)
        self.assertEqual(result.effective_config.fec_n, 12)
        with self.assertRaises(ValueError):
            server.reconfigure_radio({"fec_n": 14}, [1])
        self.assertEqual(server.active_radio_config.fec_n, 12)
        client = ControlPlaneClient(1, "10.80.0.11", initial_radio_config=RadioConfig(fec_n=12))
        client.apply_alignment({"uplink_mcs": 5})
        self.assertEqual(client._current_radio_config().fec_n, 12)

    def test_rollback_ack_requires_full_config_and_mode(self):
        for mutation in ({'config': {}}, {'calibration': True}, {'session_id': 'stale'},
                         {'node_id': 2}, {'channel': 149}):
            with self.subTest(mutation=mutation):
                def transform(reply):
                    return dict(reply, **mutation) if reply['type'] == 'RADIO_SWITCH_ROLLBACK_ACK' else reply
                result, server, _ = self.run_switch(drop='RADIO_SWITCH_FINALIZED', ack_transform=transform)
                self.assertEqual(result.status, 'radio_error')
                self.assertIsNone(result.effective_config)
                self.assertEqual(result.unresponsive_nodes, [1])
                self.assertTrue(server.radio_error)

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
                if status == "radio_error":
                    self.assertIsNone(result.effective_config)
                else:
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
        mutations = [{"session_id": "stale"}, {"job_id": "stale"}, {"node_id": 2}, {"channel": 153},
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

    def test_confirmation_rejects_matching_channel_with_wrong_complete_config(self):
        for kind in ("COMMIT_SUCCESS", "RADIO_SWITCH_FINALIZED_ACK", "RADIO_SWITCH_CONFIRMED_ACK"):
            with self.subTest(kind=kind):
                def transform(reply):
                    if reply["type"] == kind:
                        reply["config"] = dict(reply["config"], bandwidth_mhz=20)
                    return reply
                result, _, _ = self.run_switch(ack_transform=transform)
                self.assertFalse(result.success)
                self.assertEqual(result.status, "radio_error" if kind.endswith("CONFIRMED_ACK")
                                 else "rolled_back")

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
                hb = _session_heartbeat(server, 157)
                replies = server.handle_datagram(hb.to_bytes(), ("10.80.0.11", 9001))
                self.assertEqual(len(replies), 1)

    def test_prepare_failure_restores_nonbenchmark_previous_channel(self):
        server = ControlPlaneServer(RadioConfig(channel=149, radio_txpower_dbm=12))
        sent = []
        server.broadcast_downlink = sent.append
        result = server.reconfigure_radio({"channel": 153}, [1], prepare_timeout_seconds=.001,
                                          commit_timeout_seconds=.01, ping_interval_seconds=.002)
        self.assertEqual(result.status, "rolled_back")
        self.assertEqual(result.effective_config.channel, 149)
        aborts = [msg for msg in sent if msg["type"] == "CONFIG_RADIO_ABORT"]
        self.assertTrue(aborts)
        self.assertEqual(aborts[0]["channel"], 149)
        self.assertEqual(aborts[-1]["channel"], 149)


    def test_failed_transaction_can_be_followed_by_successful_public_reconfigure(self):
        _, server, _ = self.run_switch(drop="RADIO_SWITCH_FINALIZED")

        def respond(msg):
            kind = {"CONFIG_RADIO_PREPARE": "PREPARE_ACK",
                    "NEW_CHANNEL_PING": "COMMIT_SUCCESS",
                    "RADIO_SWITCH_FINALIZED": "RADIO_SWITCH_FINALIZED_ACK",
                    "RADIO_SWITCH_CONFIRMED": "RADIO_SWITCH_CONFIRMED_ACK",
                          "CONFIG_RADIO_ABORT": "RADIO_SWITCH_ROLLBACK_ACK"}.get(msg["type"])
            if kind:
                server.handle_datagram(json.dumps({"type": kind, "node_id": 1,
                    "session_id": msg["session_id"], "ok": True,
                    "current_channel": 149, "channel": 149,
                    "config": msg["config"], "calibration": msg["calibration"]}).encode(), ("10.80.0.11", 9001))

        server.broadcast_downlink = respond
        result = server.reconfigure_radio({"channel": 149}, [1],
            prepare_timeout_seconds=.01, commit_delay_seconds=0,
            commit_timeout_seconds=.02, lease_timeout_seconds=.03, ping_interval_seconds=.005)
        self.assertEqual(result.status, "finalized")

    def test_radio_error_latches_and_rejects_followup_reconfigure(self):
        _, server, _ = self.run_switch(drop="RADIO_SWITCH_CONFIRMED")
        with self.assertRaises(FLRuntimeError):
            server.reconfigure_radio({"channel": 157}, [1])
    def test_job_bound_prepare_checks_client_identity_before_ack(self):
        client = ControlPlaneClient(1, "10.80.0.11", initial_channel=157)
        sent = []
        observed = []
        client._send_unicast_datagram = lambda data: sent.append(json.loads(data))
        client.on_radio_prepare = lambda job_id, config: observed.append(
            (job_id, config, client.radio_switch_pending)
        )
        try:
            client.bind_job_context("job-a")
            message = {
                "type": "CONFIG_RADIO_PREPARE",
                "session_id": "session-a",
                "job_id": "job-a",
                "target_nodes": [1],
                "patch": {"uplink_mcs": 5},
                "config": RadioConfig(channel=157, uplink_mcs=5).to_dict(),
                "calibration": False,
            }
            client.handle_broadcast(message)
            self.assertEqual(observed[0][0], "job-a")
            self.assertFalse(observed[0][2], "prepare callback must precede local pending state")
            self.assertEqual(sent[-1]["type"], "PREPARE_ACK")
            self.assertEqual(sent[-1]["job_id"], "job-a")
            sent_before = len(sent)
            client.handle_broadcast(message)
            self.assertEqual(len(sent), sent_before + 1)
            self.assertEqual(observed[0][0], "job-a")
            self.assertEqual(len(observed), 1, "idempotent PREPARE must not re-run owner callback")

            sent_before = len(sent)
            client.handle_broadcast(dict(message, target_nodes=[1, 2]))
            self.assertEqual(len(sent), sent_before)
            client.handle_broadcast(dict(
                message, type="CONFIG_RADIO_COMMIT", target_nodes=[1, 2],
                delay_ms=0, lease_timeout_seconds=.5,
            ))
            self.assertEqual(len(sent), sent_before)
            self.assertTrue(client.radio_switch_pending)

            sent_before = len(sent)
            client.handle_broadcast(dict(message, session_id="session-b", job_id="job-b"))
            self.assertEqual(len(sent), sent_before)
        finally:
            client.handle_broadcast({
                "type": "CONFIG_RADIO_ABORT", "session_id": "session-a", "job_id": "job-a",
                "target_nodes": [1], "channel": 157,
                "config": client._current_radio_config().to_dict(), "calibration": False,
            })
            client.clear_job_context("job-a")
            client.stop()

    def test_missing_target_nodes_rejects_every_radio_phase_without_ack_or_mutation(self):
        client = ControlPlaneClient(1, "10.80.0.11", initial_channel=157)
        sent = []
        client._send_unicast_datagram = lambda data: sent.append(json.loads(data))
        base = {
            "session_id": "strict-targets",
            "job_id": None,
            "patch": {"uplink_mcs": 5},
            "channel": 157,
            "config": RadioConfig(channel=157, uplink_mcs=5).to_dict(),
            "calibration": False,
        }
        try:
            client.handle_broadcast(dict(base, type="CONFIG_RADIO_PREPARE"))
            self.assertEqual(sent, [])
            client.handle_broadcast(dict(base, type="CONFIG_RADIO_PREPARE", target_nodes=[1]))
            self.assertEqual(sent[-1]["type"], "PREPARE_ACK")
            before = (client.current_channel, client.state, len(sent))
            for kind in (
                "CONFIG_RADIO_COMMIT", "NEW_CHANNEL_PING",
                "RADIO_SWITCH_FINALIZED", "RADIO_SWITCH_CONFIRMED", "CONFIG_RADIO_ABORT",
            ):
                client.handle_broadcast(dict(base, type=kind))
            self.assertEqual(
                (client.current_channel, client.state, len(sent)), before
            )
            self.assertTrue(client.radio_switch_pending)
        finally:
            client.stop()

    def test_unbound_idle_client_accepts_job_bound_prepare(self):
        client = ControlPlaneClient(1, "10.80.0.11", initial_channel=157)
        sent = []
        client._send_unicast_datagram = lambda data: sent.append(json.loads(data))
        try:
            client.handle_broadcast({
                "type": "CONFIG_RADIO_PREPARE", "session_id": "session-idle", "job_id": "job-a",
                "target_nodes": [1], "patch": {"uplink_mcs": 5},
                "config": RadioConfig(channel=157, uplink_mcs=5).to_dict(), "calibration": False,
            })
            self.assertEqual(sent[-1]["job_id"], "job-a")
        finally:
            client.stop()

    def test_job_bound_confirmed_and_rollback_preserve_task_state_and_channel_owner(self):
        client = ControlPlaneClient(1, "10.80.0.11", initial_channel=157)
        # 本组只验证事务和所有权；真实心跳往返由动态loopback专项覆盖。
        client._send_heartbeat = Mock(return_value=object())
        sent = []
        commit_seen = threading.Event()
        client._send_unicast_datagram = lambda data: (
            sent.append(json.loads(data)),
            commit_seen.set() if json.loads(data).get("type") == "COMMIT_SUCCESS" else None,
        )
        client.bind_job_context("job-task")
        client.state = ClientNodeState.RUNNING
        client.locked_channel = 157
        base = {
            "job_id": "job-task", "target_nodes": [1],
            "config": RadioConfig(channel=157, uplink_mcs=5).to_dict(),
            "calibration": False,
        }
        try:
            client.handle_broadcast(dict(base, type="CONFIG_RADIO_PREPARE",
                                         session_id="job-session", patch={"uplink_mcs": 5}))
            client.handle_broadcast(dict(base, type="CONFIG_RADIO_COMMIT",
                                         session_id="job-session", patch={"uplink_mcs": 5},
                                         delay_ms=0, lease_timeout_seconds=.5))
            client.handle_broadcast(dict(base, type="NEW_CHANNEL_PING",
                                         session_id="job-session", channel=157))
            self.assertTrue(commit_seen.wait(1))
            client.handle_broadcast(dict(base, type="RADIO_SWITCH_FINALIZED",
                                         session_id="job-session", channel=157, patch={"uplink_mcs": 5}))
            client.handle_broadcast(dict(base, type="RADIO_SWITCH_CONFIRMED",
                                         session_id="job-session", channel=157, patch={"uplink_mcs": 5}))
            self.assertEqual(client.state, ClientNodeState.RUNNING)
            self.assertEqual(client.locked_channel, 157)
        finally:
            client.stop()

        rollback_client = ControlPlaneClient(1, "10.80.0.11", initial_channel=153)
        rollback_client.bind_job_context("job-task")
        rollback_client.state = ClientNodeState.RUNNING
        rollback_client.locked_channel = 153
        rollback_client._send_unicast_datagram = lambda data: None
        try:
            rollback_client.handle_broadcast({
                "type": "CONFIG_RADIO_PREPARE", "session_id": "rollback-session",
                "job_id": "job-task", "target_nodes": [1],
                "patch": {"uplink_mcs": 5},
            })
            rollback_client.handle_broadcast({
                "type": "CONFIG_RADIO_ABORT", "session_id": "rollback-session",
                "job_id": "job-task", "target_nodes": [1], "channel": 153,
                "config": rollback_client._current_radio_config().to_dict(),
                "calibration": False,
            })
            self.assertEqual(rollback_client.state, ClientNodeState.RUNNING)
            self.assertEqual(rollback_client.locked_channel, 153)
        finally:
            rollback_client.stop()

    def test_job_transaction_starts_and_ends_liveness_lease(self):
        server = ControlPlaneServer(RadioConfig(channel=157, radio_txpower_dbm=12))
        server.watch_job_liveness("job-lease", [1], lambda event: None)
        begin_calls = []
        end_calls = []
        original_begin = server.begin_job_liveness_lease
        original_end = server.end_job_liveness_lease
        server.begin_job_liveness_lease = lambda job, transaction, timeout: (
            begin_calls.append((job, transaction, timeout)),
            original_begin(job, transaction, timeout),
        )[1]
        server.end_job_liveness_lease = lambda job, transaction: (
            end_calls.append((job, transaction)),
            original_end(job, transaction),
        )[1]

        def broadcast(message):
            replies = {
                "CONFIG_RADIO_PREPARE": "PREPARE_ACK",
                "NEW_CHANNEL_PING": "COMMIT_SUCCESS",
                "RADIO_SWITCH_FINALIZED": "RADIO_SWITCH_FINALIZED_ACK",
                "RADIO_SWITCH_CONFIRMED": "RADIO_SWITCH_CONFIRMED_ACK",
            }
            reply_type = replies.get(message["type"])
            if reply_type is None:
                return
            payload = {
                "type": reply_type, "node_id": 1,
                "session_id": message["session_id"], "job_id": message["job_id"],
                "current_channel": message.get("channel", 149),
                "channel": message.get("channel", 149), "ok": True,
                "config": message["config"], "calibration": message["calibration"],
            }
            server.handle_datagram(json.dumps(payload).encode(), ("127.0.0.1", 12003))

        server.broadcast_downlink = broadcast
        try:
            result = server.reconfigure_radio(
                {"channel": 149}, [1], commit_delay_seconds=0,
                commit_timeout_seconds=.05, ping_interval_seconds=.005,
                job_id="job-lease",
            )
            self.assertEqual(result.status, "finalized")
            self.assertEqual(begin_calls[0][0], "job-lease")
            self.assertEqual(end_calls, [("job-lease", begin_calls[0][1])])
        finally:
            server.unwatch_job_liveness("job-lease")

    def test_stop_before_commit_restores_full_previous_config(self):
        server = ControlPlaneServer(RadioConfig(
            channel=149, radio_txpower_dbm=15, uplink_mcs=5,
        ))
        stop_event = threading.Event()
        sent = []

        def broadcast(message):
            sent.append(message)
            kind = message["type"]
            if kind == "CONFIG_RADIO_PREPARE":
                server.handle_datagram(json.dumps({
                    "type": "PREPARE_ACK", "node_id": 1,
                    "session_id": message["session_id"], "job_id": message["job_id"], "ok": True,
                }).encode(), ("127.0.0.1", 12001))
            elif kind == "CONFIG_RADIO_COMMIT":
                stop_event.set()
            elif kind == "CONFIG_RADIO_ABORT":
                server.handle_datagram(json.dumps({
                    "type": "RADIO_SWITCH_ROLLBACK_ACK", "node_id": 1,
                    "session_id": message["session_id"], "job_id": message["job_id"],
                    "channel": message["channel"], "config": message["config"],
                    "calibration": message["calibration"],
                }).encode(), ("127.0.0.1", 12001))

        server.broadcast_downlink = broadcast
        result = server.reconfigure_radio(
            {"channel": 153, "radio_txpower_dbm": 18, "uplink_mcs": 6}, [1],
            commit_delay_seconds=0, commit_timeout_seconds=.05,
            ping_interval_seconds=.005, job_id="job-stop", stop_event=stop_event,
        )
        self.assertEqual(result.status, "rolled_back")
        self.assertEqual(result.effective_config.channel, 149)
        self.assertEqual(result.effective_config.radio_txpower_dbm, 15)
        self.assertEqual(result.effective_config.uplink_mcs, 5)
        self.assertTrue(any(
            message["type"] == "CONFIG_RADIO_ABORT" and message["channel"] == 149
            for message in sent
        ))
        self.assertTrue(all(message.get("job_id") == "job-stop" for message in sent))

    def test_stop_after_confirmed_commit_fails_closed_with_unknown_effective_config(self):
        server = ControlPlaneServer(RadioConfig(channel=149, radio_txpower_dbm=12))
        stop_event = threading.Event()

        def broadcast(message):
            kind = message["type"]
            reply_type = {
                "CONFIG_RADIO_PREPARE": "PREPARE_ACK",
                "NEW_CHANNEL_PING": "COMMIT_SUCCESS",
                "RADIO_SWITCH_FINALIZED": "RADIO_SWITCH_FINALIZED_ACK",
            }.get(kind)
            if reply_type is None:
                if kind == "RADIO_SWITCH_CONFIRMED":
                    stop_event.set()
                return
            payload = {
                "type": reply_type, "node_id": 1,
                "session_id": message["session_id"], "job_id": message["job_id"],
                "current_channel": message.get("channel", 153),
                "channel": message.get("channel", 153), "ok": True,
                "config": message["config"], "calibration": message["calibration"],
            }
            server.handle_datagram(json.dumps(payload).encode(), ("127.0.0.1", 12002))

        server.broadcast_downlink = broadcast
        result = server.reconfigure_radio(
            {"channel": 153}, [1], commit_delay_seconds=0,
            commit_timeout_seconds=.05, ping_interval_seconds=.005,
            job_id="job-stop", stop_event=stop_event,
        )
        self.assertEqual(result.status, "radio_error")
        self.assertIsNone(result.effective_config)
        self.assertTrue(server.radio_error)
        self.assertEqual(result.unconfirmed_nodes, [1])
        self.assertEqual(server.active_radio_config.channel, 153)

    def setUp(self):
        self.client = ControlPlaneClient(1, "10.80.0.11", initial_channel=149, txpower_dbm=12)
        # 事务收据测试隔离确认后的心跳，禁止触碰实机TUN和生产UDP端口。
        self.client._send_heartbeat = Mock(return_value=object())
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
        self.msg = {"session_id": "session", "channel": 149, "target_nodes": [1],
                    "config": RadioConfig(channel=149, radio_txpower_dbm=18).to_dict(),
                    "calibration": False}
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

    def test_confirmation_with_wrong_complete_config_keeps_lease(self):
        self.dispatch("RADIO_SWITCH_FINALIZED")
        self.sent.clear()
        self.dispatch("RADIO_SWITCH_CONFIRMED",
                      config=RadioConfig(channel=149, radio_txpower_dbm=18, bandwidth_mhz=20).to_dict())
        self.assertTrue(self.client.lease_watchdog.is_armed)
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

    def test_prepare_abort_from_nonbenchmark_returns_to_previous_channel(self):
        client = ControlPlaneClient(2, "10.80.0.12", initial_channel=149)
        client._send_unicast_datagram = Mock()
        try:
            client.handle_broadcast({"type": "CONFIG_RADIO_PREPARE",
                "session_id": "prepare-only", "target_nodes": [2], "patch": {"channel": 153}})
            client.handle_broadcast({"type": "CONFIG_RADIO_ABORT",
                "session_id": "prepare-only", "target_nodes": [2], "channel": 149})
            self.assertEqual(client.current_channel, 149)
            self.assertFalse(client.lease_watchdog.is_armed)
        finally:
            client.stop()


class TestHeartbeatIsolation(unittest.TestCase):
    def test_server_does_not_align_target_during_session(self):
        from dataclasses import replace
        from wfb_ng.fl.control import NodeHeartbeat
        server = ControlPlaneServer(RadioConfig(channel=157, radio_txpower_dbm=12))
        hb = _session_heartbeat(server, 149)
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
                    "RADIO_SWITCH_CONFIRMED": "RADIO_SWITCH_CONFIRMED_ACK",
                          "CONFIG_RADIO_ABORT": "RADIO_SWITCH_ROLLBACK_ACK"}.get(msg["type"])
            if kind:
                server.handle_datagram(json.dumps({"type": kind, "node_id": 1,
                    "session_id": msg["session_id"], "ok": True,
                    "current_channel": 149, "channel": 149,
                    "config": msg["config"], "calibration": msg["calibration"]}).encode(), ("10.80.0.11", 9001))

        server.broadcast_downlink = broadcast
        result = server.reconfigure_radio({"channel": 149, "radio_txpower_dbm": 18}, [1],
            prepare_timeout_seconds=.01, commit_delay_seconds=0, commit_timeout_seconds=.02)
        self.assertEqual(result.status, "finalized")
        self.assertTrue(all(len(replies) == 1 for replies in observed))
        replies = heartbeat()
        self.assertEqual(json.loads(replies[-1])["type"], "CONFIG_RADIO_ALIGN")
