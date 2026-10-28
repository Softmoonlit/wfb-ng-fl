#!/usr/bin/env python3
"""只读观察MCS0控制帧部署后的四端心跳，完整保留失败及恢复样本。"""
import argparse
import json
from pathlib import Path
import subprocess
import time
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--seconds', type=float, default=120)
args = parser.parse_args()
args.output.mkdir(exist_ok=False)
source = (Path(__file__).parent / 'capture-tun.py').read_bytes()
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
processes = []
streams = []
for host, ip in [('fl00', None), ('fl04', '192.168.108.204')]:
    command = ['sudo', '-n', '/usr/bin/python3', '-B', '-'] if ip is None else [
        'ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=7', 'fl@'+ip,
        'sudo -n /usr/bin/python3 -B -']
    out = (args.output / (host+'-passive.jsonl')).open('xb')
    err = (args.output / (host+'-passive.err')).open('xb')
    streams.extend([out, err])
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=out, stderr=err)
    process.stdin.write(source)
    process.stdin.close()
    processes.append(process)
start = time.monotonic()
samples = []
try:
    with (args.output / 'samples.jsonl').open('x') as stream:
        while True:
            with opener.open('http://127.0.0.1:9090/api/v1/status', timeout=4) as response:
                state = json.load(response)
            row = {'mono': time.monotonic(), 'elapsed': time.monotonic()-start, 'state': state}
            samples.append(row)
            stream.write(json.dumps(row, ensure_ascii=False)+'\n')
            stream.flush()
            assert state['server_state']=='IDLE' and state['active_job'] is None, state
            if row['elapsed']>=args.seconds:
                break
            time.sleep(1)
finally:
    for process in processes:
        process.wait(timeout=95)
    for stream in streams:
        stream.close()
summary = {'seconds': samples[-1]['elapsed'], 'samples': len(samples), 'nodes': {}}
for node in ['1','2','4']:
    changes = []
    previous = None
    for row in samples:
        current = row['state']['nodes'][node]
        value = (current['readiness'], current['reported_state'])
        if value != previous:
            changes.append({'elapsed': row['elapsed'], 'readiness':value[0], 'reported_state':value[1],
                            'channel':current['current_channel'], 'heartbeat_age':current['last_heartbeat_ago_seconds']})
            previous = value
    summary['nodes'][node] = {'non_ready_samples': sum(r['state']['nodes'][node]['readiness']!='READY' for r in samples),
                             'transitions':changes,
                             'max_heartbeat_age': max(r['state']['nodes'][node]['last_heartbeat_ago_seconds'] for r in samples)}
summary['passed'] = all(v['non_ready_samples']==0 for v in summary['nodes'].values())
(args.output/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
print(json.dumps(summary,ensure_ascii=False))
