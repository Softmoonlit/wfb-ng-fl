# 本监督器启动前已安装边界STOP和准备60秒失联中止逻辑，不热改冻结通信工具。
# 本脚本仅调度本次充电后新批次；旧资料和冻结三文件均只读。
import concurrent.futures as futures
import hashlib
import importlib.util
import json
import os
import signal
from pathlib import Path
import re
import shlex
import subprocess
import time
import urllib.request

CWD = Path('/home/fl/projects/wfb-ng-fl')
os.chdir(CWD)
ROOT = Path('/home/fl/projects/wfb-ng-fl/.scratch/fl-testbed-magazine-draft/evidence/grant-fec-nine-current-hardware-20261109T063029Z/fec14-remaining')
TOOL = CWD / '.scratch/fl-testbed-magazine-draft/communication'
NODES = [1, 2, 3, 5, 6, 7, 8, 9, 10]
HOSTS = ['fl00'] + [f'fl{n:02d}' for n in NODES]
COMMIT = '1cd6264d5d6be660998ce60f3a349063009ecfa7'
RELEASE = '/srv/wfb-ng-fl/releases/heartbeat-1cd6264d5d6b-20261108T115743Z'
SHA = '44d6bbf500f51df4d0b3ae8cd7a53c4a45cb55b09ca66cb5217e7ac75674feb2'
ROOT.mkdir(parents=True, exist_ok=True)
MATRIX=dict(schema_version=1,profile='server-fl10-repeat-swap-30-5-fec14',participants=NODES,platform_slots=list(range(1,11)),source_commit=COMMIT,release_root=RELEASE,native_sha256=SHA,schedule_order=[30],guard_interval_ms=5,status='running',max_executions=2,candidates=[],power_context='User confirmed B2 repeat swap 00/10 NICs and antennas;02/05 unchanged',pause_marker_files=['PAUSE','STOP'])
for grant in [40,60]:
    for mode in ['smoke','model']:
        MATRIX['candidates'].append(dict(grant_duration_ms=grant,guard_interval_ms=10,fec_n=14,mode=mode,stage='pending',transfer_verified=False,cleanup_ok=None,restore_ok=None,rawpath=str(ROOT/f'fec14-g{grant}-d10-{mode}'),execute_count=0))
MATRIX.update(max_executions=4, schedule_order=[40,60], guard_interval_ms=10,profile='current-hardware-fec14-remaining')
EXPECTED_MAC={'fl00': 'fc:a3:86:b3:86:72', 'fl01': 'fc:22:1c:50:0b:fe', 'fl02': 'fc:22:1c:30:0f:04', 'fl03': '5c:ff:ff:af:6d:8c', 'fl05': 'fc:22:1c:10:01:57', 'fl06': 'fc:22:1c:30:0c:bb', 'fl07': 'fc:22:1c:30:0e:99', 'fl08': 'fc:22:1c:50:0a:88', 'fl09': '88:e6:28:52:aa:e4', 'fl10': 'fc:22:1c:10:01:19'}
CURRENT = None
PAYLOAD=Path('/home/fl/projects/wfb-ng-fl/.scratch/fl-testbed-magazine-draft/models/cifar10-resnet18-seed7.pt')
MANIFEST=Path('/home/fl/projects/wfb-ng-fl/.scratch/fl-testbed-magazine-draft/evidence/model-sized-four-20261106T173222Z/model-manifest.json')
PAYLOAD_SHA='4b55cee2f0bdcfbbf610167a505efee0e2c50e3306fed02bda3613807c234e96'
BUNDLE_SHA='ab8697fe4573ac3c1088dd11022a1fd75a25cf13752d2717de02e6aa2e625e9e'

def write(path, value):
    path = Path(path)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temp.replace(path)

def checkpoint(stage=None):
    if stage and CURRENT is not None:
        CURRENT['stage'] = stage
    MATRIX['pause_requested']=any((ROOT/name).exists() for name in ['PAUSE','STOP'])
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
  dev=pathlib.Path('/sys/class/net')/interface
  usb=[q for q in [(dev/'device').resolve(),*(dev/'device').resolve().parents] if (q/'idVendor').exists()]
  records.append(dict(mac=(dev/'address').read_text().strip(),usb_speed=(usb[0]/'speed').read_text().strip(),device_path=str((dev/'device').resolve()),pid=p.parent.name,argv=argv,sha256=hashlib.sha256((p.parent/'exe').read_bytes()).hexdigest(),queries=queries,iw=subprocess.check_output(['iw','dev',interface,'info'],text=True,timeout=3)))
 except (OSError,UnicodeError):continue
