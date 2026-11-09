import importlib.util,json,pathlib,concurrent.futures,hashlib,os,time,subprocess
ROOT=pathlib.Path(__file__).parent
spec=importlib.util.spec_from_file_location('d',ROOT/'driver-base.py');d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)
write=d.write
RUNS=[]
def local_runner_gate(label):
    suspects=[]
    for p in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
        try:a=p.read_bytes().decode().split('\0')
        except (OSError,UnicodeError):continue
        if len(a)>1 and any(pathlib.Path(x).name=='runner.py' or pathlib.Path(x).name.startswith('orchestration-') for x in a[1:3]):suspects.append(dict(pid=p.parent.name,argv=a))
    write(ROOT/(label+'-local-runner-gate.json'),dict(ok=not suspects,suspects=suspects));assert not suspects,suspects

def collect(run_id,since,label=""):
    st=d.status();assert st.get('active_job') is None and st.get('server_state')=='IDLE'
    code=r'''import pathlib,hashlib,json,subprocess,socket
root=pathlib.Path('/srv/wfb-ng-fl')/('comm-'+RUN_ID)
logs={};files=[]
if root.exists():
 for p in root.rglob('*'):
  if not p.is_file():continue
  n=p.name
  if n.endswith(('.log','.status','.json','.jsonl')):logs[str(p)]=p.read_text(errors='replace')
  elif p.stat().st_size>=1048576:
   files.append(dict(path=str(p),size_bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest(),complete_model=(p.stat().st_size==44770302 and hashlib.sha256(p.read_bytes()).hexdigest()=='4b55cee2f0bdcfbbf610167a505efee0e2c50e3306fed02bda3613807c234e96')))
k=subprocess.run(['journalctl','-k','--since','@'+str(SINCE),'--no-pager','-o','cat'],capture_output=True,text=True,timeout=4)
u=subprocess.run(['journalctl','-u','comm-smoke-'+RUN_ID+'-'+socket.gethostname()+'.service','--no-pager','-o','cat'],capture_output=True,text=True,timeout=4)
print(json.dumps(dict(host=socket.gethostname(),run_id=RUN_ID,logs=logs,files=files,kernel=k.stdout,kernel_stderr=k.stderr,unit_journal=u.stdout)))
'''
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:rows=list(pool.map(lambda h:d.remote(h,'RUN_ID='+repr(run_id)+'\nSINCE='+repr(since)+'\n'+code,timeout=24),d.HOSTS))
    write(ROOT/(run_id+label+'-exact-uftp-kernel-files.json'),dict(idle_status=st,rows=rows))

def exact_cleanup(run_id):
    src=(d.TOOL/'recover.py').read_text()
    code='import sys\nsys.argv='+repr(['recover.py',run_id])+'\n'+src
    # Source contains a shebang only, no future import; running as __main__ executes exact recover.
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:rows=list(pool.map(lambda h:d.remote(h,code,timeout=24),d.HOSTS))
    write(ROOT/(run_id+'-independent-exact-cleanup.json'),rows)
    return all(x.get('returncode')==0 and x.get('result',{}).get('ok') for x in rows)

def baseline(root,label,grant,guard,run_id=None,since=None):
    a=d.audit(root,label,grant,guard,run_id=run_id,since=since)
    gr=root/(label+'-ready');gr.mkdir();g=d.gate(gr)
    return a,g

def settled_admission(root,label,grant,guard):
    a,g=baseline(root,label,grant,guard)
    if not (a['ok'] and g['passed']):
        write(root/(label+'-settled-recheck-reason.json'),dict(first_checks=a['checks'],first_gate=g,wall=time.time(),bounded_recheck=1))
        time.sleep(3)
        a,g=baseline(root,label+'-settled-once',grant,guard)
    assert a['ok'] and g['passed'],'full baseline/READY failed after one bounded settled recheck'
    return a,g

