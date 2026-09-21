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

    def test_accepts_complete_passed_summary(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_smoke_marker()
        self.write_json('issue41_summary.json', self.complete_summary('passed'))
        self.write_text('result.md', '# result\n')

        self.assertEqual([], validate_archive(self.root))

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

        self.assertTrue(any('一轮' in error for error in errors))

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
        summary['formal_runtime_loop']['scenario']['training_delay_ms_by_node'] = {'1': 0, '2': 0}
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
        summary['formal_runtime_loop']['scenario']['artifact_size_bytes'] = 40 * 1024 * 1024
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)
        self.assertTrue(any('artifact_size_bytes' in error for error in errors))

    def test_rejects_strict_sync_client1_not_before_client2(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_smoke_marker()
        summary = self.complete_summary('passed')
        summary['formal_runtime_loop']['rounds'][0]['strict_sync']['client1_committed_before_client2'] = False
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)
        self.assertTrue(any('client1 先于 client2' in error for error in errors))

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

    def smoke_gate_evidence(self):
        cycles = [self.cycle_evidence(i) for i in (1, 2, 3)]
        return {
            'schema_version': 1,
            'status': 'passed',
            'gate_type': 'three_cycle_bidirectional',
            'cycle_count': 3,
            'artifact_size_bytes': 4 * 1024 * 1024,
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

    def cycle_evidence(self, index):
        size = 4 * 1024 * 1024
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
            'pre_runtime_smoke': self.smoke_gate_evidence(),
            'formal_runtime_loop': {
                'status': 'passed',
                'runtime_interfaces': [
                    'publish_model', 'wait_for_model', 'submit_update',
                    'wait_for_updates'],
                'data_plane': '10.80.0.0/24',
                'server_wait_for_updates_returned_node_ids': [1, 2],
                'partial_result_returned': False,
                'scenario': {
                    'round_count': 1,
                    'artifact_size_bytes': 4 * 1024 * 1024,
                    'training_delay_ms_by_node': {'1': 0, '2': 3000},
                    'placeholder_training': 'template_copy',
                    'placeholder_aggregation': 'model_copy',
                    'update_template_sha256_by_node': {
                        '1': 'b'.ljust(64, '0'),
                        '2': 'c'.ljust(64, '0'),
                    },
                },
                'rounds': [self.round_evidence(1)],
                'server_result': os.path.join(self.root, 'server-result.json'),
                'client1_result': os.path.join(self.root, 'client1-result.json'),
                'client2_result': os.path.join(self.root, 'client2-result.json'),
                'server_journal': os.path.join(self.root, 'server-journal.txt'),
                'client1_journal': os.path.join(self.root, 'client1-journal.txt'),
                'client2_journal': os.path.join(self.root, 'client2-journal.txt'),
                'route_evidence': [
                    os.path.join(self.root, 'route-%d.txt' % index)
                    for index in range(12)],
            },
            'lifecycle': {'status': 'passed'},
            'conclusion': {
                'status': conclusion,
                'reason': 'all evidence complete' if conclusion == 'passed' else 'failed section',
            },
        }

    def round_evidence(self, index):
        size = 4 * 1024 * 1024
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
            'uploads': uploads,
            'active_upload_sets': [[1], [1, 2], [2], []],
            'strict_sync': {
                'client1_committed_before_client2': True,
                'server_waited_after_client1': True,
                'intermediate_committed_node_ids': [1],
                'intermediate_pending_node_ids': [2],
                'server_wait_returned_node_ids': [1, 2],
                'partial_result_returned': False,
            },
            'server_committed_node_ids': [1, 2],
            'server_wait_returned_node_ids': [1, 2],
        }

    def write_smoke_marker(self):
        marker = {
            'schema_version': 1,
            'gate_type': 'three_cycle_bidirectional',
            'status': 'passed',
            'run_id': 'test-run',
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
