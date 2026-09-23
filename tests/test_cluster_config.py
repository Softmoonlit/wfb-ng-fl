#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试 scripts/cluster_config.sh 的配置解析与严格校验规则。"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts/cluster_config.sh"
TEMPLATE_CONF = REPO_ROOT / "cluster_nodes.conf"


def make_conf(overrides=None, clients=None):
    params = {
        "WIRELESS_CHANNEL": "157",
        "WIRELESS_CHANNEL_WIDTH": "HT40+",
        "WIRELESS_TXPOWER_DBM": "12",
        "DOWNLINK_MCS": "3",
        "UPLINK_MCS": "6",
        "UFTP_RATE_KBPS": "15000",
        "SERVER_NODE_ID": "255",
        "SERVER_TUN_IP": "10.80.0.1/24",
    }
    if overrides:
        params.update(overrides)

    if clients is None:
        clients = [
            "client1 192.168.1.101 ubuntu 1 10.80.0.11",
            "client2 192.168.1.102 ubuntu 2 10.80.0.12",
            "client3 192.168.1.103 ubuntu 3 10.80.0.13",
            "client4 192.168.1.104 ubuntu 4 10.80.0.14",
            "client5 192.168.1.105 ubuntu 5 10.80.0.15",
        ]

    lines = []
    for k, v in params.items():
        if v is not None:
            lines.append(f"{k}={v}")
    lines.append("CLIENTS=(")
    for c in clients:
        lines.append(f'    "{c}"')
    lines.append(")")
    return "\n".join(lines) + "\n"


