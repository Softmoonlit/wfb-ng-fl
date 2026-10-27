#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""严格同步联邦作业的服务端协调器。

Coordinator 只负责服务端发布、收齐、聚合调用和轮次推进；算法配置、
评估结果与模型格式由显式部署的算法入口负责。
"""

from copy import deepcopy
import logging
import math
import os
import stat
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple, cast
import uuid

from .algorithm import (
    AlgorithmContext,
    freeze_algorithm_config,
    validate_evaluation_result,
)
from .artifacts import file_sha256, preflight_artifact, validate_path_safe_identifier, write_json_atomic
from .errors import FLRuntimeError
from .radio import validate_radio_config


logger = logging.getLogger(__name__)

# 只允许严格同步模式。
VALID_FL_MODES = ("sync",)


class _JobSupervisor:
    """为单个 Coordinator 维护绝对期限和首因取消。"""

    def __init__(
        self,
        *,
        job_id: str,
        round_timeout_seconds: float,
        io_timeout_seconds: float,
        stop_event: threading.Event,
        runtime: Any,
        cancel_callback: Optional[Callable[[str], None]],
    ) -> None:
        self._job_id = job_id
        self._round_timeout_seconds = round_timeout_seconds
        self._io_timeout_seconds = io_timeout_seconds
        self._stop_event = stop_event
        self._runtime_abort = getattr(runtime, "abort", None)
        self._cancel_callback = cancel_callback
        self._condition = threading.Condition()
        self._thread: Optional[threading.Thread] = None
        self._started = False
        self._closed = False
        self._round_index: Optional[int] = None
        self._round_started_monotonic: Optional[float] = None
        self._round_deadline: Optional[float] = None
        self._active_deadline: Optional[float] = None
        self._active_error_code: Optional[str] = None
        self._cause: Optional[str] = None
        self._cause_reason: Optional[str] = None
        self._cause_round_index: Optional[int] = None
        self._cancel_dispatched = False

    def start(self) -> None:
        with self._condition:
            if self._started:
                return
            if self._closed:
                raise FLRuntimeError("coordinator_already_started", "作业 supervisor 已关闭")
            self._started = True
            self._thread = threading.Thread(
                target=self._watch,
                name=f"FLCoordinatorDeadline-{self._job_id}",
                daemon=True,
            )
            self._thread.start()

    def begin_round(self, round_index: int) -> Dict[str, float]:
        dispatch = False
        with self._condition:
            dispatch = self._expire_locked(time.monotonic())
            if self._cause is not None:
                error = self._error_locked()
            elif self._round_index is not None:
                error = FLRuntimeError("round_in_progress", "已有活动轮次预算")
            else:
                started = time.monotonic()
                deadline = started + self._round_timeout_seconds
                self._round_index = round_index
                self._round_started_monotonic = started
                self._round_deadline = deadline
                self._active_deadline = deadline
                self._active_error_code = "round_timeout"
                self._condition.notify_all()
                error = None
        if dispatch:
            self._dispatch_cancel()
        if error is not None:
            raise error
        assert self._round_started_monotonic is not None
        assert self._round_deadline is not None
        return {
            "started_monotonic": self._round_started_monotonic,
            "deadline_monotonic": self._round_deadline,
        }

    def begin_io(self) -> Dict[str, float]:
        dispatch = False
        with self._condition:
            dispatch = self._expire_locked(time.monotonic())
            if self._cause is not None:
                error = self._error_locked()
            elif self._round_deadline is None or self._round_index is None:
                error = FLRuntimeError("round_not_started", "轮次预算尚未开启")
            else:
                started = time.monotonic()
                io_deadline = min(
                    self._round_deadline,
                    started + self._io_timeout_seconds,
                )
                self._active_deadline = io_deadline
                # 同一时刻到达时由完整轮次预算裁决，避免把 round 失败伪装成 I/O 失败。
                self._active_error_code = (
                    "io_timeout" if started + self._io_timeout_seconds < self._round_deadline
                    else "round_timeout"
                )
                self._condition.notify_all()
                error = None
        if dispatch:
            self._dispatch_cancel()
        if error is not None:
            raise error
        assert self._active_deadline is not None
        return {
            "started_monotonic": started,
            "deadline_monotonic": self._active_deadline,
        }

    def end_io(self) -> None:
        dispatch = False
        with self._condition:
            # 必须先裁决 publish 的旧 I/O 期限，再把活动阶段切回 round。
            dispatch = self._expire_locked(time.monotonic())
            if self._cause is not None:
                error = self._error_locked()
            elif self._round_deadline is None:
                error = FLRuntimeError("round_not_started", "轮次预算尚未开启")
            else:
                self._active_deadline = self._round_deadline
                self._active_error_code = "round_timeout"
                self._condition.notify_all()
                error = None
        if dispatch:
            self._dispatch_cancel()
        if error is not None:
            raise error
        self.check()

    def end_round(self, round_index: int) -> None:
        self._finish_active(round_index)
        with self._condition:
            if self._cause is not None:
                error = self._error_locked()
            elif self._round_index != round_index:
                error = FLRuntimeError("round_not_started", "轮次预算身份不匹配")
            else:
                self._round_index = None
                self._round_started_monotonic = None
                self._round_deadline = None
                self._active_deadline = None
                self._active_error_code = None
                self._condition.notify_all()
                error = None
        if error is not None:
            raise error

    def cancel_round(self, round_index: int) -> None:
        # 取消轮次也必须先裁决当前期限，不能清理期限后丢失首因。
        dispatch = False
        with self._condition:
            dispatch = self._expire_locked(time.monotonic())
            if self._cause is not None:
                error = self._error_locked()
            elif self._round_index == round_index:
                self._round_index = None
                self._round_started_monotonic = None
                self._round_deadline = None
                self._active_deadline = None
                self._active_error_code = None
                self._condition.notify_all()
                error = None
            else:
                error = None
        if dispatch:
            self._dispatch_cancel()
        if error is not None:
            raise error

    def check(self) -> None:
        with self._condition:
            dispatch = self._expire_locked(time.monotonic())
            error = self._error_locked() if self._cause is not None else None
        if dispatch:
            self._dispatch_cancel()
        if error is not None:
            raise error

    def request_abort(self, reason: str) -> bool:
        with self._condition:
            # 先在同一临界区裁决已到期的期限，晚到的人工停止不能覆盖 timeout 首因。
            expired = self._expire_locked(time.monotonic())
            if self._cause is not None:
                error = self._cause
            else:
                self._cause = "aborted"
                self._cause_reason = reason or "user_requested"
                self._cause_round_index = self._round_index
                self._stop_event.set()
                self._condition.notify_all()
                error = None
        if expired or error is None:
            self._dispatch_cancel()
        return error is None

    @property
    def current_round_deadline(self) -> Optional[float]:
        """返回当前轮次的绝对 monotonic deadline；无活动轮次时为 None。"""
        with self._condition:
            return self._round_deadline

    @property
    def cause(self) -> Optional[str]:
        with self._condition:
            return self._cause

    def cancellation_error(self) -> Optional[FLRuntimeError]:
        with self._condition:
            return self._error_locked() if self._cause is not None else None

    def close(self) -> None:
        self._dispatch_cancel()
        with self._condition:
            self._closed = True
            self._active_deadline = None
            self._round_index = None
            self._round_started_monotonic = None
            self._round_deadline = None
            self._active_error_code = None
            self._condition.notify_all()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join()

    def _finish_active(self, phase: Any) -> None:
        dispatch = False
        with self._condition:
            dispatch = self._expire_locked(time.monotonic())
            if self._cause is not None:
                error = self._error_locked()
            elif phase == "io":
                error = FLRuntimeError("invalid_deadline_transition", "I/O 阶段不能通过通用路径切换")
            elif self._round_index != phase:
                error = FLRuntimeError("round_not_started", "轮次预算身份不匹配")
            else:
                error = None
            if error is None:
                self._condition.notify_all()
        if dispatch:
            self._dispatch_cancel()
        if error is not None:
            raise error

    def _expire_locked(self, now: float) -> bool:
        if (self._cause is not None or self._active_deadline is None or
                now < self._active_deadline):
            return False
        self._cause = self._active_error_code or "round_timeout"
        self._cause_reason = self._cause
        self._cause_round_index = self._round_index
        self._stop_event.set()
        self._condition.notify_all()
        return True

    def _error_locked(self) -> FLRuntimeError:
        cause = self._cause
        round_index = self._cause_round_index
        if cause == "aborted":
            return FLRuntimeError(
                "aborted",
                self._cause_reason or "作业被主动中止",
                details={"round_index": round_index},
            )
        if cause == "io_timeout":
            return FLRuntimeError(
                "io_timeout",
                f"第 {round_index} 轮模型发布 I/O 超过配置期限",
                details={"round_index": round_index},
            )
        return FLRuntimeError(
            "round_timeout",
            f"第 {round_index} 轮超过配置期限",
            details={"round_index": round_index},
        )

    def _dispatch_cancel(self) -> None:
        with self._condition:
            if self._cause is None or self._cancel_dispatched:
                return
            self._cancel_dispatched = True
            reason = self._cause_reason or self._cause
        if self._cancel_callback is not None:
            try:
                self._cancel_callback(reason)
            except Exception as exc:
                logger.warning("作业取消 callback 异常: %s", exc)
        if callable(self._runtime_abort):
            try:
                self._runtime_abort(reason=reason)
            except Exception as exc:
                logger.warning("Runtime abort failed: %s", exc)

    def _watch(self) -> None:
        while True:
            with self._condition:
                while (not self._closed and
                       (self._active_deadline is None or self._cause is not None)):
                    self._condition.wait()
                if self._closed:
                    return
                deadline = self._active_deadline
                assert deadline is not None
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    self._condition.wait(remaining)
                    continue
                dispatch = self._expire_locked(time.monotonic())
            if dispatch:
                self._dispatch_cancel()


@dataclass(frozen=True)
class JobConfig:
    """单项严格同步作业的已校验配置。"""

    job_id: str
    run_id: str
    mode: str
    target_nodes: Tuple[int, ...]
    model_path: str
    model_size_bytes: int
    rounds: int = 5
    round_timeout_seconds: float = 120.0
    io_timeout_seconds: float = 120.0
    live_observation: bool = False
    model_sha256: Optional[str] = None
    radio_config: Optional[Dict[str, Any]] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "JobConfig":
        if not isinstance(data, dict):
            raise FLRuntimeError("invalid_job_payload", "作业配置必须为 JSON object")
        legacy_fields = {"min_updates", "max_staleness"}.intersection(data)
        if legacy_fields:
            raise FLRuntimeError(
                "invalid_job_config",
                f"作业配置包含已移除字段: {sorted(legacy_fields)}",
            )

        run_id = validate_path_safe_identifier(data.get("run_id"), "run_id")

        raw_job_id = data.get("job_id")
        if raw_job_id is not None:
            job_id = validate_path_safe_identifier(raw_job_id, "job_id")
        else:
            job_id = f"job_{int(time.time())}_{uuid.uuid4().hex[:6]}"

        raw_io_timeout = data.get("io_timeout_seconds")
        if (isinstance(raw_io_timeout, bool) or
                not isinstance(raw_io_timeout, (int, float)) or
                not math.isfinite(float(raw_io_timeout)) or
                raw_io_timeout <= 0):
            raise FLRuntimeError(
                "invalid_io_timeout",
                "缺少必填参数 'io_timeout_seconds' 或值非法，必须为有限正数",
            )

        raw_live_obs = data.get("live_observation")
        if raw_live_obs is None or type(raw_live_obs) is not bool:
            raise FLRuntimeError(
                "invalid_live_observation",
                "缺少必填参数 'live_observation' 或值非法，必须为布尔值 (true/false)",
            )

        raw_nodes = data.get("target_nodes")
        if not raw_nodes or not isinstance(raw_nodes, list):
            raise FLRuntimeError("preflight_target_nodes_invalid", "必须提供非空 target_nodes 列表")
        if len(set(raw_nodes)) != len(raw_nodes):
            raise FLRuntimeError("preflight_target_nodes_invalid", "target_nodes 包含重复节点 ID")
        for node_id in raw_nodes:
            if type(node_id) is not int or not 1 <= node_id <= 10:
                raise FLRuntimeError(
                    "preflight_target_nodes_invalid", f"包含非法节点 ID: {node_id!r}"
                )
        target_nodes = tuple(raw_nodes)

        mode = data.get("mode", "sync")
        if mode not in VALID_FL_MODES:
            raise FLRuntimeError(
                "invalid_job_config",
                f"不支持的协同范式 '{mode}'，当前只支持严格同步模式",
            )

        model_path = data.get("model_path")
        if not model_path or not isinstance(model_path, str):
            raise FLRuntimeError("preflight_model_missing_path", "缺少必填参数 'model_path'")

        model_size = data.get("model_size_bytes")
        if model_size is None or type(model_size) is not int or model_size <= 0:
            raise FLRuntimeError(
                "preflight_model_missing_size",
                "必须显式提供合法正整数 'model_size_bytes' 以供确定性大小校验",
            )

        raw_rounds = data.get("rounds", 5)
        if type(raw_rounds) is not int or raw_rounds <= 0:
            raise FLRuntimeError("invalid_rounds", "rounds 轮次数必须为大于 0 的整数")

        raw_round_timeout = data.get("round_timeout_seconds", 120.0)
        if (isinstance(raw_round_timeout, bool) or
                not isinstance(raw_round_timeout, (int, float)) or
                not math.isfinite(float(raw_round_timeout)) or
                raw_round_timeout <= 0):
            raise FLRuntimeError(
                "invalid_round_timeout", "round_timeout_seconds 必须为有限正数"
            )

        model_sha256 = data.get("model_sha256")
        if model_sha256 is not None:
            if (
                not isinstance(model_sha256, str)
                or len(model_sha256) != 64
                or not all(char in "0123456789abcdefABCDEF" for char in model_sha256)
            ):
                raise FLRuntimeError(
                    "invalid_model_sha256", "model_sha256 必须为 64 位十六进制散列字符串"
                )
            model_sha256 = model_sha256.lower()

        radio_config = data.get("radio_config")
        if radio_config is not None:
            if not isinstance(radio_config, dict):
                raise FLRuntimeError("invalid_radio_configuration", "radio_config 必须为 object")
            validate_radio_config(radio_config)

        return cls(
            job_id=job_id,
            run_id=run_id,
            mode=mode,
            target_nodes=target_nodes,
            model_path=model_path,
            model_size_bytes=model_size,
            rounds=raw_rounds,
            round_timeout_seconds=float(raw_round_timeout),
            io_timeout_seconds=float(raw_io_timeout),
            live_observation=raw_live_obs,
            model_sha256=model_sha256,
            radio_config=radio_config,
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
            "round_timeout_seconds": self.round_timeout_seconds,
            "io_timeout_seconds": self.io_timeout_seconds,
            "live_observation": self.live_observation,
            "model_sha256": self.model_sha256,
            "radio_config": deepcopy(self.radio_config),
        }


class CoordinatorState(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    PUBLISHING = "PUBLISHING"
    WAITING_UPDATES = "WAITING_UPDATES"
    AGGREGATING = "AGGREGATING"
    WAITING_ROUND_REPORT = "WAITING_ROUND_REPORT"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"


class FLCoordinator:
    """拥有严格同步作业的模型发布、收齐、算法调用和轮次推进。"""

    def __init__(
        self,
        job: JobConfig,
        runtime: Any,
        work_dir: Optional[str] = None,
        result_path: Optional[str] = None,
        *,
        aggregate_fn: Callable[[str, Mapping[int, str], str, Mapping[str, Any], AlgorithmContext], None],
        evaluate_fn: Callable[[str, Mapping[str, Any], AlgorithmContext], Mapping[str, Any]],
        algorithm_config: Mapping[str, Any],
        context: AlgorithmContext,
        initial_metrics: Mapping[str, Any],
        prepare_round: Callable[[int], None],
        round_completion: Callable[[int], None],
        on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_completed: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_failed: Optional[Callable[[Exception], None]] = None,
        cancel_callback: Optional[Callable[[str], None]] = None,
    ):
        if not isinstance(job, JobConfig):
            raise FLRuntimeError("invalid_job_config", "job 必须为 JobConfig 实例")
        if job.mode != "sync":
            raise FLRuntimeError("invalid_job_config", "Coordinator 只接受严格同步作业")
        if not callable(aggregate_fn) or not callable(evaluate_fn):
            raise FLRuntimeError(
                "invalid_algorithm_configuration",
                "必须显式提供 aggregate_fn 和 evaluate_fn",
            )
        if not isinstance(algorithm_config, Mapping):
            raise FLRuntimeError("invalid_algorithm_configuration", "algorithm_config 必须为 object")
        if not isinstance(initial_metrics, Mapping):
            raise FLRuntimeError("invalid_initial_metrics", "initial_metrics 必须为 object")
        if not callable(getattr(context, "for_phase", None)) or not callable(
            getattr(context, "check_stop", None)
        ):
            raise FLRuntimeError(
                "invalid_algorithm_context",
                "context 必须提供 for_phase(...) 和 check_stop()",
            )
        stop_event = getattr(context, "stop_event", None)
        if not callable(getattr(stop_event, "is_set", None)) or not callable(
            getattr(stop_event, "set", None)
        ):
            raise FLRuntimeError(
                "invalid_algorithm_context",
                "context 必须提供共享 stop_event",
            )
        if not callable(prepare_round) or not callable(round_completion):
            raise FLRuntimeError(
                "invalid_round_callbacks",
                "必须显式提供 prepare_round(index) 和 round_completion(index)",
            )
        if cancel_callback is not None and not callable(cancel_callback):
            raise FLRuntimeError(
                "invalid_cancel_callback", "cancel_callback 必须可调用"
            )

        self.job = job
        self.runtime = runtime
        self.work_dir = os.path.abspath(work_dir or f"/tmp/wfb-ng-fl/server/jobs/{job.job_id}")
        self.artifact_dir = os.path.join(self.work_dir, "artifacts")
        os.makedirs(self.artifact_dir, exist_ok=True)
        write_json_atomic(os.path.join(self.work_dir, "job_config.json"), self.job.to_dict())

        self.result_path = result_path or os.path.join(self.work_dir, "coordinator_summary.json")
        self.aggregate_fn = aggregate_fn
        self.evaluate_fn = evaluate_fn
        self.algorithm_config = freeze_algorithm_config(algorithm_config)
        self.context = context
        self.initial_metrics = deepcopy(dict(initial_metrics))
        self.effect_result: Optional[Dict[str, Any]] = None
        self._last_model_path = os.path.abspath(job.model_path)
        self.prepare_round = prepare_round
        self.round_completion = round_completion
        self.on_event = on_event
        self.on_completed = on_completed
        self.on_failed = on_failed
        self.cancel_callback = cancel_callback

        self.state = CoordinatorState.IDLE
        self.events: List[Dict[str, Any]] = []
        self.rounds_summary: List[Dict[str, Any]] = []
        self._summary: Optional[Dict[str, Any]] = None

        self._lock = threading.RLock()
        self._stop_event: threading.Event = cast(threading.Event, stop_event)
        self._thread: Optional[threading.Thread] = None
        self._exit_code = 0
        self._supervisor = _JobSupervisor(
            job_id=job.job_id,
            round_timeout_seconds=job.round_timeout_seconds,
            io_timeout_seconds=job.io_timeout_seconds,
            stop_event=self._stop_event,
            runtime=runtime,
            cancel_callback=cancel_callback,
        )

    def start(self) -> "FLCoordinator":
        """在作业资源已装配后启动后台协调线程。"""
        with self._lock:
            if self.state != CoordinatorState.IDLE:
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
                self._exit_code = 0 if self.state == CoordinatorState.SUCCEEDED else 1
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
                except Exception as callback_error:
                    logger.warning("on_failed 回调异常: %s", callback_error)

    def wait(self, timeout: Optional[float] = None) -> int:
        """等待协调线程；终态回调在线程自身执行时不进行 self-join。"""
        with self._lock:
            thread = self._thread
        if thread is None or thread is threading.current_thread():
            return self._exit_code
        thread.join(timeout=timeout)
        if thread.is_alive():
            code = "coordinator_wait_timeout" if timeout is not None else "coordinator_wait_failed"
            message = (
                "Coordinator 在配置等待期限内仍未退出"
                if timeout is not None
                else "Coordinator 等待返回时线程仍在运行"
            )
            raise FLRuntimeError(code, message)
        return self._exit_code

    def abort(self, reason: str = "user_requested") -> None:
        """请求协作停止；终态须等协调线程和计算真正退出后裁决。"""
        with self._lock:
            if self.state in (CoordinatorState.SUCCEEDED, CoordinatorState.FAILED, CoordinatorState.ABORTED):
                return
        self._supervisor.request_abort(reason)

    def get_summary(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return deepcopy(self._summary)

    @property
    def current_round_deadline(self) -> Optional[float]:
        """当前轮次绝对 monotonic deadline 的只读快照。"""
        return self._supervisor.current_round_deadline

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
                # 计算和提交确认完整执行后，效果未改善仍单独保留执行成功事实。
                "execution_result": (
                    "succeeded" if len(self.rounds_summary) == self.job.rounds
                    and all(item['client_submission_returns_received'] for item in self.rounds_summary)
                    else status
                ),
                "rounds_total": self.job.rounds,
                "rounds_completed": len(self.rounds_summary),
                "target_nodes": list(self.job.target_nodes),
                "initial_metrics": deepcopy(self.initial_metrics),
                "effect_result": deepcopy(self.effect_result),
                "rounds": deepcopy(self.rounds_summary),
                "final_model_path": final_model_path,
                "final_model_sha256": final_sha256,
                "final_model_size_bytes": final_size,
                "events": deepcopy(self.events),
                "error_code": error_code,
                "error_message": error_message,
                "completed_at": time.time(),
            }
            self._summary = summary
            try:
                result_dir = os.path.dirname(os.path.abspath(self.result_path))
                os.makedirs(result_dir, exist_ok=True)
                write_json_atomic(self.result_path, summary)
                summary_sha256 = file_sha256(self.result_path)
                manifest_path = os.path.join(result_dir, "coordinator_summary.manifest.json")
                write_json_atomic(
                    manifest_path,
                    {
                        "schema_version": 1,
                        "artifact_type": "coordinator_summary_manifest",
                        "job_id": self.job.job_id,
                        "summary_path": self.result_path,
                        "summary_sha256": summary_sha256,
                        "timestamp": time.time(),
                    },
                )
                try:
                    os.chmod(self.result_path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
                    os.chmod(manifest_path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
                except OSError:
                    pass
            except Exception as exc:
                logger.error("写入 coordinator_summary 失败: %s", exc)
                raise FLRuntimeError("audit_persistence_failed", f"写入审计摘要失败: {exc}") from exc
            return deepcopy(summary)

    def _check_stop(self) -> None:
        cancellation_error = self._supervisor.cancellation_error()
        if cancellation_error is not None:
            raise cancellation_error
        if self._stop_event.is_set():
            raise FLRuntimeError("aborted", "协同作业被主动中止")
        self.context.check_stop()

    def _phase_context(self, phase: str, round_index: int) -> AlgorithmContext:
        try:
            return self.context.for_phase(
                phase,
                round_index=round_index,
                initial_metrics=deepcopy(self.initial_metrics),
            )
        except FLRuntimeError:
            raise
        except Exception as exc:
            raise FLRuntimeError(
                "invalid_algorithm_context", "无法派生算法阶段上下文"
            ) from exc

    def _call_aggregate(
        self,
        current_model_path: str,
        updates_by_node: Mapping[int, str],
        output_model_path: str,
        round_index: int,
    ) -> None:
        context = self._phase_context("aggregate", round_index)
        try:
            result = self.aggregate_fn(
                current_model_path,
                updates_by_node,
                output_model_path,
                self.algorithm_config,
                context,
            )
        except FLRuntimeError:
            raise
        except TypeError as exc:
            raise FLRuntimeError(
                "algorithm_contract_error",
                "aggregate_fn 必须使用五参数接口",
            ) from exc
        except Exception as exc:
            raise FLRuntimeError("algorithm_failed", "聚合函数执行失败") from exc
        if result is not None:
            raise FLRuntimeError("algorithm_contract_error", "aggregate_fn 必须返回 None")
        if not os.path.isfile(output_model_path):
            raise FLRuntimeError("aggregate_output_missing", "聚合函数未生成输出模型")

    def _call_evaluate(self, model_path: str, round_index: int) -> Dict[str, Any]:
        context = self._phase_context("evaluate", round_index)
        try:
            result = self.evaluate_fn(model_path, self.algorithm_config, context)
        except FLRuntimeError:
            raise
        except TypeError as exc:
            raise FLRuntimeError(
                "algorithm_contract_error",
                "evaluate_fn 必须使用三参数接口",
            ) from exc
        except Exception as exc:
            raise FLRuntimeError("algorithm_failed", "评估函数执行失败") from exc
        evaluation = validate_evaluation_result(result)
        if evaluation["preparation"] is not None:
            raise FLRuntimeError(
                "invalid_evaluation_result",
                "非初始评估的 preparation 必须为 None",
            )
        if round_index != self.job.rounds:
            if evaluation["verdict"] is not None or evaluation["reason"] is not None:
                raise FLRuntimeError(
                    "invalid_evaluation_result",
                    "中间轮评估的 verdict 和 reason 必须为 None",
                )
        else:
            if type(evaluation["verdict"]) is not bool:
                raise FLRuntimeError(
                    "invalid_evaluation_result",
                    "末轮评估的 verdict 必须为布尔值",
                )
            reason = evaluation["reason"]
            if (not isinstance(reason, str) or not reason.strip()
                    or len(reason.encode("utf-8")) > 512):
                raise FLRuntimeError(
                    "invalid_evaluation_result",
                    "末轮评估的 reason 必须为非空且不超过 512 字节的 UTF-8 字符串",
                )
        return evaluation

    def run(self) -> Dict[str, Any]:
        """使用准备阶段传入的 M0 指标执行严格同步轮次。"""
        with self._lock:
            if self.state == CoordinatorState.ABORTED:
                return self._summary or {}
            if self.state != CoordinatorState.IDLE:
                raise FLRuntimeError("coordinator_already_started", "Coordinator 已在运行或已结束")
            self.state = CoordinatorState.RUNNING
        self._emit_event(
            "JOB_STARTED",
            target_nodes=list(self.job.target_nodes),
            rounds=self.job.rounds,
            initial_metrics=deepcopy(self.initial_metrics),
        )

        current_model_path = os.path.abspath(self.job.model_path)
        if not os.path.isfile(current_model_path):
            exc = FLRuntimeError("model_file_missing", f"初始模型文件缺失: {current_model_path}")
            with self._lock:
                self.state = CoordinatorState.FAILED
            summary = self._finalize_summary(
                status="failed",
                error_code=exc.error_code,
                error_message=exc.error_message,
                final_model_path=self._last_model_path,
            )
            self._emit_event(
                "JOB_FAILED",
                error_code=exc.error_code,
                error_message=exc.error_message,
                summary=summary,
            )
            raise exc

        self._supervisor.start()
        try:
            current_model_path = self._run_synchronous_loop(current_model_path)
            with self._lock:
                self.state = CoordinatorState.SUCCEEDED
            summary = self._finalize_summary(status="succeeded", final_model_path=current_model_path)
            self._emit_event("JOB_COMPLETED", summary=summary)
            return summary
        except FLRuntimeError as exc:
            failure_exc = self._supervisor.cancellation_error() or exc
            aborted = failure_exc.error_code == "aborted"
            with self._lock:
                self.state = CoordinatorState.ABORTED if aborted else CoordinatorState.FAILED
            summary = self._finalize_summary(
                status="aborted" if aborted else "failed",
                error_code=failure_exc.error_code,
                error_message=failure_exc.error_message,
                final_model_path=self._last_model_path,
            )
            if aborted:
                self._emit_event("JOB_ABORTED", reason=failure_exc.error_message)
            else:
                self._emit_event(
                    "JOB_FAILED",
                    error_code=failure_exc.error_code,
                    error_message=failure_exc.error_message,
                    summary=summary,
                )
            raise failure_exc
        except Exception as exc:
            terminal_exc: Optional[FLRuntimeError] = self._supervisor.cancellation_error()
            if terminal_exc is None:
                terminal_exc = FLRuntimeError(
                    "coordinator_internal_error", f"协同器运行异常: {exc}"
                )
            aborted = terminal_exc.error_code == "aborted"
            with self._lock:
                self.state = CoordinatorState.ABORTED if aborted else CoordinatorState.FAILED
            summary = self._finalize_summary(
                status="aborted" if aborted else "failed",
                error_code=terminal_exc.error_code,
                error_message=terminal_exc.error_message,
                final_model_path=self._last_model_path,
            )
            if aborted:
                self._emit_event("JOB_ABORTED", reason=terminal_exc.error_message)
            else:
                self._emit_event(
                    "JOB_FAILED",
                    error_code=terminal_exc.error_code,
                    error_message=terminal_exc.error_message,
                    summary=summary,
                )
            raise terminal_exc from exc
        finally:
            self._supervisor.close()

    def _run_synchronous_loop(self, initial_model_path: str) -> str:
        target_nodes = tuple(sorted(self.job.target_nodes))
        current_model_path = initial_model_path

        for round_index in range(1, self.job.rounds + 1):
            round_budget = self._supervisor.begin_round(round_index)
            round_start = round_budget["started_monotonic"]
            round_deadline = round_budget["deadline_monotonic"]
            self._check_stop()
            input_sha256 = file_sha256(current_model_path)
            self._emit_event(
                "ROUND_STARTED",
                round_index=round_index,
                input_model_sha256=input_sha256,
                round_started_monotonic=round_start,
                round_deadline_monotonic=round_deadline,
            )

            # Daemon 在客户端可能收到当前模型前先打开本轮完成报告门。
            self.prepare_round(round_index)
            self._check_stop()

            with self._lock:
                self.state = CoordinatorState.PUBLISHING
            io_budget = self._supervisor.begin_io()
            self._emit_event(
                "MODEL_PUBLISH_START",
                round_index=round_index,
                io_started_monotonic=io_budget["started_monotonic"],
                io_deadline_monotonic=io_budget["deadline_monotonic"],
                round_deadline_monotonic=round_deadline,
            )
            publish_error: Optional[BaseException] = None
            try:
                self.runtime.publish_model(current_model_path)
            except BaseException as exc:
                publish_error = exc
            finally:
                try:
                    self._supervisor.end_io()
                except FLRuntimeError as deadline_error:
                    publish_error = deadline_error
            if publish_error is not None:
                cancellation_error = self._supervisor.cancellation_error()
                if cancellation_error is not None:
                    raise cancellation_error
                if isinstance(publish_error, FLRuntimeError):
                    raise publish_error
                raise FLRuntimeError("runtime_failed", "模型发布失败") from publish_error

            io_completed = time.monotonic()
            self._emit_event(
                "MODEL_PUBLISHED",
                round_index=round_index,
                model_sha256=input_sha256,
                io_completed_monotonic=io_completed,
                round_deadline_monotonic=round_deadline,
            )

            self._check_stop()
            with self._lock:
                self.state = CoordinatorState.WAITING_UPDATES
            self._emit_event(
                "WAIT_FOR_UPDATES_START",
                round_index=round_index,
                round_deadline_monotonic=round_deadline,
            )
            try:
                updates_by_node = self.runtime.wait_for_updates()
            except TypeError as exc:
                raise FLRuntimeError(
                    "runtime_contract_error", "wait_for_updates 必须使用无参数接口"
                ) from exc
            self._check_stop()
            submitted_nodes = sorted(updates_by_node.keys())
            if set(submitted_nodes) != set(target_nodes):
                raise FLRuntimeError(
                    "sync_quorum_failed",
                    f"同步模式必须收齐全员: 期望 {list(target_nodes)}, 实际收齐 {submitted_nodes}",
                )
            # 固定本轮实际交付的全部制品引用，审计无需依赖 Runtime 轮次身份或目录排序。
            update_artifacts = {}
            for node, path in updates_by_node.items():
                stream, source_stat = preflight_artifact(path)
                stream.close()
                update_artifacts[node] = {
                    'path': os.path.abspath(path),
                    'size_bytes': source_stat.st_size,
                    'sha256': file_sha256(path),
                }
            self._emit_event(
                "UPDATES_COLLECTED",
                round_index=round_index,
                submitted_nodes=submitted_nodes,
                dropped_out_nodes=[],
                round_deadline_monotonic=round_deadline,
            )

            self._check_stop()
            with self._lock:
                self.state = CoordinatorState.AGGREGATING
            self._emit_event("AGGREGATE_START", round_index=round_index, submitted_nodes=submitted_nodes,
                             round_deadline_monotonic=round_deadline)
            output_model_path = os.path.join(
                self.artifact_dir, f"global-model-round-{round_index:04d}.bin"
            )
            self._call_aggregate(
                current_model_path,
                updates_by_node,
                output_model_path,
                round_index,
            )
            output_sha256 = file_sha256(output_model_path)
            self._last_model_path = output_model_path
            self._emit_event(
                "AGGREGATION_COMPLETED",
                round_index=round_index,
                output_model_sha256=output_sha256,
                round_deadline_monotonic=round_deadline,
            )

            self._check_stop()
            evaluation = self._call_evaluate(output_model_path, round_index)
            self._check_stop()
            metrics = deepcopy(evaluation["metrics"])
            verdict = evaluation["verdict"]
            reason = evaluation["reason"]
            self._emit_event(
                "EVALUATION_COMPLETED",
                round_index=round_index,
                metrics=metrics,
                verdict=verdict,
                reason=reason,
                round_deadline_monotonic=round_deadline,
            )
            if round_index == self.job.rounds:
                self.effect_result = {
                    "round_index": round_index,
                    "metrics": deepcopy(metrics),
                    "verdict": verdict,
                    "reason": reason,
                }
            round_summary = {
                "round_index": round_index,
                "input_model_path": current_model_path,
                "input_model_sha256": input_sha256,
                "output_model_path": output_model_path,
                "output_model_sha256": output_sha256,
                "committed_nodes": submitted_nodes,
                "updates": update_artifacts,
                "dropped_out_nodes": [],
                "client_submission_returns_received": False,
                "metrics": metrics,
                "verdict": verdict,
                "reason": reason,
                "round_started_monotonic": round_start,
                "round_deadline_monotonic": round_deadline,
                "publish_io_started_monotonic": io_budget["started_monotonic"],
                "publish_io_deadline_monotonic": io_budget["deadline_monotonic"],
                "publish_io_completed_monotonic": io_completed,
                "duration_seconds": round(time.monotonic() - round_start, 3),
            }
            self.rounds_summary.append(round_summary)

            with self._lock:
                self.state = CoordinatorState.WAITING_ROUND_REPORT
            self._emit_event(
                "ROUND_REPORT_WAIT_START",
                round_index=round_index,
                round_deadline_monotonic=round_deadline,
            )
            self.round_completion(round_index)
            self._supervisor.end_round(round_index)
            round_summary['client_submission_returns_received'] = True
            self._emit_event("ROUND_REPORT_COMPLETED", round_index=round_index)

            self._emit_event("ROUND_COMPLETED", round_index=round_index, summary=round_summary)
            current_model_path = output_model_path
            if round_index == self.job.rounds and verdict is False:
                raise FLRuntimeError(
                    "effect_criterion_not_met",
                    reason or "末轮效果未达到算法判据",
                )

        return current_model_path