source=pathlib.Path(RELEASE_ROOT)
manifest=json.loads((source/'manifest.json').read_text())
installed=pathlib.Path(manifest['installed']);identity=json.loads((installed/'build_identity.json').read_text())
power=subprocess.check_output(['nvpmodel','-q'],text=True,timeout=3)
owned=subprocess.run(['systemctl','is-active',OWNED_UNIT],capture_output=True,text=True,timeout=3).stdout.strip() if OWNED_UNIT else None
logs=subprocess.run(['journalctl','-k','--since','@'+str(SINCE),'-o','cat','--no-pager'],capture_output=True,text=True,timeout=3) if SINCE else None
limits={'/sys/devices/system/cpu/online':'0-5','/sys/devices/platform/17000000.gpu/devfreq_dev/max_freq':'612000000','/sys/kernel/nvpmodel_clk_cap/emc':'2133000000','/sys/kernel/debug/bpmp/debug/clk/emc/mrq_rate_locked':'0'}
for core in range(6):limits['/sys/devices/system/cpu/cpu'+str(core)+'/cpufreq/scaling_max_freq']='1497600'
actual_limits={p:pathlib.Path(p).read_text().strip() for p in limits}
shutdown=pathlib.Path('/run/systemd/shutdown/scheduled')
old_units=subprocess.check_output(['systemctl','list-units','--all','--no-legend','--plain','comm-smoke-*'],text=True,timeout=3)
active_old_units=[line for line in old_units.splitlines() if len(line.split())>=4 and line.split()[2] not in ['inactive','failed']]

svc=subprocess.run(['systemctl','show','nvpmodel.service','-p','Result','-p','ExecMainStatus'],capture_output=True,text=True,timeout=2)
jtop=subprocess.run(['systemctl','is-active','jtop.service'],capture_output=True,text=True,timeout=2)

import shutil,re
source_checks={name:dict(expected=digest,installed=hashlib.sha256((installed/name).read_bytes()).hexdigest(),source=hashlib.sha256((source/'source/wfb_ng/fl'/name).read_bytes()).hexdigest() if name!='build_identity.json' else digest) for name,digest in manifest['files'].items()}
tools={}
for name in ['uftp','uftpd']:
 p=pathlib.Path(shutil.which(name));blob=p.read_bytes();banner=re.search(rb'UFTP version ([0-9]+\.[0-9]+\.[0-9]+)',blob)
 ldd=subprocess.check_output(['ldd',str(p)],text=True,timeout=3)
 libs={item:hashlib.sha256(pathlib.Path(item).read_bytes()).hexdigest() for item in re.findall(r'(/[^\s()]+)',ldd)}
 tools[name]=dict(path=str(p),sha256=hashlib.sha256(blob).hexdigest(),version=banner.group(1).decode() if banner else None,ldd=ldd,libraries=libs)