class TestClusterConfig(unittest.TestCase):
    def _run_with_conf(self, conf_content):
        with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as f:
            f.write(conf_content)
            conf_path = f.name
        try:
            return subprocess.run([str(SCRIPT_PATH), conf_path], capture_output=True, text=True)
        finally:
            os.unlink(conf_path)

    def assert_conf_valid(self, overrides=None, clients=None, msg=""):
        res = self._run_with_conf(make_conf(overrides, clients))
        self.assertEqual(res.returncode, 0, f"配置应当合法通过: {msg}\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}")

    def assert_conf_invalid(self, overrides=None, clients=None, expected_in_output=None, msg=""):
        res = self._run_with_conf(make_conf(overrides, clients))
        self.assertNotEqual(res.returncode, 0, f"配置应当校验失败: {msg}")
        if expected_in_output:
            self.assertIn(expected_in_output, res.stderr + res.stdout)

    def test_script_exists_and_executable(self):
        self.assertTrue(SCRIPT_PATH.exists(), f"脚本 {SCRIPT_PATH} 必须存在")
        self.assertTrue(os.access(SCRIPT_PATH, os.X_OK), "脚本必须具备执行权限")

    def test_template_conf_exists_and_valid(self):
        self.assertTrue(TEMPLATE_CONF.exists(), "根目录 cluster_nodes.conf 模板必须存在")
        res = subprocess.run([str(SCRIPT_PATH), str(TEMPLATE_CONF)], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"默认模板校验必须通过:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}")
        self.assertIn("配置文件校验通过", res.stdout)
        self.assertIn("Channel", res.stdout)
        self.assertIn("client1", res.stdout)

    def test_channel_161_strictly_forbidden(self):
        self.assert_conf_invalid({"WIRELESS_CHANNEL": "161"}, expected_in_output="黑名单")

    def test_valid_channels_accepted(self):
        for ch in ["149", "153", "157", "36", "44"]:
            self.assert_conf_valid({"WIRELESS_CHANNEL": ch}, msg=f"信道 {ch}")
        # 165 仅支持 HT20
        self.assert_conf_valid({"WIRELESS_CHANNEL": "165", "WIRELESS_CHANNEL_WIDTH": "HT20"}, msg="信道 165 HT20")

    def test_channel_165_bandwidth_constraint(self):
        self.assert_conf_invalid(
            {"WIRELESS_CHANNEL": "165", "WIRELESS_CHANNEL_WIDTH": "HT40+"},
            expected_in_output="HT20",
            msg="信道 165 不支持 HT40+"
        )

    def test_invalid_channel_rejected(self):
        for bad_ch in ["0", "-5", "196", "abc"]:
            self.assert_conf_invalid({"WIRELESS_CHANNEL": bad_ch}, msg=f"非法信道 {bad_ch}")

    def test_channel_width_validation(self):
        for cw in ["HT20", "HT40+", "HT40-"]:
            self.assert_conf_valid({"WIRELESS_CHANNEL_WIDTH": cw}, msg=f"频宽 {cw}")

        for bad_cw in ["HT40", "VHT20", "VHT80", "INVALID"]:
            self.assert_conf_invalid({"WIRELESS_CHANNEL_WIDTH": bad_cw}, msg=f"频宽 {bad_cw}")

    def test_txpower_bounds(self):
        for p in ["10", "12", "15", "20"]:
            self.assert_conf_valid({"WIRELESS_TXPOWER_DBM": p}, msg=f"发射功率 {p}")

        for p in ["9", "21", "30", "-1"]:
            self.assert_conf_invalid({"WIRELESS_TXPOWER_DBM": p}, msg=f"非法发射功率 {p}")

    def test_mcs_bounds(self):
        rate_map = {"3": "15000", "4": "22000", "5": "28000", "6": "38000"}
        for mcs in ["3", "4", "5", "6"]:
            self.assert_conf_valid({
                "DOWNLINK_MCS": mcs,
                "UPLINK_MCS": mcs,
                "UFTP_RATE_KBPS": rate_map[mcs]
            }, msg=f"MCS {mcs}")

        for bad_mcs in ["0", "1", "2", "7", "8"]:
            self.assert_conf_invalid({"DOWNLINK_MCS": bad_mcs}, msg=f"非法 DOWNLINK_MCS {bad_mcs}")

    def test_uftp_rate_matching_downlink_mcs(self):
        # MCS 3: 12000 ~ 18000
        for rate in ["12000", "15000", "18000"]:
            self.assert_conf_valid({"DOWNLINK_MCS": "3", "UFTP_RATE_KBPS": rate})
        for bad_rate in ["11999", "18001"]:
            self.assert_conf_invalid({"DOWNLINK_MCS": "3", "UFTP_RATE_KBPS": bad_rate})

        # MCS 6: 32000 ~ 45000
        for rate in ["32000", "38000", "45000"]:
            self.assert_conf_valid({"DOWNLINK_MCS": "6", "UFTP_RATE_KBPS": rate})
        for bad_rate in ["30000", "46000"]:
            self.assert_conf_invalid({"DOWNLINK_MCS": "6", "UFTP_RATE_KBPS": bad_rate})

    def test_server_fixed_contract(self):
        self.assert_conf_invalid({"SERVER_NODE_ID": "42"}, expected_in_output="SERVER_NODE_ID")
        self.assert_conf_invalid({"SERVER_TUN_IP": "10.80.0.1"}, expected_in_output="SERVER_TUN_IP")
        self.assert_conf_invalid({"SERVER_TUN_IP": "192.168.1.1/24"}, expected_in_output="SERVER_TUN_IP")

    def test_client_count_contract_5_to_7(self):
        clients_4 = [f"client{i} 192.168.1.10{i} ubuntu {i} 10.80.0.1{i}" for i in range(1, 5)]
        self.assert_conf_invalid(clients=clients_4, expected_in_output="5~7")

        clients_8 = [f"client{i} 192.168.1.10{i} ubuntu {i} 10.80.0.1{i}" for i in range(1, 9)]
        self.assert_conf_invalid(clients=clients_8, expected_in_output="5~7")

        clients_5 = [f"client{i} 192.168.1.10{i} ubuntu {i} 10.80.0.1{i}" for i in range(1, 6)]
        self.assert_conf_valid(clients=clients_5)

    def test_missing_required_parameter(self):
        self.assert_conf_invalid({"DOWNLINK_MCS": None}, expected_in_output="缺失必填参数")

    def test_unknown_key_rejection(self):
        raw = make_conf() + "\nUNKNOWN_PARAM=123\n"
        res = self._run_with_conf(raw)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("未知键", res.stderr + res.stdout)

    def test_client_validation_errors(self):
        # 1. 重复角色
        dup_role = [
            "client1 192.168.1.101 ubuntu 1 10.80.0.11",
            "client1 192.168.1.102 ubuntu 2 10.80.0.12",
            "client3 192.168.1.103 ubuntu 3 10.80.0.13",
            "client4 192.168.1.104 ubuntu 4 10.80.0.14",
            "client5 192.168.1.105 ubuntu 5 10.80.0.15",
        ]
        self.assert_conf_invalid(clients=dup_role, expected_in_output="重复")

        # 2. 非法用户名 (含引号注入)
        bad_user = [
            "client1 192.168.1.101 'ubuntu' 1 10.80.0.11",
            "client2 192.168.1.102 ubuntu 2 10.80.0.12",
            "client3 192.168.1.103 ubuntu 3 10.80.0.13",
            "client4 192.168.1.104 ubuntu 4 10.80.0.14",
            "client5 192.168.1.105 ubuntu 5 10.80.0.15",
        ]
        self.assert_conf_invalid(clients=bad_user, expected_in_output="用户名格式")

    def test_sourcing_in_bash(self):
        cmd = f"""
source "{SCRIPT_PATH}"
load_cluster_config "{TEMPLATE_CONF}"
echo "CH=$WIRELESS_CHANNEL"
echo "CLIENTS=$CLIENT_COUNT"
echo "FIRST_ROLE=${{CLIENT_ROLES[0]}}"
"""
        res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertIn("CH=157", res.stdout)
        self.assertIn("CLIENTS=7", res.stdout)
        self.assertIn("FIRST_ROLE=client1", res.stdout)


if __name__ == "__main__":
    unittest.main()
