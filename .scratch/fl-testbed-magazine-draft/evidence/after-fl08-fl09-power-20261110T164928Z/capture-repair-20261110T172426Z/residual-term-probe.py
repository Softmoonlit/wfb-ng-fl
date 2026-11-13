#!/usr/bin/env python3
import os
from pathlib import Path
import signal
import sys

pid = int(sys.argv[1])
argv = Path('/proc', str(pid), 'cmdline').read_bytes().decode().rstrip('\0').split('\0')
expected = ['/usr/bin/python3', '-I', '-', '--duration', '1500', '--clients', 'all', '--ports', '9001']
if argv != expected:
    raise SystemExit('refused pid=%s argv=%r expected=%r' % (pid, argv, expected))
os.kill(pid, signal.SIGTERM)
print('term pid=%s argv=%r' % (pid, argv))
