"""Server daemon infrastructure checks after the real-training host migration.

Training execution, explicit algorithm deployment, preparation reports, round
barriers, and cooperative evaluation cancellation live in test_fl_training_host.
This module keeps the server-owned radio, topology, rejection, lifecycle, and
packaging seams without reviving the old file-simulation job contract.
"""

import json
import os
from dataclasses import replace
import socket
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from wfb_ng.fl.control import (
    ClientNodeState,
    HeartbeatAck,
    NodeHeartbeat,
)
from wfb_ng.fl.errors import FLRuntimeError
from wfb_ng.fl.server_daemon import (
    ServerDaemon,
    ServerDaemonConfig,
    ServerState,
    build_v6_uplink_server_command,
    load_server_config,
)
from wfb_ng.tests.test_fl_client_daemon import MockNetworkAdapter
from wfb_ng.tests.test_fl_radio import MockSurveyBackend
from wfb_ng.tests.test_fl_training_host import isolated_socket_boundary


def confirm_hunting(test, client, address, heartbeat):
    """使用同一回环 UDP 端点回显公共 challenge，保持 boot_id 和时间戳。"""
    client.settimeout(1.0)
    client.sendto(heartbeat.to_bytes(), address)
    challenge = HeartbeatAck.from_bytes(client.recv(4096))
    test.assertFalse(challenge.confirmed)
    test.assertEqual(challenge.boot_id, heartbeat.boot_id)
    heartbeat = replace(heartbeat, session=challenge.session)
    client.sendto(heartbeat.to_bytes(), address)
    confirmed = HeartbeatAck.from_bytes(client.recv(4096))
    test.assertTrue(confirmed.confirmed)
    test.assertEqual(confirmed.session, challenge.session)
    return heartbeat


class TestServerDaemonConfig(unittest.TestCase):
    """Deployment defaults, explicit algorithm selection, and radio validation."""

    def test_default_config(self):
        cfg = ServerDaemonConfig()
        self.assertEqual((cfg.grant_duration_ms, cfg.guard_interval_ms), (10, 10))
        self.assertEqual(cfg.ipc_host, "127.0.0.1")
        self.assertEqual(cfg.ipc_port, 9090)
        self.assertEqual(cfg.channel, 157)
        self.assertEqual(cfg.radio_txpower_dbm, 18)
        self.assertEqual(cfg.downlink_mcs, 3)
        self.assertEqual(cfg.uplink_mcs, 6)
        self.assertEqual(cfg.tun_name, "fl-s")
        self.assertEqual(cfg.tun_ip, "10.80.0.1")
        self.assertEqual(cfg.tun_cidr, "10.80.0.1/24")
        self.assertEqual(cfg.tun_txqueuelen, 5000)
        self.assertEqual(cfg.known_clients, tuple(range(1, 11)))
        self.assertEqual(cfg.client_entry, "wfb_ng.fl.mnist:client_entry")
        self.assertEqual(cfg.aggregate_entry, "wfb_ng.fl.mnist:aggregate_entry")
        self.assertEqual(cfg.evaluate_entry, "wfb_ng.fl.mnist:evaluate_entry")
        self.assertEqual(cfg.algorithm_config, {"data_seed": 2026})

    def test_load_from_json_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as stream:
            json.dump(
                {
                    "ipc_host": "127.0.0.1",
                    "ipc_port": 9099,
                    "channel": 149,
                    "radio_txpower_dbm": 15,
                    "downlink_mcs": 4,
                    "uplink_mcs": 5,
                    "tun_name": "fl-custom",
                },
                stream,
            )
            temp_path = stream.name

        try:
            cfg = load_server_config(temp_path)
            self.assertEqual(cfg.ipc_port, 9099)
            self.assertEqual(cfg.channel, 149)
            self.assertEqual(cfg.radio_txpower_dbm, 15)
            self.assertEqual(cfg.downlink_mcs, 4)
            self.assertEqual(cfg.uplink_mcs, 5)
            self.assertEqual(cfg.tun_name, "fl-custom")
            self.assertEqual(cfg.known_clients, tuple(range(1, 11)))
            self.assertEqual(cfg.algorithm_config, {"data_seed": 2026})
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_schedule_config_load_and_reject(self):
        # 通过真实 JSON 入口验证，布尔值、字符串和浮点数不得被强转成整数。
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "server.json")
            for grant, guard in ((10, 10), (15, 5), (18, 2), (120, 0)):
                with self.subTest(grant=grant, guard=guard):
                    with open(path, "w") as stream:
                        json.dump(dict(grant_duration_ms=grant, guard_interval_ms=guard), stream)
                    cfg = load_server_config(path)
                    self.assertEqual((cfg.grant_duration_ms, cfg.guard_interval_ms), (grant, guard))
            for name, values in (
                ("grant_duration_ms", (True, False, "15", 15.0, None, 0, -1, 121)),
                ("guard_interval_ms", (True, False, "5", 5.0, None, -1, 11)),
            ):
                for value in values:
                    with self.subTest(name=name, value=value):
                        with open(path, "w") as stream:
                            json.dump({name: value}, stream)
                        with self.assertRaises(FLRuntimeError):
                            load_server_config(path)
                        with self.assertRaises(FLRuntimeError):
                            ServerDaemonConfig(**{name: value})

    def test_load_nonexistent_custom_config_fails_closed(self):
        with self.assertRaises(FLRuntimeError) as caught:
            load_server_config("/nonexistent/path/server.json")
        self.assertEqual(caught.exception.error_code, "config_not_found")

    def test_fail_closed_on_invalid_radio_parameters(self):
        with self.assertRaises(FLRuntimeError):
            ServerDaemonConfig(channel=161)
        with self.assertRaises(FLRuntimeError):
            ServerDaemonConfig(radio_txpower_dbm=0)
        with self.assertRaises(FLRuntimeError):
            ServerDaemonConfig(radio_txpower_dbm=64)
        with self.assertRaises(FLRuntimeError):
            ServerDaemonConfig(downlink_mcs=2)
        with self.assertRaises(FLRuntimeError):
            ServerDaemonConfig(downlink_mcs=7)
        with self.assertRaises(FLRuntimeError) as caught:
            ServerDaemonConfig(known_clients=(1, 2, 3))
        self.assertEqual(caught.exception.error_code, "invalid_known_clients")

    def test_invalid_deployment_algorithm_entry_fails_closed(self):
        with self.assertRaises(FLRuntimeError) as caught:
            ServerDaemonConfig(client_entry="not-an-entry")
        self.assertEqual(caught.exception.error_code, "invalid_configuration")


