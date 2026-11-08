#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json
from wfb_ng.tests._client_test_network import client_network_kwargs, isolated_client_network
import os
import signal
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
import unittest
from unittest import mock

from wfb_ng.fl.service import _read_config
from wfb_ng.fl.errors import FLRuntimeError
from wfb_ng.fl.radio import ALLOWED_5GHZ_CHANNELS, FORBIDDEN_CHANNELS, RadioConfig, validate_radio_config
from wfb_ng.fl.client_daemon import (
    ClientDaemon,
    ClientDaemonConfig,
    ClientJobConfig,
    DaemonState,
    JobSandbox,
    LinuxNetworkAdapter,
    NetworkAdapter,
    NodeIdentity,
    build_v6_uplink_client_command,
    load_node_identity,
)


class MockNetworkAdapter(NetworkAdapter):
    """In-memory mock network adapter for unit testing without physical hardware."""

    def __init__(self, interfaces=None):
        self._interfaces = list(interfaces or [])
        self.wireless_states = {}
        self.channel_history = []
        self.tun_interfaces = {}

    @staticmethod
    def _validate_width(channel, channel_width):
        if channel not in ALLOWED_5GHZ_CHANNELS:
            raise ValueError('invalid channel')
        width = RadioConfig(channel=channel, bandwidth_mhz=20 if channel_width == 'HT20' else 40).channel_width
        if width != channel_width:
            raise ValueError('invalid channel width')

    def set_interfaces(self, interfaces):
        self._interfaces = list(interfaces)

    def find_interfaces(self):
        return list(self._interfaces)

    def configure_wireless(self, iface, channel=157, channel_width="HT40+", txpower_dbm=12):
        validate_radio_config({"radio_txpower_dbm": txpower_dbm})
        self._validate_width(channel, channel_width)

        self.channel_history.append(channel)
        self.wireless_states[iface] = {
            "mode": "monitor",
            "channel": channel,
            "channel_width": channel_width,
            "txpower_dbm": txpower_dbm,
            "is_up": True,
        }

    def set_channel(self, iface, channel, channel_width="HT40+"):
        self._validate_width(channel, channel_width)
        self.channel_history.append(channel)
        if iface in self.wireless_states:
            self.wireless_states[iface]["channel"] = channel
            self.wireless_states[iface]["channel_width"] = channel_width
        else:
            self.wireless_states[iface] = {
                "channel": channel,
                "channel_width": channel_width,
                "txpower_dbm": 12,
            }

    def set_txpower(self, iface, txpower_dbm):
        validate_radio_config({"radio_txpower_dbm": txpower_dbm})
        if iface in self.wireless_states:
            self.wireless_states[iface]["txpower_dbm"] = txpower_dbm
        else:
            self.wireless_states[iface] = {
                "channel": 157,
                "channel_width": "HT40+",
                "txpower_dbm": txpower_dbm,
            }

    def setup_tun(self, tun_name, tun_cidr):
        self.tun_interfaces[tun_name] = tun_cidr

    def set_tun_txqueuelen(self, tun_name, txqueuelen=5000):
        self.tun_txqueuelen = txqueuelen

    def teardown_tun(self, tun_name):
        self.tun_interfaces.pop(tun_name, None)

    def is_tun_active(self, tun_name):
        return tun_name in self.tun_interfaces

    def is_tun_ready(self, tun_name, tun_cidr, destination):
        return self.tun_interfaces.get(tun_name) == tun_cidr


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
        with self.assertRaises(ValueError):
            adapter.configure_wireless("wlx001", channel=161)

    def test_configure_wireless_uses_explicit_ht20_for_channel_165(self):
        adapter = MockNetworkAdapter(interfaces=["wlx001"])
        adapter.configure_wireless("wlx001", channel=165, channel_width="HT20")
        self.assertEqual(adapter.wireless_states["wlx001"]["channel_width"], "HT20")

    @mock.patch("subprocess.run")
    def test_linux_network_adapter_calls_correct_commands(self, mock_run):
        mock_run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        adapter = LinuxNetworkAdapter(use_sudo=True)

        with mock.patch("os.path.exists", return_value=False):
            adapter.configure_wireless("wlx_test", channel=157, channel_width="HT40+", txpower_dbm=18)

        # Assert iw and ip commands were called
        calls = [c[0][0] for c in mock_run.call_args_list]
        self.assertTrue(any("monitor" in cmd for cmd in calls))
        self.assertTrue(any("channel" in cmd and "157" in cmd for cmd in calls))
        self.assertIn(["sudo", "iw", "dev", "wlx_test", "set", "txpower", "fixed", "-1800"], calls)

    @mock.patch("subprocess.run")
    def test_linux_adapter_allows_calibration_ht20_without_rate_table(self, run):
        run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        adapter = LinuxNetworkAdapter(use_sudo=False)
        with mock.patch("os.path.exists", return_value=False):
            adapter.configure_wireless("wlx-test", channel=157, channel_width="HT20", txpower_dbm=14)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn(["iw", "dev", "wlx-test", "set", "channel", "157", "HT20"], commands)

    @mock.patch("subprocess.run")
    def test_linux_adapter_rejects_165_ht40_before_hardware_mutation(self, run):
        adapter = LinuxNetworkAdapter(use_sudo=False)
        with self.assertRaises(ValueError):
            adapter.configure_wireless("wlx-test", channel=165, channel_width="HT40+")
        run.assert_not_called()

    @mock.patch("subprocess.run")
    def test_linux_adapter_preserves_derived_ht40_minus(self, run):
        run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        adapter = LinuxNetworkAdapter(use_sudo=False)
        adapter.set_channel("wlx-test", 153, "HT40-")
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0][-1], "HT40-")
        with self.assertRaises(ValueError):
            adapter.set_channel("wlx-test", 153, "HT40+")

    @mock.patch("wfb_ng.fl.client_daemon.find_wl_interfaces", return_value=["wlP1p2s0"])
    def test_linux_network_adapter_find_interfaces_delegates_to_find_wl_interfaces(self, mock_find):
        adapter = LinuxNetworkAdapter()
        res = adapter.find_interfaces()
        self.assertEqual(res, ["wlP1p2s0"])
        mock_find.assert_called_once()


