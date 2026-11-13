"""仅父 GO 后执行暂定单候选连续两次模型验收；本文件不在准备回合启动。"""
import concurrent.futures, hashlib, importlib.util, json, os, pathlib, subprocess, time, traceback
ROOT=pathlib.Path(__file__).resolve().parent
PHASES=['preflight','stage','prepare','stop','install','start','audit']

def load(name):
    spec=importlib.util.spec_from_file_location('selected_'+name,ROOT/(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def validate_prior_screening(matrix):
    # 四档均须处理完成；small 未通过时允许按原协议跳过 model，不补跑任何测试。
    assert matrix['status']=='complete'
    expected={(10,10),(20,10),(30,10),(20,5)}
    groups={key:[] for key in expected}
    for candidate in matrix['candidates']:
        key=(candidate['grant_duration_ms'],candidate['guard_interval_ms'])
        assert key in groups, 'FEC14 筛选档位不匹配'
        groups[key].append(candidate)
    for rows in groups.values():
        assert len(rows)==2 and {c['mode'] for c in rows}=={'smoke','model'}, '每档须各有一条 small/model 事实'
        small=next(c for c in rows if c['mode']=='smoke')
        model=next(c for c in rows if c['mode']=='model')
        assert small['execute_count']==1 and small['stage']=='candidate-finished', 'small 须恰执行一次且完成'
        small_passed=all(small.get(k) is True for k in ['transfer_verified','independent_exact_cleanup_ok','independent_restore_ok','independent_ready_ok'])
        executed=model['execute_count']==1 and model['stage']=='candidate-finished'
        skipped=model['execute_count']==0 and model['stage']=='skipped' and not small_passed
        assert executed or skipped, 'model 须完成一次，或因 small 未通过而明确跳过且零执行'

def main():
    # 父必须先确认最终选择、FEC14 四档与续跑完成、收尾完成及此前远端执行者已结束。
    go=json.loads((ROOT/'GO.json').read_text())
    assert go['parent_authorization']=='GO SELECTED VALIDATION'
    assert go['selected_candidate']==dict(fec_k=8,fec_n=12,grant_ms=30,guard_ms=5)
    assert all(go[k] is True for k in ['fec14_four_candidates_complete','fec14_continuation_complete','ownerfinished','prior_remote_owners_finished','no_active_units','final_full10_nine_ready_server10_10'])
    manifest=json.loads((ROOT/'review-manifest.json').read_text())
    for path,digest in manifest['files'].items():
        assert hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()==digest, '审阅输入发生变化: '+path
    assert go['review_manifest_sha256']==hashlib.sha256((ROOT/'review-manifest.json').read_bytes()).hexdigest()
    resume_marker=ROOT/'worker-process.json'
    resume=resume_marker.exists()
    historical_stop_sha=hashlib.sha256((ROOT/'STOP').read_bytes()).hexdigest() if (ROOT/'STOP').exists() else None
    if resume:
        previous=json.loads(resume_marker.read_text())
        assert not pathlib.Path('/proc',str(previous['pid'])).exists(), '上次 worker 仍在运行'
        assert historical_stop_sha is not None, '续跑必须保留上次 STOP 现场'
    else:
        assert not resume_marker.exists(), '禁止重启验收或重复执行'
    prior=ROOT.parent/'fec14-matched-completion/after-cold-audit-recovery'
    assert pathlib.Path(go['fec14_matrix_path']).resolve()==(prior/'matrix.json').resolve()
    assert pathlib.Path(go['fec14_final_result_path']).resolve()==(prior/'final-closeout/result.json').resolve()
    for key in ['fec14_matrix_path','fec14_final_result_path']:
        path=pathlib.Path(go[key]).resolve();assert path.is_relative_to(prior.resolve())
        assert hashlib.sha256(path.read_bytes()).hexdigest()==go[key+'_sha256']
    matrix=json.loads(pathlib.Path(go['fec14_matrix_path']).read_text())
    final=json.loads(pathlib.Path(go['fec14_final_result_path']).read_text())
    validate_prior_screening(matrix)
    old_state=json.loads((prior.parent/'heartbeat-1cd6264d5d6b-20261109T080119Z/state.json').read_text())
    assert old_state['completed']==PHASES and old_state['expected']==dict(grant_duration_ms=10,guard_interval_ms=10,fec_n=14)
    assert final['full_ten_actual_baseline_ok'] and final['exact_nine_ready_idle'] and final['fec_n']==14 and final['server_schedule']=='10/10'
    plan=json.loads((ROOT/'plan.json').read_text());profile=pathlib.Path(plan['profile'])
    deadline=time.monotonic()+75*60;since_start=int(time.time())
    d=load('driver-base');h=load('helpers');storage=load('storage')
    h.d=d;h.ROOT=ROOT;h.write=d.write
    assert storage.ROOT==ROOT and storage.BATCHROOT==ROOT.parent
    d.MATRIX.update(parent_GO_verified=True,deadline_monotonic=deadline,started_wall=time.time(),status='running')
    process_marker=ROOT/'worker-process-resume.json' if resume else ROOT/'worker-process.json'
    d.write(process_marker,dict(pid=os.getpid(),started_wall=time.time(),deadline_monotonic=deadline,phase_budget_seconds=4500,resume=resume,previous_worker_process=str(resume_marker) if resume else None))
    runs=[];error=None;cold=False;setup=False;current_run=None;recovery_used=False;settled_used=False
    def boundary(seconds=0):
        assert time.monotonic()+seconds<deadline, '75分钟总预算不足'
        assert not (ROOT/'PAUSE').exists(), '父暂停边界'
        if (ROOT/'STOP').exists():
            assert historical_stop_sha and hashlib.sha256((ROOT/'STOP').read_bytes()).hexdigest()==historical_stop_sha, '新 STOP 停止边界'
    def standard_recovery(root,hosts):
        nonlocal recovery_used
        assert not recovery_used, '最多一次标准恢复';recovery_used=True
        assert hosts and set(hosts)<=set(d.HOSTS)
        def one(host):
            unit='wfb-fl-'+('server' if host=='fl00' else 'client')+'-daemon.service'
            return d.remote(host,"import subprocess,json\np=subprocess.run(['systemctl','restart',"+repr(unit)+"],capture_output=True,text=True,timeout=15)\nprint(json.dumps(dict(returncode=p.returncode,stderr=p.stderr)))",timeout=20)
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:rows=list(pool.map(one,sorted(hosts)))
        d.write(root/'single-standard-recovery.json',rows)
        assert all(r.get('returncode')==0 and r.get('result',{}).get('returncode')==0 for r in rows), '标准恢复管理或服务失败'
    def cold_ready():
        # 首次探测与恢复探测合计最多60秒；不以固定等待替代实际状态判断。
        end=time.monotonic()+60
        try:h.ready_before_audit(ROOT,'cold-start-initial',10,10,since_start,seconds=30)
        except TimeoutError:
            poll=json.loads((ROOT/'cold-start-initial-actual-ready-poll.json').read_text())['attempts'][-1]
            hosts=set(poll['fail_hosts'])|{'fl%02d'%n for n in poll['fail_nodes']}
            standard_recovery(ROOT,hosts)
            remaining=end-time.monotonic();assert remaining>0
            h.ready_before_audit(ROOT,'cold-start-after-single-recovery',10,10,since_start,seconds=remaining)
    def admit(root,label,grant,guard,run_id=None,since=None,settled=False):
        nonlocal settled_used
        a,g=h.baseline(root,label,grant,guard,run_id,since)
        unexpected=[e for row in a['checks'] for e in row['errors'] if e!='iw baseline mismatch']
        assert not unexpected, '实际审计身份/管理/USB/残留错误: '+repr(unexpected)
        if not(a['ok'] and g['passed']) and settled:
            assert not settled_used, '本次验收的一次 settled 实际复核已用尽'
            settled_used=True
            d.write(root/(label+'-original-incomplete.json'),dict(audit=a,gate=g))
            h.ready_before_audit(root,label+'-settled',grant,guard,since or since_start,seconds=30)
            a,g=h.baseline(root,label+'-settled-once',grant,guard,run_id,since)
        assert a['ok'] and g['passed'], '全十板实际基线或九 READY 未通过'
        return a,g
    try:
        boundary(300);h.local_runner_gate('initial')
        assert d.PAYLOAD.stat().st_size==44770302 and hashlib.sha256(d.PAYLOAD.read_bytes()).hexdigest()==d.PAYLOAD_SHA
        spec=importlib.util.spec_from_file_location('selected_frozen',d.TOOL/'runner.py');runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
        assert runner.bundle_sha()==d.BUNDLE_SHA and runner.NODES==d.NODES
        assert runner.TIMING['io_timeout']==600 and runner.TIMING['orchestration_timeout']==1290
        if resume:
            state=json.loads((profile/'state.json').read_text())
            assert state['completed']==PHASES and state['targets']==d.HOSTS and state['expected']==plan['expected_cold']
            assert state['commit']==d.COMMIT and state['native_sha']==state['native_before_sha']==d.SHA
            for phase in PHASES:
                rows=[json.loads((profile/(phase+'-'+host+'.json')).read_text()) for host in d.HOSTS]
                assert all(row['exit']==0 for row in rows), '已完成冷阶段必须各十板成功'
            cold=True
        else:
            old=ROOT/'pre-cold-old-fec14';old.mkdir()
            a=d.audit(old,'old-fec14',10,10,expected_fec=14,release_root=plan['old_release'],since=since_start)
            gr=old/'ready';gr.mkdir();g=d.gate(gr)
            assert a['ok'] and g['passed'], '冷前旧 FEC14 实际全十板与九 READY 不通过'
            state=json.loads((profile/'state.json').read_text())
            assert state['completed']==[] and state['targets']==d.HOSTS and state['expected']==plan['expected_cold']
            assert state['commit']==d.COMMIT and state['native_sha']==state['native_before_sha']==d.SHA
            for phase in PHASES:
                boundary(180)
                if phase=='audit':cold_ready()
                command=['python3',str(ROOT/'release-online.py'),phase,str(profile)]
                d.write(ROOT/('cold-'+phase+'-command.json'),command)
                with (ROOT/('cold-'+phase+'.log')).open('xb') as log:
                    proc=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT);began=time.monotonic()
                    while proc.poll() is None:
                        d.checkpoint('cold-'+phase)
                        if time.monotonic()-began>180 or time.monotonic()>=deadline-120:
                            proc.terminate()
                            try:proc.wait(timeout=5)
                            except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
                            raise RuntimeError('冷阶段超时: '+phase)
                        time.sleep(2)
                assert proc.returncode==0, '冷阶段失败: '+phase
                state=json.loads((profile/'state.json').read_text())
                assert state['completed']==PHASES[:PHASES.index(phase)+1]
                rows=[json.loads((profile/(phase+'-'+host+'.json')).read_text()) for host in d.HOSTS]
                assert all(row['exit']==0 for row in rows), '七阶段必须各十板一致成功'
                d.write(ROOT/('cold-'+phase+'-barrier.json'),dict(all_ten_ok=True,hosts=d.HOSTS,completed=state['completed']))
            cold=True
            admit(ROOT,'post-cold',10,10,since=since_start)
        setuproot=ROOT/'setup';setuproot.mkdir(exist_ok=True)
        if resume:
            scheduler=json.loads((setuproot/'scheduler-change.json').read_text())
            after=scheduler['result']['after']
            assert scheduler['returncode']==0 and scheduler['result']['changed'] is True
            assert after['grant_duration_ms']==30 and after['guard_interval_ms']==5 and after['fec_n']==12
        else:
            d.configure(30,5,setuproot);setup=True
        setup=True
        configured_label='configured-resume' if resume else 'configured'
        h.ready_before_audit(setuproot,configured_label,30,5,since_start)
        admit(setuproot,configured_label,30,5,since=since_start)
        tag=time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())
        for c in d.MATRIX['candidates']:
            boundary(1450);d.CURRENT=c
            root=pathlib.Path(c['rawpath']);root.mkdir();since=int(time.time())
            h.local_runner_gate('before-trial'+str(c['trial']))
            storage.gate(d,root)
            # 两次之间仅允许一次条件复核，禁止重启服务来凑连续通过。
            admit(root,'before',30,5,since=since,settled=(c['trial']==2))
            d.MATRIX['run_tag']=tag+'-trial'+str(c['trial'])
            current_run=f'fec12-{d.MATRIX["run_tag"]}-g30-d5-model'
            runs.append(dict(trial=c['trial'],run_id=current_run,since=since))
            runid=d.run_transfer(root,30,5,'model');assert runid==current_run
            rows=h.collect(runid,since,'-after')
            facts=h.independent_nine(root,c,rows)
            clean=h.exact_cleanup(runid);c['independent_exact_cleanup_ok']=clean
            assert clean, '精确清理失败或残留'
            a,g=admit(root,'after',30,5,runid,since,settled=True)
            c.update(independent_restore_ok=a['ok'],independent_ready_ok=g['passed'],independent_server_files=facts)
            assert c['runner_returncode']==0 and all(c['original_runner_final_flags'].get(k) is True for k in ['transfer_verified','cleanup_ok','restore_ok']), 'runner 原始三项必须均为真，恢复不计作通信通过'
            c.update(outcome='passed',finished_wall=time.time());current_run=None
            d.write(root/'completion-fact.json',dict(candidate=c,run_id=runid,all_required_passed=True))
            d.checkpoint('trial-finished')
        d.MATRIX['status']='complete'
    except BaseException as exc:
        error=repr(exc);d.MATRIX.update(status='stopped',error=error)
        d.write(ROOT/'STOP',dict(reason=error,traceback=traceback.format_exc(),wall=time.time()))
    finally:
        close=ROOT/'final-closeout';close.mkdir(exist_ok=True);d.CURRENT=None
        try:
            h.local_runner_gate('before-final')
            # 管理失联、USB、空间拒绝或残留仅留证停止，不追加现场变更。
            blocked=error and any(x in error.lower() for x in ['management','usb','管理','空间','512mib','storage','清理','残留','身份'])
            if blocked:raise RuntimeError('触及停止边界，保留现场事实交父处理: '+error)
            if cold:
                if current_run:
                    assert h.exact_cleanup(current_run), '失败轮精确清理失败'
                grant,guard=(30,5) if setup else (10,10)
                try:a,g=admit(close,'final',grant,guard,since=since_start,settled=True)
                except AssertionError:
                    if not error:raise
                    # 仅失败后的标准恢复，不影响两次连续通过判定，且不重跑模型。
                    poll=json.loads((close/'final-audit.json').read_text())
                    hosts={x['host'] for x in poll['checks'] if not x['ok']}
                    gate=json.loads((close/'final-ready/gate-result.json').read_text())
                    hosts|={'fl%02d'%n for n in gate['fail_nodes']}
                    assert all(e=='iw baseline mismatch' for x in poll['checks'] for e in x['errors'])
                    standard_recovery(close,hosts)
                    h.ready_before_audit(close,'failed-run-recovery',grant,guard,since_start)
                    a,g=admit(close,'final-after-recovery',grant,guard,since=since_start)
                d.write(close/'result.json',dict(full_ten_actual_baseline_ok=a['ok'],exact_nine_ready_idle=g['passed'],fec_k=8,fec_n=12,server_schedule=f'{grant}/{guard}',measurement_channel=157,restored_channel=149,runs=runs,execute_count=sum(c['execute_count'] for c in d.MATRIX['candidates']),consecutive_two_passed=error is None and all(c.get('outcome')=='passed' for c in d.MATRIX['candidates']),measurement_error=error,recovery_used=recovery_used,trial_results=d.MATRIX['candidates'],final_actual_audit=str(close/('final-after-recovery-audit.json' if (close/'final-after-recovery-audit.json').exists() else 'final-settled-once-audit.json' if (close/'final-settled-once-audit.json').exists() else 'final-audit.json')),wall=time.time()))
        except BaseException as exc:
            d.MATRIX['status']='stopped'
            d.write(close/'error.json',dict(error=repr(exc),traceback=traceback.format_exc(),wall=time.time()))
        d.checkpoint('finished')

if __name__=='__main__':main()