print(json.dumps(dict(source_file_checks=source_checks,uftp_tools=tools,power_service=svc.stdout,jtop_state=jtop.stdout.strip(),power_limit_ok=actual_limits==limits,power_limits=actual_limits,pending_shutdown=shutdown.read_text() if shutdown.exists() else None,old_units=old_units,active_old_units=active_old_units,host=host,boot_id=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),uptime=pathlib.Path('/proc/uptime').read_text().strip(),service=service,native=records,power=power,manifest_commit=manifest['commit'],manifest_native_sha=manifest['native_sha'],installed_commit=identity['commit'],source_sha=hashlib.sha256((source/'source/wfb_v6_uplink').read_bytes()).hexdigest(),installed_native_sha=hashlib.sha256(pathlib.Path('/usr/bin/wfb_v6_uplink').read_bytes()).hexdigest(),owned_unit_state=owned,kernel_log=logs.stdout if logs else '',kernel_error=logs.stderr if logs else '',monotonic=time.monotonic())))
'''

def audit(root, label, grant, guard, run_id=None, since=None):
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
            if len(data['source_file_checks'])!=40:errors.append('expected 40 source files')
            if any(x['expected']!=x['installed'] or x['expected']!=x['source'] for x in data['source_file_checks'].values()):errors.append('source file hash mismatch')
            for name,digest in {'uftp':'71d9c15497b3bd4286a2c60c9d37871a50b7032793eae8d4bf7b3b8977e015e3','uftpd':'0d7a31c6cbf783ad5ecced0bdce6705c30530a258fdf605d555c748827aa85b9'}.items():
                tool=data['uftp_tools'][name]
                if tool['sha256']!=digest or tool['version']!='5.0.3' or not tool['libraries']:errors.append('UFTP tool/library identity mismatch')
            if 'ActiveState=active' not in data['service']:errors.append('resident service inactive')
            if any(data[key] != expected for key,expected in [('manifest_commit',COMMIT),('installed_commit',COMMIT),('manifest_native_sha',SHA),('source_sha',SHA),('installed_native_sha',SHA)]):errors.append('source/native identity mismatch')
            if data['power'].strip() != 'NV Power Mode: 15W\n0':errors.append('power mismatch')
            if not data['power_limit_ok']:errors.append('actual power limits mismatch')
            if 'Result=success' not in data['power_service'] or 'ExecMainStatus=0' not in data['power_service']:errors.append('nvpmodel service failure')
            if data['jtop_state']!='inactive':errors.append('jtop active')
            if data['pending_shutdown']:errors.append('pending shutdown scheduled')
            if data['active_old_units'] and not run_id:errors.append('old active test units')
            if len(natives)!=1:errors.append('native count mismatch')
            else:
                native=natives[0];a=native['argv'];radio=native['queries']['radio'];fec=native['queries']['fec'];iw=native['iw']
                if a[a.index('--radio-mcs-index')+1]!='18':errors.append('native radio index mismatch')
                if native['mac']!=EXPECTED_MAC[row['host']]:errors.append('fixed hardware MAC mismatch')
                if row['host']=='fl00' and native['usb_speed']!='480':errors.append('server USB speed changed')
                if native['sha256']!=SHA:errors.append('proc exe SHA mismatch')
                if radio!=[0x434f4d4d,0,0,0,0,40,3 if row['host']=='fl00' else 4,0,1]:errors.append('GET radio baseline mismatch')
                if fec!=[0x434f4d4d,0,8,14]:errors.append('GET FEC mismatch')
                if not all(text in iw for text in ['channel 149 ','width: 40 MHz','txpower -18.00 dBm','type monitor']):errors.append('iw baseline mismatch')
                if row['host']=='fl00':
                    if a[a.index('--grant-duration-ms')+1]!=str(grant) or a[a.index('--guard-interval-ms')+1]!=str(guard):errors.append('schedule mismatch')
                    slots=[int(x) for x in a[a.index('--known-clients')+1].split(',')]
                    targets=[int(a[i+1].split(':')[0]) for i,x in enumerate(a) if x=='--client-target']
                    if slots!=targets or slots!=list(range(1,11)):errors.append('platform slot pool mismatch')
            if run_id and data['owned_unit_state'] not in ['inactive','failed','unknown']:errors.append('owned unit active')
            if re.search(r'(?i)(reset\s+(?:high|full|super|low)[^\n]*USB|USB[^\n]*reset)',data['kernel_log']):errors.append('USB reset observed')
        details.append(dict(host=row['host'],ok=not errors,errors=errors))
    result=dict(ok=all(x['ok'] for x in details), elapsed=time.monotonic()-begin, checks=details,fl04='excluded; not contacted per current authorization',rows=rows)
    write(root/(label+'-audit.json'),result)
    return result

def configure(grant, guard, root, label='scheduler-change'):
    checkpoint('configure_' + str(grant))
    code='''import pathlib,json,subprocess,os,time
path=pathlib.Path('/etc/wfb-ng-fl/server.json');before=json.loads(path.read_text());after=dict(before,grant_duration_ms=GRANT,guard_interval_ms=GUARD)
changed=before!=after
old=subprocess.check_output(['systemctl','show','wfb-fl-server-daemon.service','-p','MainPID','--value'],text=True).strip()
if changed:
 temp=path.with_name('server.json.grant-fec-tmp');temp.write_text(json.dumps(after,indent=2)+'\\n');os.chmod(temp,path.stat().st_mode);os.chown(temp,path.stat().st_uid,path.stat().st_gid);os.replace(temp,path)
 subprocess.run(['systemctl','restart','wfb-fl-server-daemon.service'],check=True,timeout=15)
