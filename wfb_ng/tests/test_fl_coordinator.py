"""显式算法函数与严格同步轮次的 Coordinator 测试。"""

from dataclasses import dataclass, field, replace
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch
from typing import Any, Dict, List, Mapping

from wfb_ng.fl.coordinator import CoordinatorState, FLCoordinator, JobConfig
from wfb_ng.fl.errors import FLRuntimeError
from wfb_ng.tests.mock_runtime import MockServerRuntime


@dataclass(frozen=True)
class FrozenContext:
    """模拟 frozen AlgorithmContext 的派生上下文约定。"""

    job_id: str
    node_id: int
    target_nodes: tuple
    rounds: int
    round_index: int
    phase: str
    artifact_dir: str
    local_config: Mapping[str, Any]
    initial_metrics: Mapping[str, Any]
    stop_event: threading.Event = field(compare=False, repr=False)

    def for_phase(self, phase, *, round_index, initial_metrics):
        return replace(
            self,
            phase=phase,
            round_index=round_index,
            initial_metrics=dict(initial_metrics),
        )

    def check_stop(self):
        if self.stop_event.is_set():
            raise FLRuntimeError("aborted", "算法被停止")


class TestFLCoordinator(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_coordinator_")
        self.model_path = os.path.join(self.test_dir, "initial_model.bin")
        with open(self.model_path, "wb") as stream:
            stream.write(b"initial-model")

        self.client_updates = {}
        for node_id in (1, 2):
            path = os.path.join(self.test_dir, f"update-{node_id}.bin")
            with open(path, "wb") as stream:
                stream.write(f"update-{node_id}".encode("ascii"))
            self.client_updates[node_id] = path

        self.runtime = MockServerRuntime(
            participant_node_ids=(1, 2),
            update_fixtures_by_node=self.client_updates,
        )
        self.events: List[Dict[str, Any]] = []
        self.callback_order: List[str] = []
        self.aggregate_calls: List[Dict[str, Any]] = []
        self.evaluate_calls: List[Dict[str, Any]] = []
        self.prepare_calls: List[int] = []
        self.completion_calls: List[int] = []
        self.stop_event = threading.Event()
        self.base_context = FrozenContext(
            job_id="job-test",
            node_id=255,
            target_nodes=(1, 2),
            rounds=1,
            round_index=0,
            phase="prepared",
            artifact_dir=os.path.join(self.test_dir, "artifacts"),
            local_config={"data_seed": 7},
            initial_metrics={"loss": 2.0, "accuracy": 0.1},
            stop_event=self.stop_event,
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _job(self, rounds=1, **overrides):
        data = dict(
            job_id="job-test",
            run_id="run-test",
            mode="sync",
            target_nodes=[1, 2],
            model_path=self.model_path,
            model_size_bytes=os.path.getsize(self.model_path),
            rounds=rounds,
            round_timeout_seconds=5.0,
            io_timeout_seconds=120,
            live_observation=False,
        )
        data.update(overrides)
        return JobConfig.from_dict(data)

    def _algorithm_functions(self, *, aggregate=None, evaluate=None):
        def aggregate_entry(current_model_path, updates_by_node, output_model_path, config, context):
            self.aggregate_calls.append(
                {
                    "current": current_model_path,
                    "updates": dict(updates_by_node),
                    "output": output_model_path,
                    "config": config,
                    "context": context,
                }
            )
            Path(output_model_path).write_bytes(
                f"global-{context.round_index}".encode("ascii")
            )
            return None

        def evaluate_entry(model_path, config, context):
            self.evaluate_calls.append(
                {"model": model_path, "config": config, "context": context}
            )
            final = context.round_index == context.rounds
            return {
                "metrics": {
                    "loss": 1.0 / context.round_index,
                    "accuracy": 0.1 * context.round_index,
                },
                "preparation": None,
                "verdict": True if final else None,
                "reason": "fixture improved" if final else None,
            }

        return aggregate or aggregate_entry, evaluate or evaluate_entry

    def _coordinator(
        self,
        *,
        rounds=1,
        aggregate=None,
        evaluate=None,
        initial_metrics=None,
        prepare_round=None,
        round_completion=None,
        round_timeout_seconds=None,
        io_timeout_seconds=None,
        cancel_callback=None,
        on_failed=None,
    ):
        job_overrides = {}
        if round_timeout_seconds is not None:
            job_overrides["round_timeout_seconds"] = round_timeout_seconds
        if io_timeout_seconds is not None:
            job_overrides["io_timeout_seconds"] = io_timeout_seconds
        job = self._job(rounds=rounds, **job_overrides)
        aggregate_fn, evaluate_fn = self._algorithm_functions(
            aggregate=aggregate, evaluate=evaluate
        )
        initial = initial_metrics or {"loss": 2.0, "accuracy": 0.1}

        def prepare(index):
            self.prepare_calls.append(index)
            self.callback_order.append(f"prepare:{index}")
            if prepare_round is not None:
                prepare_round(index)

        def complete(index):
            self.completion_calls.append(index)
            self.callback_order.append(f"complete:{index}")
            if round_completion is not None:
                round_completion(index)

        original_publish = self.runtime.publish_model

        def publish(model_path):
            self.callback_order.append(f"publish:{len(self.runtime.published_models) + 1}")
            return original_publish(model_path)

        self.runtime.publish_model = publish

        coordinator_kwargs = {}
        if cancel_callback is not None:
            coordinator_kwargs["cancel_callback"] = cancel_callback

        return FLCoordinator(
            job=job,
            runtime=self.runtime,
            work_dir=os.path.join(self.test_dir, "coordinator_work"),
            aggregate_fn=aggregate_fn,
            evaluate_fn=evaluate_fn,
            algorithm_config={"data_seed": 7},
            context=replace(
                self.base_context,
                rounds=rounds,
                initial_metrics=dict(initial),
            ),
            initial_metrics=initial,
            prepare_round=prepare,
            round_completion=complete,
            on_event=self.events.append,
            on_failed=on_failed,
            **coordinator_kwargs,
        )

    def test_job_config_defaults_to_five_strict_sync_rounds(self):
        job = JobConfig.from_dict(
            {
                "run_id": "run-defaults",
                "target_nodes": [1],
                "model_path": self.model_path,
                "model_size_bytes": os.path.getsize(self.model_path),
                "io_timeout_seconds": 120,
                "live_observation": False,
            }
        )
        self.assertEqual(job.rounds, 5)
        self.assertEqual(job.mode, "sync")
        self.assertFalse(hasattr(job, "min_updates"))
        self.assertFalse(hasattr(job, "max_staleness"))
        self.assertNotIn("min_updates", job.to_dict())
        self.assertNotIn("max_staleness", job.to_dict())
        with self.assertRaises(FLRuntimeError) as raised:
            JobConfig.from_dict(
                {
                    "run_id": "run-async",
                    "target_nodes": [1],
                    "model_path": self.model_path,
                    "model_size_bytes": os.path.getsize(self.model_path),
                    "mode": "async",
                    "io_timeout_seconds": 120,
                    "live_observation": False,
                }
            )
        self.assertEqual(raised.exception.error_code, "invalid_job_config")
        for legacy_field in ("min_updates", "max_staleness"):
            with self.subTest(legacy_field=legacy_field), self.assertRaises(FLRuntimeError) as legacy:
                JobConfig.from_dict(
                    {
                        "run_id": "run-legacy",
                        "target_nodes": [1],
                        "model_path": self.model_path,
                        "model_size_bytes": os.path.getsize(self.model_path),
                        "io_timeout_seconds": 120,
                        "live_observation": False,
                        legacy_field: 1,
                    }
                )
            self.assertEqual(legacy.exception.error_code, "invalid_job_config")

    def test_one_round_uses_explicit_functions_and_final_report_barrier(self):
        coordinator = self._coordinator(rounds=1)
        original_context = coordinator.context
        summary = coordinator.run()

        self.assertEqual(coordinator.state, CoordinatorState.SUCCEEDED)
        self.assertEqual(summary["status"], "succeeded")
        self.assertEqual(summary["initial_metrics"], {"loss": 2.0, "accuracy": 0.1})
        self.assertEqual(summary["rounds_completed"], 1)
        self.assertEqual(summary["rounds"][0]["metrics"]["accuracy"], 0.1)
        self.assertEqual(summary["effect_result"]["verdict"], True)
        self.assertEqual(self.prepare_calls, [1])
        self.assertEqual(self.completion_calls, [1])
        self.assertEqual(
            self.callback_order,
            ["prepare:1", "publish:1", "complete:1"],
        )
        self.assertEqual(len(self.aggregate_calls), 1)
        self.assertEqual(len(self.evaluate_calls), 1)
        self.assertEqual(self.aggregate_calls[0]["config"], {"data_seed": 7})
        self.assertEqual(self.evaluate_calls[0]["config"], {"data_seed": 7})
        self.assertEqual(self.aggregate_calls[0]["context"].phase, "aggregate")
        self.assertEqual(self.evaluate_calls[0]["context"].phase, "evaluate")
        self.assertEqual(self.aggregate_calls[0]["context"].node_id, 255)
        self.assertEqual(self.evaluate_calls[0]["context"].initial_metrics, summary["initial_metrics"])
        self.assertIs(coordinator.context, original_context)
        self.assertEqual(original_context.phase, "prepared")
        self.assertEqual(original_context.round_index, 0)

        event_types = [event["type"] for event in self.events]
        self.assertLess(
            event_types.index("EVALUATION_COMPLETED"),
            event_types.index("ROUND_REPORT_WAIT_START"),
        )
        self.assertLess(
            event_types.index("ROUND_REPORT_COMPLETED"),
            event_types.index("ROUND_COMPLETED"),
        )
        self.assertLess(
            event_types.index("ROUND_COMPLETED"),
            event_types.index("JOB_COMPLETED"),
        )

    def test_algorithm_config_is_recursive_read_only(self):
        coordinator = self._coordinator(rounds=1)
        self.assertEqual(dict(coordinator.algorithm_config), {"data_seed": 7})
        with self.assertRaises(TypeError):
            coordinator.algorithm_config["data_seed"] = 8

    def test_non_initial_evaluation_must_not_return_preparation(self):
        def evaluate_with_preparation(model_path, config, context):
            return {
                "metrics": {"loss": 1.0, "accuracy": 0.2},
                "preparation": {"unexpected": True},
                "verdict": True,
                "reason": "fixture improved",
            }

        coordinator = self._coordinator(rounds=1, evaluate=evaluate_with_preparation)
        with self.assertRaises(FLRuntimeError) as raised:
            coordinator.run()
        self.assertEqual(raised.exception.error_code, "invalid_evaluation_result")

    def test_non_initial_evaluation_must_not_return_verdict_or_reason(self):
        def evaluate_with_intermediate_effect(model_path, config, context):
            return {
                "metrics": {"loss": 1.0, "accuracy": 0.2},
                "preparation": None,
                "verdict": True,
                "reason": "premature effect",
            }

        coordinator = self._coordinator(rounds=2, evaluate=evaluate_with_intermediate_effect)
        with self.assertRaises(FLRuntimeError) as raised:
            coordinator.run()
        self.assertEqual(raised.exception.error_code, "invalid_evaluation_result")

    def test_final_evaluation_requires_non_empty_bounded_reason(self):
        for reason in (None, "", "   ", "字" * 513):
            with self.subTest(reason=reason):
                def evaluate_invalid_final(model_path, config, context, reason=reason):
                    return {
                        "metrics": {"loss": 1.0, "accuracy": 0.2},
                        "preparation": None,
                        "verdict": True,
                        "reason": reason,
                    }

                coordinator = self._coordinator(rounds=1, evaluate=evaluate_invalid_final)
                with self.assertRaises(FLRuntimeError) as raised:
                    coordinator.run()
                self.assertEqual(raised.exception.error_code, "invalid_evaluation_result")

    def test_false_final_effect_fails_but_preserves_round_and_model(self):
        def evaluate_not_met(model_path, config, context):
            return {
                "metrics": {"loss": 3.0, "accuracy": 0.01},
                "preparation": None,
                "verdict": False,
                "reason": "fixture criterion not met",
            }

        coordinator = self._coordinator(rounds=1, evaluate=evaluate_not_met)
        with self.assertRaises(FLRuntimeError) as raised:
            coordinator.run()
        self.assertEqual(raised.exception.error_code, "effect_criterion_not_met")
        summary = coordinator.get_summary()
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["execution_result"], "succeeded")
        self.assertTrue(summary['rounds'][0]['client_submission_returns_received'])
        self.assertEqual(summary["effect_result"]["verdict"], False)
        self.assertEqual(summary["effect_result"]["reason"], "fixture criterion not met")
        self.assertEqual(summary["rounds_completed"], 1)
        self.assertTrue(os.path.isfile(summary["final_model_path"]))
        failed_event = next(event for event in coordinator.events if event["type"] == "JOB_FAILED")
        self.assertEqual(failed_event["summary"], summary)

    def test_prepare_round_precedes_each_publish_and_metrics_chain_across_rounds(self):
        coordinator = self._coordinator(rounds=2)
        summary = coordinator.run()

        self.assertEqual(summary["rounds_completed"], 2)
        self.assertEqual(self.prepare_calls, [1, 2])
        # 每轮都有 submit 返回屏障，完成后才允许下一轮发布。
        self.assertEqual(self.completion_calls, [1, 2])
        self.assertEqual(
            self.callback_order,
            ["prepare:1", "publish:1", "complete:1", "prepare:2", "publish:2", "complete:2"],
        )
        self.assertEqual(len(self.runtime.published_models), 2)
        self.assertEqual(
            self.runtime.published_models[1],
            summary["rounds"][0]["output_model_path"],
        )
        self.assertEqual(
            summary["rounds"][1]["input_model_sha256"],
            summary["rounds"][0]["output_model_sha256"],
        )
        self.assertEqual(
            [call["context"].round_index for call in self.aggregate_calls], [1, 2]
        )
        self.assertEqual(
            [call["context"].round_index for call in self.evaluate_calls], [1, 2]
        )

    def test_initial_metrics_are_not_evaluated_again_by_coordinator(self):
        coordinator = self._coordinator(
            rounds=1,
            initial_metrics={"loss": 9.0, "accuracy": 0.01},
        )
        summary = coordinator.run()
        self.assertEqual(len(self.evaluate_calls), 1)
        self.assertEqual(summary["initial_metrics"], {"loss": 9.0, "accuracy": 0.01})
        self.assertNotEqual(summary["initial_metrics"], summary["rounds"][0]["metrics"])

    def test_old_two_argument_aggregate_is_rejected(self):
        def old_aggregate(model_path, updates_by_node):
            return None

        coordinator = self._coordinator(rounds=1, aggregate=old_aggregate)
        with self.assertRaises(FLRuntimeError) as raised:
            coordinator.run()
        self.assertEqual(raised.exception.error_code, "algorithm_contract_error")
        self.assertEqual(coordinator.state, CoordinatorState.FAILED)

    def test_evaluate_requires_three_argument_contract(self):
        def old_evaluate(model_path, config, context, extra, another):
            return {"loss": 1.0}

        coordinator = self._coordinator(rounds=1, evaluate=old_evaluate)
        with self.assertRaises(FLRuntimeError) as raised:
            coordinator.run()
        self.assertEqual(raised.exception.error_code, "algorithm_contract_error")
        self.assertEqual(coordinator.state, CoordinatorState.FAILED)

    def test_missing_aggregate_output_fails_without_loading_model_in_coordinator(self):
        def no_output(model_path, updates_by_node, output_model_path, config, context):
            return None

        coordinator = self._coordinator(rounds=1, aggregate=no_output)
        with self.assertRaises(FLRuntimeError) as raised:
            coordinator.run()
        self.assertEqual(raised.exception.error_code, "aggregate_output_missing")
        self.assertEqual(coordinator.state, CoordinatorState.FAILED)

    def test_final_round_completion_failure_preserves_computed_round_and_fails_job(self):
        def fail_completion(index):
            raise FLRuntimeError("round_report_timeout", "末轮报告未收齐")

        coordinator = self._coordinator(rounds=1, round_completion=fail_completion)
        with self.assertRaises(FLRuntimeError) as raised:
            coordinator.run()
        self.assertEqual(raised.exception.error_code, "round_report_timeout")
        self.assertEqual(coordinator.state, CoordinatorState.FAILED)
        summary = coordinator.get_summary()
        self.assertIsNotNone(summary)
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary['execution_result'], 'failed')
        self.assertFalse(summary['rounds'][0]['client_submission_returns_received'])
        self.assertEqual(summary["rounds_completed"], 1)

    def test_strict_quorum_rejects_partial_updates(self):
        class PartialRuntime(MockServerRuntime):
            def wait_for_updates(self):
                return {1: self.update_fixtures_by_node[1]}

        self.runtime = PartialRuntime(
            participant_node_ids=(1, 2),
            update_fixtures_by_node=self.client_updates,
        )
        coordinator = self._coordinator(rounds=1)
        with self.assertRaises(FLRuntimeError) as raised:
            coordinator.run()
        self.assertEqual(raised.exception.error_code, "sync_quorum_failed")
        self.assertEqual(coordinator.state, CoordinatorState.FAILED)

    def test_publish_return_after_io_deadline_keeps_io_first_cause(self):
        class Clock:
            def __init__(self):
                self.now = 100.0

            def __call__(self):
                return self.now

        class LatePublishRuntime(MockServerRuntime):
            def publish_model(self, model_path):
                self.published_models.append(model_path)
                self.active_round_model = model_path
                self.round_id = f"round_{len(self.published_models)}"
                clock.now = 103.001

        clock = Clock()
        self.runtime = LatePublishRuntime(
            participant_node_ids=(1, 2),
            update_fixtures_by_node=self.client_updates,
        )
        reasons = []
        coordinator = self._coordinator(
            round_timeout_seconds=10.0,
            io_timeout_seconds=3.0,
            cancel_callback=reasons.append,
        )
        with patch("wfb_ng.fl.coordinator.time.monotonic", new=clock):
            with self.assertRaises(FLRuntimeError) as raised:
                coordinator.run()
        self.assertEqual(raised.exception.error_code, "io_timeout")
        self.assertEqual(coordinator.get_summary()["error_code"], "io_timeout")
        self.assertEqual(reasons, ["io_timeout"])
        self.assertEqual(self.runtime.abort_reason, "io_timeout")
        self.assertIsNone(coordinator.current_round_deadline)

    def test_expired_round_wins_late_operator_abort(self):
        class Clock:
            def __init__(self):
                self.now = 100.0

            def __call__(self):
                return self.now

        clock = Clock()
        entered = threading.Event()
        release = threading.Event()
        reasons = []

        def blocked_prepare(index):
            entered.set()
            release.wait()

        self.runtime = MockServerRuntime(
            participant_node_ids=(1, 2),
            update_fixtures_by_node=self.client_updates,
        )
        coordinator = self._coordinator(
            round_timeout_seconds=10.0,
            io_timeout_seconds=20.0,
            prepare_round=blocked_prepare,
            cancel_callback=reasons.append,
        )
        with patch("wfb_ng.fl.coordinator.time.monotonic", new=clock):
            coordinator.start()
            self.assertTrue(entered.wait(timeout=2.0))
            clock.now = 110.001
            coordinator.abort(reason="late_operator_stop")
            self.assertEqual(reasons, ["round_timeout"])
            self.assertEqual(self.runtime.abort_reason, "round_timeout")
            self.assertIsNone(coordinator.get_summary())
            release.set()
            self.assertEqual(coordinator.wait(timeout=2.0), 1)
        self.assertEqual(coordinator.state, CoordinatorState.FAILED)
        self.assertEqual(coordinator.get_summary()["error_code"], "round_timeout")

    def test_cancel_callback_precedes_blocking_runtime_abort_without_terminal(self):
        compute_entered = threading.Event()
        compute_release = threading.Event()
        runtime_abort_entered = threading.Event()
        runtime_abort_release = threading.Event()
        callback_seen = threading.Event()
        sequence = []

        class BlockingAbortRuntime(MockServerRuntime):
            def abort(self, reason="aborted"):
                sequence.append("runtime_abort")
                runtime_abort_entered.set()
                runtime_abort_release.wait()
                super().abort(reason=reason)

        self.runtime = BlockingAbortRuntime(
            participant_node_ids=(1, 2),
            update_fixtures_by_node=self.client_updates,
        )

        def blocking_aggregate(model_path, updates_by_node, output_model_path, config, context):
            compute_entered.set()
            compute_release.wait()
            context.check_stop()

        def cancel_callback(reason):
            sequence.append("cancel_callback")
            callback_seen.set()

        coordinator = self._coordinator(
            aggregate=blocking_aggregate,
            round_timeout_seconds=0.05,
            cancel_callback=cancel_callback,
        )
        coordinator.start()
        self.assertTrue(compute_entered.wait(timeout=2.0))
        self.assertTrue(callback_seen.wait(timeout=2.0))
        self.assertTrue(runtime_abort_entered.wait(timeout=2.0))
        self.assertEqual(sequence[:2], ["cancel_callback", "runtime_abort"])
        self.assertIsNone(coordinator.get_summary())
        with self.assertRaises(FLRuntimeError) as raised:
            coordinator.wait(timeout=0.01)
        self.assertEqual(raised.exception.error_code, "coordinator_wait_timeout")

        compute_release.set()
        runtime_abort_release.set()
        self.assertEqual(coordinator.wait(timeout=2.0), 1)
        self.assertEqual(coordinator.state, CoordinatorState.FAILED)
        self.assertEqual(coordinator.get_summary()["error_code"], "round_timeout")

    def test_round_timeout_starts_before_prepare_and_publish(self):
        entered = threading.Event()
        cancel_reasons = []

        def blocked_prepare(index):
            entered.set()
            self.stop_event.wait()

        coordinator = self._coordinator(
            round_timeout_seconds=0.05,
            prepare_round=blocked_prepare,
            cancel_callback=cancel_reasons.append,
        )
        coordinator.start()
        self.assertTrue(entered.wait(timeout=2.0))
        self.assertEqual(coordinator.wait(timeout=2.0), 1)

        self.assertEqual(coordinator.state, CoordinatorState.FAILED)
        self.assertEqual(coordinator.get_summary()["error_code"], "round_timeout")
        self.assertEqual(cancel_reasons, ["round_timeout"])
        self.assertTrue(self.runtime.aborted)
        self.assertEqual(self.runtime.abort_reason, "round_timeout")
        self.assertEqual(self.runtime.published_models, [])
        self.assertNotIn("JOB_ABORTED", [event["type"] for event in self.events])

    def test_round_timeout_wakes_wait_for_updates_without_io_budget(self):
        entered = threading.Event()

        class NoTimeoutRuntime(MockServerRuntime):
            def wait_for_updates(self):
                entered.set()
                self.abort_event.wait()
                raise FLRuntimeError("aborted", "Runtime 已被中止")

        self.runtime = NoTimeoutRuntime(
            participant_node_ids=(1, 2),
            update_fixtures_by_node=self.client_updates,
        )
        coordinator = self._coordinator(
            round_timeout_seconds=0.05,
            io_timeout_seconds=0.01,
        )
        coordinator.start()
        self.assertTrue(entered.wait(timeout=2.0))
        self.assertEqual(coordinator.wait(timeout=2.0), 1)

        self.assertEqual(coordinator.state, CoordinatorState.FAILED)
        self.assertEqual(coordinator.get_summary()["error_code"], "round_timeout")
        self.assertEqual(self.runtime.abort_reason, "round_timeout")

    def test_publish_io_deadline_uses_minimum_io_and_round_budget(self):
        entered = threading.Event()

        class SlowPublishRuntime(MockServerRuntime):
            def publish_model(self, model_path):
                entered.set()
                self.abort_event.wait()
                raise FLRuntimeError("aborted", "Runtime 已被中止")

        self.runtime = SlowPublishRuntime(
            participant_node_ids=(1, 2),
            update_fixtures_by_node=self.client_updates,
        )
        coordinator = self._coordinator(
            round_timeout_seconds=0.5,
            io_timeout_seconds=0.05,
        )
        coordinator.start()
        self.assertTrue(entered.wait(timeout=2.0))
        self.assertEqual(coordinator.wait(timeout=2.0), 1)
        self.assertEqual(coordinator.state, CoordinatorState.FAILED)
        self.assertEqual(coordinator.get_summary()["error_code"], "io_timeout")
        self.assertEqual(self.runtime.abort_reason, "io_timeout")

    def test_round_timeout_covers_aggregate_and_waits_for_cooperative_exit(self):
        entered = threading.Event()
        release = threading.Event()
        failed = threading.Event()

        def blocking_aggregate(model_path, updates_by_node, output_model_path, config, context):
            entered.set()
            release.wait()
            context.check_stop()

        coordinator = self._coordinator(
            aggregate=blocking_aggregate,
            round_timeout_seconds=0.05,
            on_failed=lambda exc: failed.set(),
        )
        coordinator.start()
        self.assertTrue(entered.wait(timeout=2.0))
        self.assertTrue(self.runtime.abort_event.wait(timeout=2.0))
        self.assertIsNone(coordinator.get_summary())
        self.assertFalse(failed.is_set())
        with self.assertRaises(FLRuntimeError) as raised:
            coordinator.wait(timeout=0.01)
        self.assertEqual(raised.exception.error_code, "coordinator_wait_timeout")

        release.set()
        self.assertEqual(coordinator.wait(timeout=2.0), 1)
        self.assertTrue(failed.is_set())
        self.assertEqual(coordinator.state, CoordinatorState.FAILED)
        self.assertEqual(coordinator.get_summary()["error_code"], "round_timeout")

    def test_round_timeout_covers_evaluate_and_report_barrier(self):
        for phase in ("evaluate", "report"):
            with self.subTest(phase=phase):
                self.runtime = MockServerRuntime(
                    participant_node_ids=(1, 2),
                    update_fixtures_by_node=self.client_updates,
                )
                self.stop_event.clear()
                entered = threading.Event()

                def blocking_evaluate(model_path, config, context):
                    entered.set()
                    self.stop_event.wait()
                    context.check_stop()
                    return {
                        "metrics": {"loss": 1.0, "accuracy": 0.2},
                        "preparation": None,
                        "verdict": True,
                        "reason": "fixture improved",
                    }

                def blocking_report(index):
                    entered.set()
                    self.stop_event.wait()
                    raise FLRuntimeError("round_report_timeout", "报告未收齐")

                coordinator = self._coordinator(
                    evaluate=blocking_evaluate if phase == "evaluate" else None,
                    round_completion=blocking_report if phase == "report" else None,
                    round_timeout_seconds=0.5,
                )
                coordinator.start()
                self.assertTrue(entered.wait(timeout=2.0))
                self.assertEqual(coordinator.wait(timeout=2.0), 1)
                self.assertEqual(coordinator.state, CoordinatorState.FAILED)
                self.assertEqual(coordinator.get_summary()["error_code"], "round_timeout")

    def test_operator_abort_waits_for_compute_exit_before_summary(self):
        entered = threading.Event()
        release = threading.Event()
        cancel_reasons = []

        def blocking_aggregate(model_path, updates_by_node, output_model_path, config, context):
            entered.set()
            release.wait()
            context.check_stop()

        coordinator = self._coordinator(
            aggregate=blocking_aggregate,
            cancel_callback=cancel_reasons.append,
        )
        coordinator.start()
        self.assertTrue(entered.wait(timeout=2.0))
        coordinator.abort(reason="operator_interrupted")
        self.assertIsNone(coordinator.get_summary())
        self.assertEqual(cancel_reasons, ["operator_interrupted"])
        with self.assertRaises(FLRuntimeError) as raised:
            coordinator.wait(timeout=0.01)
        self.assertEqual(raised.exception.error_code, "coordinator_wait_timeout")

        release.set()
        self.assertEqual(coordinator.wait(timeout=2.0), 1)
        self.assertEqual(coordinator.state, CoordinatorState.ABORTED)
        self.assertEqual(coordinator.get_summary()["status"], "aborted")
        self.assertEqual(coordinator.get_summary()["error_code"], "aborted")

    def test_first_cancellation_cause_is_not_overwritten(self):
        entered = threading.Event()
        timeout_observed = threading.Event()
        operator_finished = threading.Event()
        reasons = []

        def blocking_aggregate(model_path, updates_by_node, output_model_path, config, context):
            entered.set()
            self.stop_event.wait()
            context.check_stop()

        def cancel(reason):
            reasons.append(reason)
            if reason == "round_timeout":
                timeout_observed.set()

        coordinator = self._coordinator(
            aggregate=blocking_aggregate,
            round_timeout_seconds=0.05,
            cancel_callback=cancel,
        )
        coordinator.start()
        self.assertTrue(entered.wait(timeout=2.0))

        def request_operator_abort():
            self.assertTrue(timeout_observed.wait(timeout=2.0))
            coordinator.abort(reason="operator_interrupted")
            operator_finished.set()

        operator = threading.Thread(target=request_operator_abort)
        operator.start()
        self.assertEqual(coordinator.wait(timeout=2.0), 1)
        operator.join(timeout=2.0)
        self.assertTrue(operator_finished.is_set())
        self.assertEqual(reasons, ["round_timeout"])
        self.assertEqual(coordinator.state, CoordinatorState.FAILED)
        self.assertEqual(coordinator.get_summary()["error_code"], "round_timeout")

    def test_round_timeout_does_not_start_next_round(self):
        entered = threading.Event()

        def blocking_report(index):
            entered.set()
            self.stop_event.wait()
            raise FLRuntimeError("round_report_timeout", "报告未收齐")

        coordinator = self._coordinator(
            rounds=2,
            round_completion=blocking_report,
            round_timeout_seconds=0.05,
        )
        coordinator.start()
        self.assertTrue(entered.wait(timeout=2.0))
        self.assertEqual(coordinator.wait(timeout=2.0), 1)
        self.assertEqual(len(self.runtime.published_models), 1)
        self.assertEqual(coordinator.state, CoordinatorState.FAILED)
        self.assertEqual(coordinator.get_summary()["error_code"], "round_timeout")

    def test_nonfinite_round_or_io_timeout_is_rejected(self):
        for field in ("round_timeout_seconds", "io_timeout_seconds"):
            for value in (float("nan"), float("inf"), float("-inf")):
                with self.subTest(field=field, value=value), self.assertRaises(FLRuntimeError) as raised:
                    self._job(**{field: value})
                self.assertEqual(raised.exception.error_code, "invalid_" + field.removesuffix("_seconds"))

    def test_fractional_io_timeout_is_accepted(self):
        job = self._job(io_timeout_seconds=0.25)
        self.assertEqual(job.io_timeout_seconds, 0.25)

    def test_abort_terminates_coordinator_and_runtime(self):
        waiting = threading.Event()

        class SlowRuntime(MockServerRuntime):
            def wait_for_updates(self):
                waiting.set()
                self.abort_event.wait(timeout=5.0)
                if self.aborted:
                    raise FLRuntimeError("aborted", "Runtime 已被中止")
                return super().wait_for_updates()

        self.runtime = SlowRuntime(
            participant_node_ids=(1, 2),
            update_fixtures_by_node=self.client_updates,
        )
        coordinator = self._coordinator(rounds=1)
        coordinator.start()
        self.assertTrue(waiting.wait(timeout=2.0))
        coordinator.abort(reason="operator_interrupted")
        self.assertEqual(coordinator.wait(timeout=2.0), 1)
        self.assertEqual(coordinator.state, CoordinatorState.ABORTED)
        self.assertTrue(self.runtime.aborted)
        self.assertEqual(self.runtime.abort_reason, "operator_interrupted")
        self.assertEqual(coordinator.get_summary()["status"], "aborted")

    def test_abort_sets_shared_context_event_and_exits_cooperative_compute(self):
        entered = threading.Event()
        exited = threading.Event()

        def blocking_aggregate(model_path, updates_by_node, output_model_path, config, context):
            entered.set()
            while True:
                try:
                    context.check_stop()
                except FLRuntimeError:
                    exited.set()
                    raise
                threading.Event().wait(0.001)

        coordinator = self._coordinator(rounds=1, aggregate=blocking_aggregate)
        coordinator.start()
        self.assertTrue(entered.wait(timeout=2.0))
        self.assertIs(coordinator._stop_event, coordinator.context.stop_event)
        coordinator.abort(reason="operator_interrupted")
        self.assertTrue(coordinator.context.stop_event.is_set())
        self.assertEqual(coordinator.wait(), 1)
        self.assertTrue(exited.is_set())
        self.assertEqual(coordinator.state, CoordinatorState.ABORTED)
        self.assertEqual(coordinator.get_summary()["status"], "aborted")

    def test_wait_timeout_never_returns_success_while_compute_is_alive(self):
        entered = threading.Event()

        def blocking_aggregate(model_path, updates_by_node, output_model_path, config, context):
            entered.set()
            while True:
                context.check_stop()
                threading.Event().wait(0.001)

        coordinator = self._coordinator(rounds=1, aggregate=blocking_aggregate)
        coordinator.start()
        self.assertTrue(entered.wait(timeout=2.0))
        with self.assertRaises(FLRuntimeError) as raised:
            coordinator.wait(timeout=0.001)
        self.assertEqual(raised.exception.error_code, "coordinator_wait_timeout")
        coordinator.abort(reason="operator_interrupted")
        self.assertEqual(coordinator.wait(), 1)

    def test_wait_from_terminal_callback_does_not_self_join(self):
        holder = {}
        observed = {}

        def completed(summary):
            coordinator = holder["coordinator"]
            observed["same_thread"] = threading.current_thread() is coordinator._thread
            observed["exit_code"] = coordinator.wait()

        coordinator = self._coordinator(rounds=1)
        holder["coordinator"] = coordinator
        coordinator.on_completed = completed
        self.assertEqual(coordinator.start().wait(timeout=2.0), 0)
        self.assertTrue(observed["same_thread"])
        self.assertEqual(observed["exit_code"], 0)

    def test_summary_and_manifest_persist_initial_and_round_metrics(self):
        coordinator = self._coordinator(rounds=1)
        summary = coordinator.run()
        summary_path = Path(coordinator.work_dir) / "coordinator_summary.json"
        manifest_path = Path(coordinator.work_dir) / "coordinator_summary.manifest.json"
        self.assertTrue(summary_path.is_file())
        self.assertTrue(manifest_path.is_file())
        disk_summary = json.loads(summary_path.read_text(encoding="utf-8"))
        self.assertEqual(disk_summary["initial_metrics"], summary["initial_metrics"])
        self.assertNotIn("min_updates", disk_summary)
        self.assertNotIn("max_staleness", disk_summary)
        self.assertEqual(disk_summary["rounds"][0]["metrics"], summary["rounds"][0]["metrics"])
        self.assertEqual(json.loads(manifest_path.read_text(encoding="utf-8"))["job_id"], "job-test")


if __name__ == "__main__":
    unittest.main()
