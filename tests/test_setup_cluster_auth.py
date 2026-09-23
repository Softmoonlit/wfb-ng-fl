#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试 scripts/setup_cluster_auth.sh 的互信分发、密钥生成与状态核验。"""

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts/setup_cluster_auth.sh"
CONFIG_PATH = REPO_ROOT / "cluster_nodes.conf"


def write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def make_test_conf(tmp_path: Path, client_count: int = 5) -> Path:
    clients = []
    for i in range(1, client_count + 1):
        clients.append(f'    "client{i} 192.168.1.10{i} testuser {i} 10.80.0.1{i}"')
    clients_str = "\n".join(clients)

    conf_file = tmp_path / "test_cluster.conf"
    conf_file.write_text(f"""WIRELESS_CHANNEL=157
WIRELESS_CHANNEL_WIDTH=HT40+
WIRELESS_TXPOWER_DBM=12
DOWNLINK_MCS=3
UPLINK_MCS=6
UFTP_RATE_KBPS=15000
SERVER_NODE_ID=255
SERVER_TUN_IP=10.80.0.1/24
CLIENTS=(
{clients_str}
)
""", encoding="utf-8")
    return conf_file


class TestSetupClusterAuth(unittest.TestCase):
    def test_script_exists_and_executable(self):
        self.assertTrue(SCRIPT_PATH.exists(), f"脚本 {SCRIPT_PATH} 必须存在")
        self.assertTrue(os.access(SCRIPT_PATH, os.X_OK), "脚本必须具备执行权限")

    def test_help_flag(self):
        res = subprocess.run([str(SCRIPT_PATH), "--help"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertIn("WFB-FL ARMv8 集群全局 SSH 与 Sudo 免密互信分发工具", res.stdout)
        self.assertIn("--config", res.stdout)
        self.assertIn("--password", res.stdout)
        self.assertIn("--dry-run", res.stdout)

    def test_dry_run_mode(self):
        res = subprocess.run([str(SCRIPT_PATH), "--dry-run"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"演练模式应成功退出:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}")
        self.assertIn("[DRY-RUN]", res.stdout)
        self.assertIn("集群认证互信状态看板", res.stdout)
        self.assertIn("client1", res.stdout)
        self.assertIn("PASS", res.stdout)

    def test_ssh_key_generation_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            fake_key = tmp / "test_id_rsa"
            conf = make_test_conf(tmp)

            res = subprocess.run([
                str(SCRIPT_PATH),
                "--config", str(conf),
                "--key", str(fake_key),
                "--dry-run"
            ], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0)
            self.assertIn("ssh-keygen", res.stdout)
            self.assertIn(str(fake_key), res.stdout)

    def test_ssh_key_derive_public_when_private_exists(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            fake_key = tmp / "test_id_rsa"
            conf = make_test_conf(tmp)

            # 生成一个真实私钥但不生成公钥
            subprocess.run(["ssh-keygen", "-t", "rsa", "-b", "2048", "-N", "", "-f", str(fake_key), "-q"], check=True)
            pub_key = tmp / "test_id_rsa.pub"
            if pub_key.exists():
                pub_key.unlink()  # 删除公钥，仅保留私钥

            res = subprocess.run([
                str(SCRIPT_PATH),
                "--config", str(conf),
                "--key", str(fake_key),
                "--dry-run"
            ], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0)
            self.assertIn("导出对应公钥", res.stdout)
            self.assertIn("ssh-keygen -y", res.stdout)

    def test_full_auth_distribution_mocked(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            mock_bin = tmp / "bin"
            mock_bin.mkdir(parents=True)
            call_log = tmp / "call.log"

            # Mock ssh-copy-id: 记录调用参数并模拟成功
            mock_ssh_copy_id = f"""#!/bin/sh
echo "ssh-copy-id $@" >> "{call_log}"
exit 0
"""
            write_executable(mock_bin / "ssh-copy-id", mock_ssh_copy_id)

            # Mock ping: 始终返回成功
            write_executable(mock_bin / "ping", f"""#!/bin/sh
echo "ping $@" >> "{call_log}"
exit 0
""")

            # Mock ssh:
            # 1. 验证免密 sudo: 只有当 99-wfb-nopasswd 已经被写入后，才返回成功 (0)
            # 2. 初始检测公钥是否存在: 当 ssh-copy-id 出现后返回 0
            # 3. 远程执行 sudo 命令: 记录并模拟成功
            mock_ssh = f"""#!/bin/sh
echo "ssh $@" >> "{call_log}"

if echo "$@" | grep -q "sudo -n true"; then
    if grep -q "99-wfb-nopasswd" "{call_log}" 2>/dev/null; then
        exit 0
    else
        exit 1
    fi
fi

if echo "$@" | grep -E -q "[[:space:]]true$"; then
    if grep -q "ssh-copy-id" "{call_log}" 2>/dev/null; then
        exit 0
    else
        exit 1
    fi
fi

if echo "$@" | grep -q "sudo -S"; then
    exit 0
fi

exit 0
"""
            write_executable(mock_bin / "ssh", mock_ssh)

            # 生成一个临时 SSH 密钥对
            test_key = tmp / "id_rsa"
            test_pub = tmp / "id_rsa.pub"
            test_key.write_text("fake_private_key", encoding="utf-8")
            test_pub.write_text("fake_public_key", encoding="utf-8")
            os.chmod(str(test_key), 0o600)

            conf_file = make_test_conf(tmp, client_count=5)

            env = os.environ.copy()
            env["PATH"] = f"{mock_bin}:" + env["PATH"]
            env["SKIP_NETWORK_CHECK"] = "1"

            res = subprocess.run([
                str(SCRIPT_PATH),
                "--config", str(conf_file),
                "--key", str(test_key),
                "--password", "p@$$w0rd'\"$`\\!#%^&*()"
            ], env=env, capture_output=True, text=True)

            self.assertEqual(res.returncode, 0, f"分发流程应成功完成:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}")
            self.assertIn("SSH 与 Sudo 免密互信均已成功就绪", res.stdout)
            self.assertIn("client1", res.stdout)
            self.assertIn("client5", res.stdout)

            calls = call_log.read_text(encoding="utf-8") if call_log.exists() else ""
            self.assertIn("ssh-copy-id", calls)
            self.assertIn("testuser@192.168.1.101", calls)
            self.assertIn("testuser@192.168.1.105", calls)
            self.assertIn("99-wfb-nopasswd", calls)
            self.assertIn("sudo -n true", calls)

    def test_verification_failure_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            mock_bin = tmp / "bin"
            mock_bin.mkdir(parents=True)

            # Mock ssh 使得验证阶段始终失败 (退出码 1)
            write_executable(mock_bin / "ssh", """#!/bin/sh
exit 1
""")
            write_executable(mock_bin / "ssh-copy-id", """#!/bin/sh
exit 0
""")

            test_key = tmp / "id_rsa"
            test_key.write_text("fake_private_key", encoding="utf-8")
            (tmp / "id_rsa.pub").write_text("fake_public_key", encoding="utf-8")
            os.chmod(str(test_key), 0o600)

            conf_file = make_test_conf(tmp, client_count=5)

            env = os.environ.copy()
            env["PATH"] = f"{mock_bin}:" + env["PATH"]
            env["SKIP_NETWORK_CHECK"] = "1"

            res = subprocess.run([
                str(SCRIPT_PATH),
                "--config", str(conf_file),
                "--key", str(test_key),
                "--password", "pwd"
            ], env=env, capture_output=True, text=True)

            self.assertNotEqual(res.returncode, 0, "当节点免密验证失败时退出码必须为非 0")
            self.assertIn("FAIL", res.stdout + res.stderr)


if __name__ == "__main__":
    unittest.main()
