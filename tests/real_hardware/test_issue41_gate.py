#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import hashlib
import json
import os
import shutil
import tempfile
import unittest

from tests.real_hardware.issue41_gate import (
    GateConfig,
    build_cycle_evidence,
    build_gate_summary,
    classify_gate_failure,
    format_node_telemetry_lines,
    make_empty_node_telemetry,
    parse_pkt_src_line,
    parse_telemetry,
    validate_cycle_evidence,
    validate_gate_summary,
)


class Issue41GateTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix='wfb-issue41-gate-test-')
        self.addCleanup(shutil.rmtree, self.tmp_dir, True)
        self.config = GateConfig()

    def test_config_defaults_match_spec(self):
        self.assertEqual(3, self.config.cycle_count)
        self.assertEqual(4 * 1024 * 1024, self.config.artifact_size_bytes)
        self.assertEqual(120, self.config.io_timeout_seconds)
        self.assertEqual(240, self.config.cycle_deadline_seconds)
        self.assertEqual(157, self.config.channel)
        self.assertEqual('HT40+', self.config.channel_width)
        self.assertEqual(3, self.config.radio_mcs_index)
        self.assertEqual(8, self.config.fec_k)
        self.assertEqual(12, self.config.fec_n)

    def test_config_rejects_immediate_feedback_candidate(self):
        bad_args = ['--role', 'server', '--feedback-window-start-immediately']
        with self.assertRaises(ValueError) as ctx:
            self.config.validate_link_args(bad_args)
        self.assertIn('feedback-window-start-immediately', str(ctx.exception))

        good_args = ['--role', 'server', '--feedback-window-period-ms', '500', '--feedback-window-duration-ms', '15']
        self.config.validate_link_args(good_args)

    def test_parse_telemetry_extracts_all_required_metrics(self):
        server_log = (
            "1000\tRX_ANT\t5785:3:40\t1\t100:-60:-60:-60:25:25:25\n"
            "1000\tPKT\t100:10000:0:10:90:50:5:2:0:90:9000\n"
            "1000\tPKT_SRC\t1:50:5000:3:1:45:4500\n"
            "1000\tPKT_SRC\t2:50:5000:2:1:45:4500\n"
            "1000\tGRANT_FILTER\t50:50:0:0:0:0:0\n"
            "1000\tREADY_FILTER\t20:20:0:0:0\n"
            "1000\tREASSEMBLY\t0:40\n"
            "ready_accept node_id=1\n"
            "ready_accept node_id=2\n"
            "grant seq=1\n"
            "grant seq=2\n"
            "feedback_window_open seq=1\n"
            "feedback_window_close seq=1\n"
            "feedback_uplink_hit node_id=1 sequence=1 total=5\n"
            "feedback_uplink_hit node_id=2 sequence=1 total=6\n"
        )
        client1_log = (
            "first_declare node_id=1\n"
            "1000\tTOKEN_AUTH\t1:100:0:50:0\n"
        )
        client2_log = (
            "first_declare node_id=2\n"
            "1000\tTOKEN_AUTH\t2:100:0:50:0\n"
        )
        queue_summaries = {
            'server': {
                'tun_read_pause_total': 1,
                'tun_read_resume_total': 1,
                'currently_paused': False,
                'queued_bytes_max': 65536,
                'queued_packets_max': 32,
                'tun_read_pause_total_by_reason': {
                    'queued_bytes_threshold': 1,
                    'queued_packets_limit': 0,
                },
            },
            'client1': {
                'tun_read_pause_total': 0,
                'tun_read_resume_total': 0,
                'currently_paused': False,
                'queued_bytes_max': 1024,
                'queued_packets_max': 2,
                'tun_read_pause_total_by_reason': {
                    'queued_bytes_threshold': 0,
                    'queued_packets_limit': 0,
                },
            },
            'client2': {
                'tun_read_pause_total': 0,
                'tun_read_resume_total': 0,
                'currently_paused': False,
                'queued_bytes_max': 1024,
                'queued_packets_max': 2,
                'tun_read_pause_total_by_reason': {
                    'queued_bytes_threshold': 0,
                    'queued_packets_limit': 0,
                },
            },
        }
        tcp_stats = {
            'retransmits': 1,
        }

        telemetry = parse_telemetry(
            server_log=server_log,
            client_logs={'client1': client1_log, 'client2': client2_log},
            queue_summaries=queue_summaries,
            tcp_stats=tcp_stats,
            phase_durations={'downlink_seconds': 3.2, 'uplink_seconds': 7.5, 'cycle_total_seconds': 10.7},
        )

        self.assertEqual(20, telemetry['ready_accepted_total'])
        self.assertEqual(0, telemetry['ready_rejected_total'])
        self.assertEqual(2, telemetry['grant_sent_total'])
        self.assertEqual(50, telemetry['authorized_sends_by_node']['1'])
        self.assertEqual(50, telemetry['authorized_sends_by_node']['2'])
        self.assertEqual(1, telemetry['server_rx']['rx_ant_samples'])
        self.assertEqual(100, telemetry['server_rx']['rx_packets'])
        self.assertEqual(10000, telemetry['server_rx']['rx_bytes'])
        self.assertEqual(1, telemetry['queue']['tun_read_pause_total'])
        self.assertEqual(1, telemetry['queue']['tun_read_resume_total'])
        self.assertFalse(telemetry['queue']['currently_paused'])
        self.assertTrue(telemetry['queue']['pause_recovered'])
        self.assertEqual(0, telemetry['reassembly']['reassembly_overflow_evict'])
        self.assertEqual(40, telemetry['reassembly']['unfinished_block_limit'])
        self.assertEqual(1, telemetry['feedback']['feedback_window_open_count'])
        self.assertEqual(1, telemetry['feedback']['feedback_window_close_count'])
        self.assertEqual(11, telemetry['feedback']['feedback_uplink_hit_total'])
        self.assertEqual(2, telemetry['loss_and_fec']['packets_lost'])
        self.assertEqual(5, telemetry['loss_and_fec']['packets_fec_recovered'])
        self.assertEqual(1, telemetry['loss_and_fec_by_node']['1']['packets_lost'])
        self.assertEqual(3, telemetry['loss_and_fec_by_node']['1']['packets_fec_recovered'])
        self.assertEqual(1, telemetry['loss_and_fec_by_node']['2']['packets_lost'])
        self.assertEqual(2, telemetry['loss_and_fec_by_node']['2']['packets_fec_recovered'])
        self.assertEqual(1, telemetry['tcp_retransmits'])
        self.assertEqual(10.7, telemetry['phase_durations']['cycle_total_seconds'])

    def test_parse_pkt_src_line_valid_and_malformed(self):
        # 合法行
        line = "1000\tPKT_SRC\t1:50:5000:3:1:49:4900\n"
        res = parse_pkt_src_line(line)
        self.assertIsNotNone(res)
        self.assertEqual("1", res['node_id'])
        self.assertEqual(50, res['rx_packets'])
        self.assertEqual(5000, res['rx_bytes'])
        self.assertEqual(3, res['packets_fec_recovered'])
        self.assertEqual(1, res['packets_lost'])
        self.assertEqual(49, res['out_packets'])
        self.assertEqual(4900, res['out_bytes'])

        # 缺少 PKT_SRC 标签
        self.assertIsNone(parse_pkt_src_line("1000\tPKT\t100:10000:0:10:90:50:5:2:0:90:9000\n"))
        # 字段数不足
        self.assertIsNone(parse_pkt_src_line("1000\tPKT_SRC\t1:50:5000:3:1:49\n"))
        # 包含非数字字段
        self.assertIsNone(parse_pkt_src_line("1000\tPKT_SRC\t1:50:bad:3:1:49:4900\n"))
        # 空行与随机文本
        self.assertIsNone(parse_pkt_src_line(""))
        self.assertIsNone(parse_pkt_src_line("corrupted line without tab"))
        # 行尾追加多余字段或文本（行锚定保护）
        self.assertIsNone(parse_pkt_src_line("1000\tPKT_SRC\t1:50:5000:3:1:49:4900:extra\n"))
        self.assertIsNone(parse_pkt_src_line("1000\tPKT_SRC\t1:50:5000:3:1:49:4900 garbage\n"))
        # 节点编号越界（必须在 1..255 范围内）
        self.assertIsNone(parse_pkt_src_line("1000\tPKT_SRC\t0:50:5000:3:1:49:4900\n"))
        self.assertIsNone(parse_pkt_src_line("1000\tPKT_SRC\t256:50:5000:3:1:49:4900\n"))

    def test_format_node_telemetry_lines(self):
        by_node = {
            '1': {
                'rx_packets': 100,
                'rx_bytes': 10000,
                'packets_fec_recovered': 5,
                'packets_lost': 2,
                'out_packets': 98,
                'out_bytes': 9800,
                'loss_rate': 0.02,
                'fec_recovery_rate': 0.05,
            }
        }
        lines = format_node_telemetry_lines(by_node, label_prefix="[前缀] ")
        self.assertEqual(1, len(lines))
        self.assertIn("Client1", lines[0])
        self.assertIn("lost=2 (2.00%)", lines[0])
        self.assertIn("fec_recovered=5 (5.00%)", lines[0])

    def test_parse_telemetry_extracts_per_node_loss_and_fec(self):
        server_log = (
            "1000\tPKT\t150:15000:0:10:140:100:3:1:0:140:14000\n"
            "1000\tPKT_SRC\t1:100:10000:2:1:99:9900\n"
            "1000\tPKT_SRC\t2:50:5000:1:0:50:5000\n"
            "1000\tPKT_SRC\tbad_format_line\n"
            "1000\tPKT_SRC\t1:incomplete:field\n"
            "2000\tPKT_SRC\t1:100:10000:3:0:100:10000\n"
            "2000\tPKT_SRC\t2:50:5000:0:1:49:4900\n"
        )
        telemetry = parse_telemetry(
            server_log=server_log,
            client_logs={},
            queue_summaries={},
        )
        by_node = telemetry.get('loss_and_fec_by_node', {})
        self.assertIn('1', by_node)
        self.assertIn('2', by_node)

        n1 = by_node['1']
        self.assertEqual(200, n1['rx_packets'])
        self.assertEqual(20000, n1['rx_bytes'])
        self.assertEqual(5, n1['packets_fec_recovered'])
        self.assertEqual(1, n1['packets_lost'])
        self.assertEqual(199, n1['out_packets'])
        self.assertEqual(19900, n1['out_bytes'])
        self.assertAlmostEqual(0.005, n1['loss_rate'], places=5)
        self.assertAlmostEqual(0.025, n1['fec_recovery_rate'], places=5)

        n2 = by_node['2']
        self.assertEqual(100, n2['rx_packets'])
        self.assertEqual(10000, n2['rx_bytes'])
        self.assertEqual(1, n2['packets_fec_recovered'])
        self.assertEqual(1, n2['packets_lost'])
        self.assertEqual(99, n2['out_packets'])
        self.assertEqual(9900, n2['out_bytes'])
        self.assertAlmostEqual(0.01, n2['loss_rate'], places=5)
        self.assertAlmostEqual(0.01, n2['fec_recovery_rate'], places=5)

    def test_queue_pause_without_resume_fails_validation(self):
        cycle = self._make_valid_cycle(1)
        cycle['telemetry']['queue']['tun_read_pause_total'] = 2
        cycle['telemetry']['queue']['tun_read_resume_total'] = 1
        cycle['telemetry']['queue']['currently_paused'] = True
        cycle['telemetry']['queue']['pause_recovered'] = False

        errors = validate_cycle_evidence(cycle, self.config)
        self.assertTrue(any('queue pause' in e and '恢复' in e for e in errors))

    def test_cycle_rejects_mismatched_model_sha(self):
        cycle = self._make_valid_cycle(1)
        cycle['downlink']['client_received_verification']['2']['model_sha256'] = '0' * 64
        errors = validate_cycle_evidence(cycle, self.config)
        self.assertTrue(any('model SHA-256' in e for e in errors))

    def test_cycle_rejects_missing_uftp_connect_or_result(self):
        cycle = self._make_valid_cycle(1)
        cycle['downlink']['uftp_connect_matrix']['2'] = 'failed'
        errors = validate_cycle_evidence(cycle, self.config)
        self.assertTrue(any('CONNECT' in e for e in errors))

        cycle = self._make_valid_cycle(1)
        cycle['downlink']['uftp_result_matrix']['1']['model.bin'] = 'aborted'
        errors = validate_cycle_evidence(cycle, self.config)
        self.assertTrue(any('RESULT' in e for e in errors))

    def test_cycle_rejects_mismatched_client_and_server_http_status(self):
        cycle = self._make_valid_cycle(1)
        cycle['uplink']['uploads'][0]['server_http_status'] = 500
        errors = validate_cycle_evidence(cycle, self.config)
        self.assertTrue(any('HTTP' in e for e in errors))

    def test_cycle_rejects_unconverged_active_uploads(self):
        cycle = self._make_valid_cycle(1)
        cycle['uplink']['active_upload_sets'] = [[1], [1, 2], [2]]
        cycle['uplink']['active_uploads_converged'] = False
        errors = validate_cycle_evidence(cycle, self.config)
        self.assertTrue(any('active_upload_sets' in e or '收敛' in e for e in errors))

    def test_cycle_rejects_same_sha256_for_different_clients(self):
        cycle = self._make_valid_cycle(1)
        cycle['uplink']['uploads'][1]['sha256'] = cycle['uplink']['uploads'][0]['sha256']
        errors = validate_cycle_evidence(cycle, self.config)
        self.assertTrue(any('update SHA-256' in e and '不同' in e for e in errors))

    def test_cycle_rejects_cycle_deadline_exceeded(self):
        cycle = self._make_valid_cycle(1)
        cycle['total_duration_seconds'] = 241.0
        errors = validate_cycle_evidence(cycle, self.config)
        self.assertTrue(any('deadline' in e for e in errors))

    def test_cycle_rejects_missing_or_empty_node_telemetry_samples(self):
        # 缺少 Client1 样本
        cycle = self._make_valid_cycle(1)
        cycle['telemetry']['loss_and_fec_by_node']['1']['sample_count'] = 0
        errors = validate_cycle_evidence(cycle, self.config)
        self.assertTrue(any('client 1 缺少有效 PKT_SRC 遥测采样' in e for e in errors))

        # 缺少 Client2 键
        cycle = self._make_valid_cycle(1)
        del cycle['telemetry']['loss_and_fec_by_node']['2']
        errors = validate_cycle_evidence(cycle, self.config)
        self.assertTrue(any('缺少 client 2' in e for e in errors))

    def test_gate_summary_validates_three_cycles_and_process_reuse(self):
        cycles = [self._make_valid_cycle(i) for i in (1, 2, 3)]
        reused_pids = {
            'server_link_pid': 1001,
            'client1_link_pid': 1002,
            'client2_link_pid': 1003,
            'client1_uftpd_pid': 1004,
            'client2_uftpd_pid': 1005,
            'server_http_pid': 1006,
        }
        gate_summary = build_gate_summary(
            run_id='test-run-1',
            status='passed',
            cycles=cycles,
            reused_processes=reused_pids,
            config=self.config,
        )

        errors = validate_gate_summary(gate_summary, self.config)
        self.assertEqual([], errors)

    def test_gate_summary_rejects_less_than_three_cycles(self):
        cycles = [self._make_valid_cycle(1), self._make_valid_cycle(2)]
        reused_pids = {
            'server_link_pid': 1001,
            'client1_link_pid': 1002,
            'client2_link_pid': 1003,
            'client1_uftpd_pid': 1004,
            'client2_uftpd_pid': 1005,
            'server_http_pid': 1006,
        }
        gate_summary = build_gate_summary(
            run_id='test-run-1',
            status='passed',
            cycles=cycles,
            reused_processes=reused_pids,
            config=self.config,
        )

        errors = validate_gate_summary(gate_summary, self.config)
        self.assertTrue(any('恰好包含 3 个周期' in e for e in errors))

    def test_gate_summary_fails_when_any_cycle_fails(self):
        cycles = [self._make_valid_cycle(1), self._make_valid_cycle(2), self._make_valid_cycle(3)]
        cycles[1]['status'] = 'failed'
        reused_pids = {
            'server_link_pid': 1001,
            'client1_link_pid': 1002,
            'client2_link_pid': 1003,
            'client1_uftpd_pid': 1004,
            'client2_uftpd_pid': 1005,
            'server_http_pid': 1006,
        }
        gate_summary = build_gate_summary(
            run_id='test-run-1',
            status='passed',  # conflicting passed status with failed cycle
            cycles=cycles,
            reused_processes=reused_pids,
            config=self.config,
        )

        errors = validate_gate_summary(gate_summary, self.config)
        self.assertTrue(any('周期 2 状态为 failed' in e for e in errors))

    def test_cycle_rejects_missing_telemetry_keys(self):
        cycle = self._make_valid_cycle(1)
        del cycle['telemetry']['sender_isolation']
        errors = validate_cycle_evidence(cycle, self.config)
        self.assertTrue(any('缺少遥测事实：sender_isolation' in e for e in errors))

    def test_cycle_rejects_io_timeout_setting_mismatch(self):
        cycle = self._make_valid_cycle(1)
        cycle['io_timeout_seconds'] = 300  # not 120
        errors = validate_cycle_evidence(cycle, self.config)
        self.assertTrue(any('io_timeout_seconds' in e for e in errors))

    def test_cycle_rejects_incomplete_uftp_result_matrix(self):
        cycle = self._make_valid_cycle(1)
        # missing model.manifest.json in node 2's result
        del cycle['downlink']['uftp_result_matrix']['2']['model.manifest.json']
        errors = validate_cycle_evidence(cycle, self.config)
        self.assertTrue(any('model.manifest.json' in e for e in errors))

    def test_cycle_rejects_temporary_state_not_cleaned(self):
        cycle = self._make_valid_cycle(1)
        cycle['uplink']['temporary_state_cleaned'] = False
        errors = validate_cycle_evidence(cycle, self.config)
        self.assertTrue(any('temporary_state_cleaned' in e for e in errors))

    def test_generate_cycle_fixtures_creates_deterministic_different_4mib_files(self):
        from tests.real_hardware.issue41_gate import generate_cycle_fixtures
        server_dir = os.path.join(self.tmp_dir, 'server_c1')
        c1_dir = os.path.join(self.tmp_dir, 'c1_c1')
        c2_dir = os.path.join(self.tmp_dir, 'c2_c1')
        generate_cycle_fixtures(1, server_dir, c1_dir, c2_dir)

        model_path = os.path.join(server_dir, 'model.bin')
        c1_update = os.path.join(c1_dir, 'update.bin')
        c2_update = os.path.join(c2_dir, 'update.bin')

        self.assertEqual(4 * 1024 * 1024, os.path.getsize(model_path))
        self.assertEqual(4 * 1024 * 1024, os.path.getsize(c1_update))
        self.assertEqual(4 * 1024 * 1024, os.path.getsize(c2_update))

        with open(model_path, 'rb') as fh:
            h_model = hashlib.sha256(fh.read()).hexdigest()
        with open(c1_update, 'rb') as fh:
            h_c1 = hashlib.sha256(fh.read()).hexdigest()
        with open(c2_update, 'rb') as fh:
            h_c2 = hashlib.sha256(fh.read()).hexdigest()

        self.assertNotEqual(h_model, h_c1)
        self.assertNotEqual(h_model, h_c2)
        self.assertNotEqual(h_c1, h_c2)

    def test_cli_generate_fixtures_and_validate(self):
        from tests.real_hardware.issue41_gate import main
        server_dir = os.path.join(self.tmp_dir, 'cli_server')
        c1_dir = os.path.join(self.tmp_dir, 'cli_c1')
        c2_dir = os.path.join(self.tmp_dir, 'cli_c2')

        # 1. generate-fixtures
        rc = main([
            'generate-fixtures',
            '--cycle', '1',
            '--server-dir', server_dir,
            '--client1-dir', c1_dir,
            '--client2-dir', c2_dir,
            '--size', '1024',
        ])
        self.assertEqual(0, rc)
        self.assertTrue(os.path.isfile(os.path.join(server_dir, 'model.bin')))

        # 2. classify-failure
        diag_out = os.path.join(self.tmp_dir, 'diag.json')
        rc = main([
            'classify-failure',
            '--server-rx-ant-samples', '0',
            '--client1-declared', '1',
            '--client2-declared', '1',
            '--client1-accepted', '0',
            '--client2-accepted', '0',
            '--tun-routes-ok', '1',
            '--uftp-ok', '0',
            '--http-ok', '0',
            '--out', diag_out,
        ])
        self.assertEqual(0, rc)
        with open(diag_out, 'r', encoding='utf-8') as fh:
            diag = json.load(fh)
        self.assertEqual('link_capability', diag['category'])

    def test_http_receiver_and_client_put_roundtrip(self):
        import time
        from tests.real_hardware.issue41_gate import run_client_put, start_http_receiver_thread
        server_dir = os.path.join(self.tmp_dir, 'server_http')
        os.makedirs(server_dir, exist_ok=True)
        events_file = os.path.join(server_dir, 'events.jsonl')

        # 启动 receiver 线程（选择随机未占用端口，例如使用 0 自动绑定）
        server, port = start_http_receiver_thread('127.0.0.1', 0, server_dir)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        # 准备一个 1 MiB 测试文件（单测中用 1 MiB 验证链路吞吐与流处理）
        test_file = os.path.join(self.tmp_dir, 'test_put.bin')
        test_data = b'X' * 1024 * 1024
        with open(test_file, 'wb') as fh:
            fh.write(test_data)
        test_sha = hashlib.sha256(test_data).hexdigest()

        result1_file = os.path.join(self.tmp_dir, 'client1_put.json')
        run_client_put(
            node_id=1,
            source_ip='127.0.0.1',
            host='127.0.0.1',
            port=port,
            file_path=test_file,
            result_file=result1_file,
            timeout_seconds=10,
        )

        with open(result1_file, 'r', encoding='utf-8') as fh:
            r1 = json.load(fh)
        self.assertEqual(201, r1['status'])
        self.assertEqual(test_sha, r1['sha256'])

        # 检查 events.jsonl
        events = []
        with open(events_file, 'r', encoding='utf-8') as fh:
            for line in fh:
                events.append(json.loads(line))

        # 必须包含 active_set 和 upload 事件
        upload_events = [e for e in events if e.get('type') == 'upload']
        self.assertEqual(1, len(upload_events))
        self.assertEqual('committed', upload_events[0]['outcome'])
        self.assertEqual(201, upload_events[0]['status'])
        self.assertEqual(test_sha, upload_events[0]['sha256'])

        # active_set 最终必须收敛为 []
        active_sets = [e['active_uploads'] for e in events if e.get('type') == 'active_set']
        self.assertTrue(len(active_sets) >= 2)
        self.assertEqual([], active_sets[-1])

    def test_verify_uplink_cycle_validates_committed_and_convergence(self):
        from tests.real_hardware.issue41_gate import verify_uplink_cycle
        size = 4 * 1024 * 1024
        c1_sha = 'b' * 64
        c2_sha = 'c' * 64

        events = [
            {'type': 'active_set', 'active_uploads': [1]},
            {'type': 'upload', 'node_id': 1, 'client_address': '10.80.0.11', 'size_bytes': size, 'sha256': c1_sha, 'status': 201, 'outcome': 'committed', 'start_time': 10.0, 'end_time': 12.0},
            {'type': 'active_set', 'active_uploads': []},
            {'type': 'active_set', 'active_uploads': [2]},
            {'type': 'upload', 'node_id': 2, 'client_address': '10.80.0.12', 'size_bytes': size, 'sha256': c2_sha, 'status': 201, 'outcome': 'committed', 'start_time': 12.5, 'end_time': 14.5},
            {'type': 'active_set', 'active_uploads': []},
        ]
        client_results = {
            '1': {'node_id': 1, 'status': 201, 'size_bytes': size, 'sha256': c1_sha, 'start_time': 10.0, 'end_time': 12.0},
            '2': {'node_id': 2, 'status': 201, 'size_bytes': size, 'sha256': c2_sha, 'start_time': 12.5, 'end_time': 14.5},
        }

        uplink_res = verify_uplink_cycle(events, client_results, self.config, duration_seconds=5.0)
        self.assertEqual('passed', uplink_res['status'])
        self.assertTrue(uplink_res['active_uploads_converged'])
        self.assertEqual([[1], [], [2], []], uplink_res['active_upload_sets'])
        self.assertEqual(2, len(uplink_res['uploads']))

    def test_verify_downlink_evidence_parses_status_and_files(self):
        cycle_dir = os.path.join(self.tmp_dir, 'cycle1')
        os.makedirs(cycle_dir, exist_ok=True)
        model_path = os.path.join(cycle_dir, 'model.bin')
        manifest_path = os.path.join(cycle_dir, 'model.manifest.json')
        with open(model_path, 'wb') as fh:
            fh.write(b'M' * (4 * 1024 * 1024))
        with open(manifest_path, 'w', encoding='utf-8') as fh:
            json.dump({'schema_version': 1, 'artifact_type': 'model'}, fh)

        c1_dir = os.path.join(self.tmp_dir, 'client1', 'cycle1')
        c2_dir = os.path.join(self.tmp_dir, 'client2', 'cycle1')
        os.makedirs(c1_dir, exist_ok=True)
        os.makedirs(c2_dir, exist_ok=True)
        shutil.copy2(model_path, os.path.join(c1_dir, 'model.bin'))
        shutil.copy2(manifest_path, os.path.join(c1_dir, 'model.manifest.json'))
        shutil.copy2(model_path, os.path.join(c2_dir, 'model.bin'))
        shutil.copy2(manifest_path, os.path.join(c2_dir, 'model.manifest.json'))

        status_path = os.path.join(cycle_dir, 'uftp.status')
        with open(status_path, 'w', encoding='utf-8') as fh:
            fh.write("CONNECT;success;00000001;10.80.0.11\n")
            fh.write("CONNECT;success;00000002;10.80.0.12\n")
            fh.write("RESULT;00000001;cycle1/model.bin;4194304;copy\n")
            fh.write("RESULT;00000001;cycle1/model.manifest.json;48;copy\n")
            fh.write("RESULT;00000002;cycle1/model.bin;4194304;copy\n")
            fh.write("RESULT;00000002;cycle1/model.manifest.json;48;copy\n")

        from tests.real_hardware.issue41_gate import verify_downlink_artifacts
        dl_res = verify_downlink_artifacts(
            server_cycle_dir=cycle_dir,
            client_inboxes={'1': c1_dir, '2': c2_dir},
            status_file=status_path,
            config=self.config,
            duration_seconds=3.0,
        )
        self.assertEqual('passed', dl_res['status'])
        self.assertEqual('success', dl_res['uftp_connect_matrix']['1'])
        self.assertEqual('success', dl_res['uftp_connect_matrix']['2'])
        self.assertEqual('copy', dl_res['uftp_result_matrix']['1']['model.bin'])
        self.assertTrue(dl_res['client_received_verification']['1']['verified'])

    def test_classify_gate_failure_identifies_layers_and_category(self):
        # 场景 1: client declared 但 server 零接收 -> link_capability / server_radio
        diag1 = classify_gate_failure(
            server_rx_ant_samples=0,
            client_declared={'client1': True, 'client2': True},
            server_accepted={'client1': False, 'client2': False},
            tun_routes_ok=True,
            uftp_status_ok=False,
            http_put_ok=False,
        )
        self.assertEqual('link_capability', diag1['category'])
        self.assertEqual('link_domain_and_air', diag1['first_failing_layer'])

        # 场景 2: UFTP 阶段超时/CONNECT 失败，但空口有收包 -> link_capability / uftp_feedback_and_status
        diag2 = classify_gate_failure(
            server_rx_ant_samples=10,
            client_declared={'client1': True, 'client2': True},
            server_accepted={'client1': True, 'client2': True},
            tun_routes_ok=True,
            uftp_status_ok=False,
            http_put_ok=False,
        )
        self.assertEqual('link_capability', diag2['category'])
        self.assertEqual('uftp_feedback_and_status', diag2['first_failing_layer'])
        self.assertEqual('tun_and_route', diag2['last_successful_layer'])

        # 场景 3: 路由缺失 -> environment
        diag3 = classify_gate_failure(
            server_rx_ant_samples=10,
            client_declared={'client1': True, 'client2': True},
            server_accepted={'client1': True, 'client2': True},
            tun_routes_ok=False,
            uftp_status_ok=False,
            http_put_ok=False,
        )
        self.assertEqual('environment', diag3['category'])
        self.assertEqual('tun_and_route', diag3['first_failing_layer'])


    def _make_valid_cycle(self, cycle_index):
        size = 4 * 1024 * 1024
        model_sha = ('a' * 63) + str(cycle_index)
        manifest_sha = ('m' * 63) + str(cycle_index)
        c1_sha = ('b' * 63) + str(cycle_index)
        c2_sha = ('c' * 63) + str(cycle_index)

        return {
            'cycle_index': cycle_index,
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

    def test_parse_link_args_extracts_all_parameters(self):
        from tests.real_hardware.issue41_gate import parse_link_args
        args = [
            '--tun-name', 'v8i41s0', '--tun-addr', '10.80.0.1/24',
            '--link-id', '406', '--uplink-stream', '32', '--downlink-stream', '33',
            '--fec-k', '8', '--fec-n', '12', '--radio-bandwidth', '40',
            '--radio-mcs-index', '3', '--radio-short-gi', '--air-interface', 'wlx1',
            '--known-clients', '1,2', '--client-target', '1:10.80.0.11:127.0.0.1:1',
            '--grant-duration-ms', '120', '--guard-interval-ms', '20',
            '--downlink-pause-threshold-bytes', '131072',
            '--downlink-resume-threshold-bytes', '65536',
            '--downlink-queue-packets-limit', '64',
            '--feedback-window-period-ms', '500',
            '--feedback-window-duration-ms', '15',
            '--log-interval', '200',
        ]
        parsed = parse_link_args(args)
        self.assertEqual('v8i41s0', parsed['tun_name'])
        self.assertEqual('10.80.0.1/24', parsed['tun_addr'])
        self.assertEqual(406, parsed['link_id'])
        self.assertEqual(32, parsed['uplink_stream'])
        self.assertEqual(33, parsed['downlink_stream'])
        self.assertEqual(8, parsed['fec_k'])
        self.assertEqual(12, parsed['fec_n'])
        self.assertEqual(40, parsed['radio_bandwidth'])
        self.assertEqual(3, parsed['radio_mcs_index'])
        self.assertTrue(parsed['radio_short_gi'])
        self.assertEqual(131072, parsed['downlink_pause_threshold_bytes'])
        self.assertEqual(65536, parsed['downlink_resume_threshold_bytes'])
        self.assertEqual(64, parsed['downlink_queue_packets_limit'])
        self.assertEqual(500, parsed['feedback_window_period_ms'])
        self.assertEqual(15, parsed['feedback_window_duration_ms'])
        self.assertFalse(parsed['feedback_window_start_immediately'])

    def test_verify_config_equivalence_success(self):
        from tests.real_hardware.issue41_gate import (
            GateConfig, verify_config_equivalence,
        )
        cfg = GateConfig()
        gate_configs = {
            'server': cfg.get_expected_link_config('server', 255),
            'client1': cfg.get_expected_link_config('client', 1),
            'client2': cfg.get_expected_link_config('client', 2),
        }
        runtime_configs = {
            'server': {
                'role': 'server',
                'node_id': 255,
                'link_args': [
                    '--tun-name', 'v8i41s0', '--tun-addr', '10.80.0.1/24',
                    '--link-id', '406', '--uplink-stream', '32', '--downlink-stream', '33',
                    '--fec-k', '8', '--fec-n', '12', '--radio-bandwidth', '40',
                    '--radio-mcs-index', '3', '--radio-short-gi',
                    '--grant-duration-ms', '120', '--guard-interval-ms', '20',
                    '--downlink-pause-threshold-bytes', '131072',
                    '--downlink-resume-threshold-bytes', '65536',
                    '--downlink-queue-packets-limit', '64',
                    '--feedback-window-period-ms', '500',
                    '--feedback-window-duration-ms', '15',
                ],
            },
            'client1': {
                'role': 'client',
                'node_id': 1,
                'link_args': [
                    '--tun-name', 'v8i41c1', '--tun-addr', '10.80.0.11/24',
                    '--link-id', '406', '--uplink-stream', '32', '--downlink-stream', '33',
                    '--fec-k', '8', '--fec-n', '12', '--radio-bandwidth', '40',
                    '--radio-mcs-index', '3', '--radio-short-gi',
                    '--uplink-pause-threshold-bytes', '131072',
                    '--uplink-resume-threshold-bytes', '65536',
                    '--uplink-queue-packets-limit', '64',
                ],
            },
            'client2': {
                'role': 'client',
                'node_id': 2,
                'link_args': [
                    '--tun-name', 'v8i41c2', '--tun-addr', '10.80.0.12/24',
                    '--link-id', '406', '--uplink-stream', '32', '--downlink-stream', '33',
                    '--fec-k', '8', '--fec-n', '12', '--radio-bandwidth', '40',
                    '--radio-mcs-index', '3', '--radio-short-gi',
                    '--uplink-pause-threshold-bytes', '131072',
                    '--uplink-resume-threshold-bytes', '65536',
                    '--uplink-queue-packets-limit', '64',
                ],
            },
        }
        errors = verify_config_equivalence(gate_configs, runtime_configs)
        self.assertEqual([], errors)

    def test_verify_config_equivalence_rejects_mismatch(self):
        from tests.real_hardware.issue41_gate import (
            GateConfig, verify_config_equivalence,
        )
        cfg = GateConfig()
        gate_configs = {
            'server': cfg.get_expected_link_config('server', 255),
            'client1': cfg.get_expected_link_config('client', 1),
            'client2': cfg.get_expected_link_config('client', 2),
        }
        # client1 MCS differs (2 vs 3), server bandwidth differs (20 vs 40)
        runtime_configs = {
            'server': {
                'role': 'server',
                'node_id': 255,
                'link_args': [
                    '--tun-name', 'v8i41s0', '--tun-addr', '10.80.0.1/24',
                    '--link-id', '406', '--uplink-stream', '32', '--downlink-stream', '33',
                    '--fec-k', '8', '--fec-n', '12', '--radio-bandwidth', '20',
                    '--radio-mcs-index', '3', '--radio-short-gi',
                    '--downlink-pause-threshold-bytes', '131072',
                    '--downlink-resume-threshold-bytes', '65536',
                    '--downlink-queue-packets-limit', '64',
                    '--feedback-window-period-ms', '500',
                    '--feedback-window-duration-ms', '15',
                ],
            },
            'client1': {
                'role': 'client',
                'node_id': 1,
                'link_args': [
                    '--tun-name', 'v8i41c1', '--tun-addr', '10.80.0.11/24',
                    '--link-id', '406', '--uplink-stream', '32', '--downlink-stream', '33',
                    '--fec-k', '8', '--fec-n', '12', '--radio-bandwidth', '40',
                    '--radio-mcs-index', '2', '--radio-short-gi',
                    '--uplink-pause-threshold-bytes', '131072',
                    '--uplink-resume-threshold-bytes', '65536',
                    '--uplink-queue-packets-limit', '64',
                ],
            },
            'client2': {
                'role': 'client',
                'node_id': 2,
                'link_args': [
                    '--tun-name', 'v8i41c2', '--tun-addr', '10.80.0.12/24',
                    '--link-id', '406', '--uplink-stream', '32', '--downlink-stream', '33',
                    '--fec-k', '8', '--fec-n', '12', '--radio-bandwidth', '40',
                    '--radio-mcs-index', '3', '--radio-short-gi',
                    '--uplink-pause-threshold-bytes', '131072',
                    '--uplink-resume-threshold-bytes', '65536',
                    '--uplink-queue-packets-limit', '64',
                ],
            },
        }
        errors = verify_config_equivalence(gate_configs, runtime_configs)
        self.assertTrue(any('radio_bandwidth' in e for e in errors))
        self.assertTrue(any('radio_mcs_index' in e for e in errors))

    def test_verify_config_equivalence_rejects_immediate_feedback(self):
        from tests.real_hardware.issue41_gate import (
            GateConfig, verify_config_equivalence,
        )
        cfg = GateConfig()
        gate_configs = {
            'server': cfg.get_expected_link_config('server', 255),
            'client1': cfg.get_expected_link_config('client', 1),
            'client2': cfg.get_expected_link_config('client', 2),
        }
        runtime_configs = {
            'server': {
                'role': 'server',
                'node_id': 255,
                'link_args': [
                    '--tun-name', 'v8i41s0', '--tun-addr', '10.80.0.1/24',
                    '--link-id', '406', '--uplink-stream', '32', '--downlink-stream', '33',
                    '--fec-k', '8', '--fec-n', '12', '--radio-bandwidth', '40',
                    '--radio-mcs-index', '3', '--radio-short-gi',
                    '--downlink-pause-threshold-bytes', '131072',
                    '--downlink-resume-threshold-bytes', '65536',
                    '--downlink-queue-packets-limit', '64',
                    '--feedback-window-period-ms', '500',
                    '--feedback-window-duration-ms', '15',
                    '--feedback-window-start-immediately',
                ],
            },
            'client1': {
                'role': 'client',
                'node_id': 1,
                'link_args': [
                    '--tun-name', 'v8i41c1', '--tun-addr', '10.80.0.11/24',
                    '--link-id', '406', '--uplink-stream', '32', '--downlink-stream', '33',
                    '--fec-k', '8', '--fec-n', '12', '--radio-bandwidth', '40',
                    '--radio-mcs-index', '3', '--radio-short-gi',
                    '--uplink-pause-threshold-bytes', '131072',
                    '--uplink-resume-threshold-bytes', '65536',
                    '--uplink-queue-packets-limit', '64',
                ],
            },
            'client2': {
                'role': 'client',
                'node_id': 2,
                'link_args': [
                    '--tun-name', 'v8i41c2', '--tun-addr', '10.80.0.12/24',
                    '--link-id', '406', '--uplink-stream', '32', '--downlink-stream', '33',
                    '--fec-k', '8', '--fec-n', '12', '--radio-bandwidth', '40',
                    '--radio-mcs-index', '3', '--radio-short-gi',
                    '--uplink-pause-threshold-bytes', '131072',
                    '--uplink-resume-threshold-bytes', '65536',
                    '--uplink-queue-packets-limit', '64',
                ],
            },
        }
        errors = verify_config_equivalence(gate_configs, runtime_configs)
        self.assertTrue(any('--feedback-window-start-immediately' in e for e in errors))

    def test_cli_verify_config_equivalence(self):
        from tests.real_hardware.issue41_gate import main
        d = tempfile.mkdtemp(prefix='issue41-eq-test-')
        self.addCleanup(shutil.rmtree, d, True)
        s_path = os.path.join(d, 'fl-server.json')
        c1_path = os.path.join(d, 'fl-client1.json')
        c2_path = os.path.join(d, 'fl-client2.json')
        out_path = os.path.join(d, 'eq-out.json')
        with open(s_path, 'w') as fh:
            json.dump({
                'role': 'server',
                'link_args': [
                    '--tun-name', 'v8i41s0', '--tun-addr', '10.80.0.1/24',
                    '--link-id', '406', '--uplink-stream', '32', '--downlink-stream', '33',
                    '--fec-k', '8', '--fec-n', '12', '--radio-bandwidth', '40',
                    '--radio-mcs-index', '3', '--radio-short-gi',
                    '--downlink-pause-threshold-bytes', '131072',
                    '--downlink-resume-threshold-bytes', '65536',
                    '--downlink-queue-packets-limit', '64',
                    '--feedback-window-period-ms', '500',
                    '--feedback-window-duration-ms', '15',
                ]
            }, fh)
        with open(c1_path, 'w') as fh:
            json.dump({
                'role': 'client',
                'node_id': 1,
                'link_args': [
                    '--tun-name', 'v8i41c1', '--tun-addr', '10.80.0.11/24',
                    '--link-id', '406', '--uplink-stream', '32', '--downlink-stream', '33',
                    '--fec-k', '8', '--fec-n', '12', '--radio-bandwidth', '40',
                    '--radio-mcs-index', '3', '--radio-short-gi',
                    '--uplink-pause-threshold-bytes', '131072',
                    '--uplink-resume-threshold-bytes', '65536',
                    '--uplink-queue-packets-limit', '64',
                ]
            }, fh)
        with open(c2_path, 'w') as fh:
            json.dump({
                'role': 'client',
                'node_id': 2,
                'link_args': [
                    '--tun-name', 'v8i41c2', '--tun-addr', '10.80.0.12/24',
                    '--link-id', '406', '--uplink-stream', '32', '--downlink-stream', '33',
                    '--fec-k', '8', '--fec-n', '12', '--radio-bandwidth', '40',
                    '--radio-mcs-index', '3', '--radio-short-gi',
                    '--uplink-pause-threshold-bytes', '131072',
                    '--uplink-resume-threshold-bytes', '65536',
                    '--uplink-queue-packets-limit', '64',
                ]
            }, fh)
        ret = main([
            'verify-config-equivalence',
            '--server-fl', s_path,
            '--client1-fl', c1_path,
            '--client2-fl', c2_path,
            '--out', out_path,
        ])
        self.assertEqual(0, ret)
        self.assertTrue(os.path.isfile(out_path))
        with open(out_path, 'r') as fh:
            res = json.load(fh)
        self.assertEqual('passed', res['status'])

    def test_build_cycle_evidence_defaults_and_config(self):
        ev = build_cycle_evidence(
            cycle_index=1,
            downlink={'status': 'passed'},
            uplink={'status': 'passed'},
            telemetry={'phase_durations': {'cycle_total_seconds': 12.5}},
            config=self.config,
        )
        self.assertEqual(1, ev['cycle_index'])
        self.assertEqual('passed', ev['status'])
        self.assertEqual(12.5, ev['total_duration_seconds'])
        self.assertEqual(240, ev['deadline_seconds'])
        self.assertEqual(120, ev['io_timeout_seconds'])

    def test_gate_config_from_env_defaults_and_overrides(self):
        # 默认无环境变量情况
        old_env = os.environ.copy()
        try:
            for k in ('ISSUE41_SMOKE_CYCLE_COUNT', 'ISSUE41_INPUT_SIZE_BYTES',
                      'ISSUE41_SMOKE_IO_TIMEOUT_SECONDS', 'ISSUE41_SMOKE_CYCLE_DEADLINE_SECONDS'):
                os.environ.pop(k, None)
            cfg = GateConfig.from_env()
            self.assertEqual(3, cfg.cycle_count)
            self.assertEqual(4 * 1024 * 1024, cfg.artifact_size_bytes)
            self.assertEqual(120, cfg.io_timeout_seconds)
            self.assertEqual(240, cfg.cycle_deadline_seconds)

            # 环境变量覆盖
            os.environ['ISSUE41_SMOKE_CYCLE_COUNT'] = '1'
            os.environ['ISSUE41_INPUT_SIZE_BYTES'] = str(40 * 1024 * 1024)
            os.environ['ISSUE41_SMOKE_IO_TIMEOUT_SECONDS'] = '120'
            os.environ['ISSUE41_SMOKE_CYCLE_DEADLINE_SECONDS'] = '300'
            cfg2 = GateConfig.from_env()
            self.assertEqual(1, cfg2.cycle_count)
            self.assertEqual(40 * 1024 * 1024, cfg2.artifact_size_bytes)
            self.assertEqual(120, cfg2.io_timeout_seconds)
            self.assertEqual(300, cfg2.cycle_deadline_seconds)
        finally:
            os.environ.clear()
            os.environ.update(old_env)


if __name__ == '__main__':
    unittest.main()
