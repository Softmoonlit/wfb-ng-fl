#!/usr/bin/env python3
"""只读检查完整九客户端是否真正可开始通信，失败返回非零。"""
import concurrent.futures
import json
import subprocess
import sys
import urllib.request

NODES = [1, 2, 3, 5, 6, 7, 8, 9, 10]
with urllib.request.urlopen('http://127.0.0.1:9090/api/v1/status', timeout=4) as response:
    status = json.load(response)

def inspect(node):
    host = f'fl{node:02d}'
    command = "for p in /sys/class/net/*; do [ \"$(basename \"$(readlink -f \"$p/device/driver\")\")\" = rtl88xxau_wfb ] && iw dev \"$(basename \"$p\")\" info; done"
    result = subprocess.run(['ssh', '-T', '-oBatchMode=yes', '-oConnectTimeout=4', host, command], capture_output=True, text=True, timeout=8)
    record = status.get('nodes', {}).get(str(node), {})
    ok = result.returncode == 0 and 'channel 149 ' in result.stdout and 'width: 40 MHz' in result.stdout and record.get('readiness') == 'READY' and record.get('reported_state') == 'IDLE' and record.get('error_code') is None
    return dict(node=node, ok=ok, readiness=record.get('readiness'), iw=result.stdout, error=result.stderr)

online = sorted(int(key) for key, value in status.get('nodes', {}).items()
                if value.get('readiness') != 'OFFLINE')
registry_ok = (online == NODES and status.get('active_job') is None
               and status.get('server_state') == 'IDLE')

with concurrent.futures.ThreadPoolExecutor(max_workers=len(NODES)) as pool:
    records = list(pool.map(inspect, NODES))
print(json.dumps(records, ensure_ascii=False, indent=2))
sys.exit(0 if registry_ok and all(record['ok'] for record in records) else 1)
