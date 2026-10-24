#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
WFB-ng Stage 2 FL Algorithm Runtime Coordinator.

Coordinates multi-round federated learning workflows across three collaboration paradigms:
- sync: Strict full-quorum waiting gate (all target nodes required).
- semi_async: Parameterized min_updates quota with dropout / straggler tracking.
- async: Continuous pipeline / arrive-and-aggregate with max_staleness bound.
"""

import json
import logging
import os
import stat
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple
import uuid

from .artifacts import (
    archive_file,
    file_sha256,
    inspect_artifact,
    read_json,
    validate_path_safe_identifier,
    write_json_atomic,
)
from .errors import FLRuntimeError
from .issue41_algorithm import aggregate as aggregate_models
from .radio import validate_radio_config

logger = logging.getLogger(__name__)

VALID_FL_MODES = ("sync", "semi_async", "async")


@dataclass(frozen=True)
class JobConfig:
    """Structured, validated configuration for an FL simulation job."""
    job_id: str
    run_id: str
    mode: str
    target_nodes: Tuple[int, ...]
    model_path: str
    model_size_bytes: int
    rounds: int = 1
    min_updates: int = 0
    max_staleness: int = 0
    round_timeout_seconds: float = 120.0
    io_timeout_seconds: int = 120
    live_observation: bool = False
    model_sha256: Optional[str] = None
    radio_config: Optional[Dict[str, Any]] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "JobConfig":
        if not isinstance(data, dict):
            raise FLRuntimeError("invalid_job_payload", "作业配置必须为 JSON object")

        raw_run_id = data.get("run_id")
        run_id = validate_path_safe_identifier(raw_run_id, "run_id")

        raw_job_id = data.get("job_id")
        if raw_job_id is not None:
            job_id = validate_path_safe_identifier(raw_job_id, "job_id")
        else:
            job_id = f"job_{int(time.time())}_{uuid.uuid4().hex[:6]}"

        raw_io_timeout = data.get("io_timeout_seconds")
        if raw_io_timeout is None or type(raw_io_timeout) is not int or raw_io_timeout <= 0:
            raise FLRuntimeError(
                "invalid_io_timeout",
                "缺少必填参数 'io_timeout_seconds' 或值非法，必须为大于 0 的正整数",
            )
        io_timeout_seconds = raw_io_timeout

        raw_live_obs = data.get("live_observation")
        if raw_live_obs is None or type(raw_live_obs) is not bool:
            raise FLRuntimeError(
                "invalid_live_observation",
                "缺少必填参数 'live_observation' 或值非法，必须为布尔值 (true/false)",
            )
        live_observation = raw_live_obs

        raw_nodes = data.get("target_nodes")
        if not raw_nodes or not isinstance(raw_nodes, list):
            raise FLRuntimeError("preflight_target_nodes_invalid", "必须提供非空 target_nodes 列表")
        if len(set(raw_nodes)) != len(raw_nodes):
            raise FLRuntimeError("preflight_target_nodes_invalid", "target_nodes 包含重复节点 ID")
        for nid in raw_nodes:
            if type(nid) is not int or not (1 <= nid <= 10):
                raise FLRuntimeError("preflight_target_nodes_invalid", f"包含非法节点 ID: {nid!r}")
        target_nodes = tuple(raw_nodes)

        mode = data.get("mode", "sync")
        if mode not in VALID_FL_MODES:
            raise FLRuntimeError("invalid_fl_mode", f"不支持的协同范式 '{mode}'，必须为 {VALID_FL_MODES}")

        min_updates_raw = data.get("min_updates")
        if mode == "semi_async":
            if min_updates_raw is None:
                raise FLRuntimeError("invalid_semi_async_config", "semi_async 模式必须指定 min_updates")
            if type(min_updates_raw) is not int or not (1 <= min_updates_raw <= len(target_nodes)):
                raise FLRuntimeError("invalid_semi_async_config", f"min_updates 必须在 [1, {len(target_nodes)}] 范围内的整数")
            min_updates = min_updates_raw
        elif mode == "sync":
            min_updates = len(target_nodes)
        else:  # async
            min_updates = 1

        model_path = data.get("model_path")
        if not model_path or not isinstance(model_path, str):
            raise FLRuntimeError("preflight_model_missing_path", "缺少必填参数 'model_path'")

        model_size_raw = data.get("model_size_bytes")
        if model_size_raw is None or type(model_size_raw) is not int or model_size_raw <= 0:
            raise FLRuntimeError(
                "preflight_model_missing_size",
                "必须显式提供合法正整数 'model_size_bytes' 以供确定性大小校验",
            )
        model_size_bytes = model_size_raw

        rounds_raw = data.get("rounds", 1)
        if type(rounds_raw) is not int or rounds_raw <= 0:
            raise FLRuntimeError("invalid_rounds", "rounds 轮次数必须为大于 0 的整数")
        rounds = rounds_raw

        max_staleness_raw = data.get("max_staleness", 0)
        if type(max_staleness_raw) is not int or max_staleness_raw < 0:
            raise FLRuntimeError("invalid_max_staleness", "max_staleness 必须为大于等于 0 的整数")
        max_staleness = max_staleness_raw

        round_timeout_raw = data.get("round_timeout_seconds", 120.0)
        if (type(round_timeout_raw) not in (int, float)) or round_timeout_raw <= 0:
            raise FLRuntimeError("invalid_round_timeout", "round_timeout_seconds 必须为大于 0 的数值")
        round_timeout_seconds = float(round_timeout_raw)

        model_sha256 = data.get("model_sha256")
        if model_sha256 is not None:
            if not isinstance(model_sha256, str) or len(model_sha256) != 64 or not all(c in "0123456789abcdefABCDEF" for c in model_sha256):
                raise FLRuntimeError("invalid_model_sha256", "model_sha256 必须为 64 位十六进制散列字符串")
            model_sha256 = model_sha256.lower()

        radio_cfg = data.get("radio_config")
        if radio_cfg is not None:
            if not isinstance(radio_cfg, dict):
                raise FLRuntimeError("invalid_radio_configuration", "radio_config 必须为 object")
            validate_radio_config(radio_cfg)

        return cls(
            job_id=job_id,
            run_id=run_id,
            mode=mode,
            target_nodes=target_nodes,
            model_path=model_path,
            model_size_bytes=model_size_bytes,
            rounds=rounds,
            min_updates=min_updates,
            max_staleness=max_staleness,
            round_timeout_seconds=round_timeout_seconds,
            io_timeout_seconds=io_timeout_seconds,
            live_observation=live_observation,
            model_sha256=model_sha256,
            radio_config=radio_cfg,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "run_id": self.run_id,
            "mode": self.mode,
            "target_nodes": list(self.target_nodes),
            "model_path": self.model_path,
            "model_size_bytes": self.model_size_bytes,
            "rounds": self.rounds,
            "min_updates": self.min_updates,
            "max_staleness": self.max_staleness,
            "round_timeout_seconds": self.round_timeout_seconds,
            "io_timeout_seconds": self.io_timeout_seconds,
            "live_observation": self.live_observation,
            "model_sha256": self.model_sha256,
            "radio_config": self.radio_config,
        }


class CoordinatorState(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    PUBLISHING = "PUBLISHING"
    WAITING_UPDATES = "WAITING_UPDATES"
    AGGREGATING = "AGGREGATING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"


class FLCoordinator:
    """
    Federated Learning Runtime Coordinator.
    """

    def __init__(
        self,
        job: JobConfig,
        runtime: Any,
        work_dir: Optional[str] = None,
        result_path: Optional[str] = None,
        aggregation_fn: Optional[Callable] = None,
        on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_completed: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_failed: Optional[Callable[[Exception], None]] = None,
    ):
        if not isinstance(job, JobConfig):
            raise FLRuntimeError("invalid_job_config", "job 必须为 JobConfig 实例")
        self.job = job
        self.runtime = runtime
        self.work_dir = os.path.abspath(work_dir or f"/tmp/wfb-ng-fl/server/jobs/{job.job_id}")
        self.artifact_dir = os.path.join(self.work_dir, "artifacts")
        os.makedirs(self.artifact_dir, exist_ok=True)
        write_json_atomic(os.path.join(self.work_dir, 'job_config.json'), self.job.to_dict())

        self.result_path = result_path or os.path.join(self.work_dir, "coordinator_summary.json")
        self.aggregation_fn = aggregation_fn or aggregate_models
        self.on_event = on_event
        self.on_completed = on_completed
        self.on_failed = on_failed

        self.state = CoordinatorState.IDLE
        self.events: List[Dict[str, Any]] = []
        self.rounds_summary: List[Dict[str, Any]] = []
        self._summary: Optional[Dict[str, Any]] = None

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._exit_code = 0

    def start(self) -> "FLCoordinator":
        """Start the coordinator in a background thread."""
        with self._lock:
            if self.state not in (CoordinatorState.IDLE,):
                raise FLRuntimeError("coordinator_already_started", "Coordinator 已在运行或已结束")
            self._thread = threading.Thread(
                target=self._run_wrapper,
                name=f"FLCoordinator-{self.job.job_id}",
                daemon=True,
            )
            self._thread.start()
            return self

    def _run_wrapper(self) -> None:
        try:
            summary = self.run()
            with self._lock:
                if self.state == CoordinatorState.SUCCEEDED:
                    self._exit_code = 0
                else:
                    self._exit_code = 1
            if self.state == CoordinatorState.SUCCEEDED and self.on_completed:
                try:
                    self.on_completed(summary)
                except Exception as exc:
                    logger.warning("on_completed 回调异常: %s", exc)
        except Exception as exc:
            logger.exception("FLCoordinator execution failed for job %s", self.job.job_id)
            self._exit_code = 1
            if self.on_failed:
                try:
                    self.on_failed(exc)
                except Exception as cb_exc:
                    logger.warning("on_failed 回调异常: %s", cb_exc)

    def wait(self, timeout: Optional[float] = None) -> int:
        """Wait for the coordinator thread to finish."""
        th = None
        with self._lock:
            th = self._thread
        if th is not None:
            th.join(timeout=timeout)
        return self._exit_code

    def abort(self, reason: str = "user_requested") -> None:
        """Abort coordinator execution immediately."""
        with self._lock:
            if self.state in (CoordinatorState.SUCCEEDED, CoordinatorState.FAILED, CoordinatorState.ABORTED):
                return
            self._stop_event.set()
            self.state = CoordinatorState.ABORTED
            self._emit_event("JOB_ABORTED", reason=reason)

            # Abort underlying runtime
            try:
                self.runtime.abort(reason=reason)
            except Exception as exc:
                logger.warning("Runtime abort failed: %s", exc)

            self._finalize_summary(status="aborted", error_code="aborted", error_message=reason)

    def get_summary(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._summary

    def _emit_event(self, event_type: str, **kwargs: Any) -> None:
        event = {
            "type": event_type,
            "job_id": self.job.job_id,
            "mode": self.job.mode,
            "timestamp": time.time(),
            "monotonic": time.monotonic(),
        }
        event.update(kwargs)
        with self._lock:
            self.events.append(event)
        if self.on_event is not None:
            try:
                self.on_event(event)
            except Exception as exc:
                logger.warning("Event callback raised: %s", exc)

    def _finalize_summary(
        self,
        status: str,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
        final_model_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            final_sha256 = None
            final_size = None
            if final_model_path and os.path.isfile(final_model_path):
                final_sha256 = file_sha256(final_model_path)
                final_size = os.path.getsize(final_model_path)

            summary = {
                "schema_version": 1,
                "job_id": self.job.job_id,
                "run_id": self.job.run_id,
                "mode": self.job.mode,
                "status": status,
                "rounds_total": self.job.rounds,
                "rounds_completed": len(self.rounds_summary),
                "target_nodes": list(self.job.target_nodes),
                "min_updates": self.job.min_updates,
                "max_staleness": self.job.max_staleness,
                "rounds": list(self.rounds_summary),
                "final_model_path": final_model_path,
                "final_model_sha256": final_sha256,
                "final_model_size_bytes": final_size,
                "events": list(self.events),
                "error_code": error_code,
                "error_message": error_message,
                "completed_at": time.time(),
            }
            self._summary = summary
            try:
                os.makedirs(os.path.dirname(os.path.abspath(self.result_path)), exist_ok=True)
                write_json_atomic(self.result_path, summary)
                summary_sha256 = file_sha256(self.result_path)
                audit_manifest = {
                    "schema_version": 1,
                    "artifact_type": "coordinator_summary_manifest",
                    "job_id": self.job.job_id,
                    "summary_path": self.result_path,
                    "summary_sha256": summary_sha256,
                    "timestamp": time.time(),
                }
                manifest_path = os.path.join(
                    os.path.dirname(os.path.abspath(self.result_path)),
                    "coordinator_summary.manifest.json",
                )
                write_json_atomic(manifest_path, audit_manifest)
                try:
                    os.chmod(self.result_path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
                    os.chmod(manifest_path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
                except OSError:
                    pass
            except Exception as exc:
                logger.error("写入 coordinator_summary 失败: %s", exc)
                raise FLRuntimeError("audit_persistence_failed", f"写入审计摘要失败: {exc}") from exc
            return summary

    def run(self) -> Dict[str, Any]:
        """Execute the FL coordination loop synchronously."""
        with self._lock:
            if self.state == CoordinatorState.ABORTED:
                return self._summary or {}
            self.state = CoordinatorState.RUNNING
        self._emit_event("JOB_STARTED", target_nodes=list(self.job.target_nodes), rounds=self.job.rounds)

        current_model_path = os.path.abspath(self.job.model_path)
        if not os.path.isfile(current_model_path):
            exc = FLRuntimeError("model_file_missing", f"初始模型文件缺失: {current_model_path}")
            self.state = CoordinatorState.FAILED
            self._finalize_summary(status="failed", error_code=exc.error_code, error_message=exc.error_message)
            raise exc

        try:
            if self.job.mode == "sync":
                self._run_synchronous_loop(current_model_path, min_updates=len(self.job.target_nodes), strict_quorum=True)
            elif self.job.mode == "semi_async":
                min_req = self.job.min_updates or len(self.job.target_nodes)
                self._run_synchronous_loop(current_model_path, min_updates=min_req, strict_quorum=False)
            elif self.job.mode == "async":
                self._run_async(current_model_path)
            else:
                raise FLRuntimeError("invalid_fl_mode", f"未知的协同模式: {self.job.mode}")

            with self._lock:
                if self.state == CoordinatorState.ABORTED:
                    return self._summary or {}
                self.state = CoordinatorState.SUCCEEDED
            final_path = self.rounds_summary[-1]["output_model_path"] if self.rounds_summary else current_model_path
            summary = self._finalize_summary(status="succeeded", final_model_path=final_path)
            self._emit_event("JOB_COMPLETED", summary=summary)
            return summary
        except FLRuntimeError as exc:
            with self._lock:
                if self.state == CoordinatorState.ABORTED:
                    return self._summary or {}
                self.state = CoordinatorState.FAILED
            self._finalize_summary(status="failed", error_code=exc.error_code, error_message=exc.error_message)
            self._emit_event("JOB_FAILED", error_code=exc.error_code, error_message=exc.error_message)
            raise
        except Exception as exc:
            with self._lock:
                if self.state == CoordinatorState.ABORTED:
                    return self._summary or {}
                self.state = CoordinatorState.FAILED
            msg = str(exc)
            self._finalize_summary(status="failed", error_code="coordinator_internal_error", error_message=msg)
            self._emit_event("JOB_FAILED", error_code="coordinator_internal_error", error_message=msg)
            raise FLRuntimeError("coordinator_internal_error", f"协同器运行异常: {msg}") from exc

    def _run_synchronous_loop(
        self,
        initial_model_path: str,
        min_updates: int,
        strict_quorum: bool,
    ) -> None:
        """
        Unified round execution loop for sync and semi_async modes.
        - sync: strict_quorum=True, min_updates=len(target_nodes)
        - semi_async: strict_quorum=False, min_updates=job.min_updates
        """
        target_nodes = tuple(sorted(self.job.target_nodes))
        current_model_path = initial_model_path

        for r in range(1, self.job.rounds + 1):
            if self._stop_event.is_set():
                return
            input_sha256 = file_sha256(current_model_path)
            round_start_time = time.monotonic()
            self._emit_event("ROUND_STARTED", round_index=r, input_model_sha256=input_sha256)

            # 1. Publish model
            with self._lock:
                self.state = CoordinatorState.PUBLISHING
            self._emit_event("MODEL_PUBLISH_START", round_index=r)
            self.runtime.publish_model(current_model_path)
            self._emit_event("MODEL_PUBLISHED", round_index=r, model_sha256=input_sha256)

            if self._stop_event.is_set():
                return

            # 2. Wait for updates
            with self._lock:
                self.state = CoordinatorState.WAITING_UPDATES
            self._emit_event(
                "WAIT_FOR_UPDATES_START",
                round_index=r,
                min_updates=min_updates,
                timeout=self.job.round_timeout_seconds,
            )
            updates_by_node = self.runtime.wait_for_updates(
                min_updates=min_updates,
                timeout=self.job.round_timeout_seconds,
            )
            submitted_nodes = sorted(updates_by_node.keys())

            if strict_quorum:
                if set(submitted_nodes) != set(target_nodes):
                    raise FLRuntimeError(
                        "sync_quorum_failed",
                        f"同步模式必须收齐全员: 期望 {list(target_nodes)}, 实际收齐 {submitted_nodes}",
                    )
            else:
                if len(submitted_nodes) < min_updates:
                    raise FLRuntimeError(
                        "semi_async_quorum_failed",
                        f"半异步模式未达到最小配额: 期望 {min_updates}, 实际 {len(submitted_nodes)}",
                    )

            dropped_out_nodes = sorted(set(target_nodes) - set(submitted_nodes))
            self._emit_event(
                "UPDATES_COLLECTED",
                round_index=r,
                submitted_nodes=submitted_nodes,
                dropped_out_nodes=dropped_out_nodes,
            )

            if self._stop_event.is_set():
                return

            # 3. Aggregate
            with self._lock:
                self.state = CoordinatorState.AGGREGATING
            self._emit_event(
                "AGGREGATE_START",
                round_index=r,
                submitted_nodes=submitted_nodes,
                dropped_out_nodes=dropped_out_nodes,
            )
            output_model_path = os.path.join(self.artifact_dir, f"global-model-round-{r:04d}.bin")
            self.aggregation_fn(
                current_model_path,
                updates_by_node,
                output_model_path,
                self.job.to_dict(),
            )
            output_sha256 = file_sha256(output_model_path)
            self._emit_event("AGGREGATION_COMPLETED", round_index=r, output_model_sha256=output_sha256)

            round_summary = {
                "round_index": r,
                "input_model_path": current_model_path,
                "input_model_sha256": input_sha256,
                "output_model_path": output_model_path,
                "output_model_sha256": output_sha256,
                "committed_nodes": submitted_nodes,
                "dropped_out_nodes": dropped_out_nodes,
                "duration_seconds": round(time.monotonic() - round_start_time, 3),
            }
            self.rounds_summary.append(round_summary)
            self._emit_event("ROUND_COMPLETED", round_index=r, summary=round_summary)

            current_model_path = output_model_path

    def _get_update_base_version(
        self,
        node_id: int,
        update_path: str,
        round_version_map: Dict[str, int],
    ) -> int:
        """Read the update base version from its manifest."""
        update_dir = os.path.dirname(os.path.abspath(update_path))
        manifest_path = os.path.join(update_dir, "update.manifest.json")
        if not os.path.isfile(manifest_path):
            raise FLRuntimeError(
                "invalid_update_manifest",
                f"节点 {node_id} 的 update.manifest.json 缺失",
            )
        manifest = read_json(manifest_path)
        rid = manifest.get("round_id")
        if rid is not None and str(rid) in round_version_map:
            return round_version_map[str(rid)]

        raise FLRuntimeError(
            "invalid_update_manifest",
            f"节点 {node_id} 的更新 round_id={rid!r} 不属于已知轮次版本",
        )

    def _run_async(self, initial_model_path: str) -> None:
        """
        Continuous pipeline / arrive-and-aggregate mode:
        Enforces true max_staleness bounds on arriving updates.
        """
        target_nodes = tuple(sorted(self.job.target_nodes))
        current_model_path = initial_model_path
        current_version = 1
        round_version_map: Dict[str, int] = {}

        for r in range(1, self.job.rounds + 1):
            if self._stop_event.is_set():
                return
            input_sha256 = file_sha256(current_model_path)
            step_start_time = time.monotonic()
            self._emit_event(
                "ASYNC_STEP_START",
                step_index=r,
                model_version=current_version,
                input_model_sha256=input_sha256,
            )

            # 1. Publish latest global model
            with self._lock:
                self.state = CoordinatorState.PUBLISHING
            self.runtime.publish_model(current_model_path)
            active_rid = getattr(self.runtime, "round_id", None)
            if active_rid is not None:
                round_version_map[str(active_rid)] = current_version

            if self._stop_event.is_set():
                return

            # 2. Wait for at least 1 update and aggregate
            with self._lock:
                self.state = CoordinatorState.WAITING_UPDATES

            step_committed: List[int] = []
            deadline = time.monotonic() + self.job.round_timeout_seconds
            while time.monotonic() < deadline and not self._stop_event.is_set():
                remaining = max(0.1, deadline - time.monotonic())
                updates_by_node = self.runtime.wait_for_updates(
                    min_updates=1,
                    timeout=remaining,
                )

                for nid, upath in updates_by_node.items():
                    if nid not in target_nodes:
                        continue

                    base_ver = self._get_update_base_version(nid, upath, round_version_map)
                    staleness = max(0, current_version - base_ver)
                    if staleness > self.job.max_staleness:
                        self._emit_event(
                            "UPDATE_REJECTED_STALE",
                            node_id=nid,
                            staleness=staleness,
                            max_staleness=self.job.max_staleness,
                            base_version=base_ver,
                            global_version=current_version,
                        )
                        continue

                    # Accepted: aggregate into pipeline
                    with self._lock:
                        self.state = CoordinatorState.AGGREGATING
                    output_model_path = os.path.join(self.artifact_dir, f"global-model-round-{r:04d}.bin")
                    self.aggregation_fn(
                        current_model_path,
                        {nid: upath},
                        output_model_path,
                        self.job.to_dict(),
                    )
                    step_committed.append(nid)
                    current_model_path = output_model_path
                    current_version += 1
                    break

                if step_committed:
                    break

            if not step_committed and not self._stop_event.is_set():
                raise FLRuntimeError("async_step_timeout", f"异步流水线第 {r} 步未在时限内收到有效更新")

            output_sha256 = file_sha256(current_model_path)
            round_summary = {
                "round_index": r,
                "input_model_path": initial_model_path if r == 1 else self.rounds_summary[-1]["output_model_path"],
                "input_model_sha256": input_sha256,
                "output_model_path": current_model_path,
                "output_model_sha256": output_sha256,
                "committed_nodes": step_committed,
                "dropped_out_nodes": sorted(set(target_nodes) - set(step_committed)),
                "duration_seconds": round(time.monotonic() - step_start_time, 3),
            }
            self.rounds_summary.append(round_summary)
            self._emit_event("ASYNC_STEP_COMPLETED", step_index=r, output_model_sha256=output_sha256)
