#!/usr/bin/env python3
import hashlib
import json
import pathlib
import socket
import struct
import subprocess
import time

RELEASE = '/srv/wfb-ng-fl/releases/heartbeat-66f15358d309-20261110T114225Z'
NATIVE_SHA = '44d6bbf500f51df4d0b3ae8cd7a53c4a45cb55b09ca66cb5217e7ac75674feb2'

def run(argv, timeout=10):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return {'argv': argv, 'returncode': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr}
    except Exception as exc:
        return {'argv': argv, 'error': repr(exc)}

def sha(path):
    h = hashlib.sha256()
    with pathlib.Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()

def query(port, code, fmt):
    request = struct.pack('!IB', 0x434f4d4d, code)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(3)
            sock.sendto(request, ('127.0.0.1', port))
            raw, address = sock.recvfrom(4096)
        return {'address': list(address), 'request_hex': request.hex(), 'raw_hex': raw.hex(),
                'decoded': list(struct.unpack(fmt, raw))}
    except Exception as exc:
        return {'request_hex': request.hex(), 'error': repr(exc)}

host = socket.gethostname()
boot_id = pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
service = run(['systemctl', 'show', 'wfb-fl-client-daemon.service', '-p', 'ActiveState', '-p', 'SubState', '-p', 'MainPID'])
main_pid = int(next((line.split('=', 1)[1] for line in service['stdout'].splitlines()
                     if line.startswith('MainPID=')), '0'))
daemon_argv = pathlib.Path('/proc', str(main_pid), 'cmdline').read_bytes().decode().rstrip(chr(0)).split(chr(0)) if main_pid else []
native = []
for proc in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
    try:
        argv = proc.read_bytes().decode().rstrip(chr(0)).split(chr(0))
        if not argv or argv[0] != '/usr/bin/wfb_v6_uplink':
            continue
        iface = argv[argv.index('--air-interface') + 1]
        port = int(argv[argv.index('--radio-control-port') + 1])
        dev = pathlib.Path('/sys/class/net') / iface
        usb = (dev / 'device').resolve()
        speed = next((item / 'speed' for item in (usb, *usb.parents) if (item / 'speed').exists()), None)
        native.append({
            'pid': proc.parent.name, 'argv': argv, 'proc_exe': str((proc.parent / 'exe').resolve()),
            'proc_sha256': sha(proc.parent / 'exe'), 'interface': iface,
            'mac': (dev / 'address').read_text().strip(),
            'driver': (dev / 'device/driver').resolve().name,
            'usb_path': usb.name, 'usb_speed': speed.read_text().strip() if speed else None,
            'radio_query': query(port, 4, '!II7B'), 'fec_query': query(port, 3, '!II2B'),
            'iw': run(['iw', 'dev', iface, 'info']),
        })
    except Exception as exc:
        native.append({'probe_error': repr(exc)})
manifest = json.loads((pathlib.Path(RELEASE) / 'manifest.json').read_text())
identity = json.loads((pathlib.Path(manifest['installed']) / 'build_identity.json').read_text())
result = {
    'probe_type': 'fl02_post_power_recovery_readonly',
    'captured_at_wall_ns': time.time_ns(), 'captured_at_mono_ns': time.monotonic_ns(),
    'host': host, 'boot_id': boot_id, 'uptime': pathlib.Path('/proc/uptime').read_text().strip(),
    'power_query': run(['nvpmodel', '-q']),
    'power_service': run(['systemctl', 'show', 'nvpmodel.service', '-p', 'Result', '-p', 'ExecMainStatus', '-p', 'ActiveState']),
    'service': service, 'daemon_pid': main_pid, 'daemon_argv': daemon_argv, 'native': native,
    'release': {'root': RELEASE, 'commit': manifest.get('commit'), 'native_sha': manifest.get('native_sha'),
                'source_native_sha256': sha(pathlib.Path(RELEASE) / 'source/wfb_v6_uplink'),
                'installed_native_sha256': sha('/usr/bin/wfb_v6_uplink'), 'identity': identity},
    'kernel_recent': run(['journalctl', '-k', '--since=-10 minutes', '--no-pager', '-o', 'short-iso']),
}
print(json.dumps(result, ensure_ascii=False, indent=2))
