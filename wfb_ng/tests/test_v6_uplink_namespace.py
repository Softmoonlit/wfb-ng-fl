#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json
import os
import shutil
import subprocess
import tempfile
import time

from twisted.trial import unittest


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
            summary_json = os.path.join(log_dir, 'v6_uplink_issue23_summary.json')
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertTrue(os.path.exists(result_md))
            self.assertTrue(os.path.exists(summary_json))

            with open(result_md, 'r') as fh:
                content = fh.read()
            with open(summary_json, 'r') as fh:
                summary = json.load(fh)

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
            self.assertEqual('v6_uplink_namespace_issue23', summary['scenario_id'])
            self.assertFalse(summary['feedback_window_covered'])
            self.assertGreater(summary['tun_read_pause_total'], 0)
            self.assertGreater(summary['tun_read_resume_total'], 0)
            self.assertGreater(summary['tun_read_pause_total_by_reason']['queued_bytes_threshold'], 0)
            self.assertGreater(summary['tun_read_pause_total_by_reason']['queued_packets_limit'], 0)
            self.assertIn('reassembly_overflow_evict', summary)
            self.assertGreater(summary['unfinished_block_limit'], 0)
            self.assertIn('rx_reassembly', summary)
            self.assertEqual(summary['unfinished_block_limit'], summary['rx_reassembly']['server']['unfinished_block_limit'])
        finally:
            subprocess.run(['ip', 'netns', 'delete', env['SERVER_NS']], capture_output=True)
            subprocess.run(['ip', 'netns', 'delete', env['CLIENT1_NS']], capture_output=True)
            subprocess.run(['ip', 'netns', 'delete', env['CLIENT2_NS']], capture_output=True)
            shutil.rmtree(log_dir, ignore_errors=True)
