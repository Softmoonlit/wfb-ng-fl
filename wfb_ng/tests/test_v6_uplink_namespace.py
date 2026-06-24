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
    SCENARIO_V6_NAMESPACE_UPLINK,
    SUMMARY_FILENAME,
    allowed_fields_for,
    load_summary,
)


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
SCRIPT_PATH = os.path.join(PROJECT_ROOT, 'tests', 'acceptance', 'v6_uplink_namespace.sh')


class V6UplinkNamespaceTestCase(unittest.TestCase):
    if os.geteuid() != 0 or not os.path.exists('/dev/net/tun'):
        skip = 'Root and /dev/net/tun are required'

    def test_v6_uplink_namespace_success(self):
        suffix = '%d-%d' % (os.getpid(), int(time.time() * 1000))
        log_dir = tempfile.mkdtemp(prefix='wfb-v6-uplink-', dir='/tmp')
        env = os.environ.copy()
        env.update({
            'LOG_DIR': log_dir,
            'SERVER_NS': 'v6u-server-%s' % suffix,
            'CLIENT1_NS': 'v6u-client1-%s' % suffix,
            'CLIENT2_NS': 'v6u-client2-%s' % suffix,
            'STARTUP_WAIT_SEC': '0.5',
            'PING_COUNT': '4',
            'PING_DEADLINE_SEC': '20',
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
            self.assertIn('- 已覆盖: trusted_plaintext client TUN -> 空口 -> server TUN 首条新底座上行路径', content)
            self.assertIn('- 已覆盖: 双客户端 READY/GRANT 持续推进与独立 TCP/IP 会话语义', content)
            self.assertIn('- 已覆盖: issue #22 固定容量用户态队列、双阈值水位与 TUN 反压 2A 摘要', content)
            self.assertIn('- 已覆盖: issue #23 统一结构化摘要已接入 RX 重组窗口字段', content)
            self.assertRegex(content, r'- client1 grants: [1-9][0-9]*')
            self.assertRegex(content, r'- client2 grants: [1-9][0-9]*')
            self.assertRegex(content, r'- client1 authorized sends: [1-9][0-9]*')
            self.assertRegex(content, r'- client2 authorized sends: [1-9][0-9]*')
            self.assertRegex(content, r'- run unfinished_block_limit: [1-9][0-9]*')
            self.assertEqual('namespace', summary['run_kind'])
            self.assertEqual('trusted_plaintext', summary['link_security_mode'])
            self.assertEqual(SCENARIO_V6_NAMESPACE_UPLINK, summary['scenario_id'])
            self.assertFalse(summary['feedback_window_covered'])
            self.assertEqual(set(allowed_fields_for(SCENARIO_V6_NAMESPACE_UPLINK)), set(summary.keys()))
            self.assertGreater(summary['tun_read_pause_total'], 0)
            self.assertGreater(summary['tun_read_resume_total'], 0)
            self.assertGreater(summary['tun_read_pause_total_by_reason']['queued_bytes_threshold'], 0)
            self.assertGreater(summary['tun_read_pause_total_by_reason']['queued_packets_limit'], 0)
            self.assertIn('reassembly_overflow_evict', summary)
            self.assertGreater(summary['unfinished_block_limit'], 0)
            self.assertNotIn('rx_reassembly', summary)
            self.assertNotIn('clients', summary)
            self.assertIn('- 对应 issue：#25', conclusion)
            self.assertIn('- 统一 2A 摘要: %s' % summary_json, conclusion)
        finally:
            subprocess.run(['ip', 'netns', 'delete', env['SERVER_NS']], capture_output=True)
            subprocess.run(['ip', 'netns', 'delete', env['CLIENT1_NS']], capture_output=True)
            subprocess.run(['ip', 'netns', 'delete', env['CLIENT2_NS']], capture_output=True)
            shutil.rmtree(log_dir, ignore_errors=True)
