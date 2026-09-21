#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout

from tests.real_hardware.issue41_build_summary import EVENT_PREFIX, main


class Issue41BuildSummaryTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='wfb-issue41-summary-test-')
        self.addCleanup(shutil.rmtree, self.root, True)
        self.archive_dir = os.path.join(self.root, 'archive')
        os.makedirs(os.path.join(self.archive_dir, 'formal_runtime_loop', 'server'), exist_ok=True)
        os.makedirs(os.path.join(self.archive_dir, 'formal_runtime_loop', 'client1'), exist_ok=True)
        os.makedirs(os.path.join(self.archive_dir, 'formal_runtime_loop', 'client2'), exist_ok=True)
        os.makedirs(os.path.join(self.archive_dir, 'raw'), exist_ok=True)

    def test_build_summary_passed(self):
        self.write_fixtures()
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main([self.archive_dir])
        self.assertEqual(0, ret)
        summary = json.loads(buf.getvalue())
        self.assertEqual('passed', summary['status'])
        self.assertEqual('一轮 4 MiB 严格同步确定性场景证据完整', summary['reason'])
        self.assertEqual(1, summary['scenario']['round_count'])
        self.assertEqual(4 * 1024 * 1024, summary['scenario']['artifact_size_bytes'])
        self.assertEqual({'1': 0, '2': 3000}, summary['scenario']['training_delay_ms_by_node'])
        self.assertEqual([1, 2], summary['server_wait_for_updates_returned_node_ids'])
        self.assertFalse(summary['partial_result_returned'])

        round1 = summary['rounds'][0]
        self.assertEqual([1, 2], round1['strict_sync']['server_wait_returned_node_ids'])
        self.assertTrue(round1['strict_sync']['client1_committed_before_client2'])
        self.assertTrue(round1['strict_sync']['server_waited_after_client1'])
        self.assertFalse(round1['strict_sync']['partial_result_returned'])

    def test_build_summary_rejects_two_rounds(self):
        self.write_fixtures(rounds_count=2)
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main([self.archive_dir])
        self.assertEqual(0, ret)
        summary = json.loads(buf.getvalue())
        self.assertEqual('failed', summary['status'])
        self.assertIn('server 算法结果必须恰好包含一轮', summary['reason'])

    def test_build_summary_rejects_client2_delay_zero(self):
        self.write_fixtures(client2_delay=0)
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main([self.archive_dir])
        self.assertEqual(0, ret)
        summary = json.loads(buf.getvalue())
        self.assertEqual('failed', summary['status'])
        self.assertIn('client2 第 1 轮训练延时不是 3000 ms', summary['reason'])

    def test_build_summary_rejects_partial_result(self):
        self.write_fixtures(returned_node_ids=[1])
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main([self.archive_dir])
        self.assertEqual(0, ret)
        summary = json.loads(buf.getvalue())
        self.assertEqual('failed', summary['status'])
        self.assertTrue(summary['partial_result_returned'])

    def test_build_summary_rejects_identical_template_hashes(self):
        self.write_fixtures(same_template_hash=True)
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main([self.archive_dir])
        self.assertEqual(0, ret)
        summary = json.loads(buf.getvalue())
        self.assertEqual('failed', summary['status'])
        self.assertIn('两个 client update 模板 SHA-256 相同', summary['reason'])

    def test_build_summary_downlink_matrix_and_telemetry_included(self):
        self.write_fixtures()
        # Also write an explicit uftp status file in server rounds dir
        r_dir = os.path.join(self.archive_dir, 'formal_runtime_loop', 'server', 'rounds', 'round-1')
        os.makedirs(r_dir, exist_ok=True)
        with open(os.path.join(r_dir, 'uftp-1.status'), 'w', encoding='utf-8') as fh:
            fh.write('CONNECT;success;0x00000001\n')
            fh.write('CONNECT;success;0x00000002\n')
            fh.write('RESULT;0x00000001;model.bin;4194304;copy\n')
            fh.write('RESULT;0x00000001;model.manifest.json;120;copy\n')
            fh.write('RESULT;0x00000002;model.bin;4194304;copy\n')
            fh.write('RESULT;0x00000002;model.manifest.json;120;copy\n')

        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main([self.archive_dir])
        self.assertEqual(0, ret)
        summary = json.loads(buf.getvalue())
        self.assertEqual('passed', summary['status'])
        round1 = summary['rounds'][0]
        self.assertIn('downlink_matrix', round1)
        self.assertEqual('passed', round1['downlink_matrix']['status'])
        self.assertEqual('success', round1['downlink_matrix']['uftp_connect_matrix']['1'])
        self.assertEqual('success', round1['downlink_matrix']['uftp_connect_matrix']['2'])
        self.assertEqual('copy', round1['downlink_matrix']['uftp_result_matrix']['1']['model.bin'])
        self.assertIn('telemetry', round1)
        self.assertIn('ready_accepted_total', round1['telemetry'])
        self.assertIn('queue', round1['telemetry'])
        self.assertIn('config_equivalence', summary)
        self.assertEqual('passed', summary['config_equivalence']['status'])
        self.assertIn('controlled_stop', summary)
        self.assertEqual('passed', summary['controlled_stop']['status'])

    def test_build_summary_detects_unrecovered_queue_pause(self):
        self.write_fixtures()
        # Write server queue summary indicating pause not recovered
        q_path = os.path.join(self.archive_dir, 'formal_runtime_loop', 'server', 'server_queue_summary.json')
        with open(q_path, 'w', encoding='utf-8') as fh:
            json.dump({
                'tun_read_pause_total': 3,
                'tun_read_resume_total': 2,
                'currently_paused': True,
                'pause_recovered': False,
                'tun_read_pause_total_by_reason': {'queued_bytes_threshold': 3, 'queued_packets_limit': 0},
                'queued_bytes_max': 200000,
                'queued_packets_max': 50,
            }, fh)
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main([self.archive_dir])
        self.assertEqual(0, ret)
        summary = json.loads(buf.getvalue())
        self.assertEqual('failed', summary['status'])
        self.assertIn('Runtime 队列自然暂停后未成功恢复', summary['reason'])

    def test_build_summary_detects_config_equivalence_failure(self):
        self.write_fixtures()
        eq_path = os.path.join(self.archive_dir, 'formal_runtime_loop', 'config_equivalence.json')
        with open(eq_path, 'w', encoding='utf-8') as fh:
            json.dump({
                'status': 'failed',
                'errors': ['角色 client1 配置项 radio_mcs_index 不等价: gate=3, runtime=2'],
            }, fh)
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main([self.archive_dir])
        self.assertEqual(0, ret)
        summary = json.loads(buf.getvalue())
        self.assertEqual('failed', summary['status'])
        self.assertIn('链路配置等价性失败', summary['reason'])

    def test_build_summary_detects_controlled_stop_failure(self):
        self.write_fixtures()
        cs_path = os.path.join(self.archive_dir, 'formal_runtime_loop', 'controlled_stop.json')
        with open(cs_path, 'w', encoding='utf-8') as fh:
            json.dump({
                'status': 'failed',
                'server_stopped': False,
            }, fh)
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main([self.archive_dir])
        self.assertEqual(0, ret)
        summary = json.loads(buf.getvalue())
        self.assertEqual('failed', summary['status'])
        self.assertIn('角色服务受控停止未通过', summary['reason'])

    def test_build_summary_detects_missing_uftp_status(self):
        self.write_fixtures()
        r_dir = os.path.join(self.archive_dir, 'formal_runtime_loop', 'server', 'rounds', 'round-1')
        for f in os.listdir(r_dir):
            if f.endswith('.status'):
                os.remove(os.path.join(r_dir, f))
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main([self.archive_dir])
        self.assertEqual(0, ret)
        summary = json.loads(buf.getvalue())
        self.assertEqual('failed', summary['status'])
        self.assertIn('UFTP', summary['reason'])

    def test_build_summary_passed_two_rounds_40mib_concurrent(self):
        env_path = os.path.join(self.archive_dir, 'envelope.json')
        with open(env_path, 'w', encoding='utf-8') as fh:
            json.dump({
                'schema_version': 1,
                'run_id': 'stage1_two_round_40m_test',
                'resolved_config': {
                    'rounds': 2,
                    'artifact_size_bytes': 40 * 1024 * 1024,
                    'training_delay_ms_by_node': {'1': 0, '2': 0},
                    'runtime_timeout_seconds': 400,
                    'io_timeout_seconds': 120,
                }
            }, fh)
        self.write_fixtures(rounds_count=2, client2_delay=0, size=40 * 1024 * 1024, concurrent_obs=True)
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main([self.archive_dir])
        self.assertEqual(0, ret)
        summary = json.loads(buf.getvalue())
        self.assertEqual('passed', summary['status'])
        self.assertEqual('2 轮 40 MiB 严格同步场景证据完整', summary['reason'])
        self.assertEqual(2, summary['scenario']['round_count'])
        self.assertEqual(40 * 1024 * 1024, summary['scenario']['artifact_size_bytes'])
        self.assertEqual({'1': 0, '2': 0}, summary['scenario']['training_delay_ms_by_node'])
        self.assertEqual(2, len(summary['rounds']))
        for r in summary['rounds']:
            self.assertEqual([1, 2], r['strict_sync']['server_wait_returned_node_ids'])
            self.assertFalse(r['strict_sync']['partial_result_returned'])
            self.assertTrue(r['concurrent_put']['natural_overlap'])
            self.assertTrue(r['concurrent_put']['concurrent_active_observed'])
            self.assertGreater(r['concurrent_put']['overlap_duration_seconds'], 0.0)

    def test_build_summary_rejects_duplicate_round_id(self):
        env_path = os.path.join(self.archive_dir, 'envelope.json')
        with open(env_path, 'w', encoding='utf-8') as fh:
            json.dump({
                'schema_version': 1,
                'run_id': 'stage1_dup_round_test',
                'resolved_config': {
                    'rounds': 2,
                    'artifact_size_bytes': 40 * 1024 * 1024,
                    'training_delay_ms_by_node': {'1': 0, '2': 0},
                }
            }, fh)
        self.write_fixtures(rounds_count=2, client2_delay=0, size=40 * 1024 * 1024, duplicate_round_id=True)
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main([self.archive_dir])
        self.assertEqual(0, ret)
        summary = json.loads(buf.getvalue())
        self.assertEqual('failed', summary['status'])
        self.assertIn('round_id 重复', summary['reason'])

    def test_build_summary_rejects_client_mismatched_template_in_round_2(self):
        env_path = os.path.join(self.archive_dir, 'envelope.json')
        with open(env_path, 'w', encoding='utf-8') as fh:
            json.dump({
                'schema_version': 1,
                'run_id': 'stage1_mismatch_tpl_test',
                'resolved_config': {
                    'rounds': 2,
                    'artifact_size_bytes': 40 * 1024 * 1024,
                    'training_delay_ms_by_node': {'1': 0, '2': 0},
                }
            }, fh)
        self.write_fixtures(rounds_count=2, client2_delay=0, size=40 * 1024 * 1024, client_mismatched_template=True)
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main([self.archive_dir])
        self.assertEqual(0, ret)
        summary = json.loads(buf.getvalue())
        self.assertEqual('failed', summary['status'])
        self.assertIn('未复用对应 update 模板', summary['reason'])

    def test_build_summary_rejects_upload_in_progress(self):
        self.write_fixtures()
        # Append upload_in_progress event to server observations
        obs_path = os.path.join(self.archive_dir, 'formal_runtime_loop', 'server', 'observation.jsonl')
        with open(obs_path, 'a', encoding='utf-8') as fh:
            fh.write(EVENT_PREFIX + json.dumps({'event': 'upload_phase', 'round': 'round-1', 'node_id': 1, 'error_code': 'upload_in_progress'}) + '\n')
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main([self.archive_dir])
        self.assertEqual(0, ret)
        summary = json.loads(buf.getvalue())
        self.assertEqual('failed', summary['status'])
        self.assertIn('upload_in_progress', summary['reason'])

    def write_fixtures(self, rounds_count=1, client2_delay=3000,
                       returned_node_ids=None, same_template_hash=False,
                       size=4 * 1024 * 1024, concurrent_obs=False,
                       duplicate_round_id=False, client_mismatched_template=False):
        model_sha = 'a' * 64
        c1_sha = '1' * 64
        c2_sha = '1' * 64 if same_template_hash else '2' * 64
        if returned_node_ids is None:
            returned_node_ids = [1, 2]

        server_rounds = []
        c1_rounds = []
        c2_rounds = []
        server_obs = []
        c1_obs = []
        c2_obs = []

        for r in range(1, rounds_count + 1):
            r_id = 'round-1' if (duplicate_round_id and r > 1) else f'round-{r}'
            r_dir = os.path.join(self.archive_dir, 'formal_runtime_loop', 'server', 'rounds', r_id)
            os.makedirs(r_dir, exist_ok=True)
            with open(os.path.join(r_dir, f'uftp-{r}.status'), 'w', encoding='utf-8') as fh:
                fh.write('CONNECT;success;0x00000001\n')
                fh.write('CONNECT;success;0x00000002\n')
                fh.write(f'RESULT;0x00000001;{r_id}/model.bin;{size};copy\n')
                fh.write(f'RESULT;0x00000001;{r_id}/model.manifest.json;120;copy\n')
                fh.write(f'RESULT;0x00000002;{r_id}/model.bin;{size};copy\n')
                fh.write(f'RESULT;0x00000002;{r_id}/model.manifest.json;120;copy\n')
            updates = []
            for nid in returned_node_ids:
                u_sha = c1_sha if nid == 1 else c2_sha
                if client_mismatched_template and r > 1 and nid == 1:
                    u_sha = '9' * 64
                updates.append({
                    'node_id': nid,
                    'path': f'/var/lib/wfb-ng/issue41/server/updates/{r}/{nid}/update.bin',
                    'size_bytes': size,
                    'sha256': u_sha,
                })
            server_rounds.append({
                'round_index': r,
                'round_id': r_id,
                'input_model_path': f'/var/lib/wfb-ng/issue41/server/model-{r}.bin',
                'input_model_size_bytes': size,
                'input_model_sha256': model_sha,
                'update_node_ids': returned_node_ids,
                'updates': updates,
                'aggregation_delay_ms': 0,
                'output_model_path': f'/var/lib/wfb-ng/issue41/server/global-{r}.bin',
                'output_model_size_bytes': size,
                'output_model_sha256': model_sha,
            })

            c1_round_sha = ('9' * 64) if (client_mismatched_template and r > 1) else c1_sha
            c1_rounds.append({
                'round_index': r,
                'round_id': r_id,
                'node_id': 1,
                'model_path': f'/var/lib/wfb-ng/issue41/client/rounds/{r_id}/model.bin',
                'model_size_bytes': size,
                'model_sha256': model_sha,
                'model_receive_interval': {'start': 1.0 * r, 'end': 1.0 * r + 0.5},
                'update_path': '/var/lib/wfb-ng/issue41/client/update.bin',
                'update_size_bytes': size,
                'update_sha256': c1_round_sha,
                'put_interval': {'start': 1.0 * r + 0.6, 'end': 1.0 * r + 2.0},
                'training_delay_ms': 0,
            })

            c2_rounds.append({
                'round_index': r,
                'round_id': r_id,
                'node_id': 2,
                'model_path': f'/var/lib/wfb-ng/issue41/client/rounds/{r_id}/model.bin',
                'model_size_bytes': size,
                'model_sha256': model_sha,
                'model_receive_interval': {'start': 1.0 * r, 'end': 1.0 * r + 0.5},
                'update_path': '/var/lib/wfb-ng/issue41/client/update.bin',
                'update_size_bytes': size,
                'update_sha256': c2_sha,
                'put_interval': {'start': 1.0 * r + 0.8 if concurrent_obs else 1.0 * r + 4.6,
                                 'end': 1.0 * r + 2.5 if concurrent_obs else 1.0 * r + 5.0},
                'training_delay_ms': client2_delay,
            })

            if concurrent_obs:
                server_obs.extend([
                    {'event': 'upload_accepted', 'round': r_id, 'node_id': 1, 'transport_outcome': 'accepted'},
                    {'event': 'active_uploads', 'round': r_id, 'node_id': 1, 'active_node_ids': [1]},
                    {'event': 'upload_accepted', 'round': r_id, 'node_id': 2, 'transport_outcome': 'accepted'},
                    {'event': 'active_uploads', 'round': r_id, 'node_id': 2, 'active_node_ids': [1, 2]},
                    {'event': 'upload_committed', 'round': r_id, 'node_id': 1, 'transport_outcome': 'committed'},
                    {'event': 'active_uploads', 'round': r_id, 'node_id': 1, 'active_node_ids': [2]},
                    {'event': 'upload_committed', 'round': r_id, 'node_id': 2, 'transport_outcome': 'committed'},
                    {'event': 'active_uploads', 'round': r_id, 'node_id': 2, 'active_node_ids': []},
                ])
            else:
                server_obs.extend([
                    {'event': 'upload_accepted', 'round': r_id, 'node_id': 1, 'transport_outcome': 'accepted'},
                    {'event': 'active_uploads', 'round': r_id, 'node_id': 1, 'active_node_ids': [1]},
                    {'event': 'upload_committed', 'round': r_id, 'node_id': 1, 'transport_outcome': 'committed'},
                    {'event': 'active_uploads', 'round': r_id, 'node_id': 1, 'active_node_ids': []},
                    {'event': 'upload_accepted', 'round': r_id, 'node_id': 2, 'transport_outcome': 'accepted'},
                    {'event': 'active_uploads', 'round': r_id, 'node_id': 2, 'active_node_ids': [2]},
                    {'event': 'upload_committed', 'round': r_id, 'node_id': 2, 'transport_outcome': 'committed'},
                    {'event': 'active_uploads', 'round': r_id, 'node_id': 2, 'active_node_ids': []},
                ])

            c1_obs.append({'event': 'upload_phase', 'round': r_id, 'node_id': 1, 'transport_outcome': 'created'})
            c2_obs.append({'event': 'upload_phase', 'round': r_id, 'node_id': 2, 'transport_outcome': 'created'})

        self.write_json(
            os.path.join(self.archive_dir, 'formal_runtime_loop', 'server', 'issue41-server-result.json'),
            {
                'schema_version': 1,
                'role': 'server',
                'rounds': server_rounds,
                'initial_model_size_bytes': size,
                'conclusion': 'succeeded',
            })

        self.write_json(
            os.path.join(self.archive_dir, 'formal_runtime_loop', 'client1', 'issue41-client1-result.json'),
            {
                'schema_version': 1,
                'role': 'client',
                'node_id': 1,
                'update_template_sha256': c1_sha,
                'rounds': c1_rounds,
                'conclusion': 'succeeded',
            })

        self.write_json(
            os.path.join(self.archive_dir, 'formal_runtime_loop', 'client2', 'issue41-client2-result.json'),
            {
                'schema_version': 1,
                'role': 'client',
                'node_id': 2,
                'update_template_sha256': c2_sha,
                'rounds': c2_rounds,
                'conclusion': 'succeeded',
            })

        self.write_obs(
            os.path.join(self.archive_dir, 'formal_runtime_loop', 'server', 'observation.jsonl'),
            server_obs)

        self.write_obs(
            os.path.join(self.archive_dir, 'formal_runtime_loop', 'client1', 'observation.jsonl'),
            c1_obs)

        self.write_obs(
            os.path.join(self.archive_dir, 'formal_runtime_loop', 'client2', 'observation.jsonl'),
            c2_obs)

    def write_json(self, path, data):
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(data, fh)

    def write_obs(self, path, events):
        with open(path, 'w', encoding='utf-8') as fh:
            for ev in events:
                fh.write(EVENT_PREFIX + json.dumps(ev) + '\n')


if __name__ == '__main__':
    unittest.main()
