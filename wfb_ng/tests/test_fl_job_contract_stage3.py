#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Unit tests for Stage 3 Ticket 03:
- Strict validation and pass-through of run_id, io_timeout_seconds, and live_observation.
- Guaranteed terminal consistency between abort, completion, and failure in ServerDaemon.
- Rejection of malicious or unwhitelisted observation paths injected over the wire.
- Readback verification of ServerRole and ClientRole configurations.
"""

import json
import os
import shutil
import socket
import tempfile
import time
import unittest
from typing import Any, Dict, List
from unittest import mock

from wfb_ng.fl.client_daemon import (
    ClientDaemon,
    ClientDaemonConfig,
    ClientJobConfig,
    DaemonState,
    JobSandbox,
)
from wfb_ng.fl.coordinator import JobConfig
from wfb_ng.fl.errors import FLRuntimeError
from wfb_ng.fl.role import ClientRole, ServerRole
from wfb_ng.fl.server_daemon import (
    ServerDaemon,
    ServerDaemonConfig,
    ServerState,
)
from wfb_ng.fl.service import load_role_service
from wfb_ng.tests.test_fl_client_daemon import MockNetworkAdapter
from wfb_ng.tests.test_fl_radio import MockSurveyBackend


class TestStage3JobContractAndTerminalConsistency(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="wfb_test_stage3_job_")
        self.adapter = MockNetworkAdapter(["wlan0"])
        self.survey_backend = MockSurveyBackend()

        # Ephemeral ports for server daemon
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.bind(("127.0.0.1", 0))
            self.control_bind_port = s.getsockname()[1]
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.bind(("127.0.0.1", 0))
            self.control_broadcast_port = s.getsockname()[1]

        # Valid mock model file
        self.model_data = b"M" * 1024
        self.model_path = os.path.join(self.temp_dir, "model.bin")
        with open(self.model_path, "wb") as f:
            f.write(self.model_data)

        self.valid_job_payload = {
            "run_id": "stage3_run_001",
            "job_id": "job_stage3_001",
            "mode": "sync",
            "target_nodes": [1],
            "model_path": self.model_path,
            "model_size_bytes": len(self.model_data),
            "rounds": 1,
            "round_timeout_seconds": 120.0,
            "io_timeout_seconds": 120,
            "live_observation": True,
        }

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # Slice 1: JobConfig & POST /api/v1/jobs/start Strict Field Validation
    # -------------------------------------------------------------------------
    def test_job_config_requires_run_id_io_timeout_and_live_observation(self):
        # Missing run_id
        payload = dict(self.valid_job_payload)
        del payload["run_id"]
        with self.assertRaises(FLRuntimeError) as ctx:
            JobConfig.from_dict(payload)
        self.assertEqual(ctx.exception.error_code, "invalid_run_id")

        # Invalid run_id (contains path traversal, spaces, or empty)
        for bad_run_id in ["", "   ", "run/1", "../escape", "run 1", None, 123]:
            payload = dict(self.valid_job_payload, run_id=bad_run_id)
            with self.assertRaises(FLRuntimeError) as ctx:
                JobConfig.from_dict(payload)
            self.assertEqual(ctx.exception.error_code, "invalid_run_id")

        # Missing io_timeout_seconds
        payload = dict(self.valid_job_payload)
        del payload["io_timeout_seconds"]
        with self.assertRaises(FLRuntimeError) as ctx:
            JobConfig.from_dict(payload)
        self.assertEqual(ctx.exception.error_code, "invalid_io_timeout")

        # Invalid io_timeout_seconds (non-integer, <= 0)
        for bad_io in [0, -1, 120.5, "120", True, False, None]:
            payload = dict(self.valid_job_payload, io_timeout_seconds=bad_io)
            with self.assertRaises(FLRuntimeError) as ctx:
                JobConfig.from_dict(payload)
            self.assertEqual(ctx.exception.error_code, "invalid_io_timeout")

        # Missing live_observation
        payload = dict(self.valid_job_payload)
        del payload["live_observation"]
        with self.assertRaises(FLRuntimeError) as ctx:
            JobConfig.from_dict(payload)
        self.assertEqual(ctx.exception.error_code, "invalid_live_observation")

        # Invalid live_observation (non-boolean)
        for bad_obs in ["true", 1, 0, None, []]:
            payload = dict(self.valid_job_payload, live_observation=bad_obs)
            with self.assertRaises(FLRuntimeError) as ctx:
                JobConfig.from_dict(payload)
            self.assertEqual(ctx.exception.error_code, "invalid_live_observation")

    def test_job_config_round_and_io_timeout_are_independent(self):
        # round_timeout_seconds and io_timeout_seconds can be completely different positive values
        payload = dict(
            self.valid_job_payload,
            round_timeout_seconds=45.0,
            io_timeout_seconds=120,
        )
        cfg = JobConfig.from_dict(payload)
        self.assertEqual(cfg.round_timeout_seconds, 45.0)
        self.assertEqual(cfg.io_timeout_seconds, 120)
        self.assertEqual(cfg.run_id, "stage3_run_001")
        self.assertTrue(cfg.live_observation)

    # -------------------------------------------------------------------------
    # Slice 2: ServerRole construction uses request I/O timeout & live_observation
    # -------------------------------------------------------------------------
    def test_server_role_persists_actual_transport_configuration(self):
        role_dir = os.path.join(self.temp_dir, "readback")
        role = ServerRole(
            work_dir=role_dir, participant_node_id=(1, 2),
            participant_uftp_uid=(1, 2), server_uftp_uid=100,
            uftp_port=1044, http_port=0, max_update_size_bytes=1024,
            live_observation=True, observation_path=os.path.join(role_dir, "observation.jsonl"),
            io_timeout=120, uftp_rate_kbps=15000,
        )
        try:
            with open(os.path.join(role_dir, "server_role.json"), encoding="utf-8") as stream:
                actual = json.load(stream)
            self.assertEqual(actual["io_timeout_seconds"], 120)
            self.assertEqual(actual["uftp_rate_kbps"], 15000)
            self.assertIs(actual["live_observation"], True)
            self.assertEqual(actual["participant_node_ids"], [1, 2])
        finally:
            role.close()

    def test_server_role_constructs_with_explicit_io_timeout_and_live_observation(self):
        role_dir = os.path.join(self.temp_dir, "server_role_test")
        obs_file = os.path.join(role_dir, "obs.jsonl")
        role = ServerRole(
            work_dir=role_dir,
            participant_node_id=(1, 2),
            participant_uftp_uid=(1, 2),
            server_uftp_uid=100,
            uftp_port=1044,
            http_port=8080,
            max_update_size_bytes=1024,
            live_observation=True,
            observation_path=obs_file,
            io_timeout=120,
        )
        try:
            self.assertEqual(role.transport.io_timeout, 120)
            self.assertTrue(role.transport.live_observation)
            self.assertEqual(role.transport.observation_path, obs_file)
        finally:
            role.close()

        # Readback verification of generated configuration dictionary
        from wfb_ng.fl.service import _read_config
        server_cfg = {
            "schema_version": 1,
            "role": "server",
            "node_id": 100,
            "work_dir": role_dir,
            "channel": 157,
            "channel_width": "HT40+",
            "link_args": ["--tun-name", "fl-s"],
            "participant_node_ids": [1, 2],
            "participant_uftp_uids": [1, 2],
            "server_uftp_uid": 100,
            "uftp_port": 1044,
            "http_host": "10.80.0.1",
            "http_port": 8080,
            "uftp_bind_host": "10.80.0.1",
            "uftp_multicast_host": "239.80.41.1",
            "uftp_private_multicast_host": "239.80.41.2",
            "uftp_rate_kbps": 12000,
            "max_update_size_bytes": 1024,
            "live_observation": True,
            "observation_path": obs_file,
            "io_timeout_seconds": 120,
        }
        cfg_file = os.path.join(role_dir, "server_role.json")
        with open(cfg_file, "w", encoding="utf-8") as f:
            json.dump(server_cfg, f)
        parsed = _read_config(cfg_file)
        self.assertEqual(parsed["io_timeout_seconds"], 120)
        self.assertTrue(parsed["live_observation"])
        self.assertEqual(parsed["observation_path"], obs_file)

        # Verification that ServerDaemon.start_job passes these exact parameters into ServerRole
        daemon_cfg = ServerDaemonConfig(
            ipc_host="127.0.0.1",
            ipc_port=0,
            control_bind_port=self.control_bind_port,
            control_broadcast_port=self.control_broadcast_port,
            work_dir=self.temp_dir,
            enable_link_process=True,
        )
        srv_daemon = ServerDaemon(
            config=daemon_cfg,
            network_adapter=self.adapter,
            survey_backend=self.survey_backend,
        )
        srv_daemon.current_interface = "wlan0"
        srv_daemon.server_state = ServerState.IDLE

        from wfb_ng.fl.control import NodeRecord, NodeReadiness
        srv_daemon.control_plane.registry._nodes[1] = NodeRecord(
            node_id=1,
            tun_ip="10.80.0.11",
            reported_state="IDLE",
            readiness=NodeReadiness.READY,
            elapsed_ms=0,
            current_channel=157,
            txpower_dbm=12,
            uplink_mcs=6,
            last_heartbeat_time=time.monotonic(),
        )

        with mock.patch("wfb_ng.fl.role.ServerRole.start"), \
             mock.patch.object(srv_daemon.control_plane, "wait_for_task_readiness", return_value=True), \
             mock.patch.object(srv_daemon.control_plane, "broadcast_downlink"), \
             mock.patch.object(srv_daemon, "_stop_link_process"), \
             mock.patch.object(srv_daemon, "_start_link_process"):
            res = srv_daemon.start_job(self.valid_job_payload)
            self.assertEqual(res["status"], "accepted")

            # Assert the REAL ServerRole was created on daemon with correct passed parameters
            self.assertIsInstance(srv_daemon.server_role, ServerRole)
            self.assertEqual(srv_daemon.server_role.transport.io_timeout, 120)
            self.assertTrue(srv_daemon.server_role.transport.live_observation)
            expected_role_obs = os.path.join(self.temp_dir, "job_job_stage3_001_role", "observation.jsonl")
            self.assertEqual(srv_daemon.server_role.transport.observation_path, expected_role_obs)
            srv_daemon.abort_job(reason="test_teardown")

    # -------------------------------------------------------------------------
    # Slice 3: TASK_ANNOUNCE propagation & ClientJobConfig strict parsing
    # -------------------------------------------------------------------------
    def test_client_daemon_task_announce_requires_and_propagates_fields(self):
        config = ClientDaemonConfig(
            node_id=1,
            tun_ip="10.80.0.11",
            channel=157,
            work_dir=self.temp_dir,
            enable_control_plane=False,
            enable_link_process=False,
        )
        daemon = ClientDaemon(config, network_adapter=self.adapter)
        daemon.current_interface = "wlan0"

        # Trap triggered job config
        triggered_job: List[ClientJobConfig] = []
        daemon.trigger_job = lambda job_cfg: triggered_job.append(job_cfg)  # type: ignore

        base_announce = {
            "type": "TASK_ANNOUNCE",
            "run_id": "run_test_42",
            "job_id": "job_42",
            "target_nodes": [1],
            "rounds": 2,
            "algorithm": "wfb_ng.fl.issue41_algorithm:client_main",
            "algorithm_config": {},
            "server_http_host": "10.80.0.1",
            "server_http_port": 8080,
            "uftp_port": 1044,
            "link_id": 7669206,
            "io_timeout_seconds": 120,
            "live_observation": True,
        }

        # 1. Missing run_id in announce -> rejected
        msg_bad_run = dict(base_announce)
        del msg_bad_run["run_id"]
        daemon._handle_task_announce(msg_bad_run)
        self.assertEqual(len(triggered_job), 0)

        # 2. Missing io_timeout_seconds -> rejected
        msg_bad_io = dict(base_announce)
        del msg_bad_io["io_timeout_seconds"]
        daemon._handle_task_announce(msg_bad_io)
        self.assertEqual(len(triggered_job), 0)

        # 3. Missing live_observation -> rejected
        msg_bad_obs = dict(base_announce)
        del msg_bad_obs["live_observation"]
        daemon._handle_task_announce(msg_bad_obs)
        self.assertEqual(len(triggered_job), 0)

        # 4. Injected observation_path from server -> strictly ignored and sandboxed
        msg_injected_path = dict(base_announce, observation_path="/etc/passwd")
        daemon._handle_task_announce(msg_injected_path)
        self.assertEqual(len(triggered_job), 1)
        job = triggered_job[0]
        self.assertEqual(job.run_id, "run_test_42")
        self.assertEqual(job.job_id, "job_42")
        self.assertEqual(job.io_timeout_seconds, 120)
        self.assertTrue(job.live_observation)
        # ClientJobConfig has no observation_path field; sandbox controls it strictly
        self.assertFalse(hasattr(job, "observation_path"))

    def test_client_sandbox_generates_fixed_sandboxed_observation_path(self):
        sandbox = JobSandbox(
            work_dir=self.temp_dir,
            network_adapter=self.adapter,
            _command_prefix=["true"],
        )
        job = ClientJobConfig(
            run_id="run_sb_01",
            job_id="job_sb_01",
            node_id=1,
            tun_name="fl-c1",
            tun_ip="10.80.0.11",
            io_timeout_seconds=120,
            live_observation=True,
        )
        sandbox.start(job, air_interface="wlan0")

        # Read generated role configuration
        role_cfg_path = os.path.join(self.temp_dir, "job_job_sb_01", "client_role.json")
        self.assertTrue(os.path.isfile(role_cfg_path))
        with open(role_cfg_path, "r", encoding="utf-8") as f:
            role_cfg = json.load(f)

        self.assertEqual(role_cfg["io_timeout_seconds"], 120)
        self.assertTrue(role_cfg["live_observation"])
        expected_obs_path = os.path.join(self.temp_dir, "job_job_sb_01", "observation.jsonl")
        self.assertEqual(role_cfg["observation_path"], expected_obs_path)
        sandbox.abort()

    # -------------------------------------------------------------------------
    # Slice 4: ServerDaemon abort vs completed vs failed terminal consistency
    # -------------------------------------------------------------------------
    def test_server_daemon_abort_job_resets_link_and_broadcasts_job_abort(self):
        cfg = ServerDaemonConfig(
            ipc_host="127.0.0.1",
            ipc_port=0,
            control_bind_port=self.control_bind_port,
            control_broadcast_port=self.control_broadcast_port,
            work_dir=self.temp_dir,
            enable_link_process=True,
        )
        daemon = ServerDaemon(
            config=cfg,
            network_adapter=self.adapter,
            survey_backend=self.survey_backend,
        )
        daemon.current_interface = "wlan0"

        # Mock coordinator, role, and link reset methods
        mock_coord = mock.Mock()
        mock_role = mock.Mock()
        daemon.coordinator = mock_coord
        daemon.server_role = mock_role
        daemon.active_job = {"job_id": "job_term_abort", "run_id": "run_term_abort"}
        daemon.server_state = ServerState.RUNNING

        broadcasted_messages: List[Dict[str, Any]] = []
        daemon.control_plane.broadcast_downlink = lambda msg: broadcasted_messages.append(msg)

        with mock.patch.object(daemon, "_stop_link_process") as mock_stop_link, \
             mock.patch.object(daemon, "_start_link_process") as mock_start_link:
            res = daemon.abort_job(reason="operator_abort")

        self.assertEqual(res["status"], "aborted")
        self.assertEqual(res["job_id"], "job_term_abort")
        self.assertEqual(daemon.server_state, ServerState.IDLE)
        self.assertIsNone(daemon.active_job)
        self.assertIsNone(daemon.coordinator)
        self.assertIsNone(daemon.server_role)

        # Coordinator and ServerRole properly stopped
        mock_coord.abort.assert_called_once_with(reason="operator_abort")
        mock_role.close.assert_called_once()

        # Link reset strictly performed
        mock_stop_link.assert_called_once()
        mock_start_link.assert_called_once()

        # Downlink broadcast sent with correct job_id
        abort_msgs = [m for m in broadcasted_messages if m.get("type") == "JOB_ABORT"]
        self.assertEqual(len(abort_msgs), 1)
        self.assertEqual(abort_msgs[0]["job_id"], "job_term_abort")
        self.assertEqual(abort_msgs[0]["reason"], "operator_abort")

    def test_server_daemon_abort_handles_link_reset_failure_without_masking(self):
        cfg = ServerDaemonConfig(
            ipc_host="127.0.0.1",
            ipc_port=0,
            control_bind_port=self.control_bind_port,
            control_broadcast_port=self.control_broadcast_port,
            work_dir=self.temp_dir,
            enable_link_process=True,
        )
        daemon = ServerDaemon(
            config=cfg,
            network_adapter=self.adapter,
            survey_backend=self.survey_backend,
        )
        daemon.active_job = {"job_id": "job_fail_reset", "run_id": "run_fail_reset"}
        daemon.server_state = ServerState.RUNNING

        broadcasted_messages: List[Dict[str, Any]] = []
        daemon.control_plane.broadcast_downlink = lambda msg: broadcasted_messages.append(msg)

        with mock.patch.object(daemon, "_stop_link_process"), \
             mock.patch.object(daemon, "_start_link_process", side_effect=OSError("Interface busy")):
            # Must raise FLRuntimeError, fail closed, and not masquerade as IDLE
            with self.assertRaises(FLRuntimeError) as ctx:
                daemon.abort_job(reason="emergency")
            self.assertEqual(ctx.exception.error_code, "link_reset_failed")
            self.assertEqual(daemon.server_state, ServerState.STOPPED)
            # Terminal broadcast MUST NOT be emitted if persistent link reset fails
            self.assertEqual(len(broadcasted_messages), 0)

    def test_client_job_config_strictly_requires_and_validates_fields(self):
        # Empty or trailing newline run_id strictly rejected
        for bad_id in ["", "run\n", "run 1", "../run"]:
            with self.assertRaises(FLRuntimeError) as ctx:
                ClientJobConfig(
                    job_id="job_valid",
                    run_id=bad_id,
                    node_id=1,
                    tun_name="fl-c1",
                    tun_ip="10.80.0.11",
                    io_timeout_seconds=120,
                    live_observation=True,
                )
            self.assertEqual(ctx.exception.error_code, "invalid_run_id")

        # Invalid io_timeout_seconds
        for bad_io in [0, -10, "120", 120.5, None]:
            with self.assertRaises(FLRuntimeError) as ctx:
                ClientJobConfig(
                    job_id="job_valid",
                    run_id="run_valid",
                    node_id=1,
                    tun_name="fl-c1",
                    tun_ip="10.80.0.11",
                    io_timeout_seconds=bad_io,  # type: ignore
                    live_observation=True,
                )
            self.assertEqual(ctx.exception.error_code, "invalid_io_timeout")

        # Invalid live_observation
        for bad_obs in [None, "true", 1, 0]:
            with self.assertRaises(FLRuntimeError) as ctx:
                ClientJobConfig(
                    job_id="job_valid",
                    run_id="run_valid",
                    node_id=1,
                    tun_name="fl-c1",
                    tun_ip="10.80.0.11",
                    io_timeout_seconds=120,
                    live_observation=bad_obs,  # type: ignore
                )
            self.assertEqual(ctx.exception.error_code, "invalid_live_observation")


    def test_server_daemon_stop_cleans_resources_and_stays_stopped(self):
        cfg = ServerDaemonConfig(
            ipc_host="127.0.0.1",
            ipc_port=0,
            control_bind_port=self.control_bind_port,
            control_broadcast_port=self.control_broadcast_port,
            work_dir=self.temp_dir,
            enable_link_process=True,
        )
        daemon = ServerDaemon(
            config=cfg,
            network_adapter=self.adapter,
            survey_backend=self.survey_backend,
        )
        daemon.active_job = {"job_id": "job_to_stop"}
        daemon.server_state = ServerState.RUNNING
        mock_coord = mock.Mock()
        mock_role = mock.Mock()
        daemon.coordinator = mock_coord
        daemon.server_role = mock_role

        with mock.patch.object(daemon, "_start_link_process") as mock_start_link, \
             mock.patch.object(daemon, "_stop_link_process") as mock_stop_link:
            daemon.stop()

        self.assertEqual(daemon.server_state, ServerState.STOPPED)
        self.assertIsNone(daemon.active_job)
        self.assertIsNone(daemon.coordinator)
        self.assertIsNone(daemon.server_role)
        mock_coord.abort.assert_called_once_with(reason="daemon_stopped")
        mock_role.close.assert_called_once()
        mock_stop_link.assert_called_once()
        mock_start_link.assert_not_called()

    def test_finalize_job_ignores_stale_job_id_callbacks(self):
        cfg = ServerDaemonConfig(
            ipc_host="127.0.0.1",
            ipc_port=0,
            control_bind_port=self.control_bind_port,
            control_broadcast_port=self.control_broadcast_port,
            work_dir=self.temp_dir,
            enable_link_process=False,
        )
        daemon = ServerDaemon(
            config=cfg,
            network_adapter=self.adapter,
            survey_backend=self.survey_backend,
        )
        daemon.active_job = {"job_id": "current_active_job"}
        daemon.server_state = ServerState.RUNNING

        # Late callback from an older job
        res = daemon._finalize_job(outcome="completed", job_id="stale_old_job")
        self.assertEqual(res["status"], "ignored")
        self.assertEqual(daemon.server_state, ServerState.RUNNING)
        self.assertIsNotNone(daemon.active_job)
        self.assertEqual(daemon.active_job["job_id"], "current_active_job")

    def test_on_job_completed_and_failed_cleanup_terminal_state(self):
        cfg = ServerDaemonConfig(
            ipc_host="127.0.0.1",
            ipc_port=0,
            control_bind_port=self.control_bind_port,
            control_broadcast_port=self.control_broadcast_port,
            work_dir=self.temp_dir,
            enable_link_process=False,
        )
        daemon = ServerDaemon(
            config=cfg,
            network_adapter=self.adapter,
            survey_backend=self.survey_backend,
        )
        # Test completion cleans active_job, server_role, and coordinator
        daemon.active_job = {"job_id": "job_comp"}
        daemon.server_state = ServerState.RUNNING
        daemon.coordinator = mock.Mock()
        mock_role = mock.Mock()
        daemon.server_role = mock_role
        daemon._on_job_completed("job_comp", {"schema_version": 1})
        self.assertIsNone(daemon.active_job)
        self.assertIsNone(daemon.server_role)
        self.assertIsNone(daemon.coordinator)
        self.assertEqual(daemon.server_state, ServerState.IDLE)
        mock_role.close.assert_called_once()

        # Test failure cleans active_job, server_role, and coordinator
        daemon.active_job = {"job_id": "job_fail"}
        daemon.server_state = ServerState.RUNNING
        daemon.coordinator = mock.Mock()
        mock_role2 = mock.Mock()
        daemon.server_role = mock_role2
        daemon._on_job_failed("job_fail", RuntimeError("simulated error"))
        self.assertIsNone(daemon.active_job)
        self.assertIsNone(daemon.server_role)
        self.assertIsNone(daemon.coordinator)
        self.assertEqual(daemon.server_state, ServerState.IDLE)
        mock_role2.close.assert_called_once()

    def test_finalize_job_ignores_when_active_job_none_or_server_stopped(self):
        cfg = ServerDaemonConfig(
            ipc_host="127.0.0.1",
            ipc_port=0,
            control_bind_port=self.control_bind_port,
            control_broadcast_port=self.control_broadcast_port,
            work_dir=self.temp_dir,
            enable_link_process=True,
        )
        daemon = ServerDaemon(
            config=cfg,
            network_adapter=self.adapter,
            survey_backend=self.survey_backend,
        )
        daemon.server_state = ServerState.STOPPED
        daemon.active_job = None

        with mock.patch.object(daemon, "_start_link_process") as mock_start:
            res = daemon._finalize_job(outcome="completed", job_id="job_late")
            self.assertEqual(res["status"], "ignored")
            self.assertEqual(daemon.server_state, ServerState.STOPPED)
            mock_start.assert_not_called()

        daemon.server_state = ServerState.IDLE
        daemon.active_job = None
        with mock.patch.object(daemon, "_start_link_process") as mock_start:
            res = daemon._finalize_job(outcome="completed", job_id="job_late_2")
            self.assertEqual(res["status"], "ignored")
            self.assertEqual(daemon.server_state, ServerState.IDLE)
            mock_start.assert_not_called()

    def test_daemon_stop_still_cleans_up_after_link_reset_failure(self):
        cfg = ServerDaemonConfig(
            ipc_host="127.0.0.1",
            ipc_port=0,
            control_bind_port=self.control_bind_port,
            control_broadcast_port=self.control_broadcast_port,
            work_dir=self.temp_dir,
            enable_link_process=True,
        )
        daemon = ServerDaemon(
            config=cfg,
            network_adapter=self.adapter,
            survey_backend=self.survey_backend,
        )
        daemon.active_job = {"job_id": "job_reset_fail"}
        daemon.server_state = ServerState.RUNNING

        # Simulate link reset failure setting state to STOPPED
        with mock.patch.object(daemon, "_stop_link_process"), \
             mock.patch.object(daemon, "_start_link_process", side_effect=OSError("Interface busy")):
            with self.assertRaises(FLRuntimeError):
                daemon._finalize_job(outcome="aborted", reason="err")

        self.assertEqual(daemon.server_state, ServerState.STOPPED)
        # Even though server_state is STOPPED, calling stop() cleans HTTP, control plane, etc.
        with mock.patch.object(daemon.control_plane, "stop") as mock_cp_stop:
            daemon.stop()
            mock_cp_stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
