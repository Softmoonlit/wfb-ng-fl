"""Exercise build identity in real distributions without building radio binaries."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[2]
RESOURCE = 'wfb_ng/fl/build_identity.json'
COMMIT = '0123456789abcdef0123456789abcdef01234567'
OTHER_COMMIT = 'abcdef0123456789abcdef0123456789abcdef01'


class BuildIdentityPackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / 'source'
        self.source.mkdir()
        # Copy only packaging inputs; never touch the checkout's generated files.
        for name in ('setup.py', 'MANIFEST.in'):
            shutil.copyfile(ROOT / name, self.source / name)
        shutil.copytree(ROOT / 'wfb_ng', self.source / 'wfb_ng',
                        ignore=shutil.ignore_patterns('__pycache__', 'build_identity.json', 'site.cfg'))
        (self.source / 'docs').mkdir()
        shutil.copyfile(ROOT / 'docs/README.md', self.source / 'docs/README.md')

    def build(self, *args, commit=COMMIT, source=None, succeeds=True):
        env = dict(os.environ, VERSION='1.0.0', OMIT_DATA_FILES='1')
        env.pop('COMMIT', None)
        if commit is not None:
            env['COMMIT'] = commit
        result = subprocess.run(
            [sys.executable, 'setup.py', *args], cwd=source or self.source,
            env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if succeeds:
            self.assertEqual(result.returncode, 0, result.stdout)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def assert_identity(self, raw, commit=COMMIT):
        self.assertEqual(json.loads(raw), {'schema_version': 1, 'commit': commit})

    def unpack_sdist(self):
        self.build('sdist')
        archive = next((self.source / 'dist').glob('*.tar.gz'))
        with tarfile.open(archive) as package:
            member = next(item for item in package.getmembers()
                          if item.name.endswith('/' + RESOURCE))
            self.assert_identity(package.extractfile(member).read())
            package.extractall(Path(self.temp.name) / 'unpacked')
        unpacked = Path(self.temp.name) / 'unpacked' / 'wfb_ng-1.0.0'
        self.assertFalse((unpacked / '.git').exists())
        return unpacked

    def test_wheel_contains_direct_package_resource_and_refreshes_commit(self):
        for commit in (COMMIT, OTHER_COMMIT):
            self.build('bdist_wheel', commit=commit)
            wheel = next((self.source / 'dist').glob('*.whl'))
            with zipfile.ZipFile(wheel) as package:
                self.assert_identity(package.read(RESOURCE), commit)
                self.assertEqual(package.namelist().count(RESOURCE), 1)
            wheel.unlink()

    def test_sdist_then_wheel_preserves_target_identity_without_git(self):
        unpacked = self.unpack_sdist()
        original = (unpacked / RESOURCE).read_bytes()
        self.build('bdist_wheel', source=unpacked)
        with zipfile.ZipFile(next((unpacked / 'dist').glob('*.whl'))) as package:
            self.assertEqual(package.read(RESOURCE), original)
            self.assert_identity(package.read(RESOURCE))

    def test_missing_or_invalid_commit_fails_before_generating_resources(self):
        for commit in (None, '', 'release', 'a' * 39, 'a' * 41, 'A' * 40,
                       'g' * 40, COMMIT + '\n'):
            with self.subTest(commit=commit):
                result = self.build('bdist_wheel', commit=commit, succeeds=False)
                self.assertIn('COMMIT must be', result.stdout)
                self.assertFalse((self.source / RESOURCE).exists())
                self.assertFalse((self.source / 'wfb_ng/conf/site.cfg').exists())

    def test_sdist_rejects_missing_invalid_or_different_commit(self):
        unpacked = self.unpack_sdist()
        original = (unpacked / RESOURCE).read_bytes()
        for commit in (None, 'release', OTHER_COMMIT):
            with self.subTest(commit=commit):
                self.build('bdist_wheel', source=unpacked, commit=commit, succeeds=False)
                self.assertEqual((unpacked / RESOURCE).read_bytes(), original)
        self.assertFalse((unpacked / 'build').exists())

    def test_sdist_rejects_missing_or_invalid_packaged_identity(self):
        unpacked = self.unpack_sdist()
        path = unpacked / RESOURCE
        for identity in (None, b'{', b'[]',
                         json.dumps({'schema_version': True, 'commit': COMMIT}).encode(),
                         json.dumps({'schema_version': 2, 'commit': COMMIT}).encode(),
                         json.dumps({'schema_version': 1, 'commit': OTHER_COMMIT}).encode()):
            with self.subTest(identity=identity):
                if identity is None:
                    path.unlink()
                else:
                    path.write_bytes(identity)
                self.build('bdist_wheel', source=unpacked, succeeds=False)
        self.assertFalse((unpacked / 'build').exists())


if __name__ == '__main__':
    unittest.main()
