import concurrent.futures
import datetime
import json
import pathlib
import subprocess

root = pathlib.Path(__file__).resolve().parent / (
    'txpower-index18-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
root.mkdir()
remote = r'''set -eu
pid=$(pgrep -f '^/usr/bin/wfb_v6_uplink ' | head -1)
[ -n "$pid" ]
iface=$(python3 -c 'import pathlib,sys; a=pathlib.Path("/proc/"+sys.argv[1]+"/cmdline").read_bytes().split(bytes([0])); i=a.index(b"--air-interface"); print(a[i+1].decode())' "$pid")
[ -n "$iface" ]
old=$(iw dev "$iface" info 2>&1 | sed -n '/txpower/p' || true)
iw dev "$iface" set txpower fixed -1800
new=$(iw dev "$iface" info 2>&1 | sed -n '/txpower/p')
printf '%s\n' "$iface" "$old" "$new"
'''
hosts = ['fl00', 'fl01', 'fl02', 'fl04', 'fl05', 'fl06', 'fl07', 'fl08', 'fl09', 'fl10']

def apply(host):
    command = (['sudo', '-n', 'bash', '-s'] if host == 'fl00' else
               ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=6', host, 'sudo -n bash -s'])
    try:
        result = subprocess.run(command, input=remote, text=True,
                                capture_output=True, timeout=20)
        record = {'host': host, 'exit': result.returncode,
                  'stdout': result.stdout, 'stderr': result.stderr}
        (root / (host + '.json')).write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + '\n')
        return record
    except Exception as exc:
        return {'host': host, 'exit': -1, 'error': repr(exc)}

with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
    records = list(executor.map(apply, hosts))
(root / 'summary.json').write_text(json.dumps(records, ensure_ascii=False, indent=2) + '\n')
for record in records:
    print(record['host'], record['exit'], record.get('stdout', '').replace('\n', ' | '), record.get('stderr', ''))
print('EVIDENCE', root)
