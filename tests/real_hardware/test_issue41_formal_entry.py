#!/usr/bin/env python3
"""测试 Issue #41 唯一正式入口与端到端归档合成验证。

验证目标：
1. 完整正式运行（preflight -> gate -> config-equivalence -> runtime -> lifecycle -> summary）合成归档全量通过校验器。
2. 任意阶段失败（gate / runtime / lifecycle）复用同一 run 生成 failed 归档，保留已完成阶段证据，且通过归档校验。
3. 配置等价性（channel/bandwidth/MCS/FEC/TUN/queue/feedback）被严格校验，不等价时被拒绝。
4. 运行中修改配置（deadline、io_timeout等）被校验器拒绝。
5. 候选 feedback 行为及 diagnostic 模式被严格拒绝。
"""

import json
import os
import unittest

from tests.real_hardware.issue41_envelope import FailureCategory
from tests.real_hardware.issue41_validate_archive import validate_archive
from tests.real_hardware.test_issue41_validate_archive import Issue41ArchiveValidatorTestCase


class TestIssue41FormalEntry(Issue41ArchiveValidatorTestCase):

    def write_test_envelope(self, run_id='test-run', mode='formal', resolved_overrides=None):
        cfg = {
            'smoke_cycle_deadline_seconds': 240,
            'runtime_timeout_seconds': 180,
            'io_timeout_seconds': 120,
        }
        if resolved_overrides:
            cfg.update(resolved_overrides)
        self.write_json('envelope.json', {
            'schema_version': 1,
            'run_id': run_id,
            'mode': mode,
            'network_isolation': {'prohibit_management_as_data_plane': True},
            'resolved_config': cfg,
        })

    def write_dummy_files(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_lifecycle_evidence()

    def test_complete_passed_archive_validates_successfully(self):
        run_id = 'test-run'
        self.write_test_envelope(run_id=run_id, mode='formal')
        self.write_smoke_marker(run_id)
        self.write_dummy_files()
        summary = self.complete_summary('passed')
        summary['run_id'] = run_id
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# issue41 result\n- conclusion: passed\n- reason: all ok\n')

        errors = validate_archive(self.root)
        self.assertEqual([], errors, f"完整通过归档应通过校验: {errors}")

    def test_stage_failure_gate_preserves_orchestration_and_is_auditable(self):
        run_id = 'test-run'
        self.write_test_envelope(run_id=run_id, mode='formal')
        summary = self.complete_summary('passed')
        summary['run_id'] = run_id
        summary['pre_runtime_smoke'] = {
            'schema_version': 1,
            'run_id': run_id,
            'status': 'failed',
            'gate_type': 'three_cycle_bidirectional',
            'failure_category': FailureCategory.LINK_CAPABILITY,
            'failure_reason': 'client2 信号中断未完成第 2 周期',
            'last_successful_layer': 'orchestration',
            'first_failing_layer': 'pre_runtime_smoke',
        }
        summary['formal_runtime_loop'] = {'status': 'skipped', 'reason': 'Gate 失败，跳过'}
        summary['lifecycle'] = {'status': 'skipped', 'reason': 'Gate 失败，跳过'}
        summary['conclusion'] = {
            'status': 'failed',
            'category': FailureCategory.LINK_CAPABILITY,
            'reason': 'client2 信号中断未完成第 2 周期',
            'first_failing_layer': 'pre_runtime_smoke',
            'last_successful_layer': 'orchestration',
        }
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# issue41 result\n- conclusion: failed\n- failure_category: link_capability\n- reason: client2 信号中断\n')

        errors = validate_archive(self.root)
        self.assertEqual([], errors, f"Gate 失败归档应通过校验: {errors}")

    def test_stage_failure_runtime_preserves_gate_and_is_auditable(self):
        run_id = 'test-run'
        self.write_test_envelope(run_id=run_id, mode='formal')
        self.write_smoke_marker(run_id)
        summary = self.complete_summary('passed')
        summary['run_id'] = run_id
        summary['formal_runtime_loop']['status'] = 'failed'
        summary['formal_runtime_loop']['failure_reason'] = 'client2 训练延时超时未在 deadline 内完成'
        summary['lifecycle'] = {'status': 'skipped', 'reason': 'Runtime 失败，跳过'}
        summary['conclusion'] = {
            'status': 'failed',
            'category': FailureCategory.IMPLEMENTATION,
            'reason': 'client2 训练延时超时未在 deadline 内完成',
            'first_failing_layer': 'formal_runtime_loop',
            'last_successful_layer': 'pre_runtime_smoke',
        }
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# issue41 result\n- conclusion: failed\n- failure_category: implementation\n- reason: client2 训练超时\n')

        errors = validate_archive(self.root)
        self.assertEqual([], errors, f"Runtime 失败归档应通过校验: {errors}")

    def test_stage_failure_lifecycle_preserves_runtime_and_is_auditable(self):
        run_id = 'test-run'
        self.write_test_envelope(run_id=run_id, mode='formal')
        self.write_smoke_marker(run_id)
        self.write_dummy_files()
        summary = self.complete_summary('passed')
        summary['run_id'] = run_id
        summary['lifecycle'] = {
            'schema_version': 1,
            'run_id': run_id,
            'status': 'failed',
            'reason': '重启时发生 MainPID 重叠，未能保证 no-overlap',
            'failure_category': FailureCategory.IMPLEMENTATION,
        }
        summary['conclusion'] = {
            'status': 'failed',
            'category': FailureCategory.IMPLEMENTATION,
            'reason': '重启时发生 MainPID 重叠，未能保证 no-overlap',
            'first_failing_layer': 'lifecycle',
            'last_successful_layer': 'formal_runtime_loop',
        }
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# issue41 result\n- conclusion: failed\n- failure_category: implementation\n- reason: PID 重叠\n')

        errors = validate_archive(self.root)
        self.assertEqual([], errors, f"Lifecycle 失败归档应通过校验: {errors}")

    def test_rejects_config_modification_during_run(self):
        run_id = 'test-run'
        self.write_test_envelope(run_id=run_id, mode='formal')
        self.write_smoke_marker(run_id)
        self.write_dummy_files()
        summary = self.complete_summary('passed')
        summary['run_id'] = run_id
        # 篡改 Runtime 阶段中的 deadline
        summary['formal_runtime_loop']['scenario']['round_deadline_seconds'] = 999
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# issue41 result\n- conclusion: passed\n- reason: ok\n')

        errors = validate_archive(self.root)
        self.assertTrue(len(errors) > 0)
        self.assertTrue(any('运行中修改配置' in e for e in errors), f"未捕获运行中修改配置错误: {errors}")

    def test_rejects_diagnostic_mode_in_formal_acceptance(self):
        run_id = 'test-run'
        self.write_test_envelope(run_id=run_id, mode='diagnostic')
        self.write_smoke_marker(run_id)
        self.write_dummy_files()
        summary = self.complete_summary('passed')
        summary['run_id'] = run_id
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# issue41 result\n- conclusion: passed\n- reason: ok\n')

        errors = validate_archive(self.root)
        self.assertTrue(len(errors) > 0)
        self.assertTrue(any('非 formal 模式' in e for e in errors), f"未捕获 diagnostic 模式: {errors}")

    def test_rejects_candidate_feedback_in_summary(self):
        run_id = 'test-run'
        self.write_test_envelope(run_id=run_id, mode='formal')
        self.write_smoke_marker(run_id)
        self.write_dummy_files()
        summary = self.complete_summary('passed')
        summary['run_id'] = run_id
        summary['formal_runtime_loop']['feedback_window_start_immediately'] = True
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# issue41 result\n- conclusion: passed\n- reason: ok\n')

        errors = validate_archive(self.root)
        self.assertTrue(len(errors) > 0)
        self.assertTrue(any('feedback_window_start_immediately' in e for e in errors), f"未捕获候选 feedback: {errors}")

    def test_rejects_mixed_run_ids(self):
        run_id = 'test-run'
        self.write_test_envelope(run_id=run_id, mode='formal')
        self.write_smoke_marker(run_id)
        self.write_dummy_files()
        summary = self.complete_summary('passed')
        summary['run_id'] = run_id
        summary['pre_runtime_smoke']['run_id'] = 'test-run-other'
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# issue41 result\n- conclusion: passed\n- reason: ok\n')

        errors = validate_archive(self.root)
        self.assertTrue(len(errors) > 0)
        self.assertTrue(any('run_id' in e for e in errors), f"未捕获 run_id 不一致错误: {errors}")


if __name__ == '__main__':
    unittest.main()
