import hashlib
import json
import os
import pathlib
import subprocess


def run(args, timeout=15):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return {'exit': p.returncode, 'stdout': p.stdout.strip(), 'stderr': p.stderr.strip()}
    except Exception as exc:
        return {'error': str(exc)}


result = {}
result['native'] = []
for p in pathlib.Path('/proc').glob('[0-9]*/exe'):
    try:
        target = os.readlink(p)
        if 'wfb_v6' in target:
            result['native'].append({'pid': int(p.parent.name), 'exe': target, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'args': (p.parent/'cmdline').read_bytes().decode().replace('\0',' ')})
    except (OSError, ProcessLookupError):
        pass
root = pathlib.Path('/usr/local/lib/python3.10/dist-packages/wfb_ng')
result['installed'] = {}
for relative in ['fl/build_identity.json','fl/radio.py','fl/client_daemon.py','fl/control.py','fl/server_daemon.py']:
    p = root/relative
    if p.exists():
        result['installed'][relative] = {'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
        if relative.endswith('.json'):
            result['installed'][relative]['data'] = json.loads(p.read_text())
result['journal'] = run(['journalctl','--no-pager','-u','wfb-fl-client-daemon','-u','wfb-fl-server-daemon','-n','35','-o','short-iso'])
result['tools'] = {tool: run(['sh','-c','command -v "$1"','probe',tool]) for tool in ['uftp','uftpd','wfb_v6_uplink','make','gcc','pkg-config']}
result['libraries'] = run(['pkg-config','--modversion','libsodium','libpcap'])
result['network_manager'] = run(['nmcli','-t','-f','DEVICE,TYPE,STATE','device'])
print(json.dumps(result,ensure_ascii=False,indent=2))
