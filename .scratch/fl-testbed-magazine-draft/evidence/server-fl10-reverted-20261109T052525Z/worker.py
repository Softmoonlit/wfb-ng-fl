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

def collect(run_id,since):
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
    write(ROOT/(run_id+'-exact-uftp-kernel-files.json'),dict(idle_status=st,rows=rows))

def exact_cleanup(run_id):
    src=(d.TOOL/'recover.py').read_text()
    code='import sys\nsys.argv='+repr(['recover.py',run_id])+'\n'+src
    # Source contains a shebang only, no future import; running as __main__ executes exact recover.
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:rows=list(pool.map(lambda h:d.remote(h,code,timeout=24),d.HOSTS))
    write(ROOT/(run_id+'-independent-exact-cleanup.json'),rows)
    return all(x.get('returncode')==0 and x.get('result',{}).get('ok') for x in rows)

error=None;admitted=False
try:
    local_runner_gate('initial')
    payload=dict(path=str(d.PAYLOAD),size_bytes=d.PAYLOAD.stat().st_size,sha256=hashlib.sha256(d.PAYLOAD.read_bytes()).hexdigest())
    spec=importlib.util.spec_from_file_location('frozen_runner',d.TOOL/'runner.py');rr=importlib.util.module_from_spec(spec);spec.loader.exec_module(rr)
    assert payload['size_bytes']==44770302 and payload['sha256']==d.PAYLOAD_SHA and rr.bundle_sha()==d.BUNDLE_SHA
    write(ROOT/'model-input-verification.json',dict(payload=payload,bundleSHA=rr.bundle_sha(),manifest=json.loads(d.MANIFEST.read_text())))
    d.wait_server_ready(ROOT,10,10,'initial-server-readiness')
    a=d.audit(ROOT,'initial-ten-board-baseline',10,10);assert a['ok'],a['checks']
    gr=ROOT/'initial-ready';gr.mkdir();assert d.gate(gr)['passed']
    admitted=True;write(ROOT/'admission.json',dict(ok=True,wall=time.time()))
    while not (ROOT/'GO').exists():
        if (ROOT/'STOP').exists():raise RuntimeError('STOP before measurement')
        time.sleep(1)
    for c in d.MATRIX['candidates']:
        d.CURRENT=c
        if (ROOT/'STOP').exists():raise RuntimeError('STOP at candidate boundary')
        root=pathlib.Path(c['rawpath']);root.mkdir();since=int(time.time());c['started_wall']=time.time()
        d.configure(30,5,root);d.wait_server_ready(root,30,5)
        assert d.gate(root)['passed'],'nine READY gate failed'
        a=d.audit(root,'before',30,5,since=since);assert a['ok'],a['checks']
        local_runner_gate('before-'+c['mode'])
        run_id=d.run_transfer(root,30,5,c['mode']);RUNS.append((run_id,since))
        # Preserve raw results before independent restoration and never rewrite them.
        a=d.audit(root,'after',30,5,run_id=run_id,since=since);c['independent_restore_ok']=a['ok']
        collect(run_id,since)
        settled=root/'independent-ready';settled.mkdir();ready=d.gate(settled)
        assert a['ok'] and ready['passed'],'independent restoration incomplete: stop without another execute'
        assert c['transfer_verified'] and c['cleanup_ok'] and c['runner_restore_ok'],'raw loop failure: stop without retry'
        c['outcome']='passed';c['finished_wall']=time.time();d.checkpoint('complete_candidate')
    d.MATRIX['status']='measurements_complete'
except BaseException as exc:
    error=repr(exc);d.MATRIX['status']='stopped';d.MATRIX['error']=error
    if d.CURRENT:d.CURRENT['error']=error
    write(ROOT/'STOP',dict(reason=error,wall=time.time()))
    d.checkpoint('stopped')
    print(error,flush=True)
finally:
    close=ROOT/'final-closeout';close.mkdir(exist_ok=True)
    try:
        clean=all([exact_cleanup(r) for r,_ in RUNS])
        d.CURRENT=None
        # One standard server recovery only. Configure performs it if scheduler changes.
        cr=d.configure(10,10,close,'server10-10-standard-recovery')
        d.wait_server_ready(close,10,10,'server-baseline-ready')
        a=d.audit(close,'actual-baseline',10,10)
        gr=close/'ready';gr.mkdir();ready=d.gate(gr)
        if not (a['ok'] and ready['passed']) and not cr.get('result',{}).get('changed'):
            restart=d.remote('fl00',"import subprocess,json\np=subprocess.run(['systemctl','restart','wfb-fl-server-daemon.service'],capture_output=True,text=True,timeout=15)\nprint(json.dumps(dict(returncode=p.returncode,stderr=p.stderr)))",timeout=20)
            write(close/'single-standard-server-restart.json',restart)
            d.wait_server_ready(close,10,10,'after-standard-restart-readiness')
            a=d.audit(close,'after-standard-restart-baseline',10,10)
            gr=close/'after-standard-restart-ready';gr.mkdir();ready=d.gate(gr)
        local_runner_gate('final')
        write(close/'result.json',dict(full_ten_actual_baseline_ok=a['ok'],exact_nine_ready_idle=ready['passed'],exact_cleanup_ok=clean,server_schedule='10/10',independent_restoration=True,measurement_error=error,raw_flags=[dict(mode=c['mode'],execute_count=c['execute_count'],transfer_verified=c['transfer_verified'],cleanup_ok=c['cleanup_ok'],restore_ok=c['restore_ok']) for c in d.MATRIX['candidates']],wall=time.time()))
    except BaseException as exc:
        write(close/'error.json',dict(error=repr(exc),wall=time.time()));print('CLOSEOUT '+repr(exc),flush=True)
    d.MATRIX['status']='complete' if error is None else 'stopped';d.checkpoint('finished')
