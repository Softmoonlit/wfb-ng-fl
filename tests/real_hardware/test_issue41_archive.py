#!/usr/bin/env python3
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
VALIDATOR = os.path.join(HERE, 'issue41_validate_archive.py')
ROUND = '550e8400-e29b-41d4-a716-446655440000'


class Issue41ArchiveTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = self.temporary.name
        self.make_fixture()

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, role, name, value):
        path = os.path.join(self.root, role, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        mode = 'wb' if isinstance(value, bytes) else 'w'
        kwargs = {} if mode == 'wb' else {'encoding': 'utf-8'}
        with open(path, mode, **kwargs) as fh:
            fh.write(value)
        return path

    def write_json(self, role, name, value):
        self.write(role, name, json.dumps(value, sort_keys=True) + '\n')

    def manifest(self, kind, payload, node_id=None):
        value = {'schema_version': 1, 'artifact_type': kind, 'round_id': ROUND,
                 'size_bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}
        if kind == 'model':
            value['participant_node_ids'] = [1, 2]
        else:
            value['node_id'] = node_id
        return value

    def make_fixture(self):
        model = b'issue41-model\n'
        model_manifest = self.manifest('model', model)
        server_log = ''.join([
            'ready_accept node_id=1\n', 'ready_accept node_id=2\n',
            'grant seq=1 node_id=1 x\n', 'grant seq=2 node_id=2 x\n',
            'feedback_window_open sequence=1\n',
            'feedback_uplink_hit node_id=1 sequence=1 total=1\n',
            'feedback_uplink_hit node_id=2 sequence=1 total=1\n',
            'feedback_window_close sequence=1\n', 'x\tPKT\tx\n',
            'x\tREASSEMBLY\t0:64\n'])
        client_log = 'grant_accept\nx\tTOKEN_AUTH\t0:1\nx\tREASSEMBLY\t0:64\n'
        queue = {'tun_read_pause_total': 0, 'tun_read_resume_total': 0,
                 'tun_read_pause_total_by_reason': {
                     'queued_bytes_threshold': 0, 'queued_packets_limit': 0}}
        self.write_json('baseline_uplink', 'formal_2a_summary.json', {
            'run_kind': 'real_hardware',
            'link_security_mode': 'trusted_plaintext',
            'scenario_id': 'v6_real_hardware_uplink_dual_long_run',
            'feedback_window_covered': False,
            'tun_read_pause_total': 1,
            'tun_read_resume_total': 1,
            'tun_read_pause_total_by_reason': {
                'queued_bytes_threshold': 1, 'queued_packets_limit': 0},
            'reassembly_overflow_evict': 0,
            'unfinished_block_limit': 64,
        })
        self.write('baseline_uplink', 'result.md',
                   '# Baseline\n\n- 总结论: **PASS**\n')
        self.write('baseline_uplink', 'baseline.log', 'formal raw log\n')
        self.write_json('baseline_uplink', 'queue-summary.json', {
            'tun_read_pause_total': 1,
            'tun_read_resume_total': 1,
        })
        baseline_files = (
            'formal_2a_summary.json', 'result.md', 'baseline.log',
            'queue-summary.json')
        baseline_checksums = []
        for name in baseline_files:
            path = os.path.join(self.root, 'baseline_uplink', name)
            with open(path, 'rb') as fh:
                baseline_checksums.append(
                    '%s  %s\n' % (hashlib.sha256(fh.read()).hexdigest(), name))
        self.write(
            'baseline_uplink', 'SHA256SUMS', ''.join(baseline_checksums))
        self.write_json('server', 'downlink-formal-2a-summary.json', {
            'run_kind': 'real_hardware',
            'link_security_mode': 'trusted_plaintext',
            'scenario_id': 'v6_real_hardware_downlink_shared_uftp_feedback',
            'feedback_window_covered': True,
            'grant_sent_total': 2,
            'ready_accepted_total': 2,
            'ready_rejected_total_by_reason': {
                'invalid_source': 0,
                'wrong_ingress_or_link_domain': 0,
                'unknown_client': 0,
            },
            'feedback_window_open_count': 1,
            'feedback_window_close_count': 1,
            'feedback_uplink_hit_total_by_node': {'1': 1, '2': 1},
            'feedback_uplink_hit_total': 2,
            'tun_read_pause_total': 0,
            'tun_read_resume_total': 0,
            'tun_read_pause_total_by_reason': {
                'queued_bytes_threshold': 0, 'queued_packets_limit': 0},
            'reassembly_overflow_evict': 0,
            'unfinished_block_limit': 64,
        })
        updates = {}
        for node_id in (1, 2):
            payload = ('update-%d\n' % node_id).encode('ascii')
            updates[node_id] = (payload, self.manifest('update', payload, node_id))
        for role in ('server', 'client1', 'client2'):
            self.write(role, 'ROUND_ID', ROUND + '\n')
            address = '10.80.0.1/24' if role == 'server' else '10.80.0.%s/24' % (11 if role == 'client1' else 12)
            config = {'schema_version': 1, 'role': 'server' if role == 'server' else 'client',
                      'uftp_port': 1044,
                      'link_args': ['--tun-name', 'wfb-fl0', '--tun-addr', address]}
            if role == 'server':
                config.update({'http_port': 8080, 'uftp_multicast_address': '230.4.4.1'})
            else:
                config['server_uftp_multicast_address'] = '230.4.4.1'
            self.write_json(role, 'role-config.json', config)
            self.write(role, 'version.txt',
                       'commit=0123456789abcdef0123456789abcdef01234567\n'
                       'describe=44814de\n')
            self.write(role, 'hostname.txt', role + '-host\n')
            installed = ''.join(
                '%s  /usr/bin/%s\n' % (hashlib.sha256(name.encode()).hexdigest(), name)
                for name in ('wfb_v6_uplink', 'wfb-fl-server', 'wfb-fl-client'))
            self.write(role, 'installed-sha256.txt', installed)
            self.write(role, 'wireless-running.txt', 'Interface wlan0\n\ttype monitor\n\tchannel 157 (5785 MHz), width: 40 MHz, center1: 5795 MHz HT40+\n')
            self.write(role, 'addresses-running.txt', 'wfb-fl0 inet %s\n' % address)
            self.write(role, 'sockets-running.txt', 'udp 0 0 0.0.0.0:1044\n' +
                       ('tcp 0 0 10.80.0.1:8080\n' if role == 'server' else ''))
            self.write(role, 'routes-running.txt', '230.4.4.1 dev wfb-fl0 scope link\n')
            self.write(role, 'stopped-lifecycle.txt',
                       'active_state=inactive\nmain_pid=0\norphan_process_count=0\nresult=PASS\n')
            self.write(role, 'restart-lifecycle.txt',
                       'restart_active_state=active\nrestart_main_pid=42\nrestart_cgroup=/system.slice/test\n'
                       'stopped_active_state=inactive\nstopped_main_pid=0\norphan_process_count=0\nresult=PASS\n')
            self.write(role, 'unit-show.txt', 'ActiveState=active\nControlGroup=/system.slice/test\n')
            self.write(role, 'journal.log', server_log if role == 'server' else client_log)
            self.write_json(role, 'queue-summary.json', queue)
            self.write(role, 'round/model.bin', model)
            self.write_json(role, 'round/model.manifest.json', model_manifest)
            self.write_json(role, 'round/round-state.json', {
                'schema_version': 1, 'round_id': ROUND,
                'role': 'server' if role == 'server' else 'client', 'state': 'succeeded',
                **({'participant_node_ids': [1, 2], 'committed_update_node_ids': [1, 2]}
                   if role == 'server' else {})})
        status = []
        for node_id in (1, 2):
            status.append('CONNECT;success;0x%08x' % node_id)
            for name in ('model.bin', 'model.manifest.json'):
                status.append('RESULT;0x%08x;%s/%s;1;copy;0' % (node_id, ROUND, name))
        self.write('server', 'uftp-11111111-1111-1111-1111-111111111111.status', '\n'.join(status) + '\n')
        server_results = []
        for node_id, (payload, manifest) in updates.items():
            self.write('server', 'round/updates/%d/update.bin' % node_id, payload)
            self.write_json('server', 'round/updates/%d/update.manifest.json' % node_id, manifest)
            role = 'client%d' % node_id
            self.write(role, 'round/update.bin', payload)
            self.write_json(role, 'round/update.manifest.json', manifest)
            self.write_json(role, 'round/http-put-result.json', {
                'schema_version': 1,
                'round_id': ROUND,
                'node_id': node_id,
                'connection_count': 1,
                'put_request_count': 1,
                'continue_status': 100,
                'final_status': 201,
                'final_content_length': 0,
            })
            submit_completed = ('2026-07-20T00:00:03Z' if node_id == 1
                                else '2026-07-20T00:00:04Z')
            self.write_json(role, 'algorithm-result.json', {
                'schema_version': 1, 'role': 'client', 'node_id': node_id,
                'status': 'succeeded', 'participant_node_ids': [1, 2],
                'configured_delay_seconds': 0 if node_id == 1 else 2,
                'interface_timings': {
                    'wait_for_model': {
                        'started_at': '2026-07-20T00:00:00Z',
                        'completed_at': '2026-07-20T00:00:01Z',
                        'elapsed_seconds': 1,
                    },
                    'submit_update': {
                        'started_at': '2026-07-20T00:00:02Z',
                        'completed_at': submit_completed,
                        'elapsed_seconds': 1 if node_id == 1 else 2,
                    },
                },
                'input_sha256': model_manifest['sha256'],
                'output_sha256': manifest['sha256'],
                'update_mapping': [
                    {'node_id': node_id, 'sha256': manifest['sha256']}],
            })
            server_results.append({
                'node_id': node_id,
                'path': '/managed/update-%d.bin' % node_id,
                'sha256': manifest['sha256'],
            })
        self.write_json('server', 'algorithm-result.json', {
            'schema_version': 1, 'role': 'server', 'status': 'succeeded',
            'participant_node_ids': [1, 2],
            'interface_timings': {
                'publish_model': {
                    'started_at': '2026-07-20T00:00:00Z',
                    'completed_at': '2026-07-20T00:00:02Z',
                    'elapsed_seconds': 2,
                },
                'wait_for_updates': {
                    'started_at': '2026-07-20T00:00:02Z',
                    'completed_at': '2026-07-20T00:00:05Z',
                    'elapsed_seconds': 3,
                },
            },
            'input_sha256': model_manifest['sha256'],
            'output_sha256_by_node': {
                str(item['node_id']): item['sha256'] for item in server_results},
            'update_mapping': server_results,
        })
        self.refresh_checksums()

    def refresh_checksums(self):
        for role in ('server', 'client1', 'client2'):
            directory = os.path.join(self.root, role)
            lines = []
            for current, _, files in os.walk(directory):
                for name in sorted(files):
                    path = os.path.join(current, name)
                    relative = os.path.relpath(path, directory)
                    if relative == 'SHA256SUMS':
                        continue
                    with open(path, 'rb') as fh:
                        checksum = hashlib.sha256(fh.read()).hexdigest()
                    lines.append('%s  %s\n' % (checksum, relative))
            self.write(role, 'SHA256SUMS', ''.join(sorted(lines)))

    def run_validator(self):
        return subprocess.run([sys.executable, VALIDATOR, self.root],
                              text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_complete_fixture_passes(self):
        result = self.run_validator()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        with open(os.path.join(self.root, 'summary', 'formal_summary.json'), encoding='utf-8') as fh:
            summary = json.load(fh)
        self.assertEqual('PASS', summary['status'])

    def test_baseline_not_pass_does_not_false_positive(self):
        self.write(
            'baseline_uplink', 'result.md',
            '# Baseline\n\n- 总结论: **NOT PASS**\n')
        result = self.run_validator()
        self.assertNotEqual(0, result.returncode)
        with open(os.path.join(
                self.root, 'summary', 'formal_summary.json'),
                encoding='utf-8') as fh:
            summary = json.load(fh)
        self.assertEqual('FAIL', summary['sections']['baseline_uplink'])

    def test_different_host_commit_fails_closed(self):
        self.write(
            'client2', 'version.txt',
            'commit=abcdef0123456789abcdef0123456789abcdef01\n'
            'describe=other\n')
        self.refresh_checksums()
        result = self.run_validator()
        self.assertNotEqual(0, result.returncode)
        with open(os.path.join(
                self.root, 'summary', 'formal_summary.json'),
                encoding='utf-8') as fh:
            summary = json.load(fh)
        self.assertEqual('FAIL', summary['sections']['runtime_loop'])

    def test_missing_feedback_evidence_fails_closed(self):
        path = os.path.join(
            self.root, 'server', 'downlink-formal-2a-summary.json')
        with open(path, encoding='utf-8') as fh:
            summary = json.load(fh)
        summary['feedback_window_close_count'] = 0
        self.write_json('server', 'downlink-formal-2a-summary.json', summary)
        self.refresh_checksums()
        result = self.run_validator()
        self.assertNotEqual(0, result.returncode)
        with open(os.path.join(self.root, 'summary', 'formal_summary.json'), encoding='utf-8') as fh:
            summary = json.load(fh)
        self.assertEqual('FAIL', summary['status'])
        self.assertEqual('FAIL', summary['sections']['downlink_feedback'])


if __name__ == '__main__':
    unittest.main()
