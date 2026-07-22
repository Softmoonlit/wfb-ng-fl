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
        self.write_json('pre_runtime_smoke/downlink_uftp/passed.json', self.smoke_marker('downlink_uftp'))
        self.write_json('pre_runtime_smoke/uplink_http_put/passed.json', self.smoke_marker('uplink_http_put'))
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

        self.assertTrue(any('smoke marker downlink_uftp' in error for error in errors))
        self.assertTrue(any('smoke marker uplink_http_put' in error for error in errors))

    def test_rejects_passed_summary_without_two_complete_40mib_rounds(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_json('pre_runtime_smoke/downlink_uftp/passed.json', self.smoke_marker('downlink_uftp'))
        self.write_json('pre_runtime_smoke/uplink_http_put/passed.json', self.smoke_marker('uplink_http_put'))
        summary = self.complete_summary('passed')
        summary['formal_runtime_loop']['rounds'].pop()
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)

        self.assertTrue(any('两轮' in error for error in errors))

    def test_rejects_update_not_matching_its_reused_template(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_json('pre_runtime_smoke/downlink_uftp/passed.json', self.smoke_marker('downlink_uftp'))
        self.write_json('pre_runtime_smoke/uplink_http_put/passed.json', self.smoke_marker('uplink_http_put'))
        summary = self.complete_summary('passed')
        summary['formal_runtime_loop']['rounds'][1]['uploads'][0]['sha256'] = 'd'.ljust(64, '0')
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)

        self.assertTrue(any('未复用对应 update 模板' in error for error in errors))

    def test_rejects_upload_in_progress_or_mismatched_submission_facts(self):
        for name in ('server-result.json', 'client1-result.json', 'client2-result.json'):
            self.write_json(name, {'conclusion': 'succeeded'})
        self.write_runtime_evidence()
        self.write_json('pre_runtime_smoke/downlink_uftp/passed.json', self.smoke_marker('downlink_uftp'))
        self.write_json('pre_runtime_smoke/uplink_http_put/passed.json', self.smoke_marker('uplink_http_put'))
        summary = self.complete_summary('passed')
        summary['formal_runtime_loop']['rounds'][0]['uploads'][0]['server_http_status'] = 409
        summary['formal_runtime_loop']['rounds'][0]['uploads'][0]['server_outcome'] = 'upload_in_progress'
        self.write_json('issue41_summary.json', summary)
        self.write_text('result.md', '# result\n')

        errors = validate_archive(self.root)

        self.assertTrue(any('upload_in_progress' in error for error in errors))

    def write_runtime_evidence(self):
        for name in ('server-journal.txt', 'client1-journal.txt', 'client2-journal.txt'):
            self.write_text(name, 'evidence\n')
        for index in range(12):
            self.write_text('route-%d.txt' % index, 'route\n')

    def complete_summary(self, conclusion):
        return {
            'orchestration': {'status': 'passed'},
            'pre_runtime_smoke': {
                'downlink_uftp': {'status': 'passed'},
                'uplink_http_put': {'status': 'passed'},
            },
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
                'rounds': [self.round_evidence(1), self.round_evidence(2)],
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
        size = 40 * 1024 * 1024
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
            'server_committed_node_ids': [1, 2],
            'server_wait_returned_node_ids': [1, 2],
        }

    def smoke_marker(self, name):
        return {
            'schema_version': 1,
            'smoke': name,
            'status': 'passed',
            'run_id': 'test-run',
            'data_plane': '10.80.0.0/24',
        }

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
