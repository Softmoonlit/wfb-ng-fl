#!/usr/bin/env python3
"""收集并计算传输期间各节点的丢包率与 FEC 纠错率。"""
import concurrent.futures
import json
import subprocess
import sys
import time

HOSTS = ['fl00', 'fl01', 'fl02', 'fl03', 'fl05', 'fl06', 'fl07', 'fl08', 'fl09']
CLIENTS = ['fl01', 'fl02', 'fl03', 'fl05', 'fl06', 'fl07', 'fl08', 'fl09']

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

def collect_diff_and_compute(start_offsets):
    """读取自 start_offsets 以来的增量日志，计算各节点丢包率和 FEC 纠错率。"""
    # 1. 服务端上行统计 (Clients -> Server)
    server_cmd = ['sudo', '-n', 'tail', '-n', f'+{start_offsets.get("fl00", 0) + 1}', '/srv/wfb-ng-fl/server/wfb_uplink.log']
    try:
        server_lines = subprocess.check_output(server_cmd, text=True, errors='ignore').splitlines()
    except Exception:
        server_lines = []

    uplink_stats = {}
    for line in server_lines:
        if '\tPKT_SRC\t' in line:
            try:
                parts = line.split('\tPKT_SRC\t')[1].split(':')
                node_id = int(parts[0])
                raw_p = int(parts[1])
                fec_rec = int(parts[3])
                lost = int(parts[4])
                if node_id not in uplink_stats:
                    uplink_stats[node_id] = {'raw_p': 0, 'fec_rec': 0, 'lost': 0}
                uplink_stats[node_id]['raw_p'] += raw_p
                uplink_stats[node_id]['fec_rec'] += fec_rec
                uplink_stats[node_id]['lost'] += lost
            except (IndexError, ValueError):
                continue

    # 2. 客户端下行统计 (Server -> Clients)
    downlink_stats = {}
    def get_client_stats(host):
        node_id = int(host.replace('fl0', '').replace('fl', ''))
        offset = start_offsets.get(host, 0)
        cmd = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=4', host,
               'sudo', '-n', 'tail', '-n', f'+{offset + 1}', '/srv/wfb-ng-fl/client/wfb_uplink.log']
        try:
            client_lines = subprocess.check_output(cmd, text=True, errors='ignore', timeout=8, stderr=subprocess.DEVNULL).splitlines()
        except Exception:
            client_lines = []

        d_stats = {'raw_p': 0, 'fec_rec': 0, 'lost': 0}
        for line in client_lines:
            if '\tPKT_SRC\t' in line:
                try:
                    parts = line.split('\tPKT_SRC\t')[1].split(':')
                    src_node = int(parts[0])
                    if src_node == 255: # 来自服务端的广播
                        raw_p = int(parts[1])
                        fec_rec = int(parts[3])
                        lost = int(parts[4])
                        d_stats['raw_p'] += raw_p
                        d_stats['fec_rec'] += fec_rec
                        d_stats['lost'] += lost
                except (IndexError, ValueError):
                    continue
        return node_id, d_stats

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(CLIENTS)) as pool:
        for node_id, d_stats in pool.map(get_client_stats, CLIENTS):
            downlink_stats[node_id] = d_stats

    return {'uplink': uplink_stats, 'downlink': downlink_stats}

def format_report(stats):
    lines = []
    lines.append("### 1. 下行传输统计 (Server -> Clients, 广播)")
    lines.append("| 客户端 | 接收有效包数 | FEC恢复包数 | FEC纠错率 | 最终丢失包数 | 最终丢包率 | 物理层空口丢包率 |")
    lines.append("|---|---|---|---|---|---|---|")
    for node_id in sorted(stats['downlink'].keys()):
        s = stats['downlink'][node_id]
        total = s['raw_p'] + s['lost']
        if total > 0:
            fec_rate = (s['fec_rec'] / total) * 100
            final_loss_rate = (s['lost'] / total) * 100
            air_loss_rate = ((s['fec_rec'] + s['lost']) / total) * 100
            lines.append(f"| fl0{node_id} | {s['raw_p']} | {s['fec_rec']} | {fec_rate:.2f}% | {s['lost']} | {final_loss_rate:.2f}% | {air_loss_rate:.2f}% |")
        else:
            lines.append(f"| fl0{node_id} | 0 | 0 | 0.00% | 0 | 0.00% | 0.00% |")

    lines.append("\n### 2. 上行传输统计 (Clients -> Server, 并发单播)")
    lines.append("| 客户端 | 接收有效包数 | FEC恢复包数 | FEC纠错率 | 最终丢失包数 | 最终丢包率 | 物理层空口丢包率 |")
    lines.append("|---|---|---|---|---|---|---|")
    for node_id in sorted(stats['uplink'].keys()):
        s = stats['uplink'][node_id]
        total = s['raw_p'] + s['lost']
        if total > 0:
            fec_rate = (s['fec_rec'] / total) * 100
            final_loss_rate = (s['lost'] / total) * 100
            air_loss_rate = ((s['fec_rec'] + s['lost']) / total) * 100
            lines.append(f"| fl0{node_id} | {s['raw_p']} | {s['fec_rec']} | {fec_rate:.2f}% | {s['lost']} | {final_loss_rate:.2f}% | {air_loss_rate:.2f}% |")
        else:
            lines.append(f"| fl0{node_id} | 0 | 0 | 0.00% | 0 | 0.00% | 0.00% |")
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
