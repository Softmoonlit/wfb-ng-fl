#!/usr/bin/env python3
"""收集并计算本批次客户端传输期间的丢包率与 FEC 纠错率。"""
import concurrent.futures
import json
import subprocess
import sys

HOSTS = ['fl00', 'fl01', 'fl02', 'fl03', 'fl05', 'fl06', 'fl07', 'fl08', 'fl09']
CLIENTS = ['fl01', 'fl02', 'fl03', 'fl05', 'fl06', 'fl07', 'fl08', 'fl09']
CLIENT_NODE_IDS = tuple(int(host[2:]) for host in CLIENTS)


def get_one_offset(host):
    log_path = '/srv/wfb-ng-fl/server/wfb_uplink.log' if host == 'fl00' else '/srv/wfb-ng-fl/client/wfb_uplink.log'
    cmd = ['sudo', '-n', 'wc', '-l', log_path]
    if host != 'fl00':
        cmd = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=4', host, 'sudo', '-n', 'wc', '-l', log_path]
    try:
        res = subprocess.check_output(cmd, text=True, timeout=5, stderr=subprocess.DEVNULL).strip().split()[0]
        return host, int(res)
    except Exception:
        return host, 0


def get_log_tail_offsets():
    """并发获取所有节点当前日志的行数。"""
    offsets = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(HOSTS)) as pool:
        for host, count in pool.map(get_one_offset, HOSTS):
            offsets[host] = count
    return offsets


def empty_stats():
    return {'raw_p': 0, 'fec_rec': 0, 'lost': 0, 'out_p': 0}


def add_pkt_src(stats, parts):
    """累加 raw_p:raw_b:fec_rec:lost:out_p:out_b 字段。"""
    stats['raw_p'] += int(parts[1])
    stats['fec_rec'] += int(parts[3])
    stats['lost'] += int(parts[4])
    stats['out_p'] += int(parts[5])


def collect_diff_and_compute(start_offsets):
    """读取快照之后的增量日志，只计算当前八客户端。"""
    server_cmd = ['sudo', '-n', 'tail', '-n', f'+{start_offsets.get("fl00", 0) + 1}', '/srv/wfb-ng-fl/server/wfb_uplink.log']
    try:
        server_lines = subprocess.check_output(server_cmd, text=True, errors='ignore').splitlines()
    except Exception:
        server_lines = []

    uplink_stats = {}
    for line in server_lines:
        if '\tPKT_SRC\t' not in line:
            continue
        try:
            parts = line.split('\tPKT_SRC\t')[1].split(':')
            node_id = int(parts[0])
            if node_id not in CLIENT_NODE_IDS:
                continue
            stats = uplink_stats.setdefault(node_id, empty_stats())
            add_pkt_src(stats, parts)
        except (IndexError, ValueError):
            continue

    downlink_stats = {node_id: empty_stats() for node_id in CLIENT_NODE_IDS}

    def get_client_stats(host):
        node_id = int(host[2:])
        offset = start_offsets.get(host, 0)
        cmd = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=4', host,
               'sudo', '-n', 'tail', '-n', f'+{offset + 1}', '/srv/wfb-ng-fl/client/wfb_uplink.log']
        try:
            client_lines = subprocess.check_output(
                cmd, text=True, errors='ignore', timeout=8, stderr=subprocess.DEVNULL
            ).splitlines()
        except Exception:
            client_lines = []

        stats = empty_stats()
        for line in client_lines:
            if '\tPKT_SRC\t' not in line:
                continue
            try:
                parts = line.split('\tPKT_SRC\t')[1].split(':')
                if int(parts[0]) == 255:
                    add_pkt_src(stats, parts)
            except (IndexError, ValueError):
                continue
        return node_id, stats

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(CLIENTS)) as pool:
        for node_id, stats in pool.map(get_client_stats, CLIENTS):
            downlink_stats[node_id] = stats

    return {'uplink': uplink_stats, 'downlink': downlink_stats}


def row(label, stats):
    total = stats['out_p'] + stats['lost']
    if total == 0:
        return f"| {label} | 0 | 0 | 0 | 0.00% | 0 | 0.00% |"
    fec_rate = stats['fec_rec'] / total * 100
    loss_rate = stats['lost'] / total * 100
    return (f"| {label} | {stats['raw_p']} | {stats['out_p']} | {stats['fec_rec']} | "
            f"{fec_rate:.2f}% | {stats['lost']} | {loss_rate:.2f}% |")


def format_report(stats):
    lines = []
    header = "| 客户端 | 原始接收包数 | 有效输出包数 | FEC恢复包数 | FEC恢复率 | 最终丢失包数 | 最终丢包率 |"
    separator = "|---|---:|---:|---:|---:|---:|---:|"
    lines.append("### 1. 下行传输统计 (Server -> Clients, 广播)")
    lines.append(header)
    lines.append(separator)
    for node_id in sorted(CLIENT_NODE_IDS):
        lines.append(row(f"fl{node_id:02d}", stats['downlink'].get(node_id, empty_stats())))

    lines.append("\n### 2. 上行传输统计 (Clients -> Server, 并发单播)")
    lines.append(header)
    lines.append(separator)
    for node_id in sorted(CLIENT_NODE_IDS):
        lines.append(row(f"fl{node_id:02d}", stats['uplink'].get(node_id, empty_stats())))
    return "\n".join(lines)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--snapshot':
        print(json.dumps(get_log_tail_offsets()))
    elif len(sys.argv) > 2 and sys.argv[1] == '--report':
        offsets = json.loads(sys.argv[2])
        stats = collect_diff_and_compute(offsets)
        print(format_report(stats))
        if len(sys.argv) > 3:
            with open(sys.argv[3], 'w') as fp:
                json.dump(stats, fp, indent=2)
