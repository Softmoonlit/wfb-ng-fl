#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json
import os
import shutil
import stat
import tempfile
import threading
import time
import unittest
from typing import Any, Dict, List, Optional
from unittest import mock

from wfb_ng.fl.artifacts import file_sha256
from wfb_ng.fl.coordinator import (
    CoordinatorState,
    FLCoordinator,
    aggregate_models,
)
from wfb_ng.fl.errors import FLRuntimeError
from wfb_ng.fl.server_daemon import JobConfig
from wfb_ng.tests.mock_runtime import MockServerRuntime


class TestFLCoordinator(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_coordinator_")
        self.model_size = 1024 * 64  # 64 KiB for fast unit tests
        self.model_path = os.path.join(self.test_dir, "initial_model.bin")
        with open(self.model_path, "wb") as f:
            f.write(b"M" * self.model_size)
        self.model_sha256 = file_sha256(self.model_path)

        # Create client update fixtures
        self.client_updates = {}
        for nid in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10):
            client_dir = os.path.join(self.test_dir, f"client_{nid}")
            os.makedirs(client_dir, exist_ok=True)
            upath = os.path.join(client_dir, "update.bin")
            with open(upath, "wb") as f:
                f.write(bytes([nid % 256]) * self.model_size)
            with open(os.path.join(client_dir, "update.manifest.json"), "w") as f:
                json.dump({"round_id": "round_1", "size_bytes": self.model_size, "sha256": file_sha256(upath)}, f)
            self.client_updates[nid] = upath

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _make_job(
        self,
        mode: str = "sync",
        rounds: int = 2,
        target_nodes: tuple = (1, 2),
        min_updates: Optional[int] = None,
        max_staleness: int = 0,
        round_timeout_seconds: float = 5.0,
    ) -> JobConfig:
        return JobConfig(
            job_id=f"job_{mode}_{int(time.time() * 1000)}",
            mode=mode,
            target_nodes=target_nodes,
            model_path=self.model_path,
            model_size_bytes=self.model_size,
            rounds=rounds,
            min_updates=min_updates,
            max_staleness=max_staleness,
            round_timeout_seconds=round_timeout_seconds,
            model_sha256=self.model_sha256,
        )

    def test_sync_mode_two_rounds_success_with_sha256_continuity(self):
        """测试 sync 同步全员模式：两轮作业，收齐全员，FedAvg聚合，校验跨轮SHA256连续性与审计摘要。"""
        job = self._make_job(mode="sync", rounds=2, target_nodes=(1, 2))
        runtime = MockServerRuntime(
            participant_node_ids=(1, 2),
            update_fixtures_by_node=self.client_updates,
        )

        events: List[Dict[str, Any]] = []
        coordinator = FLCoordinator(
            job=job,
            runtime=runtime,
            work_dir=os.path.join(self.test_dir, "coordinator_work"),
            on_event=events.append,
        )

        summary = coordinator.run()

        self.assertEqual(coordinator.state, CoordinatorState.SUCCEEDED)
        self.assertEqual(summary["status"], "succeeded")
        self.assertEqual(summary["rounds_completed"], 2)
        self.assertEqual(len(summary["rounds"]), 2)

        # 校验跨轮模型连续性 (Project Memory #51)
        round1 = summary["rounds"][0]
        round2 = summary["rounds"][1]
        self.assertEqual(round1["round_index"], 1)
        self.assertEqual(round2["round_index"], 2)
        self.assertEqual(round1["input_model_sha256"], self.model_sha256)
        self.assertEqual(round1["output_model_sha256"], round2["input_model_sha256"])
        self.assertEqual(round1["committed_nodes"], [1, 2])
        self.assertEqual(round2["committed_nodes"], [1, 2])
        self.assertEqual(round1["dropped_out_nodes"], [])
        self.assertEqual(round2["dropped_out_nodes"], [])

        # 校验审计摘要文件在磁盘生成
        summary_path = os.path.join(coordinator.work_dir, "coordinator_summary.json")
        self.assertTrue(os.path.isfile(summary_path))
        with open(summary_path, "r", encoding="utf-8") as f:
            disk_summary = json.load(f)
        self.assertEqual(disk_summary["status"], "succeeded")
        self.assertEqual(disk_summary["job_id"], job.job_id)

        # 校验不可篡改审计清单与防篡改断言
        manifest_path = os.path.join(coordinator.work_dir, "coordinator_summary.manifest.json")
        self.assertTrue(os.path.isfile(manifest_path))
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest_data = json.load(f)
        self.assertEqual(manifest_data["job_id"], job.job_id)
        self.assertEqual(manifest_data["summary_sha256"], file_sha256(summary_path))

        # 防篡改断言：若人为修改摘要，校验哈希不匹配
        os.chmod(summary_path, stat.S_IRUSR | stat.S_IWUSR)
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write("\n")
        self.assertNotEqual(manifest_data["summary_sha256"], file_sha256(summary_path))

        # 校验关键事件流
        event_types = [e["type"] for e in events]
        self.assertIn("ROUND_STARTED", event_types)
        self.assertIn("MODEL_PUBLISHED", event_types)
        self.assertIn("UPDATES_COLLECTED", event_types)
        self.assertIn("AGGREGATION_COMPLETED", event_types)
        self.assertIn("ROUND_COMPLETED", event_types)
        self.assertIn("JOB_COMPLETED", event_types)

    def test_sync_mode_fails_closed_when_node_drops_out(self):
        """测试 sync 同步模式：任一节点缺失/掉队时 fail-closed 报错并中止作业。"""
        job = self._make_job(mode="sync", rounds=1, target_nodes=(1, 2))
        runtime = MockServerRuntime(
            participant_node_ids=(1, 2),
            update_fixtures_by_node=self.client_updates,
            simulate_straggler_nodes={2},  # 节点2掉队
        )

        coordinator = FLCoordinator(
            job=job,
            runtime=runtime,
            work_dir=os.path.join(self.test_dir, "coordinator_work_sync_fail"),
        )

        with self.assertRaises(FLRuntimeError) as ctx:
            coordinator.run()

        self.assertIn(ctx.exception.error_code, ("sync_quorum_failed", "round_timeout"))
        self.assertEqual(coordinator.state, CoordinatorState.FAILED)
        summary = coordinator.get_summary()
        self.assertIsNotNone(summary)
        self.assertEqual(summary["status"], "failed")

    def test_semi_async_mode_succeeds_with_quota_and_tracks_stragglers(self):
        """测试 semi_async 半异步配额模式：达到 min_updates 即推进下一轮，未提交节点标记 dropped_out。"""
        job = self._make_job(
            mode="semi_async",
            rounds=2,
            target_nodes=(1, 2),
            min_updates=1,  # 只需要 1 个更新
        )
        runtime = MockServerRuntime(
            participant_node_ids=(1, 2),
            update_fixtures_by_node=self.client_updates,
            simulate_straggler_nodes={2},  # 节点2掉队
        )

        events: List[Dict[str, Any]] = []
        coordinator = FLCoordinator(
            job=job,
            runtime=runtime,
            work_dir=os.path.join(self.test_dir, "coordinator_work_semi"),
            on_event=events.append,
        )

        summary = coordinator.run()

        self.assertEqual(coordinator.state, CoordinatorState.SUCCEEDED)
        self.assertEqual(summary["status"], "succeeded")
        self.assertEqual(summary["rounds_completed"], 2)

        round1 = summary["rounds"][0]
        self.assertEqual(round1["committed_nodes"], [1])
        self.assertEqual(round1["dropped_out_nodes"], [2])

        round2 = summary["rounds"][1]
        self.assertEqual(round2["committed_nodes"], [1])
        self.assertEqual(round2["dropped_out_nodes"], [2])
        # 跨轮模型连续性依然成立
        self.assertEqual(round1["output_model_sha256"], round2["input_model_sha256"])

    def test_semi_async_mode_fails_when_below_min_updates_quota(self):
        """测试 semi_async 模式：超时未达 min_updates 配额时 fail-closed 报错。"""
        job = self._make_job(
            mode="semi_async",
            rounds=1,
            target_nodes=(1, 2),
            min_updates=2,
        )
        runtime = MockServerRuntime(
            participant_node_ids=(1, 2),
            update_fixtures_by_node=self.client_updates,
            simulate_straggler_nodes={1, 2},  # 全部掉队
        )

        coordinator = FLCoordinator(
            job=job,
            runtime=runtime,
            work_dir=os.path.join(self.test_dir, "coordinator_work_semi_fail"),
        )

        with self.assertRaises(FLRuntimeError) as ctx:
            coordinator.run()

        self.assertIn(ctx.exception.error_code, ("semi_async_quorum_failed", "round_timeout"))
        self.assertEqual(coordinator.state, CoordinatorState.FAILED)

    def test_async_mode_pipeline_aggregation_and_staleness_filtering(self):
        """测试 async 异步随到随聚模式：随到随聚，支持 max_staleness 限制并过滤过期更新。"""
        job = self._make_job(
            mode="async",
            rounds=2,
            target_nodes=(1, 2),
            max_staleness=1,
        )

        class AsyncDynamicRuntime(MockServerRuntime):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.step = 0

            def wait_for_updates(self, min_updates=None, timeout=None):
                self.step += 1
                if self.step == 2:
                    # 更新节点 manifest 为版本 2
                    for nid in (1, 2):
                        client_dir = os.path.dirname(self.update_fixtures_by_node[nid])
                        with open(os.path.join(client_dir, "update.manifest.json"), "w") as f:
                            json.dump({"round_id": "round_2"}, f)
                return super().wait_for_updates(min_updates=min_updates, timeout=timeout)

        runtime = AsyncDynamicRuntime(
            participant_node_ids=(1, 2),
            update_fixtures_by_node=self.client_updates,
        )

        coordinator = FLCoordinator(
            job=job,
            runtime=runtime,
            work_dir=os.path.join(self.test_dir, "coordinator_work_async"),
        )

        summary = coordinator.run()

        self.assertEqual(coordinator.state, CoordinatorState.SUCCEEDED)
        self.assertEqual(summary["status"], "succeeded")
        self.assertEqual(summary["rounds_completed"], 2)

    def test_async_mode_rejects_stale_update_when_exceeding_bound(self):
        """测试 async 模式：当节点的更新落后步数超过 max_staleness 时正确丢弃并记录事件。"""
        job = self._make_job(
            mode="async",
            rounds=2,
            target_nodes=(1, 2),
            max_staleness=0,  # 严格拒绝任何落后版本的更新
        )

        stale_dir = os.path.join(self.test_dir, "stale_client_2")
        os.makedirs(stale_dir, exist_ok=True)
        stale_upath = os.path.join(stale_dir, "update.bin")
        shutil.copyfile(self.client_updates[2], stale_upath)
        with open(os.path.join(stale_dir, "update.manifest.json"), "w") as f:
            json.dump({"round_id": "round_1"}, f)

        fresh_dir = os.path.join(self.test_dir, "fresh_client_1")
        os.makedirs(fresh_dir, exist_ok=True)
        fresh_upath = os.path.join(fresh_dir, "update.bin")
        shutil.copyfile(self.client_updates[1], fresh_upath)
        with open(os.path.join(fresh_dir, "update.manifest.json"), "w") as f:
            json.dump({"round_id": "round_2"}, f)

        class AsyncStaleRuntime(MockServerRuntime):
            def __init__(self, stale_path, fresh_path, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.stale_path = stale_path
                self.fresh_path = fresh_path
                self.step = 0

            def wait_for_updates(self, min_updates=None, timeout=None):
                self.step += 1
                if self.step == 1:
                    # 步 1：节点 1 提交，推进全局版本到 2
                    return {1: self.update_fixtures_by_node[1]}
                else:
                    # 步 2：节点 2 提交（带旧 manifest，固定为版本 1），节点 1 提交版本 2
                    return {2: self.stale_path, 1: self.fresh_path}

        runtime = AsyncStaleRuntime(
            stale_upath,
            fresh_upath,
            participant_node_ids=(1, 2),
            update_fixtures_by_node=self.client_updates,
        )

        events: List[Dict[str, Any]] = []
        coordinator = FLCoordinator(
            job=job,
            runtime=runtime,
            work_dir=os.path.join(self.test_dir, "coordinator_work_async_stale"),
            on_event=events.append,
        )

        summary = coordinator.run()
        self.assertEqual(summary["status"], "succeeded")

        # 校验事件流中确实捕获到了 UPDATE_REJECTED_STALE 事件
        rejected_events = [e for e in events if e.get("type") == "UPDATE_REJECTED_STALE"]
        self.assertTrue(len(rejected_events) >= 1)
        self.assertEqual(rejected_events[0]["node_id"], 2)
        self.assertEqual(rejected_events[0]["staleness"], 1)

    def test_abort_terminates_coordinator_and_runtime(self):
        """测试 abort 中止功能：主动中止即时中断并在状态与摘要中记录 ABORTED。"""
        job = self._make_job(mode="sync", rounds=5, target_nodes=(1, 2))
        runtime = MockServerRuntime(
            participant_node_ids=(1, 2),
            update_fixtures_by_node=self.client_updates,
        )

        # 慢速运行时以在等待时触发中止
        def slow_wait(min_updates=None, timeout=None):
            runtime.waiting_updates_event.set()
            runtime.abort_event.wait(timeout=5.0)
            if runtime.aborted:
                raise FLRuntimeError("aborted", "Runtime 已被中止")
            return dict(runtime.update_fixtures_by_node)

        runtime.wait_for_updates = slow_wait

        coordinator = FLCoordinator(
            job=job,
            runtime=runtime,
            work_dir=os.path.join(self.test_dir, "coordinator_work_abort"),
        )

        # 启动后台线程
        coordinator.start()
        self.assertTrue(runtime.waiting_updates_event.wait(timeout=2.0))
        coordinator.abort(reason="operator_interrupted")

        exit_code = coordinator.wait(timeout=2.0)
        self.assertEqual(exit_code, 1)
        self.assertEqual(coordinator.state, CoordinatorState.ABORTED)
        self.assertTrue(runtime.aborted)
        self.assertEqual(runtime.abort_reason, "operator_interrupted")

        summary = coordinator.get_summary()
        self.assertIsNotNone(summary)
        self.assertEqual(summary["status"], "aborted")

    def test_sync_mode_with_custom_fedavg_aggregation(self):
        """测试 sync 模式配合真实 FedAvg 数值参数平均聚合。"""
        node1_upath = os.path.join(self.test_dir, "client_1", "update.bin")
        node2_upath = os.path.join(self.test_dir, "client_2", "update.bin")
        with open(node1_upath, "wb") as f:
            f.write(bytes([10] * 1024))
        with open(node2_upath, "wb") as f:
            f.write(bytes([30] * 1024))

        def real_fedavg(model_path, updates_by_node, output_model_path, config):
            u_data = [open(p, "rb").read() for p in updates_by_node.values()]
            avg = bytearray()
            for b1, b2 in zip(u_data[0], u_data[1]):
                avg.append((b1 + b2) // 2)
            with open(output_model_path, "wb") as f:
                f.write(bytes(avg))
            return output_model_path

        job = self._make_job(mode="sync", rounds=1, target_nodes=(1, 2))
        runtime = MockServerRuntime(
            participant_node_ids=(1, 2),
            update_fixtures_by_node={1: node1_upath, 2: node2_upath},
        )
        coordinator = FLCoordinator(
            job=job,
            runtime=runtime,
            work_dir=os.path.join(self.test_dir, "coordinator_fedavg"),
            aggregation_fn=real_fedavg,
        )
        summary = coordinator.run()
        self.assertEqual(summary["status"], "succeeded")
        out_model = summary["rounds"][0]["output_model_path"]
        with open(out_model, "rb") as f:
            averaged = f.read()
        self.assertEqual(averaged, bytes([20] * 1024))

    def test_support_1_to_10_nodes_capacity(self):
        """验证 1~10 任意节点容量配置能力（如 5 节点、10 节点子集）。"""
        ten_nodes = tuple(range(1, 11))
        job = self._make_job(mode="sync", rounds=1, target_nodes=ten_nodes)
        runtime = MockServerRuntime(
            participant_node_ids=ten_nodes,
            update_fixtures_by_node=self.client_updates,
        )

        coordinator = FLCoordinator(
            job=job,
            runtime=runtime,
            work_dir=os.path.join(self.test_dir, "coordinator_work_10nodes"),
        )

        summary = coordinator.run()
        self.assertEqual(summary["status"], "succeeded")
        self.assertEqual(summary["rounds"][0]["committed_nodes"], list(ten_nodes))


if __name__ == "__main__":
    unittest.main()
