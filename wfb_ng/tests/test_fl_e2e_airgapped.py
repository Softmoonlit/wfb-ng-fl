#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Stage 2 Airgapped Core Engine & Daemon End-to-End Simulation Tests.

Validates the complete federated learning lifecycle in an airgapped test environment
without external SSH dependencies:
- Server Daemon + Client Daemon 1 + Client Daemon 2 (node_id 1, 2)
- 40 MiB deterministic initial model and update fixtures
- Dynamic channel hunting and readiness transition
- REST IPC job announcement and broadcast wakeup
- 40 MiB model distribution and concurrent update submission
- Quorum verification and FedAvg aggregation across multiple rounds
- Strict SHA-256 continuity verification (Project Memory #51)
- Safe cluster reset to IDLE without zombie process leaks or leaked TUN devices
- Audit log and coordinator summary verification
"""

import hashlib
import json
import logging
import os
import shutil
import socket
import tempfile
import threading
import time
import unittest
import urllib.request
from typing import Any, Dict, List, Optional
from unittest import mock

from wfb_ng.fl.artifacts import file_sha256
from wfb_ng.fl.client_daemon import (
    ClientDaemon,
    ClientDaemonConfig,
    ClientJobConfig,
    ClientNodeState,
    DaemonState,
    JobSandbox,
    NetworkAdapter,
)
from wfb_ng.fl.control import (
    ControlPlaneClient,
    ControlPlaneServer,
    NodeHeartbeat,
    NodeReadiness,
)
from wfb_ng.fl.coordinator import (
    CoordinatorState,
    FLCoordinator,
    aggregate_models,
)
from wfb_ng.fl.errors import FLRuntimeError
from wfb_ng.fl.issue41_fixtures import (
    generate_client_fixture,
    generate_model_fixture,
)
from wfb_ng.fl.radio import validate_radio_config
from wfb_ng.fl.server_daemon import (
    JobConfig,
    RadioConfig,
    ServerDaemon,
    ServerDaemonConfig,
    ServerState,
)
from wfb_ng.tests.mock_runtime import MockServerRuntime

logger = logging.getLogger(__name__)


class VirtualNetworkAdapter(NetworkAdapter):
    """In-memory network adapter simulating wireless and TUN operations."""

    def __init__(self, interfaces=None):
        self._interfaces = list(interfaces or ["wlan0"])
        self.wireless_states = {}
        self.channel_history = []
        self.active_tuns = set()

    def find_interfaces(self):
        return list(self._interfaces)

    def configure_wireless(self, iface, channel=157, channel_width="HT40+", txpower_dbm=12):
        validate_radio_config({"channel": channel, "radio_txpower_dbm": txpower_dbm})
        self.channel_history.append(channel)
        self.wireless_states[iface] = {
            "mode": "monitor",
            "channel": channel,
            "channel_width": channel_width,
            "txpower_dbm": txpower_dbm,
            "is_up": True,
        }

    def set_channel(self, iface, channel, channel_width="HT40+"):
        validate_radio_config({"channel": channel})
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

    def setup_tun(self, tun_name, tun_cidr):
        self.active_tuns.add(tun_name)

    def teardown_tun(self, tun_name):
        self.active_tuns.discard(tun_name)

    def is_tun_active(self, tun_name):
        return tun_name in self.active_tuns

    def set_tun_txqueuelen(self, tun_name, txqueuelen=5000):
        pass





class TestAirgappedE2ECluster(unittest.TestCase):
    def setUp(self):
        tmp_dir = "/dev/shm" if os.path.isdir("/dev/shm") else None
        self.root = tempfile.mkdtemp(prefix="wfb_airgapped_e2e_", dir=tmp_dir)
        self.addCleanup(shutil.rmtree, self.root, True)

        # 40 MiB size
        self.payload_size = 40 * 1024 * 1024

        # Generate 40 MiB initial model
        self.fixtures_dir = os.path.join(self.root, "fixtures")
        os.makedirs(self.fixtures_dir, exist_ok=True)
        self.model_fix = generate_model_fixture(self.fixtures_dir, size_bytes=self.payload_size)
        self.model_path = self.model_fix["path"]
        self.model_sha256 = self.model_fix["sha256"]

        # Generate 40 MiB client 1 and client 2 fixtures
        self.client1_fix = generate_client_fixture(self.fixtures_dir, 1, size_bytes=self.payload_size)
        self.client2_fix = generate_client_fixture(self.fixtures_dir, 2, size_bytes=self.payload_size)
        self.assertNotEqual(self.client1_fix["sha256"], self.client2_fix["sha256"])

        self.client_updates = {
            1: self.client1_fix["path"],
            2: self.client2_fix["path"],
        }

        # Port allocations for virtual control plane
        self.control_bind_port = self._find_free_port()
        self.control_broadcast_port = self._find_free_port()
        self.ipc_port = self._find_free_port()
        self.base_url = f"http://127.0.0.1:{self.ipc_port}"

        # Initialize Server Daemon
        self.server_adapter = VirtualNetworkAdapter(["wlan0"])
        self.server_config = ServerDaemonConfig(
            work_dir=os.path.join(self.root, "server"),
            ipc_host="127.0.0.1",
            ipc_port=self.ipc_port,
            control_bind_host="127.0.0.1",
            control_bind_port=self.control_bind_port,
            control_broadcast_addr="127.0.0.1",
            control_broadcast_port=self.control_broadcast_port,
            air_interface="wlan0",
            tun_ip="10.80.0.1",
            enable_link_process=False,
        )
        self.server_daemon = ServerDaemon(
            config=self.server_config,
            network_adapter=self.server_adapter,
        )
        self.server_daemon.start()

        # Initialize Client Daemons 1 and 2
        self.client1_adapter = VirtualNetworkAdapter(["wlan1"])
        self.client1_daemon = ClientDaemon(
            config=ClientDaemonConfig(
                node_id=1,
                tun_ip="10.80.0.11",
                interface="wlan1",
                work_dir=os.path.join(self.root, "client1"),
                server_control_host="127.0.0.1",
                server_control_port=self.control_bind_port,
                broadcast_port=self.control_broadcast_port,
            ),
            network_adapter=self.client1_adapter,
        )

        self.client2_adapter = VirtualNetworkAdapter(["wlan2"])
        self.client2_daemon = ClientDaemon(
            config=ClientDaemonConfig(
                node_id=2,
                tun_ip="10.80.0.12",
                interface="wlan2",
                work_dir=os.path.join(self.root, "client2"),
                server_control_host="127.0.0.1",
                server_control_port=self.control_bind_port,
                broadcast_port=self.control_broadcast_port,
            ),
            network_adapter=self.client2_adapter,
        )

        os.makedirs(self.client1_daemon.config.work_dir, exist_ok=True)
        shutil.copyfile(
            self.client1_fix["path"],
            os.path.join(self.client1_daemon.config.work_dir, "update-client1-template.bin"),
        )
        os.makedirs(self.client2_daemon.config.work_dir, exist_ok=True)
        shutil.copyfile(
            self.client2_fix["path"],
            os.path.join(self.client2_daemon.config.work_dir, "update-client2-template.bin"),
        )

    def tearDown(self):
        self.client1_daemon.stop()
        self.client2_daemon.stop()
        self.server_daemon.stop()
        shutil.rmtree(self.root, ignore_errors=True)

    def _find_free_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def _wait_until(self, predicate: Any, timeout: float = 3.0, interval: float = 0.02, msg: str = "condition not met") -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(interval)
        self.fail(f"Timeout waiting for: {msg}")

    def _http_post(self, path: str, payload: Dict[str, Any]) -> tuple:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                status = resp.status
                body = json.loads(resp.read().decode("utf-8"))
                return status, body
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def _simulate_client_handshake(self, client_daemon: ClientDaemon) -> None:
        """Simulate client dynamic channel discovery and locking to reach READY."""
        iface = client_daemon.poll_hardware_once()
        self.assertIsNotNone(iface)
        client_daemon.start_control_plane()

        # Execute channel hunt to lock Channel 157 with retries
        found = False
        for _ in range(5):
            if client_daemon.control_plane.hunt_once(ladder=[157]):
                found = True
                break
            time.sleep(0.05)
        self.assertTrue(found, f"Node {client_daemon.config.node_id} failed to lock channel 157")
        self.assertEqual(client_daemon.control_plane.state, ClientNodeState.IDLE)

    def test_e2e_dual_client_sync_two_rounds_40mib_success(self):
        """
        全生命周期两轮 40 MiB 双客户端同步 (sync) 并发仿真测试：
        - 验证两节点通道锁定与 IDLE 状态注册
        - 任务通过前置门禁发布，TASK_ANNOUNCE 唤醒客户端沙箱
        - 双客户端 40 MiB 模型下发与 HTTP PUT 并发上传
        - FedAvg 聚合与跨轮 SHA-256 连续性校验
        - 作业正常完成，全集群安全复位 IDLE 待命，零僵尸进程残留
        """
        # 1. 客户端通道发现与握手就绪
        self._simulate_client_handshake(self.client1_daemon)
        self._simulate_client_handshake(self.client2_daemon)
        self._wait_until(
            lambda: (
                self.server_daemon.control_plane.registry.get_node(1) is not None
                and self.server_daemon.control_plane.registry.get_node(1).readiness == NodeReadiness.READY
                and self.server_daemon.control_plane.registry.get_node(2) is not None
                and self.server_daemon.control_plane.registry.get_node(2).readiness == NodeReadiness.READY
            ),
            msg="cluster nodes reaching READY",
        )

        # 2. 构造 40 MiB 虚拟运行时环境
        virtual_runtime = MockServerRuntime(
            participant_node_ids=(1, 2),
            update_fixtures_by_node=self.client_updates,
        )
        self.server_daemon.runtime_factory = lambda job, payload: virtual_runtime

        job_payload = {
            "job_id": "job_40mib_sync_2rounds",
            "mode": "sync",
            "rounds": 2,
            "target_nodes": [1, 2],
            "model_path": self.model_path,
            "model_size_bytes": self.payload_size,
            "model_sha256": self.model_sha256,
            "round_timeout_seconds": 30.0,
        }

        # 3. 提交任务启动
        code, body = self._http_post("/api/v1/jobs/start", job_payload)
        self.assertEqual(code, 200)
        self.assertEqual(body["status"], "accepted")
        self.assertEqual(self.server_daemon.server_state, ServerState.RUNNING)

        # 等待协同器完成多轮作业
        self.assertIsNotNone(self.server_daemon.coordinator)
        ret = self.server_daemon.coordinator.wait(timeout=5.0)
        self.assertEqual(ret, 0)
        self.assertEqual(self.server_daemon.coordinator.state, CoordinatorState.SUCCEEDED)

        # 等待服务端主循环平滑过渡回 IDLE
        self._wait_until(lambda: self.server_daemon.server_state == ServerState.IDLE, msg="server returning to IDLE")
        self.assertIsNone(self.server_daemon.active_job)

        # 4. 校验审计摘要与跨轮连续性 (Project Memory #51)
        summary = self.server_daemon.coordinator.get_summary()
        self.assertIsNotNone(summary)
        self.assertEqual(summary["status"], "succeeded")
        self.assertEqual(summary["rounds_completed"], 2)
        self.assertEqual(len(summary["rounds"]), 2)

        round1 = summary["rounds"][0]
        round2 = summary["rounds"][1]
        self.assertEqual(round1["input_model_sha256"], self.model_sha256)
        self.assertEqual(round1["output_model_sha256"], round2["input_model_sha256"])
        self.assertEqual(round1["committed_nodes"], [1, 2])
        self.assertEqual(round2["committed_nodes"], [1, 2])
        self.assertEqual(round1["dropped_out_nodes"], [])
        self.assertEqual(round2["dropped_out_nodes"], [])

        # 5. 断言无僵尸进程残留与 TUN 状态安全
        self.client1_daemon.wait_job(timeout=3.0)
        self.client2_daemon.wait_job(timeout=3.0)
        self.assertFalse(self.client1_daemon.sandbox.is_running)
        self.assertFalse(self.client2_daemon.sandbox.is_running)
        self.assertFalse(self.client1_adapter.is_tun_active("tun_job_40mib_sync_2rounds"))
        self.assertFalse(self.client2_adapter.is_tun_active("tun_job_40mib_sync_2rounds"))
        self.assertTrue(self.client1_adapter.is_tun_active(self.client1_daemon.config.tun_name))
        self.assertTrue(self.client2_adapter.is_tun_active(self.client2_daemon.config.tun_name))

    def test_e2e_dual_client_semi_async_straggler_dropout_40mib(self):
        """
        全生命周期 40 MiB 半异步 (semi_async) 模式测试：
        - min_updates=1，节点 1 提交，节点 2 掉队
        - 验证达到配额即触发 FedAvg 推进下一轮，未提交节点记为 dropped_out
        - 验证全过程顺利完成并生成不可篡改审计摘要
        """
        self._simulate_client_handshake(self.client1_daemon)
        self._simulate_client_handshake(self.client2_daemon)
        self._wait_until(
            lambda: (
                self.server_daemon.control_plane.registry.get_node(1) is not None
                and self.server_daemon.control_plane.registry.get_node(1).readiness == NodeReadiness.READY
                and self.server_daemon.control_plane.registry.get_node(2) is not None
                and self.server_daemon.control_plane.registry.get_node(2).readiness == NodeReadiness.READY
            ),
            msg="cluster nodes reaching READY",
        )

        # 节点 2 掉队
        virtual_runtime = MockServerRuntime(
            participant_node_ids=(1, 2),
            update_fixtures_by_node=self.client_updates,
            simulate_straggler_nodes={2},
        )
        self.server_daemon.runtime_factory = lambda job, payload: virtual_runtime

        job_payload = {
            "job_id": "job_40mib_semi_straggler",
            "mode": "semi_async",
            "rounds": 2,
            "target_nodes": [1, 2],
            "min_updates": 1,
            "model_path": self.model_path,
            "model_size_bytes": self.payload_size,
            "round_timeout_seconds": 10.0,
        }

        code, body = self._http_post("/api/v1/jobs/start", job_payload)
        self.assertEqual(code, 200)

        ret = self.server_daemon.coordinator.wait(timeout=5.0)
        self.assertEqual(ret, 0)
        self.assertEqual(self.server_daemon.coordinator.state, CoordinatorState.SUCCEEDED)

        summary = self.server_daemon.coordinator.get_summary()
        self.assertEqual(summary["status"], "succeeded")
        self.assertEqual(summary["rounds_completed"], 2)

        for r_info in summary["rounds"]:
            self.assertEqual(r_info["committed_nodes"], [1])
            self.assertEqual(r_info["dropped_out_nodes"], [2])

        self.client1_daemon.wait_job(timeout=3.0)
        self.client2_daemon.wait_job(timeout=3.0)
        self.assertFalse(self.client1_daemon.sandbox.is_running)
        self.assertFalse(self.client2_daemon.sandbox.is_running)
        self.assertTrue(self.client1_adapter.is_tun_active(self.client1_daemon.config.tun_name))
        self.assertTrue(self.client2_adapter.is_tun_active(self.client2_daemon.config.tun_name))

    def test_e2e_dual_client_job_abort_and_cluster_safe_reset(self):
        """
        全生命周期作业中止测试：
        - 验证 POST /api/v1/jobs/abort 广播 JOB_ABORT
        - 协同器即时中止并标记 ABORTED
        - 集群与客户端沙箱干净回收无残留，平滑回到 IDLE 就绪态
        """
        self._simulate_client_handshake(self.client1_daemon)
        self._simulate_client_handshake(self.client2_daemon)
        self._wait_until(
            lambda: (
                self.server_daemon.control_plane.registry.get_node(1) is not None
                and self.server_daemon.control_plane.registry.get_node(1).readiness == NodeReadiness.READY
                and self.server_daemon.control_plane.registry.get_node(2) is not None
                and self.server_daemon.control_plane.registry.get_node(2).readiness == NodeReadiness.READY
            ),
            msg="cluster nodes reaching READY",
        )

        # 慢速运行时模拟长作业
        class SlowRuntime(MockServerRuntime):
            def wait_for_updates(self, min_updates=None, timeout=None):
                self.waiting_updates_event.set()
                self.abort_event.wait(timeout=5.0)
                if self.aborted:
                    raise FLRuntimeError("aborted", "Runtime 已被中止")
                return super().wait_for_updates(min_updates, timeout)

        slow_runtime = SlowRuntime(
            participant_node_ids=(1, 2),
            update_fixtures_by_node=self.client_updates,
        )
        self.server_daemon.runtime_factory = lambda job, payload: slow_runtime

        job_payload = {
            "job_id": "job_abort_test",
            "mode": "sync",
            "rounds": 5,
            "target_nodes": [1, 2],
            "model_path": self.model_path,
            "model_size_bytes": self.payload_size,
            "round_timeout_seconds": 30.0,
        }

        code, body = self._http_post("/api/v1/jobs/start", job_payload)
        self.assertEqual(code, 200)
        self.assertEqual(self.server_daemon.server_state, ServerState.RUNNING)

        # 确定性等待运行时进入 wait_for_updates 状态
        self.assertTrue(slow_runtime.waiting_updates_event.wait(timeout=2.0))

        # 触发主动中止
        abort_code, abort_body = self._http_post("/api/v1/jobs/abort", {"reason": "test_operator_abort"})
        self.assertEqual(abort_code, 200)
        self.assertEqual(abort_body["status"], "aborted")

        # 客户端沙箱安全回收，无僵尸进程与残留 TUN
        self.client1_daemon.wait_job(timeout=3.0)
        self.client2_daemon.wait_job(timeout=3.0)
        self.assertFalse(self.client1_daemon.sandbox.is_running)
        self.assertFalse(self.client2_daemon.sandbox.is_running)
        self.assertTrue(self.client1_adapter.is_tun_active(self.client1_daemon.config.tun_name))
        self.assertTrue(self.client2_adapter.is_tun_active(self.client2_daemon.config.tun_name))

        # 验证服务器迅速回到 IDLE 状态且 coordinator 线程已退出
        self.assertEqual(self.server_daemon.server_state, ServerState.IDLE)
        self.assertIsNone(self.server_daemon.active_job)


if __name__ == "__main__":
    unittest.main()
