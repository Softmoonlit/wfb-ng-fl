import concurrent.futures
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import signal
import subprocess
import sys
import time
import traceback

ROOT = pathlib.Path(__file__).resolve().parent
HOSTS = ['fl00'] + [f'fl{n:02d}' for n in [1, 2, 3, 5, 6, 7, 8, 9, 10]]
NODES = [1, 2, 3, 5, 6, 7, 8, 9, 10]
PHASE_BUDGET = 4500
GRANT = 30
GUARD = 5
FEC_N = 12
BASELINE_CHANNEL = 149
TEST_CHANNEL = 157
MODEL_BYTES = 44770302
MODEL_SHA = '4b55cee2f0bdcfbbf610167a505efee0e2c50e3306fed02bda3613807c234e96'
IDENTITY = json.loads((ROOT / 'release-identity.json').read_text())
BUNDLE_SHA = json.loads((ROOT / 'bundle-manifest.json').read_text())['bundle_sha256']
COMMIT = IDENTITY.get('commit')
NATIVE_SHA = IDENTITY.get('native_sha256')
RELEASE = IDENTITY.get('release_root')
PAYLOAD = pathlib.Path('/home/fl/projects/wfb-ng-fl/.scratch/fl-testbed-magazine-draft/models/cifar10-resnet18-seed7.pt')
MANIFEST = ROOT / 'model-manifest.json'


def load(name):
    spec = importlib.util.spec_from_file_location('after_restart_' + name, ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256(path):
    digest = hashlib.sha256()
    with pathlib.Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def write(path, value):
    path = pathlib.Path(path)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def stop_marker(reason, **details):
    value = dict(reason=reason, wall=time.time(), pid=os.getpid(), **details)
    write(ROOT / 'STOP', value)
    return value


def verify_inputs(d, require_release=False):
    manifest_path = ROOT / 'review-manifest.json'
    review = json.loads(manifest_path.read_text())
    for path, expected in review['files'].items():
        actual = sha256(pathlib.Path(path))
        if actual != expected:
            raise RuntimeError('审阅输入 SHA 发生变化: ' + path)
    if review['constraints']['bundle_sha'] != BUNDLE_SHA:
        raise RuntimeError('冻结 bundle SHA 约束不一致')
    if review['constraints']['commit'] != COMMIT or review['constraints']['native_sha'] != NATIVE_SHA:
        raise RuntimeError('冻结 commit/native SHA 约束不一致')
    pending = (IDENTITY.get('status') != 'parent-release-published'
               or not isinstance(COMMIT, str) or not isinstance(RELEASE, str)
               or not isinstance(NATIVE_SHA, str))
    if require_release and pending:
        raise RuntimeError('父发布身份尚未提供；禁止进入远端审计或模型执行')
    if not pending:
        if not re.fullmatch(r'[0-9a-f]{40}', COMMIT):
            raise RuntimeError('release commit must be a full lowercase SHA')
        if not re.fullmatch(r'[0-9a-f]{64}', NATIVE_SHA):
            raise RuntimeError('native SHA must be lowercase SHA-256')
        if not RELEASE.startswith('/srv/wfb-ng-fl/releases/heartbeat-' + COMMIT[:12] + '-'):
            raise RuntimeError('release root does not match commit')
    if PAYLOAD.stat().st_size != MODEL_BYTES or sha256(PAYLOAD) != MODEL_SHA:
        raise RuntimeError('模型制品 size/SHA 不匹配')
    if d.PAYLOAD.stat().st_size != MODEL_BYTES or d.PAYLOAD_SHA != MODEL_SHA:
        raise RuntimeError('driver 模型输入不匹配')
    runner_spec = importlib.util.spec_from_file_location('after_restart_runner_check', d.TOOL / 'runner.py')
    runner = importlib.util.module_from_spec(runner_spec)
    runner_spec.loader.exec_module(runner)
    if runner.bundle_sha() != BUNDLE_SHA or runner.NODES != NODES:
        raise RuntimeError('通信 bundle 或固定拓扑不匹配')
    if runner.TIMING['io_timeout'] != 600 or runner.TIMING['orchestration_timeout'] != 1290:
        raise RuntimeError('通信期限不匹配')
    write(ROOT / 'input-verification.json', dict(ok=True, execution_ready=not pending,
        release_identity_pending=pending, model_bytes=MODEL_BYTES,
        model_sha256=MODEL_SHA, bundle_sha256=BUNDLE_SHA, commit=COMMIT,
        native_sha256=NATIVE_SHA, release_root=RELEASE, topology=HOSTS, excluded=['fl04'],
        timing=runner.TIMING, wall=time.time()))


def service_recovery(d, audit, since):
    """仅在所有审计错误都严格等于 iw 信道偏移时，按常驻服务路径恢复。"""
    errors = [error for row in audit['checks'] for error in row['errors']]
    if not errors or any(error != 'iw baseline mismatch' for error in errors):
        raise RuntimeError('标准恢复前置条件不满足：除 iw 信道外存在实质错误')
    rows = audit['rows']
    if any(row.get('returncode') != 0 for row in rows):
        raise RuntimeError('恢复前管理面不完整，拒绝部分服务重启')
    mismatched = []
    for row in rows:
        native = row['result'].get('native', [])
        if len(native) != 1:
            raise RuntimeError('恢复前 native 数量异常: ' + row['host'])
        iw = native[0]['iw']
        if f'channel {BASELINE_CHANNEL} ' not in iw:
            mismatched.append(row['host'])
    if not mismatched:
        write(ROOT / 'standard-service-recovery.json', dict(attempted=False,
            reason='十端实际已在149，无需重启配置', hosts=HOSTS, wall=time.time()))
        return
    def one(host):
        role = 'server' if host == 'fl00' else 'client'
        unit = 'wfb-fl-' + role + '-daemon.service'
        code = "import subprocess,json\np=subprocess.run(['systemctl','restart',UNIT],capture_output=True,text=True,timeout=20)\nprint(json.dumps(dict(unit=UNIT,returncode=p.returncode,stderr=p.stderr)))"
        return d.remote(host, 'UNIT=' + repr(unit) + '\n' + code, timeout=25)
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(mismatched)) as pool:
        results = list(pool.map(one, mismatched))
    write(ROOT / 'standard-service-recovery.json', dict(attempted=True,
        hosts=mismatched, results=results, method='systemctl restart resident service', wall=time.time()))
    if any(row.get('returncode') != 0 or row.get('result', {}).get('returncode') != 0 for row in results):
        raise RuntimeError('标准服务恢复失败')
    d_helpers = load('helpers')
    d_helpers.d = d
    d_helpers.ROOT = ROOT
    d_helpers.write = write
    d_helpers.ready_before_audit(ROOT, 'after-standard-service-recovery', GRANT, GUARD, since, seconds=60)


