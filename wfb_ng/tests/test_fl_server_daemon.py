"""
Tests for WFB-ng Stage 2 Server Persistent Daemon, Pre-allocated Slot Pool,
Local REST IPC, and Deterministic Job Preflight Gates (Ticket 05).
"""

import json
import os
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

from wfb_ng.fl.control import (
    ClientNodeState,
    ControlPlaneClient,
    ControlPlaneServer,
    NodeHeartbeat,
    NodeReadiness,
)
from wfb_ng.fl.errors import FLRuntimeError
from wfb_ng.fl.radio import (
    ChannelSurveyResult,
    RadioConfig,
    SpectrumSurveyReport,
)
from wfb_ng.fl.server_daemon import (
    DEFAULT_SERVER_CONFIG_PATH,
    ServerDaemon,
    ServerDaemonConfig,
    ServerState,
    build_v6_uplink_server_command,
    load_server_config,
)
from wfb_ng.tests.test_fl_client_daemon import MockNetworkAdapter
from wfb_ng.tests.test_fl_radio import MockSurveyBackend


class TestServerDaemonConfig(unittest.TestCase):
    """Test configuration loading, validation, and defaults."""

    def test_default_config(self):
        cfg = ServerDaemonConfig()
        self.assertEqual(cfg.ipc_host, "127.0.0.1")
        self.assertEqual(cfg.ipc_port, 9090)
        self.assertEqual(cfg.channel, 157)
        self.assertEqual(cfg.radio_txpower_dbm, 12)
        self.assertEqual(cfg.downlink_mcs, 3)
        self.assertEqual(cfg.uplink_mcs, 6)
        self.assertEqual(cfg.tun_name, "fl-s")
        self.assertEqual(cfg.tun_ip, "10.80.0.1")
        self.assertEqual(cfg.tun_cidr, "10.80.0.1/24")
        self.assertEqual(cfg.tun_txqueuelen, 5000)
        self.assertEqual(cfg.known_clients, tuple(range(1, 11)))

    def test_load_from_json_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({
                "ipc_host": "127.0.0.1",
                "ipc_port": 9099,
                "channel": 149,
                "radio_txpower_dbm": 15,
                "downlink_mcs": 4,
                "uplink_mcs": 5,
                "tun_name": "fl-custom",
            }, f)
            temp_path = f.name

        try:
            cfg = load_server_config(temp_path)
            self.assertEqual(cfg.ipc_port, 9099)
            self.assertEqual(cfg.channel, 149)
            self.assertEqual(cfg.radio_txpower_dbm, 15)
            self.assertEqual(cfg.downlink_mcs, 4)
            self.assertEqual(cfg.uplink_mcs, 5)
            self.assertEqual(cfg.tun_name, "fl-custom")
            self.assertEqual(cfg.known_clients, tuple(range(1, 11)))
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_load_nonexistent_custom_config_fails_closed(self):
        with self.assertRaises(FLRuntimeError) as ctx:
            load_server_config("/nonexistent/path/server.json")
        self.assertEqual(ctx.exception.error_code, "config_not_found")

    def test_fail_closed_on_invalid_radio_parameters(self):
        # Forbidden channel 161
        with self.assertRaises(FLRuntimeError):
            ServerDaemonConfig(channel=161)

        # Invalid txpower
        with self.assertRaises(FLRuntimeError):
            ServerDaemonConfig(radio_txpower_dbm=0)
        with self.assertRaises(FLRuntimeError):
            ServerDaemonConfig(radio_txpower_dbm=35)

        # Invalid MCS
        with self.assertRaises(FLRuntimeError):
            ServerDaemonConfig(downlink_mcs=2)
        with self.assertRaises(FLRuntimeError):
            ServerDaemonConfig(downlink_mcs=7)

        # Shrinking known_clients pool is strictly forbidden
        with self.assertRaises(FLRuntimeError) as ctx:
            ServerDaemonConfig(known_clients=(1, 2, 3))
        self.assertEqual(ctx.exception.error_code, "invalid_known_clients")


