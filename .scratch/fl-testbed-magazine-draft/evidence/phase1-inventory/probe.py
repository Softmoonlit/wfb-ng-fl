import glob
import hashlib
import json
import os
import pathlib
import platform
import socket
import subprocess
import time


def run(args, timeout=12):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return {"exit": p.returncode, "stdout": p.stdout.strip(), "stderr": p.stderr.strip()}
    except Exception as exc:
        return {"error": str(exc)}


result = {"hostname": socket.gethostname(), "time_utc": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), "platform": platform.platform()}
commands = {
    "identity": ["id"],
    "sudo": ["sudo", "-n", "true"],
    "disk": ["df", "-h", "/", "/srv"],
    "memory": ["free", "-m"],
    "usb": ["lsusb"],
    "radio": ["iw", "dev"],
    "regulatory": ["iw", "reg", "get"],
    "gpu": ["nvidia-smi", "--query-gpu=name,temperature.gpu,memory.total,memory.used", "--format=csv,noheader"],
    "services": ["systemctl", "show", "wfb-fl-server-daemon", "wfb-fl-client-daemon", "-p", "Id", "-p", "ActiveState", "-p", "SubState", "-p", "MainPID", "-p", "ExecStart"],
    "processes": ["ps", "-eo", "pid,args"],
    "install": ["python3", "-c", "import importlib.metadata as m, importlib.util as u; s=u.find_spec('wfb_ng'); print('module=',s.origin if s else None); print('distributions=',[(d.metadata.get('Name'),d.version,str(d.locate_file(''))) for d in m.distributions() if 'wfb' in d.metadata.get('Name','').lower()])"],
}
result["checks"] = {k: run(v) for k, v in commands.items()}
if 'stdout' in result['checks']['processes']:
    result['checks']['processes']['stdout'] = '\n'.join(line for line in result['checks']['processes']['stdout'].splitlines() if any(x in line for x in ['wfb', 'uftp', 'fl-server', 'fl-client']))
interfaces = []
for path in pathlib.Path('/sys/class/net').iterdir():
    if (path / 'phy80211').exists():
        driver = path / 'device/driver'
        interfaces.append({'interface': path.name, 'driver': os.path.basename(os.path.realpath(driver)) if driver.exists() else None, 'address': (path / 'address').read_text().strip()})
result['wireless_interfaces'] = interfaces
result['configs'] = {}
for p in glob.glob('/etc/wfb-ng-fl/*.json'):
    try:
        data = json.loads(pathlib.Path(p).read_text())
        result['configs'][p] = {k: v for k, v in data.items() if not any(s in k.lower() for s in ('key','password','secret','token'))}
    except Exception as exc:
        result['configs'][p] = {'error': str(exc)}
result['native_processes'] = []
for p in pathlib.Path('/proc').glob('[0-9]*/exe'):
    try:
        target = os.readlink(p)
        if 'wfb_v6' in target:
            result['native_processes'].append({'pid': int(p.parent.name), 'exe': target, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()})
    except (PermissionError, FileNotFoundError, ProcessLookupError):
        pass
result['thermals'] = {}
for p in pathlib.Path('/sys/class/thermal').glob('thermal_zone*'):
    try:
        result['thermals'][(p/'type').read_text().strip()] = (p/'temp').read_text().strip()
    except (OSError, TypeError) as exc:
        result['thermals'][p.name] = {'error': str(exc)}
print(json.dumps(result, ensure_ascii=False, indent=2))
