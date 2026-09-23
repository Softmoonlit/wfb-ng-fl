#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试 scripts/deploy_node.sh 的核心行为与集成流程。"""

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts/deploy_node.sh"


def write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class TestDeployNodeScript(unittest.TestCase):
    def test_script_exists_and_executable(self):
        self.assertTrue(SCRIPT_PATH.exists(), f"脚本 {SCRIPT_PATH} 必须存在")
        self.assertTrue(os.access(SCRIPT_PATH, os.X_OK), "脚本必须具备可执行权限")

    def test_help_flag(self):
        res = subprocess.run([str(SCRIPT_PATH), "--help"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertIn("用法", res.stdout)
        self.assertIn("ARMv8", res.stdout)
        self.assertIn("--dry-run", res.stdout)

    def test_dry_run_mode(self):
        res = subprocess.run([str(SCRIPT_PATH), "--dry-run"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertIn("[DRY-RUN]", res.stdout)
        self.assertIn("libsodium-dev", res.stdout)
        self.assertIn("libpcap-dev", res.stdout)
        self.assertIn("dkms-install.sh", res.stdout)
        self.assertNotIn("dkms-remove.sh", res.stdout)
        self.assertIn("rfkill unblock all", res.stdout)

    def test_full_pipeline_with_mocked_system(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            mock_bin = tmp / "bin"
            mock_etc = tmp / "etc"
            mock_usr = tmp / "usr"
            mock_bin.mkdir(parents=True)
            mock_etc.mkdir(parents=True)
            mock_usr.mkdir(parents=True)

            call_log = tmp / "call.log"

            # Mock log helper
            def make_logger_mock(cmd_name):
                return f"""#!/bin/sh
echo "{cmd_name} $@" >> "{call_log}"
exit 0
"""

            # Mock tools
            write_executable(mock_bin / "apt-get", make_logger_mock("apt-get"))
            write_executable(mock_bin / "rfkill", make_logger_mock("rfkill"))
            write_executable(mock_bin / "sysctl", make_logger_mock("sysctl"))
            write_executable(mock_bin / "systemctl", make_logger_mock("systemctl"))
            write_executable(mock_bin / "modprobe", make_logger_mock("modprobe"))
            write_executable(mock_bin / "dkms", make_logger_mock("dkms"))
            write_executable(mock_bin / "git", make_logger_mock("git"))
            write_executable(mock_bin / "make", make_logger_mock("make"))
            write_executable(mock_bin / "unzip", make_logger_mock("unzip"))
            write_executable(mock_bin / "gcc", make_logger_mock("gcc"))

            # Mock dummy fake targets for make build_v6 & install
            fake_fake_zip = tmp / "uftp_src-5.0.3.zip"
            fake_fake_zip.touch()

            # Fake rtl8812au dir with dkms-install.sh
            fake_driver_dir = tmp / "rtl8812au"
            fake_driver_dir.mkdir()
            write_executable(fake_driver_dir / "dkms-install.sh", make_logger_mock("dkms-install.sh"))

            # Fake make install_v8 creating commands in mock_usr/bin
            fake_bin_dir = mock_usr / "bin"
            fake_bin_dir.mkdir(parents=True)
            for bin_name in ["wfb-fl-server", "wfb-fl-client", "wfb_v6_uplink", "uftp", "uftpd"]:
                write_executable(fake_bin_dir / bin_name, f"#!/bin/sh\necho {bin_name}")

            env = os.environ.copy()
            env["PATH"] = f"{mock_bin}:{fake_bin_dir}:" + env["PATH"]
            env["SYSCONFDIR"] = str(mock_etc)
            env["PREFIX"] = str(mock_usr)
            env["UFTP_SRC_ZIP"] = str(fake_fake_zip)
            env["RTL8812AU_DIR"] = str(fake_driver_dir)
            env["TARGET_USER"] = "testuser"
            env["SKIP_ROOT_CHECK"] = "1"
            env["SKIP_BUILD_WFB"] = "1"  # Skip building actual C++ uplink in test

            res = subprocess.run([str(SCRIPT_PATH)], env=env, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"部署脚本执行失败:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}")

            calls = call_log.read_text(encoding="utf-8") if call_log.exists() else ""

            # 验证各关键命令是否被执行
            self.assertIn("apt-get", calls)
            self.assertIn("libsodium-dev", calls)
            self.assertIn("libpcap-dev", calls)
            self.assertIn("libssl-dev", calls)
            self.assertIn("dkms-install.sh", calls)
            self.assertNotIn("dkms-remove.sh", calls)
            self.assertIn("rfkill unblock all", calls)

            # 验证 NetworkManager 配置已写入
            nm_conf = mock_etc / "NetworkManager/conf.d/wfb-unmanaged.conf"
            self.assertTrue(nm_conf.exists(), "NetworkManager 忽略规则文件必须生成")
            self.assertIn("unmanaged-devices=interface-name:wlx*", nm_conf.read_text(encoding="utf-8"))

            # 验证 sysctl 配置已拷贝
            sysctl_conf = mock_etc / "sysctl.d/98-wifibroadcast.conf"
            self.assertTrue(sysctl_conf.exists(), "sysctl 配置文件必须生成")

            # 验证 sudoers 规则已生成且包含 NOPASSWD
            sudoers_file = mock_etc / "sudoers.d/99-wfb-nopasswd"
            self.assertTrue(sudoers_file.exists(), "sudoers 规则文件必须生成")
            self.assertIn("testuser ALL=(ALL) NOPASSWD: ALL", sudoers_file.read_text(encoding="utf-8"))

    def test_root_check_enforced(self):
        # 如果当前用户不是 root 且未设置 SKIP_ROOT_CHECK，执行应失败
        if os.getuid() != 0:
            env = os.environ.copy()
            env.pop("SKIP_ROOT_CHECK", None)
            env.pop("DRY_RUN", None)
            res = subprocess.run([str(SCRIPT_PATH)], env=env, capture_output=True, text=True)
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("root", res.stderr + res.stdout)

    def test_check_only_flag(self):
        res = subprocess.run([str(SCRIPT_PATH), "--check-only"], capture_output=True, text=True)
        # --check-only 会做验证输出，只要返回即可
        self.assertIn("安装结果最终核验", res.stdout)


if __name__ == "__main__":
    unittest.main()
