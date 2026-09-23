#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import shutil
import tempfile
import unittest

from tests.real_hardware.issue41_validate_archive import validate_archive


class Issue41ArchiveValidatorTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='wfb-issue41-archive-')
        self.addCleanup(shutil.rmtree, self.root, True)
        self.write_json('envelope.json', {
            'schema_version': 1,
            'run_id': 'run-test-run',
            'mode': 'formal',
            'network_isolation': {'prohibit_management_as_data_plane': True},
            'resolved_config': {
                'radio_mcs_index': 3,
                'radio_bandwidth': 40,
                'channel_width': 'HT40+',
                'radio_txpower_dbm': 12,
                'uftp_rate_kbps': 15000,
                'grant_duration_ms': 120,
                'guard_interval_ms': 20,
                'rounds': 2,
                'artifact_size_bytes': 40 * 1024 * 1024,
                'training_delay_ms_by_node': {'1': 0, '2': 0},
                'smoke_cycle_count': 3,
                'smoke_io_timeout_seconds': 120,
                'smoke_cycle_deadline_seconds': 240,
                'runtime_timeout_seconds': 400,
                'io_timeout_seconds': 120,
            },
        })

    def test_accepts_complete_passed_summary(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_smoke_marker()
        self.write_lifecycle_evidence()
        self.write_json('issue41_summary.json', self.complete_summary('passed'))
        self.write_text('result.md', '# result\n')

        self.assertEqual([], validate_archive(self.root))

    def test_rejects_uftp_rate_mismatch_in_passed_archive(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_smoke_marker()
        self.write_lifecycle_evidence()
        summary = self.complete_summary('passed')
        summary['formal_runtime_loop']['config_equivalence']['runtime_configs']['server']['uftp_rate_kbps'] = 6000
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')
        self.assertTrue(any('UFTP 速率' in e for e in validate_archive(self.root)))

    def test_rejects_uftp_rate_outside_radio_envelope(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_smoke_marker()
        self.write_lifecycle_evidence()
        summary = self.complete_summary('passed')
        summary['formal_runtime_loop']['config_equivalence']['gate_configs']['server']['uftp_rate_kbps'] = 6000
        summary['formal_runtime_loop']['config_equivalence']['runtime_configs']['server']['uftp_rate_kbps'] = 6000
        for cycle in summary['pre_runtime_smoke']['cycles']:
            cycle['downlink']['uftp_rate_kbps'] = 6000
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')
        with open(os.path.join(self.root, 'envelope.json'), encoding='utf-8') as fh:
            envelope = json.load(fh)
        envelope['resolved_config']['uftp_rate_kbps'] = 6000
        self.write_json('envelope.json', envelope)
        self.assertTrue(any('安全区间' in e for e in validate_archive(self.root)))

    def test_rejects_missing_runtime_evidence_when_passed(self):
        summary = self.complete_summary('passed')
        summary['formal_runtime_loop'].pop('client2_result')
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)

        self.assertTrue(any('client2_result' in error for error in errors))

    def test_failed_conclusion_requires_reason(self):
        summary = self.complete_summary('failed')
        summary['formal_runtime_loop']['status'] = 'failed'
        summary['conclusion'].pop('reason')
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)

        self.assertTrue(any('reason' in error for error in errors))

    def test_rejects_passed_smoke_without_marker(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_json('issue41_summary.json', self.complete_summary('passed'))
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)

        self.assertTrue(any('smoke gate marker' in error for error in errors))

    def test_rejects_smoke_gate_with_less_than_three_cycles(self):
        summary = self.complete_summary('passed')
        summary['pre_runtime_smoke']['cycles'] = summary['pre_runtime_smoke']['cycles'][:2]
        self.write_runtime_evidence()
        self.write_smoke_marker()
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)
        self.assertTrue(any('3 个周期' in error for error in errors))

    def test_rejects_smoke_gate_with_failed_cycle(self):
        summary = self.complete_summary('passed')
        summary['pre_runtime_smoke']['cycles'][1]['status'] = 'failed'
        self.write_runtime_evidence()
        self.write_smoke_marker()
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)
        self.assertTrue(any('周期 2 状态为 failed' in error for error in errors))

    def test_rejects_smoke_gate_with_unrecovered_pause(self):
        summary = self.complete_summary('passed')
        cycle = summary['pre_runtime_smoke']['cycles'][0]
        cycle['telemetry']['queue']['tun_read_pause_total'] = 2
        cycle['telemetry']['queue']['tun_read_resume_total'] = 1
        cycle['telemetry']['queue']['currently_paused'] = True
        cycle['telemetry']['queue']['pause_recovered'] = False
        self.write_runtime_evidence()
        self.write_smoke_marker()
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)
        self.assertTrue(any('queue pause' in error and '恢复' in error for error in errors))

    def test_rejects_smoke_gate_with_missing_telemetry(self):
        summary = self.complete_summary('passed')
        del summary['pre_runtime_smoke']['cycles'][0]['telemetry']['sender_isolation']
        self.write_runtime_evidence()
        self.write_smoke_marker()
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)
        self.assertTrue(any('缺少遥测事实：sender_isolation' in error for error in errors))

    def test_rejects_passed_summary_without_complete_4mib_round(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_smoke_marker()
        summary = self.complete_summary('passed')
        summary['formal_runtime_loop']['rounds'] = []
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)

        self.assertTrue(any('轮完整证据' in error for error in errors))

    def test_rejects_update_not_matching_its_reused_template(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_smoke_marker()
        summary = self.complete_summary('passed')
        summary['formal_runtime_loop']['rounds'][0]['uploads'][0]['sha256'] = 'd'.ljust(64, '0')
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)

        self.assertTrue(any('未复用对应 update 模板' in error for error in errors))

    def test_rejects_upload_in_progress_or_mismatched_submission_facts(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_smoke_marker()
        summary = self.complete_summary('passed')
        summary['formal_runtime_loop']['rounds'][0]['uploads'][0]['server_http_status'] = 409
        summary['formal_runtime_loop']['rounds'][0]['uploads'][0]['server_outcome'] = 'upload_in_progress'
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)

        self.assertTrue(any('upload_in_progress' in error for error in errors))

    def test_envelope_validation_rejects_mismatched_run_id(self):
        summary = self.complete_summary('passed')
        summary['run_id'] = 'run-123'
        self.write_runtime_evidence()
        self.write_smoke_marker()
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')
        self.write_json('envelope.json', {
            'schema_version': 1,
            'run_id': 'run-456',
            'network_isolation': {'prohibit_management_as_data_plane': True},
        })

        errors = validate_archive(self.root)
        self.assertTrue(any('run_id 不一致' in error for error in errors))

    def test_envelope_validation_rejects_missing_network_isolation(self):
        summary = self.complete_summary('passed')
        summary['run_id'] = 'run-123'
        self.write_runtime_evidence()
        self.write_smoke_marker()
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')
        self.write_json('envelope.json', {
            'schema_version': 1,
            'run_id': 'run-123',
            'network_isolation': {'prohibit_management_as_data_plane': False},
        })

        errors = validate_archive(self.root)
        self.assertTrue(any('明确限制管理网' in error for error in errors))

    def test_rejects_partial_result_returned(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_smoke_marker()
        summary = self.complete_summary('passed')
        summary['formal_runtime_loop']['partial_result_returned'] = True
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)
        self.assertTrue(any('partial result' in error for error in errors))

    def test_rejects_incomplete_server_wait_returned_node_ids(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_smoke_marker()
        summary = self.complete_summary('passed')
        summary['formal_runtime_loop']['server_wait_for_updates_returned_node_ids'] = [1]
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)
        self.assertTrue(any('server_wait_for_updates_returned_node_ids' in error for error in errors))

    def test_rejects_incorrect_training_delay(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_smoke_marker()
        summary = self.complete_summary('passed')
        summary['formal_runtime_loop']['scenario']['training_delay_ms_by_node'] = {'1': 0, '2': 3000}
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)
        self.assertTrue(any('training_delay_ms_by_node' in error for error in errors))

    def test_rejects_wrong_artifact_size_40mib(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_smoke_marker()
        summary = self.complete_summary('passed')
        summary['formal_runtime_loop']['scenario']['artifact_size_bytes'] = 4 * 1024 * 1024
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)
        self.assertTrue(any('artifact_size_bytes' in error for error in errors))

    def test_rejects_missing_or_non_integer_round_count(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_smoke_marker()
        summary = self.complete_summary('passed')
        summary['formal_runtime_loop']['scenario']['round_count'] = None
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)
        self.assertTrue(any('round_count 必须为 2 轮' in error for error in errors))

    def test_rejects_strict_sync_server_not_waiting_after_first_commit(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_smoke_marker()
        summary = self.complete_summary('passed')
        summary['formal_runtime_loop']['rounds'][0]['strict_sync']['server_waited_after_first_commit'] = False
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)
        self.assertTrue(any('首个客户端提交后 server 保持等待' in error for error in errors))

    def test_rejects_downlink_matrix_failure(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_smoke_marker()
        summary = self.complete_summary('passed')
        summary['formal_runtime_loop']['rounds'][0]['downlink_matrix'] = {
            'status': 'failed',
            'uftp_connect_matrix': {'1': 'success', '2': 'failed'},
        }
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)
        self.assertTrue(any('UFTP' in error for error in errors))

    def test_rejects_unrecovered_queue_pause(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_smoke_marker()
        summary = self.complete_summary('passed')
        summary['formal_runtime_loop']['rounds'][0]['telemetry'] = {
            'queue': {
                'tun_read_pause_total': 2,
                'tun_read_resume_total': 1,
                'pause_recovered': False,
            }
        }
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)
        self.assertTrue(any('队列自然暂停' in error for error in errors))

    def test_rejects_concurrent_put_interval_mismatch_with_uploads(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_smoke_marker()
        summary = self.complete_summary('passed')
        summary['formal_runtime_loop']['rounds'][0]['concurrent_put']['client_intervals']['1'] = {'start': 5.0, 'end': 6.0}
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)
        self.assertTrue(any('concurrent_put node 1 区间与 uploads 不一致' in error for error in errors))

    def test_rejects_concurrent_put_overlap_duration_mismatch(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_smoke_marker()
        summary = self.complete_summary('passed')
        summary['formal_runtime_loop']['rounds'][0]['concurrent_put']['overlap_duration_seconds'] = 3.5
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)
        self.assertTrue(any('overlap_duration_seconds 与时间区间计算不符' in error for error in errors))

    def test_rejects_concurrent_put_natural_overlap_contradiction(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_smoke_marker()
        summary = self.complete_summary('passed')
        summary['formal_runtime_loop']['rounds'][0]['concurrent_put']['natural_overlap'] = False
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)
        self.assertTrue(any('natural_overlap 标记与实际时间区间矛盾' in error for error in errors))

    def test_rejects_smoke_io_timeout_exceeding_120_in_formal(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_smoke_marker()
        self.write_lifecycle_evidence()
        summary = self.complete_summary('passed')
        summary['run_id'] = 'run-123'
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')
        self.write_json('envelope.json', {
            'schema_version': 1,
            'run_id': 'run-123',
            'mode': 'formal',
            'network_isolation': {'prohibit_management_as_data_plane': True},
            'resolved_config': {
                'radio_mcs_index': 3,
                'radio_bandwidth': 40,
                'channel_width': 'HT40+',
                'radio_txpower_dbm': 12,
                'uftp_rate_kbps': 15000,
                'grant_duration_ms': 120,
                'guard_interval_ms': 20,
                'smoke_io_timeout_seconds': 130,
            },
        })

        errors = validate_archive(self.root)
        self.assertTrue(any('smoke_io_timeout_seconds' in error for error in errors))

    def test_rejects_io_timeout_exceeding_120_in_formal(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_smoke_marker()
        self.write_lifecycle_evidence()
        summary = self.complete_summary('passed')
        summary['run_id'] = 'run-123'
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')
        self.write_json('envelope.json', {
            'schema_version': 1,
            'run_id': 'run-123',
            'mode': 'formal',
            'network_isolation': {'prohibit_management_as_data_plane': True},
            'resolved_config': {
                'radio_mcs_index': 3,
                'radio_bandwidth': 40,
                'channel_width': 'HT40+',
                'radio_txpower_dbm': 12,
                'uftp_rate_kbps': 15000,
                'grant_duration_ms': 120,
                'guard_interval_ms': 20,
                'io_timeout_seconds': 130,
            },
        })

        errors = validate_archive(self.root)
        self.assertTrue(any('io_timeout_seconds' in error for error in errors))

    def test_validates_archive_passed_two_rounds_40mib(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_smoke_marker()
        self.write_lifecycle_evidence()

        shared_sha = 'a' * 64
        r1 = self.round_evidence(1, size=40 * 1024 * 1024, model_sha=shared_sha)
        r2 = self.round_evidence(2, size=40 * 1024 * 1024, model_sha=shared_sha)
        summary = self.complete_summary('passed')
        summary['pre_runtime_smoke'] = self.smoke_gate_evidence(artifact_size=40 * 1024 * 1024)
        summary['formal_runtime_loop']['scenario'] = {
            'round_count': 2,
            'artifact_size_bytes': 40 * 1024 * 1024,
            'training_delay_ms_by_node': {'1': 0, '2': 0},
            'placeholder_training': 'template_copy',
            'placeholder_aggregation': 'model_copy',
            'update_template_sha256_by_node': {
                '1': 'b'.ljust(64, '0'),
                '2': 'c'.ljust(64, '0'),
            },
        }
        summary['formal_runtime_loop']['rounds'] = [r1, r2]
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')
        self.write_json('envelope.json', {
            'schema_version': 1,
            'run_id': 'test-run',
            'network_isolation': {'prohibit_management_as_data_plane': True},
            'resolved_config': {
                'radio_mcs_index': 3,
                'radio_bandwidth': 40,
                'channel_width': 'HT40+',
                'radio_txpower_dbm': 12,
                'uftp_rate_kbps': 15000,
                'grant_duration_ms': 120,
                'guard_interval_ms': 20,
                'rounds': 2,
                'artifact_size_bytes': 40 * 1024 * 1024,
                'training_delay_ms_by_node': {'1': 0, '2': 0},
                'smoke_cycle_count': 3,
                'smoke_io_timeout_seconds': 120,
                'smoke_cycle_deadline_seconds': 240,
                'runtime_timeout_seconds': 400,
                'io_timeout_seconds': 120,
            }
        })

        errors = validate_archive(self.root)
        self.assertEqual([], errors)

    def test_rejects_round_2_aggregation_model_mismatch(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_smoke_marker()
        self.write_lifecycle_evidence()

        r1 = self.round_evidence(1, size=40 * 1024 * 1024, model_sha='1' * 64)
        r2 = self.round_evidence(2, size=40 * 1024 * 1024, model_sha='2' * 64)
        summary = self.complete_summary('passed')
        summary['formal_runtime_loop']['scenario'] = {
            'round_count': 2,
            'artifact_size_bytes': 40 * 1024 * 1024,
            'training_delay_ms_by_node': {'1': 0, '2': 0},
            'placeholder_training': 'template_copy',
            'placeholder_aggregation': 'model_copy',
            'update_template_sha256_by_node': {
                '1': 'b'.ljust(64, '0'),
                '2': 'c'.ljust(64, '0'),
            },
        }
        summary['formal_runtime_loop']['rounds'] = [r1, r2]
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)
        self.assertTrue(any('占位聚合模型 SHA-256 与前一轮输出不匹配' in error for error in errors))

    def test_rejects_missing_client_in_pkt_src_telemetry(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_smoke_marker()
        self.write_lifecycle_evidence()

        shared_sha = 'a' * 64
        r1 = self.round_evidence(1, size=40 * 1024 * 1024, model_sha=shared_sha)
        r2 = self.round_evidence(2, size=40 * 1024 * 1024, model_sha=shared_sha)
        # Remove client 2 from loss_and_fec_by_node in round 2
        del r2['telemetry']['loss_and_fec_by_node']['2']
        summary = self.complete_summary('passed')
        summary['formal_runtime_loop']['scenario'] = {
            'round_count': 2,
            'artifact_size_bytes': 40 * 1024 * 1024,
            'training_delay_ms_by_node': {'1': 0, '2': 0},
            'placeholder_training': 'template_copy',
            'placeholder_aggregation': 'model_copy',
            'update_template_sha256_by_node': {
                '1': 'b'.ljust(64, '0'),
                '2': 'c'.ljust(64, '0'),
            },
        }
        summary['formal_runtime_loop']['rounds'] = [r1, r2]
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)
        self.assertTrue(any('loss_and_fec_by_node 缺少 client 2' in error for error in errors))

    def test_rejects_mismatch_with_envelope_rounds_count(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_smoke_marker()
        self.write_lifecycle_evidence()

        summary = self.complete_summary('passed')
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')
        self.write_json('envelope.json', {
            'schema_version': 1,
            'run_id': 'test-run',
            'resolved_config': {
                'radio_mcs_index': 3,
                'radio_bandwidth': 40,
                'channel_width': 'HT40+',
                'radio_txpower_dbm': 12,
                'uftp_rate_kbps': 15000,
                'grant_duration_ms': 120,
                'guard_interval_ms': 20,
                'rounds': 3,
                'artifact_size_bytes': 40 * 1024 * 1024,
            }
        })

        errors = validate_archive(self.root)
        self.assertTrue(any('round_count 与 envelope 不一致' in error for error in errors))

    def test_rejects_config_equivalence_failure(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_smoke_marker()
        summary = self.complete_summary('passed')
        summary['formal_runtime_loop']['config_equivalence'] = {
            'status': 'failed',
            'errors': ['mismatch'],
        }
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)
        self.assertTrue(any('配置等价性' in error for error in errors))

    def test_rejects_controlled_stop_failure(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_smoke_marker()
        summary = self.complete_summary('passed')
        summary['formal_runtime_loop']['controlled_stop'] = {
            'status': 'failed',
            'server_stopped': False,
        }
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)
        self.assertTrue(any('受控停止' in error for error in errors))

    def write_runtime_evidence(self):
        for name in ('server-journal.txt', 'client1-journal.txt', 'client2-journal.txt'):
            self.write_text(name, 'evidence\n')
        for index in range(12):
            self.write_text('route-%d.txt' % index, 'route\n')

    def test_rejects_passed_lifecycle_missing_first_stop(self):
        summary = self.complete_summary('passed')
        summary['lifecycle'].pop('first_stop')
        self.write_runtime_evidence()
        self.write_smoke_marker()
        self.write_lifecycle_evidence()
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)
        self.assertTrue(any('缺少阶段: first_stop' in error for error in errors))

    def test_rejects_lifecycle_first_stop_with_tun_residual(self):
        summary = self.complete_summary('passed')
        summary['lifecycle']['first_stop']['roles']['client1']['tun_exists'] = True
        self.write_runtime_evidence()
        self.write_smoke_marker()
        self.write_lifecycle_evidence()
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)
        self.assertTrue(any('TUN 设备必须已消失' in error for error in errors))

    def test_rejects_lifecycle_first_stop_with_cgroup_residual(self):
        summary = self.complete_summary('passed')
        summary['lifecycle']['first_stop']['roles']['server']['cgroup_clean'] = False
        self.write_runtime_evidence()
        self.write_smoke_marker()
        self.write_lifecycle_evidence()
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)
        self.assertTrue(any('cgroup 必须清理干净' in error for error in errors))

    def test_rejects_lifecycle_first_stop_with_orphan_proc(self):
        summary = self.complete_summary('passed')
        summary['lifecycle']['first_stop']['roles']['client2']['orphan_processes'] = ['uftpd']
        self.write_runtime_evidence()
        self.write_smoke_marker()
        self.write_lifecycle_evidence()
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)
        self.assertTrue(any('不得残留孤儿进程' in error for error in errors))

    def test_rejects_lifecycle_restart_unclean_work_dir(self):
        summary = self.complete_summary('passed')
        summary['lifecycle']['restart']['clean_state_verified'] = False
        self.write_runtime_evidence()
        self.write_smoke_marker()
        self.write_lifecycle_evidence()
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)
        self.assertTrue(any('必须证明工作区干净隔离' in error for error in errors))

    def test_rejects_lifecycle_restart_cgroup_child_overlap(self):
        summary = self.complete_summary('passed')
        summary['lifecycle']['restart']['roles']['server']['cgroup_disjoint'] = False
        self.write_runtime_evidence()
        self.write_smoke_marker()
        self.write_lifecycle_evidence()
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)
        self.assertTrue(any('子进程与旧进程集合重叠' in error for error in errors))

    def test_rejects_lifecycle_restart_missing_old_main_pid(self):
        summary = self.complete_summary('passed')
        summary['lifecycle']['restart']['roles']['client1']['old_main_pid'] = 0
        self.write_runtime_evidence()
        self.write_smoke_marker()
        self.write_lifecycle_evidence()
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)
        self.assertTrue(any('必须记录有效的前序 MainPID' in error for error in errors))

    def test_rejects_lifecycle_restart_with_pid_overlap(self):
        summary = self.complete_summary('passed')
        summary['lifecycle']['restart']['roles']['server']['pid_reused'] = True
        self.write_runtime_evidence()
        self.write_smoke_marker()
        self.write_lifecycle_evidence()
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)
        self.assertTrue(any('禁止复用旧进程 PID' in error for error in errors))

    def test_rejects_lifecycle_restart_with_tun_down(self):
        summary = self.complete_summary('passed')
        summary['lifecycle']['restart']['roles']['client1']['tun_up'] = False
        self.write_runtime_evidence()
        self.write_smoke_marker()
        self.write_lifecycle_evidence()
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)
        self.assertTrue(any('处于 UP 状态' in error for error in errors))

    def test_rejects_lifecycle_second_stop_with_tun_residual(self):
        summary = self.complete_summary('passed')
        summary['lifecycle']['second_stop']['roles']['client2']['tun_exists'] = True
        self.write_runtime_evidence()
        self.write_smoke_marker()
        self.write_lifecycle_evidence()
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)
        self.assertTrue(any('TUN 设备必须已消失' in error for error in errors))

    def test_rejects_lifecycle_missing_evidence_files(self):
        summary = self.complete_summary('passed')
        self.write_runtime_evidence()
        self.write_smoke_marker()
        # 故意不调用 write_lifecycle_evidence()
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)
        self.assertTrue(any('缺少 lifecycle 原始证据文件' in error for error in errors))

    def test_rejects_passed_conclusion_when_lifecycle_failed(self):
        summary = self.complete_summary('passed')
        summary['lifecycle'] = {'status': 'failed', 'reason': '残留孤儿进程'}
        self.write_runtime_evidence()
        self.write_smoke_marker()
        self.write_lifecycle_evidence()
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)
        self.assertTrue(any('存在未通过分区时 conclusion 不能 passed' in error for error in errors))

    def test_rejects_diagnostic_mode_when_passed(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_smoke_marker()
        self.write_lifecycle_evidence()
        summary = self.complete_summary('passed')
        summary['run_id'] = 'run-123'
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')
        self.write_json('envelope.json', {
            'schema_version': 1,
            'run_id': 'run-123',
            'mode': 'diagnostic',
            'network_isolation': {'prohibit_management_as_data_plane': True},
            'resolved_config': {
                'radio_mcs_index': 3,
                'radio_bandwidth': 40,
                'channel_width': 'HT40+',
                'radio_txpower_dbm': 12,
                'uftp_rate_kbps': 15000,
                'grant_duration_ms': 120,
                'guard_interval_ms': 20,
                'smoke_cycle_deadline_seconds': 240,
                'runtime_timeout_seconds': 180,
                'io_timeout_seconds': 120,
            },
        })

        errors = validate_archive(self.root)
        self.assertTrue(any('diagnostic' in error or '非 formal 模式' in error for error in errors))

    def test_rejects_candidate_feedback_start_immediately(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_smoke_marker()
        self.write_lifecycle_evidence()
        summary = self.complete_summary('passed')
        summary['formal_runtime_loop']['link_args'] = ['--feedback-window-start-immediately']
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)
        self.assertTrue(any('--feedback-window-start-immediately' in error for error in errors))

    def test_rejects_mismatched_run_id_in_smoke(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_smoke_marker()
        self.write_lifecycle_evidence()
        summary = self.complete_summary('passed')
        summary['run_id'] = 'run-main'
        summary['pre_runtime_smoke']['run_id'] = 'run-other'
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)
        self.assertTrue(any('pre_runtime_smoke 与 summary 的 run_id 不一致' in error for error in errors))

    def test_rejects_mismatched_run_id_in_lifecycle(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_smoke_marker()
        self.write_lifecycle_evidence()
        summary = self.complete_summary('passed')
        summary['run_id'] = 'run-main'
        summary['lifecycle']['run_id'] = 'run-other'
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)
        self.assertTrue(any('lifecycle 与 summary 的 run_id 不一致' in error for error in errors))

    def test_rejects_overwritten_archive(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_smoke_marker()
        self.write_lifecycle_evidence()
        summary = self.complete_summary('passed')
        summary['run_id'] = 'run-123'
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')
        self.write_json('envelope.json', {
            'schema_version': 1,
            'run_id': 'run-123',
            'mode': 'formal',
            'overwritten': True,
            'network_isolation': {'prohibit_management_as_data_plane': True},
        })

        errors = validate_archive(self.root)
        self.assertTrue(any('覆盖' in error for error in errors))

    def test_rejects_runtime_passed_when_gate_failed(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_smoke_marker()
        self.write_lifecycle_evidence()
        summary = self.complete_summary('failed')
        summary['pre_runtime_smoke']['status'] = 'failed'
        summary['pre_runtime_smoke']['reason'] = 'Gate 周期 1 丢包'
        summary['formal_runtime_loop']['status'] = 'passed'
        summary['conclusion']['reason'] = 'Gate 周期 1 丢包'
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)
        self.assertTrue(any('Gate 未通过' in error for error in errors))

    def test_rejects_lifecycle_passed_when_runtime_failed(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_smoke_marker()
        self.write_lifecycle_evidence()
        summary = self.complete_summary('failed')
        summary['formal_runtime_loop']['status'] = 'failed'
        summary['conclusion']['reason'] = 'Runtime 失败'
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)
        self.assertTrue(any('formal_runtime_loop 未通过时 lifecycle 不得标记为 passed' in error for error in errors))

    def test_rejects_missing_partitions(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_smoke_marker()
        self.write_lifecycle_evidence()
        for key in ('orchestration', 'pre_runtime_smoke', 'formal_runtime_loop', 'lifecycle', 'conclusion'):
            summary = self.complete_summary('passed')
            del summary[key]
            self.write_json('issue41_summary.json', summary)
            self.write_text('result.md', '# result\n')
            errors = validate_archive(self.root)
            self.assertTrue(any('缺少分区' in error and key in error for error in errors))


    def test_envelope_requires_deadlines_in_resolved_config(self):
        summary = self.complete_summary('passed')
        summary['run_id'] = 'run-123'
        self.write_runtime_evidence()
        self.write_smoke_marker()
        self.write_lifecycle_evidence()
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')
        self.write_json('envelope.json', {
            'schema_version': 1,
            'run_id': 'run-123',
            'mode': 'formal',
            'network_isolation': {'prohibit_management_as_data_plane': True},
            'resolved_config': {
                'radio_mcs_index': 3,
                'radio_bandwidth': 40,
                'channel_width': 'HT40+',
                'radio_txpower_dbm': 12,
                'uftp_rate_kbps': 15000,
                'grant_duration_ms': 120,
                'guard_interval_ms': 20,
                'channel': 157,
            },
        })

        errors = validate_archive(self.root)
        self.assertTrue(any('deadline' in error.lower() or '超时' in error for error in errors))

    def test_accepts_valid_failed_archive_preserving_completed_stages(self):
        self.write_smoke_marker('run-123')
        summary = self.complete_summary('failed')
        summary['formal_runtime_loop'] = {
            'status': 'failed',
            'reason': 'client2 提交超时',
        }
        summary['lifecycle'] = {
            'status': 'skipped',
            'reason': '前序阶段失败，跳过生命周期测试',
        }
        summary['conclusion'] = {
            'status': 'failed',
            'reason': 'client2 提交超时',
            'category': 'implementation',
        }
        summary['run_id'] = 'run-123'
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')
        self.write_json('envelope.json', {
            'schema_version': 1,
            'run_id': 'run-123',
            'mode': 'formal',
            'network_isolation': {'prohibit_management_as_data_plane': True},
            'resolved_config': {
                'radio_mcs_index': 3,
                'radio_bandwidth': 40,
                'channel_width': 'HT40+',
                'radio_txpower_dbm': 12,
                'uftp_rate_kbps': 15000,
                'grant_duration_ms': 120,
                'guard_interval_ms': 20,
                'smoke_cycle_count': 3,
                'smoke_io_timeout_seconds': 120,
                'smoke_cycle_deadline_seconds': 240,
                'artifact_size_bytes': 40 * 1024 * 1024,
                'runtime_timeout_seconds': 180,
                'io_timeout_seconds': 120,
            },
        })

        errors = validate_archive(self.root)
        self.assertEqual([], errors)


    def smoke_gate_evidence(self, artifact_size=4 * 1024 * 1024):
        cycles = [self.cycle_evidence(i, size=artifact_size) for i in (1, 2, 3)]
        return {
            'schema_version': 1,
            'status': 'passed',
            'gate_type': 'three_cycle_bidirectional',
            'cycle_count': 3,
            'artifact_size_bytes': artifact_size,
            'io_timeout_seconds': 120,
            'cycle_deadline_seconds': 240,
            'reused_processes': {
                'server_link_pid': 1001,
                'client1_link_pid': 1002,
                'client2_link_pid': 1003,
                'client1_uftpd_pid': 1004,
                'client2_uftpd_pid': 1005,
                'server_http_pid': 1006,
            },
            'cycles': cycles,
        }

    def cycle_evidence(self, index, size=4 * 1024 * 1024):
        model_sha = ('a%d' % index).ljust(64, '0')
        manifest_sha = ('m%d' % index).ljust(64, '0')
        c1_sha = ('b%d' % index).ljust(64, '0')
        c2_sha = ('c%d' % index).ljust(64, '0')
        return {
            'cycle_index': index,
            'status': 'passed',
            'downlink': {
                'status': 'passed',
                'operation': 'shared_uftp',
                'uftp_rate_kbps': 15000,
                'grant_duration_ms': 120,
                'guard_interval_ms': 20,
                'file': {'name': 'model.bin', 'size_bytes': size, 'sha256': model_sha},
                'manifest': {'name': 'model.manifest.json', 'size_bytes': 120, 'sha256': manifest_sha},
                'uftp_connect_matrix': {'1': 'success', '2': 'success'},
                'uftp_result_matrix': {
                    '1': {'model.bin': 'copy', 'model.manifest.json': 'copy'},
                    '2': {'model.bin': 'copy', 'model.manifest.json': 'copy'},
                },
                'client_received_verification': {
                    '1': {'model_sha256': model_sha, 'model_size_bytes': size, 'manifest_sha256': manifest_sha, 'verified': True},
                    '2': {'model_sha256': model_sha, 'model_size_bytes': size, 'manifest_sha256': manifest_sha, 'verified': True},
                },
                'duration_seconds': 3.5,
            },
            'uplink': {
                'status': 'passed',
                'operation': 'http_put_over_tcp',
                'uploads': [
                    {
                        'node_id': 1,
                        'client_address': '10.80.0.11',
                        'size_bytes': size,
                        'sha256': c1_sha,
                        'client_http_status': 201,
                        'server_http_status': 201,
                        'server_outcome': 'committed',
                        'client_put_interval': {'start': 10.0, 'end': 14.0},
                        'server_put_interval': {'start': 10.1, 'end': 13.9},
                    },
                    {
                        'node_id': 2,
                        'client_address': '10.80.0.12',
                        'size_bytes': size,
                        'sha256': c2_sha,
                        'client_http_status': 201,
                        'server_http_status': 201,
                        'server_outcome': 'committed',
                        'client_put_interval': {'start': 14.5, 'end': 18.5},
                        'server_put_interval': {'start': 14.6, 'end': 18.4},
                    },
                ],
                'active_upload_sets': [[1], [], [2], []],
                'active_uploads_converged': True,
                'temporary_state_cleaned': True,
                'duration_seconds': 8.5,
            },
            'telemetry': {
                'ready_accepted_total': 20,
                'ready_rejected_total': 0,
                'grant_sent_total': 100,
                'authorized_sends_by_node': {'1': 500, '2': 500},
                'server_rx': {
                    'rx_ant_samples': 20,
                    'rx_packets': 1200,
                    'rx_bytes': 1048576,
                },
                'queue': {
                    'tun_read_pause_total': 1,
                    'tun_read_resume_total': 1,
                    'currently_paused': False,
                    'pause_recovered': True,
                    'tun_read_pause_total_by_reason': {
                        'queued_bytes_threshold': 1,
                        'queued_packets_limit': 0,
                    },
                    'queued_bytes_max': 65536,
                    'queued_packets_max': 32,
                },
                'reassembly': {
                    'reassembly_overflow_evict': 0,
                    'unfinished_block_limit': 40,
                },
                'sender_isolation': {
                    'unauthorized_air_injections': 0,
                    'unknown_client_rejects': 0,
                },
                'feedback': {
                    'feedback_window_open_count': 10,
                    'feedback_window_close_count': 10,
                    'feedback_uplink_hit_total': 20,
                },
                'loss_and_fec': {
                    'packets_lost': 2,
                    'packets_fec_recovered': 2,
                },
                'loss_and_fec_by_node': {
                    '1': {
                        'sample_count': 1,
                        'rx_packets': 600,
                        'rx_bytes': 524288,
                        'packets_fec_recovered': 1,
                        'packets_lost': 1,
                        'out_packets': 550,
                        'out_bytes': 500000,
                        'loss_rate': 0.0016,
                        'fec_recovery_rate': 0.0016,
                    },
                    '2': {
                        'sample_count': 1,
                        'rx_packets': 600,
                        'rx_bytes': 524288,
                        'packets_fec_recovered': 1,
                        'packets_lost': 1,
                        'out_packets': 550,
                        'out_bytes': 500000,
                        'loss_rate': 0.0016,
                        'fec_recovery_rate': 0.0016,
                    },
                },
                'tcp_retransmits': 1,
                'phase_durations': {
                    'downlink_seconds': 3.5,
                    'uplink_seconds': 8.5,
                    'cycle_total_seconds': 12.0,
                },
            },
            'total_duration_seconds': 12.0,
            'deadline_seconds': 240,
            'io_timeout_seconds': 120,
        }

    def complete_summary(self, conclusion):
        return {
            'orchestration': {'status': 'passed'},
            'pre_runtime_smoke': self.smoke_gate_evidence(artifact_size=40 * 1024 * 1024),
            'formal_runtime_loop': {
                'status': 'passed',
                'runtime_interfaces': [
                    'publish_model', 'wait_for_model', 'submit_update',
                    'wait_for_updates'],
                'data_plane': '10.80.0.0/24',
                'server_wait_for_updates_returned_node_ids': [1, 2],
                'partial_result_returned': False,
                'scenario': {
                    'round_count': 2,
                    'artifact_size_bytes': 40 * 1024 * 1024,
                    'training_delay_ms_by_node': {'1': 0, '2': 0},
                    'placeholder_training': 'template_copy',
                    'placeholder_aggregation': 'model_copy',
                    'update_template_sha256_by_node': {
                        '1': 'b'.ljust(64, '0'),
                        '2': 'c'.ljust(64, '0'),
                    },
                },
                'rounds': [
                    self.round_evidence(1, size=40 * 1024 * 1024, model_sha='a' * 64),
                    self.round_evidence(2, size=40 * 1024 * 1024, model_sha='a' * 64),
                ],
                'server_result': os.path.join(self.root, 'server-result.json'),
                'client1_result': os.path.join(self.root, 'client1-result.json'),
                'client2_result': os.path.join(self.root, 'client2-result.json'),
                'server_journal': os.path.join(self.root, 'server-journal.txt'),
                'client1_journal': os.path.join(self.root, 'client1-journal.txt'),
                'client2_journal': os.path.join(self.root, 'client2-journal.txt'),
                'route_evidence': [
                    os.path.join(self.root, 'route-%d.txt' % index)
                    for index in range(12)],
                'config_equivalence': {
                    'status': 'passed', 'errors': [],
                    'gate_configs': {'server': {'uftp_rate_kbps': 15000}},
                    'runtime_configs': {'server': {'uftp_rate_kbps': 15000}},
                },
                'controlled_stop': {'status': 'passed', 'server_stopped': True, 'client1_stopped': True, 'client2_stopped': True, 'cleaned': True},
            },
            'lifecycle': self.lifecycle_evidence(),
            'conclusion': {
                'status': conclusion,
                'reason': 'all evidence complete' if conclusion == 'passed' else 'failed section',
            },
        }

    def lifecycle_evidence(self):
        return {
            'status': 'passed',
            'first_stop': {
                'status': 'passed',
                'roles': {
                    role: {
                        'unit_status': 'inactive',
                        'cgroup_clean': True,
                        'tun_exists': False,
                        'orphan_processes': [],
                    } for role in ('server', 'client1', 'client2')
                }
            },
            'restart': {
                'status': 'passed',
                'clean_state_verified': True,
                'roles': {
                    role: {
                        'unit_status': 'active',
                        'main_pid': 2000 + i,
                        'old_main_pid': 1000 + i,
                        'pid_reused': False,
                        'cgroup_disjoint': True,
                        'tun_up': True,
                    } for i, role in enumerate(('server', 'client1', 'client2'), 1)
                }
            },
            'second_stop': {
                'status': 'passed',
                'roles': {
                    role: {
                        'unit_status': 'inactive',
                        'cgroup_clean': True,
                        'tun_exists': False,
                        'orphan_processes': [],
                    } for role in ('server', 'client1', 'client2')
                }
            },
            'evidence_files': [
                'lifecycle/first_stop_evidence.json',
                'lifecycle/restart_evidence.json',
                'lifecycle/second_stop_evidence.json',
            ],
        }

    def write_lifecycle_evidence(self):
        self.write_json('lifecycle/first_stop_evidence.json', {'status': 'passed'})
        self.write_json('lifecycle/restart_evidence.json', {'status': 'passed'})
        self.write_json('lifecycle/second_stop_evidence.json', {'status': 'passed'})

    def round_evidence(self, index, size=4 * 1024 * 1024, model_sha=None):
        if model_sha is None:
            model_sha = ('a%d' % index).ljust(64, '0')
        uploads = []
        for node_id, sha in ((1, 'b'), (2, 'c')):
            uploads.append({
                'node_id': node_id,
                'size_bytes': size,
                'sha256': sha.ljust(64, '0'),
                'client_put_interval': {'start': 1.0, 'end': 2.0},
                'client_http_status': 201,
                'server_put_interval': {'start': 1.0, 'end': 2.0},
                'server_http_status': 201,
                'server_outcome': 'committed',
            })
        return {
            'round_index': index,
            'round_id': 'round-%d' % index,
            'model': {'size_bytes': size, 'sha256': model_sha},
            'model_receive_intervals': {
                '1': {'start': 1.0, 'end': 2.0},
                '2': {'start': 1.0, 'end': 2.0},
            },
            'downlink_matrix': {
                'status': 'passed',
                'uftp_connect_matrix': {'1': 'success', '2': 'success'},
                'uftp_result_matrix': {
                    '1': {'model.bin': 'copy', 'model.manifest.json': 'copy'},
                    '2': {'model.bin': 'copy', 'model.manifest.json': 'copy'},
                },
            },
            'uploads': uploads,
            'active_upload_sets': [[1], [1, 2], [2], []],
            'concurrent_put': {
                'natural_overlap': True,
                'overlap_duration_seconds': 1.0,
                'concurrent_active_observed': True,
                'client_intervals': {
                    '1': {'start': 1.0, 'end': 2.0},
                    '2': {'start': 1.0, 'end': 2.0},
                },
                'server_intervals': {
                    '1': {'start': 1.0, 'end': 2.0},
                    '2': {'start': 1.0, 'end': 2.0},
                },
            },
            'strict_sync': {
                'client1_committed_before_client2': True,
                'server_waited_after_first_commit': True,
                'intermediate_committed_node_ids': [1],
                'intermediate_pending_node_ids': [2],
                'server_wait_returned_node_ids': [1, 2],
                'partial_result_returned': False,
            },
            'server_committed_node_ids': [1, 2],
            'server_wait_returned_node_ids': [1, 2],
            'telemetry': {
                'loss_and_fec_by_node': {
                    '1': {'rx_packets': 1000, 'out_packets': 980, 'packets_lost': 20, 'packets_fec_recovered': 5, 'sample_count': 10},
                    '2': {'rx_packets': 1000, 'out_packets': 975, 'packets_lost': 25, 'packets_fec_recovered': 6, 'sample_count': 10},
                }
            }
        }

    def test_rejects_runtime_exceeding_round_deadline(self):
        self.write_runtime_evidence()
        self.write_smoke_marker()
        self.write_lifecycle_evidence()
        summary = self.complete_summary('passed')
        summary['formal_runtime_loop']['rounds'][0]['duration_seconds'] = 250.0
        summary['formal_runtime_loop']['rounds'][1]['duration_seconds'] = 200.0
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')
        self.write_json('envelope.json', {
            'schema_version': 1,
            'run_id': 'run-test-run',
            'mode': 'formal',
            'network_isolation': {'prohibit_management_as_data_plane': True},
            'resolved_config': {
                'radio_mcs_index': 3,
                'radio_bandwidth': 40,
                'channel_width': 'HT40+',
                'radio_txpower_dbm': 12,
                'uftp_rate_kbps': 15000,
                'grant_duration_ms': 120,
                'guard_interval_ms': 20,
                'rounds': 2,
                'artifact_size_bytes': 40 * 1024 * 1024,
                'training_delay_ms_by_node': {'1': 0, '2': 0},
                'smoke_cycle_count': 3,
                'smoke_io_timeout_seconds': 120,
                'smoke_cycle_deadline_seconds': 240,
                'runtime_timeout_seconds': 400,
                'io_timeout_seconds': 120,
            },
        })
        errors = validate_archive(self.root)
        self.assertTrue(any('formal_runtime_loop 实际总耗时' in e for e in errors))

    def test_rejects_missing_or_invalid_radio_txpower_dbm(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_smoke_marker()
        self.write_lifecycle_evidence()
        summary = self.complete_summary('passed')
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        # 缺少 radio_txpower_dbm
        self.write_json('envelope.json', {
            'schema_version': 1,
            'run_id': 'run-test-run',
            'mode': 'formal',
            'network_isolation': {'prohibit_management_as_data_plane': True},
            'resolved_config': {
                'radio_mcs_index': 3,
                'radio_bandwidth': 40,
                'channel_width': 'HT40+',
                'uftp_rate_kbps': 15000,
                'grant_duration_ms': 120,
                'guard_interval_ms': 20,
                'rounds': 2,
                'artifact_size_bytes': 40 * 1024 * 1024,
                'training_delay_ms_by_node': {'1': 0, '2': 0},
                'smoke_cycle_count': 3,
                'smoke_io_timeout_seconds': 120,
                'smoke_cycle_deadline_seconds': 240,
                'runtime_timeout_seconds': 400,
                'io_timeout_seconds': 120,
            },
        })
        errors = validate_archive(self.root)
        self.assertTrue(any('radio_txpower_dbm' in e for e in errors))

        # 非法 radio_txpower_dbm (> 30)
        self.write_json('envelope.json', {
            'schema_version': 1,
            'run_id': 'run-test-run',
            'mode': 'formal',
            'network_isolation': {'prohibit_management_as_data_plane': True},
            'resolved_config': {
                'radio_mcs_index': 3,
                'radio_bandwidth': 40,
                'channel_width': 'HT40+',
                'radio_txpower_dbm': 35,
                'uftp_rate_kbps': 15000,
                'grant_duration_ms': 120,
                'guard_interval_ms': 20,
                'rounds': 2,
                'artifact_size_bytes': 40 * 1024 * 1024,
                'training_delay_ms_by_node': {'1': 0, '2': 0},
                'smoke_cycle_count': 3,
                'smoke_io_timeout_seconds': 120,
                'smoke_cycle_deadline_seconds': 240,
                'runtime_timeout_seconds': 400,
                'io_timeout_seconds': 120,
            },
        })
        errors = validate_archive(self.root)
        self.assertTrue(any('radio_txpower_dbm' in e for e in errors))

    def write_smoke_marker(self, run_id='test-run'):
        marker = {
            'schema_version': 1,
            'gate_type': 'three_cycle_bidirectional',
            'status': 'passed',
            'run_id': run_id,
            'data_plane': '10.80.0.0/24',
        }
        self.write_json('pre_runtime_smoke/passed.json', marker)

    def write_json(self, name, value):
        path = os.path.join(self.root, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(value, fh)

    def write_text(self, name, value):
        path = os.path.join(self.root, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(value)


if __name__ == '__main__':
    unittest.main()