def old_trial2_cleanup_gate(d, helpers):
    """核对旧 trial2 的具名 unit 和专有路由；只允许精确 recover，不碰模型文件。"""
    old_run_id = 'fec12-20261109T092236Z-trial2-g30-d5-model'
    code = '''import json,pathlib,socket,subprocess
host=socket.gethostname();unit='comm-smoke-OLD_RUN_ID-'+host+'.service'
active=subprocess.run(['systemctl','is-active',unit],capture_output=True,text=True,timeout=5).stdout.strip()
routes={group:json.loads(subprocess.check_output(['ip','-j','route','show','exact',group],text=True,timeout=5)) for group in ['239.80.41.1/32','239.80.42.1/32']}
root=pathlib.Path('/srv/wfb-ng-fl')/('comm-OLD_RUN_ID')
print(json.dumps(dict(host=host,unit=unit,active=active,routes=routes,root_exists=root.exists(),cleanup_state=(root/'cleanup-state.json').exists())))'''.replace('OLD_RUN_ID', old_run_id)
    def one(host):
        return d.remote(host, code, timeout=10)
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(HOSTS)) as pool:
        before = list(pool.map(one, HOSTS))
    write(ROOT / 'old-trial2-cleanup-before.json', dict(run_id=old_run_id, rows=before, wall=time.time()))
    if any(row.get('returncode') != 0 for row in before):
        raise RuntimeError('旧 trial2 unit/路由核验管理失联')
    residual = []
    for row in before:
        result = row['result']
        if result['active'] not in ['inactive', 'failed', 'unknown'] or any(result['routes'].values()):
            residual.append(result['host'])
    cleanup_rows = []
    if residual:
        if not all(row['result']['cleanup_state'] for row in before if row['result']['host'] in residual):
            raise RuntimeError('旧 trial2 残留缺少 cleanup-state，拒绝扩大清理范围')
        old_cleanup = helpers.exact_cleanup(old_run_id)
        cleanup_rows = json.loads((ROOT / (old_run_id + '-independent-exact-cleanup.json')).read_text())
        if not old_cleanup:
            raise RuntimeError('旧 trial2 精确 unit/路由回收失败')
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(HOSTS)) as pool:
        after = list(pool.map(one, HOSTS))
    write(ROOT / 'old-trial2-cleanup-after.json', dict(run_id=old_run_id, residual_hosts=residual,
        cleanup_rows=cleanup_rows, rows=after, wall=time.time()))
    if any(row.get('returncode') != 0 for row in after):
        raise RuntimeError('旧 trial2 回收后复核管理失联')
    for row in after:
        result = row['result']
        if result['active'] not in ['inactive', 'failed', 'unknown'] or any(result['routes'].values()):
            raise RuntimeError('旧 trial2 具名 unit/路由仍有残留: ' + result['host'])