class TestV6UplinkServerCommand(unittest.TestCase):
    """Pre-allocated slot command construction remains server-owned."""

    def test_configurable_schedule_flags(self):
        # 默认与候选值均检查真实 builder 产出的命令，不推断实际调度周期。
        for grant, guard in ((10, 10), (15, 5), (18, 2), (120, 0)):
            with self.subTest(grant=grant, guard=guard):
                cmd = build_v6_uplink_server_command(
                    "/usr/bin/wfb_v6_uplink", "fl-s", "10.80.0.1/24", "wlan0",
                    grant_duration_ms=grant, guard_interval_ms=guard,
                )
                self.assertEqual(cmd[cmd.index("--grant-duration-ms") + 1], str(grant))
                self.assertEqual(cmd[cmd.index("--guard-interval-ms") + 1], str(guard))

    def test_builder_rejects_invalid_schedule(self):
        for name, values in (
            ("grant_duration_ms", (True, False, "15", 15.0, None, 0, -1, 121)),
            ("guard_interval_ms", (True, False, "5", 5.0, None, -1, 11)),
        ):
            for value in values:
                with self.subTest(name=name, value=value):
                    with self.assertRaises(FLRuntimeError):
                        build_v6_uplink_server_command(
                            "/usr/bin/wfb_v6_uplink", "fl-s", "10.80.0.1/24", "wlan0",
                            **{name: value},
                        )

    def test_start_link_passes_loaded_schedule_to_real_builder(self):
        # 仅替代进程和硬件边界，保留配置加载及真实命令生成路径。
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "server.json")
            with open(path, "w") as stream:
                json.dump(dict(grant_duration_ms=18, guard_interval_ms=2, work_dir=directory), stream)
            daemon = object.__new__(ServerDaemon)
            daemon.config = load_server_config(path)
            daemon.radio_config = MagicMock(channel=157, downlink_mcs=3, bandwidth_mhz=40)
            daemon.current_interface = "wlan0"
            daemon.adapter = MagicMock()
            daemon.adapter.is_tun_ready.return_value = True
            daemon.control_plane = MagicMock()
            with patch("wfb_ng.fl.server_daemon.shutil.which", return_value="/usr/bin/wfb_v6_uplink"), \
                    patch("wfb_ng.fl.server_daemon.subprocess.Popen") as popen:
                popen.return_value.poll.return_value = None
                try:
                    daemon._start_link_process()
                    cmd = popen.call_args.args[0]
                    self.assertEqual(cmd[cmd.index("--grant-duration-ms") + 1], "18")
                    self.assertEqual(cmd[cmd.index("--guard-interval-ms") + 1], "2")
                finally:
                    if getattr(daemon, "_link_log_file", None):
                        daemon._link_log_file.close()

    def test_command_generation_preallocated_slots_and_fixed_slot_flags(self):
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
        self.assertEqual(cmd[cmd.index("--role") + 1], "server")
        self.assertEqual(cmd[cmd.index("--tun-name") + 1], "fl-s")
        self.assertEqual(cmd[cmd.index("--tun-addr") + 1], "10.80.0.1/24")
        self.assertEqual(cmd[cmd.index("--node-id") + 1], "255")
        self.assertEqual(cmd[cmd.index("--link-id") + 1], "7669206")
        self.assertEqual(cmd[cmd.index("--air-interface") + 1], "wlan0")
        self.assertEqual(cmd[cmd.index("--fec-k") + 1], "8")
        self.assertEqual(cmd[cmd.index("--fec-n") + 1], "14")
        self.assertEqual(cmd[cmd.index("--radio-bandwidth") + 1], "40")
        self.assertEqual(cmd[cmd.index("--radio-mcs-index") + 1], "3")
        self.assertNotIn("--radio-short-gi", cmd)
        self.assertEqual(
            cmd[cmd.index("--known-clients") + 1],
            "1,2,3,4,5,6,7,8,9,10",
        )
        for node_id in range(1, 11):
            self.assertIn(f"{node_id}:10.80.0.{10 + node_id}:127.0.0.1:1", cmd)
        self.assertEqual(cmd[cmd.index("--grant-duration-ms") + 1], "10")
        self.assertEqual(cmd[cmd.index("--guard-interval-ms") + 1], "10")

    def test_channel_165_requires_explicit_narrow_bandwidth(self):
        with self.assertRaises(ValueError):
            build_v6_uplink_server_command(
                "/usr/bin/wfb_v6_uplink", "fl-s", "10.80.0.1/24", "wlan0", channel=165
            )
        cmd = build_v6_uplink_server_command(
            executable_path="/usr/bin/wfb_v6_uplink",
            tun_name="fl-s",
            tun_addr="10.80.0.1/24",
            air_interface="wlan0",
            channel=165,
            bandwidth_mhz=20,
            downlink_mcs=3,
        )
        self.assertEqual(cmd[cmd.index("--radio-bandwidth") + 1], "20")
        self.assertNotIn("--radio-short-gi", cmd)


