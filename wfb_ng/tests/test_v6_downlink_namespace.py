#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import shutil
import subprocess
import tempfile
import time

from twisted.trial import unittest

from wfb_ng.tests.v6_formal_summary import (
    CONCLUSION_FILENAME,
    SCENARIO_V6_NAMESPACE_DOWNLINK,
    SUMMARY_FILENAME,
    allowed_fields_for,
    load_summary,
)


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
SCRIPT_PATH = os.path.join(PROJECT_ROOT, 'tests', 'acceptance', 'v6_downlink_namespace.sh')
UFTP_BIN = shutil.which('uftp')
UFTPD_BIN = shutil.which('uftpd')


class V6DownlinkNamespaceTestCase(unittest.TestCase):
    if os.geteuid() != 0 or not os.path.exists('/dev/net/tun'):
        skip = 'Root and /dev/net/tun are required'
    elif not UFTP_BIN or not UFTPD_BIN:
        skip = 'uftp/uftpd are required'

    def test_v6_downlink_namespace_success(self):
        suffix = '%d-%d' % (os.getpid(), int(time.time() * 1000))
        log_dir = tempfile.mkdtemp(prefix='wfb-v6-downlink-', dir='/tmp')
        env = os.environ.copy()
        env.update({
            'LOG_DIR': log_dir,
            'SERVER_NS': 'v6d-server-%s' % suffix,
            'CLIENT1_NS': 'v6d-client1-%s' % suffix,
            'CLIENT2_NS': 'v6d-client2-%s' % suffix,
            'STARTUP_WAIT_SEC': '1',
            'POST_TRANSFER_WAIT_SEC': '2',
            'UFTP_BIN': UFTP_BIN,
            'UFTPD_BIN': UFTPD_BIN,
            'UFTP_PAYLOAD_SIZE': '131072',
            'SERVER_DOWNLINK_PAUSE_THRESHOLD_BYTES': '1200',
            'SERVER_DOWNLINK_RESUME_THRESHOLD_BYTES': '600',
            'SERVER_DOWNLINK_QUEUE_PACKETS_LIMIT': '1',
            'FEEDBACK_WINDOW_PERIOD_MS': '150',
            'FEEDBACK_WINDOW_DURATION_MS': '50',
        })

        try:
            result = subprocess.run(
                ['bash', SCRIPT_PATH],
                cwd=PROJECT_ROOT,
                env=env,
                capture_output=True,
                text=True,
            )
            result_md = os.path.join(log_dir, 'result.md')
            summary_json = os.path.join(log_dir, SUMMARY_FILENAME)
            conclusion_md = os.path.join(log_dir, CONCLUSION_FILENAME)
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertTrue(os.path.exists(result_md))
            self.assertTrue(os.path.exists(summary_json))
            self.assertTrue(os.path.exists(conclusion_md))

            with open(result_md, 'r') as fh:
                content = fh.read()
            with open(conclusion_md, 'r') as fh:
                conclusion = fh.read()
            summary = load_summary(summary_json)

            self.assertIn('- 结果: PASS', content)
            self.assertIn('- cleanup_verified: 是', content)
            self.assertIn('- 已覆盖: trusted_plaintext 新底座 shared UFTP 一发多收下行语义', content)
            self.assertIn('- 已覆盖: server 下行固定容量队列与 TUN 停读/恢复 2A 摘要', content)
            self.assertIn('- 已覆盖: feedback window 短 GRANT 轮询与反向上行命中证据', content)
            self.assertRegex(content, r'- grant_sent_total: [1-9][0-9]*')
            self.assertRegex(content, r'- ready_accepted_total: [1-9][0-9]*')
            self.assertRegex(content, r'- feedback_window_open_count: [1-9][0-9]*')
            self.assertRegex(content, r'- feedback_window_close_count: [1-9][0-9]*')
            self.assertRegex(content, r'- feedback_uplink_hit_total: [1-9][0-9]*')
            self.assertRegex(content, r'- server tun_read_pause_total: [1-9][0-9]*')
            self.assertRegex(content, r'- server tun_read_resume_total: [1-9][0-9]*')

            self.assertEqual('namespace', summary['run_kind'])
            self.assertEqual('trusted_plaintext', summary['link_security_mode'])
            self.assertEqual(SCENARIO_V6_NAMESPACE_DOWNLINK, summary['scenario_id'])
            self.assertTrue(summary['feedback_window_covered'])
            self.assertEqual(set(allowed_fields_for(SCENARIO_V6_NAMESPACE_DOWNLINK)), set(summary.keys()))
            self.assertGreater(summary['grant_sent_total'], 0)
            self.assertGreater(summary['ready_accepted_total'], 0)
            self.assertEqual(
                {'invalid_source', 'wrong_ingress_or_link_domain', 'unknown_client'},
                set(summary['ready_rejected_total_by_reason'].keys()),
            )
            self.assertGreater(summary['feedback_window_open_count'], 0)
            self.assertGreater(summary['feedback_window_close_count'], 0)
            self.assertIn('feedback_uplink_hit_total_by_node', summary)
            self.assertGreater(summary['feedback_uplink_hit_total'], 0)
            self.assertGreater(summary['tun_read_pause_total'], 0)
            self.assertGreater(summary['tun_read_resume_total'], 0)
            self.assertIn('queued_bytes_threshold', summary['tun_read_pause_total_by_reason'])
            self.assertIn('queued_packets_limit', summary['tun_read_pause_total_by_reason'])
            self.assertGreater(summary['reassembly_overflow_evict'], -1)
            self.assertGreater(summary['unfinished_block_limit'], 0)
            self.assertNotIn('clients', summary)
            self.assertNotIn('tun_read_paused', summary)
            self.assertNotIn('current_pause_reason', summary)
            self.assertIn('- 对应 issue：#25', conclusion)
            self.assertIn('- 统一 2A 摘要: %s' % summary_json, conclusion)

            server_payload = os.path.join(log_dir, 'server', 'uftp_payload.bin')
            client1_payload = os.path.join(log_dir, 'client1', 'uftp_dest', 'uftp_payload.bin')
            client2_payload = os.path.join(log_dir, 'client2', 'uftp_dest', 'uftp_payload.bin')
            self.assertTrue(os.path.exists(server_payload))
            self.assertTrue(os.path.exists(client1_payload))
            self.assertTrue(os.path.exists(client2_payload))
            self.assertEqual(self.file_sha256(server_payload), self.file_sha256(client1_payload))
            self.assertEqual(self.file_sha256(server_payload), self.file_sha256(client2_payload))
        finally:
            subprocess.run(['ip', 'netns', 'delete', env['SERVER_NS']], capture_output=True)
            subprocess.run(['ip', 'netns', 'delete', env['CLIENT1_NS']], capture_output=True)
            subprocess.run(['ip', 'netns', 'delete', env['CLIENT2_NS']], capture_output=True)
            shutil.rmtree(log_dir, ignore_errors=True)

    def file_sha256(self, path):
        import hashlib
        digest = hashlib.sha256()
        with open(path, 'rb') as fh:
            digest.update(fh.read())
        return digest.hexdigest()
