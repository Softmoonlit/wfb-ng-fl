"""人工确认射频重配的 REST 准入与状态互斥。"""

import json
import threading
import unittest
import urllib.error
import urllib.request

from unittest.mock import patch

from wfb_ng.fl.radio import RadioConfig
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
                NodeHeartbeat(node_id, state.value, 0, 157, 12, 6, timestamp_ms=1000 + (state == ClientNodeState.IDLE)).to_bytes(),
                ("127.0.0.1", 10000 + node_id),
            )

    def test_cannot_exclude_an_online_client_from_cluster_configuration(self):
        self.ready_node(1)
        self.ready_node(2)
        with patch.object(self.daemon.control_plane, "broadcast_downlink", side_effect=OSError("不应开始事务")):
            code, result = self.request({
                "confirmed": True, "patch": {"radio_txpower_dbm": 14}, "target_nodes": [1],
            })
        self.assertEqual(code, 409, result)
        self.assertEqual(result["error"], "radio_target_set_changed")
        self.assertEqual(result["expected_nodes"], [1, 2])
        self.assertEqual(self.daemon.get_status_report()["server_state"], "IDLE")

    def test_calibration_is_explicit_and_prevents_normal_jobs(self):
        self.ready_node()
        candidate = {"bandwidth_mhz": 20, "downlink_mcs": 3, "uftp_rate_kbps": 4000}
        payload = {"confirmed": True, "patch": candidate, "target_nodes": [1]}
        self.assertEqual(self.request(payload)[0], 400)
        with patch.object(self.daemon.control_plane, "broadcast_downlink", side_effect=self.protocol_peer):
            code, result = self.request(payload, "/api/v1/radio/calibrate")
            self.assertEqual(code, 200, result)
            self.assertEqual(result["status"], "finalized")
            self.assertTrue(result["calibration"])
            self.assertEqual(result["effective_config"]["channel_width"], "HT20")
            with self.assertRaises(FLRuntimeError) as raised:
                self.daemon.start_job({"job_id": "cal-job", "run_id": "cal-run", "target_nodes": [1],
                    "model_path": "/missing/model", "model_size_bytes": 1,
                    "io_timeout_seconds": 120, "live_observation": False})
            self.assertEqual(raised.exception.error_code, "radio_calibration_active")
            code, result = self.request({"confirmed": True, "patch": {"bandwidth_mhz": 40},
                                         "target_nodes": [1]})
        self.assertEqual(code, 200, result)
        self.assertEqual(result["status"], "finalized")
        self.assertFalse(result["calibration"])
        self.assertEqual(result["effective_config"]["uftp_rate_kbps"], 15000)

    def test_saved_operator_configuration_is_used_after_restart(self):
        import tempfile
        from pathlib import Path
        from wfb_ng.fl.server_daemon import load_server_config
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "server.json"
            path.write_text('{"channel":157,"downlink_mcs":3,"model_library_dir":"/keep/models"}')
            daemon = ServerDaemon(load_server_config(str(path)), MockNetworkAdapter(["wlx-test"]))
            daemon.server_state = ServerState.IDLE
            daemon.radio_config = RadioConfig(
                channel=153, downlink_mcs=4, uftp_rate_kbps=22000)
            result = daemon.save_radio_configuration({"confirmed": True})
            self.assertEqual(result["saved_config"]["channel"], 153)
            restarted = load_server_config(str(path))
            self.assertEqual(restarted.channel, 153)
            self.assertEqual(restarted.downlink_mcs, 4)
            self.assertEqual(restarted.uftp_rate_kbps, 22000)
            self.assertEqual(restarted.model_library_dir, "/keep/models")

    def test_boot_configuration_rejects_incompatible_channel_and_bandwidth(self):
        with self.assertRaises(FLRuntimeError):
            ServerDaemonConfig(channel=165, bandwidth_mhz=40)

    def test_server_startup_uses_explicit_bandwidth(self):
        config = ServerDaemonConfig(bandwidth_mhz=40, uftp_rate_kbps=17000)
        self.assertEqual(config.bandwidth_mhz, 40)
        daemon = ServerDaemon(config, MockNetworkAdapter(["wlx-test"]))
        self.assertEqual(daemon.radio_config.uftp_rate_kbps, 17000)
        self.assertEqual(daemon.radio_config.bandwidth_mhz, 40)

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
            "CONFIG_RADIO_ABORT": "RADIO_SWITCH_ROLLBACK_ACK",
        }
        kind = kinds.get(message["type"])
        if kind:
            for nid in message["target_nodes"]:
                self.daemon.control_plane.handle_datagram(json.dumps({
                    "type": kind, "node_id": nid, "session_id": message["session_id"],
                    "current_channel": message.get("channel", 149),
                    "channel": message.get("channel", 149), "ok": True,
                    **({"config": message["config"], "calibration": message["calibration"]}
                       if kind != "PREPARE_ACK" else {}),
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
            NodeHeartbeat(1, "IDLE", 0, 149, 18, 5, timestamp_ms=2000).to_bytes(), ("127.0.0.1", 10001),
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
