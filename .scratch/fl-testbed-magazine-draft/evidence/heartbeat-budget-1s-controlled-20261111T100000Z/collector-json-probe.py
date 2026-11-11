#!/usr/bin/env python3
import json
from pathlib import Path
import socket
import sys

# argv: <script> <remote_root>
remote_root_arg = sys.argv[1]
host = socket.gethostname()
raw = Path(remote_root_arg) / host / 'raw.json'
try:
    value = json.loads(raw.read_text())
except Exception as exc:
    raise SystemExit('remote raw JSON parse failed: ' + repr(exc))
if value.get('schema_version') != 3:
    raise SystemExit('schema mismatch: ' + repr(value.get('schema_version')))
if value.get('errors') != []:
    raise SystemExit('collector errors: ' + repr(value.get('errors')))
window = value.get('capture_window', {})
analysis = value.get('analysis', {})
stats = analysis.get('packet_statistics_delta', {})
print(json.dumps({
    'host': host,
    'schema_version': value.get('schema_version'),
    'raw_size_bytes': raw.stat().st_size,
    'stop_requested': value.get('stop_requested'),
    'capture_elapsed_seconds': window.get('elapsed_mono_seconds'),
    'request_count': analysis.get('request_count'),
    'paired_confirmed_count': analysis.get('paired_confirmed_count'),
    'timely_count': analysis.get('timely_count'),
    'late_count': analysis.get('late_count'),
    'timely_1000ms_count': analysis.get('timely_1000ms_count'),
    'late_1000ms_count': analysis.get('late_1000ms_count'),
    'unmatched_count': analysis.get('unmatched_count'),
    'right_censored_count': analysis.get('right_censored_count'),
    'challenge_count': analysis.get('challenge_count'),
    'drops': stats.get('drops'),
    'packets': stats.get('packets'),
}, ensure_ascii=False))
