"""只在本地临时目录测试；禁止执行发布阶段或连接设备。"""
import ast
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

BASE = Path(__file__).resolve().parent
INSTALLER = BASE.parents[1] / 'scripts/install/install-v8.sh'


class NativeArtifactTests(unittest.TestCase):
    def test_new_batch_requires_explicit_artifact(self):
        result = subprocess.run(['python3', str(BASE / 'release-online.py'), 'init',
                                 'a' * 40, 'fl00'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('--native-artifact', result.stderr)

    def atomic_replace(self):
        tree = ast.parse((BASE / 'release-online-node.py').read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'atomic_native_replace')
        namespace = {'hashlib': hashlib, 'os': os, 'tempfile': tempfile, 'pathlib': __import__('pathlib')}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<atomic>', 'exec'), namespace)
        return namespace['atomic_native_replace']

    def test_installer_replaces_inode_and_preserves_open_old_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            target = base / 'installed'
            target.write_bytes(b'old-native')
            artifact = base / 'artifact'
            artifact.write_bytes(b'new-native')
            old_inode = target.stat().st_ino
            with target.open('rb') as old:
                self.atomic_replace()(artifact, target, hashlib.sha256(b'new-native').hexdigest())
                self.assertEqual(old.read(), b'old-native')
            self.assertEqual(target.read_bytes(), b'new-native')
            self.assertNotEqual(target.stat().st_ino, old_inode)
            self.assertEqual(target.stat().st_mode & 0o777, 0o755)

    def test_wrong_sha_preserves_old_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            target = base / 'installed';target.write_bytes(b'old-native')
            artifact = base / 'artifact';artifact.write_bytes(b'new-native')
            with self.assertRaises(AssertionError):
                self.atomic_replace()(artifact, target, '0' * 64)
            self.assertEqual(target.read_bytes(), b'old-native')
            self.assertEqual(list(base.glob('.wfb-native-*')), [])

    def test_abi_checks_locale_and_rejects_missing_dependencies(self):
        tree = ast.parse((BASE / 'release-online-node.py').read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'native_abi')
        calls = []
        missing = [False]
        def run(args, **kwargs):
            calls.append((args, kwargs))
            self.assertEqual(kwargs['env']['LC_ALL'], 'C')
            if args[0] == 'ldd':
                return 'libbad.so => not found' if missing[0] else 'libc.so.6 => /lib/libc.so.6'
            return {'-h': "ELF64 AArch64 2's complement, little endian", '-l': '/lib/ld-linux-aarch64.so.1'}.get(args[1], '')
        namespace = {'os': os, 'run': run}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<abi>', 'exec'), namespace)
        namespace['native_abi']('/fixture/native')
        self.assertEqual(len(calls), 5)
        missing[0] = True
        with self.assertRaises(AssertionError):
            namespace['native_abi']('/fixture/native')


if __name__ == '__main__':
    unittest.main()
