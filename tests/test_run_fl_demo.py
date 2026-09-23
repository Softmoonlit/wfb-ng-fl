#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试 tests/real_hardware/run_fl_demo.sh 现场演示总控脚本。"""

import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_SCRIPT = REPO_ROOT / "tests/real_hardware/run_fl_demo.sh"
DEFAULT_CONF = REPO_ROOT / "cluster_nodes.conf"


def write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def make_test_conf(tmp_path: Path, channel: int = 157, client_count: int = 5) -> Path:
    clients = []
    for i in range(1, client_count + 1):
        clients.append(f'    "client{i} 192.168.1.10{i} testuser {i} 10.80.0.1{i}"')
    clients_str = "\n".join(clients)

    conf_file = tmp_path / "test_cluster.conf"
    conf_file.write_text(f"""WIRELESS_CHANNEL={channel}
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


class TestRunFLDemoScript(unittest.TestCase):
    def test_script_exists_and_executable(self):
        self.assertTrue(DEMO_SCRIPT.exists(), f"脚本 {DEMO_SCRIPT} 必须存在")
        self.assertTrue(os.access(DEMO_SCRIPT, os.X_OK), "脚本必须具备执行权限")

    def test_help_flag(self):
        res = subprocess.run([str(DEMO_SCRIPT), "--help"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertIn("现场演示总控脚本与多维指标看板", res.stdout)
        self.assertIn("all", res.stdout)
        self.assertIn("downlink", res.stdout)
        self.assertIn("uplink", res.stdout)
        self.assertIn("--file", res.stdout)
        self.assertIn("--config", res.stdout)
        self.assertIn("--dry-run", res.stdout)

    def test_dry_run_all_flow(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            conf = make_test_conf(tmp, channel=157, client_count=5)
            res = subprocess.run([
                str(DEMO_SCRIPT), "all",
                "--config", str(conf),
                "--work-dir", str(tmp / "work"),
                "--dry-run"
            ], capture_output=True, text=True)

            self.assertEqual(res.returncode, 0, f"STDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}")
            self.assertIn("步骤 1: 读取并解析集群配置文件", res.stdout)
            self.assertIn("步骤 2: 探测 Client 在线状态与容错集合发现", res.stdout)
            self.assertIn("步骤 3: 准备无线空口网卡", res.stdout)
            self.assertIn("步骤 4: 启动底层 WFB Mesh 组播与调度信道", res.stdout)
            self.assertIn("步骤 5: 执行下行大文件组播广播分发", res.stdout)
            self.assertIn("下行大文件组播传输指标看板", res.stdout)
            self.assertIn("步骤 6: 执行上行受控大文件回传", res.stdout)
            self.assertIn("上行受控调度回传指标看板", res.stdout)
            self.assertIn("步骤 7: WFB-FL 现场演示全流程执行完毕", res.stdout)

    def test_dry_run_downlink_only(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            conf = make_test_conf(tmp, channel=157, client_count=5)
            res = subprocess.run([
                str(DEMO_SCRIPT), "downlink",
                "--config", str(conf),
                "--work-dir", str(tmp / "work"),
                "--dry-run"
            ], capture_output=True, text=True)

            self.assertEqual(res.returncode, 0)
            self.assertIn("下行大文件组播传输指标看板", res.stdout)
            self.assertNotIn("上行受控调度回传指标看板", res.stdout)

    def test_dry_run_uplink_only(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            conf = make_test_conf(tmp, channel=157, client_count=5)
            work = tmp / "work"
            ref_model = work / "source/model_40mb.bin"
            ref_model.parent.mkdir(parents=True)
            ref_model.write_bytes(b"DEMO_REF_MODEL" * 1024)

            res = subprocess.run([
                str(DEMO_SCRIPT), "uplink",
                "--config", str(conf),
                "--file", str(ref_model),
                "--work-dir", str(work),
                "--dry-run"
            ], capture_output=True, text=True)

            self.assertEqual(res.returncode, 0, f"STDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}")
            self.assertNotIn("下行大文件组播传输指标看板", res.stdout)
            self.assertIn("上行受控调度回传指标看板", res.stdout)

    def test_dry_run_clean_command(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            conf = make_test_conf(tmp, channel=157, client_count=5)
            res = subprocess.run([
                str(DEMO_SCRIPT), "clean",
                "--config", str(conf),
                "--dry-run"
            ], capture_output=True, text=True)

            self.assertEqual(res.returncode, 0)
            self.assertIn("集群残留进程与 TUN 接口已彻底清理！", res.stdout)
            self.assertNotIn("步骤 3", res.stdout)

    def test_custom_file_option(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            conf = make_test_conf(tmp, channel=157, client_count=5)
            custom_file = tmp / "my_custom_model.bin"
            custom_file.write_bytes(b"HELLO_WFB_FL" * 1024)

            res = subprocess.run([
                str(DEMO_SCRIPT), "downlink",
                "--config", str(conf),
                "--file", str(custom_file),
                "--work-dir", str(tmp / "work"),
                "--dry-run"
            ], capture_output=True, text=True)

            self.assertEqual(res.returncode, 0)
            self.assertIn("my_custom_model.bin", res.stdout)

    def test_channel_161_strictly_rejected(self):
        """测试信道 161 触发安全黑名单拦截并 fail-closed。"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            bad_conf = make_test_conf(tmp, channel=161, client_count=5)

            res = subprocess.run([
                str(DEMO_SCRIPT), "all",
                "--config", str(bad_conf),
                "--dry-run"
            ], capture_output=True, text=True)

            self.assertNotEqual(res.returncode, 0)
            combined = res.stdout + res.stderr
            self.assertIn("161", combined)

    def test_offline_client_tolerance_and_warning(self):
        """测试在非 dry-run 下，部分节点离线时给出明显黄色告警并安全跳过。"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            conf = make_test_conf(tmp, channel=157, client_count=5)
            mock_bin = tmp / "bin"
            mock_bin.mkdir(parents=True)

            # Mock ssh: 192.168.1.102 离线 (exit 255), 其他在线 (exit 0)
            mock_ssh = """#!/bin/bash
for arg in "$@"; do
    if [[ "$arg" =~ 192.168.1.102 ]]; then
        exit 255
    fi
done
# 模拟其他命令执行
if echo "$@" | grep -q "iw dev"; then
    echo "Interface wlxfc221c500a88"
fi
exit 0
"""
            write_executable(mock_bin / "ssh", mock_ssh)

            # Mock iw 本机探测
            mock_iw = """#!/bin/bash
echo "Interface wlxbcec23372588"
exit 0
"""
            write_executable(mock_bin / "iw", mock_iw)

            # Mock sudo
            mock_sudo = """#!/bin/bash
exit 0
"""
            write_executable(mock_bin / "sudo", mock_sudo)

            env = os.environ.copy()
            env["PATH"] = f"{mock_bin}:" + env["PATH"]

            res = subprocess.run([
                str(DEMO_SCRIPT), "clean",
                "--config", str(conf),
                "--work-dir", str(tmp / "work")
            ], env=env, capture_output=True, text=True)

            self.assertEqual(res.returncode, 0, f"STDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}")
            combined = res.stdout + res.stderr
            self.assertIn("WARN", combined)
            self.assertIn("192.168.1.102", combined)
            self.assertIn("跳过离线 1 台", combined)
            self.assertIn("在线 4 台", combined)

    def test_all_clients_offline_fails(self):
        """测试所有 Client 均离线时，总控脚本拒绝继续并返回非 0 退出码。"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            conf = make_test_conf(tmp, channel=157, client_count=5)
            mock_bin = tmp / "bin"
            mock_bin.mkdir(parents=True)

            # Mock ssh 始终失败 (全离线)
            mock_ssh = """#!/bin/bash
exit 255
"""
            write_executable(mock_bin / "ssh", mock_ssh)

            env = os.environ.copy()
            env["PATH"] = f"{mock_bin}:" + env["PATH"]

            res = subprocess.run([
                str(DEMO_SCRIPT), "all",
                "--config", str(conf),
                "--work-dir", str(tmp / "work")
            ], env=env, capture_output=True, text=True)

            self.assertNotEqual(res.returncode, 0)
            combined = res.stdout + res.stderr
            self.assertIn("所有 Client 节点均离线", combined)

    def test_server_no_wlx_fails(self):
        """测试在真实模式下 Server 未发现 wlx* 空口网卡时 fail-closed。"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            conf = make_test_conf(tmp, channel=157, client_count=5)
            mock_bin = tmp / "bin"
            mock_bin.mkdir(parents=True)

            # Mock ssh 成功
            mock_ssh = """#!/bin/bash
exit 0
"""
            write_executable(mock_bin / "ssh", mock_ssh)

            # Mock iw: 无 wlx 网卡
            mock_iw = """#!/bin/bash
echo "Interface wlan0"
exit 0
"""
            write_executable(mock_bin / "iw", mock_iw)

            # Mock sudo
            mock_sudo = """#!/bin/bash
exit 0
"""
            write_executable(mock_bin / "sudo", mock_sudo)

            env = os.environ.copy()
            env["PATH"] = f"{mock_bin}:" + env["PATH"]

            res = subprocess.run([
                str(DEMO_SCRIPT), "downlink",
                "--config", str(conf),
                "--work-dir", str(tmp / "work")
            ], env=env, capture_output=True, text=True)

            self.assertNotEqual(res.returncode, 0)
            combined = res.stdout + res.stderr
            self.assertIn("必须恰好存在一个 wlx* 无线网卡", combined)


    def test_uplink_missing_ref_file_fails(self):
        """测试在无原始参考模型文件时直接执行 uplink 报阻断错误。"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            conf = make_test_conf(tmp, channel=157, client_count=5)
            res = subprocess.run([
                str(DEMO_SCRIPT), "uplink",
                "--config", str(conf),
                "--work-dir", str(tmp / "empty_work"),
                "--dry-run"
            ], capture_output=True, text=True)

            self.assertNotEqual(res.returncode, 0)
            combined = res.stdout + res.stderr
            self.assertIn("未找到原始参考模型文件", combined)

    def test_uplink_missing_client_file_fails(self):
        """测试在非 dry-run 下，Client 缺少待回传模型文件时拒绝 fallback 并阻断退出。"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            conf = make_test_conf(tmp, channel=157, client_count=5)
            mock_bin = tmp / "bin"
            mock_bin.mkdir(parents=True)

            # 创建本地参考模型文件
            work = tmp / "work"
            ref_model = work / "source/model_40mb.bin"
            ref_model.parent.mkdir(parents=True)
            ref_model.write_bytes(b"TEST_MODEL_BYTES" * 1024)

            # Mock ssh: 探测在线通过，但检查模型文件返回 0 (不存在)
            mock_ssh = """#!/bin/bash
if echo "$@" | grep -q "model_40mb.bin"; then
    echo "0"
    exit 0
fi
if echo "$@" | grep -q "iw dev.*info"; then
    echo "type monitor"
    exit 0
fi
if echo "$@" | grep -q "iw dev"; then
    echo "wlxfc221c500a88"
    exit 0
fi
if echo "$@" | grep -q "ip link show"; then
    exit 0
fi
exit 0
"""
            write_executable(mock_bin / "ssh", mock_ssh)

            # Mock iw 本机探测
            mock_iw = """#!/bin/bash
if echo "$@" | grep -q "info"; then
    echo "type monitor"
    exit 0
fi
echo "Interface wlxbcec23372588"
exit 0
"""
            write_executable(mock_bin / "iw", mock_iw)

            # Mock ip
            mock_ip = """#!/bin/bash
exit 0
"""
            write_executable(mock_bin / "ip", mock_ip)

            # Mock wfb_v6_uplink
            mock_wfb = """#!/bin/bash
while true; do sleep 10; done
"""
            write_executable(mock_bin / "wfb_v6_uplink", mock_wfb)

            # Mock sudo
            mock_sudo = """#!/bin/bash
if [ "$1" = "bash" ]; then
    shift
    exec bash "$@"
fi
exit 0
"""
            write_executable(mock_bin / "sudo", mock_sudo)

            env = os.environ.copy()
            env["PATH"] = f"{mock_bin}:" + env["PATH"]

            res = subprocess.run([
                str(DEMO_SCRIPT), "uplink",
                "--config", str(conf),
                "--file", str(ref_model),
                "--work-dir", str(work)
            ], env=env, capture_output=True, text=True)

            self.assertNotEqual(res.returncode, 0)
            combined = res.stdout + res.stderr
            self.assertIn("缺少待回传模型文件", combined)

    def test_dashboard_failure_returns_nonzero(self):
        """测试当哈希校验不通过时，render-downlink 与 render-uplink 返回非 0 退出码。"""
        metrics_script = REPO_ROOT / "tests/real_hardware/fl_demo_metrics.py"
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            bad_dl_json = tmp / "bad_dl.json"
            bad_dl_json.write_text("""{
  "source_file": "/tmp/model.bin",
  "size_bytes": 100,
  "source_sha256": "correct_hash",
  "clients": [
    {"role": "client1", "size_bytes": 100, "sha256": "corrupted_hash"}
  ]
}""", encoding="utf-8")

            res_dl = subprocess.run([
                sys.executable, str(metrics_script),
                "render-downlink", "--json", str(bad_dl_json)
            ], capture_output=True, text=True)
            self.assertNotEqual(res_dl.returncode, 0)
            self.assertIn("[失败]", res_dl.stdout)

            bad_ul_json = tmp / "bad_ul.json"
            bad_ul_json.write_text("""{
  "server_address": "10.80.0.1:8080",
  "total_bytes": 100,
  "expected_sha256": "correct_hash",
  "clients": [
    {"role": "client1", "size_bytes": 100, "sha256": "corrupted_hash"}
  ]
}""", encoding="utf-8")

            res_ul = subprocess.run([
                sys.executable, str(metrics_script),
                "render-uplink", "--json", str(bad_ul_json)
            ], capture_output=True, text=True)
            self.assertNotEqual(res_ul.returncode, 0)
            self.assertIn("[失败]", res_ul.stdout)


    def test_downlink_hash_mismatch_exits_nonzero(self):
        """测试下行若 Client 哈希不匹配，总控脚本返回非 0 并报错退出。"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            conf = make_test_conf(tmp, channel=157, client_count=5)
            mock_bin = tmp / "bin"
            mock_bin.mkdir(parents=True)
            work = tmp / "work"

            # 创建本地源文件
            src_file = work / "source/model_40mb.bin"
            src_file.parent.mkdir(parents=True)
            src_file.write_bytes(b"ORIGINAL_BYTES" * 1024)

            # Mock ssh: 探测通过，网卡通过，落盘通过，但 sha256 损坏
            mock_ssh = """#!/bin/bash
if echo "$@" | grep -q "sha256sum"; then
    echo "corrupted_hash_value  /path"
    exit 0
fi
if echo "$@" | grep -q "stat -c%s"; then
    exit 0
fi
if echo "$@" | grep -q "iw dev.*info"; then
    echo "type monitor"
    exit 0
fi
if echo "$@" | grep -q "iw dev"; then
    echo "wlxfc221c500a88"
    exit 0
fi
if echo "$@" | grep -q "ip link show"; then
    exit 0
fi
exit 0
"""
            write_executable(mock_bin / "ssh", mock_ssh)

            # Mock iw 本机
            mock_iw = """#!/bin/bash
if echo "$@" | grep -q "info"; then
    echo "type monitor"
    exit 0
fi
echo "Interface wlxbcec23372588"
exit 0
"""
            write_executable(mock_bin / "iw", mock_iw)

            # Mock ip
            mock_ip = """#!/bin/bash
exit 0
"""
            write_executable(mock_bin / "ip", mock_ip)

            # Mock uftp
            mock_uftp = """#!/bin/bash
exit 0
"""
            write_executable(mock_bin / "uftp", mock_uftp)

            # Mock wfb_v6_uplink
            mock_wfb = """#!/bin/bash
while true; do sleep 10; done
"""
            write_executable(mock_bin / "wfb_v6_uplink", mock_wfb)

            # Mock sudo
            mock_sudo = """#!/bin/bash
if [ "$1" = "bash" ]; then
    shift
    exec bash "$@"
fi
exit 0
"""
            write_executable(mock_bin / "sudo", mock_sudo)

            env = os.environ.copy()
            env["PATH"] = f"{mock_bin}:" + env["PATH"]

            res = subprocess.run([
                str(DEMO_SCRIPT), "downlink",
                "--config", str(conf),
                "--file", str(src_file),
                "--work-dir", str(work)
            ], env=env, capture_output=True, text=True)

            self.assertNotEqual(res.returncode, 0)
            combined = res.stdout + res.stderr
            self.assertIn("下行传输校验失败", combined)


if __name__ == "__main__":
    unittest.main()

