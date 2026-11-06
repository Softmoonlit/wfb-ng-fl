"""记录固定目标重启前后身份；不修改节点注册表。"""
import concurrent.futures
import json
import pathlib
import subprocess
import sys

phase, evidence_arg = sys.argv[1:]
root = pathlib.Path(evidence_arg)
release = root.name
script = '''import json,pathlib,subprocess,socket,time
host=socket.gethostname()
role='server' if host=='fl00' else 'client'
unit='wfb-fl-'+role+'-daemon.service'
print(json.dumps({'host':host,'kernel_boot_id':pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'uptime':pathlib.Path('/proc/uptime').read_text().split()[0],'unit':subprocess.check_output(['systemctl','show',unit,'-p','MainPID','-p','ActiveState','-p','ExecMainStartTimestampMonotonic'],text=True),'loaded':json.loads(pathlib.Path('/srv/wfb-ng-fl/releases/RELEASE/loaded.json').read_text()),'nvpmodel':subprocess.check_output(['/usr/sbin/nvpmodel','-q'],text=True)}))
'''.replace('RELEASE', release)


def snapshot(host):
    cmd = ['sudo', '-n', '/usr/bin/python3', '-'] if host == 'fl00' else ['ssh', '-o', 'BatchMode=yes',
           '-o', 'ConnectTimeout=6', host, 'sudo -n /usr/bin/python3 -']
    p = subprocess.run(cmd, input=script, text=True, capture_output=True, timeout=15)
    result = {'host': host, 'exit': p.returncode, 'out': p.stdout, 'err': p.stderr}
    return result


with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
    results = list(pool.map(snapshot, ['fl00', 'fl01', 'fl05']))
(root / (phase + '-identities.json')).write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n')
for row in results:
    print(row['host'], row['exit'])
assert all(r['exit'] == 0 for r in results)
