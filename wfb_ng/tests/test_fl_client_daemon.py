#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json
import os
import signal
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from wfb_ng.fl.service import _read_config
from wfb_ng.fl.errors import FLRuntimeError
from wfb_ng.fl.radio import ALLOWED_5GHZ_CHANNELS, FORBIDDEN_CHANNELS
from wfb_ng.fl.client_daemon import (
    ClientDaemon,
    ClientDaemonConfig,
    ClientJobConfig,
    DaemonState,
    JobSandbox,
    LinuxNetworkAdapter,
    NetworkAdapter,
    NodeIdentity,
    load_node_identity,
)


class MockNetworkAdapter(NetworkAdapter):
    """In-memory mock network adapter for unit testing without physical hardware."""

    def __init__(self, interfaces=None):
        self._interfaces = list(interfaces or [])
        self.wireless_states = {}
        self.tun_interfaces = {}

    def set_interfaces(self, interfaces):
        self._interfaces = list(interfaces)

    def find_interfaces(self):
        return list(self._interfaces)

    def configure_wireless(self, iface, channel=157, channel_width="HT40+", txpower_dbm=12):
        if channel in FORBIDDEN_CHANNELS:
            raise FLRuntimeError(
                "forbidden_channel",
                f"Channel {channel} is strictly forbidden due to driver kernel crash defect.",
            )
        if channel not in ALLOWED_5GHZ_CHANNELS:
            raise FLRuntimeError(
                "invalid_channel",
                f"Channel {channel} is not in legal pool {ALLOWED_5GHZ_CHANNELS}.",
            )
        if channel == 165:
            channel_width = "HT20"

        self.wireless_states[iface] = {
            "mode": "monitor",
            "channel": channel,
            "channel_width": channel_width,
            "txpower_dbm": txpower_dbm,
            "is_up": True,
        }

    def setup_tun(self, tun_name, tun_cidr):
        self.tun_interfaces[tun_name] = tun_cidr

    def teardown_tun(self, tun_name):
        self.tun_interfaces.pop(tun_name, None)

    def is_tun_active(self, tun_name):
        return tun_name in self.tun_interfaces


