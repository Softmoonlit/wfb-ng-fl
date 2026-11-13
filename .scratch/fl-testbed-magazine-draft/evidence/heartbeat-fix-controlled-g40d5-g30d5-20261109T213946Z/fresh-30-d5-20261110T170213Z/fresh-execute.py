#!/usr/bin/env python3
"""Fresh-only 30/5 smoke gate followed by one model run; no retry."""
import concurrent.futures
import importlib.util
import json
import pathlib
import signal
import subprocess
import time

ROOT = pathlib.Path(__file__).resolve().parent
NODES = [1, 2, 3, 5, 6, 7, 8, 9, 10]
HOSTS = ['fl00'] + [f'fl{n:02d}' for n in NODES]
COMMIT = 'cccac233291c5a79ca7a66bb2ebb210497ab4071'
NATIVE = '44d6bbf500f51df4d0b3ae8cd7a53c4a45cb55b09ca66cb5217e7ac75674feb2'
RELEASE = '/srv/wfb-ng-fl/releases/heartbeat-cccac233291c-20261109T183910Z'
BUNDLE = json.loads((ROOT / 'bundle-manifest.json').read_text())['bundle_sha256']
PAYLOAD = '/home/fl/projects/wfb-ng-fl/.scratch/fl-testbed-magazine-draft/models/cifar10-resnet18-seed7.pt'
MODEL_MANIFEST = str(ROOT / 'model-manifest.json')
GLOBAL_LIMIT = 1800
DIRECTION_LIMIT = 600

spec = importlib.util.spec_from_file_location('fresh_driver_execute', ROOT / 'driver-base.py')
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)
helper_spec = importlib.util.spec_from_file_location('fresh_helpers_execute', ROOT / 'helpers.py')
helpers = importlib.util.module_from_spec(helper_spec)
helper_spec.loader.exec_module(helpers)
helpers.d = d
helpers.ROOT = ROOT
helpers.write = lambda path, value: pathlib.Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
d.MATRIX['parent_GO_verified'] = True
d.MATRIX['status'] = 'executing-30-d5'
d.MATRIX['started_wall'] = time.time()
d.MATRIX['deadline_monotonic'] = time.monotonic() + GLOBAL_LIMIT
SINCE = int(time.time())


def checkpoint(value):
    with (ROOT / 'execution-checkpoints.jsonl').open('a') as stream:
        stream.write(json.dumps(dict(wall=time.time(), monotonic=time.monotonic(), **value), ensure_ascii=False) + '\n')
    print(json.dumps(value, ensure_ascii=False), flush=True)


def read_progress(path):
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows


def run_phase(mode, phase_root, run_id):
    phase_root.mkdir(exist_ok=False)
    payload = ['--smoke'] if mode == 'smoke' else ['--payload', PAYLOAD, '--model-manifest', MODEL_MANIFEST]
    command = ['python3', str(ROOT / 'runner.py'), *payload,
               '--native-sha', NATIVE, '--release-commit', COMMIT, '--release-root', RELEASE,
               '--grant-duration-ms', '30', '--guard-interval-ms', '5',
               '--channel', '157', '--bandwidth-mhz', '40', '--downlink-mcs', '3', '--uplink-mcs', '4',
               '--fec-n', '12', '--uftp-rate-kbps', '12000', '--run-id', run_id,
               '--output', str(phase_root), '--execute', '--reviewed-sha', BUNDLE]
    (phase_root / 'command.json').write_text(json.dumps(command, ensure_ascii=False, indent=2) + '\n')
    started = time.monotonic()
    log = (phase_root / 'runner.log').open('w', buffering=1)
    proc = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
    (phase_root / 'runner-process.json').write_text(json.dumps(dict(pid=proc.pid, started_wall=time.time(), run_id=run_id), indent=2) + '\n')
    progress = phase_root / run_id / 'progress.jsonl'
    last_report = 0.0
    stop_sent = False
    while proc.poll() is None:
        rows = read_progress(progress)
        now = time.monotonic()
        latest = rows[-1] if rows else {}
        starts = [row for row in rows if row.get('event') == 'dispatch_start']
        phases = {phase: [row for row in rows if row.get('phase') == phase and row.get('ok') is True]
                  for phase in ('prepare', 'publish', 'submit', 'verify')}
        uploads = [row for row in rows if row.get('phase') == 'upload_progress']
        if now - last_report >= 55:
            checkpoint(dict(kind='phase-checkpoint', mode=mode, run_id=run_id,
                            elapsed_seconds=now - started, phase=latest.get('phase'),
                            current_dispatch=starts[-1].get('phase') if starts else None,
                            publish_completed=[row.get('host') for row in phases['publish']],
                            submit_completed=[row.get('host') for row in phases['submit']],
                            latest_upload_progress=uploads[-1] if uploads else None))
            last_report = now
        if now >= d.MATRIX['deadline_monotonic'] - 10 and not stop_sent:
            checkpoint(dict(kind='global-boundary-stop', mode=mode, run_id=run_id))
            proc.send_signal(signal.SIGINT)
            stop_sent = True
        time.sleep(5)
    proc.wait(timeout=30)
    log.close()
    rows = read_progress(progress)
    final = next((row for row in reversed(rows) if row.get('phase') == 'result'), {})
    summary = dict(mode=mode, run_id=run_id, returncode=proc.returncode,
                   transfer_verified=final.get('transfer_verified', False),
                   cleanup_ok=final.get('cleanup_ok', False), restore_ok=final.get('restore_ok', False),
                   elapsed_seconds=time.monotonic() - started, final=final,
                   progress_path=str(progress))
    (phase_root / 'run-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    checkpoint(dict(kind='phase-complete', **{key: summary[key] for key in ('mode', 'run_id', 'returncode', 'transfer_verified', 'cleanup_ok', 'restore_ok', 'elapsed_seconds')}))
    return summary


def post_phase(label, run_id, phase_root, expected_success):
    collected = helpers.collect(run_id, SINCE, '-after-exact')
    cleanup = helpers.exact_cleanup(run_id)
    audit = d.audit(ROOT, label, 30, 5, run_id=run_id, since=SINCE, expected_fec=12, release_root=d.RELEASE)
    gate_root = ROOT / (label + '-ready')
    gate = d.gate(gate_root)
    result = dict(label=label, run_id=run_id, cleanup=cleanup, audit_ok=audit.get('ok'), gate_passed=gate.get('passed'), expected_success=expected_success,
                  collected_hosts=[row.get('host') for row in collected], wall=time.time())
    (ROOT / (label + '-post.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    checkpoint(dict(kind='post-phase', **result))
    if expected_success and not (cleanup and audit.get('ok') and gate.get('passed')):
        raise RuntimeError(label + ' post-phase audit/gate failed')
    return result


smoke = None
model = None
try:
    smoke_id = 'fresh30d5-smoke-20261110'
    smoke = run_phase('smoke', ROOT / 'smoke-30-d5', smoke_id)
    if not all(smoke.get(key) is True for key in ('transfer_verified', 'cleanup_ok', 'restore_ok')):
        raise RuntimeError('smoke gate failed: ' + repr(smoke))
    post_phase('after-30-d5-smoke', smoke_id, ROOT / 'smoke-30-d5', True)
    if time.monotonic() >= d.MATRIX['deadline_monotonic']:
        raise RuntimeError('global boundary reached after smoke')
    model_id = 'fresh30d5-model-20261110'
    model = run_phase('model', ROOT / 'model-30-d5', model_id)
    if not all(model.get(key) is True for key in ('transfer_verified', 'cleanup_ok', 'restore_ok')):
        # Preserve the failed model and its partial files; exact cleanup is still safe and does not delete payloads.
        post_phase('after-30-d5-model-failure', model_id, ROOT / 'model-30-d5', False)
        raise RuntimeError('model failed: ' + repr(model))
    post_phase('after-30-d5-model', model_id, ROOT / 'model-30-d5', True)
    final_audit = d.audit(ROOT, 'final-30-d5', 30, 5, since=SINCE, expected_fec=12, release_root=d.RELEASE)
    final_gate = d.gate(ROOT / 'final-30-d5-ready')
    closeout = dict(status='complete', smoke=smoke, model=model, final_audit_ok=final_audit.get('ok'), final_ready=final_gate.get('passed'), model_executions=1, smoke_executions=1, wall=time.time())
except BaseException as exc:
    closeout = dict(status='STOP', error=repr(exc), smoke=smoke, model=model, model_executions=1 if model else 0, smoke_executions=1 if smoke else 0, wall=time.time())
    (ROOT / 'STOP').write_text(json.dumps(dict(reason=repr(exc), wall=time.time()), ensure_ascii=False, indent=2) + '\n')
finally:
    (ROOT / 'execution-closeout.json').write_text(json.dumps(closeout, ensure_ascii=False, indent=2) + '\n')
    d.MATRIX['status'] = closeout['status']
    (ROOT / 'execution-worker-exit.json').write_text(json.dumps(dict(status=closeout['status'], smoke_executions=closeout.get('smoke_executions', 0), model_executions=closeout.get('model_executions', 0), exited_wall=time.time()), ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(closeout, ensure_ascii=False), flush=True)
raise SystemExit(0 if closeout['status'] == 'complete' else 1)
