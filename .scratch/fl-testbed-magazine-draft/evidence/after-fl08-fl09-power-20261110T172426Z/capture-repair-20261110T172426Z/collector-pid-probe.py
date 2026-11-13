#!/usr/bin/env python3
"""只验证远端 collector PID；可选地只 TERM 该已验证 PID。"""
import json
from pathlib import Path
import signal
import socket
import sys

pid = int(sys.argv[1])
post = '--post' in sys.argv
term = '--term' in sys.argv
REMOTE_ROOT = '/var/tmp/wfb-heartbeat-capture-repair-20261110T172426Z'
host = socket.gethostname()
expected = ['/usr/bin/python3', '-I', REMOTE_ROOT + '/' + host + '/capture-heartbeats-batch.py',
            '--duration', '5', '--clients', 'all', '--ports', '9001']
proc = Path('/proc', str(pid))
alive = proc.exists()
argv = []
if alive:
    argv = proc.joinpath('cmdline').read_bytes().decode().rstrip('\0').split('\0')
    if argv != expected:
        raise SystemExit(json.dumps({'status': 'refused', 'pid': pid, 'argv': argv, 'expected': expected}))
if not post and not alive:
    raise SystemExit(json.dumps({'status': 'not_alive', 'pid': pid, 'argv': argv}))
result = {'status': 'alive_verified' if alive else 'not_alive', 'pid': pid, 'argv': argv,
          'host': socket.gethostname(), 'term_requested': term}
if alive and term:
    proc.joinpath('cmdline').read_bytes()
    import os
    os.kill(pid, signal.SIGTERM)
    result['status'] = 'term_sent'
print(json.dumps(result, ensure_ascii=False))
