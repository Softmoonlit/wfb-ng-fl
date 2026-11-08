"""只提取 helper 的纯解析函数；不执行节点脚本顶层或任何远端动作。"""
import ast
from pathlib import Path
import unittest
import struct
import json
import os
import tempfile
from unittest import mock

BASE = Path(__file__).resolve().parent
namespace = {'struct': struct, 'json': json, 'os': os, 'tempfile': tempfile, 'pathlib': __import__('pathlib')}
tree = ast.parse((BASE / 'release-online-node.py').read_text())
functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
             and node.name in {'native_option', 'verify_native_expectations', 'client_startup_options', 'parse_fec_response', 'install_server_fec'}]
exec(compile(ast.Module(body=functions, type_ignores=[]), '<helper-pure-functions>', 'exec'), namespace)
verify = namespace['verify_native_expectations']
startup = namespace['client_startup_options']


class ExpectedRuntimeTests(unittest.TestCase):
    expected = dict(grant_duration_ms=18, guard_interval_ms=2, fec_n=14)
    config = dict(grant_duration_ms=18, guard_interval_ms=2)

    def server_args(self, grant='18', guard='2', fec='14'):
        return ['/usr/bin/wfb_v6_uplink', '--fec-k', '8', '--fec-n', fec,
                '--grant-duration-ms', grant, '--guard-interval-ms', guard]

    def test_install_server_fec12_atomic_preserves_other_json_and_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'server.json'
            record = Path(directory) / 'change.json'
            before = dict(self.config, fec_n=14, channel=149, downlink_mcs=3,
                          uplink_mcs=4, other={'keep': [1, 2]})
            path.write_text(json.dumps(before))
            path.chmod(0o640)
            expected = dict(self.expected, fec_n=12)
            replace = os.replace
            def checked_replace(source, destination):
                self.assertEqual(json.loads(path.read_text()), before)
                self.assertEqual(Path(source).parent, path.parent)
                self.assertEqual(json.loads(Path(source).read_text()), dict(before, fec_n=12))
                replace(source, destination)
            with mock.patch.object(os, 'replace', side_effect=checked_replace) as replacement:
                namespace['install_server_fec'](path, record, expected)
                replacement.assert_called_once()
            self.assertEqual(json.loads(path.read_text()), dict(before, fec_n=12))
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)
            self.assertEqual(json.loads(record.read_text()), dict(before=before, after=dict(before, fec_n=12), expected=expected))
            self.assertEqual(list(Path(directory).glob('.server-fec-*')), [])

    def test_install_server_fec_rejects_schedule_mismatch_without_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'server.json'
            path.write_text(json.dumps(dict(grant_duration_ms=15, guard_interval_ms=5)))
            before = path.read_bytes()
            with self.assertRaises(AssertionError):
                namespace['install_server_fec'](path, Path(directory) / 'change.json', dict(self.expected, fec_n=12))
            self.assertEqual(path.read_bytes(), before)

    def test_get_fec_response_matches_actual_expectation(self):
        parse = namespace['parse_fec_response']
        address = ('127.0.0.1', 9200)
        for fec in (12, 14):
            self.assertEqual(parse(struct.pack('!II2B', 0x52464543, 0, 8, fec), address, 9200, fec)['n'], fec)
        for raw, sender in ((struct.pack('!II2B', 0x52464543, 0, 8, 12), address),
                            (struct.pack('!II2B', 1, 0, 8, 14), address),
                            (struct.pack('!II2B', 0x52464543, 1, 8, 14), address),
                            (b'', address),
                            (struct.pack('!II2B', 0x52464543, 0, 8, 14), ('127.0.0.1', 9201))):
            with self.subTest(raw=raw, sender=sender), self.assertRaises(AssertionError):
                parse(raw, sender, 9200, 14)

    def test_server_18_2_14_text_and_proc_bytes(self):
        args = self.server_args()
        verify(args, 'server', self.config, self.expected)
        verify([p.encode() for p in args] + [b''], 'server', self.config, self.expected)

    def test_wrong_native_schedule_or_fec_rejected(self):
        for args in (self.server_args(grant='10'), self.server_args(guard='10'), self.server_args(fec='12')):
            with self.subTest(args=args), self.assertRaises(AssertionError):
                verify(args, 'server', self.config, self.expected)

    def test_json_schedule_and_fec_must_match_explicit_state(self):
        for config in ({}, dict(grant_duration_ms=18, guard_interval_ms=5), dict(self.config, fec_n=12)):
            with self.subTest(config=config), self.assertRaises(AssertionError):
                verify(self.server_args(), 'server', config, self.expected)

    def test_missing_duplicate_or_dangling_native_arg_rejected(self):
        for args in (['--fec-k', '8'], ['--fec-k', '8', '--fec-n'],
                     self.server_args() + ['--fec-n', '14']):
            with self.subTest(args=args), self.assertRaises(AssertionError):
                verify(args, 'server', self.config, self.expected)

    def test_legacy_schedule_defaults_are_only_10_10(self):
        verify(self.server_args('10', '10'), 'server', {},
               dict(grant_duration_ms=10, guard_interval_ms=10, fec_n=14))

    def test_client_12_and_14_match_actual_native(self):
        for fec in (12, 14):
            verify(['--fec-k', '8', '--fec-n', str(fec)], 'client', {}, dict(self.expected, fec_n=fec))
            with self.assertRaises(AssertionError):
                verify(['--fec-k', '8', '--fec-n', str(26 - fec)], 'client', {}, dict(self.expected, fec_n=fec))

    def test_client_startup_explicit_fec_keeps_other_parameters(self):
        before = ['--config', '/etc/wfb-ng-fl/node.json', '--interface', 'actualiface', '--uplink-mcs', '4']
        for fec in (12, 14):
            self.assertEqual(startup(before, fec), before + ['--fec-n', str(fec)])
            self.assertEqual(startup(before + ['--fec-n', '14'], fec), before + ['--fec-n', str(fec)])
        self.assertNotIn('--fec-n', before)

    def test_client_startup_duplicate_or_missing_value_rejected(self):
        for args in (['--fec-n'], ['--fec-n', '14', '--fec-n', '12']):
            with self.subTest(args=args), self.assertRaises(AssertionError):
                startup(args, 14)


if __name__ == '__main__':
    unittest.main()
