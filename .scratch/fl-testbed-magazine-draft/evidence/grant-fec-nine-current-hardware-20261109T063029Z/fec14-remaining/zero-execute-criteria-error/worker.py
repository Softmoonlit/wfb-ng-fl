import importlib.util,json,pathlib,hashlib,time,os,concurrent.futures,traceback
ROOT=pathlib.Path(__file__).parent
s=importlib.util.spec_from_file_location('d',ROOT/'driver-base.py');d=importlib.util.module_from_spec(s);s.loader.exec_module(d)
s=importlib.util.spec_from_file_location('h',ROOT/'helpers.py');h=importlib.util.module_from_spec(s);s.loader.exec_module(h);h.d=d;h.ROOT=ROOT;h.write=d.write
RUNS=[];error=None
DEADLINE=json.loads((ROOT/'metadata.json').read_text())['deadline_wall']
def stop(reason):
    d.write(ROOT/'STOP',dict(reason=reason,wall=time.time()));raise RuntimeError(reason)
def fatal(a):
    return any(any(e in ['management/probe failed','USB reset observed'] for e in row['errors']) for row in a['checks'])
def check_time(required=0):
    if time.time()+required>=DEADLINE:stop('55 minute phase budget insufficient')
def standard_recovery(root,a,g):
    failed={x['host'] for x in a['checks'] if not x['ok']}|{'fl%02d'%n for n in g['fail_nodes']}
    if not failed:failed=set(d.HOSTS)
    def one(host):
        unit='wfb-fl-'+('server' if host=='fl00' else 'client')+'-daemon.service'
        return d.remote(host,"import subprocess,json\np=subprocess.run(['systemctl','restart',"+repr(unit)+"],capture_output=True,text=True,timeout=15)\nprint(json.dumps(dict(returncode=p.returncode,stderr=p.stderr)))",timeout=20)
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:rows=list(pool.map(one,sorted(failed)))
    d.write(root/'single-standard-recovery.json',rows)
    if any(x.get('returncode')!=0 or x.get('result',{}).get('returncode')!=0 for x in rows):stop('standard recovery failed')
def admission(root,label,grant,guard,run_id=None,since=None,recover=False):
    a,g=h.baseline(root,label,grant,guard,run_id,since)
    if fatal(a):stop('management or USB reset boundary')
    if a['ok'] and g['passed']:return a,g
    d.write(root/(label+'-original-STOP.json'),dict(reason='independent baseline not yet complete',audit=a['checks'],gate=g,wall=time.time()))
    time.sleep(3)
    a,g=h.baseline(root,label+'-settled-once',grant,guard,run_id,since)
    if fatal(a):stop('management or USB reset boundary')
    if not(a['ok'] and g['passed']) and recover:
        standard_recovery(root,a,g)
        d.wait_server_ready(root,grant,guard,'recovery-api-ready')
        a,g=h.baseline(root,label+'-after-single-recovery',grant,guard,None,since)
    if not(a['ok'] and g['passed']):stop('full ten baseline/nine READY restoration incomplete')
    return a,g
try:
    h.local_runner_gate('initial')
    spec=importlib.util.spec_from_file_location('frozen',d.TOOL/'runner.py');rr=importlib.util.module_from_spec(spec);spec.loader.exec_module(rr)
    assert rr.bundle_sha()==d.BUNDLE_SHA
    assert d.PAYLOAD.stat().st_size==44770302 and hashlib.sha256(d.PAYLOAD.read_bytes()).hexdigest()==d.PAYLOAD_SHA
    d.write(ROOT/'input-verification.json',dict(bundle_sha=rr.bundle_sha(),model_size=44770302,model_sha=d.PAYLOAD_SHA))
    d.wait_server_ready(ROOT,10,10,'initial-api-ready')
    admission(ROOT,'initial',10,10)
    d.write(ROOT/'admission.json',dict(ok=True,wall=time.time()))
    for grant in [40,60]:
        small_ok=False
        for mode in ['smoke','model']:
            if mode=='model' and not small_ok:continue
            check_time(1420 if mode=='model' else 180)
            c=next(x for x in d.MATRIX['candidates'] if x['grant_duration_ms']==grant and x['mode']==mode);d.CURRENT=c
            root=pathlib.Path(c['rawpath']);root.mkdir();since=int(time.time())
            d.configure(grant,10,root);d.wait_server_ready(root,grant,10)
            admission(root,'before',grant,10)
            h.local_runner_gate(f'before-g{grant}-{mode}')
            if mode=='model':d.write(root/'independent-continuation-admission.json',dict(ok=True,smoke_not_repeated=True,wall=time.time()))
            runid=d.run_transfer(root,grant,10,mode);RUNS.append((runid,since))
            # Preserve the first actual after-audit, including transient 157.
            a,g=h.baseline(root,'after',grant,10,runid,since)
            c['immediate_independent_restore_ok']=a['ok'];c['immediate_independent_ready_ok']=g['passed']
            h.collect(runid,since,'-after')
            clean=h.exact_cleanup(runid);c['independent_exact_cleanup_ok']=clean
            if fatal(a):stop('management or USB reset observed after execute')
            if not clean or not c['cleanup_ok']:stop('exact cleanup failure')
            if not(a['ok'] and g['passed']):
                d.write(root/'STOP',dict(reason='original immediate independent recovery incomplete',wall=time.time()))
                # One bounded settled check, followed if necessary by one standard restoration.
                time.sleep(3)
                a,g=h.baseline(root,'after-settled-once',grant,10,runid,since)
                if fatal(a):stop('management or USB reset during recovery')
                if not(a['ok'] and g['passed']):
                    standard_recovery(root,a,g);d.wait_server_ready(root,grant,10,'recovery-api-ready')
                    a,g=h.baseline(root,'after-single-standard-recovery',grant,10,None,since)
                if not(a['ok'] and g['passed']):stop('independent restoration remains incomplete')
            c['independent_restore_ok']=a['ok'];c['independent_ready_ok']=g['passed']
            c['outcome']='passed' if c['transfer_verified'] else 'transfer_failed_recovered'
            c['finished_wall']=time.time();d.checkpoint('candidate-finished')
            if mode=='smoke':small_ok=c['transfer_verified'] and clean and a['ok'] and g['passed']
    d.MATRIX['status']='complete'
except BaseException as exc:
    error=repr(exc);d.MATRIX.update(status='stopped',error=error)
    d.write(ROOT/'STOP',dict(reason=error,traceback=traceback.format_exc(),wall=time.time()))
finally:
    close=ROOT/'final-closeout';close.mkdir(exist_ok=True)
    try:
        d.CURRENT=None
        h.local_runner_gate('before-final')
        d.configure(10,10,close,'server10-10-standard-recovery');d.wait_server_ready(close,10,10,'final-api-ready')
        a,g=admission(close,'final-baseline',10,10,recover=True)
        for runid,since in RUNS:h.collect(runid,since,'-final-idle')
        h.local_runner_gate('final')
        d.write(close/'result.json',dict(full_ten_actual_baseline_ok=a['ok'],exact_nine_ready_idle=g['passed'],server_schedule='10/10',runs=RUNS,execute_count=sum(c['execute_count'] for c in d.MATRIX['candidates']),measurement_error=error,wall=time.time()))
    except BaseException as exc:
        d.write(close/'error.json',dict(error=repr(exc),traceback=traceback.format_exc(),wall=time.time()))
    d.checkpoint('finished')
