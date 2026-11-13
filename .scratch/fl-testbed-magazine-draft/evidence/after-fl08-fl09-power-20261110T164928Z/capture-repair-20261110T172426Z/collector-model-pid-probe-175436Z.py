#!/usr/bin/env python3
import json
import os
from pathlib import Path
import signal
import socket
import sys

REMOTE_ROOT = '/var/tmp/wfb-heartbeat-capture-repair-20261110T175436Z'
host = socket.gethostname()
remote_dir = Path(REMOTE_ROOT) / host
script = str(remote_dir / 'capture-heartbeats-batch.py')
expected = ['/usr/bin/python3', '-I', script, '--duration', '1500', '--clients', 'all', '--ports', '9001']
pid = int(sys.argv[1])
post = '--post' in sys.argv
term = '--term' in sys.argv
proc = Path('/proc', str(pid))
alive = proc.exists()
argv = []
if alive:
    argv = proc.joinpath('cmdline').read_bytes().decode().rstrip('\0').split('\0')
    if argv != expected:
        raise SystemExit(json.dumps({'status': 'refused', 'pid': pid, 'argv': argv, 'expected': expected}))
if not post and not alive:
    raise SystemExit(json.dumps({'status': 'not_alive', 'pid': pid, 'argv': argv}))
stat = remote_dir.stat()
packet_path = proc / 'net' / 'packet'
packet_text = packet_path.read_text() if packet_path.exists() else ''
result = {
    'status': 'alive_verified' if alive else 'not_alive',
    'pid': pid,
    'argv': argv,
    'host': host,
    'term_requested': term,
    'remote_dir_mode': oct(stat.st_mode & 0o777),
    'raw_path': str(remote_dir / 'raw.json'),
    'raw_exists': (remote_dir / 'raw.json').exists(),
    'raw_size_bytes': (remote_dir / 'raw.json').stat().st_size if (remote_dir / 'raw.json').exists() else None,
    'packet_proc_readable': bool(packet_text),
    'packet_proc_rows': packet_text.splitlines()[:20],
}
if alive and term:
    os.kill(pid, signal.SIGTERM)
    result['status'] = 'term_sent'
print(json.dumps(result, ensure_ascii=False))