def admit(d, helpers, label, run_id=None, since=None, settled=False):
    audit = d.audit(ROOT, label, GRANT, GUARD, run_id=run_id, since=since,
                    expected_fec=FEC_N, release_root=RELEASE)
    gate_root = ROOT / (label + '-ready')
    gate_root.mkdir(exist_ok=True)
    gate = d.gate(gate_root)
    if audit['ok'] and gate['passed']:
        return audit, gate
    management_errors = [row['host'] for row in audit['checks'] if not row['ok']
                         and any('management' in error for error in row['errors'])]
    if management_errors:
        raise RuntimeError('管理失联停止: ' + repr(management_errors))
    if not settled:
        raise RuntimeError('全十实际基线或九 READY 未通过: ' + repr(gate.get('fail_nodes', [])))
    write(ROOT / (label + '-original-incomplete.json'), dict(audit=audit, gate=gate,
        bounded_settled_recheck=True, wall=time.time()))
    helpers.ready_before_audit(ROOT, label + '-settled', GRANT, GUARD, since, seconds=30)
    audit = d.audit(ROOT, label + '-settled-once', GRANT, GUARD, run_id=run_id,
                    since=since, expected_fec=FEC_N, release_root=RELEASE)
    gate_root = ROOT / (label + '-settled-once-ready')
    gate_root.mkdir(exist_ok=True)
    gate = d.gate(gate_root)
    if not audit['ok'] or not gate['passed']:
        raise RuntimeError('一次 settled 复核后全十实际基线或九 READY 仍未通过')
    return audit, gate


def boundary(d, deadline):
    if time.monotonic() >= deadline:
        raise RuntimeError('4500 秒阶段期限到达')
    if (ROOT / 'STOP').exists():
        raise RuntimeError('新批次 STOP 边界已触发')
    if (ROOT / 'PAUSE').exists():
        raise RuntimeError('新批次 PAUSE 边界已触发')
    d.checkpoint('boundary')