class TestV6UplinkServerCommand(unittest.TestCase):
    """Test command-line construction for wfb_v6_uplink server role."""

    def test_command_generation_preallocated_slots_and_no_slot_flags(self):
        cmd = build_v6_uplink_server_command(
            executable_path="/usr/bin/wfb_v6_uplink",
            tun_name="fl-s",
            tun_addr="10.80.0.1/24",
            air_interface="wlan0",
            channel=157,
            downlink_mcs=3,
            link_id=7669206,
            known_clients=tuple(range(1, 11)),
        )

        # Basic role and identifiers
        self.assertIn("--role", cmd)
        self.assertEqual(cmd[cmd.index("--role") + 1], "server")
        self.assertIn("--tun-name", cmd)
        self.assertEqual(cmd[cmd.index("--tun-name") + 1], "fl-s")
        self.assertIn("--tun-addr", cmd)
        self.assertEqual(cmd[cmd.index("--tun-addr") + 1], "10.80.0.1/24")
        self.assertIn("--node-id", cmd)
        self.assertEqual(cmd[cmd.index("--node-id") + 1], "255")
        self.assertIn("--link-id", cmd)
        self.assertEqual(cmd[cmd.index("--link-id") + 1], "7669206")
        self.assertIn("--air-interface", cmd)
        self.assertEqual(cmd[cmd.index("--air-interface") + 1], "wlan0")

        # FEC and bandwidth
        self.assertIn("--fec-k", cmd)
        self.assertEqual(cmd[cmd.index("--fec-k") + 1], "8")
        self.assertIn("--fec-n", cmd)
        self.assertEqual(cmd[cmd.index("--fec-n") + 1], "14")
        self.assertIn("--radio-bandwidth", cmd)
        self.assertEqual(cmd[cmd.index("--radio-bandwidth") + 1], "40")
        self.assertIn("--radio-mcs-index", cmd)
        self.assertEqual(cmd[cmd.index("--radio-mcs-index") + 1], "3")
        self.assertIn("--radio-short-gi", cmd)

        # Pre-allocated 1..10 known clients
        self.assertIn("--known-clients", cmd)
        self.assertEqual(
            cmd[cmd.index("--known-clients") + 1],
            "1,2,3,4,5,6,7,8,9,10",
        )

        # Check all 10 client targets
        for nid in range(1, 11):
            expected_target = f"{nid}:10.80.0.{10 + nid}:127.0.0.1:1"
            self.assertIn(expected_target, cmd)

        # Spec / ADR-0014 requirement: MUST NOT pass grant-duration-ms or guard-interval-ms
        self.assertNotIn("--grant-duration-ms", cmd)
        self.assertNotIn("--guard-interval-ms", cmd)

    def test_channel_165_sets_narrow_bandwidth(self):
        cmd = build_v6_uplink_server_command(
            executable_path="/usr/bin/wfb_v6_uplink",
            tun_name="fl-s",
            tun_addr="10.80.0.1/24",
            air_interface="wlan0",
            channel=165,
            downlink_mcs=3,
        )
        self.assertIn("--radio-bandwidth", cmd)
        self.assertEqual(cmd[cmd.index("--radio-bandwidth") + 1], "20")