class TestServerRadioIsolation(unittest.TestCase):
    def test_no_radio_mode_does_not_discover_or_configure_interfaces(self):
        adapter = MockNetworkAdapter(["wlP1p1s0"])
        daemon = ServerDaemon(
            ServerDaemonConfig(
                ipc_port=0,
                air_interface="",
                enable_link_process=False,
                enable_control_plane=False,
            ),
            network_adapter=adapter,
        )

        daemon.start()
        try:
            self.assertIsNone(daemon.current_interface)
            self.assertEqual(adapter.wireless_states, {})
        finally:
            daemon.stop()

    def test_no_radio_mode_does_not_configure_explicit_wfb_interface(self):
        adapter = MockNetworkAdapter(["wlx-dedicated"])
        daemon = ServerDaemon(
            ServerDaemonConfig(
                ipc_port=0,
                air_interface="wlx-dedicated",
                enable_link_process=False,
                enable_control_plane=False,
            ),
            network_adapter=adapter,
        )

        daemon.start()
        try:
            self.assertEqual(daemon.current_interface, "wlx-dedicated")
            self.assertEqual(adapter.wireless_states, {})
        finally:
            daemon.stop()

    def test_no_radio_mode_rejects_explicit_non_wfb_interface(self):
        adapter = MockNetworkAdapter(["wlx-dedicated"])
        daemon = ServerDaemon(
            ServerDaemonConfig(
                air_interface="wlP1p1s0",
                enable_link_process=False,
                enable_control_plane=False,
            ),
            network_adapter=adapter,
        )

        with self.assertRaises(FLRuntimeError) as caught:
            daemon.start()

        self.assertEqual(caught.exception.error_code, "radio_interface_unavailable")
        self.assertEqual(adapter.wireless_states, {})

    def test_explicit_non_wfb_interface_is_rejected_before_configuration(self):
        adapter = MockNetworkAdapter(["wlx-dedicated"])
        daemon = ServerDaemon(
            ServerDaemonConfig(
                air_interface="wlP1p1s0",
                enable_link_process=False,
            ),
            network_adapter=adapter,
        )

        with self.assertRaises(FLRuntimeError) as caught:
            daemon.start()

        self.assertEqual(caught.exception.error_code, "radio_interface_unavailable")
        self.assertEqual(adapter.wireless_states, {})

    def test_survey_without_wfb_interface_does_not_fall_back_to_wlan0(self):
        daemon = ServerDaemon(
            ServerDaemonConfig(
                ipc_port=0,
                enable_link_process=False,
                enable_control_plane=False,
            ),
            network_adapter=MockNetworkAdapter(),
        )
        daemon.start()
        try:
            with self.assertRaises(FLRuntimeError) as caught:
                daemon.run_spectrum_survey()
            self.assertEqual(caught.exception.error_code, "radio_interface_unavailable")
        finally:
            daemon.stop()


