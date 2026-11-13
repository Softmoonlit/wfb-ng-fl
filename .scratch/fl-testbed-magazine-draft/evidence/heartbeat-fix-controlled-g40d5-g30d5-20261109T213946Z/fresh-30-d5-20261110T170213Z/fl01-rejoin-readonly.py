#!/usr/bin/env python3
import json
import pathlib
import shlex
import subprocess
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
HOST = 'fl01'
KNOWN_IP = '192.168.0.191'
MAX_RETRIES = 2
RETRY_GAP = 15


def local(argv, timeout):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return dict(argv=argv, returncode=p.returncode, stdout=p.stdout, stderr=p.stderr)
    except Exception as exc:
        return dict(argv=argv, error=repr(exc))


def remote(code):
    command = shlex.join(['sudo', '-n', 'python3', '-c', code])
    return local(['ssh', '-T', '-oBatchMode=yes', '-oConnectTimeout=5', HOST, command], 12)

probe = r'''import json, pathlib, socket, subprocess, time

def run(argv):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=5)
        return dict(returncode=p.returncode, stdout=p.stdout, stderr=p.stderr)
    except Exception as exc:
        return dict(error=repr(exc))

def read(path):
    try:
        return pathlib.Path(path).read_bytes().rstrip(b'\\0').decode(errors='replace')
    except Exception as exc:
        return 'ERROR:' + repr(exc)

role = 'client'
unit = 'wfb-fl-client-daemon.service'
service = run(['systemctl', 'show', unit, '-p', 'MainPID', '-p', 'ActiveState', '-p', 'SubState'])
args = []
for p in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
    try:
        a = p.read_bytes().rstrip(b'\\0').decode(errors='replace').split('\\0')
        if a and a[0] == '/usr/bin/wfb_v6_uplink':
            args.append(dict(pid=p.parent.name, argv=a, exe_sha256=__import__('hashlib').sha256((p.parent / 'exe').read_bytes()).hexdigest()))
    except (OSError, UnicodeError):
        pass
interfaces = run(['bash', '-lc', 'for p in /sys/class/net/*; do [ "$(basename "$(readlink -f "$p/device/driver")")" = rtl88xxau_wfb ] && iw dev "$(basename "$p")" info; done'])
get_radio = None
get_fec = None
if len(args) == 1:
    import socket as so, struct
    try:
        port = int(args[0]['argv'][args[0]['argv'].index('--radio-control-port') + 1])
        with so.socket(so.AF_INET, so.SOCK_DGRAM) as sock:
            sock.settimeout(2)
            sock.sendto(struct.pack('!IB', 0x434f4d4d, 4), ('127.0.0.1', port))
            get_radio = list(struct.unpack('!II7B', sock.recvfrom(4096)[0]))
        with so.socket(so.AF_INET, so.SOCK_DGRAM) as sock:
            sock.settimeout(2)
            sock.sendto(struct.pack('!IB', 0x434f4d4d, 3), ('127.0.0.1', port))
            get_fec = list(struct.unpack('!II2B', sock.recvfrom(4096)[0]))
    except Exception as exc:
        get_radio = 'ERROR:' + repr(exc)
        get_fec = 'ERROR:' + repr(exc)
space = pathlib.Path('/srv/wfb-ng-fl')
st = __import__('os').statvfs(str(space))
power = run(['nvpmodel', '-q'])
limits = {}
for path in ['/sys/devices/system/cpu/online', '/sys/devices/platform/17000000.gpu/devfreq_dev/max_freq', '/sys/kernel/nvpmodel_clk_cap/emc', '/sys/kernel/debug/bpmp/debug/clk/emc/mrq_rate_locked'] + ['/sys/devices/system/cpu/cpu' + str(i) + '/cpufreq/scaling_max_freq' for i in range(6)]:
    limits[path] = read(path)
print(json.dumps(dict(hostname=socket.gethostname(), model=read('/proc/device-tree/model'), board_id=read('/proc/device-tree/serial-number'), machine_id=read('/etc/machine-id'), boot_id=read('/proc/sys/kernel/random/boot_id'), service=service, native=args, iw=interfaces, get_radio=get_radio, get_fec=get_fec, power=power, limits=limits, storage=dict(available_bytes=st.f_bavail * st.f_frsize, available_inodes=st.f_favail), monotonic=time.monotonic()), ensure_ascii=False))
'''

attempts = []
for attempt in range(1, MAX_RETRIES + 1):
    began = time.time()
    ping = local(['ping', '-c', '1', '-W', '1', KNOWN_IP], 4)
    ssh = remote(probe)
    row = dict(attempt=attempt, wall=began, known_host=HOST, known_ip=KNOWN_IP, ping=ping, ssh=ssh)
    if ssh.get('returncode') == 0:
        try:
            row['remote'] = json.loads(ssh['stdout'])
        except ValueError as exc:
            row['remote_parse_error'] = repr(exc)
    attempts.append(row)
    (ROOT / 'fl01-rejoin-checkpoint.json').write_text(json.dumps(dict(status='running', attempts=attempts), ensure_ascii=False, indent=2) + '\n')
    if ssh.get('returncode') == 0 and 'remote' in row:
        break
    if attempt < MAX_RETRIES:
        time.sleep(RETRY_GAP)

try:
    with urllib.request.urlopen('http://127.0.0.1:9090/api/v1/status', timeout=4) as response:
        registry = json.load(response)
except Exception as exc:
    registry = dict(error=repr(exc))
result = dict(status='rejoined' if any('remote' in row for row in attempts) else 'still-unreachable', known_host=HOST, known_ip=KNOWN_IP, max_retries=MAX_RETRIES, attempts=attempts, registry=registry, wall=time.time(), note='Only the existing fl01 hostname and 192.168.0.191 were used; no unknown address discovery.')
(ROOT / 'fl01-rejoin-readonly.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
(ROOT / 'fl01-rejoin-checkpoint.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(dict(status=result['status'], attempts=len(attempts), registry_online=sorted(k for k,v in registry.get('nodes', {}).items() if v.get('readiness') != 'OFFLINE') if isinstance(registry, dict) else None), ensure_ascii=False))