new=subprocess.check_output(['systemctl','show','wfb-fl-server-daemon.service','-p','MainPID','--value'],text=True).strip()
print(json.dumps(dict(before=before,after=after,changed=changed,old_pid=old,new_pid=new,monotonic=time.monotonic())))
'''.replace('GRANT',str(grant)).replace('GUARD',str(guard))
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

def run_transfer(root,grant,guard,mode):
    spec=importlib.util.spec_from_file_location('matrix_comm_runner',TOOL/'runner.py');runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
    assert runner.NODES==NODES and runner.TIMING['io_timeout']==600 and runner.TIMING['orchestration_timeout']==1290
    bundle=runner.bundle_sha();assert bundle==BUNDLE_SHA;hashes={name:hashlib.sha256((TOOL/name).read_bytes()).hexdigest() for name in ['runner.py','node_agent.py','recover.py']}
    CURRENT['bundleSHA']=bundle
    write(root/'frozen-bundle.json',dict(bundleSHA=bundle,files=hashes,source_commit=COMMIT,nativeSHA=SHA))
    run_id=f'gf20261109T063029Z-g{grant}-d{guard}-{mode}'
    payload_args=['--smoke'] if mode=='smoke' else ['--payload',str(PAYLOAD),'--model-manifest',str(MANIFEST)]
    command=['python3',str(TOOL/'runner.py'),*payload_args,'--native-sha',SHA,'--release-commit',COMMIT,'--release-root',RELEASE,'--grant-duration-ms',str(grant),'--guard-interval-ms',str(guard),'--channel','157','--bandwidth-mhz','40','--downlink-mcs','3','--uplink-mcs','4','--fec-n','14','--uftp-rate-kbps','12000','--run-id',run_id,'--output',str(root),'--execute','--reviewed-sha',bundle]
    write(root/'command.json',command)
    CURRENT['execute_count']=1
    checkpoint('execute')
    began=time.monotonic()
    monitor=(root/'live-status-events.jsonl').open('x',buffering=1)
    previous_states={}
    prepare_probe_after=0
    prepare_abort_signaled=False
    with (root/'runner.log').open('xb') as output:
        proc=subprocess.Popen(command,stdout=output,stderr=subprocess.STDOUT)
        write(root/'runner-process.json',dict(pid=proc.pid,started_monotonic=began))
        path=root/run_id/'progress.jsonl'
        while proc.poll() is None:
            if path.exists():
                try:
                    progress=[json.loads(line) for line in path.read_text().splitlines()]
                    last=progress[-1];CURRENT['runner_last_phase']=last['phase'];CURRENT['runner_last_monotonic']=last.get('monotonic')
                    starts=[x for x in progress if x.get('event')=='dispatch_start']
                    CURRENT['currentphase']=starts[-1]['phase'] if starts else last['phase']
                    uploads=[x for x in progress if x['phase']=='upload_progress']
                    if uploads:
                        CURRENT['client_upload_progress']=uploads[-1]
                    completed={}
                    for phase in ['publish','submit']:
                        completed[phase]=[x.get('host') for x in progress if x['phase']==phase and x.get('ok') is True]
                    CURRENT['completed_hosts']=completed
                except (ValueError,IndexError):pass
            # 启动前安装的准备阶段监督：单端60秒未回应且管理失联，只中止本轮runner。
            if not prepare_abort_signaled and path.exists():
                try:
                    live=[json.loads(line) for line in path.read_text().splitlines()]
                    starts=[row for row in live if row.get('event')=='dispatch_start']
                    prep=[row for row in starts if row['phase']=='prepare']
                    pending=[host for host in HOSTS if not any(row.get('phase')=='prepare' and row.get('host')==host and row.get('ok') is True for row in live)]
                    now=time.monotonic()
                    if prep and starts[-1]['phase']=='prepare' and pending and now-prep[0]['dispatch_start']>=60 and now>=prepare_probe_after:
                        def ping_management(host):
                            command=['systemctl','show','comm-smoke-'+run_id+'-'+host+'.service','-p','MainPID','-p','ActiveState','-p','SubState']
                            argv=command if host=='fl00' else ['ssh','-T','-oBatchMode=yes','-oConnectTimeout=4',host,shlex.join(command)]
                            try:
                                p=subprocess.run(argv,capture_output=True,text=True,timeout=5)
                                return dict(host=host,returncode=p.returncode,stdout=p.stdout,stderr=p.stderr)
                            except Exception as exc:return dict(host=host,error=repr(exc))
                        with futures.ThreadPoolExecutor(max_workers=len(pending)) as probes:
                            management=list(probes.map(ping_management,pending))
                        event=dict(wall=time.time(),monotonic=time.monotonic(),prepare_elapsed=time.monotonic()-prep[0]['dispatch_start'],pending_hosts=pending,management=management)
                        monitor.write(json.dumps(dict(prepare_watchdog=event))+'\n')
                        CURRENT['prepare_watchdog']=event
                        failed=[probe['host'] for probe in management if probe.get('returncode')!=0]
                        if failed:
                            write(ROOT/'STOP',dict(reason='prepare watchdog: single node no prepare response>=60s and management lost',hosts=failed,run_id=run_id,wall=time.time()))
                            refreshed=[json.loads(line) for line in path.read_text().splitlines()]
                            if not any(row.get('phase')=='acquire_radio' for row in refreshed) and proc.poll() is None:
                                cmdline=Path('/proc',str(proc.pid),'cmdline').read_bytes().decode().split(chr(0))
                                assert str(TOOL/'runner.py') in cmdline and run_id in cmdline
                                proc.send_signal(signal.SIGINT)
                                prepare_abort_signaled=True
                                CURRENT['controlled_prepare_abort']=dict(pid=proc.pid,signal='SIGINT',failed_hosts=failed,radio_attempted=False,payload_dispatched=False,wall=time.time())
                                write(root/'controlled-prepare-abort.json',CURRENT['controlled_prepare_abort'])
                        prepare_probe_after=time.monotonic()+15
                except (ValueError,IndexError,FileNotFoundError) as exc:
                    monitor.write(json.dumps(dict(prepare_watchdog_error=repr(exc),wall=time.time()))+'\n')
            try:
                with urllib.request.urlopen('http://127.0.0.1:9090/api/v1/status',timeout=1) as response: observation=json.load(response)
                states={str(n):dict(readiness=observation.get('nodes',{}).get(str(n),{}).get('readiness'),reported_state=observation.get('nodes',{}).get(str(n),{}).get('reported_state'),channel=observation.get('nodes',{}).get(str(n),{}).get('current_channel')) for n in NODES}
                events=[dict(node=n,**value) for n,value in states.items() if 'HUNT' in str(value.get('reported_state')) and previous_states.get(n)!=value]
                monitor.write(json.dumps(dict(wall=time.time(),monotonic=time.monotonic(),states=states,new_hunt_events=events))+'\n')
                CURRENT['latest_registry_states']=states
                if events:CURRENT.setdefault('hunt_events',[]).extend([dict(wall=time.time(),**event) for event in events])
                previous_states=states
            except Exception as exc:
                monitor.write(json.dumps(dict(wall=time.time(),monotonic=time.monotonic(),status_error=repr(exc)))+'\n')
                CURRENT['latest_status_probe_error']=repr(exc)
            checkpoint('execute')
            if time.monotonic()-began>1365:
                write(ROOT/'STOP',dict(reason='runner supervision boundary',run_id=run_id))
                if proc.poll() is None:proc.send_signal(signal.SIGINT)
                CURRENT['controlled_timeout_sigint']=True
            time.sleep(5)
    monitor.close()
    lines=[json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    final=next((row for row in reversed(lines) if row.get('phase')=='result'),{})
    CURRENT.update(runner_returncode=proc.returncode,transfer_verified=final.get('transfer_verified',False),cleanup_ok=final.get('cleanup_ok',False),restore_ok=final.get('restore_ok',False),runner_restore_ok=final.get('restore_ok',False),progress_path=str(path),elapsed=time.monotonic()-began)
    for phase,key in [('publish','DLduration'),('submit','ULduration')]:
        starts=[row['dispatch_start'] for row in lines if row.get('phase')==phase and row.get('event')=='dispatch_start']
        completions=[row['monotonic'] for row in lines if row.get('phase')==phase and 'ok' in row]
        CURRENT[key]=max(completions)-starts[0] if starts and completions else None
    CURRENT['failures']=[row for row in lines if row.get('ok') is False]
    CURRENT['original_runner_final_flags']={key:final.get(key) for key in ['transfer_verified','cleanup_ok','restore_ok']}
    CURRENT['server_verified_files']=next((row['result']['files'] for row in lines if row.get('phase')=='verify' and row.get('host')=='fl00' and row.get('ok') is True),{})
    CURRENT['phase_completion_seconds']={}
    for phase in ['publish','submit']:
        starts=[row['dispatch_start'] for row in lines if row.get('phase')==phase and row.get('event')=='dispatch_start']
        CURRENT['phase_completion_seconds'][phase]={row['host']:row['monotonic']-starts[0] for row in lines if starts and row.get('phase')==phase and row.get('ok') is True and 'host' in row}
    CURRENT['bundle_unchanged']=all(hashlib.sha256((TOOL/name).read_bytes()).hexdigest()==digest for name,digest in hashes.items())
    write(root/'run-summary.json',dict(candidate=CURRENT,result=final))
    if not CURRENT['bundle_unchanged']:raise RuntimeError('tool bundle changed during execution')
    return run_id


def wait_server_ready(root, grant, guard, label='server-readiness'):
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
                ready=(current.get('server_state')=='IDLE' and current.get('active_job') is None and n['sha256']==SHA and a[a.index('--grant-duration-ms')+1]==str(grant) and a[a.index('--guard-interval-ms')+1]==str(guard) and a[a.index('--fec-k')+1]=='8' and a[a.index('--fec-n')+1]=='14')
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



