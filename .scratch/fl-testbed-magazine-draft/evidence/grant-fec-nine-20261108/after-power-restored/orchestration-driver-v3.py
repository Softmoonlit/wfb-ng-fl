import concurrent.futures as futures
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time
import urllib.request

CWD = Path('/home/fl/projects/wfb-ng-fl')
os.chdir(CWD)
ROOT = CWD / '.scratch/fl-testbed-magazine-draft/evidence/grant-fec-nine-20261108/after-power-restored'
TOOL = CWD / '.scratch/fl-testbed-magazine-draft/communication'
NODES = [1, 2, 3, 5, 6, 7, 8, 9, 10]
HOSTS = ['fl00'] + [f'fl{n:02d}' for n in NODES]
COMMIT = '1cd6264d5d6be660998ce60f3a349063009ecfa7'
RELEASE = '/srv/wfb-ng-fl/releases/heartbeat-1cd6264d5d6b-20261108T115743Z'
SHA = '44d6bbf500f51df4d0b3ae8cd7a53c4a45cb55b09ca66cb5217e7ac75674feb2'
ROOT.mkdir(parents=True, exist_ok=True)
MATRIX = dict(schema_version=1, profile='fec14-guard10', participants=NODES, platform_slots=list(range(1,11)), source_commit=COMMIT, release_root=RELEASE, native_sha256=SHA, schedule_order=[10,20,30,40,60], status='running', candidates=[])
for grant in MATRIX['schedule_order']:
    MATRIX['candidates'].append(dict(grant_duration_ms=grant, guard_interval_ms=10, fec_n=14, stage='pending', readiness_fail_nodes=[], DLduration=None, ULduration=None, transfer_verified=False, cleanup_ok=None, restore_ok=None, rawpath=str(ROOT / f'fec14-g{grant}-d10'), bundleSHA=None, source_commit=COMMIT, nativeSHA=SHA, execute_count=0))
CURRENT = None

def write(path, value):
    path = Path(path)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temp.replace(path)

def checkpoint(stage=None):
    if stage and CURRENT is not None:
        CURRENT['stage'] = stage
    MATRIX['updated_wall'] = time.time()
    MATRIX['updated_monotonic'] = time.monotonic()
    write(ROOT / 'matrix.json', MATRIX)
    write(ROOT / 'checkpoint.json', dict(wall=time.time(), monotonic=time.monotonic(), pid=os.getpid(), status=MATRIX['status'], current=CURRENT, stage=stage or (CURRENT or {}).get('stage')))
    print(json.dumps(dict(monotonic=time.monotonic(), status=MATRIX['status'], grant=(CURRENT or {}).get('grant_duration_ms'), stage=stage or (CURRENT or {}).get('stage'))), flush=True)

def status():
    with urllib.request.urlopen('http://127.0.0.1:9090/api/v1/status', timeout=4) as response:
        return json.load(response)

def remote(host, code, timeout=10):
    command = ['sudo','-n','python3','-c',code]
    argv = command if host == 'fl00' else ['ssh','-T','-oBatchMode=yes','-oConnectTimeout=4',host,shlex.join(command)]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return dict(host=host, returncode=result.returncode, result=json.loads(result.stdout) if result.returncode == 0 else result.stdout, stderr=result.stderr)
    except Exception as exc:
        return dict(host=host, error=repr(exc))

