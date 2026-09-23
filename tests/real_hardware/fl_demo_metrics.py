#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WFB-FL ARMv8 现场演示指标采集、解析与看板渲染辅助工具。

功能涵盖:
1. 确定性测试文件生成与 SHA-256 哈希校验；
2. Linux 系统网络 TCP 重传指标采集 (/proc/net/snmp)；
3. WFB 底座空口物理收发、丢包、FEC 恢复与反压遥测日志解析；
4. 受控 HTTP PUT 接收端 (Server) 与回传发送端 (Client)；
5. 下行组播与上行回传演示指标看板渲染。
"""

import argparse
import hashlib
import http.client
import http.server
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ==============================================================================
# 终端颜色与样式控制
# ==============================================================================
def use_color() -> bool:
    return sys.stdout.isatty() and not bool(os.environ.get("NO_COLOR"))


def color_text(code: str, text: str) -> str:
    if not use_color():
        return text
    return f"\033[{code}m{text}\033[0m"


def c_green(text: str) -> str: return color_text("32;1", text)
def c_red(text: str) -> str: return color_text("31;1", text)
def c_yellow(text: str) -> str: return color_text("33;1", text)
def c_blue(text: str) -> str: return color_text("34;1", text)
def c_cyan(text: str) -> str: return color_text("36;1", text)
def c_bold(text: str) -> str: return color_text("1", text)


# ==============================================================================
# 通用 ASCII 表格格式化器 (消除看板间重复代码)
# ==============================================================================
def format_ascii_table(
    headers: List[str],
    rows: List[List[str]],
    col_widths: List[int],
    alignments: List[str],
) -> List[str]:
    """渲染自适应 ASCII 表格。"""
    sep = "+" + "+".join("-" * w for w in col_widths) + "+"

    def build_row(cells: List[str]) -> str:
        parts = []
        for cell, w, align in zip(cells, col_widths, alignments):
            content = str(cell)
            if len(content) > w - 2:
                content = "..." + content[-(w - 5):]
            if align == "right":
                parts.append(f" {content:>{w - 2}} ")
            elif align == "center":
                parts.append(f" {content:^{w - 2}} ")
            else:
                parts.append(f" {content:<{w - 2}} ")
        return "|" + "|".join(parts) + "|"

    out = [sep, build_row(headers), sep]
    for row in rows:
        out.append(build_row(row))
    out.append(sep)
    return out


# ==============================================================================
# 1. 确定性测试文件生成与哈希计算
# ==============================================================================
def calc_file_sha256(path: Path) -> Tuple[str, int]:
    """计算指定文件的 SHA-256 哈希值与字节大小。"""
    if not path.is_file():
        raise FileNotFoundError(f"文件不存在或不是普通文件: {path}")

    h = hashlib.sha256()
    total_bytes = 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(64 * 1024)
            if not chunk:
                break
            h.update(chunk)
            total_bytes += len(chunk)
    return h.hexdigest(), total_bytes


def generate_deterministic_file(path: Path, size_bytes: int = 41943040, seed_tag: str = "wfb_fl_demo") -> Dict[str, Any]:
    """快速生成确定性测试文件 (默认 40 MiB = 41,943,040 字节)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    chunk_size = 64 * 1024
    pattern_block = (f"=== WFB-FL DEMO FILE [{seed_tag}] ===\n" * 128).encode("utf-8")
    pattern_len = len(pattern_block)

    written = 0
    h = hashlib.sha256()
    with open(path, "wb") as fh:
        while written < size_bytes:
            to_write = min(chunk_size, size_bytes - written)
            if to_write >= pattern_len:
                chunk = (pattern_block * (to_write // pattern_len + 1))[:to_write]
            else:
                chunk = pattern_block[:to_write]
            fh.write(chunk)
            h.update(chunk)
            written += len(chunk)

    return {
        "path": str(path.resolve()),
        "size_bytes": written,
        "sha256": h.hexdigest(),
    }


# ==============================================================================
# 2. Linux 系统网络 TCP 重传指标采集
# ==============================================================================
def read_system_tcp_retransmits(snmp_path: str = "/proc/net/snmp") -> int:
    """从 /proc/net/snmp 读取系统当前累积 TCP 重传报文段数 (RetransSegs)。"""
    p = Path(snmp_path)
    if not p.is_file():
        raise FileNotFoundError(f"系统 SNMP 统计文件不存在: {snmp_path}")

    with open(p, "r", encoding="utf-8") as f:
        lines = f.readlines()

    for i in range(len(lines) - 1):
        if lines[i].startswith("Tcp:") and lines[i + 1].startswith("Tcp:"):
            headers = lines[i].split()
            values = lines[i + 1].split()
            if "RetransSegs" in headers:
                idx = headers.index("RetransSegs")
                return int(values[idx])

    raise ValueError(f"SNMP 统计文件中未找到 Tcp: RetransSegs 指标: {snmp_path}")


# ==============================================================================
# 3. WFB 底座空口遥测与日志解析
# ==============================================================================
def parse_pkt_src_line(line: str) -> Optional[Dict[str, Any]]:
    """解析单行 PKT_SRC 统计。"""
    if "PKT_SRC" not in line:
        return None
    m = re.search(r"(\d+)[\t ]+PKT_SRC[\t ]+(\d+):(\d+):(\d+):(\d+):(\d+):(\d+):(\d+)\s*$", line)
    if not m:
        return None
    try:
        node_id_int = int(m.group(2))
        return {
            "timestamp_ms": int(m.group(1)),
            "node_id": str(node_id_int),
            "rx_packets": int(m.group(3)),
            "rx_bytes": int(m.group(4)),
            "packets_fec_recovered": int(m.group(5)),
            "packets_lost": int(m.group(6)),
            "out_packets": int(m.group(7)),
            "out_bytes": int(m.group(8)),
        }
    except (ValueError, IndexError):
        return None


def parse_wfb_telemetry(
    server_log: str,
    client_logs: Optional[Dict[str, str]] = None,
    queue_summaries: Optional[Dict[str, Dict[str, Any]]] = None,
    tcp_retrans_delta: int = 0,
) -> Dict[str, Any]:
    """解析 WFB 底座运行日志与网络指标。"""
    if client_logs is None:
        client_logs = {}
    if queue_summaries is None:
        queue_summaries = {}

    rx_ant_samples = 0
    rx_packets = 0
    rx_bytes = 0
    packets_lost = 0
    packets_fec_recovered = 0

    tun_pause_count = 0
    tun_resume_count = 0

    loss_and_fec_by_node: Dict[str, Dict[str, Any]] = {}

    for line in server_log.splitlines():
        if "\tRX_ANT\t" in line:
            rx_ant_samples += 1

        # PKT 宏观遥测
        m_pkt = re.search(r"\tPKT\t(\d+):(\d+):(\d+):(\d+):(\d+):(\d+):(\d+):(\d+):(\d+):(\d+):(\d+)", line)
        if m_pkt:
            rx_packets += int(m_pkt.group(1))
            rx_bytes += int(m_pkt.group(2))
            packets_fec_recovered += int(m_pkt.group(7))
            packets_lost += int(m_pkt.group(8))

        # PKT_SRC 分源遥测
        src_entry = parse_pkt_src_line(line)
        if src_entry:
            nid = src_entry["node_id"]
            if nid not in loss_and_fec_by_node:
                loss_and_fec_by_node[nid] = {
                    "rx_packets": 0,
                    "rx_bytes": 0,
                    "packets_lost": 0,
                    "packets_fec_recovered": 0,
                    "out_packets": 0,
                    "out_bytes": 0,
                }
            node_data = loss_and_fec_by_node[nid]
            node_data["rx_packets"] += src_entry["rx_packets"]
            node_data["rx_bytes"] += src_entry["rx_bytes"]
            node_data["packets_lost"] += src_entry["packets_lost"]
            node_data["packets_fec_recovered"] += src_entry["packets_fec_recovered"]
            node_data["out_packets"] += src_entry["out_packets"]
            node_data["out_bytes"] += src_entry["out_bytes"]

        if "TUN_PAUSE" in line or "tun_read_pause" in line:
            tun_pause_count += 1
        if "TUN_RESUME" in line or "tun_read_resume" in line:
            tun_resume_count += 1

    # 令牌授权统计与客户端日志流控
    authorized_sends_by_node: Dict[str, int] = {}
    for role, log_text in client_logs.items():
        m_nid = re.search(r"\d+", role)
        node_id = m_nid.group() if m_nid else "1"
        authorized_sends_by_node[node_id] = 0
        for line in log_text.splitlines():
            # WFB 底座输出: \tTOKEN_AUTH\t<accepted>:<rejected>:<authorized>:<denied>
            m_ta = re.search(r"\tTOKEN_AUTH\t(\d+):(\d+):(\d+):(\d+)", line)
            if m_ta:
                authorized_sends_by_node[node_id] = max(
                    authorized_sends_by_node[node_id], int(m_ta.group(3))
                )
            if not queue_summaries:
                if "TUN_PAUSE" in line or "tun_read_pause" in line:
                    tun_pause_count += 1
                if "TUN_RESUME" in line or "tun_read_resume" in line:
                    tun_resume_count += 1

    # 优先采用结构化队列摘要累计计数，避免与日志行重复计数
    if queue_summaries:
        tun_pause_count = 0
        tun_resume_count = 0
        for summary in queue_summaries.values():
            tun_pause_count += int(summary.get("tun_read_pause_total", 0))
            tun_resume_count += int(summary.get("tun_read_resume_total", 0))

    total_air = rx_packets + packets_lost
    loss_rate = (packets_lost / total_air) if total_air > 0 else 0.0
    fec_recovery_rate = (packets_fec_recovered / (packets_fec_recovered + packets_lost)) if (packets_fec_recovered + packets_lost) > 0 else 0.0

    return {
        "rx_ant_samples": rx_ant_samples,
        "rx_packets": rx_packets,
        "rx_bytes": rx_bytes,
        "packets_lost": packets_lost,
        "packets_fec_recovered": packets_fec_recovered,
        "loss_rate": round(loss_rate, 4),
        "fec_recovery_rate": round(fec_recovery_rate, 4),
        "tun_pause_count": tun_pause_count,
        "tun_resume_count": tun_resume_count,
        "tcp_retransmits": tcp_retrans_delta,
        "authorized_sends_by_node": authorized_sends_by_node,
        "loss_and_fec_by_node": loss_and_fec_by_node,
    }


# ==============================================================================
# 4. 受控 HTTP PUT 接收端与发送端
# ==============================================================================
class DemoHTTPPutHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_PUT(self) -> None:
        t0 = time.monotonic()
        client_ip = self.client_address[0]

        node_id_match = re.search(r"client_?(\d+)", self.path)
        if node_id_match:
            node_id = node_id_match.group(1)
        else:
            parts = client_ip.split(".")
            node_id = str(int(parts[3]) - 10) if len(parts) == 4 and parts[0] == "10" and parts[1] == "80" else "1"

        out_dir = Path(getattr(self.server, "output_dir", "/var/lib/wfb-ng/fl_demo/server_received"))
        out_dir.mkdir(parents=True, exist_ok=True)
        target_path = out_dir / f"client_{node_id}_update.bin"

        content_len = int(self.headers.get("Content-Length", 0))
        read_bytes = 0
        hasher = hashlib.sha256()

        with open(target_path, "wb") as fh:
            while read_bytes < content_len:
                chunk_to_read = min(64 * 1024, content_len - read_bytes)
                chunk = self.rfile.read(chunk_to_read)
                if not chunk:
                    break
                fh.write(chunk)
                hasher.update(chunk)
                read_bytes += len(chunk)

        complete = (read_bytes == content_len)
        status = 201 if complete else 400
        t1 = time.monotonic()
        duration = max(0.001, t1 - t0)
        digest = hasher.hexdigest()

        event = {
            "type": "upload",
            "node_id": node_id,
            "client_ip": client_ip,
            "path": str(target_path.resolve()),
            "size_bytes": read_bytes,
            "sha256": digest,
            "status": status,
            "duration_seconds": round(duration, 3),
        }

        events_file = out_dir / "server_put_events.jsonl"
        with open(events_file, "a", encoding="utf-8") as ef:
            ef.write(json.dumps(event, ensure_ascii=False) + "\n")

        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def log_message(self, fmt: str, *args: Any) -> None:
        pass


class ThreadedDemoServer(http.server.ThreadingHTTPServer):
    allow_reuse_address = True
    output_dir: str = "/var/lib/wfb-ng/fl_demo/server_received"


def start_http_receiver(host: str, port: int, output_dir: str, ready_file: Optional[str] = None) -> None:
    """启动 HTTP PUT 接收端。"""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    server = ThreadedDemoServer((host, port), DemoHTTPPutHandler)
    server.output_dir = str(out.resolve())

    if ready_file:
        rf = Path(ready_file)
        rf.parent.mkdir(parents=True, exist_ok=True)
        bound_port = server.server_address[1]
        rf.write_text(f"{bound_port}\n", encoding="utf-8")

    server.serve_forever()


def send_http_put_file(
    host: str,
    port: int,
    file_path: Path,
    node_id: int,
    source_ip: Optional[str] = None,
    timeout: float = 120.0,
) -> Dict[str, Any]:
    """Client 端通过 HTTP PUT 发送文件。"""
    if not file_path.is_file():
        raise FileNotFoundError(f"发送文件不存在: {file_path}")

    file_size = file_path.stat().st_size
    digest, _ = calc_file_sha256(file_path)

    path = f"/client{node_id}"
    source_address = (source_ip, 0) if source_ip else None

    t0 = time.monotonic()
    conn = http.client.HTTPConnection(host, port, timeout=timeout, source_address=source_address)

    with open(file_path, "rb") as fh:
        body = fh.read()

    conn.request(
        "PUT",
        path,
        body=body,
        headers={
            "Content-Length": str(len(body)),
            "Content-Type": "application/octet-stream",
            "Connection": "close",
        },
    )
    resp = conn.getresponse()
    resp.read()
    conn.close()
    t1 = time.monotonic()

    duration = max(0.001, t1 - t0)
    throughput_mbps = (file_size * 8.0) / (duration * 1_000_000.0)

    return {
        "node_id": node_id,
        "path": path,
        "status_code": resp.status,
        "size_bytes": file_size,
        "sha256": digest,
        "duration_seconds": round(duration, 3),
        "throughput_mbps": round(throughput_mbps, 2),
    }


# ==============================================================================
# 5. 多维指标看板渲染 (下行看板 & 上行看板)
# ==============================================================================
def render_downlink_dashboard(data: Dict[str, Any]) -> Tuple[str, bool]:
    """渲染下行大文件组播传输指标看板。返回 (渲染文本, 是否全部通过)。"""
    source_file = data.get("source_file", "未知")
    size_bytes = data.get("size_bytes", 0)
    size_mb = size_bytes / (1024 * 1024)
    source_sha = data.get("source_sha256", "未知")
    channel = data.get("channel", "157")
    channel_width = data.get("channel_width", "HT40+")
    txpower = data.get("txpower_dbm", 12)
    mcast_group = data.get("multicast_group", "239.80.41.1")
    mcast_port = data.get("multicast_port", 1044)
    mcs = data.get("mcs", 3)
    rate_kbps = data.get("rate_kbps", 15000)
    duration = data.get("duration_seconds", 0.0)
    throughput = (size_bytes * 8.0) / (duration * 1_000_000.0) if duration > 0 else 0.0

    clients: List[Dict[str, Any]] = data.get("clients", [])

    lines = []
    w = 88
    lines.append("=" * w)
    lines.append(c_bold("                    WFB-FL 现场演示：下行大文件组播传输指标看板"))
    lines.append("=" * w)
    lines.append(c_bold("【下行传输概况】"))
    lines.append(f"  • 源文件路径:   {source_file}")
    lines.append(f"  • 文件载荷大小: {size_mb:.2f} MB ({size_bytes:,} 字节)")
    lines.append(f"  • 初始 SHA-256: {source_sha}")
    lines.append(f"  • 物理射频配置: 信道 {channel} {channel_width} | 功率 {txpower} dBm")
    lines.append(f"  • 组播传输参数: 组播组 {mcast_group}:{mcast_port} | MCS {mcs} | 注入速率 {rate_kbps} Kbps")
    lines.append(f"  • 广播传输耗时: {duration:.2f} 秒")
    lines.append(f"  • 空口平均吞吐: {c_cyan(f'{throughput:.2f} Mbps')}")
    lines.append("")
    lines.append(c_bold("【各客户端落盘与完整性校验】"))

    headers = ["角色", "管理网 IP", "远端落盘绝对路径", "接收大小", "耗时", "状态"]
    col_widths = [10, 16, 36, 12, 9, 8]
    alignments = ["left", "left", "left", "right", "right", "center"]

    rows = []
    all_passed = bool(clients)
    for c in clients:
        role = c.get("role", "client")
        host = c.get("host_ip", "-")
        rec_path = c.get("received_path", "-")
        c_bytes = f"{c.get('size_bytes', 0):,}"
        c_dur = f"{c.get('duration_seconds', 0.0):.2f}s"
        c_sha = c.get("sha256", "")
        passed = (c_sha == source_sha) and (c.get("size_bytes", 0) == size_bytes)
        if not passed:
            all_passed = False
        st_text = c_green("[通过]") if passed else c_red("[失败]")
        rows.append([role, host, rec_path, c_bytes, c_dur, st_text])

    lines.extend(format_ascii_table(headers, rows, col_widths, alignments))

    if all_passed:
        conclusion = c_green(f"【传输结论】所有在线 Client ({len(clients)}/{len(clients)}) 接收完成且 SHA-256 绝对一致，组播下行成功！")
    else:
        conclusion = c_red("【传输结论】客户端接收校验未通过，存在哈希不匹配、文件缺失或传输残缺！")
    lines.append(conclusion)
    lines.append("=" * w)
    return "\n".join(lines), all_passed


def render_uplink_dashboard(data: Dict[str, Any]) -> Tuple[str, bool]:
    """渲染上行受控调度回传指标看板。返回 (渲染文本, 是否全部通过)。"""
    server_addr = data.get("server_address", "10.80.0.1:8080")
    total_bytes = data.get("total_bytes", 0)
    total_mb = total_bytes / (1024 * 1024)
    duration = data.get("duration_seconds", 0.0)
    throughput = (total_bytes * 8.0) / (duration * 1_000_000.0) if duration > 0 else 0.0
    expected_sha = data.get("expected_sha256", "")

    mcs = data.get("mcs", 6)
    slot_ms = data.get("slot_duration_ms", 120)
    guard_ms = data.get("guard_interval_ms", 10)

    telemetry = data.get("telemetry", {})
    rx_packets = telemetry.get("rx_packets", 0)
    rx_bytes = telemetry.get("rx_bytes", 0)
    packets_lost = telemetry.get("packets_lost", 0)
    loss_rate = telemetry.get("loss_rate", 0.0) * 100.0
    fec_recovered = telemetry.get("packets_fec_recovered", 0)
    fec_rate = telemetry.get("fec_recovery_rate", 0.0) * 100.0
    tun_pause = telemetry.get("tun_pause_count", 0)
    tun_resume = telemetry.get("tun_resume_count", 0)
    tcp_retrans = telemetry.get("tcp_retransmits", 0)

    auth_sends_dict = telemetry.get("authorized_sends_by_node", {})
    auth_sends_total = sum(auth_sends_dict.values())
    per_node_rf = telemetry.get("loss_and_fec_by_node", {})

    clients: List[Dict[str, Any]] = data.get("clients", [])

    lines = []
    w = 88
    lines.append("=" * w)
    lines.append(c_bold("                    WFB-FL 现场演示：上行受控调度回传指标看板"))
    lines.append("=" * w)
    lines.append(c_bold("【传输与调度概况】"))
    lines.append(f"  • 上行目标地址: {server_addr}")
    lines.append(f"  • 回传文件总量: {total_mb:.2f} MB ({total_bytes:,} 字节，共 {len(clients)} 个客户端)")
    lines.append(f"  • 调度时隙参数: 时隙长度 {slot_ms} ms | 保护间隔 {guard_ms} ms | MCS {mcs}")
    lines.append(f"  • 回传总耗时间: {duration:.2f} 秒")
    lines.append(f"  • 聚合有效吞吐: {c_cyan(f'{throughput:.2f} Mbps')}")
    lines.append("")
    lines.append(c_bold("【物理链路与底座遥测】"))
    lines.append(f"  • 空口物理收包: {rx_packets:,} 包 ({(rx_bytes/(1024*1024)):.2f} MB)")
    lines.append(f"  • 物理空中丢包: {packets_lost:,} 包 (丢包率: {loss_rate:.2f}%)")
    lines.append(f"  • FEC 恢复包数: {fec_recovered:,} 包 (恢复率: {fec_rate:.2f}%)")
    lines.append(f"  • 令牌授权发送: {auth_sends_total:,} 次")
    lines.append(f"  • TUN 反压流控: PAUSE {tun_pause} 次 / RESUME {tun_resume} 次")
    lines.append(f"  • TCP 协议重传: {tcp_retrans} 次 (端到端传输可靠闭环)")
    lines.append("")
    lines.append(c_bold("【各客户端分源回传与一致性校验】"))

    headers = ["角色", "TUN IP", "回传大小", "耗时", "速率", "收包/丢包(丢包率)", "FEC恢复(率)", "反压(P/R)", "校验状态"]
    col_widths = [10, 14, 12, 8, 10, 18, 14, 10, 9]
    alignments = ["left", "left", "right", "right", "right", "center", "center", "center", "center"]

    rows = []
    all_passed = bool(clients) and bool(expected_sha)
    for c in clients:
        role = c.get("role", "client")
        ip = c.get("tun_ip", "-")
        c_bytes = f"{c.get('size_bytes', 0):,}"
        c_dur = c.get("duration_seconds", 0.0)
        c_dur_str = f"{c_dur:.2f}s" if c_dur > 0 else "-"
        c_rate = f"{(c.get('size_bytes', 0)*8/(c_dur*1e6)):.2f}M" if c_dur > 0 else "-"
        c_sha = c.get("sha256", "")

        # 匹配分源物理遥测
        m_nid = re.search(r"\d+", role)
        nid_str = m_nid.group() if m_nid else "1"
        rf_node = per_node_rf.get(nid_str, {})
        rf_rx = rf_node.get("rx_packets", 0)
        rf_lost = rf_node.get("packets_lost", 0)
        rf_fec = rf_node.get("packets_fec_recovered", 0)
        tot_air = rf_rx + rf_lost
        loss_pct = (rf_lost / tot_air * 100) if tot_air > 0 else 0.0
        fec_pct = (rf_fec / (rf_fec + rf_lost) * 100) if (rf_fec + rf_lost) > 0 else 0.0

        rf_summary = f"{rf_rx:,}/{rf_lost} ({loss_pct:.1f}%)"
        fec_summary = f"{rf_fec} ({fec_pct:.1f}%)"

        # 分源反压统计
        c_pause = c.get("tun_pause_count", 0)
        c_resume = c.get("tun_resume_count", 0)
        backpressure_summary = f"{c_pause}/{c_resume}"

        passed = bool(expected_sha) and (c_sha == expected_sha)
        if not passed:
            all_passed = False
        st_text = c_green("[通过]") if passed else c_red("[失败]")

        rows.append([role, ip, c_bytes, c_dur_str, c_rate, rf_summary, fec_summary, backpressure_summary, st_text])

    lines.extend(format_ascii_table(headers, rows, col_widths, alignments))

    if all_passed:
        conclusion = c_green(f"【回传结论】所有在线 Client ({len(clients)}/{len(clients)}) 回传完成且与源文件 SHA-256 绝对一致，上行传输成功！")
    else:
        conclusion = c_red("【回传结论】客户端回传未通过校验，存在哈希损毁或缺少原始参照！")
    lines.append(conclusion)
    lines.append("=" * w)
    return "\n".join(lines), all_passed


# ==============================================================================
# 6. CLI 命令行入口
# ==============================================================================
def main() -> None:
    parser = argparse.ArgumentParser(description="WFB-FL 演示指标看板与遥测辅助工具")
    subparsers = parser.add_subparsers(dest="command")

    # generate-file
    p_gen = subparsers.add_parser("generate-file", help="生成测试文件")
    p_gen.add_argument("--output", required=True, help="输出文件路径")
    p_gen.add_argument("--size", type=int, default=41943040, help="文件字节大小 (默认 40MB)")
    p_gen.add_argument("--seed", default="wfb_fl_demo", help="确定性种子标识")

    # sha256
    p_sha = subparsers.add_parser("sha256", help="计算文件 SHA-256")
    p_sha.add_argument("--file", required=True, help="目标文件路径")

    # tcp-retrans
    p_tcp = subparsers.add_parser("tcp-retrans", help="读取当前 TCP 重传数")
    p_tcp.add_argument("--snmp", default="/proc/net/snmp", help="snmp 文件路径")

    # render-downlink
    p_rdl = subparsers.add_parser("render-downlink", help="渲染下行看板")
    p_rdl.add_argument("--json", required=True, help="下行指标 JSON 路径")

    # render-uplink
    p_rul = subparsers.add_parser("render-uplink", help="渲染上行看板")
    p_rul.add_argument("--json", required=True, help="上行指标 JSON 路径")

    # http-receiver
    p_recv = subparsers.add_parser("http-receiver", help="运行 HTTP PUT 接收端")
    p_recv.add_argument("--host", default="0.0.0.0", help="监听地址")
    p_recv.add_argument("--port", type=int, default=8080, help="监听端口")
    p_recv.add_argument("--output-dir", required=True, help="落盘目录")
    p_recv.add_argument("--ready-file", default="", help="就绪标记文件")

    # http-sender
    p_send = subparsers.add_parser("http-sender", help="运行 HTTP PUT 发送端")
    p_send.add_argument("--host", required=True, help="Server 目标地址")
    p_send.add_argument("--port", type=int, default=8080, help="Server 端口")
    p_send.add_argument("--file", required=True, help="待发送文件路径")
    p_send.add_argument("--node-id", type=int, required=True, help="当前 Client 节点 ID")
    p_send.add_argument("--source-ip", default="", help="绑定本地网卡 IP")
    p_send.add_argument("--timeout", type=float, default=120.0, help="超时时间")

    # parse-telemetry
    p_pt = subparsers.add_parser("parse-telemetry", help="解析 WFB 日志并输出 JSON")
    p_pt.add_argument("--server-log", required=True, help="Server wfb.log 路径")
    p_pt.add_argument("--clients-dir", default="", help="包含各 Client wfb.log 的目录")
    p_pt.add_argument("--tcp-retrans-delta", type=int, default=0, help="TCP 重传增量")
    p_pt.add_argument("--output", required=True, help="输出 JSON 路径")

    args = parser.parse_args()

    if args.command == "generate-file":
        res = generate_deterministic_file(Path(args.output), args.size, args.seed)
        print(json.dumps(res, indent=2))
    elif args.command == "sha256":
        digest, size = calc_file_sha256(Path(args.file))
        print(f"{digest}  {size}")
    elif args.command == "tcp-retrans":
        val = read_system_tcp_retransmits(args.snmp)
        print(val)
    elif args.command == "render-downlink":
        with open(args.json, "r", encoding="utf-8") as f:
            data = json.load(f)
        text, ok = render_downlink_dashboard(data)
        print(text)
        if not ok:
            sys.exit(1)
    elif args.command == "render-uplink":
        with open(args.json, "r", encoding="utf-8") as f:
            data = json.load(f)
        text, ok = render_uplink_dashboard(data)
        print(text)
        if not ok:
            sys.exit(1)
    elif args.command == "http-receiver":
        start_http_receiver(args.host, args.port, args.output_dir, args.ready_file or None)
    elif args.command == "http-sender":
        res = send_http_put_file(
            args.host,
            args.port,
            Path(args.file),
            args.node_id,
            args.source_ip or None,
            args.timeout,
        )
        print(json.dumps(res, indent=2))
    elif args.command == "parse-telemetry":
        server_log_path = Path(args.server_log)
        if not server_log_path.is_file():
            raise FileNotFoundError(f"Server 遥测日志不存在: {args.server_log}")

        with open(server_log_path, "r", encoding="utf-8", errors="replace") as f:
            server_log_str = f.read()

        client_logs = {}
        queue_summaries = {}
        if args.clients_dir and os.path.isdir(args.clients_dir):
            for entry in os.listdir(args.clients_dir):
                sub = os.path.join(args.clients_dir, entry)
                if os.path.isdir(sub):
                    log_file = os.path.join(sub, "wfb.log")
                    if os.path.isfile(log_file):
                        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                            client_logs[entry] = f.read()
                    q_file = os.path.join(sub, "client_queue_summary.json")
                    if os.path.isfile(q_file):
                        try:
                            with open(q_file, "r", encoding="utf-8") as qf:
                                queue_summaries[entry] = json.load(qf)
                        except Exception:
                            pass

        res = parse_wfb_telemetry(
            server_log=server_log_str,
            client_logs=client_logs,
            queue_summaries=queue_summaries,
            tcp_retrans_delta=args.tcp_retrans_delta,
        )
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=2, ensure_ascii=False)
        print(f"遥测数据已保存至 {args.output}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
