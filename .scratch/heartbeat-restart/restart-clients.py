"""受控重启fl01/fl05并记录恢复过程，服务端必须保持同一进程。"""
import concurrent.futures
import json
import pathlib
import subprocess
import sys
import time
import urllib.request

mode, evidence_arg = sys.argv[1:]
assert mode in {'service', 'reboot'}
root = pathlib.Path(evidence_arg)
old_rows = json.loads((root / ('before-restart-identities.json' if mode == 'service' else 'after-service-identities.json')).read_text())
old = {r['host']: json.loads(r['out']) for r in old_rows}
server_pid = old['fl00']['loaded']['pid']
log = root / (mode + '-recovery.jsonl')
start = time.monotonic()


def command(host):
    action = 'sudo -n systemctl restart wfb-fl-client-daemon.service' if mode == 'service' else 'sudo -n systemctl reboot'
    p = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=6', host, action],
                       text=True, capture_output=True, timeout=30)
    return {'host': host, 'exit': p.returncode, 'out': p.stdout, 'err': p.stderr}


with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    results = list(pool.map(command, ['fl01', 'fl05']))
(root / (mode + '-commands.json')).write_text(json.dumps(results, indent=2) + '\n')
assert all(r['exit'] == 0 for r in results), results
new_boots = {}
with log.open('w') as output:
    for i in range(140):
        row = {'elapsed': time.monotonic() - start}
        if mode == 'reboot' and i % 5 == 0:
            for host in ['fl01', 'fl05']:
                try:
                    p = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=2', host,
                                        'cat /proc/sys/kernel/random/boot_id'], text=True, capture_output=True, timeout=4)
                    if p.returncode == 0:
                        new_boots[host] = p.stdout.strip()
                except subprocess.TimeoutExpired:
                    pass
        try:
            with urllib.request.urlopen('http://127.0.0.1:9090/api/v1/status', timeout=3) as response:
                status = json.load(response)
            row['nodes'] = {n: status['nodes'][n] for n in ['1', '2', '4', '5', '6']}
            row['new_kernel_boots'] = dict(new_boots)
            server = json.loads(pathlib.Path('/srv/wfb-ng-fl/releases', root.name, 'loaded.json').read_text())
            assert server['pid'] == server_pid, '服务端进程意外变化'
            ready = all(status['nodes'][n]['readiness'] == 'READY' for n in ['1', '5'])
            if mode == 'reboot':
                ready = ready and all(new_boots.get(h) and new_boots[h] != old[h]['kernel_boot_id'] for h in ['fl01', 'fl05'])
            output.write(json.dumps(row) + '\n')
            output.flush()
            if ready and i >= 3:
                print(json.dumps({'mode': mode, 'recovered_seconds': row['elapsed'],
                                  'server_pid': server_pid, 'new_boots': new_boots}), flush=True)
                break
        except (OSError, ValueError) as exc:
            output.write(json.dumps({'elapsed': time.monotonic() - start, 'error': repr(exc)}) + '\n')
            output.flush()
        if time.monotonic() - start >= 150:
            raise SystemExit('恢复验证超时；已停止推进')
        time.sleep(1)
    else:
        raise SystemExit('恢复验证超时；已停止推进')
