#!/usr/bin/env python3
"""暂时暂停另两端，仅观察 fl04 常驻心跳；无论结果如何都恢复服务。"""
import json
from pathlib import Path
import subprocess
import time
import urllib.request

root = Path(__file__).resolve().parent / 'single-fl04-resident'
root.mkdir(exist_ok=False)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def state():
    with opener.open('http://127.0.0.1:9090/api/v1/status', timeout=4) as response:
        return json.load(response)

def service(host, action):
    command = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=7', 'fl@' + host,
               'sudo -n systemctl ' + action + ' wfb-fl-client-daemon.service']
    result = subprocess.run(command, text=True, capture_output=True, timeout=45)
    with (root / 'operations.jsonl').open('a') as stream:
        stream.write(json.dumps({'mono': time.monotonic(), 'host': host, 'action': action,
                                 'rc': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}) + '\n')
    result.check_returncode()

before = state()
assert before['server_state'] == 'IDLE' and before['active_job'] is None
(root / 'before.json').write_text(json.dumps(before, ensure_ascii=False, indent=2))
changed = []
try:
    for host in ('192.168.108.201', '192.168.108.202'):
        changed.append(host)
        service(host, 'stop')
    start = time.monotonic()
    with (root / 'samples.jsonl').open('x') as stream:
        while True:
            current = state()
            now = time.monotonic()
            stream.write(json.dumps({'mono': now, 'elapsed': now-start, 'state': current}, ensure_ascii=False)+'\n')
            stream.flush()
            if now-start >= 120:
                break
            time.sleep(1)
finally:
    errors = []
    for host in changed:
        try:
            service(host, 'start')
        except Exception as error:
            errors.append(repr(error))
    (root / 'restoration.json').write_text(json.dumps({'errors': errors, 'state': state()}, ensure_ascii=False, indent=2))
    if errors:
        raise RuntimeError(errors)
