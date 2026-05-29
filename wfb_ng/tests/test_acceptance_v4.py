#!/usr/bin/env python

import os
import shutil
import subprocess
import tempfile
import time

from twisted.trial import unittest


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
SCRIPT_PATH = os.path.join(PROJECT_ROOT, 'tests', 'acceptance', 'v4_namespace_topology.sh')


class V4AcceptanceTestCase(unittest.TestCase):
    if os.geteuid() != 0 or not os.path.exists('/dev/net/tun'):
        skip = 'Root and /dev/net/tun are required'

    def make_env(self, suffix, log_dir):
        env = os.environ.copy()
        env.update({
            'LOG_DIR': log_dir,
            'SERVER_NS': 'v4-test-server-%s' % (suffix,),
            'CLIENT1_NS': 'v4-test-client1-%s' % (suffix,),
            'CLIENT2_NS': 'v4-test-client2-%s' % (suffix,),
            'STARTUP_WAIT_SEC': '0.5',
        })
        return env

    def run_script(self, env):
        return subprocess.run(
            ['bash', SCRIPT_PATH],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

    def assert_namespaces_cleaned(self, env):
        netns_list = subprocess.run(
            ['ip', 'netns', 'list'],
            capture_output=True,
            check=True,
            text=True,
        ).stdout

        self.assertNotIn(env['SERVER_NS'], netns_list)
        self.assertNotIn(env['CLIENT1_NS'], netns_list)
        self.assertNotIn(env['CLIENT2_NS'], netns_list)

    def test_v4_acceptance_success(self):
        suffix = '%d-%d' % (os.getpid(), int(time.time() * 1000))
        log_dir = tempfile.mkdtemp(prefix='wfb-v4-success-', dir='/tmp')
        env = self.make_env(suffix, log_dir)

        try:
            result = self.run_script(env)
            result_md = os.path.join(log_dir, 'result.md')

            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertTrue(os.path.exists(result_md))

            with open(result_md, 'r') as fh:
                content = fh.read()

            self.assertIn('- 结果: PASS', content)
            self.assertIn(env['SERVER_NS'], content)
            self.assertIn(env['CLIENT1_NS'], content)
            self.assertIn(env['CLIENT2_NS'], content)
            self.assertIn('- cleanup_verified: 是', content)
        finally:
            self.assert_namespaces_cleaned(env)
            shutil.rmtree(log_dir, ignore_errors=True)

    def test_v4_acceptance_failure_still_cleans_up(self):
        suffix = '%d-%d-fail' % (os.getpid(), int(time.time() * 1000))
        log_dir = tempfile.mkdtemp(prefix='wfb-v4-fail-', dir='/tmp')
        env = self.make_env(suffix, log_dir)
        env['SERVER_TUN_ADDR'] = 'invalid-address'

        try:
            result = self.run_script(env)
            result_md = os.path.join(log_dir, 'result.md')

            self.assertNotEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertTrue(os.path.exists(result_md))

            with open(result_md, 'r') as fh:
                content = fh.read()

            self.assertIn('- 结果: FAIL', content)
            self.assertIn('- cleanup_verified: 是', content)
        finally:
            self.assert_namespaces_cleaned(env)
            shutil.rmtree(log_dir, ignore_errors=True)