class TestReportReadinessConcurrency(unittest.TestCase):
    """Heartbeat processing must not block a real TASK_READY report."""

    def test_preparing_heartbeat_does_not_block_report_under_daemon_lock(self):
        with tempfile.TemporaryDirectory() as root:
            daemon = ServerDaemon(
                ServerDaemonConfig(
                    control_bind_host="127.0.0.1",
                    control_bind_port=0,
                    control_broadcast_addr="127.0.0.1",
                    control_broadcast_port=0,
                    enable_link_process=False,
                    model_library_dir=root,
                ),
                network_adapter=MockNetworkAdapter(),
            )
            control = daemon.control_plane
            address_fixture = ("127.0.0.1", 50001)
            hunting = NodeHeartbeat(
                node_id=1, state="HUNTING", elapsed_ms=0,
                current_channel=daemon.config.channel,
                txpower_dbm=daemon.config.radio_txpower_dbm,
                uplink_mcs=daemon.config.uplink_mcs, timestamp_ms=998,
            )
            # 先完成身份握手和 IDLE；下方仅观察 PREPARING 的真实业务回调。
            challenge = HeartbeatAck.from_bytes(
                control.handle_datagram(hunting.to_bytes(), address_fixture)[0])
            self.assertFalse(challenge.confirmed)
            self.assertIsNone(control.registry.get_node(1))
            hunting = replace(hunting, session=challenge.session)
            confirmed = HeartbeatAck.from_bytes(
                control.handle_datagram(hunting.to_bytes(), address_fixture)[0])
            self.assertTrue(confirmed.confirmed)
            idle = replace(hunting, state="IDLE", timestamp_ms=999)
            control.handle_datagram(idle.to_bytes(), address_fixture)
            entered_callback = threading.Event()
            original_callback = control.on_heartbeat_received

            def on_heartbeat(hb, record):
                entered_callback.set()
                original_callback(hb, record)

            control.on_heartbeat_received = on_heartbeat
            diagnostic = daemon.event_bus.subscribe()
            control.start()
            try:
                address = control._server_sock.getsockname()
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
                    client.settimeout(1.0)
                    control.prepare_task_readiness("startup-job", [1])
                    heartbeat = NodeHeartbeat(
                        node_id=1,
                        state="PREPARING",
                        elapsed_ms=0,
                        current_channel=daemon.config.channel,
                        txpower_dbm=daemon.config.radio_txpower_dbm,
                        uplink_mcs=daemon.config.uplink_mcs,
                        timestamp_ms=1000,
                        boot_id=hunting.boot_id, session=hunting.session,
                    )
                    with daemon._lock:
                        client.sendto(heartbeat.to_bytes(), address)
                        self.assertTrue(entered_callback.wait(1.0))
                        client.sendto(
                            json.dumps(
                                {
                                    "type": "TASK_READY",
                                    "report_id": "startup-report-1",
                                    "job_id": "startup-job",
                                    "node_id": 1,
                                    "report": {
                                        "model_format": "reference",
                                        "sample_count": 1,
                                        "data_sha256": {},
                                        "device": {"type": "cpu"},
                                    },
                                }
                            ).encode(),
                            address,
                        )
                        self.assertTrue(
                            control.wait_for_task_readiness(1.0),
                            "PREPARING diagnostic blocked TASK_READY report reception",
                        )
                        replies = [json.loads(client.recv(4096)) for _ in range(2)]
                        self.assertIn({
                            "ack": True,
                            "heartbeat_timestamp_ms": 1000,
                            "channel": 157,
                            "boot_id": hunting.boot_id,
                            "session": hunting.session,
                            "confirmed": True,
                        }, replies)
                        report_reply = next(reply for reply in replies if reply.get("type") == "TASK_READY_ACK")
                        self.assertTrue(report_reply["ok"])
                        event = diagnostic.get(timeout=1.0)
                        self.assertEqual(event["type"], "NODE_HEARTBEAT")
                        self.assertEqual(event["state"], "PREPARING")
                    state = daemon.console.snapshot()
                    self.assertEqual(state["nodes"][0]["state"], "preparing")
                    self.assertEqual(state["events"], [])
            finally:
                control.stop()


