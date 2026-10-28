#!/usr/bin/env python3
"""仅提高 fl04 实际功率做常驻对照；结束恢复原功率，不修改守护配置。"""
import json
from pathlib import Path
import re
import subprocess
import time
import urllib.request
from datetime import datetime, timezone

base = Path(__file__).resolve().parent
root = base / ('power18-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
root.mkdir()
(base / 'power18-active.txt').write_text(str(root) + '\n')
remote = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=7', 'fl@192.168.108.204']
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
interface = 'wlxfc221c300f04'

def ssh(command):
    result = subprocess.run(remote + [command], text=True, capture_output=True, timeout=12)
    result.check_returncode()
    return result.stdout

def status():
    with opener.open('http://127.0.0.1:9090/api/v1/status', timeout=4) as response:
        return json.load(response)

def power(value):
    # 复用已部署的硬件适配器，同时写驱动 override 和 iw；不调用守护状态入口。
    command = ('sudo -n /usr/bin/python3 -B -c '
               '"from wfb_ng.fl.client_daemon import LinuxNetworkAdapter; '
               f'LinuxNetworkAdapter(use_sudo=False).set_txpower(\'{interface}\', {value})"')
    ssh(command)
    info = ssh('sudo -n iw dev ' + interface + ' info')
    assert re.search(r'txpower\s+-' + str(value) + r'\.00 dBm', info), info
    return info

before = status()
assert before['active_job'] is None and before['server_state'] == 'IDLE'
original = ssh('sudo -n iw dev ' + interface + ' info')
assert 'txpower -14.00 dBm' in original, original
(root / 'before.json').write_text(json.dumps({'state': before, 'iw': original}, ensure_ascii=False, indent=2))
processes = []
files = []
try:
    changed = power(18)
    (root / 'power18-readback.txt').write_text(changed)
    source = (base / 'capture-tun.py').read_bytes()
    for host, ip in [('fl00', None), ('fl04', '192.168.108.204')]:
        command = ['sudo', '-n', '/usr/bin/python3', '-B', '-'] if ip is None else remote + ['sudo -n /usr/bin/python3 -B -']
        out = (root / (host + '-passive.jsonl')).open('xb')
        err = (root / (host + '-passive.err')).open('xb')
        files.extend([out, err])
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=out, stderr=err)
        proc.stdin.write(source)
        proc.stdin.close()
        processes.append(proc)
    start = time.monotonic()
    last_iw = -10.0
    with (root / 'samples.jsonl').open('x') as stream:
        while True:
            elapsed = time.monotonic() - start
            record = {'mono': time.monotonic(), 'elapsed': elapsed, 'state': status()}
            if elapsed - last_iw >= 5:
                last_iw = elapsed
                record['iw'] = ssh('sudo -n iw dev ' + interface + ' info')
                record['override'] = ssh('sudo -n cat /sys/module/88XXau_wfb/parameters/rtw_tx_pwr_idx_override').strip()
                assert 'txpower -18.00 dBm' in record['iw'], record
                assert record['override'] == '18', record
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')
            stream.flush()
            if elapsed >= 120:
                break
            time.sleep(1)
finally:
    restored = power(14)
    (root / 'restored.json').write_text(json.dumps({'iw': restored, 'state': status()}, ensure_ascii=False, indent=2))
    for proc in processes:
        proc.wait(timeout=15)
    for stream in files:
        stream.close()
print(root)