error=None
try:
    local_runner_gate('initial')
    prior=json.loads((ROOT.parent/'final-closeout/result.json').read_text())
    old=json.loads((ROOT.parent/'matrix.json').read_text())
    assert prior['full_ten_actual_baseline_ok'] and prior['exact_nine_ready_idle'] and prior['exact_cleanup_ok']
    assert sum(c['execute_count'] for c in old['candidates'])==1 and old['candidates'][1]['execute_count']==0
    payload=dict(path=str(d.PAYLOAD),size_bytes=d.PAYLOAD.stat().st_size,sha256=hashlib.sha256(d.PAYLOAD.read_bytes()).hexdigest())
    spec=importlib.util.spec_from_file_location('frozen_runner',d.TOOL/'runner.py');rr=importlib.util.module_from_spec(spec);spec.loader.exec_module(rr)
    assert payload['size_bytes']==44770302 and payload['sha256']==d.PAYLOAD_SHA and rr.bundle_sha()==d.BUNDLE_SHA
    write(ROOT/'model-input-verification.json',dict(payload=payload,bundleSHA=rr.bundle_sha(),prior_recovery=prior,prior_execute_count=1))
    d.wait_server_ready(ROOT,10,10,'initial-server-readiness')
    settled_admission(ROOT,'initial-ten-board-baseline',10,10)
    write(ROOT/'admission.json',dict(ok=True,wall=time.time(),remaining_execute=1))
    while not (ROOT/'GO').exists():
        if (ROOT/'STOP').exists():raise RuntimeError('STOP before measurement')
        time.sleep(1)
    c=d.MATRIX['candidates'][0];d.CURRENT=c
    assert not (ROOT/'STOP').exists()
    root=pathlib.Path(c['rawpath']);root.mkdir();since=int(time.time());c['started_wall']=time.time()
    d.configure(30,5,root);d.wait_server_ready(root,30,5)
    settled_admission(root,'before',30,5)
    local_runner_gate('before-model')
    run_id='rsmc20261109T060537Z-g30-d5-model';RUNS.append((run_id,since))
    actual=d.run_transfer(root,30,5,'model');assert actual==run_id
    a,g=baseline(root,'after',30,5,run_id=run_id,since=since)
    c['independent_restore_ok']=a['ok'];c['independent_ready_ok']=g['passed']
    collect(run_id,since,'-before-final-recovery')
    assert c['transfer_verified'] and c['cleanup_ok'] and c['runner_restore_ok'],'raw model loop failure; no retry'
    assert a['ok'] and g['passed'],'immediate independent restoration incomplete; preserve failure'
    c['outcome']='passed';c['finished_wall']=time.time();d.checkpoint('complete_candidate')
except BaseException as exc:
    error=repr(exc);d.MATRIX['status']='stopped';d.MATRIX['error']=error
    if d.CURRENT:d.CURRENT['error']=error
    write(ROOT/'STOP',dict(reason=error,wall=time.time()));d.checkpoint('stopped');print(error,flush=True)
finally:
    close=ROOT/'final-closeout';close.mkdir(exist_ok=True)
    try:
        clean=all([exact_cleanup(r) for r,_ in RUNS]);d.CURRENT=None
        cr=d.configure(10,10,close,'server10-10-standard-recovery');d.wait_server_ready(close,10,10,'server-baseline-ready')
        a,g=baseline(close,'actual-baseline',10,10)
        if not(a['ok'] and g['passed']):
            time.sleep(3);a,g=baseline(close,'settled-once-baseline',10,10)
        if not(a['ok'] and g['passed']):
            failed=set(x['host'] for x in a['checks'] if not x['ok'])|set('fl%02d'%n for n in g['fail_nodes'])
            if cr.get('result',{}).get('changed'):failed.discard('fl00')
            def recover_host(h):
                unit='wfb-fl-server-daemon.service' if h=='fl00' else 'wfb-fl-client-daemon.service'
                return d.remote(h,"import subprocess,json\np=subprocess.run(['systemctl','restart',"+repr(unit)+"],capture_output=True,text=True,timeout=15)\nprint(json.dumps(dict(returncode=p.returncode,stderr=p.stderr)))",timeout=20)
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:recoveries=list(pool.map(recover_host,sorted(failed)))
            write(close/'single-necessary-standard-recovery.json',dict(hosts=sorted(failed),results=recoveries))
            a,g=baseline(close,'after-single-standard-recovery',10,10)
        for run_id,since in RUNS:collect(run_id,since,'-final-idle')
        local_runner_gate('final')
        write(close/'result.json',dict(full_ten_actual_baseline_ok=a['ok'],exact_nine_ready_idle=g['passed'],exact_cleanup_ok=clean,server_schedule='10/10',independent_restoration=True,measurement_error=error,raw_flags=[dict(mode=c['mode'],execute_count=c['execute_count'],transfer_verified=c['transfer_verified'],cleanup_ok=c['cleanup_ok'],restore_ok=c['restore_ok']) for c in d.MATRIX['candidates']],total_execute_count=1+sum(c['execute_count'] for c in d.MATRIX['candidates']),wall=time.time()))
    except BaseException as exc:
        write(close/'error.json',dict(error=repr(exc),wall=time.time()));print('CLOSEOUT '+repr(exc),flush=True)
    d.MATRIX['status']='complete' if error is None else 'stopped';d.checkpoint('finished')