class TestNodeIdentity(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_node_id_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_node_json(self, data):
        path = os.path.join(self.temp_dir, "node.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        return path

    def test_load_valid_node_identity(self):
        path = self._write_node_json({"node_id": 1, "tun_ip": "10.80.0.11"})
        identity = load_node_identity(path)
        self.assertEqual(identity.node_id, 1)
        self.assertEqual(identity.tun_ip, "10.80.0.11")
        self.assertEqual(identity.tun_cidr, "10.80.0.11/24")

    def test_load_valid_node_identity_with_cidr(self):
        path = self._write_node_json({"node_id": 7, "tun_ip": "10.80.0.17/24"})
        identity = load_node_identity(path)
        self.assertEqual(identity.node_id, 7)
        self.assertEqual(identity.tun_ip, "10.80.0.17")
        self.assertEqual(identity.tun_cidr, "10.80.0.17/24")

    def test_load_node_identity_rejects_missing_tun_ip(self):
        path = self._write_node_json({"node_id": 4})
        with self.assertRaises(FLRuntimeError) as ctx:
            load_node_identity(path)
        self.assertEqual(ctx.exception.error_code, "invalid_node_identity")

    def test_load_node_identity_supports_boundary_1_and_10(self):
        path1 = self._write_node_json({"node_id": 1, "tun_ip": "10.80.0.11"})
        id1 = load_node_identity(path1)
        self.assertEqual(id1.node_id, 1)

        path10 = self._write_node_json({"node_id": 10, "tun_ip": "10.80.0.20"})
        id10 = load_node_identity(path10)
        self.assertEqual(id10.node_id, 10)
        self.assertEqual(id10.tun_ip, "10.80.0.20")

    def test_load_node_identity_rejects_out_of_bound_node_ids(self):
        for invalid_id in [0, 11, -1, 255]:
            path = self._write_node_json({"node_id": invalid_id})
            with self.assertRaises(FLRuntimeError) as ctx:
                load_node_identity(path)
            self.assertEqual(ctx.exception.error_code, "invalid_node_identity")

    def test_load_node_identity_rejects_non_integer_node_id(self):
        for bad_val in ["1", 1.5, True, None, []]:
            path = self._write_node_json({"node_id": bad_val})
            with self.assertRaises(FLRuntimeError) as ctx:
                load_node_identity(path)
            self.assertEqual(ctx.exception.error_code, "invalid_node_identity")

    def test_load_node_identity_rejects_missing_file(self):
        bad_path = os.path.join(self.temp_dir, "nonexistent.json")
        with self.assertRaises(FLRuntimeError) as ctx:
            load_node_identity(bad_path)
        self.assertEqual(ctx.exception.error_code, "invalid_node_identity")

    def test_load_node_identity_rejects_invalid_tun_ip(self):
        # Conflicts with server 10.80.0.1, wrong node IP (10.80.0.11 is node 1), invalid prefix length /16, invalid IP, wrong subnet
        for bad_ip in ["10.80.0.1", "10.80.0.11", "10.80.0.12/16", "invalid_ip", "10.80.0.256", "192.168.1.1"]:
            path = self._write_node_json({"node_id": 2, "tun_ip": bad_ip})
            with self.assertRaises(FLRuntimeError) as ctx:
                load_node_identity(path)
            self.assertEqual(ctx.exception.error_code, "invalid_node_identity")


class TestNetworkAdapter(unittest.TestCase):
    def test_mock_network_adapter_lifecycle(self):
        adapter = MockNetworkAdapter(interfaces=["wlx001"])
        self.assertEqual(adapter.find_interfaces(), ["wlx001"])

        # Configure wireless
        adapter.configure_wireless("wlx001", channel=157, channel_width="HT40+", txpower_dbm=12)
        self.assertEqual(adapter.wireless_states["wlx001"]["channel"], 157)
        self.assertEqual(adapter.wireless_states["wlx001"]["channel_width"], "HT40+")
        self.assertEqual(adapter.wireless_states["wlx001"]["txpower_dbm"], 12)
        self.assertEqual(adapter.wireless_states["wlx001"]["mode"], "monitor")

        # TUN setup & teardown
        self.assertFalse(adapter.is_tun_active("tun_c1"))
        adapter.setup_tun("tun_c1", "10.80.0.11/24")
        self.assertTrue(adapter.is_tun_active("tun_c1"))
        adapter.teardown_tun("tun_c1")
        self.assertFalse(adapter.is_tun_active("tun_c1"))

    def test_configure_wireless_rejects_forbidden_channel_161(self):
        adapter = MockNetworkAdapter(interfaces=["wlx001"])
        with self.assertRaises(FLRuntimeError) as ctx:
            adapter.configure_wireless("wlx001", channel=161)
        self.assertEqual(ctx.exception.error_code, "forbidden_channel")

    def test_configure_wireless_sets_ht20_for_channel_165(self):
        adapter = MockNetworkAdapter(interfaces=["wlx001"])
        adapter.configure_wireless("wlx001", channel=165)
        self.assertEqual(adapter.wireless_states["wlx001"]["channel_width"], "HT20")

    @mock.patch("subprocess.run")
    def test_linux_network_adapter_calls_correct_commands(self, mock_run):
        mock_run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        adapter = LinuxNetworkAdapter(use_sudo=True)

        with mock.patch("os.path.exists", return_value=False):
            adapter.configure_wireless("wlx_test", channel=157, channel_width="HT40+", txpower_dbm=12)

        # Assert iw and ip commands were called
        calls = [c[0][0] for c in mock_run.call_args_list]
        self.assertTrue(any("monitor" in cmd for cmd in calls))
        self.assertTrue(any("channel" in cmd and "157" in cmd for cmd in calls))
        self.assertTrue(any("txpower" in cmd and "-1200" in cmd for cmd in calls))


class TestHardwarePolling(unittest.TestCase):
    def test_polling_suspends_and_takes_over_when_card_plugged(self):
        adapter = MockNetworkAdapter(interfaces=[])
        config = ClientDaemonConfig(
            node_id=1,
            tun_ip="10.80.0.11",
            poll_interval_seconds=0.05,
        )
        daemon = ClientDaemon(config=config, network_adapter=adapter)

        # Initially no interface -> POLLING_HARDWARE
        self.assertIsNone(daemon.current_interface)

        # Simulate hot plug after 2 polls
        poll_count = 0
        original_find = adapter.find_interfaces

        def delayed_find():
            nonlocal poll_count
            poll_count += 1
            if poll_count >= 2:
                return ["wlx_hotplug_01"]
            return []

        adapter.find_interfaces = delayed_find

        # Perform poll step
        iface = daemon.poll_hardware_once()
        self.assertIsNone(iface)
        self.assertEqual(daemon.state, DaemonState.POLLING_HARDWARE)

        # Second poll step finds card
        iface = daemon.poll_hardware_once()
        self.assertEqual(iface, "wlx_hotplug_01")
        self.assertEqual(daemon.current_interface, "wlx_hotplug_01")
        self.assertEqual(daemon.state, DaemonState.IDLE)
        self.assertTrue(adapter.wireless_states["wlx_hotplug_01"]["is_up"])
        self.assertTrue(adapter.is_tun_active(daemon.config.tun_name))

    def test_polling_detects_card_unplugged(self):
        adapter = MockNetworkAdapter(interfaces=["wlx_hotplug_01"])
        config = ClientDaemonConfig(node_id=1, tun_ip="10.80.0.11", poll_interval_seconds=0.05)
        daemon = ClientDaemon(config=config, network_adapter=adapter)

        daemon.poll_hardware_once()
        self.assertEqual(daemon.state, DaemonState.IDLE)
        self.assertEqual(daemon.current_interface, "wlx_hotplug_01")
        self.assertTrue(adapter.is_tun_active(daemon.config.tun_name))

        # Unplug interface
        adapter.set_interfaces([])
        iface = daemon.poll_hardware_once()
        self.assertIsNone(iface)
        self.assertIsNone(daemon.current_interface)
        self.assertEqual(daemon.state, DaemonState.POLLING_HARDWARE)
        self.assertFalse(adapter.is_tun_active(daemon.config.tun_name))


class TestRoleServiceSandbox(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_sandbox_")
        self.adapter = MockNetworkAdapter(interfaces=["wlx001"])

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_sandbox_runs_process_and_completes_cleanly(self):
        # Use python inline script as stub role service that exits 0
        stub_script = "import sys, time; time.sleep(0.05); sys.exit(0)"
        sandbox = JobSandbox(
            work_dir=self.temp_dir,
            network_adapter=self.adapter,
            command_prefix=[sys.executable, "-c", stub_script],
        )

        job_config = ClientJobConfig(
            job_id="job_001",
            node_id=1,
            tun_name="tun_test1",
            tun_ip="10.80.0.11",
            server_http_host="10.80.0.1",
            server_http_port=8080,
            channel=157,
            radio_txpower_dbm=12,
            uplink_mcs=6,
        )

        proc = sandbox.start(job_config, air_interface="wlx001")
        self.assertIsNotNone(proc.pid)
        self.assertEqual(sandbox.state, DaemonState.RUNNING)

        exit_code = sandbox.wait(timeout=2.0)
        self.assertEqual(exit_code, 0)
        self.assertEqual(sandbox.state, DaemonState.IDLE)

        # Assert TUN and sandbox resources are clean
        self.assertFalse(self.adapter.is_tun_active("tun_test1"))

    def test_sandbox_abort_terminates_child_process_group_and_cleans_resources(self):
        # Stub script that ignores SIGTERM once, requiring kill/terminate
        stub_script = (
            "import time, signal, sys\n"
            "signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))\n"
            "while True:\n"
            "    time.sleep(0.1)\n"
        )
        sandbox = JobSandbox(
            work_dir=self.temp_dir,
            network_adapter=self.adapter,
            command_prefix=[sys.executable, "-c", stub_script],
        )

        job_config = ClientJobConfig(
            job_id="job_002",
            node_id=2,
            tun_name="tun_test2",
            tun_ip="10.80.0.12",
            server_http_host="10.80.0.1",
            server_http_port=8080,
            channel=157,
            radio_txpower_dbm=12,
            uplink_mcs=6,
        )

        proc = sandbox.start(job_config, air_interface="wlx001")
        pid = proc.pid
        self.assertEqual(sandbox.state, DaemonState.RUNNING)

        # Abort job
        sandbox.abort(timeout=1.0)
        self.assertEqual(sandbox.state, DaemonState.IDLE)

        # Audit: assert process is terminated
        try:
            os.kill(pid, 0)
            self.fail("Process should have been terminated")
        except OSError:
            pass  # Process dead

        # Audit: TUN removed
        self.assertFalse(self.adapter.is_tun_active("tun_test2"))

    def test_sandbox_terminates_entire_process_tree_including_grandchild(self):
        # Spawn child which spawns a background grandchild process
        gc_pid_file = os.path.join(self.temp_dir, "grandchild.pid")
        stub_script = (
            "import time, subprocess, sys\n"
            f"sub = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
            f"with open(r'{gc_pid_file}', 'w') as f:\n"
            "    f.write(str(sub.pid))\n"
            "while True:\n"
            "    time.sleep(0.1)\n"
        )
        sandbox = JobSandbox(
            work_dir=self.temp_dir,
            network_adapter=self.adapter,
            command_prefix=[sys.executable, "-c", stub_script],
        )
        job_config = ClientJobConfig(
            job_id="job_tree_01",
            node_id=2,
            tun_name="tun_test_tree",
            tun_ip="10.80.0.12",
            server_http_host="10.80.0.1",
            server_http_port=8080,
        )

        # Pre-set TUN to verify it gets cleaned
        self.adapter.setup_tun("tun_test_tree", "10.80.0.12/24")
        self.assertTrue(self.adapter.is_tun_active("tun_test_tree"))

        proc = sandbox.start(job_config, air_interface="wlx001")
        child_pid = proc.pid

        # Wait for grandchild PID file to appear
        for _ in range(50):
            if os.path.exists(gc_pid_file):
                break
            time.sleep(0.05)
        self.assertTrue(os.path.exists(gc_pid_file))
        with open(gc_pid_file, "r") as f:
            gc_pid = int(f.read().strip())

        # Verify grandchild is alive
        os.kill(gc_pid, 0)

        # Abort sandbox
        sandbox.abort(timeout=1.0)
        self.assertEqual(sandbox.state, DaemonState.IDLE)

        # Verify child is dead
        with self.assertRaises(OSError):
            os.kill(child_pid, 0)

        # Verify grandchild is dead (no orphaned grandchild processes)
        with self.assertRaises(OSError):
            os.kill(gc_pid, 0)

        # Verify TUN is clean
        self.assertFalse(self.adapter.is_tun_active("tun_test_tree"))

    def test_sandbox_start_failure_cleans_up_workspace(self):
        sandbox = JobSandbox(
            work_dir=self.temp_dir,
            network_adapter=self.adapter,
            command_prefix=["/nonexistent/invalid_binary_name_fail"],
        )
        job_config = ClientJobConfig(
            job_id="fail_job_01",
            node_id=1,
            tun_name="tun_fail",
            tun_ip="10.80.0.11",
        )
        with self.assertRaises(FLRuntimeError) as ctx:
            sandbox.start(job_config, air_interface="wlx001")
        self.assertEqual(ctx.exception.error_code, "sandbox_start_failed")
        self.assertEqual(sandbox.state, DaemonState.IDLE)
        self.assertFalse(os.path.exists(os.path.join(self.temp_dir, "job_fail_01")))

    def test_sandbox_rejects_duplicate_concurrent_jobs(self):
        stub_script = "import time; time.sleep(1.0)"
        sandbox = JobSandbox(
            work_dir=self.temp_dir,
            network_adapter=self.adapter,
            command_prefix=[sys.executable, "-c", stub_script],
        )

        job_config = ClientJobConfig(
            job_id="job_003",
            node_id=1,
            tun_name="tun_test3",
            tun_ip="10.80.0.11",
            server_http_host="10.80.0.1",
            server_http_port=8080,
            channel=157,
            radio_txpower_dbm=12,
            uplink_mcs=6,
        )

        sandbox.start(job_config, air_interface="wlx001")
        with self.assertRaises(FLRuntimeError) as ctx:
            sandbox.start(job_config, air_interface="wlx001")
        self.assertEqual(ctx.exception.error_code, "sandbox_already_running")

        sandbox.abort(timeout=1.0)


class TestClientDaemonLifecycle(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_daemon_")
        self.node_json_path = os.path.join(self.temp_dir, "node.json")
        with open(self.node_json_path, "w", encoding="utf-8") as f:
            json.dump({"node_id": 1, "tun_ip": "10.80.0.11"}, f)
        self.adapter = MockNetworkAdapter(interfaces=["wlx001"])

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_daemon_full_lifecycle(self):
        config = ClientDaemonConfig(
            node_id=1,
            tun_ip="10.80.0.11",
            work_dir=self.temp_dir,
            poll_interval_seconds=0.05,
        )
        stub_script = "import sys; sys.exit(0)"
        daemon = ClientDaemon(
            config=config,
            network_adapter=self.adapter,
            sandbox_command_prefix=[sys.executable, "-c", stub_script],
        )

        # Poll and initialize hardware
        daemon.poll_hardware_once()
        self.assertEqual(daemon.state, DaemonState.IDLE)
        self.assertEqual(daemon.current_interface, "wlx001")

        # Trigger job
        job_config = ClientJobConfig(
            job_id="run_01",
            node_id=1,
            tun_name="fl-c1",
            tun_ip="10.80.0.11",
            server_http_host="10.80.0.1",
            server_http_port=8080,
            channel=157,
            radio_txpower_dbm=12,
            uplink_mcs=6,
        )
        daemon.trigger_job(job_config)
        self.assertEqual(daemon.state, DaemonState.RUNNING)

        # Wait for completion
        exit_code = daemon.wait_job(timeout=2.0)
        self.assertEqual(exit_code, 0)
        self.assertEqual(daemon.state, DaemonState.IDLE)
        self.assertTrue(self.adapter.is_tun_active(daemon.config.tun_name))

    def test_generated_client_role_config_strictly_conforms_to_service_contract(self):
        # Verify that client_role.json written by JobSandbox can be successfully validated by service._read_config
        stub_script = "import sys; sys.exit(0)"
        sandbox = JobSandbox(
            work_dir=self.temp_dir,
            network_adapter=self.adapter,
            command_prefix=[sys.executable, "-c", stub_script],
        )
        job_config = ClientJobConfig(
            job_id="conformance_01",
            node_id=3,
            tun_name="tun_test_conf",
            tun_ip="10.80.0.13",
            server_http_host="10.80.0.1",
            server_http_port=8080,
            channel=157,
            channel_width="HT40+",
            radio_txpower_dbm=12,
            uplink_mcs=6,
        )
        # Intercept before child terminates to read config file
        sandbox.start(job_config, air_interface="wlx001")
        config_path = os.path.join(self.temp_dir, "job_conformance_01", "client_role.json")
        self.assertTrue(os.path.exists(config_path))

        # Validate with service._read_config
        parsed_config = _read_config(config_path)
        self.assertEqual(parsed_config["role"], "client")
        self.assertEqual(parsed_config["node_id"], 3)
        self.assertEqual(parsed_config["channel"], 157)
        self.assertEqual(parsed_config["radio_txpower_dbm"], 12)
        self.assertIn("--tun-name", parsed_config["link_args"])

        sandbox.wait(timeout=2.0)

    def test_sandbox_default_command_uses_module_entry(self):
        sandbox = JobSandbox(
            work_dir=self.temp_dir,
            network_adapter=self.adapter,
        )
        job_config = ClientJobConfig(
            job_id="mod_test",
            node_id=1,
            tun_name="fl-c1",
            tun_ip="10.80.0.11",
        )
        with mock.patch("subprocess.Popen") as mock_popen:
            mock_proc = mock.Mock(
                pid=12345,
                poll=mock.Mock(return_value=0),
                wait=mock.Mock(return_value=0),
            )
            mock_popen.return_value = mock_proc
            sandbox.start(job_config, air_interface="wlx001")

            cmd = mock_popen.call_args[0][0]
            self.assertEqual(cmd[0], sys.executable)
            self.assertEqual(cmd[1], "-m")
            self.assertEqual(cmd[2], "wfb_ng.fl.service")
            self.assertIn("--config", cmd)
            sandbox.abort()

    def test_sandbox_handles_abnormal_child_exit_code(self):
        stub_script = "import sys; sys.exit(42)"
        sandbox = JobSandbox(
            work_dir=self.temp_dir,
            network_adapter=self.adapter,
            command_prefix=[sys.executable, "-c", stub_script],
        )
        job_config = ClientJobConfig(
            job_id="job_err_01",
            node_id=1,
            tun_name="tun_err",
            tun_ip="10.80.0.11",
            server_http_host="10.80.0.1",
            server_http_port=8080,
        )
        sandbox.start(job_config, air_interface="wlx001")
        exit_code = sandbox.wait(timeout=2.0)
        self.assertEqual(exit_code, 42)
        self.assertEqual(sandbox.state, DaemonState.IDLE)
        self.assertFalse(self.adapter.is_tun_active("tun_err"))

    def test_daemon_background_loop_and_hotplug(self):
        adapter = MockNetworkAdapter(interfaces=[])
        config = ClientDaemonConfig(
            node_id=1,
            tun_ip="10.80.0.11",
            work_dir=self.temp_dir,
            poll_interval_seconds=0.02,
        )
        stub_script = "import sys; sys.exit(0)"
        daemon = ClientDaemon(
            config=config,
            network_adapter=adapter,
            sandbox_command_prefix=[sys.executable, "-c", stub_script],
        )

        bg_thread = threading.Thread(target=daemon.run, daemon=True)
        bg_thread.start()

        # Initially waiting for hardware
        time.sleep(0.05)
        self.assertEqual(daemon.state, DaemonState.POLLING_HARDWARE)

        # Hot plug interface
        adapter.set_interfaces(["wlx_bg_01"])
        time.sleep(0.08)
        self.assertEqual(daemon.state, DaemonState.IDLE)
        self.assertEqual(daemon.current_interface, "wlx_bg_01")

        # Trigger job
        job_config = ClientJobConfig(
            job_id="bg_job",
            node_id=1,
            tun_name="fl-c1",
            tun_ip="10.80.0.11",
        )
        daemon.trigger_job(job_config)
        self.assertEqual(daemon.state, DaemonState.RUNNING)

        time.sleep(0.08)
        self.assertEqual(daemon.state, DaemonState.IDLE)

        # Hot unplug interface
        adapter.set_interfaces([])
        time.sleep(0.08)
        self.assertEqual(daemon.state, DaemonState.POLLING_HARDWARE)
        self.assertIsNone(daemon.current_interface)

        # Stop daemon cleanly
        daemon.stop()
        bg_thread.join(timeout=1.0)
        self.assertFalse(bg_thread.is_alive())
        self.assertEqual(daemon.state, DaemonState.STOPPED)

    def test_daemon_cli_main_with_valid_and_invalid_args(self):
        from wfb_ng.fl.client_daemon import main as cli_main

        # 1. Invalid config path fails with exit code 1
        ret = cli_main(["--config", "/nonexistent/path/node.json"])
        self.assertEqual(ret, 1)

        # 2. Valid config runs and exits on signal / stop
        valid_node_path = os.path.join(self.temp_dir, "cli_node.json")
        with open(valid_node_path, "w", encoding="utf-8") as f:
            json.dump({"node_id": 5, "tun_ip": "10.80.0.15"}, f)

        # Mock ClientDaemon.run to exit immediately
        with mock.patch("wfb_ng.fl.client_daemon.ClientDaemon.run", return_value=None):
            ret = cli_main(["--config", valid_node_path, "--poll-interval", "0.1"])
            self.assertEqual(ret, 0)

    def test_systemd_service_file_validity(self):
        service_path = "scripts/systemd/wfb-fl-client-daemon.service"
        self.assertTrue(os.path.exists(service_path), f"{service_path} must exist")
        with open(service_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("[Unit]", content)
        self.assertIn("[Service]", content)
        self.assertIn("[Install]", content)
        self.assertIn("ExecStart=/usr/bin/wfb-fl-client-daemon --config /etc/wfb-ng-fl/node.json", content)
        self.assertIn("KillMode=control-group", content)
        self.assertIn("Restart=always", content)

    def test_trigger_job_validates_matching_identity_and_restores_on_failure(self):
        config = ClientDaemonConfig(node_id=1, tun_ip="10.80.0.11", work_dir=self.temp_dir)
        daemon = ClientDaemon(config=config, network_adapter=self.adapter)
        daemon.poll_hardware_once()
        self.assertTrue(self.adapter.is_tun_active("fl-c1"))

        # 1. Reject mismatched node_id
        bad_node = ClientJobConfig(job_id="b1", node_id=2, tun_name="fl-c1", tun_ip="10.80.0.12")
        with self.assertRaises(FLRuntimeError) as ctx:
            daemon.trigger_job(bad_node)
        self.assertEqual(ctx.exception.error_code, "invalid_job_config")

        # 2. Reject mismatched tun_name
        bad_name = ClientJobConfig(job_id="b2", node_id=1, tun_name="other_tun", tun_ip="10.80.0.11")
        with self.assertRaises(FLRuntimeError) as ctx:
            daemon.trigger_job(bad_name)
        self.assertEqual(ctx.exception.error_code, "invalid_job_config")

        # 3. Reject mismatched tun_ip
        bad_ip = ClientJobConfig(job_id="b3", node_id=1, tun_name="fl-c1", tun_ip="10.80.0.12")
        with self.assertRaises(FLRuntimeError) as ctx:
            daemon.trigger_job(bad_ip)
        self.assertEqual(ctx.exception.error_code, "invalid_job_config")

        # 4. Failed start restores TUN
        daemon.sandbox.command_prefix = ["/nonexistent/failing_binary"]
        good_job = ClientJobConfig(job_id="fail_restore", node_id=1, tun_name="fl-c1", tun_ip="10.80.0.11")
        with self.assertRaises(FLRuntimeError):
            daemon.trigger_job(good_job)
        self.assertTrue(self.adapter.is_tun_active("fl-c1"))

    def test_rf_parameter_ranges_validation(self):
        # Forbidden channel 161
        with self.assertRaises(FLRuntimeError) as ctx:
            ClientDaemonConfig(node_id=1, tun_ip="10.80.0.11", channel=161)
        self.assertEqual(ctx.exception.error_code, "forbidden_channel")

        # Out of pool channel 36
        with self.assertRaises(FLRuntimeError) as ctx:
            ClientDaemonConfig(node_id=1, tun_ip="10.80.0.11", channel=36)
        self.assertEqual(ctx.exception.error_code, "invalid_channel")

        # Out of range txpower
        with self.assertRaises(FLRuntimeError) as ctx:
            ClientDaemonConfig(node_id=1, tun_ip="10.80.0.11", radio_txpower_dbm=25)
        self.assertEqual(ctx.exception.error_code, "invalid_txpower")

        # Out of range uplink_mcs
        with self.assertRaises(FLRuntimeError) as ctx:
            ClientDaemonConfig(node_id=1, tun_ip="10.80.0.11", uplink_mcs=7)
        self.assertEqual(ctx.exception.error_code, "invalid_mcs")

    def test_default_template_node_json_validity(self):
        template_path = "scripts/default/node.json"
        self.assertTrue(os.path.exists(template_path), f"{template_path} must exist")
        identity = load_node_identity(template_path)
        self.assertEqual(identity.node_id, 1)
        self.assertEqual(identity.tun_ip, "10.80.0.11")
        self.assertEqual(identity.tun_cidr, "10.80.0.11/24")


if __name__ == "__main__":
    unittest.main()
