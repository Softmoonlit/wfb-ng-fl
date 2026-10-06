#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试 scripts/sync_to_server.sh WSL/Linux Bash 增量同步脚本。"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SH_SCRIPT = REPO_ROOT / "scripts" / "sync_to_server.sh"


class TestSyncToServerSh(unittest.TestCase):
    def test_script_file_exists_and_executable(self):
        self.assertTrue(SH_SCRIPT.is_file(), f"Shell 脚本应存在: {SH_SCRIPT}")
        self.assertTrue(os.access(SH_SCRIPT, os.X_OK), f"Shell 脚本应具备可执行权限: {SH_SCRIPT}")

    def _run_sh(self, args):
        res = subprocess.run([
            str(SH_SCRIPT),
            *args
        ], capture_output=True, text=True)
        return res.returncode, res.stdout, res.stderr

    def test_help_option(self):
        rc, stdout, stderr = self._run_sh(["--help"])
        self.assertEqual(rc, 0, f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}")
        self.assertIn("WFB-FL", stdout)
        self.assertIn("--dry-run", stdout)

    def test_missing_server_fails(self):
        rc, stdout, stderr = self._run_sh(["--config", "/non_existent_file.conf", "--dry-run"])
        self.assertNotEqual(rc, 0)
        self.assertIn("Server", stdout + stderr)

    def test_dry_run_with_server(self):
        rc, stdout, stderr = self._run_sh(["-s", "vm0", "--dry-run"])
        self.assertEqual(rc, 0, f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}")
        self.assertIn("[DRY-RUN]", stdout)
        self.assertIn("vm0", stdout)

    def test_dry_run_with_ip_and_user(self):
        rc, stdout, stderr = self._run_sh(["-s", "192.168.1.188", "-u", "virtuser", "--dry-run"])
        self.assertEqual(rc, 0, f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}")
        self.assertIn("virtuser@192.168.1.188", stdout)

    def test_dry_run_with_custom_config(self):
        with tempfile.NamedTemporaryFile("w+", suffix=".conf", delete=False) as tf:
            tf.write("SERVER_HOST=192.168.1.222\nSERVER_USER=customuser\n")
            tf_path = tf.name

        try:
            rc, stdout, stderr = self._run_sh(["-c", tf_path, "--dry-run"])
            self.assertEqual(rc, 0, f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}")
            self.assertIn("customuser@192.168.1.222", stdout)
        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)

    def test_dry_run_with_uftp_zip(self):
        with tempfile.NamedTemporaryFile("w+", suffix=".zip", delete=False) as tf:
            tf.write("dummy zip content")
            tf_path = tf.name

        try:
            rc, stdout, stderr = self._run_sh(["-s", "vm0", "-z", tf_path, "--dry-run"])
            self.assertEqual(rc, 0, f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}")
            self.assertIn(tf_path, stdout)
            self.assertIn("scp", stdout)
        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)

    def test_dry_run_with_tar_method(self):
        rc, stdout, stderr = self._run_sh(["-s", "vm0", "-m", "tar", "--dry-run"])
        self.assertEqual(rc, 0, f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}")
        self.assertIn("tar 管道", stdout)
        self.assertIn("tar -xzf", stdout)

    def test_dry_run_with_rsync_no_delete(self):
        rc, stdout, stderr = self._run_sh(["-s", "vm0", "--no-delete", "--dry-run"])
        self.assertEqual(rc, 0, f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}")
        self.assertIn("rsync", stdout)
        self.assertNotIn("--delete", stdout)

    def test_invalid_method_fails(self):
        rc, stdout, stderr = self._run_sh(["-s", "vm0", "-m", "ftp", "--dry-run"])
        self.assertNotEqual(rc, 0)
        self.assertIn("不支持的传输模式", stdout + stderr)


if __name__ == "__main__":
    unittest.main()