def main():
    audit_only = '--audit-only' in sys.argv[1:]
    d = load('driver-base')
    helpers = load('helpers')
    storage = load('storage')
    helpers.d = d
    helpers.ROOT = ROOT
    helpers.write = write
    storage.ROOT = ROOT
    # storage.py keeps the archived-success evidence root when fresh root is nested.
    storage.BATCHROOT = storage.BATCHROOT
    d.MATRIX['parent_GO_verified'] = True
    d.MATRIX['status'] = 'running'
    d.MATRIX['started_wall'] = time.time()
    deadline = time.monotonic() + PHASE_BUDGET
    d.MATRIX['deadline_monotonic'] = deadline
    since = int(time.time())
    owner = dict(owner='fl00', pid=os.getpid(), host='fl00', root=str(ROOT),
                 started_wall=time.time(), deadline_monotonic=deadline,
                 phase_budget_seconds=PHASE_BUDGET, stop_file=str(ROOT / 'STOP'),
                 run_ids=[], remote_owner='current general-purpose worker')
    write(ROOT / 'owner.json', owner)
    write(ROOT / 'worker-process.json', owner)
    try:
        verify_inputs(d, require_release=True)
        d.checkpoint('restart-readonly-audit')
        audit = d.audit(ROOT, 'restart-readonly', GRANT, GUARD, since=since,
                        expected_fec=FEC_N, release_root=RELEASE)
        status = d.status()
        write(ROOT / 'restart-management-status.json', status)
        gate_root = ROOT / 'restart-ready'
        gate_root.mkdir(exist_ok=True)
        gate = d.gate(gate_root)
        write(ROOT / 'restart-readonly-fact.json', dict(audit=audit, gate=gate,
            status=status, wall=time.time(), model_executed=False))
        if not audit['ok']:
            failures = {row['host']: row['errors'] for row in audit['checks'] if not row['ok']}
            errors = [error for row in audit['checks'] for error in row['errors']]
            if errors and all(error == 'iw baseline mismatch' for error in errors):
                service_recovery(d, audit, since)
                audit, gate = admit(d, helpers, 'post-recovery', since=since)
            else:
                raise RuntimeError('重启后只读实际审计失败，停止且不恢复: ' + repr(failures))
        elif not gate['passed']:
            service_recovery(d, audit, since)
            audit, gate = admit(d, helpers, 'post-recovery', since=since)
        old_trial2_cleanup_gate(d, helpers)
        if not gate['passed']:
            raise RuntimeError('旧 trial2 核验后全十实际或九 READY 未通过')
        if audit_only:
            write(ROOT / 'audit-only-closeout.json', dict(ok=audit['ok'] and gate['passed'],
                model_executed=False, wall=time.time()))
            return
        # 配置已在此前全十准入中确认，不再次重启服务；本地 setup 目录先行建立。
        (ROOT / 'setup').mkdir(exist_ok=True)
        d.configure(GRANT, GUARD, ROOT / 'setup')
        admit(d, helpers, 'configured', since=since)
        tag = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
        results = []
        for trial in [1, 2]:
            boundary(d, deadline)
            candidate = d.MATRIX['candidates'][trial - 1]
            d.CURRENT = candidate
            trial_root = ROOT / ('trial' + str(trial))
            trial_root.mkdir(exist_ok=True)
            helpers.local_runner_gate('before-trial' + str(trial))
            storage.gate(d, trial_root)
            audit, gate = admit(d, helpers, 'trial' + str(trial) + '-before', since=since,
                                settled=trial == 2)
            run_id = f'fec12-apr-{tag}-t{trial}-g30-d5-model'
            if len(run_id) > 64:
                raise RuntimeError('run ID 超过通信工具上限')
            d.MATRIX['run_tag'] = f'apr-{tag}-t{trial}'
            owner['run_ids'].append(run_id)
            write(ROOT / 'owner.json', owner)
            result_id = d.run_transfer(trial_root, GRANT, GUARD, 'model')
            rows = helpers.collect(result_id, since, '-after')
            facts = helpers.independent_nine(trial_root, candidate, rows)
            cleanup_ok = helpers.exact_cleanup(result_id)
            candidate['independent_exact_cleanup_ok'] = cleanup_ok
            if not cleanup_ok:
                raise RuntimeError('精确清理失败或残留')
            audit, gate = admit(d, helpers, 'trial' + str(trial) + '-after',
                                run_id=result_id, since=since, settled=trial == 2)
            candidate.update(independent_restore_ok=audit['ok'], independent_ready_ok=gate['passed'],
                             independent_server_files=facts)
            if candidate.get('runner_returncode') != 0 or any(candidate.get('original_runner_final_flags', {}).get(key) is not True
                                                            for key in ['transfer_verified', 'cleanup_ok', 'restore_ok']):
                raise RuntimeError('runner 原始 transfer_verified/cleanup_ok/restore_ok 三项未全真')
            candidate.update(outcome='passed', finished_wall=time.time())
            write(trial_root / 'completion-fact.json', dict(candidate=candidate, run_id=result_id,
                all_required_passed=True, model_sha256=MODEL_SHA, model_bytes=MODEL_BYTES))
            results.append(dict(trial=trial, run_id=result_id, DLduration=candidate.get('DLduration'),
                                ULduration=candidate.get('ULduration'), outcome='passed'))
            d.checkpoint('trial-finished')
        final_audit, final_gate = admit(d, helpers, 'final', since=since, settled=False)
        d.MATRIX['status'] = 'complete'
        write(ROOT / 'final-closeout.json', dict(ok=True, consecutive_two_passed=True,
            runs=results, final_actual_baseline=final_audit, final_ready=final_gate,
            restored_channel=BASELINE_CHANNEL, final_schedule='30/5', fec='8/12', wall=time.time()))
    except BaseException as exc:
        d.MATRIX['status'] = 'stopped'
        stop_marker(repr(exc), traceback=traceback.format_exc(), model_executed=any(
            candidate.get('execute_count', 0) for candidate in d.MATRIX['candidates']))
        write(ROOT / 'error.json', dict(error=repr(exc), traceback=traceback.format_exc(), wall=time.time()))
    finally:
        d.CURRENT = None
        d.checkpoint('finished')
        write(ROOT / 'worker-exit.json', dict(pid=os.getpid(), status=d.MATRIX['status'],
            exited_wall=time.time(), stop_exists=(ROOT / 'STOP').exists()))


if __name__ == '__main__':
    main()
