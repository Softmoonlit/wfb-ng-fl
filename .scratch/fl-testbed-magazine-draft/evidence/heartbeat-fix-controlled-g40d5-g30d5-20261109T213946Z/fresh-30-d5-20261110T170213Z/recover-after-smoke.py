#!/usr/bin/env python3
import importlib.util
import json
import pathlib
import subprocess
import time
import concurrent.futures

ROOT = pathlib.Path(__file__).resolve().parent
RUN_ID = 'fresh30d5-smoke-20261110'
HOSTS = ['fl00', 'fl01', 'fl02', 'fl03', 'fl05', 'fl06', 'fl07', 'fl08', 'fl09', 'fl10']
CLIENTS = HOSTS[1:]

spec = importlib.util.spec_from_file_location('recovery_driver', ROOT / 'driver-base.py')
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)
hspec = importlib.util.spec_from_file_location('recovery_helpers', ROOT / 'helpers.py')
helpers = importlib.util.module_from_spec(hspec)
hspec.loader.exec_module(helpers)
helpers.d = d
helpers.ROOT = ROOT
helpers.write = lambda path, value: pathlib.Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
d.MATRIX['parent_GO_verified'] = True
d.MATRIX['status'] = 'recovery-after-smoke-stop'
d.MATRIX['deadline_monotonic'] = time.monotonic() + 600
since = int(time.time())

status_before = d.status()
(ROOT / 'recovery-smoke-status-before.json').write_text(json.dumps(status_before, ensure_ascii=False, indent=2) + '\n')
if status_before.get('active_job') is not None or status_before.get('server_state') != 'IDLE':
    raise RuntimeError('recovery refused: active job or non-IDLE server')

progress = ROOT / 'smoke-30-d5' / RUN_ID / 'progress.jsonl'
rows = [json.loads(line) for line in progress.read_text().splitlines()]
final = next((row for row in reversed(rows) if row.get('phase') == 'result'), {})
progress_upload = [row for row in rows if row.get('phase') == 'upload_progress']
(ROOT / 'smoke-failure-field.json').write_text(json.dumps(dict(run_id=RUN_ID, final=final, latest_upload_progress=progress_upload[-1] if progress_upload else None, protected='no partial/model files deleted'), ensure_ascii=False, indent=2) + '\n')

collected = helpers.collect(RUN_ID, since, '-recovery-field')
cleanup = helpers.exact_cleanup(RUN_ID)
(ROOT / 'smoke-failure-collection.json').write_text(json.dumps(dict(cleanup_ok=cleanup, hosts=[row.get('host') for row in collected]), ensure_ascii=False, indent=2) + '\n')

restart_code = "import subprocess,json,socket,time\nunit='wfb-fl-client-daemon.service'\np=subprocess.run(['systemctl','restart',unit],capture_output=True,text=True,timeout=20)\nprint(json.dumps(dict(host=socket.gethostname(),unit=unit,returncode=p.returncode,stdout=p.stdout,stderr=p.stderr)))\nraise SystemExit(p.returncode)"
def restart_client(host):
    return d.remote(host, restart_code, timeout=30)
with concurrent.futures.ThreadPoolExecutor(max_workers=len(CLIENTS)) as pool:
    client_rows = list(pool.map(restart_client, CLIENTS))
server = subprocess.run(['sudo', '-n', 'systemctl', 'restart', 'wfb-fl-server-daemon.service'], capture_output=True, text=True, timeout=30)
restart_result = dict(clients=client_rows, server=dict(returncode=server.returncode, stdout=server.stdout, stderr=server.stderr), wall=time.time())
(ROOT / 'standard-service-restart.json').write_text(json.dumps(restart_result, ensure_ascii=False, indent=2) + '\n')
if server.returncode != 0 or any(row.get('returncode') != 0 for row in client_rows):
    raise RuntimeError('standard service restart failure')
time.sleep(5)
audit = d.audit(ROOT, 'recovery-after-smoke', 30, 5, since=since, expected_fec=12, release_root=d.RELEASE)
gate = d.gate(ROOT / 'recovery-after-smoke-ready')
result = dict(status='recovered' if audit.get('ok') and gate.get('passed') else 'recovery-incomplete', cleanup_ok=cleanup, audit_ok=audit.get('ok'), gate_passed=gate.get('passed'), restart=restart_result, wall=time.time())
(ROOT / 'recovery-after-smoke-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(result, ensure_ascii=False, indent=2))
raise SystemExit(0 if result['status'] == 'recovered' else 1)