class TestServerDaemonRestIPCAndPreflight(unittest.TestCase):
    """Test Local REST IPC server and the 3 deterministic preflight checks."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="wfb_test_server_")
        self.adapter = MockNetworkAdapter(["wlan0"])
        self.survey_backend = MockSurveyBackend({
            149: 5,
            153: 0,
            157: 12,
            165: 25,
        })

        # Ephemeral ports for test isolation
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.bind(("127.0.0.1", 0))
            self.control_bind_port = s.getsockname()[1]
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.bind(("127.0.0.1", 0))
            self.control_broadcast_port = s.getsockname()[1]

        self.config = ServerDaemonConfig(
            ipc_host="127.0.0.1",
            ipc_port=0,  # Ephemeral HTTP port
            channel=157,
            radio_txpower_dbm=12,
            downlink_mcs=3,
            uplink_mcs=6,
            control_bind_port=self.control_bind_port,
            control_broadcast_port=self.control_broadcast_port,
            work_dir=self.temp_dir,
            enable_link_process=False,  # Mocked
        )

        self.daemon = ServerDaemon(
            config=self.config,
            network_adapter=self.adapter,
            survey_backend=self.survey_backend,
        )
        self.daemon.start()
        self.http_port = self.daemon.actual_ipc_port
        self.base_url = f"http://127.0.0.1:{self.http_port}"

        # Create dummy initial model file
        self.model_path = os.path.join(self.temp_dir, "initial_model.bin")
        self.model_data = b"M" * 1024
        with open(self.model_path, "wb") as f:
            f.write(self.model_data)

    def tearDown(self):
        self.daemon.stop()
        if os.path.exists(self.temp_dir):
            import shutil
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _http_get(self, path: str) -> Tuple[int, Dict[str, Any]]:
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return resp.status, data
        except urllib.error.HTTPError as exc:
            data = json.loads(exc.read().decode("utf-8"))
            return exc.code, data

    def _http_post(self, path: str, payload: Optional[Dict[str, Any]] = None) -> Tuple[int, Dict[str, Any]]:
        url = f"{self.base_url}{path}"
        body = json.dumps(payload or {}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return resp.status, data
        except urllib.error.HTTPError as exc:
            data = json.loads(exc.read().decode("utf-8"))
            return exc.code, data

    def _simulate_client_handshake(self, node_id: int):
        """Simulate client legitimate HUNTING -> ACK -> IDLE handshake to establish READY state."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server_addr = ("127.0.0.1", self.control_bind_port)

        # 1. HUNTING
        hb_hunting = NodeHeartbeat(
            node_id=node_id,
            state=ClientNodeState.HUNTING.value,
            elapsed_ms=0,
            current_channel=157,
            txpower_dbm=12,
            uplink_mcs=6,
            timestamp_ms=1000,
        )
        sock.sendto(hb_hunting.to_bytes(), server_addr)
        time.sleep(0.05)

        # 2. IDLE
        hb_idle = NodeHeartbeat(
            node_id=node_id,
            state=ClientNodeState.IDLE.value,
            elapsed_ms=0,
            current_channel=157,
            txpower_dbm=12,
            uplink_mcs=6,
            timestamp_ms=1050,
        )
        sock.sendto(hb_idle.to_bytes(), server_addr)
        time.sleep(0.05)
        sock.close()

    def test_status_endpoint_returns_topology_and_radio(self):
        # 1. Basic status without nodes
        status_code, body = self._http_get("/api/v1/status")
        self.assertEqual(status_code, 200)
        self.assertEqual(body["server_state"], "IDLE")
        self.assertIsNone(body["active_job"])
        self.assertEqual(body["radio"]["channel"], 157)
        self.assertEqual(body["radio"]["radio_txpower_dbm"], 12)
        self.assertEqual(body["radio"]["downlink_mcs"], 3)
        self.assertEqual(body["radio"]["uplink_mcs"], 6)
        self.assertEqual(body["tun"]["name"], "fl-s")
        self.assertEqual(body["tun"]["ip"], "10.80.0.1")

        # Also verify unversioned alias returns 404 (strict versioned API)
        code_alias, body_alias = self._http_get("/status")
        self.assertEqual(code_alias, 404)

        # 2. Simulate Node 1 and Node 2 check-ins
        self._simulate_client_handshake(1)
        self._simulate_client_handshake(2)

        status_code, body = self._http_get("/api/v1/status")
        self.assertEqual(status_code, 200)
        nodes = body["nodes"]
        # Per spec, all 1..10 pre-allocated slots must be present
        self.assertEqual(len(nodes), 10)
        for nid in range(1, 11):
            self.assertIn(str(nid), nodes)

        self.assertEqual(nodes["1"]["readiness"], "READY")
        self.assertEqual(nodes["1"]["reported_state"], "IDLE")
        self.assertIsNotNone(nodes["1"]["last_heartbeat_timestamp_ms"])
        self.assertEqual(nodes["2"]["readiness"], "READY")
        self.assertEqual(nodes["2"]["reported_state"], "IDLE")
        self.assertLess(nodes["1"]["last_heartbeat_ago_seconds"], 2.0)

        # Unconnected nodes are pre-allocated but OFFLINE
        self.assertEqual(nodes["3"]["readiness"], "OFFLINE")
        self.assertIsNone(nodes["3"]["reported_state"])
        self.assertEqual(nodes["3"]["tun_ip"], "10.80.0.13")

    def test_survey_endpoint(self):
        # Trigger 5GHz survey
        status_code, body = self._http_post("/api/v1/survey", {"duration_ms": 300})
        self.assertEqual(status_code, 200)
        self.assertIn("results", body)
        self.assertIn("ranking", body)
        # Channel 153 had 0 frames, so it should rank 1st
        self.assertEqual(body["ranking"][0], 153)

        # Also verify unversioned alias returns 404
        status_code_alias, body_alias = self._http_post("/survey", {"duration_ms": 300})
        self.assertEqual(status_code_alias, 404)

    def test_preflight_gate_1_target_nodes_not_ready(self):
        """
        Gate 1: target_nodes must be confirmed IDLE within 10 seconds.
        """
        # Node 1 is ready, but Node 2 has never checked in
        self._simulate_client_handshake(1)

        payload = {
            "job_id": "job_fl_001",
            "target_nodes": [1, 2],
            "model_path": self.model_path,
            "model_size_bytes": len(self.model_data),
        }
        status_code, body = self._http_post("/api/v1/jobs/start", payload)
        self.assertEqual(status_code, 400)
        self.assertEqual(body["error"], "preflight_target_nodes_not_ready")
        self.assertIn("2", body["unready_nodes"])

    def test_preflight_gate_1_target_node_in_hunting_fails(self):
        """
        Node only sent HUNTING and hasn't closed two-army IDLE loop -> CONNECTING, fails.
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        hb = NodeHeartbeat(
            node_id=1,
            state=ClientNodeState.HUNTING.value,
            elapsed_ms=0,
            current_channel=157,
            txpower_dbm=12,
            uplink_mcs=6,
        )
        sock.sendto(hb.to_bytes(), ("127.0.0.1", self.control_bind_port))
        sock.close()
        time.sleep(0.05)

        payload = {
            "job_id": "job_fl_002",
            "target_nodes": [1],
            "model_path": self.model_path,
            "model_size_bytes": len(self.model_data),
        }
        status_code, body = self._http_post("/api/v1/jobs/start", payload)
        self.assertEqual(status_code, 400)
        self.assertEqual(body["error"], "preflight_target_nodes_not_ready")
        self.assertEqual(body["unready_nodes"]["1"]["readiness"], "CONNECTING")

    def test_preflight_gate_1_target_node_timed_out_offline(self):
        """
        Node was ready, but last heartbeat was > 10s ago -> OFFLINE, fails.
        """
        self._simulate_client_handshake(1)
        # Fast forward last heartbeat time
        node_record = self.daemon.control_plane.registry.get_node(1)
        node_record.last_heartbeat_time -= 12.0

        payload = {
            "job_id": "job_fl_003",
            "target_nodes": [1],
            "model_path": self.model_path,
            "model_size_bytes": len(self.model_data),
        }
        status_code, body = self._http_post("/api/v1/jobs/start", payload)
        self.assertEqual(status_code, 400)
        self.assertEqual(body["error"], "preflight_target_nodes_not_ready")
        self.assertEqual(body["unready_nodes"]["1"]["readiness"], "OFFLINE")

    def test_preflight_gate_2_model_file_validation(self):
        """
        Gate 2: initial model file must exist, non-empty, and match size.
        """
        self._simulate_client_handshake(1)

        # 1. Non-existent file
        payload_missing = {
            "job_id": "job_fl_004",
            "target_nodes": [1],
            "model_path": os.path.join(self.temp_dir, "non_existent.bin"),
            "model_size_bytes": 1024,
        }
        status_code, body = self._http_post("/api/v1/jobs/start", payload_missing)
        self.assertEqual(status_code, 400)
        self.assertEqual(body["error"], "preflight_model_file_not_found")

        # 2. Empty file
        empty_path = os.path.join(self.temp_dir, "empty.bin")
        open(empty_path, "wb").close()
        payload_empty = {
            "job_id": "job_fl_005",
            "target_nodes": [1],
            "model_path": empty_path,
            "model_size_bytes": 1024,
        }
        status_code, body = self._http_post("/api/v1/jobs/start", payload_empty)
        self.assertEqual(status_code, 400)
        self.assertEqual(body["error"], "preflight_model_empty")

        # 3. Missing model_size_bytes (strictly required)
        payload_missing_size = {
            "job_id": "job_fl_no_size",
            "target_nodes": [1],
            "model_path": self.model_path,
        }
        status_code, body = self._http_post("/api/v1/jobs/start", payload_missing_size)
        self.assertEqual(status_code, 400)
        self.assertEqual(body["error"], "preflight_model_missing_size")

        # 4. Size mismatch
        payload_size_mismatch = {
            "job_id": "job_fl_006",
            "target_nodes": [1],
            "model_path": self.model_path,
            "model_size_bytes": 999999,  # actual is 1024
        }
        status_code, body = self._http_post("/api/v1/jobs/start", payload_size_mismatch)
        self.assertEqual(status_code, 400)
        self.assertEqual(body["error"], "preflight_model_validation_failed")

        # 5. Checksum mismatch if model_sha256 provided
        payload_sha_mismatch = {
            "job_id": "job_fl_bad_sha",
            "target_nodes": [1],
            "model_path": self.model_path,
            "model_size_bytes": len(self.model_data),
            "model_sha256": "0" * 64,
        }
        status_code, body = self._http_post("/api/v1/jobs/start", payload_sha_mismatch)
        self.assertEqual(status_code, 400)
        self.assertEqual(body["error"], "preflight_model_checksum_failed")

    def test_preflight_gate_3_engine_concurrency_conflict(self):
        """
        Gate 3: engine must be IDLE; concurrent job submissions fail with HTTP 409.
        """
        self._simulate_client_handshake(1)
        self._simulate_client_handshake(2)

        # Start first job successfully
        payload = {
            "job_id": "job_fl_primary",
            "mode": "sync",
            "target_nodes": [1, 2],
            "model_path": self.model_path,
            "model_size_bytes": 1024,
        }
        status_code, body = self._http_post("/api/v1/jobs/start", payload)
        self.assertEqual(status_code, 200)
        self.assertEqual(body["status"], "accepted")
        self.assertEqual(body["job_id"], "job_fl_primary")
        self.assertEqual(self.daemon.server_state, ServerState.RUNNING)

        # Attempt to submit second concurrent job -> must be rejected with HTTP 409
        status_code2, body2 = self._http_post("/api/v1/jobs/start", payload)
        self.assertEqual(status_code2, 409)
        self.assertEqual(body2["error"], "preflight_engine_conflict")

        # Attempt to run survey while job is running -> must be rejected with HTTP 409
        status_code_survey, body_survey = self._http_post("/api/v1/survey")
        self.assertEqual(status_code_survey, 409)
        self.assertEqual(body_survey["error"], "engine_busy")

    def test_job_abort_resets_engine_and_broadcasts(self):
        """
        Test POST /api/v1/jobs/abort stops active job and resets engine to IDLE.
        """
        self._simulate_client_handshake(1)

        payload = {
            "job_id": "job_to_abort",
            "target_nodes": [1],
            "model_path": self.model_path,
            "model_size_bytes": len(self.model_data),
        }
        status_code, _ = self._http_post("/api/v1/jobs/start", payload)
        self.assertEqual(status_code, 200)
        self.assertEqual(self.daemon.server_state, ServerState.RUNNING)

        # Abort job
        status_code_abort, body_abort = self._http_post("/api/v1/jobs/abort")
        self.assertEqual(status_code_abort, 200)
        self.assertEqual(body_abort["status"], "aborted")
        self.assertEqual(self.daemon.server_state, ServerState.IDLE)
        self.assertIsNone(self.daemon.active_job)

        # New job can now start
        status_code_restart, _ = self._http_post("/api/v1/jobs/start", payload)
        self.assertEqual(status_code_restart, 200)

    def test_logs_stream_sse_protocol(self):
        """
        Test GET /api/v1/logs/stream returns SSE events.
        """
        url = f"{self.base_url}/api/v1/logs/stream"
        req = urllib.request.Request(url, method="GET")

        # Open streaming connection
        resp = urllib.request.urlopen(req, timeout=3.0)
        self.assertEqual(resp.status, 200)
        self.assertIn("text/event-stream", resp.headers.get("Content-Type", ""))

        # Read initial connected event
        line1 = resp.readline().decode("utf-8").strip()
        line2 = resp.readline().decode("utf-8").strip()
        line3 = resp.readline().decode("utf-8").strip()  # empty separator

        self.assertTrue(line1.startswith("event:") or line1.startswith("data:"))
        # Publish an event via daemon
        self.daemon.publish_event({"type": "CUSTOM_TEST_EVENT", "value": 42})

        # Read next SSE frame
        event_lines = []
        for _ in range(3):
            line = resp.readline().decode("utf-8").strip()
            if line:
                event_lines.append(line)

        raw_event = " ".join(event_lines)
        self.assertIn("CUSTOM_TEST_EVENT", raw_event)
        self.assertIn("42", raw_event)

        resp.close()


    def test_preflight_gate_1_unsolicited_idle_held_in_connecting_fails(self):
        """
        An unsolicited IDLE heartbeat without prior HUNTING handshake is held in CONNECTING
        by the two-army anti-desync gate, so preflight check MUST reject it.
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Send direct IDLE for node 3 without any prior HUNTING
        hb_unsolicited = NodeHeartbeat(
            node_id=3,
            state=ClientNodeState.IDLE.value,
            elapsed_ms=0,
            current_channel=157,
            txpower_dbm=12,
            uplink_mcs=6,
        )
        sock.sendto(hb_unsolicited.to_bytes(), ("127.0.0.1", self.control_bind_port))
        sock.close()
        time.sleep(0.05)

        # Registry should have node 3 in CONNECTING, not READY
        node3 = self.daemon.control_plane.registry.get_node(3)
        self.assertIsNotNone(node3)
        self.assertEqual(node3.readiness, NodeReadiness.CONNECTING)

        payload = {
            "job_id": "job_unsolicited",
            "target_nodes": [3],
            "model_path": self.model_path,
            "model_size_bytes": len(self.model_data),
        }
        status_code, body = self._http_post("/api/v1/jobs/start", payload)
        self.assertEqual(status_code, 400)
        self.assertEqual(body["error"], "preflight_target_nodes_not_ready")
        self.assertEqual(body["unready_nodes"]["3"]["readiness"], "CONNECTING")

    def test_preflight_rejects_uftp_control_port_conflict(self):
        self._simulate_client_handshake(1)
        payload = {
            "job_id": "job_port_conflict",
            "target_nodes": [1],
            "model_path": self.model_path,
            "model_size_bytes": len(self.model_data),
            "uftp_port": self.config.control_broadcast_port,
        }
        status_code, body = self._http_post("/api/v1/jobs/start", payload)
        self.assertEqual(status_code, 400)
        self.assertEqual(body["error"], "preflight_port_conflict")
        self.assertEqual(self.daemon.server_state, ServerState.IDLE)

    def test_preflight_invalid_target_nodes_payload(self):
        # Empty list
        status_code, body = self._http_post(
            "/api/v1/jobs/start",
            {"target_nodes": [], "model_path": self.model_path, "model_size_bytes": 1024},
        )
        self.assertEqual(status_code, 400)
        self.assertEqual(body["error"], "preflight_target_nodes_invalid")

        # Out-of-bounds node id
        status_code, body = self._http_post(
            "/api/v1/jobs/start",
            {"target_nodes": [1, 99], "model_path": self.model_path, "model_size_bytes": 1024},
        )
        self.assertEqual(status_code, 400)
        self.assertEqual(body["error"], "preflight_target_nodes_invalid")

        # Non-integer node id
        status_code, body = self._http_post(
            "/api/v1/jobs/start",
            {"target_nodes": ["client1"], "model_path": self.model_path, "model_size_bytes": 1024},
        )
        self.assertEqual(status_code, 400)
        self.assertEqual(body["error"], "preflight_target_nodes_invalid")

    def test_semi_async_and_async_modes_validation(self):
        self._simulate_client_handshake(1)
        self._simulate_client_handshake(2)

        # semi_async missing min_updates fails
        payload_no_min = {
            "job_id": "job_semi_no_min",
            "mode": "semi_async",
            "target_nodes": [1, 2],
            "model_path": self.model_path,
            "model_size_bytes": 1024,
        }
        code, body = self._http_post("/api/v1/jobs/start", payload_no_min)
        self.assertEqual(code, 400)
        self.assertEqual(body["error"], "invalid_semi_async_config")

        # semi_async min_updates boolean (True) fails
        payload_bool_min = {
            "job_id": "job_semi_bool_min",
            "mode": "semi_async",
            "target_nodes": [1, 2],
            "min_updates": True,
            "model_path": self.model_path,
            "model_size_bytes": 1024,
        }
        code, body = self._http_post("/api/v1/jobs/start", payload_bool_min)
        self.assertEqual(code, 400)
        self.assertEqual(body["error"], "invalid_semi_async_config")

        # semi_async out of bounds min_updates fails
        payload_bad_min = {
            "job_id": "job_semi_bad_min",
            "mode": "semi_async",
            "target_nodes": [1, 2],
            "min_updates": 5,
            "model_path": self.model_path,
            "model_size_bytes": 1024,
        }
        code, body = self._http_post("/api/v1/jobs/start", payload_bad_min)
        self.assertEqual(code, 400)
        self.assertEqual(body["error"], "invalid_semi_async_config")

        # invalid mode fails
        payload_bad_mode = {
            "job_id": "job_bad_mode",
            "mode": "unknown_paradigm",
            "target_nodes": [1, 2],
            "model_path": self.model_path,
            "model_size_bytes": 1024,
        }
        code, body = self._http_post("/api/v1/jobs/start", payload_bad_mode)
        self.assertEqual(code, 400)
        self.assertEqual(body["error"], "invalid_fl_mode")

        # duplicate target nodes fail
        payload_dup_nodes = {
            "job_id": "job_dup",
            "target_nodes": [1, 1],
            "model_path": self.model_path,
            "model_size_bytes": 1024,
        }
        code, body = self._http_post("/api/v1/jobs/start", payload_dup_nodes)
        self.assertEqual(code, 400)
        self.assertEqual(body["error"], "preflight_target_nodes_invalid")

        # model_size_bytes boolean (True) fails
        payload_bool_size = {
            "job_id": "job_bool_size",
            "target_nodes": [1],
            "model_path": self.model_path,
            "model_size_bytes": True,
        }
        code, body = self._http_post("/api/v1/jobs/start", payload_bool_size)
        self.assertEqual(code, 400)
        self.assertEqual(body["error"], "preflight_model_missing_size")

        # malformed model_sha256 fails
        payload_bad_hex = {
            "job_id": "job_bad_hex",
            "target_nodes": [1],
            "model_path": self.model_path,
            "model_size_bytes": 1024,
            "model_sha256": "not-valid-hex-or-length",
        }
        code, body = self._http_post("/api/v1/jobs/start", payload_bad_hex)
        self.assertEqual(code, 400)
        self.assertEqual(body["error"], "invalid_model_sha256")

    def test_invalid_json_request_body(self):
        url = f"{self.base_url}/api/v1/jobs/start"
        req = urllib.request.Request(
            url,
            data=b"not-valid-json{{{",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                self.fail("Expected 400 error")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)
            data = json.loads(exc.read().decode("utf-8"))
            self.assertEqual(data["error"], "invalid_json")

    def test_not_found_endpoint(self):
        status_code, body = self._http_get("/api/v1/non_existent_route")
        self.assertEqual(status_code, 404)
        self.assertEqual(body["error"], "not_found")

    def test_full_client_control_plane_and_server_daemon_interaction(self):
        """
        End-to-end integration between ControlPlaneClient and ServerDaemon:
        - Client hunts and locks channel.
        - Server marks client as READY in registry.
        - Job starts successfully.
        - Client receives TASK_ANNOUNCE via broadcast.
        - Server aborts job, Client receives JOB_ABORT via broadcast.
        """
        client_adapter = MockNetworkAdapter(["wlan1"])
        client = ControlPlaneClient(
            node_id=1,
            tun_ip="10.80.0.11",
            network_adapter=client_adapter,
            air_interface="wlan1",
            initial_channel=157,
            cached_channel=157,
            txpower_dbm=12,
            uplink_mcs=6,
            server_host="127.0.0.1",
            server_port=self.control_bind_port,
            broadcast_port=self.control_broadcast_port,
            attempt_timeout_seconds=0.1,
            max_attempts_per_channel=2,
        )
        client.start_broadcast_listener()

        received_broadcasts: List[Dict[str, Any]] = []
        client.register_broadcast_handler(
            "TASK_ANNOUNCE", lambda m: received_broadcasts.append(m)
        )
        client.register_broadcast_handler(
            "JOB_ABORT", lambda m: received_broadcasts.append(m)
        )

        try:
            # 1. Client hunts
            found = client.hunt_once(ladder=[157])
            self.assertTrue(found)
            self.assertEqual(client.state, ClientNodeState.IDLE)

            # Wait briefly for server to process IDLE heartbeat
            time.sleep(0.08)

            # Verify server reports node 1 as READY
            status_code, status_body = self._http_get("/api/v1/status")
            self.assertEqual(status_code, 200)
            self.assertEqual(status_body["nodes"]["1"]["readiness"], "READY")

            # 2. Start job via REST IPC
            payload = {
                "job_id": "job_e2e_test",
                "mode": "sync",
                "target_nodes": [1],
                "model_path": self.model_path,
                "model_size_bytes": len(self.model_data),
            }
            start_code, start_body = self._http_post("/api/v1/jobs/start", payload)
            self.assertEqual(start_code, 200)
            self.assertEqual(start_body["status"], "accepted")

            # Wait for broadcast reception
            time.sleep(0.1)
            self.assertTrue(any(b.get("type") == "TASK_ANNOUNCE" for b in received_broadcasts))
            task_msg = next(b for b in received_broadcasts if b.get("type") == "TASK_ANNOUNCE")
            self.assertEqual(task_msg["job_id"], "job_e2e_test")
            self.assertEqual(task_msg["link_id"], self.daemon.config.link_id)

            # 3. Abort job via REST IPC
            abort_code, abort_body = self._http_post("/api/v1/jobs/abort")
            self.assertEqual(abort_code, 200)

            time.sleep(0.1)
            self.assertTrue(any(b.get("type") == "JOB_ABORT" for b in received_broadcasts))
        finally:
            client.stop()


