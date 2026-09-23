#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试 scripts/sync_to_server.ps1 Windows PowerShell 增量同步脚本。"""

import os
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PS1_SCRIPT = REPO_ROOT / "scripts" / "sync_to_server.ps1"

# 查找 PowerShell 可执行文件
POWERSHELL_BIN = None
for cand in ["pwsh", "powershell", "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"]:
    if shutil.which(cand) or (os.path.exists(cand) and os.access(cand, os.X_OK)):
        POWERSHELL_BIN = cand
        break


class TestSyncToServerPs1(unittest.TestCase):
    def test_script_file_exists(self):
        self.assertTrue(PS1_SCRIPT.is_file(), f"PowerShell 脚本应存在: {PS1_SCRIPT}")

    def _run_ps(self, args):
        res = subprocess.run([
            POWERSHELL_BIN,
            "-ExecutionPolicy", "Bypass",
            "-File", str(PS1_SCRIPT),
            *args
        ], capture_output=True)
        # Windows PowerShell 输出为宿主机代码页 (如 GBK)
        stdout = res.stdout.decode("gbk", errors="replace")
        stderr = res.stderr.decode("gbk", errors="replace")
        return res.returncode, stdout, stderr

    @unittest.skipIf(POWERSHELL_BIN is None, "未检测到 PowerShell 环境，跳过执行测试")
    def test_help_option(self):
        rc, stdout, stderr = self._run_ps(["-Help"])
        self.assertEqual(rc, 0, f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}")
        self.assertIn("WFB-FL", stdout)
        self.assertIn("-DryRun", stdout)

    @unittest.skipIf(POWERSHELL_BIN is None, "未检测到 PowerShell 环境，跳过执行测试")
    def test_missing_server_fails(self):
        rc, stdout, stderr = self._run_ps(["-ConfigFile", "non_existent_file.conf", "-DryRun"])
        self.assertNotEqual(rc, 0)
        self.assertIn("Server", stdout + stderr)

    @unittest.skipIf(POWERSHELL_BIN is None, "未检测到 PowerShell 环境，跳过执行测试")
    def test_dry_run_with_server(self):
        rc, stdout, stderr = self._run_ps(["-Server", "vm0", "-DryRun"])
        self.assertEqual(rc, 0, f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}")
        self.assertIn("[DRY-RUN]", stdout)
        self.assertIn("vm0", stdout)

    @unittest.skipIf(POWERSHELL_BIN is None, "未检测到 PowerShell 环境，跳过执行测试")
    def test_dry_run_with_ip_and_user(self):
        rc, stdout, stderr = self._run_ps(["-Server", "192.168.1.188", "-User", "virtuser", "-DryRun"])
        self.assertEqual(rc, 0, f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}")
        self.assertIn("virtuser@192.168.1.188", stdout)


if __name__ == "__main__":
    unittest.main()