AUDIT_CODE = r'''import json,pathlib,subprocess,socket,struct,hashlib,time
host=socket.gethostname();role='server' if host=='fl00' else 'client';unit='wfb-fl-'+role+'-daemon.service'
service=subprocess.check_output(['systemctl','show',unit,'-p','MainPID','-p','ActiveState'],text=True,timeout=3)
records=[]
for p in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
 try:
  argv=p.read_bytes().decode().rstrip(chr(0)).split(chr(0))
  if argv[0]!='/usr/bin/wfb_v6_uplink':continue
  port=int(argv[argv.index('--radio-control-port')+1]);interface=argv[argv.index('--air-interface')+1];queries={}
  for cmd,fmt,key in [(4,'!II7B','radio'),(3,'!II2B','fec')]:
   with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
    s.settimeout(2);s.sendto(struct.pack('!IB',0x434f4d4d,cmd),('127.0.0.1',port));raw,peer=s.recvfrom(4096)
    assert peer==('127.0.0.1',port);queries[key]=list(struct.unpack(fmt,raw))
  records.append(dict(pid=p.parent.name,argv=argv,sha256=hashlib.sha256((p.parent/'exe').read_bytes()).hexdigest(),queries=queries,iw=subprocess.check_output(['iw','dev',interface,'info'],text=True,timeout=3)))
 except (OSError,UnicodeError):continue
source=pathlib.Path(RELEASE_ROOT)
manifest=json.loads((source/'manifest.json').read_text())
installed=pathlib.Path(manifest['installed']);identity=json.loads((installed/'build_identity.json').read_text())
power=subprocess.check_output(['nvpmodel','-q'],text=True,timeout=3)
owned=subprocess.run(['systemctl','is-active',OWNED_UNIT],capture_output=True,text=True,timeout=3).stdout.strip() if OWNED_UNIT else None
logs=subprocess.run(['journalctl','-k','--since','@'+str(SINCE),'-o','cat','--no-pager'],capture_output=True,text=True,timeout=3) if SINCE else None
print(json.dumps(dict(host=host,boot_id=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),uptime=pathlib.Path('/proc/uptime').read_text().strip(),service=service,native=records,power=power,manifest_commit=manifest['commit'],manifest_native_sha=manifest['native_sha'],installed_commit=identity['commit'],source_sha=hashlib.sha256((source/'source/wfb_v6_uplink').read_bytes()).hexdigest(),installed_native_sha=hashlib.sha256(pathlib.Path('/usr/bin/wfb_v6_uplink').read_bytes()).hexdigest(),owned_unit_state=owned,kernel_log=logs.stdout if logs else '',kernel_error=logs.stderr if logs else '',monotonic=time.monotonic())))
'''

def audit(root, label, grant, run_id=None, since=None):
    checkpoint('audit_' + label)
    def one(host):
        unit = 'comm-smoke-' + run_id + '-' + host + '.service' if run_id else ''
        code = 'RELEASE_ROOT=' + repr(RELEASE) + '\nOWNED_UNIT=' + repr(unit) + '\nSINCE=' + repr(since) + '\n' + AUDIT_CODE
        return remote(host, code)
    begin = time.monotonic()
    with futures.ThreadPoolExecutor(max_workers=10) as pool:
        rows = list(pool.map(one, HOSTS))
    details=[]
    for row in rows:
        errors=[]
        if row.get('returncode') != 0:
            errors.append('management/probe failed')
        else:
            data=row['result']; natives=data['native']
            if 'ActiveState=active' not in data['service']:errors.append('resident service inactive')
            if any(data[key] != expected for key,expected in [('manifest_commit',COMMIT),('installed_commit',COMMIT),('manifest_native_sha',SHA),('source_sha',SHA),('installed_native_sha',SHA)]):errors.append('source/native identity mismatch')
            if data['power'].strip() != 'NV Power Mode: 15W\n0':errors.append('power mismatch')
            if len(natives)!=1:errors.append('native count mismatch')
            else:
                native=natives[0];a=native['argv'];radio=native['queries']['radio'];fec=native['queries']['fec'];iw=native['iw']
                if native['sha256']!=SHA:errors.append('proc exe SHA mismatch')
                if radio[:2]!=[0x434f4d4d,0] or radio[4:7]!=[0,40,3 if row['host']=='fl00' else 4]:errors.append('GET radio baseline mismatch')
                if fec!=[0x434f4d4d,0,8,14]:errors.append('GET FEC mismatch')
                if not all(text in iw for text in ['channel 149 ','width: 40 MHz','txpower -18.00 dBm','type monitor']):errors.append('iw baseline mismatch')
                if row['host']=='fl00':
                    if a[a.index('--grant-duration-ms')+1]!=str(grant) or a[a.index('--guard-interval-ms')+1]!='10':errors.append('schedule mismatch')
                    slots=[int(x) for x in a[a.index('--known-clients')+1].split(',')]
                    targets=[int(a[i+1].split(':')[0]) for i,x in enumerate(a) if x=='--client-target']
                    if slots!=targets or slots!=list(range(1,11)):errors.append('platform slot pool mismatch')
            if run_id and data['owned_unit_state'] not in ['inactive','failed','unknown']:errors.append('owned unit active')
            if re.search(r'(?i)(reset\s+(?:high|full|super|low)[^\n]*USB|USB[^\n]*reset)',data['kernel_log']):errors.append('USB reset observed')
        details.append(dict(host=row['host'],ok=not errors,errors=errors))
    result=dict(ok=all(x['ok'] for x in details), elapsed=time.monotonic()-begin, checks=details,fl04='excluded; not contacted per current authorization',rows=rows)
    write(root/(label+'-audit.json'),result)
    return result