class TestSystemdUnitAndPackage(unittest.TestCase):
    """Verify systemd service unit and setup.py entrypoint registrations."""

    def test_systemd_service_file_exists_and_configured(self):
        service_path = "scripts/systemd/wfb-fl-server-daemon.service"
        self.assertTrue(os.path.exists(service_path))
        with open(service_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("ExecStart=/usr/bin/wfb-fl-server-daemon", content)
        self.assertIn("Restart=always", content)
        self.assertIn("KillMode=control-group", content)

    def test_setup_py_contains_server_daemon_entry_and_data_files(self):
        with open("setup.py", "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("'wfb-fl-server-daemon=wfb_ng.fl.server_daemon:main'", content)
        self.assertIn("'scripts/systemd/wfb-fl-server-daemon.service'", content)

    def test_main_cli_argument_parsing_and_run(self):
        """Verify main() parses CLI arguments and initializes ServerDaemon correctly."""
        with patch("sys.argv", [
            "wfb-fl-server-daemon",
            "--ipc-host", "127.0.0.1",
            "--ipc-port", "9191",
            "--channel", "149",
            "--txpower", "14",
            "--downlink-mcs", "4",
            "--uplink-mcs", "5",
            "--interface", "wlan_test",
            "--tun-name", "fl-test",
        ]):
            with patch.object(ServerDaemon, "run") as mock_run:
                from wfb_ng.fl.server_daemon import main
                main()
                mock_run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
