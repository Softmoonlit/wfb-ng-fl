#!/usr/bin/env python

import os
import shutil
import subprocess
import tempfile
import time
import hashlib

from twisted.trial import unittest


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
SCRIPT_PATH = os.path.join(PROJECT_ROOT, 'tests', 'acceptance', 'v4_namespace_topology.sh')
UFTP_BIN = os.path.join(PROJECT_ROOT, 'uftp')
UFTPD_BIN = os.path.join(PROJECT_ROOT, 'uftpd')


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
            'UFTP_BIN': UFTP_BIN,
            'UFTPD_BIN': UFTPD_BIN,
            'UFTP_PAYLOAD_SIZE': '65536',
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
            self.assertIn('- shared downlink sender: 1', content)
            self.assertIn('- 已覆盖: shared downlink sender 与测试专用 fan-out', content)
            self.assertIn('- 说明: fan-out 为测试专用，不代表生产传输组件', content)
            self.assertIn('- 已覆盖: UFTP 共享下行 payload', content)
            self.assertIn('- UFTP payload size: 65536 bytes', content)
            self.assertIn('- UFTP source sha256:', content)
            self.assertIn('- client1 UFTP sha256:', content)
            self.assertIn('- client2 UFTP sha256:', content)

            client1_probe = os.path.join(log_dir, 'client1', 'downlink_probe_received.log')
            client2_probe = os.path.join(log_dir, 'client2', 'downlink_probe_received.log')

            self.assertTrue(os.path.exists(client1_probe))
            self.assertTrue(os.path.exists(client2_probe))

            with open(client1_probe, 'r') as fh:
                client1_content = fh.read()
            with open(client2_probe, 'r') as fh:
                client2_content = fh.read()

            self.assertIn('V4_SHARED_DOWNLINK_CLIENT1', client1_content)
            self.assertIn('V4_SHARED_DOWNLINK_CLIENT2', client2_content)

            server_payload = os.path.join(log_dir, 'server', 'uftp_payload.bin')
            client1_payload = os.path.join(log_dir, 'client1', 'uftp_dest', 'uftp_payload.bin')
            client2_payload = os.path.join(log_dir, 'client2', 'uftp_dest', 'uftp_payload.bin')

            self.assertTrue(os.path.exists(server_payload))
            self.assertTrue(os.path.exists(client1_payload))
            self.assertTrue(os.path.exists(client2_payload))
            self.assertEqual(self.file_sha256(server_payload), self.file_sha256(client1_payload))
            self.assertEqual(self.file_sha256(server_payload), self.file_sha256(client2_payload))
        finally:
            self.assert_namespaces_cleaned(env)
            shutil.rmtree(log_dir, ignore_errors=True)

    def file_sha256(self, path):
        digest = hashlib.sha256()
        with open(path, 'rb') as fh:
            for chunk in iter(lambda: fh.read(65536), b''):
                digest.update(chunk)
        return digest.hexdigest()

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

    def test_v4_acceptance_missing_uftp_dependency_guidance(self):
        for env_name, binary_name in (('UFTP_BIN', 'uftp'), ('UFTPD_BIN', 'uftpd')):
            suffix = '%d-%d-missing-%s' % (os.getpid(), int(time.time() * 1000), binary_name)
            log_dir = tempfile.mkdtemp(prefix='wfb-v4-missing-%s-' % (binary_name,), dir='/tmp')
            env = self.make_env(suffix, log_dir)
            env[env_name] = '/nonexistent/%s' % (binary_name,)

            try:
                result = self.run_script(env)
                result_md = os.path.join(log_dir, 'result.md')

                self.assertNotEqual(result.returncode, 0, msg=result.stdout + result.stderr)
                self.assertTrue(os.path.exists(result_md))

                with open(result_md, 'r') as fh:
                    content = fh.read()

                self.assertIn('- 结果: FAIL', content)
                self.assertIn('SourceForge', content)
                self.assertIn('手动编译', content)
                self.assertNotIn('apt install', content)
            finally:
                self.assert_namespaces_cleaned(env)
                shutil.rmtree(log_dir, ignore_errors=True)
