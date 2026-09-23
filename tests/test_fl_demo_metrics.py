#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试 tests/real_hardware/fl_demo_metrics.py 辅助模块。"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from tests.real_hardware.fl_demo_metrics import (
    calc_file_sha256,
    generate_deterministic_file,
    parse_wfb_telemetry,
    read_system_tcp_retransmits,
    render_downlink_dashboard,
    render_uplink_dashboard,
    send_http_put_file,
    start_http_receiver,
    ThreadedDemoServer,
    DemoHTTPPutHandler,
)

METRICS_SCRIPT = Path(__file__).resolve().parent / "real_hardware" / "fl_demo_metrics.py"


class TestFLDemoMetrics(unittest.TestCase):
    def test_generate_and_sha256(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            target = tmp / "test_file.bin"
            size = 256 * 1024  # 256 KB
            res = generate_deterministic_file(target, size_bytes=size, seed_tag="test1")

            self.assertTrue(target.exists())
            self.assertEqual(res["size_bytes"], size)
            self.assertEqual(target.stat().st_size, size)

            sha, calc_size = calc_file_sha256(target)
            self.assertEqual(calc_size, size)
            self.assertEqual(sha, res["sha256"])

    def test_read_system_tcp_retransmits(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            snmp_file = tmp / "snmp"
            snmp_content = """Ip: Forwarding DefaultTTL InReceives InHdrErrors InAddrErrors ForwDatagrams InUnknownProtos InDiscards InDelivers OutRequests OutDiscards OutNoRoutes ReasmTimeout ReasmReqds ReasmOKs ReasmFails FragOKs FragFails FragCreates
Ip: 1 64 12345 0 0 0 0 0 12340 10000 0 0 0 0 0 0 0 0 0
Tcp: RtoAlgorithm RtoMin RtoMax MaxConn ActiveOpens PassiveOpens AttemptFails EstabResets CurrEstab InSegs OutSegs RetransSegs InErrs OutRsts InCsumErrors
Tcp: 1 200 120000 -1 500 200 10 5 2 50000 60000 42 0 15 0
"""
            snmp_file.write_text(snmp_content, encoding="utf-8")
            retrans = read_system_tcp_retransmits(str(snmp_file))
            self.assertEqual(retrans, 42)

    def test_parse_wfb_telemetry(self):
        server_log = """
1000\tRX_ANT\t-65:-70
1005\tPKT\t100:150000:0:0:0:0:5:10:0:0:0
1010\tPKT_SRC\t1:100:150000:5:10:95:140000
1015\tTUN_PAUSE
1020\tTUN_RESUME
"""
        client_log_str = "500\tTOKEN_AUTH\t120:10:25:100\n600\ttun_read_pause\n"
        client_logs = {
            "client1": client_log_str
        }
        res = parse_wfb_telemetry(server_log, client_logs, tcp_retrans_delta=3)

        self.assertEqual(res["rx_ant_samples"], 1)
        self.assertEqual(res["rx_packets"], 100)
        self.assertEqual(res["packets_fec_recovered"], 5)
        self.assertEqual(res["packets_lost"], 10)
        self.assertAlmostEqual(res["loss_rate"], round(10 / 110, 4))
        self.assertAlmostEqual(res["fec_recovery_rate"], round(5 / 15, 4))
        self.assertEqual(res["tun_pause_count"], 2)
        self.assertEqual(res["tun_resume_count"], 1)
        self.assertEqual(res["tcp_retransmits"], 3)
        self.assertEqual(res["authorized_sends_by_node"].get("1"), 25)

    def test_http_receiver_and_sender(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            out_dir = tmp / "server_received"
            ready_file = tmp / "ready"
            test_file = tmp / "source_model.bin"
            file_meta = generate_deterministic_file(test_file, size_bytes=64 * 1024, seed_tag="http_test")

            host = "127.0.0.1"

            # 绑定临时端口 (port 0) 避免端口冲突
            server = ThreadedDemoServer((host, 0), DemoHTTPPutHandler)
            server.output_dir = str(out_dir.resolve())
            actual_port = server.server_address[1]

            server_thread = threading.Thread(
                target=server.serve_forever,
                daemon=True,
            )
            server_thread.start()

            try:
                # 发送文件
                res = send_http_put_file(
                    host=host,
                    port=actual_port,
                    file_path=test_file,
                    node_id=1,
                )

                self.assertEqual(res["status_code"], 201)
                self.assertEqual(res["sha256"], file_meta["sha256"])

                # 验证服务端落盘
                received_file = out_dir / "client_1_update.bin"
                self.assertTrue(received_file.exists(), "服务端应收到对应文件")
                rec_sha, rec_size = calc_file_sha256(received_file)
                self.assertEqual(rec_size, 64 * 1024)
                self.assertEqual(rec_sha, file_meta["sha256"])
            finally:
                server.shutdown()
                server.server_close()

    def test_render_downlink_dashboard(self):
        data = {
            "source_file": "/tmp/model.bin",
            "size_bytes": 41943040,
            "source_sha256": "abcdef1234567890",
            "multicast_group": "239.80.41.1",
            "multicast_port": 1044,
            "mcs": 3,
            "rate_kbps": 15000,
            "duration_seconds": 22.5,
            "clients": [
                {
                    "role": "client1",
                    "host_ip": "192.168.1.101",
                    "received_path": "/var/lib/wfb-ng/fl_demo/received/model.bin",
                    "size_bytes": 41943040,
                    "duration_seconds": 22.4,
                    "sha256": "abcdef1234567890",
                },
                {
                    "role": "client2",
                    "host_ip": "192.168.1.102",
                    "received_path": "/var/lib/wfb-ng/fl_demo/received/model.bin",
                    "size_bytes": 41943040,
                    "duration_seconds": 22.5,
                    "sha256": "abcdef1234567890",
                },
            ],
        }
        output, ok = render_downlink_dashboard(data)
        self.assertTrue(ok)
        self.assertIn("下行大文件组播传输指标看板", output)
        self.assertIn("40.00 MB", output)
        self.assertIn("client1", output)
        self.assertIn("client2", output)
        self.assertIn("192.168.1.101", output)
        self.assertIn("[通过]", output)
        self.assertIn("下行成功", output)

    def test_render_uplink_dashboard(self):
        data = {
            "server_address": "10.80.0.1:8080",
            "total_bytes": 83886080,
            "duration_seconds": 35.0,
            "expected_sha256": "abcdef1234567890",
            "mcs": 6,
            "slot_duration_ms": 120,
            "guard_interval_ms": 10,
            "telemetry": {
                "rx_packets": 60000,
                "rx_bytes": 85000000,
                "packets_lost": 300,
                "loss_rate": 0.005,
                "packets_fec_recovered": 290,
                "fec_recovery_rate": 0.9667,
                "tun_pause_count": 5,
                "tun_resume_count": 5,
                "tcp_retransmits": 2,
                "authorized_sends_by_node": {"1": 150, "2": 150},
            },
            "clients": [
                {
                    "role": "client1",
                    "tun_ip": "10.80.0.11",
                    "server_path": "/var/lib/wfb-ng/fl_demo/server_received/client_1_update.bin",
                    "size_bytes": 41943040,
                    "duration_seconds": 34.0,
                    "sha256": "abcdef1234567890",
                },
                {
                    "role": "client2",
                    "tun_ip": "10.80.0.12",
                    "server_path": "/var/lib/wfb-ng/fl_demo/server_received/client_2_update.bin",
                    "size_bytes": 41943040,
                    "duration_seconds": 35.0,
                    "sha256": "abcdef1234567890",
                },
            ],
        }
        output, ok = render_uplink_dashboard(data)
        self.assertTrue(ok)
        self.assertIn("上行受控调度回传指标看板", output)
        self.assertIn("80.00 MB", output)
        self.assertIn("空口物理收包", output)
        self.assertIn("FEC 恢复包数", output)
        self.assertIn("TUN 反压流控", output)
        self.assertIn("TCP 协议重传", output)
        self.assertIn("client1", output)
        self.assertIn("client2", output)
        self.assertIn("[通过]", output)
        self.assertIn("上行传输成功", output)

    def test_cli_execution(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            out_file = tmp / "cli_gen.bin"

            # 1. generate-file
            res = subprocess.run([
                sys.executable, str(METRICS_SCRIPT),
                "generate-file", "--output", str(out_file), "--size", "1024"
            ], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0)
            self.assertEqual(out_file.stat().st_size, 1024)

            # 2. sha256
            res_sha = subprocess.run([
                sys.executable, str(METRICS_SCRIPT),
                "sha256", "--file", str(out_file)
            ], capture_output=True, text=True)
            self.assertEqual(res_sha.returncode, 0)
            self.assertIn("1024", res_sha.stdout)


if __name__ == "__main__":
    unittest.main()
