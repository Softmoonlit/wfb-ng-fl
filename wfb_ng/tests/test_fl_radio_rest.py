"""人工确认射频重配的 REST 准入与状态互斥。"""

import json
import threading
import unittest
import urllib.error
import urllib.request

from unittest.mock import patch

from wfb_ng.fl.errors import FLRuntimeError
from wfb_ng.fl.control import ClientNodeState, NodeHeartbeat
from wfb_ng.fl.server_daemon import ServerDaemon, ServerDaemonConfig, ServerState
from wfb_ng.tests.test_fl_client_daemon import MockNetworkAdapter


class TestRadioReconfigureREST(unittest.TestCase):
    def setUp(self):
        self.daemon = ServerDaemon(
            config=ServerDaemonConfig(
                ipc_port=0, enable_link_process=False, enable_control_plane=False,
                air_interface="wlx-test",
            ),
            network_adapter=MockNetworkAdapter(["wlx-test"]),
        )
        self.daemon.start()

    def tearDown(self):
        self.daemon.stop()

    def request(self, payload, path="/api/v1/radio/reconfigure", method="POST"):
        data = json.dumps(payload).encode() if method == "POST" else None
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.daemon.actual_ipc_port}{path}",
            data=data, method=method, headers={"Content-Type": "application/json"},
        )
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.code, json.loads(response.read())

    def ready_node(self, node_id=1):
        for state in (ClientNodeState.HUNTING, ClientNodeState.IDLE):
            self.daemon.control_plane.handle_datagram(
                NodeHeartbeat(node_id, state.value, 0, 157, 12, 6).to_bytes(),
                ("127.0.0.1", 10000 + node_id),
            )

    def test_unready_nodes_are_conflicts(self):
        payload = {"confirmed": True, "patch": {"channel": 149}, "target_nodes": [1, 2]}
        self.ready_node()
        code, result = self.request(payload)
        self.assertEqual(code, 409)
        self.assertEqual(result["unready_nodes"], [2])

    def protocol_peer(self, message):
        """在 UDP 边界模拟目标节点的协议应答。"""
        kinds = {
            "CONFIG_RADIO_PREPARE": "PREPARE_ACK",
            "NEW_CHANNEL_PING": "COMMIT_SUCCESS",
            "RADIO_SWITCH_FINALIZED": "RADIO_SWITCH_FINALIZED_ACK",
            "RADIO_SWITCH_CONFIRMED": "RADIO_SWITCH_CONFIRMED_ACK",
        }
        kind = kinds.get(message["type"])
        if kind:
            for nid in message["target_nodes"]:
                self.daemon.control_plane.handle_datagram(json.dumps({
                    "type": kind, "node_id": nid, "session_id": message["session_id"],
                    "current_channel": message.get("channel", 149),
                    "channel": message.get("channel", 149), "ok": True,
                }).encode(), ("127.0.0.1", 10000 + nid))

    def test_finalized_result_and_status_report_effective_config(self):
        self.ready_node()
        payload = {"confirmed": True, "patch": {"channel": 149}, "target_nodes": [1]}
        with patch.object(self.daemon.control_plane, "broadcast_downlink", side_effect=self.protocol_peer):
            code, result = self.request(payload)
        self.assertEqual(code, 200, result)
        self.assertEqual(result["status"], "finalized")
        self.assertTrue(result["session_id"])
        self.assertIsNone(result["failed_phase"])
        self.assertEqual(result["unresponsive_nodes"], [])
        _, report = self.request(None, "/api/v1/status", "GET")
        self.assertEqual(report["server_state"], "IDLE")
        self.assertEqual(report["radio"]["channel"], 149)
        self.assertEqual(report["radio"], result["effective_config"])

    def test_switching_rejects_jobs_survey_and_second_reconfigure(self):
        self.ready_node()
        entered = threading.Event()
        release = threading.Event()
        responses = []

        def peer(message):
            if message["type"] == "CONFIG_RADIO_PREPARE":
                entered.set()
                if not release.wait(4):
                    raise RuntimeError("测试未释放协议")
            self.protocol_peer(message)

        payload = {"confirmed": True, "patch": {"channel": 149}, "target_nodes": [1]}
        with patch.object(self.daemon.control_plane, "broadcast_downlink", side_effect=peer):
            worker = threading.Thread(target=lambda: responses.append(self.request(payload)))
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                _, report = self.request(None, "/api/v1/status", "GET")
                self.assertEqual(report["server_state"], "SWITCHING_RADIO")
                self.assertEqual(self.request(payload)[0], 409)
                self.assertEqual(self.request({}, "/api/v1/survey")[0], 409)
                job = {
                    "job_id": "conflict-job", "run_id": "conflict-run", "target_nodes": [1],
                    "model_path": "/unused/model.bin", "model_size_bytes": 1,
                    "io_timeout_seconds": 120, "live_observation": True,
                }
                self.assertEqual(self.request(job, "/api/v1/jobs/start")[0], 409)
                with self.assertRaises(FLRuntimeError) as raised:
                    self.daemon.run_spectrum_survey()
                self.assertEqual(raised.exception.error_code, "engine_busy")
            finally:
                release.set()
                worker.join(4)
        self.assertFalse(worker.is_alive())
        self.assertEqual(responses[0][1]["status"], "finalized")

    def test_unexpected_protocol_exception_fails_closed(self):
        self.ready_node()
        payload = {"confirmed": True, "patch": {"channel": 149}, "target_nodes": [1]}
        with patch.object(self.daemon.control_plane, "reconfigure_radio", side_effect=RuntimeError("unexpected")):
            self.assertEqual(self.request(payload)[0], 500)
        _, report = self.request(None, "/api/v1/status", "GET")
        self.assertEqual(report["server_state"], "RADIO_ERROR")
        replies = self.daemon.control_plane.handle_datagram(
            NodeHeartbeat(1, "IDLE", 0, 149, 18, 5).to_bytes(), ("127.0.0.1", 10001),
        )
        self.assertEqual(len(replies), 1)
        self.assertEqual(self.request(payload)[0], 409)
        self.assertEqual(self.request({}, "/api/v1/survey")[0], 409)
        with self.assertRaises(FLRuntimeError):
            self.daemon.run_spectrum_survey()

    def test_protocol_failures_sync_operation_result_and_status(self):
        self.ready_node()
        payload = {"confirmed": True, "patch": {"channel": 149}, "target_nodes": [1]}
        for failed_kind, expected_status, expected_phase, expected_channel in (
            ("CONFIG_RADIO_PREPARE", "rolled_back", "PREPARE", 157),
            ("RADIO_SWITCH_FINALIZED", "rolled_back", "FINALIZED", 157),
            ("RADIO_SWITCH_CONFIRMED", "radio_error", "CONFIRMED", 149),
        ):
            with self.subTest(phase=expected_phase):
                def peer(message):
                    if message["type"] == failed_kind:
                        raise OSError("模拟广播故障")
                    self.protocol_peer(message)
                with patch.object(self.daemon.control_plane, "broadcast_downlink", side_effect=peer):
                    code, result = self.request(payload)
                self.assertEqual(code, 200)
                self.assertEqual(result["status"], expected_status)
                self.assertEqual(result["failed_phase"], expected_phase)
                _, report = self.request(None, "/api/v1/status", "GET")
                self.assertEqual(report["radio"]["channel"], expected_channel)
                self.assertEqual(report["radio"], result["effective_config"])
                self.assertEqual(report["server_state"], "RADIO_ERROR" if expected_status == "radio_error" else "IDLE")

    def test_unconfirmed_hardware_rollback_reports_unknown_radio(self):
        self.ready_node()
        payload = {"confirmed": True, "patch": {"channel": 149}, "target_nodes": [1]}
        with patch.object(self.daemon.control_plane, "broadcast_downlink", side_effect=OSError("广播故障")), patch.object(
            self.daemon.adapter, "set_channel", side_effect=OSError("硬件故障")
        ):
            code, result = self.request(payload)
        self.assertEqual(code, 200)
        self.assertEqual(result["status"], "radio_error")
        self.assertIsNone(result["effective_config"])
        _, report = self.request(None, "/api/v1/status", "GET")
        self.assertEqual(report["server_state"], "RADIO_ERROR")
        self.assertIsNone(report["radio"])

    def test_survey_reservation_blocks_reconfigure_and_recovers_on_failure(self):
        self.ready_node()
        entered = threading.Event()
        release = threading.Event()
        responses = []

        def survey(**kwargs):
            entered.set()
            release.wait(2)
            raise OSError("模拟扫频故障")

        with patch("wfb_ng.fl.server_daemon.survey_spectrum", side_effect=survey):
            worker = threading.Thread(target=lambda: responses.append(self.request({}, "/api/v1/survey")))
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                payload = {"confirmed": True, "patch": {"channel": 149}, "target_nodes": [1]}
                self.assertEqual(self.request(payload)[0], 409)
            finally:
                release.set()
                worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(responses[0][0], 500)
        self.assertEqual(self.daemon.get_status_report()["server_state"], "IDLE")

    def test_invalid_requests_fail_before_protocol(self):


        cases = [
            None, [], True, {},
            {"confirmed": 1, "patch": {"channel": 149}, "target_nodes": [1]},
            {"confirmed": True, "patch": {}, "target_nodes": [1]},
            {"confirmed": True, "patch": {"channel": 161}, "target_nodes": [1]},
            {"confirmed": True, "patch": {"channel": True}, "target_nodes": [1]},
            {"confirmed": True, "patch": {"unknown": 149}, "target_nodes": [1]},
            {"confirmed": True, "patch": {"channel": 149}, "target_nodes": []},
            {"confirmed": True, "patch": {"channel": 149}, "target_nodes": [True]},
            {"confirmed": True, "patch": {"channel": 149}, "target_nodes": [1, 1]},
            {"confirmed": True, "patch": {"channel": 149}, "target_nodes": [11]},
            {"confirmed": True, "patch": {"channel": 149}, "target_nodes": [1], "commit_timeout_seconds": 1},
        ]
        for body in cases:
            with self.subTest(body=body):
                code, result = self.request(body)
                self.assertEqual(code, 400, result)
                self.assertEqual(self.daemon.get_status_report()["server_state"], "IDLE")