class TestServerDaemonRestIPCAndPreflight(unittest.TestCase):
    """Server-owned status, rejection gates, radio and lifecycle seams."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="wfb_test_server_")
        self.adapter = MockNetworkAdapter(["wlan0"])
        self.survey_backend = MockSurveyBackend({149: 5, 153: 0, 157: 12, 165: 25})
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind(("127.0.0.1", 0))
            self.control_bind_port = sock.getsockname()[1]
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind(("127.0.0.1", 0))
            self.control_broadcast_port = sock.getsockname()[1]

        self.config = ServerDaemonConfig(
            ipc_host="127.0.0.1",
            ipc_port=0,
            channel=157,
            radio_txpower_dbm=12,
            downlink_mcs=3,
            uplink_mcs=6,
            control_bind_host="127.0.0.1",
            control_bind_port=self.control_bind_port,
            control_broadcast_addr="127.0.0.1",
            control_broadcast_port=self.control_broadcast_port,
            work_dir=self.temp_dir,
            enable_link_process=False,
        )
        self.daemon = ServerDaemon(
            config=self.config,
            network_adapter=self.adapter,
            survey_backend=self.survey_backend,
        )
        self.daemon.start()
        self.model_path = os.path.join(self.temp_dir, "initial_model.bin")
        self.model_data = b"M" * 1024
        with open(self.model_path, "wb") as stream:
            stream.write(self.model_data)

    def tearDown(self):
        self.daemon.stop()
        if os.path.exists(self.temp_dir):
            import shutil
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _simulate_client_handshake(self, node_id: int):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        address = ("127.0.0.1", self.control_bind_port)
        try:
            hunting = NodeHeartbeat(
                node_id=node_id, state=ClientNodeState.HUNTING.value,
                elapsed_ms=0, current_channel=157, txpower_dbm=12,
                uplink_mcs=6, timestamp_ms=1000,
            )
            hunting = confirm_hunting(self, sock, address, hunting)
            idle = replace(hunting, state=ClientNodeState.IDLE.value, timestamp_ms=1050)
            sock.sendto(idle.to_bytes(), address)
            self.assertTrue(HeartbeatAck.from_bytes(sock.recv(4096)).confirmed)
        finally:
            sock.close()

    def _job_payload(self, **overrides):
        payload = {
            "run_id": "run-test",
            "job_id": "job-test",
            "mode": "sync",
            "target_nodes": [1],
            "model_path": self.model_path,
            "model_size_bytes": len(self.model_data),
            "io_timeout_seconds": 2,
            "live_observation": False,
        }
        payload.update(overrides)
        return payload

    def test_terminal_job_preserves_persistent_link_and_duplicate_does_not_reset(self):
        daemon = ServerDaemon(
            config=ServerDaemonConfig(
                work_dir=self.temp_dir,
                enable_link_process=True,
                enable_control_plane=False,
            ),
            network_adapter=self.adapter,
            survey_backend=self.survey_backend,
        )
        target_nodes = [1, 2, 4]
        daemon.control_plane.prepare_task_readiness("terminal-reset", target_nodes)
        daemon.active_job = {
            "job_id": "terminal-reset", "target_nodes": target_nodes,
            "rounds_completed": 0, "io_timeout_seconds": 1,
        }
        daemon.server_state = ServerState.RUNNING
        role = MagicMock()
        daemon.server_role = role
        calls = []
        role.close.side_effect = lambda: calls.append("role_close")

        def deliver_terminal(message):
            # 投递边界模拟本作业全部客户端，恢复事实仍由真实控制面协议校验。
            calls.append("terminal_broadcast")
            for node_id in target_nodes:
                replies = daemon.control_plane.handle_datagram(json.dumps({
                    "type": "JOB_RECOVERY", "job_id": message["job_id"],
                    "node_id": node_id, "report_id": f"terminal-recovery-{node_id}",
                    "report": {"recovery_epoch": 1, "recovery_state": "ready"},
                }).encode(), ("127.0.0.1", node_id))
                self.assertTrue(json.loads(replies[0])["ok"])

        with patch.object(daemon, "_stop_link_process", side_effect=lambda: calls.append("link_stop")), \
                patch.object(daemon, "_start_link_process", side_effect=lambda: calls.append("link_start")), \
                patch.object(daemon.control_plane, "broadcast_downlink", side_effect=deliver_terminal) as broadcast:
            final_summary = {'rounds_completed': 5, 'final_model_sha256': 'a' * 64,
                             'execution_result': 'succeeded'}
            first = daemon._finalize_job(outcome="completed", job_id="terminal-reset", summary=final_summary)
            duplicate = daemon._finalize_job(outcome="completed", job_id="terminal-reset")

        self.assertEqual(duplicate, first)
        self.assertEqual(duplicate['status'], 'completed')
        self.assertEqual(daemon.get_status_report()['recent_job']['summary'], final_summary)
        self.assertCountEqual(calls, ["role_close", "terminal_broadcast"])
        self.assertEqual(daemon.get_status_report()['recent_job']['recovery_state'], 'ready')
        self.assertEqual(broadcast.call_args.args[0]["job_id"], "terminal-reset")
        self.assertEqual(daemon.server_state, ServerState.IDLE)
        self.assertIsNone(daemon.active_job)
        self.assertIsNone(daemon.server_role)

    def test_completed_terminal_requires_explicit_execution_summary(self):
        self.daemon.active_job = {'job_id': 'missing-summary'}
        self.daemon.server_state = ServerState.RUNNING
        for summary in (None, {}, {'rounds_completed': 5}):
            with self.subTest(summary=summary), self.assertRaises(FLRuntimeError) as raised:
                self.daemon._finalize_job(outcome='completed', job_id='missing-summary', summary=summary)
            self.assertEqual(raised.exception.error_code, 'invalid_coordinator_summary')
            self.assertIsNotNone(self.daemon.active_job)

    def test_status_snapshot_returns_topology_and_radio(self):
        body = self.daemon.get_status_report()
        self.assertEqual(body["server_state"], "IDLE")
        self.assertIsNone(body["active_job"])
        self.assertEqual(body["radio"]["channel"], 157)
        self.assertEqual(body["radio"]["radio_txpower_dbm"], 12)
        self.assertEqual(body["radio"]["downlink_mcs"], 3)
        self.assertEqual(body["radio"]["uplink_mcs"], 6)
        self.assertEqual(body["tun"]["name"], "fl-s")
        self.assertEqual(body["tun"]["ip"], "10.80.0.1")

        self._simulate_client_handshake(1)
        self._simulate_client_handshake(2)
        body = self.daemon.get_status_report()
        nodes = body["nodes"]
        self.assertEqual(len(nodes), 10)
        self.assertEqual(nodes["1"]["readiness"], "READY")
        self.assertEqual(nodes["1"]["reported_state"], "IDLE")
        self.assertEqual(nodes["2"]["readiness"], "READY")
        self.assertEqual(nodes["2"]["reported_state"], "IDLE")
        self.assertEqual(nodes["3"]["readiness"], "OFFLINE")
        self.assertEqual(nodes["3"]["tun_ip"], "10.80.0.13")

    def test_survey_snapshot_returns_ranking(self):
        report = self.daemon.run_spectrum_survey(duration_ms=300)
        self.assertEqual(report.recommended_channel, 153)

    def test_target_nodes_not_ready_is_rejected_before_training_host(self):
        self._simulate_client_handshake(1)
        with self.assertRaises(FLRuntimeError) as caught:
            self.daemon.start_job(
                self._job_payload(target_nodes=[1, 2], job_id="job-target-not-ready")
            )
        self.assertEqual(caught.exception.error_code, "preflight_target_nodes_not_ready")
        self.assertIn("2", caught.exception.details["unready_nodes"])

    def test_hunting_node_is_not_a_training_candidate(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            heartbeat = NodeHeartbeat(
                node_id=1, state=ClientNodeState.HUNTING.value, elapsed_ms=0,
                current_channel=157, txpower_dbm=12, uplink_mcs=6,
                timestamp_ms=407,
            )
            confirm_hunting(self, sock, ("127.0.0.1", self.control_bind_port), heartbeat)
        finally:
            sock.close()
        with self.assertRaises(FLRuntimeError) as caught:
            self.daemon.start_job(self._job_payload(job_id="job-hunting"))
        self.assertEqual(caught.exception.error_code, "preflight_target_nodes_not_ready")
        self.assertEqual(caught.exception.details["unready_nodes"]["1"]["readiness"], "CONNECTING")

    def test_timed_out_node_is_rejected(self):
        self._simulate_client_handshake(1)
        self.daemon.control_plane.registry.get_node(1).last_heartbeat_time -= 12.0
        with self.assertRaises(FLRuntimeError) as caught:
            self.daemon.start_job(self._job_payload(job_id="job-offline"))
        self.assertEqual(caught.exception.error_code, "preflight_target_nodes_not_ready")
        self.assertEqual(caught.exception.details["unready_nodes"]["1"]["readiness"], "OFFLINE")

    def test_model_file_validation_rejects_before_training_host(self):
        self._simulate_client_handshake(1)
        cases = [
            ("missing", os.path.join(self.temp_dir, "missing.bin"), 1024, "preflight_model_file_not_found"),
            ("empty", os.path.join(self.temp_dir, "empty.bin"), 1024, "preflight_model_empty"),
            ("size", self.model_path, 999999, "preflight_model_validation_failed"),
        ]
        open(cases[1][1], "wb").close()
        for name, path, size, expected in cases:
            with self.subTest(name=name):
                with self.assertRaises(FLRuntimeError) as caught:
                    self.daemon.start_job(
                        self._job_payload(
                            job_id=f"job-model-{name}",
                            model_path=path,
                            model_size_bytes=size,
                        )
                    )
                self.assertEqual(caught.exception.error_code, expected)

        with self.assertRaises(FLRuntimeError) as caught:
            self.daemon.start_job(
                self._job_payload(job_id="job-model-sha", model_sha256="0" * 64)
            )
        self.assertEqual(caught.exception.error_code, "preflight_model_checksum_failed")

    def test_busy_engine_rejects_new_job_and_survey_without_fake_acceptance(self):
        self.daemon.active_job = {"job_id": "already-running"}
        self.daemon.server_state = ServerState.RUNNING
        with self.assertRaises(FLRuntimeError) as caught:
            self.daemon.start_job(self._job_payload())
        self.assertEqual(caught.exception.error_code, "preflight_engine_conflict")
        with self.assertRaises(FLRuntimeError) as caught:
            self.daemon.run_spectrum_survey()
        self.assertEqual(caught.exception.error_code, "engine_busy")

    def test_old_algorithm_and_partial_mode_fields_are_rejected(self):
        cases = [
            {"algorithm": "legacy:client"},
            {"algorithm_config": {"file_simulation": True}},
            {"client_entry": "legacy:client"},
            {"aggregate_entry": "legacy:aggregate"},
            {"evaluate_entry": "legacy:evaluate"},
            {"min_updates": 1},
            {"max_staleness": 1},
            {"mode": "semi_async"},
            {"mode": "async"},
        ]
        for extra in cases:
            with self.subTest(extra=extra):
                with self.assertRaises(FLRuntimeError) as caught:
                    self.daemon.start_job(self._job_payload(**extra))
                self.assertEqual(caught.exception.error_code, "invalid_job_config")
        self.assertIsNone(self.daemon.active_job)

    def test_unsolicited_idle_does_not_update_registry(self):
        # 未握手 IDLE 不应创建节点，也不能进入训练候选列表。
        replies = self.daemon.control_plane.handle_datagram(
            NodeHeartbeat(
                node_id=3, state=ClientNodeState.IDLE.value, elapsed_ms=0,
                current_channel=157, txpower_dbm=12, uplink_mcs=6,
                timestamp_ms=640,
            ).to_bytes(), ("127.0.0.1", 50003),
        )
        self.assertFalse(HeartbeatAck.from_bytes(replies[0]).confirmed)
        self.assertIsNone(self.daemon.control_plane.registry.get_node(3))
        with self.assertRaises(FLRuntimeError) as caught:
            self.daemon.start_job(
                self._job_payload(target_nodes=[3], job_id="job-unsolicited")
            )
        self.assertEqual(caught.exception.error_code, "preflight_target_nodes_not_ready")
        self.assertEqual(caught.exception.details["unready_nodes"]["3"]["readiness"], "OFFLINE")

    def test_uftp_control_port_conflict_is_rejected(self):
        self._simulate_client_handshake(1)
        with self.assertRaises(FLRuntimeError) as caught:
            self.daemon.start_job(
                self._job_payload(
                    job_id="job-port-conflict",
                    uftp_port=self.config.control_broadcast_port,
                )
            )
        self.assertEqual(caught.exception.error_code, "preflight_port_conflict")
        self.assertEqual(self.daemon.server_state, ServerState.IDLE)

    def test_invalid_target_nodes_payloads_are_rejected(self):
        for name, targets in (("empty", []), ("range", [1, 99]), ("type", ["client1"])):
            with self.subTest(name=name):
                with self.assertRaises(FLRuntimeError) as caught:
                    self.daemon.start_job(
                        self._job_payload(job_id=f"job-invalid-{name}", target_nodes=targets)
                    )
                self.assertEqual(caught.exception.error_code, "preflight_target_nodes_invalid")

class TestSystemdUnitAndPackage(unittest.TestCase):
    def test_systemd_service_file_exists_and_configured(self):
        path = "scripts/systemd/wfb-fl-server-daemon.service"
        self.assertTrue(os.path.exists(path))
        with open(path, encoding="utf-8") as stream:
            content = stream.read()
        self.assertIn("ExecStart=/usr/bin/wfb-fl-server-daemon", content)
        self.assertIn("Restart=always", content)
        self.assertIn("KillMode=control-group", content)

    def test_setup_py_contains_server_daemon_entry_and_data_files(self):
        with open("setup.py", encoding="utf-8") as stream:
            content = stream.read()

        self.assertIn("'wfb-fl-server-daemon=wfb_ng.fl.server_daemon:main'", content)
        self.assertIn("'scripts/systemd/wfb-fl-server-daemon.service'", content)

    def test_main_cli_argument_parsing_and_run(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json") as config_file:
            json.dump({}, config_file)
            config_file.flush()
            with patch(
                "sys.argv",
                [
                    "wfb-fl-server-daemon",
                    "--config", config_file.name,
                    "--ipc-host", "127.0.0.1",
                    "--ipc-port", "9191",
                    "--channel", "149",
                    "--txpower", "14",
                    "--downlink-mcs", "4",
                    "--uplink-mcs", "5",
                    "--interface", "wlan_test",
                    "--tun-name", "fl-test",
                ],
            ):
                with patch.object(ServerDaemon, "run") as mock_run:
                    from wfb_ng.fl.server_daemon import main
                    main()
                    mock_run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
