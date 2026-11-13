import concurrent.futures
import hashlib
import importlib.util
import json
import pathlib
import signal
import subprocess
import sys
import time
import traceback
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
PHASE_BUDGET = 3000
NODES = [1, 2, 3, 5, 6, 7, 8, 9, 10]
HOSTS = ['fl00'] + [f'fl{n:02d}' for n in NODES]
COMMIT = 'cccac233291c5a79ca7a66bb2ebb210497ab4071'
NATIVE = '44d6bbf500f51df4d0b3ae8cd7a53c4a45cb55b09ca66cb5217e7ac75674feb2'
MODEL_BYTES = 44770302
MODEL_SHA = '4b55cee2f0bdcfbbf610167a505efee0e2c50e3306fed02bda3613807c234e96'
PAYLOAD = pathlib.Path('/home/fl/projects/wfb-ng-fl/.scratch/fl-testbed-magazine-draft/models/cifar10-resnet18-seed7.pt')
ROOT.mkdir(parents=True, exist_ok=True)
CURRENT = None


def load(name):
    spec = importlib.util.spec_from_file_location('continuation_' + name, ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256(path):
    h = hashlib.sha256()
    with pathlib.Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def write(path, value):
    path = pathlib.Path(path)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def checkpoint(d, stage):
    if CURRENT is not None:
        CURRENT['stage'] = stage
    d.MATRIX['updated_wall'] = time.time()
    d.MATRIX['updated_monotonic'] = time.monotonic()
    d.MATRIX['pause_requested'] = any((ROOT / x).exists() for x in ('PAUSE', 'STOP'))
    write(ROOT / 'matrix.json', d.MATRIX)
    write(ROOT / 'checkpoint.json', dict(wall=time.time(), monotonic=time.monotonic(),
        pid=__import__('os').getpid(), status=d.MATRIX['status'], current=CURRENT, stage=stage))
    print(json.dumps(dict(stage=stage, status=d.MATRIX['status'], current=CURRENT), ensure_ascii=False), flush=True)


def verify_inputs(d):
    review = json.loads((ROOT / 'review-manifest.json').read_text())
    for path, expected in review['files'].items():
        if sha256(pathlib.Path(path)) != expected:
            raise RuntimeError('reviewed input changed: ' + path)
    identity = json.loads((ROOT / 'release-identity.json').read_text())
    if identity.get('commit') != COMMIT or identity.get('native_sha256') != NATIVE:
        raise RuntimeError('release identity mismatch')
    if PAYLOAD.stat().st_size != MODEL_BYTES or sha256(PAYLOAD) != MODEL_SHA:
        raise RuntimeError('model payload mismatch')
    runner_spec = importlib.util.spec_from_file_location('input_runner', ROOT / 'runner.py')
    runner = importlib.util.module_from_spec(runner_spec)
    runner_spec.loader.exec_module(runner)
    bundle = json.loads((ROOT / 'bundle-manifest.json').read_text())['bundle_sha256']
    if runner.bundle_sha() != bundle or runner.NODES != NODES:
        raise RuntimeError('bundle/topology mismatch')
    if runner.TIMING['io_timeout'] != 600 or runner.TIMING['orchestration_timeout'] != 1290:
        raise RuntimeError('communication timing mismatch')
    write(ROOT / 'input-verification.json', dict(ok=True, commit=COMMIT, native_sha256=NATIVE,
        model_bytes=MODEL_BYTES, model_sha256=MODEL_SHA, bundle_sha256=bundle,
        timing=runner.TIMING, topology=HOSTS, wall=time.time()))


def admit(d, helpers, label, grant, guard, since, settled=False):
    audit = d.audit(ROOT, label, grant, guard, since=since, expected_fec=12, release_root=d.RELEASE)
    gate_root = ROOT / (label + '-ready')
    if gate_root.exists():
        index = 2
        while (ROOT / (label + '-ready-attempt' + str(index))).exists():
            index += 1
        gate_root = ROOT / (label + '-ready-attempt' + str(index))
    gate_root.mkdir(exist_ok=True)
    gate = d.gate(gate_root)
    if audit['ok'] and gate['passed']:
        return audit, gate
    errors = [error for row in audit.get('checks', []) for error in row.get('errors', [])]
    if gate['passed'] and errors and all(error == 'native count mismatch' for error in errors):
        helpers.ready_before_audit(ROOT, label + '-native-settled', grant, guard, since, seconds=30)
        audit = d.audit(ROOT, label + '-native-settled-once', grant, guard, since=since,
                        expected_fec=12, release_root=d.RELEASE)
        settled_gate = ROOT / (label + '-native-settled-ready')
        if settled_gate.exists():
            index = 2
            while (ROOT / (label + '-native-settled-ready-attempt' + str(index))).exists():
                index += 1
            settled_gate = ROOT / (label + '-native-settled-ready-attempt' + str(index))
        settled_gate.mkdir(exist_ok=True)
        gate = d.gate(settled_gate)
        if audit['ok'] and gate['passed']:
            return audit, gate
    if not settled:
        raise RuntimeError(label + ' admission failed: ' + repr(gate.get('fail_nodes', [])))
    helpers.ready_before_audit(ROOT, label + '-settled', grant, guard, since, seconds=30)
    audit = d.audit(ROOT, label + '-settled-once', grant, guard, since=since,
                    expected_fec=12, release_root=d.RELEASE)
    gate_root = ROOT / (label + '-settled-once-ready')
    if gate_root.exists():
        index = 2
        while (ROOT / (label + '-settled-once-ready-attempt' + str(index))).exists():
            index += 1
        gate_root = ROOT / (label + '-settled-once-ready-attempt' + str(index))
    gate_root.mkdir(exist_ok=True)
    gate = d.gate(gate_root)
    if not audit['ok'] or not gate['passed']:
        raise RuntimeError(label + ' settled admission failed')
    return audit, gate


def execute(d, root, grant, guard, mode, phase, deadline):
    global CURRENT
    runner = importlib.util.spec_from_file_location('phase_runner', ROOT / 'runner.py')
    module = importlib.util.module_from_spec(runner)
    runner.loader.exec_module(module)
    bundle = module.bundle_sha()
    run_id = f'fec12-{phase}-20261110'
    payload = ['--smoke'] if mode == 'smoke' else ['--payload', str(PAYLOAD), '--model-manifest', str(ROOT / 'model-manifest.json')]
    command = ['python3', str(ROOT / 'runner.py'), *payload, '--native-sha', NATIVE,
        '--release-commit', COMMIT, '--release-root', d.RELEASE,
        '--grant-duration-ms', str(grant), '--guard-interval-ms', str(guard),
        '--channel', '157', '--bandwidth-mhz', '40', '--downlink-mcs', '3',
        '--uplink-mcs', '4', '--fec-n', '12', '--uftp-rate-kbps', '12000',
        '--run-id', run_id, '--output', str(root), '--execute', '--reviewed-sha', bundle]
    write(root / 'command.json', command)
    CURRENT = dict(phase=phase, mode=mode, grant=grant, guard=guard, execute_count=1,
                   run_id=run_id, started_wall=time.time())
    checkpoint(d, phase + '-execute')
    started = time.monotonic()
    log = (root / 'runner.log').open('x', buffering=1)
    monitor = (root / 'live-status-events.jsonl').open('x', buffering=1)
    proc = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
    write(root / 'runner-process.json', dict(pid=proc.pid, started_wall=time.time(), run_id=run_id))
    progress_path = root / run_id / 'progress.jsonl'
    previous = {}
    stop_sent = False
    try:
        while proc.poll() is None:
            if time.monotonic() >= deadline - 20:
                write(ROOT / 'STOP', dict(reason='total 3000-second boundary', phase=phase, wall=time.time()))
            if (ROOT / 'STOP').exists() and not stop_sent:
                proc.send_signal(signal.SIGINT)
                stop_sent = True
                CURRENT['controlled_stop'] = True
            if progress_path.exists():
                try:
                    rows = [json.loads(x) for x in progress_path.read_text().splitlines()]
                    if rows:
                        last = rows[-1]
                        CURRENT['last_phase'] = last.get('phase')
                        starts = [x for x in rows if x.get('event') == 'dispatch_start']
                        if starts:
                            CURRENT['current_phase'] = starts[-1].get('phase')
                        uploads = [x for x in rows if x.get('phase') == 'upload_progress']
                        if uploads:
                            CURRENT['upload_progress'] = uploads[-1]
                except (ValueError, IndexError):
                    pass
            try:
                with urllib.request.urlopen('http://127.0.0.1:9090/api/v1/status', timeout=1) as response:
                    status = json.load(response)
                states = {str(n): dict(readiness=status.get('nodes', {}).get(str(n), {}).get('readiness'),
                    reported_state=status.get('nodes', {}).get(str(n), {}).get('reported_state'),
                    channel=status.get('nodes', {}).get(str(n), {}).get('current_channel')) for n in NODES}
                hunts = [dict(node=n, **v) for n, v in states.items() if 'HUNT' in str(v.get('reported_state')) and previous.get(n) != v]
                monitor.write(json.dumps(dict(wall=time.time(), states=states, new_hunt_events=hunts)) + '\n')
                CURRENT['latest_states'] = states
                if hunts:
                    CURRENT.setdefault('hunt_events', []).extend(hunts)
                previous = states
            except Exception as exc:
                monitor.write(json.dumps(dict(wall=time.time(), status_error=repr(exc))) + '\n')
            checkpoint(d, phase + '-execute')
            if time.monotonic() - started > 1290 and not stop_sent:
                write(ROOT / 'STOP', dict(reason='phase 1290-second boundary', phase=phase, wall=time.time()))
                proc.send_signal(signal.SIGINT)
                stop_sent = True
            time.sleep(5)
        proc.wait(timeout=20)
    finally:
        log.close()
        monitor.close()
    rows = [json.loads(x) for x in progress_path.read_text().splitlines()] if progress_path.exists() else []
    final = next((x for x in reversed(rows) if x.get('phase') == 'result'), {})
    CURRENT.update(runner_returncode=proc.returncode, progress_path=str(progress_path),
        transfer_verified=final.get('transfer_verified', False), cleanup_ok=final.get('cleanup_ok', False),
        restore_ok=final.get('restore_ok', False), elapsed=time.monotonic() - started,
        final_flags={k: final.get(k) for k in ('transfer_verified', 'cleanup_ok', 'restore_ok')})
    write(root / 'run-summary.json', dict(candidate=CURRENT, result=final))
    return run_id, CURRENT, rows


def collect_and_verify(d, helpers, run_id, phase_root, candidate, since, model):
    rows = helpers.collect(run_id, since, '-after')
    if model:
        candidate['server_verified_files'] = next((x['result']['files'] for x in json.loads(
            (phase_root / run_id / 'progress.jsonl').read_text()).__iter__()
            if x.get('phase') == 'verify' and x.get('host') == 'fl00' and x.get('ok') is True), {})
        candidate['independent_server_files'] = helpers.independent_nine(phase_root, candidate, rows)
    cleanup_ok = helpers.exact_cleanup(run_id)
    if not cleanup_ok:
        raise RuntimeError('exact cleanup failed: ' + run_id)
    return rows


def main():
    d = load('driver-base')
    helpers = load('helpers')
    storage = load('storage')
    helpers.d = d
    helpers.ROOT = ROOT
    helpers.write = write
    storage.ROOT = ROOT
    d.MATRIX['parent_GO_verified'] = True
    d.MATRIX['status'] = 'running'
    d.MATRIX['started_wall'] = time.time()
    deadline = time.monotonic() + PHASE_BUDGET
    d.MATRIX['deadline_monotonic'] = deadline
    since = int(time.time())
    owner = dict(owner='fl00', pid=__import__('os').getpid(), host='fl00', root=str(ROOT),
        started_wall=time.time(), deadline_monotonic=deadline, phase_budget_seconds=PHASE_BUDGET,
        stop_file=str(ROOT / 'STOP'), run_ids=[], remote_owner='current general-purpose worker')
    write(ROOT / 'owner.json', owner)
    write(ROOT / 'worker-process.json', owner)
    results = []
    schedule = (30, 5)
    model_count = 0
    try:
        verify_inputs(d)
        admit(d, helpers, 'initial-30-d5', 30, 5, since)
        storage_root = ROOT / 'storage-admission'
        storage_root.mkdir(exist_ok=True)
        storage.gate(d, storage_root)
        if (ROOT / 'STOP').exists():
            (ROOT / 'STOP').unlink()
            write(ROOT / 'stop-cleared.json', dict(reason='full read-only admission passed', wall=time.time()))
        config40_root = ROOT / 'configure-40-d5'
        config40_root.mkdir(exist_ok=True)
        d.configure(40, 5, config40_root)
        admit(d, helpers, 'before-40-d5', 40, 5, since)
        phase_root = ROOT / 'smoke-40-d5'; phase_root.mkdir(exist_ok=True)
        run_id, current, _ = execute(d, phase_root, 40, 5, 'smoke', 'g40-d5-smoke', deadline)
        if not all(current.get(k) is True for k in ('transfer_verified', 'cleanup_ok', 'restore_ok')):
            raise RuntimeError('40/5 smoke gate failed: ' + repr(current))
        helpers.collect(run_id, since, '-after')
        if not helpers.exact_cleanup(run_id):
            raise RuntimeError('40/5 smoke exact cleanup failed')
        admit(d, helpers, 'after-40-d5-smoke', 40, 5, since)
        results.append(dict(phase='40/5-smoke', run_id=run_id, result=current))
        phase_root = ROOT / 'model-40-d5'; phase_root.mkdir(exist_ok=True)
        model_count += 1
        candidate = dict(phase='40/5-model', grant_duration_ms=40, guard_interval_ms=5,
            execute_count=1, rawpath=str(phase_root))
        d.CURRENT = candidate
        run_id, current, rows = execute(d, phase_root, 40, 5, 'model', 'g40-d5-model', deadline)
        if not all(current.get(k) is True for k in ('transfer_verified', 'cleanup_ok', 'restore_ok')):
            raise RuntimeError('40/5 model failed: ' + repr(current))
        candidate['server_verified_files'] = next((x['result']['files'] for x in rows if x.get('phase') == 'verify' and x.get('host') == 'fl00' and x.get('ok') is True), {})
        rows_collected = helpers.collect(run_id, since, '-after')
        candidate['independent_server_files'] = helpers.independent_nine(phase_root, candidate, rows_collected)
        if not helpers.exact_cleanup(run_id):
            raise RuntimeError('40/5 model exact cleanup failed')
        admit(d, helpers, 'after-40-d5-model', 40, 5, since)
        results.append(dict(phase='40/5-model', run_id=run_id, result=current, candidate=candidate))
        config30_root = ROOT / 'configure-30-d5'
        config30_root.mkdir(exist_ok=True)
        d.configure(30, 5, config30_root)
        admit(d, helpers, 'before-30-d5', 30, 5, since)
        phase_root = ROOT / 'model-30-d5'; phase_root.mkdir(exist_ok=True)
        model_count += 1
        candidate = dict(phase='30/5-model', grant_duration_ms=30, guard_interval_ms=5,
            execute_count=1, rawpath=str(phase_root))
        d.CURRENT = candidate
        run_id, current, rows = execute(d, phase_root, 30, 5, 'model', 'g30-d5-model', deadline)
        if not all(current.get(k) is True for k in ('transfer_verified', 'cleanup_ok', 'restore_ok')):
            raise RuntimeError('30/5 model failed: ' + repr(current))
        candidate['server_verified_files'] = next((x['result']['files'] for x in rows if x.get('phase') == 'verify' and x.get('host') == 'fl00' and x.get('ok') is True), {})
        rows_collected = helpers.collect(run_id, since, '-after')
        candidate['independent_server_files'] = helpers.independent_nine(phase_root, candidate, rows_collected)
        if not helpers.exact_cleanup(run_id):
            raise RuntimeError('30/5 model exact cleanup failed')
        admit(d, helpers, 'after-30-d5-model', 30, 5, since)
        results.append(dict(phase='30/5-model', run_id=run_id, result=current, candidate=candidate))
        final_audit, final_gate = admit(d, helpers, 'final-30-d5', 30, 5, since)
        d.MATRIX['status'] = 'complete'
        write(ROOT / 'final-closeout.json', dict(ok=True, results=results, model_executions=model_count,
            final_actual_baseline=final_audit, final_ready=final_gate, final_schedule='30/5', fec='8/12', wall=time.time()))
    except BaseException as exc:
        d.MATRIX['status'] = 'stopped'
        write(ROOT / 'error.json', dict(error=repr(exc), traceback=traceback.format_exc(),
            model_executions=model_count, wall=time.time()))
        write(ROOT / 'STOP', dict(reason=repr(exc), wall=time.time(), model_executions=model_count))
        try:
            recovery_root = ROOT / 'recovery-30-d5'
            recovery_root.mkdir(exist_ok=True)
            d.configure(30, 5, recovery_root)
            admit(d, helpers, 'recovery-30-d5', 30, 5, since)
        except BaseException as recovery_exc:
            write(ROOT / 'recovery-error.json', dict(error=repr(recovery_exc), traceback=traceback.format_exc(), wall=time.time()))
    finally:
        d.CURRENT = None
        checkpoint(d, 'finished')
        write(ROOT / 'worker-exit.json', dict(pid=__import__('os').getpid(), status=d.MATRIX['status'],
            model_executions=model_count, exited_wall=time.time(), stop_exists=(ROOT / 'STOP').exists()))


if __name__ == '__main__':
    main()
