#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import io
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout

from wfb_ng.fl import FLRuntimeError
from wfb_ng.fl import issue41_fixtures
from wfb_ng.fl.issue41_fixtures import (
    DEFAULT_ARTIFACT_SIZE_BYTES,
    generate_all_fixtures,
    generate_client_fixture,
    generate_deterministic_file,
    generate_model_fixture,
    main,
    verify_fixture_file,
)


class Issue41FixturesTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='wfb-issue41-fixtures-test-')
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_generates_exact_4mib_deterministic_files(self):
        dest_dir = os.path.join(self.root, 'fixtures')
        fixtures = generate_all_fixtures(dest_dir, size_bytes=DEFAULT_ARTIFACT_SIZE_BYTES)

        self.assertEqual(DEFAULT_ARTIFACT_SIZE_BYTES, os.path.getsize(fixtures['model']['path']))
        self.assertEqual(DEFAULT_ARTIFACT_SIZE_BYTES, os.path.getsize(fixtures['client1']['path']))
        self.assertEqual(DEFAULT_ARTIFACT_SIZE_BYTES, os.path.getsize(fixtures['client2']['path']))

        self.assertNotEqual(fixtures['model']['sha256'], fixtures['client1']['sha256'])
        self.assertNotEqual(fixtures['client1']['sha256'], fixtures['client2']['sha256'])
        self.assertNotEqual(fixtures['model']['sha256'], fixtures['client2']['sha256'])

        # 再次生成确认确定性不变
        re_fixtures = generate_all_fixtures(dest_dir, size_bytes=DEFAULT_ARTIFACT_SIZE_BYTES)
        self.assertEqual(fixtures['model']['sha256'], re_fixtures['model']['sha256'])
        self.assertEqual(fixtures['client1']['sha256'], re_fixtures['client1']['sha256'])
        self.assertEqual(fixtures['client2']['sha256'], re_fixtures['client2']['sha256'])

    def test_verify_fixture_file_succeeds_for_valid_file(self):
        path = os.path.join(self.root, 'model.bin')
        info = generate_model_fixture(path, size_bytes=1024)
        digest = verify_fixture_file(path, expected_size=1024, expected_sha256=info['sha256'])
        self.assertEqual(info['sha256'], digest)

    def test_verify_fixture_file_fails_on_size_mismatch(self):
        path = os.path.join(self.root, 'model.bin')
        generate_model_fixture(path, size_bytes=1024)
        with self.assertRaises(FLRuntimeError) as ctx:
            verify_fixture_file(path, expected_size=2048)
        self.assertIn('大小错误', ctx.exception.error_message)

    def test_verify_fixture_file_fails_on_sha256_mismatch(self):
        path = os.path.join(self.root, 'model.bin')
        generate_model_fixture(path, size_bytes=1024)
        with self.assertRaises(FLRuntimeError) as ctx:
            verify_fixture_file(path, expected_size=1024, expected_sha256='0' * 64)
        self.assertIn('SHA-256 不匹配', ctx.exception.error_message)

    def test_cli_generate_and_verify(self):
        dest_dir = os.path.join(self.root, 'cli_fixtures')
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main(['generate', '--dest-dir', dest_dir, '--size', '1024'])
        self.assertEqual(0, ret)
        self.assertIn('model:', buf.getvalue())
        self.assertIn('client1:', buf.getvalue())
        self.assertIn('client2:', buf.getvalue())

        model_path = os.path.join(dest_dir, 'model-4mib.bin')
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main(['verify', model_path, '--size', '1024'])
        self.assertEqual(0, ret)
        self.assertIn('OK:', buf.getvalue())


if __name__ == '__main__':
    unittest.main()
