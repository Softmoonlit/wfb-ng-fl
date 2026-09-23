#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试 scripts/deploy_cluster.sh 的集群批量部署与就绪核验编排。"""

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts/deploy_cluster.sh"
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


class TestDeployClusterScript(unittest.TestCase):
    def test_script_exists_and_executable(self):
        self.assertTrue(SCRIPT_PATH.exists(), f"脚本 {SCRIPT_PATH} 必须存在")
        self.assertTrue(os.access(SCRIPT_PATH, os.X_OK), "脚本必须具备执行权限")

    def test_help_flag(self):
        res = subprocess.run([str(SCRIPT_PATH), "--help"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertIn("集群一键批量部署与环境就绪验证编排器", res.stdout)
        self.assertIn("--config", res.stdout)
        self.assertIn("--dry-run", res.stdout)
        self.assertIn("--check-only", res.stdout)

    def test_dry_run_mode(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            conf = make_test_conf(tmp, 5)

            res = subprocess.run([
                str(SCRIPT_PATH),
                "--config", str(conf),
                "--dry-run"
            ], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"演练模式应成功退出:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}")
            self.assertIn("[DRY-RUN]", res.stdout)
            self.assertIn("WFB-FL 集群部署与环境就绪报告", res.stdout)
            self.assertIn("client1", res.stdout)
            self.assertIn("client2", res.stdout)
            self.assertIn("client3", res.stdout)

    def test_offline_nodes_warn_and_skip(self):
        """测试部分节点离线时给出明显黄色告警并安全跳过，在线节点正常执行。"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            conf = make_test_conf(tmp, 5)
            mock_bin = tmp / "bin"
            mock_bin.mkdir(parents=True)
            call_log = tmp / "call.log"

            # Mock ssh: 192.168.1.102 离线 (exit 255), 其他在线 (exit 0)
            mock_ssh = f"""#!/bin/bash
echo "ssh $@" >> "{call_log}"
for arg in "$@"; do
    if [[ "$arg" =~ 192.168.1.102 ]]; then
        exit 255
    fi
done
# 模拟就绪检测输出
if echo "$@" | grep -q "bash -s"; then
    echo "VERIFY_RESULT: cmds_missing=0 driver=LOADED nic=wlx0013ef123456"
fi
exit 0
"""
            write_executable(mock_bin / "ssh", mock_ssh)

            # Mock rsync: 始终成功
            mock_rsync = f"""#!/bin/bash
echo "rsync $@" >> "{call_log}"
exit 0
"""
            write_executable(mock_bin / "rsync", mock_rsync)

            env = os.environ.copy()
            env["PATH"] = f"{mock_bin}:" + env["PATH"]
            log_dir = tmp / "logs"

            res = subprocess.run([
                str(SCRIPT_PATH),
                "--config", str(conf),
                "--log-dir", str(log_dir)
            ], env=env, capture_output=True, text=True)

            # 节点 2 离线应给出 WARN 警告
            combined = res.stdout + res.stderr
            self.assertIn("WARN", combined)
            self.assertIn("192.168.1.102", combined)
            self.assertIn("跳过", combined)

            # 表格中应包含各节点并明确标识离线状态
            self.assertIn("client2", res.stdout)
            self.assertIn("OFFLINE", res.stdout)

            # 在线节点 client1 和 client3 应正常完成
            self.assertIn("client1", res.stdout)
            self.assertIn("client3", res.stdout)
            self.assertEqual(res.returncode, 0)

    def test_all_nodes_offline_fails_safely(self):
        """测试所有节点均离线时，输出告警并安全报错退出。"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            conf = make_test_conf(tmp, 5)
            mock_bin = tmp / "bin"
            mock_bin.mkdir(parents=True)

            mock_ssh = """#!/bin/bash
exit 255
"""
            write_executable(mock_bin / "ssh", mock_ssh)

            env = os.environ.copy()
            env["PATH"] = f"{mock_bin}:" + env["PATH"]

            res = subprocess.run([
                str(SCRIPT_PATH),
                "--config", str(conf)
            ], env=env, capture_output=True, text=True)

            self.assertNotEqual(res.returncode, 0)
            combined = res.stdout + res.stderr
            self.assertIn("WARN", combined)
            self.assertIn("所有 Client 节点均离线", combined)

    def test_full_pipeline_with_mocked_sync_deploy_verify(self):
        """测试完整执行链路：代码与 UFTP 同步、远程 deploy_node.sh 执行并捕获日志、就绪核验与报表输出。"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            conf = make_test_conf(tmp, 5)
            mock_bin = tmp / "bin"
            mock_bin.mkdir(parents=True)
            call_log = tmp / "call.log"
            log_dir = tmp / "deploy_logs"

            # 创建假 UFTP 压缩包
            fake_uftp = tmp / "uftp_src-5.0.3.zip"
            fake_uftp.write_text("fake uftp zip", encoding="utf-8")

            # Mock ssh
            mock_ssh = f"""#!/bin/bash
echo "ssh $@" >> "{call_log}"
if echo "$@" | grep -q "deploy_node.sh"; then
    echo "[STEP 1] apt ok"
    echo "[STEP 2] driver ok"
    echo "[STEP 8] PASS all"
    exit 0
fi
if echo "$@" | grep -q "bash -s"; then
    echo "VERIFY_RESULT: cmds_missing=0 driver=LOADED nic=wlx0013ef123456"
    exit 0
fi
exit 0
"""
            write_executable(mock_bin / "ssh", mock_ssh)

            # Mock rsync
            mock_rsync = f"""#!/bin/bash
echo "rsync $@" >> "{call_log}"
exit 0
"""
            write_executable(mock_bin / "rsync", mock_rsync)

            env = os.environ.copy()
            env["PATH"] = f"{mock_bin}:" + env["PATH"]
            env["UFTP_SRC_ZIP"] = str(fake_uftp)

            res = subprocess.run([
                str(SCRIPT_PATH),
                "--config", str(conf),
                "--log-dir", str(log_dir),
                "--remote-dir", "projects/wfb-ng-fl"
            ], env=env, capture_output=True, text=True)

            self.assertEqual(res.returncode, 0, f"部署脚本应成功完成:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}")

            # 验证日志文件生成并捕获输出
            self.assertTrue(log_dir.exists(), "日志目录必须生成")
            log1 = log_dir / "client1_192.168.1.101.log"
            log2 = log_dir / "client2_192.168.1.102.log"
            self.assertTrue(log1.exists(), f"客户端日志文件 {log1} 必须存在")
            self.assertTrue(log2.exists(), f"客户端日志文件 {log2} 必须存在")
            self.assertIn("deploy_node.sh", log1.read_text(encoding="utf-8"))

            # 验证表格看板生成
            self.assertIn("WFB-FL 集群部署与环境就绪报告", res.stdout)
            self.assertIn("client1", res.stdout)
            self.assertIn("client2", res.stdout)
            self.assertIn("SUCCESS", res.stdout)
            self.assertIn("READY", res.stdout)

    def test_check_only_mode(self):
        """测试 --check-only 模式直接跳过同步与部署，执行就绪核验。"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            conf = make_test_conf(tmp, 5)
            mock_bin = tmp / "bin"
            mock_bin.mkdir(parents=True)
            call_log = tmp / "call.log"

            mock_ssh = f"""#!/bin/bash
echo "ssh $@" >> "{call_log}"
if echo "$@" | grep -q "bash -s"; then
    echo "VERIFY_RESULT: cmds_missing=0 driver=LOADED nic=wlx0013ef123456"
fi
exit 0
"""
            write_executable(mock_bin / "ssh", mock_ssh)

            env = os.environ.copy()
            env["PATH"] = f"{mock_bin}:" + env["PATH"]

            res = subprocess.run([
                str(SCRIPT_PATH),
                "--config", str(conf),
                "--check-only"
            ], env=env, capture_output=True, text=True)

            self.assertEqual(res.returncode, 0)
            calls = call_log.read_text(encoding="utf-8") if call_log.exists() else ""
            self.assertNotIn("deploy_node.sh", calls, "check-only 模式不应调用 deploy_node.sh")
            self.assertIn("就绪报告", res.stdout)

    def test_serial_mode_and_passthrough_flags(self):
        """测试串行模式与 --skip-driver, --skip-apt 参数透传。"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            conf = make_test_conf(tmp, 5)
            mock_bin = tmp / "bin"
            mock_bin.mkdir(parents=True)
            call_log = tmp / "call.log"

            mock_ssh = f"""#!/bin/bash
echo "ssh $@" >> "{call_log}"
if echo "$@" | grep -q "deploy_node.sh"; then
    echo "[STEP 1] apt skipped"
    exit 0
fi
if echo "$@" | grep -q "bash -s"; then
    echo "VERIFY_RESULT: cmds_missing=0 driver=LOADED nic=wlx0013ef123456"
fi
exit 0
"""
            write_executable(mock_bin / "ssh", mock_ssh)
            write_executable(mock_bin / "rsync", f"#!/bin/bash\necho rsync >> '{call_log}'\nexit 0\n")

            env = os.environ.copy()
            env["PATH"] = f"{mock_bin}:" + env["PATH"]

            res = subprocess.run([
                str(SCRIPT_PATH),
                "--config", str(conf),
                "--serial",
                "--skip-driver",
                "--skip-apt",
                "--log-dir", str(tmp / "logs")
            ], env=env, capture_output=True, text=True)

            self.assertEqual(res.returncode, 0)
            calls = call_log.read_text(encoding="utf-8")
            self.assertIn("--skip-driver", calls)
            self.assertIn("--skip-apt", calls)

    def test_deploy_failure_exits_with_error(self):
        """测试远程节点部署失败时，捕获错误日志并在最终退出时返回非 0 状态码。"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            conf = make_test_conf(tmp, 5)
            mock_bin = tmp / "bin"
            mock_bin.mkdir(parents=True)
            log_dir = tmp / "logs"

            mock_ssh = """#!/bin/bash
if echo "$@" | grep -q "deploy_node.sh"; then
    echo "APT package install failed!" >&2
    exit 1
fi
if echo "$@" | grep -q "bash -s"; then
    echo "VERIFY_RESULT: cmds_missing=2 driver=NOT_LOADED nic=NONE"
fi
exit 0
"""
            write_executable(mock_bin / "ssh", mock_ssh)
            write_executable(mock_bin / "rsync", "#!/bin/bash\nexit 0\n")

            env = os.environ.copy()
            env["PATH"] = f"{mock_bin}:" + env["PATH"]

            res = subprocess.run([
                str(SCRIPT_PATH),
                "--config", str(conf),
                "--log-dir", str(log_dir)
            ], env=env, capture_output=True, text=True)

            self.assertNotEqual(res.returncode, 0)
            self.assertIn("FAILED", res.stdout)
            self.assertIn("存在", res.stdout + res.stderr)
            self.assertIn("部署失败", res.stdout + res.stderr)

    def test_client_readiness_failure_causes_nonzero_exit(self):
        """测试远程节点部署虽然返回 0，但核验发现关键命令缺失时，判定为硬性就绪故障并退出非 0。"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            conf = make_test_conf(tmp, 5)
            mock_bin = tmp / "bin"
            mock_bin.mkdir(parents=True)
            log_dir = tmp / "logs"

            mock_ssh = """#!/bin/bash
if echo "$@" | grep -q "deploy_node.sh"; then
    exit 0
fi
if echo "$@" | grep -q "bash -s"; then
    # 模拟关键命令缺失
    echo "VERIFY_RESULT: cmds_missing=2 driver=LOADED nic=wlx0013ef123456"
fi
exit 0
"""
            write_executable(mock_bin / "ssh", mock_ssh)
            write_executable(mock_bin / "rsync", "#!/bin/bash\nexit 0\n")

            env = os.environ.copy()
            env["PATH"] = f"{mock_bin}:" + env["PATH"]

            res = subprocess.run([
                str(SCRIPT_PATH),
                "--config", str(conf),
                "--log-dir", str(log_dir)
            ], env=env, capture_output=True, text=True)

            self.assertNotEqual(res.returncode, 0)
            self.assertIn("CMD_MISSING", res.stdout)
            self.assertIn("硬性就绪故障", res.stdout + res.stderr)

    def test_ssh_verification_transport_failure_reports_check_fail(self):
        """测试核验阶段 SSH 传输失败时，如实报告 CHECK_FAIL，绝不误报 5/5 OK。"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            conf = make_test_conf(tmp, 5)
            mock_bin = tmp / "bin"
            mock_bin.mkdir(parents=True)
            log_dir = tmp / "logs"

            # 部署成功，但在就绪检测阶段 SSH 报错断开
            mock_ssh = """#!/bin/bash
if echo "$@" | grep -q "deploy_node.sh"; then
    exit 0
fi
if echo "$@" | grep -q "bash -s"; then
    echo "SSH connection broken" >&2
    exit 255
fi
exit 0
"""
            write_executable(mock_bin / "ssh", mock_ssh)
            write_executable(mock_bin / "rsync", "#!/bin/bash\nexit 0\n")

            env = os.environ.copy()
            env["PATH"] = f"{mock_bin}:" + env["PATH"]

            res = subprocess.run([
                str(SCRIPT_PATH),
                "--config", str(conf),
                "--log-dir", str(log_dir)
            ], env=env, capture_output=True, text=True)

            self.assertNotEqual(res.returncode, 0)
            self.assertIn("CHECK_FAIL", res.stdout)
            self.assertNotIn("5/5 OK", res.stdout)


if __name__ == "__main__":
    unittest.main()