def configure(grant, root, label='scheduler-change'):
    checkpoint('configure_' + str(grant))
    code='''import pathlib,json,subprocess,os,time
path=pathlib.Path('/etc/wfb-ng-fl/server.json');before=json.loads(path.read_text());after=dict(before,grant_duration_ms=GRANT,guard_interval_ms=10)
changed=before!=after
old=subprocess.check_output(['systemctl','show','wfb-fl-server-daemon.service','-p','MainPID','--value'],text=True).strip()
if changed:
 temp=path.with_name('server.json.grant-fec-tmp');temp.write_text(json.dumps(after,indent=2)+'\\n');os.chmod(temp,path.stat().st_mode);os.chown(temp,path.stat().st_uid,path.stat().st_gid);os.replace(temp,path)
 subprocess.run(['systemctl','restart','wfb-fl-server-daemon.service'],check=True,timeout=15)
new=subprocess.check_output(['systemctl','show','wfb-fl-server-daemon.service','-p','MainPID','--value'],text=True).strip()
print(json.dumps(dict(before=before,after=after,changed=changed,old_pid=old,new_pid=new,monotonic=time.monotonic())))
'''.replace('GRANT',str(grant))
    result=remote('fl00',code,timeout=20);write(root/(label+'.json'),result)
    if result.get('returncode')!=0:raise RuntimeError('server configuration/restart failed')
    return result

def gate(root):
    checkpoint('readiness_gate')
    begin=time.monotonic();end=begin+30;passed=False;last={};failure=[]
    with (root/'readiness-observations.jsonl').open('x',buffering=1) as log:
        while time.monotonic()<end:
            try:
                last=status()
            except (urllib.error.URLError, TimeoutError) as exc:
                log.write(json.dumps(dict(wall=time.time(),monotonic=time.monotonic(),startup_http_error=repr(exc)))+'\n')
                checkpoint()
                if time.monotonic()<end:time.sleep(min(2,end-time.monotonic()))
                continue
            online=sorted(int(k) for k,v in last['nodes'].items() if v.get('readiness')!='OFFLINE')
            failure=[n for n in NODES if any(last['nodes'].get(str(n),{}).get(k)!=v for k,v in [('readiness','READY'),('reported_state','IDLE'),('current_channel',149)])]
            ready=not failure and online==NODES and last.get('active_job') is None and last.get('server_state')=='IDLE'
            log.write(json.dumps(dict(wall=time.time(),monotonic=time.monotonic(),ready=ready,online=online,fail_nodes=failure,status=last))+'\n')
            if ready:
                try:
                    check=subprocess.run(['python3',str(TOOL/'check-all-ready.py')],capture_output=True,text=True,timeout=min(10,max(.1,end-time.monotonic())))
                    write(root/'check-all-ready.json',dict(returncode=check.returncode,stdout=check.stdout,stderr=check.stderr))
                    if check.returncode==0:passed=True;break
                except subprocess.TimeoutExpired:break
            checkpoint()
            if time.monotonic()<end:time.sleep(min(2,end-time.monotonic()))
    result=dict(passed=passed,elapsed=time.monotonic()-begin,fail_nodes=failure,status=last)
    write(root/'gate-result.json',result)
    return result

