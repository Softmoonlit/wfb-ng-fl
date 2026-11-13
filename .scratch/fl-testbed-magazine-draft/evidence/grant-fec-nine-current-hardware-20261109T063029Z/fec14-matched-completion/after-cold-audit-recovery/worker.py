"""FEC14 冷切审计恢复后的四档续跑；复用已审源与原始总截止时间。"""
import concurrent.futures, hashlib, importlib.util, json, os, pathlib, subprocess, time, traceback
ROOT=pathlib.Path(__file__).resolve().parent
ORIGINAL=ROOT.parent
PROFILE=pathlib.Path('/home/fl/projects/wfb-ng-fl/.scratch/fl-testbed-magazine-draft/evidence/grant-fec-nine-current-hardware-20261109T063029Z/fec14-matched-completion/heartbeat-1cd6264d5d6b-20261109T080119Z')
PHASES=['preflight','stage','prepare','stop','install','start','audit']

def load(name):
    spec=importlib.util.spec_from_file_location('fec14_'+name,ORIGINAL/(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def main():
    # 先验证父授权，再导入运行编排模块或发出任何命令。
    go=json.loads((ROOT/'GO.json').read_text())
    assert go['parent_authorization']=='GO FEC14' and go['fec12_actor_finished'] is True
    assert go['scope']=='after-cold-audit-recovery-four-fec14-candidates' and go['bounded_continuation_authorized'] is True
    prior=json.loads((ORIGINAL.parent/'fec12/matrix.json').read_text())
    close=json.loads((ORIGINAL.parent/'fec12/final-closeout/result.json').read_text())
    assert prior['status']=='complete' and close['full_ten_actual_baseline_ok'] and close['exact_nine_ready_idle']
    assert close['fec_n']==12 and close['server_schedule']=='10/10'
    assert not (ROOT/'worker-process.json').exists(), 'no restart or duplicate execution'
    manifest=json.loads((ROOT/'review-manifest.json').read_text())
    for path,digest in manifest['files'].items():
        assert hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()==digest, 'reviewed input changed: '+path
    original=json.loads((ORIGINAL/'worker-process.json').read_text())
    began=original['deadline_monotonic']-110*60;deadline=original['deadline_monotonic']
    assert time.monotonic()<deadline and not pathlib.Path('/proc',str(original['pid']),'cmdline').exists()
    d=load('driver-base');h=load('helpers');storage=load('storage')
    d.ROOT=ROOT
    for candidate in d.MATRIX['candidates']:
        candidate['rawpath']=str(ROOT/pathlib.Path(candidate['rawpath']).name)
    assert storage.ROOT.resolve()==ORIGINAL.resolve()
    h.d=d;h.ROOT=ROOT;h.write=d.write
    d.MATRIX.update(parent_GO_verified=True,run_tag=time.strftime('%Y%m%dT%H%M%SZ',time.gmtime()),
                    deadline_monotonic=deadline,started_wall=time.time(),original_started_wall=original['started_wall'],original_deadline_monotonic=deadline,status='running')
    d.write(ROOT/'worker-process.json',dict(pid=os.getpid(),started_wall=time.time(),deadline_monotonic=deadline,original_started_wall=original['started_wall'],phase_budget_seconds=6600))
    runs=[];cold_complete=False;error=None;hard_stop=False
    def stop(reason):
        nonlocal hard_stop
        if any(text in reason.lower() for text in ['management','usb reset','cleanup failure','residual','incomplete','criterion failure']):hard_stop=True
        d.write(ROOT/'STOP',dict(reason=reason,wall=time.time()));raise RuntimeError(reason)
    def boundary(required=0,allow_stop=False):
        if time.monotonic()+required>=deadline:stop('110 minute total stage budget insufficient')
        if not allow_stop and any((ROOT/x).exists() for x in ['PAUSE','STOP']):stop('parent pause/STOP boundary')
    def fatal(a):
        return any(any(e in ['management/probe failed','USB reset observed'] for e in row['errors']) for row in a['checks'])
    def standard_recovery(root,a,g):
        failed={x['host'] for x in a['checks'] if not x['ok']}|{'fl%02d'%n for n in g['fail_nodes']}
        unexpected=[e for r in a['checks'] for e in r['errors'] if e not in ['iw baseline mismatch']]
        if unexpected:stop('identity/config criterion failure: '+repr(unexpected))
        if not failed:stop('actual recovery incomplete: no anomalous host identified')
        def one(host):
            unit='wfb-fl-'+('server' if host=='fl00' else 'client')+'-daemon.service'
            return d.remote(host,"import subprocess,json\np=subprocess.run(['systemctl','restart',"+repr(unit)+"],capture_output=True,text=True,timeout=15)\nprint(json.dumps(dict(returncode=p.returncode,stderr=p.stderr)))",timeout=20)
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:rows=list(pool.map(one,sorted(failed)))
        d.write(root/'single-standard-recovery.json',rows)
        if any(x.get('returncode')!=0 or x.get('result',{}).get('returncode')!=0 for x in rows):stop('standard recovery management/service failure')
    def admit(root,label,grant,guard,run_id=None,since=None,recover=False,initial=None):
        a,g=initial if initial else h.baseline(root,label,grant,guard,run_id,since)
        if fatal(a):stop('management or USB reset boundary')
        unexpected=[e for row in a['checks'] for e in row['errors'] if e!='iw baseline mismatch']
        if unexpected:stop('identity/config criterion failure: '+repr(unexpected))
        if a['ok'] and g['passed']:return a,g
        d.write(root/(label+'-original-incomplete.json'),dict(audit=a['checks'],gate=g,wall=time.time()))
        time.sleep(3)
        a,g=h.baseline(root,label+'-settled-once',grant,guard,run_id,since)
        if fatal(a):stop('management or USB reset during settled check')
        unexpected=[e for row in a['checks'] for e in row['errors'] if e!='iw baseline mismatch']
        if unexpected:stop('identity/config criterion failure: '+repr(unexpected))
        if not(a['ok'] and g['passed']) and recover:
            standard_recovery(root,a,g);d.wait_server_ready(root,grant,guard,'single-recovery-api-ready')
            a,g=h.baseline(root,label+'-after-single-recovery',grant,guard,None,since)
        if not(a['ok'] and g['passed']):stop('actual full ten FEC14 baseline/nine READY incomplete')
        return a,g
    def completed_profile_gate():
        state=json.loads((PROFILE/'state.json').read_text())
        assert state['completed']==PHASES and state['targets']==d.HOSTS
        assert state['expected']==dict(grant_duration_ms=10,guard_interval_ms=10,fec_n=14)
        assert state['native_sha']==state['native_before_sha']==d.SHA and state['commit']==d.COMMIT
        rows=[json.loads((PROFILE/('audit-'+host+'.json')).read_text()) for host in d.HOSTS]
        assert all(row['exit']==0 for row in rows)
        admission=json.loads((ORIGINAL/'bounded-cold-audit-continuation/initial-result.json').read_text())
        assert admission['full_ten_actual_baseline_ok'] and admission['exact_nine_ready_idle']
        d.wait_server_ready(ROOT,10,10,'continued-api-ready')
        admit(ROOT,'continued-fec14-baseline',10,10,recover=True)
        d.write(ROOT/'completed-profile-admission.json',dict(completed=state['completed'],full_ten_profile_audit_ok=True,original_stage_start_wall=original['started_wall'],deadline_monotonic=deadline,storage_evidence_root=str(storage.ROOT),wall=time.time()))
    try:
        boundary(300)
        h.local_runner_gate('initial')
        assert d.PAYLOAD.stat().st_size==44770302 and hashlib.sha256(d.PAYLOAD.read_bytes()).hexdigest()==d.PAYLOAD_SHA
        spec=importlib.util.spec_from_file_location('frozen',d.TOOL/'runner.py');runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
        assert runner.bundle_sha()==d.BUNDLE_SHA and runner.NODES==d.NODES
        assert runner.TIMING['io_timeout']==600 and runner.TIMING['orchestration_timeout']==1290
        d.write(ROOT/'input-verification.json',dict(bundle_sha=runner.bundle_sha(),model_size=44770302,model_sha=d.PAYLOAD_SHA))
        completed_profile_gate();cold_complete=True
        for grant,guard in d.SCHEDULE:
            smoke_ok=False
            for mode in ['smoke','model']:
                if mode=='model' and not smoke_ok:
                    candidate=next(c for c in d.MATRIX['candidates'] if (c['grant_duration_ms'],c['guard_interval_ms'],c['mode'])==(grant,guard,mode))
                    candidate.update(stage='skipped',reason='same candidate 1MiB transfer did not pass');continue
                boundary(1420)
                c=next(c for c in d.MATRIX['candidates'] if (c['grant_duration_ms'],c['guard_interval_ms'],c['mode'])==(grant,guard,mode));d.CURRENT=c
                root=pathlib.Path(c['rawpath']);root.mkdir();since=int(time.time())
                d.configure(grant,guard,root);d.wait_server_ready(root,grant,guard)
                admit(root,'before',grant,guard,recover=True)
                h.local_runner_gate(f'before-g{grant}-d{guard}-{mode}')
                if mode=='model':storage.gate(d,root)
                runid=d.run_transfer(root,grant,guard,mode);runs.append((runid,since))
                a,g=h.baseline(root,'after',grant,guard,runid,since)
                c.update(immediate_independent_restore_ok=a['ok'],immediate_independent_ready_ok=g['passed'])
                # 保留原始 false 结果；最终 true 使用独立字段与文件记录。
                if fatal(a):stop('management or USB reset observed after execute')
                h.collect(runid,since,'-after')
                clean=h.exact_cleanup(runid);c['independent_exact_cleanup_ok']=clean
                if not clean:stop('exact cleanup failure/residual state')
                a,g=admit(root,'after',grant,guard,runid,since,recover=True,initial=(a,g))
                c.update(independent_restore_ok=a['ok'],independent_ready_ok=g['passed'],finished_wall=time.time(),
                         outcome='passed' if c['transfer_verified'] else 'transfer_failed_recovered')
                if mode=='smoke':smoke_ok=c['transfer_verified'] and clean and a['ok'] and g['passed']
                d.checkpoint('candidate-finished')
                if h.infrastructure_failure(runid,root):stop('infrastructure failure: UFTP 8 or insufficient disk; stop matrix')
                # 传输失败但独立清理与恢复均通过后，允许进入下一不同候选。
        d.MATRIX['status']='complete'
    except BaseException as exc:
        error=repr(exc)
        if any(text in error.lower() for text in ['management','hash failed','incomplete','service failure']):hard_stop=True
        d.MATRIX.update(status='stopped',error=error)
        d.write(ROOT/'STOP',dict(reason=error,traceback=traceback.format_exc(),wall=time.time()))
    finally:
        if cold_complete:
            close=ROOT/'final-closeout';close.mkdir(exist_ok=True);d.CURRENT=None
            try:
                h.local_runner_gate('before-final')
                if hard_stop:
                    d.write(close/'hard-stop-no-mutation.json',dict(reason=error,wall=time.time()))
                    raise RuntimeError('hard stop: parent must review management/USB/cleanup before further live operations')
                d.configure(10,10,close,'server10-10-fec14-recovery');d.wait_server_ready(close,10,10,'final-api-ready')
                a,g=admit(close,'final-fec14-baseline',10,10,recover=True)
                d.write(close/'result.json',dict(full_ten_actual_baseline_ok=a['ok'],exact_nine_ready_idle=g['passed'],fec_k=8,fec_n=14,server_schedule='10/10',runs=runs,execute_count=sum(c['execute_count'] for c in d.MATRIX['candidates']),measurement_error=error,wall=time.time()))
            except BaseException as exc:
                d.MATRIX['status']='stopped'
                d.write(close/'error.json',dict(error=repr(exc),traceback=traceback.format_exc(),wall=time.time()))
        d.checkpoint('finished')

if __name__=='__main__':main()