class TestHardwarePolling(unittest.TestCase):
    def test_polling_suspends_and_takes_over_when_card_plugged(self):
        adapter = MockNetworkAdapter(interfaces=[])
        config = ClientDaemonConfig(**client_network_kwargs(),
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

    def test_disabled_radio_planes_do_not_poll_or_configure_interfaces(self):
        adapter = MockNetworkAdapter(interfaces=["wlx-dedicated"])
        daemon = ClientDaemon(
            ClientDaemonConfig(
                **client_network_kwargs(), node_id=1, tun_ip="10.80.0.11",
                enable_link_process=False,
                enable_control_plane=False,
            ),
            network_adapter=adapter,
        )

        class StopAfterFirstWait:
            stopped = False

            def is_set(self):
                return self.stopped

            def wait(self, timeout=None):
                self.stopped = True
                return True

        daemon._stop_event = StopAfterFirstWait()
        daemon.run()
        self.assertEqual(adapter.wireless_states, {})

    def test_explicit_interface_must_be_a_discovered_wfb_radio(self):
        adapter = MockNetworkAdapter(interfaces=["wlP1p1s0"])
        config = ClientDaemonConfig(
            **client_network_kwargs(), node_id=1, tun_ip="10.80.0.11",
            interface="wlx-missing",
        )
        daemon = ClientDaemon(config=config, network_adapter=adapter)

        self.assertIsNone(daemon.poll_hardware_once())
        self.assertEqual(adapter.wireless_states, {})
        self.assertEqual(daemon.state, DaemonState.POLLING_HARDWARE)

    def test_polling_detects_card_unplugged(self):
        adapter = MockNetworkAdapter(interfaces=["wlx_hotplug_01"])
        config = ClientDaemonConfig(**client_network_kwargs(), node_id=1, tun_ip="10.80.0.11", poll_interval_seconds=0.05)
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


def _make_client_job(
    job_id: str = "job_001",
    run_id: str = "run_001",
    node_id: int = 1,
    tun_name: str = "fl-c1",
    tun_ip: str = "10.80.0.11",
    io_timeout_seconds: float = 120,
    live_observation: bool = True,
    **kwargs,
) -> ClientJobConfig:
    return ClientJobConfig(
        job_id=job_id,
        run_id=run_id,
        node_id=node_id,
        tun_name=tun_name,
        tun_ip=tun_ip,
        io_timeout_seconds=io_timeout_seconds,
        live_observation=live_observation,
        **kwargs,
    )


class TestRoleServiceSandbox(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_sandbox_")
        identity_path = os.path.join(self.temp_dir, 'build_identity.json')
        with open(identity_path, 'w', encoding='utf-8') as identity:
            json.dump({'schema_version': 1, 'commit': 'a' * 40}, identity)
        for patch in (
            mock.patch('wfb_ng.fl.evidence.BUILD_IDENTITY_PATH', Path(identity_path)),
            mock.patch('wfb_ng.fl.client_daemon.DEFAULT_EVIDENCE_DIR', os.path.join(self.temp_dir, 'evidence')),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.adapter = MockNetworkAdapter(interfaces=["wlx001"])

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_sandbox_runs_process_and_completes_cleanly(self):
        # Use python inline script as stub role service that exits 0
        stub_script = "import sys, time; time.sleep(0.05); sys.exit(0)"
        sandbox = JobSandbox(
            work_dir=self.temp_dir,
            network_adapter=self.adapter,
            _command_prefix=[sys.executable, "-c", stub_script],
        )

        job_config = _make_client_job(
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
            _command_prefix=[sys.executable, "-c", stub_script],
        )

        job_config = _make_client_job(
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
        # Spawn child which exits immediately on SIGTERM, while grandchild ignores SIGTERM
        gc_pid_file = os.path.join(self.temp_dir, "grandchild.pid")
        stub_script = (
            "import time, subprocess, sys, signal\n"
            "signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))\n"
            f"sub = subprocess.Popen([sys.executable, '-c', 'import time, signal; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)'])\n"
            f"with open(r'{gc_pid_file}', 'w') as f:\n"
            "    f.write(str(sub.pid))\n"
            "while True:\n"
            "    time.sleep(0.1)\n"
        )
        sandbox = JobSandbox(
            work_dir=self.temp_dir,
            network_adapter=self.adapter,
            _command_prefix=[sys.executable, "-c", stub_script],
        )
        job_config = _make_client_job(
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
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                os.kill(child_pid, 0)
                time.sleep(0.05)
            except OSError:
                break
        with self.assertRaises(OSError):
            os.kill(child_pid, 0)

        # Verify grandchild is dead (no orphaned grandchild processes)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                os.kill(gc_pid, 0)
                time.sleep(0.05)
            except OSError:
                break
        with self.assertRaises(OSError):
            os.kill(gc_pid, 0)

    def test_sandbox_normal_completion_kills_lingering_grandchild_descendants(self):
        # Child completes normally with exit code 0, but leaves a lingering background grandchild
        gc_pid_file = os.path.join(self.temp_dir, "grandchild_norm.pid")
        stub_script = (
            "import time, subprocess, sys\n"
            f"sub = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
            f"with open(r'{gc_pid_file}', 'w') as f:\n"
            "    f.write(str(sub.pid))\n"
            "time.sleep(0.05)\n"
            "sys.exit(0)\n"
        )
        sandbox = JobSandbox(
            work_dir=self.temp_dir,
            network_adapter=self.adapter,
            _command_prefix=[sys.executable, "-c", stub_script],
        )
        job_config = _make_client_job(
            job_id="norm_tree_01",
            node_id=2,
            tun_name="fl-c2",
            tun_ip="10.80.0.12",
        )

        sandbox.start(job_config, air_interface="wlx001")

        # Wait for child to exit normally
        exit_code = sandbox.wait(timeout=2.0)
        self.assertEqual(exit_code, 0)
        self.assertEqual(sandbox.state, DaemonState.IDLE)

        # Read grandchild PID
        self.assertTrue(os.path.exists(gc_pid_file))
        with open(gc_pid_file, "r") as f:
            gc_pid = int(f.read().strip())

        # Verify lingering grandchild was terminated by _finalize
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                os.kill(gc_pid, 0)
                time.sleep(0.05)
            except OSError:
                break
        with self.assertRaises(OSError):
            os.kill(gc_pid, 0)

        # Verify TUN is clean
        self.assertFalse(self.adapter.is_tun_active("tun_test_tree"))

    def test_sandbox_start_failure_cleans_up_workspace(self):
        sandbox = JobSandbox(
            work_dir=self.temp_dir,
            network_adapter=self.adapter,
            _command_prefix=["/nonexistent/invalid_binary_name_fail"],
        )
        job_config = _make_client_job(
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
            _command_prefix=[sys.executable, "-c", stub_script],
        )

        job_config = _make_client_job(
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
        identity_path = os.path.join(self.temp_dir, 'build_identity.json')
        with open(identity_path, 'w', encoding='utf-8') as identity:
            json.dump({'schema_version': 1, 'commit': 'a' * 40}, identity)
        for patch in (
            mock.patch('wfb_ng.fl.evidence.BUILD_IDENTITY_PATH', Path(identity_path)),
            mock.patch('wfb_ng.fl.client_daemon.DEFAULT_EVIDENCE_DIR', os.path.join(self.temp_dir, 'evidence')),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.node_json_path = os.path.join(self.temp_dir, "node.json")
        with open(self.node_json_path, "w", encoding="utf-8") as f:
            json.dump({"node_id": 1, "tun_ip": "10.80.0.11"}, f)
        self.adapter = MockNetworkAdapter(interfaces=["wlx001"])

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_idle_link_hands_tun_to_sandbox_and_recovers_after_completion(self):
        config = ClientDaemonConfig(**client_network_kwargs(),
            node_id=1,
            tun_ip="10.80.0.11",
            work_dir=self.temp_dir,
            link_id=7669206,
        )
        link_processes = []

        class FakeLinkProcess:
            def __init__(self):
                self.returncode = None
                link_processes.append(self)
                self.adapter = None

            def poll(self):
                return self.returncode

            def terminate(self):
                self.returncode = 0

            def wait(self, timeout=None):
                return self.returncode

            def kill(self):
                self.returncode = -9

        def start_link(cmd, **kwargs):
            self.adapter.setup_tun("fl-c1", "10.80.0.11/24")
            return FakeLinkProcess()

        daemon = ClientDaemon(
            config=config,
            network_adapter=self.adapter,
            _link_process_factory=start_link,
        )
        daemon.poll_hardware_once()
        daemon.sandbox._process = mock.Mock()
        daemon.sandbox._process.poll.return_value = None
        daemon.sandbox.state = DaemonState.RUNNING
        daemon.start_idle_link()
        self.assertFalse(daemon.is_idle_link_running)
        self.assertEqual(link_processes, [])
        daemon.sandbox._process = None
        daemon.sandbox.state = DaemonState.IDLE

        daemon.start_idle_link()
        self.assertTrue(daemon.is_idle_link_running)
        self.assertEqual(len(link_processes), 1)

        sandbox_proc = mock.Mock()
        sandbox_proc.poll.return_value = None
        with mock.patch.object(daemon.sandbox, "start", return_value=sandbox_proc) as sandbox_start:
            job = _make_client_job(
                job_id="handoff",
                node_id=1,
                tun_name="fl-c1",
                tun_ip="10.80.0.11",
                link_id=7669206,
            )
            daemon.trigger_job(job)
            self.assertFalse(daemon.is_idle_link_running)
            self.assertEqual(link_processes[0].returncode, 0)
            sandbox_start.assert_called_once()

        daemon.sandbox._process = None
        daemon.sandbox.state = DaemonState.IDLE
        daemon.start_idle_link()
        self.assertTrue(daemon.is_idle_link_running)
        self.assertEqual(len(link_processes), 2)

        # 本地资源恢复仍需服务器确认，才可解除接单门禁。
        with mock.patch('wfb_ng.fl.client_daemon.ControlPlaneClient.start'):
            daemon.start_control_plane()
        recovery_reports = []
        daemon.control_plane.notify_job_recovery = lambda *args, **kwargs: recovery_reports.append((args, kwargs))
        daemon._handle_job_terminal({"job_id": "stale"}, aborted=False)
        self.assertEqual(len(link_processes), 2)
        daemon._handle_job_terminal({"job_id": "handoff"}, aborted=False)
        worker = daemon._recovery_thread
        if worker is not None:
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive(), "异步终态回收必须有界完成")
        self.assertIsNone(link_processes[1].returncode)
        self.assertTrue(daemon.is_idle_link_running)
        self.assertEqual(len(link_processes), 2)
        self.assertIsNone(daemon._current_job_id)
        self.assertEqual(recovery_reports[0][0], ('handoff', 1, 'ready'))
        daemon._handle_job_terminal({"job_id": "handoff"}, aborted=False)
        worker = daemon._recovery_thread
        if worker is not None:
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
        self.assertEqual(len(link_processes), 2)
        self.assertEqual(recovery_reports[1], recovery_reports[0])
        daemon.stop()

    def test_startup_bandwidth_is_the_source_for_hardware_and_link(self):
        for channel, bandwidth, width in ((157, 20, 'HT20'), (153, 40, 'HT40-')):
            with self.subTest(channel=channel, bandwidth=bandwidth):
                adapter = MockNetworkAdapter(['wlx-test'])
                config = ClientDaemonConfig(**client_network_kwargs(), node_id=1, tun_ip='10.80.0.11',
                                            work_dir=self.temp_dir, channel=channel,
                                            bandwidth_mhz=bandwidth,
                                            enable_control_plane=False)
                commands = []

                def spawn(cmd, **kwargs):
                    commands.append(cmd)
                    proc = mock.Mock()
                    proc.poll.return_value = None
                    proc.terminate.side_effect = lambda: setattr(proc.poll, 'return_value', 0)
                    proc.kill.side_effect = lambda: setattr(proc.poll, 'return_value', -9)
                    proc.wait.side_effect = lambda timeout=None: proc.poll()
                    adapter.setup_tun(config.tun_name, config.tun_cidr)
                    return proc

                daemon = ClientDaemon(config, adapter, _link_process_factory=spawn)
                self.addCleanup(daemon.stop)
                daemon.poll_hardware_once()
                with mock.patch('wfb_ng.fl.client_daemon.shutil.which', return_value='/bin/wfb_v6_uplink'):
                    daemon.start_idle_link()
                self.assertEqual(adapter.wireless_states['wlx-test']['channel_width'], width)
                self.assertEqual(commands[0][commands[0].index('--radio-bandwidth') + 1], str(bandwidth))
                self.assertEqual(commands[0][commands[0].index('--radio-control-port') + 1], str(config.radio_control_port))

    def test_config_rejects_165_ht40_instead_of_downgrading(self):
        with self.assertRaises(ValueError):
            ClientDaemonConfig(**client_network_kwargs(), node_id=1, tun_ip='10.80.0.11', channel=165, bandwidth_mhz=40)
        with self.assertRaises(ValueError):
            _make_client_job(channel=165, bandwidth_mhz=40)
        self.assertEqual(_make_client_job(channel=165, bandwidth_mhz=20).channel_width, 'HT20')

    def test_client_link_command_and_job_share_server_link_id(self):
        cmd = build_v6_uplink_client_command(
            executable_path="/usr/bin/wfb_v6_uplink",
            node_id=2,
            tun_name="fl-c2",
            tun_addr="10.80.0.12/24",
            air_interface="wlx-test",
            uplink_mcs=6,
            link_id=7669206,
        )
        self.assertEqual(cmd[cmd.index("--link-id") + 1], "7669206")
        self.assertNotIn("--radio-short-gi", cmd)
        for bandwidth in (20, 40):
            narrow_cmd = build_v6_uplink_client_command(
                "/usr/bin/wfb_v6_uplink", 2, "fl-c2", "10.80.0.12/24",
                "wlx-test", 6, 7669206, bandwidth_mhz=bandwidth)
            self.assertNotIn("--radio-short-gi", narrow_cmd)
        job = _make_client_job(
            job_id="link-id",
            node_id=2,
            tun_name="fl-c2",
            tun_ip="10.80.0.12",
            link_id=7669206,
        )
        self.assertEqual(job.link_id, 7669206)
        self.assertEqual(job.uftp_port, 1044)
        self.assertEqual(job.uftp_multicast_host, "239.80.41.1")
        self.assertEqual(job.uftp_private_multicast_host, "239.80.41.2")

    def test_daemon_full_lifecycle(self):
        config = ClientDaemonConfig(**client_network_kwargs(),
            node_id=1,
            tun_ip="10.80.0.11",
            work_dir=self.temp_dir,
            poll_interval_seconds=0.05,
            enable_link_process=False,
        )
        stub_script = "import sys; sys.exit(0)"
        daemon = ClientDaemon(
            config=config,
            network_adapter=self.adapter,
        )
        daemon.sandbox._command_prefix = [sys.executable, "-c", stub_script]

        # Poll and initialize hardware
        daemon.poll_hardware_once()
        self.assertEqual(daemon.state, DaemonState.IDLE)
        self.assertEqual(daemon.current_interface, "wlx001")

        # Trigger job
        job_config = _make_client_job(
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
            _command_prefix=[sys.executable, "-c", stub_script],
        )
        job_config = _make_client_job(
            job_id="conformance_01",
            fec_n=12,
            node_id=3,
            tun_name="tun_test_conf",
            tun_ip="10.80.0.13",
            server_http_host="10.80.0.1",
            server_http_port=8080,
            channel=157,
            bandwidth_mhz=20,
            radio_txpower_dbm=12,
            uplink_mcs=6,
        )
        # Intercept before child terminates to read config file
        sandbox.start(job_config, air_interface="wlx001")
        config_path = os.path.join(self.temp_dir, "job_conformance_01", "client_role.json")
        self.assertTrue(os.path.exists(config_path))

        # Validate with service._read_config
        with open(config_path, encoding='utf-8') as stream:
            role = json.load(stream)
        self.assertEqual(role['channel_width'], 'HT20')
        self.assertEqual(role['link_args'][role['link_args'].index('--fec-n') + 1], '12')
        self.assertNotIn('--radio-short-gi', role['link_args'])
        self.assertEqual(role['link_args'][role['link_args'].index('--radio-bandwidth') + 1], '20')
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
            _require_ready_notification=False,
        )
        job_config = _make_client_job(
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
            _command_prefix=[sys.executable, "-c", stub_script],
        )
        job_config = _make_client_job(
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
        config = ClientDaemonConfig(**client_network_kwargs(),
            node_id=1,
            tun_ip="10.80.0.11",
            work_dir=self.temp_dir,
            poll_interval_seconds=0.02,
            enable_control_plane=False,
        )
        stub_script = "import sys; sys.exit(0)"
        daemon = ClientDaemon(
            config=config,
            network_adapter=adapter,
        )
        daemon.sandbox._command_prefix = [sys.executable, "-c", stub_script]

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
        job_config = _make_client_job(
            job_id="bg_job",
            node_id=1,
            tun_name="fl-c1",
            tun_ip="10.80.0.11",
        )
        daemon.trigger_job(job_config)
        self.assertEqual(daemon.state, DaemonState.RUNNING)

        # 等待可观察的作业回收状态，避免依赖宿主进程调度速度。
        deadline = time.monotonic() + 2
        while daemon.state != DaemonState.IDLE and time.monotonic() < deadline:
            time.sleep(0.01)
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
        config = ClientDaemonConfig(**client_network_kwargs(),
            node_id=1,
            tun_ip="10.80.0.11",
            work_dir=self.temp_dir,
            enable_link_process=False,
        )
        daemon = ClientDaemon(config=config, network_adapter=self.adapter)
        daemon.poll_hardware_once()
        self.assertTrue(self.adapter.is_tun_active("fl-c1"))

        # 1. Reject mismatched node_id
        bad_node = _make_client_job(job_id="b1", node_id=2, tun_name="fl-c1", tun_ip="10.80.0.12")
        with self.assertRaises(FLRuntimeError) as ctx:
            daemon.trigger_job(bad_node)
        self.assertEqual(ctx.exception.error_code, "invalid_job_config")

        # 2. Reject mismatched tun_name
        bad_name = _make_client_job(job_id="b2", node_id=1, tun_name="other_tun", tun_ip="10.80.0.11")
        with self.assertRaises(FLRuntimeError) as ctx:
            daemon.trigger_job(bad_name)
        self.assertEqual(ctx.exception.error_code, "invalid_job_config")

        # 3. Reject mismatched tun_ip
        bad_ip = _make_client_job(job_id="b3", node_id=1, tun_name="fl-c1", tun_ip="10.80.0.12")
        with self.assertRaises(FLRuntimeError) as ctx:
            daemon.trigger_job(bad_ip)
        self.assertEqual(ctx.exception.error_code, "invalid_job_config")

        # 4. Failed start restores TUN
        daemon.sandbox._command_prefix = ["/nonexistent/failing_binary"]
        good_job = _make_client_job(job_id="fail_restore", node_id=1, tun_name="fl-c1", tun_ip="10.80.0.11")
        with self.assertRaises(FLRuntimeError):
            daemon.trigger_job(good_job)
        self.assertTrue(self.adapter.is_tun_active("fl-c1"))

    def test_rf_parameter_ranges_validation(self):
        # Forbidden channel 161
        with self.assertRaises(ValueError):
            ClientDaemonConfig(**client_network_kwargs(), node_id=1, tun_ip="10.80.0.11", channel=161)

        # Out of pool channel 36
        with self.assertRaises(ValueError):
            ClientDaemonConfig(**client_network_kwargs(), node_id=1, tun_ip="10.80.0.11", channel=36)

        # Out of range txpower
        with self.assertRaises(ValueError):
            ClientDaemonConfig(**client_network_kwargs(), node_id=1, tun_ip="10.80.0.11", radio_txpower_dbm=0)

        # Out of range uplink_mcs
        with self.assertRaises(ValueError):
            ClientDaemonConfig(**client_network_kwargs(), node_id=1, tun_ip="10.80.0.11", uplink_mcs=7)

        # Non-integer channel
        with self.assertRaises(ValueError):
            ClientDaemonConfig(**client_network_kwargs(), node_id=1, tun_ip="10.80.0.11", channel=157.0)

        # Non-integer txpower
        with self.assertRaises(ValueError):
            ClientDaemonConfig(**client_network_kwargs(), node_id=1, tun_ip="10.80.0.11", radio_txpower_dbm=12.5)

        # Non-integer uplink_mcs
        with self.assertRaises(ValueError):
            ClientDaemonConfig(**client_network_kwargs(), node_id=1, tun_ip="10.80.0.11", uplink_mcs=3.5)

    def test_default_template_node_json_validity(self):
        template_path = "scripts/default/node.json"
        self.assertTrue(os.path.exists(template_path), f"{template_path} must exist")
        identity = load_node_identity(template_path)
        self.assertEqual(identity.node_id, 1)
        self.assertEqual(identity.tun_ip, "10.80.0.11")
        self.assertEqual(identity.tun_cidr, "10.80.0.11/24")


if __name__ == "__main__":
    unittest.main()


class TestStartupFecClient(unittest.TestCase):
    def test_real_builder_and_config(self):
        for fec_n in (12, 14):
            config = ClientDaemonConfig(node_id=1, tun_ip="10.80.0.11", fec_n=fec_n)
            daemon = ClientDaemon(config=config, network_adapter=mock.MagicMock())
            self.assertEqual(daemon._effective_radio.fec_n, fec_n)
            cmd = build_v6_uplink_client_command("wfb_v6_uplink", 1, "fl-c1", "10.80.0.11/24",
                                                  "wlan0", 6, 7669206, fec_n=fec_n)
            self.assertEqual(cmd[cmd.index("--fec-k") + 1], "8")
            self.assertEqual(cmd[cmd.index("--fec-n") + 1], str(fec_n))
        for value in (True, 12.0, "12", None, 13):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ClientDaemonConfig(node_id=1, tun_ip="10.80.0.11", fec_n=value)