def run_smoke(root,grant):
    spec=importlib.util.spec_from_file_location('matrix_comm_runner',TOOL/'runner.py');runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
    assert runner.NODES==NODES and runner.TIMING['io_timeout']==600 and runner.TIMING['orchestration_timeout']==1290
    bundle=runner.bundle_sha();hashes={name:hashlib.sha256((TOOL/name).read_bytes()).hexdigest() for name in ['runner.py','node_agent.py','recover.py']}
    CURRENT['bundleSHA']=bundle
    write(root/'frozen-bundle.json',dict(bundleSHA=bundle,files=hashes,source_commit=COMMIT,nativeSHA=SHA))
    run_id=f'fec14-g{grant}-d10-smoke'
    command=['python3',str(TOOL/'runner.py'),'--smoke','--native-sha',SHA,'--release-commit',COMMIT,'--release-root',RELEASE,'--grant-duration-ms',str(grant),'--guard-interval-ms','10','--channel','157','--bandwidth-mhz','40','--downlink-mcs','3','--uplink-mcs','4','--fec-n','14','--uftp-rate-kbps','12000','--run-id',run_id,'--output',str(root),'--execute','--reviewed-sha',bundle]
    write(root/'command.json',command)
    CURRENT['execute_count']=1
    checkpoint('execute')
    began=time.monotonic()
    with (root/'runner.log').open('xb') as output:
        proc=subprocess.Popen(command,stdout=output,stderr=subprocess.STDOUT)
        write(root/'runner-process.json',dict(pid=proc.pid,started_monotonic=began))
        path=root/run_id/'progress.jsonl'
        while proc.poll() is None:
            if path.exists():
                try:
                    lines=path.read_text().splitlines();last=json.loads(lines[-1]);CURRENT['runner_last_phase']=last['phase'];CURRENT['runner_last_monotonic']=last.get('monotonic')
                except (ValueError,IndexError):pass
            checkpoint('execute')
            if time.monotonic()-began>1365:
                raise RuntimeError('runner exceeded existing overall hard boundary; stop matrix for investigation')
            time.sleep(5)
    lines=[json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    final=next((row for row in reversed(lines) if row.get('phase')=='result'),{})
    CURRENT.update(runner_returncode=proc.returncode,transfer_verified=final.get('transfer_verified',False),cleanup_ok=final.get('cleanup_ok',False),restore_ok=final.get('restore_ok',False),runner_restore_ok=final.get('restore_ok',False),progress_path=str(path),elapsed=time.monotonic()-began)
    for phase,key in [('publish','DLduration'),('submit','ULduration')]:
        starts=[row['dispatch_start'] for row in lines if row.get('phase')==phase and row.get('event')=='dispatch_start']
        completions=[row['monotonic'] for row in lines if row.get('phase')==phase and 'ok' in row]
        CURRENT[key]=max(completions)-starts[0] if starts and completions else None
    CURRENT['failures']=[row for row in lines if row.get('ok') is False]
    CURRENT['bundle_unchanged']=all(hashlib.sha256((TOOL/name).read_bytes()).hexdigest()==digest for name,digest in hashes.items())
    write(root/'run-summary.json',dict(candidate=CURRENT,result=final))
    if not CURRENT['bundle_unchanged']:raise RuntimeError('tool bundle changed during execution')
    return run_id


def wait_server_ready(root, grant, label='server-readiness'):
    checkpoint('server_readiness_15s')
    begin=time.monotonic();deadline=begin+15;attempts=[]
    code="""import pathlib,json,hashlib
items=[]
for p in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
 try:
  argv=p.read_bytes().decode().rstrip(chr(0)).split(chr(0))
  if argv[0]=='/usr/bin/wfb_v6_uplink':items.append(dict(pid=p.parent.name,argv=argv,sha256=hashlib.sha256((p.parent/'exe').read_bytes()).hexdigest()))
 except (OSError,UnicodeError):pass
print(json.dumps(dict(native=items)))
"""
    while time.monotonic()<deadline:
        attempt=dict(monotonic=time.monotonic())
        try:
            with urllib.request.urlopen('http://127.0.0.1:9090/api/v1/status',timeout=min(2,max(.1,deadline-time.monotonic()))) as response: current=json.load(response)
            attempt['status']=current
            actual=remote('fl00',code,timeout=min(3,max(.1,deadline-time.monotonic())))
            attempt['actual']=actual
            natives=actual.get('result',{}).get('native',[]) if actual.get('returncode')==0 else []
            ready=False
            if len(natives)==1:
                n=natives[0];a=n['argv']
                ready=(current.get('server_state')=='IDLE' and current.get('active_job') is None and n['sha256']==SHA and a[a.index('--grant-duration-ms')+1]==str(grant) and a[a.index('--guard-interval-ms')+1]=='10' and a[a.index('--fec-k')+1]=='8' and a[a.index('--fec-n')+1]=='14')
            attempt['ready']=ready
            attempts.append(attempt)
            write(root/(label+'.json'),dict(passed=ready,deadline_seconds=15,elapsed=time.monotonic()-begin,attempts=attempts))
            if ready and time.monotonic()<=deadline:return
        except Exception as exc:
            attempt['error']=repr(exc);attempts.append(attempt)
            write(root/(label+'.json'),dict(passed=False,deadline_seconds=15,elapsed=time.monotonic()-begin,attempts=attempts))
        checkpoint()
        if time.monotonic()<deadline:time.sleep(min(.5,deadline-time.monotonic()))
    raise RuntimeError('server actual HTTP/native readiness exceeded 15s deadline')

MATRIX=json.loads((ROOT/'matrix.json').read_text())
write(ROOT/'matrix-before-explicit-readiness-driver.json',MATRIX)
MATRIX.setdefault('recoveryhistory',[]).extend([dict(reason='intentional g20 server restart API startup race, not RF/retransmission',original_stop_snapshot='matrix-before-startup-tool-correction.json',source_unchanged=True),dict(reason='driver handoff after successful g40 result; preserve all completed experiments, add explicit 15s HTTP/native startup gate',handoff='driver-handoff.json',wall=time.time())])
MATRIX['status']='running'
MATRIX.pop('error',None)
CURRENT=None
try:
    for CURRENT in MATRIX['candidates']:
        if CURRENT['stage']=='complete':continue
        grant=CURRENT['grant_duration_ms'];root=Path(CURRENT['rawpath']);root.mkdir(parents=True,exist_ok=True);since=int(time.time())
        if CURRENT['execute_count']==1:
            path=root/f'fec14-g{grant}-d10-smoke/progress.jsonl';lines=[json.loads(x) for x in path.read_text().splitlines()];final=lines[-1]
            assert final['phase']=='result', 'no handoff during unfinished experiment'
            CURRENT.update(transfer_verified=final['transfer_verified'],cleanup_ok=final['cleanup_ok'],restore_ok=final['restore_ok'],runner_restore_ok=final['restore_ok'],progress_path=str(path),recovered_finished_result=True)
            for phase,key in [('publish','DLduration'),('submit','ULduration')]:
                starts=[row['dispatch_start'] for row in lines if row.get('phase')==phase and row.get('event')=='dispatch_start'];ends=[row['monotonic'] for row in lines if row.get('phase')==phase and 'ok' in row]
                CURRENT[key]=max(ends)-starts[0] if starts and ends else None
            frozen=json.loads((root/'frozen-bundle.json').read_text());CURRENT['bundle_unchanged']=all(hashlib.sha256((TOOL/name).read_bytes()).hexdigest()==digest for name,digest in frozen['files'].items());assert CURRENT['bundle_unchanged']
            run_id=f'fec14-g{grant}-d10-smoke'
            after=audit(root,'after',grant,run_id=run_id,since=int(CURRENT['started_wall']))
            CURRENT['independent_restore_ok']=after['ok']
            if not after['ok'] or CURRENT['cleanup_ok'] is not True:raise RuntimeError('completed candidate handoff audit failed')
            CURRENT.pop('error',None);CURRENT['outcome']='passed' if CURRENT['transfer_verified'] and CURRENT['runner_restore_ok'] else 'runner_failed';CURRENT['finished_wall']=time.time();checkpoint('complete');continue
        assert CURRENT['execute_count']==0
        CURRENT['started_wall']=time.time();checkpoint('starting_candidate')
        configure(grant,root)
        wait_server_ready(root,grant)
        gate_result=gate(root);CURRENT['readiness_fail_nodes']=gate_result['fail_nodes']
        if gate_result['passed']:
            before=audit(root,'before',grant,since=since)
            if not before['ok']:
                CURRENT['outcome']='management_or_baseline_failure_before_execute';CURRENT['audit_failures']=before['checks'];raise RuntimeError('independent baseline audit failed; no execute')
            run_id=run_smoke(root,grant)
        else:
            CURRENT['outcome']='readiness_preflight_failed';CURRENT['cleanup_ok']=True;run_id=None
        after=audit(root,'after',grant,run_id=run_id,since=since);CURRENT['independent_restore_ok']=after['ok']
        if not gate_result['passed']:CURRENT['restore_ok']=after['ok']
        if not after['ok']:
            CURRENT['audit_failures']=after['checks'];raise RuntimeError('independent final health audit failed; matrix halted')
        if CURRENT['cleanup_ok'] is not True:raise RuntimeError('runner cleanup failure; matrix halted')
        CURRENT['outcome']=CURRENT.get('outcome') or ('passed' if CURRENT['transfer_verified'] and CURRENT['runner_restore_ok'] else 'runner_failed');CURRENT['finished_wall']=time.time();checkpoint('complete')
    CURRENT=None
    MATRIX['status']='restoring_schedule_10_10';checkpoint('restore_schedule')
    configure(10,ROOT,'final-scheduler-change')
    wait_server_ready(ROOT,10,'final-server-readiness')
    final=audit(ROOT,'final-schedule-10-10',10)
    if not final['ok']:raise RuntimeError('final server 10/10 health audit failed')
    MATRIX['status']='complete';MATRIX['final_schedule']='10/10';checkpoint('complete')
except BaseException as exc:
    MATRIX['status']='stopped';MATRIX['error']=repr(exc)
    if CURRENT is not None:CURRENT['stage']='stopped';CURRENT['error']=repr(exc)
    checkpoint();print(repr(exc),flush=True);raise
