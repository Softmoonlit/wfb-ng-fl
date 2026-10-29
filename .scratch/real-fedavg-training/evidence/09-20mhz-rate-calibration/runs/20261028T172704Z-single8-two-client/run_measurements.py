#!/usr/bin/env python3
"""工单09四板单点或24次矩阵测量。仅调用已部署03 IPC和独立1144 UFTP，不部署/停服务。"""
import concurrent.futures as cf
import argparse, shutil
import datetime, hashlib, json, math, os, pathlib, re, shlex, subprocess, sys, time, urllib.request, urllib.error
ROOT=pathlib.Path(__file__).resolve().parent
HOSTS=['fl00','fl01','fl02']
NODES={'fl01':1,'fl02':2}
TARGET_NODES=[1,2]
OPENER=urllib.request.build_opener(urllib.request.ProxyHandler({}))

def save(p,v):
    p=pathlib.Path(p);tmp=p.with_name(p.name+'.tmp')
    with tmp.open('w') as f:
        f.write(json.dumps(v,ensure_ascii=False,indent=2)+'\n');f.flush();os.fsync(f.fileno())
    os.replace(tmp,p)
    fd=os.open(str(p.parent),os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
def event(msg):
    print(datetime.datetime.now(datetime.timezone.utc).isoformat(),msg,flush=True)
def remote(host,code,args=(),timeout=25):
    argv=['sudo','-n','python3','-B','-',*map(str,args)]
    cmd=argv if host=='fl00' else ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=8',host,shlex.join(argv)]
    p=subprocess.run(cmd,input=code.encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
    if p.returncode:raise RuntimeError(f'{host}: rc={p.returncode}: {p.stderr.decode()[-2000:]} {p.stdout.decode()[-1000:]}')
    return p.stdout

def api(path,body=None):
    t=time.monotonic()
    req=urllib.request.Request('http://127.0.0.1:9090/api/v1/'+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
    try:
        with OPENER.open(req,timeout=45) as resp:r={'http':resp.status,'body':json.loads(resp.read())}
    except urllib.error.HTTPError as e:r={'http':e.code,'body':json.loads(e.read())}
    r['elapsed_seconds']=time.monotonic()-t
    return r

def allhosts(code,args=()):
    with cf.ThreadPoolExecutor(4) as ex:
        fut={ex.submit(remote,h,code,args):h for h in HOSTS}
        return {fut[f]:json.loads(f.result()) for f in cf.as_completed(fut)}

SNAP=r'''
import os,sys,json,pathlib,subprocess,time,re
os.chdir('/tmp'); r={'utc':time.time(),'monotonic':time.monotonic()}
def cmd(a):
 p=subprocess.run(a,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=10);return {'rc':p.returncode,'out':p.stdout}
r['iw_all']=cmd(['iw','dev']);r['ip']=cmd(['ip','-j','address']);r['sockets']=cmd(['ss','-lunp']);r['processes']=cmd(['ps','-eo','pid,etimes,pcpu,pmem,args','--sort=-pcpu'])
r['loadavg']=pathlib.Path('/proc/loadavg').read_text();r['cpu']=pathlib.Path('/proc/stat').read_text();r['disk']=pathlib.Path('/proc/diskstats').read_text();r['links']=[]
for p in pathlib.Path('/proc').glob('[0-9]*'):
 try:
  argv=[x.decode() for x in (p/'cmdline').read_bytes().split(b'\0') if x]
  if argv and argv[0]=='/usr/bin/wfb_v6_uplink':
   interfaces=[argv[i+1] for i,x in enumerate(argv[:-1]) if x=='--air-interface']
   interfaces.extend(x.split('=',1)[1] for x in argv if x.startswith('--air-interface='))
   if len(interfaces)!=1:raise RuntimeError('native must specify one air interface')
   interface=interfaces[0]
   if not re.fullmatch(r'[A-Za-z0-9_.-]+',interface):raise RuntimeError('invalid air interface')
   log=os.readlink(p/'fd/1');offset=os.stat(log).st_size;latest=None
   with open(log,'rb') as f:
    while f.tell()<offset:
     line_start=f.tell();line=f.readline(offset-line_start).decode(errors='replace')
     if not line.endswith('\n'):
      offset=line_start;break
     match=re.search(r'v6_radio_update generation=(\d+) bandwidth=(\d+) data_mcs_index=(\d+) control_mcs_index=(\d+)',line)
     if match:latest=dict(zip(('generation','bandwidth','data_mcs_index','control_mcs_index'),map(int,match.groups())),line=line.rstrip())
   if latest is None:
    try:
     bandwidth=argv[argv.index('--radio-bandwidth')+1]
     data_mcs=argv[argv.index('--radio-mcs-index')+1]
     latest={'generation':0,'bandwidth':int(bandwidth),'data_mcs_index':int(data_mcs),'control_mcs_index':0,
             'line':'startup argv: radio-bandwidth='+bandwidth+' radio-mcs-index='+data_mcs+' control_mcs_index=0'}
    except (ValueError,IndexError):
     pass
   r['links'].append({'pid':int(p.name),'args':' '.join(argv),'argv':argv,'air_interface':interface,'start_ticks':(p/'stat').read_text().rsplit(')',1)[1].split()[19],'log':log,'offset':offset,'runtime_radio':latest})
 except (OSError,UnicodeError):pass
if len(r['links'])!=1:raise RuntimeError('expected exactly one native for iw snapshot')
r['air_interface']=r['links'][0]['air_interface'];r['iw']=cmd(['iw','dev',r['air_interface'],'info'])
r['tun']=[x for x in json.loads(r['ip']['out']) if x['ifname'].startswith('fl-')]
print(json.dumps(r))
'''

START_RX=r'''
import sys,pathlib,subprocess,json,time,os
root=pathlib.Path(sys.argv[1]);nid=int(sys.argv[2]);root.mkdir(parents=True,exist_ok=False)
(root/'received').mkdir();(root/'tmp').mkdir()
a=['uftpd','-d','-p','1144','-U',f'0x{nid:08x}','-D',str(root/'received'),'-T',str(root/'tmp'),'-I',f'fl-c{nid}','-M','239.255.42.1','-L',str(root/'uftpd.log'),'-F',str(root/'uftpd.status')]
with (root/'receiver-console.log').open('wb') as out:p=subprocess.Popen(a,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
time.sleep(.35)
r={'pid':p.pid,'argv':a,'poll':p.poll()}
if p.poll() is None:r['start_ticks']=pathlib.Path(f'/proc/{p.pid}/stat').read_text().rsplit(')',1)[1].split()[19]
with (root/'receiver.json').open('w') as f:
 f.write(json.dumps(r));f.flush();os.fsync(f.fileno())
print(json.dumps(r))
'''

FINISH_RX=r'''
import sys,pathlib,subprocess,json,time,hashlib,os,signal
root=pathlib.Path(sys.argv[1]);r={};p=root/'received'/sys.argv[2]
t=time.monotonic()
try:
 if p.exists():
  h=hashlib.sha256()
  with p.open('rb') as f:
   for block in iter(lambda:f.read(1048576),b''):h.update(block)
  r.update(bytes=p.stat().st_size,sha256=h.hexdigest())
 else:r.update(bytes=None,sha256=None,error='final_file_missing')
except Exception as e:r.update(bytes=None,sha256=None,error=repr(e))
r['hash_seconds']=time.monotonic()-t;r['received_files']=[str(x.relative_to(root)) for x in root.rglob('*') if x.is_file()]
info=json.loads((root/'receiver.json').read_text());pid=info['pid'];proc=pathlib.Path(f'/proc/{pid}')
if proc.exists():
 stat=(proc/'stat').read_text().rsplit(')',1)[1].split();a=(proc/'cmdline').read_bytes()
 if stat[19]!=info.get('start_ticks') or b'uftpd' not in a or str(root).encode() not in a:raise RuntimeError('PID identity mismatch')
 os.kill(pid,signal.SIGTERM)
 for _ in range(30):
  if not proc.exists() or (proc/'stat').read_text().rsplit(')',1)[1].split()[0]=='Z':break
  time.sleep(.1)
 r['receiver_stopped']=not proc.exists() or (proc/'stat').read_text().rsplit(')',1)[1].split()[0]=='Z'
else:r['receiver_stopped']=True
for name in ['uftpd.log','uftpd.status','receiver-console.log','receiver.json']:
 q=root/name;r[name]=q.read_text(errors='replace') if q.exists() else None
print(json.dumps(r))
'''

GET_LOG=r'''
import sys,pathlib,json
p=pathlib.Path(sys.argv[1]);off=int(sys.argv[2]);size=p.stat().st_size
if size<off:raise RuntimeError('log truncated')
with p.open('rb') as f:f.seek(off);b=f.read()
print(json.dumps({'path':str(p),'offset':off,'end':size,'text':b.decode(errors='replace')}))
'''

def ready():
    r=api('status');s=r['body']
    online=sorted(int(k) for k,v in s.get('nodes',{}).items() if v['readiness']!='OFFLINE')
    ok=r['http']==200 and s.get('server_state')=='IDLE' and s.get('active_job') is None and online==TARGET_NODES and all(s['nodes'][str(n)]['readiness']=='READY' and s['nodes'][str(n)]['reported_state']=='IDLE' for n in TARGET_NODES)
    return ok,r

def wait_ready(path, checks=12):
    history=[]
    for _ in range(checks):
        ok,r=ready();history.append(r);save(path,history)
        if ok:return r
        time.sleep(2)
    raise RuntimeError('all-target IDLE/READY gate failed')

def assert_identity(before,after):
    for h in HOSTS:
        a,b=before[h],after[h]
        if [(x['pid'],x['start_ticks'])for x in a['links']] != [(x['pid'],x['start_ticks'])for x in b['links']]:raise RuntimeError(h+' link PID changed')
        if a['air_interface']!=b['air_interface']:raise RuntimeError(h+' air interface changed')
        if [(x['ifname'],x['ifindex'])for x in a['tun']] != [(x['ifname'],x['ifindex'])for x in b['tun']]:raise RuntimeError(h+' TUN changed')

def assert_no_pytest():
    active=[]
    for p in pathlib.Path('/proc').glob('[0-9]*'):
        try:
            args=(p/'cmdline').read_bytes().split(b'\0')
            if len(args)>2 and args[1:3]==[b'-m',b'pytest']:active.append({'pid':int(p.name),'args':[a.decode(errors='replace') for a in args if a]})
        except OSError:pass
    if active:raise RuntimeError('concurrent pytest: '+json.dumps(active))

FROZEN_NORMAL={'channel':149,'bandwidth_mhz':40,'channel_width':'HT40+','radio_txpower_dbm':14,'downlink_mcs':3,'uplink_mcs':4,'uftp_rate_kbps':15000}

PATCH_KEYS=('channel','bandwidth_mhz','radio_txpower_dbm','downlink_mcs','uplink_mcs','uftp_rate_kbps')

def iw_radio(snapshot):
    out={}
    for h,v in snapshot.items():
        rows=re.findall(r'channel\s+(\d+)\s+\((\d+) MHz\),\s+width:\s+(\d+) MHz,\s+center1:\s+(\d+) MHz',v['iw']['out'])
        power=re.findall(r'txpower\s+(-?\d+(?:\.\d+)?)\s+dBm',v['iw']['out'])
        if v['iw']['rc'] or len(rows)!=1 or len(power)!=1:raise RuntimeError(h+' ambiguous iw radio')
        channel,freq,width,center=map(int,rows[0])
        out[h]={'air_interface':v['air_interface'],'channel':channel,'frequency':freq,'width':width,'center':center,'txpower':power[0]}
    return out

def verify_radio(snapshot,config,original=False):
    actual=iw_radio(snapshot)
    for h,v in actual.items():
        base=ORIGINAL_IW[h]
        expected=base if original else dict(base,width=20,center=base['frequency'])
        if v!=expected or v['channel']!=config['channel'] or v['width']!=config['bandwidth_mhz']:
            raise RuntimeError(h+' actual iw radio mismatch: '+repr(v))
    return actual

def record_attempt(d,rec):
    save(d/'result.json',rec)
    with (RUN/'attempts.jsonl').open('a') as f:
        f.write(json.dumps(rec,ensure_ascii=False)+'\n');f.flush();os.fsync(f.fileno())

def measure(index,mcs,rate,mib):
    name=f'{index:02d}-mcs{mcs}-r{rate}-{mib}mib';d=RUN/name;d.mkdir(exist_ok=False)
    rec={'attempt':name,'mcs':mcs,'rate_kbps':rate,'mib':mib,'target_nodes':TARGET_NODES,
         'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'success':False,'phase':'started'}
    record_attempt(d,rec);event('START '+name)
    receivers={};before=None;logs={}
    expected=dict(ORIGINAL,bandwidth_mhz=20,channel_width='HT20',downlink_mcs=mcs,uftp_rate_kbps=rate)
    try:
        assert_no_pytest();wait_ready(d/'admission.json')
        before=allhosts(SNAP);save(d/'before.json',before);assert_identity(BASELINE,before)
        for h in HOSTS:
            if ':1144 ' in before[h]['sockets']['out']:raise RuntimeError(h+' 1144 occupied')
        request={'confirmed':True,'target_nodes':TARGET_NODES,'patch':{k:expected[k] for k in PATCH_KEYS}}
        save(d/'calibrate-request.json',request)
        wait_ready(d/'immediate-admission.json')
        r=api('radio/calibrate',request);save(d/'calibrate-response.json',r)
        if r['http']!=200 or r['body'].get('status')!='finalized' or r['body'].get('calibration') is not True:
            raise RuntimeError('calibration not finalized')
        if r['body'].get('effective_config')!=expected:raise RuntimeError('effective config mismatch')
        status=api('status');save(d/'applied-status.json',status)
        if status['http']!=200 or status['body'].get('radio')!=expected:raise RuntimeError('actual status radio mismatch')
        applied=allhosts(SNAP);save(d/'applied.json',applied);assert_identity(BASELINE,applied)
        rec['actual_radio']=verify_radio(applied,expected)
        with cf.ThreadPoolExecutor(3) as ex:
            futures={ex.submit(remote,h,START_RX,[REMOTE+'/'+name,NODES[h]]):h for h in NODES}
            errors=[]
            for f in cf.as_completed(futures):
                try:receivers[futures[f]]=json.loads(f.result())
                except Exception as e:errors.append(repr(e))
            if errors:raise RuntimeError(str(errors))
        save(d/'receivers.json',receivers)
        if len(receivers)!=3 or any(v['poll'] is not None for v in receivers.values()):raise RuntimeError('receiver failed')
        path=SOURCE[mib]['path'];budget=max(180,math.ceil(3*mib*1048576*8/(rate*1000)+60))
        args=['sudo','-n','timeout','--signal=TERM','--kill-after=10s',str(budget),'uftp','-Y','none','-p','1144','-R',str(rate),'-I','fl-s','-M','239.255.42.1','-P','239.255.42.2','-H','0x00000001,0x00000002,0x00000004','-U','0x000000ff','-r','0.5:0.1:2.0','-s','20','-L',str(d/'uftp.log'),'-S',str(d/'uftp.status'),path]
        save(d/'sender-command.json',args);t=time.monotonic()
        with (d/'sender-console.log').open('wb') as out:p=subprocess.run(args,stdout=out,stderr=subprocess.STDOUT,timeout=budget+20)
        rec.update(sender_rc=p.returncode,sender_wall_seconds=time.monotonic()-t,timeout_seconds=budget,source=SOURCE[mib])
    except BaseException as e:
        rec['error']=repr(e)
    finally:
        try:
            # 清理失败单独记录，不覆盖测量错误。
            for h in NODES:
                try:
                    data=json.loads(remote(h,FINISH_RX,[REMOTE+'/'+name,pathlib.Path(SOURCE[mib]['path']).name]))
                    save(d/(h+'-receiver.json'),data)
                    rec.setdefault('received',{})[h]={k:v for k,v in data.items() if k in ['bytes','sha256','hash_seconds','receiver_stopped','error']}
                    for n in ['uftpd.log','uftpd.status','receiver-console.log']:
                        if data.get(n) is not None:(d/(h+'-'+n)).write_text(data[n])
                except Exception as e:
                    if h in receivers:rec.setdefault('cleanup_errors',{})[h]=repr(e)
                    else:rec.setdefault('receiver_probe_errors',{})[h]=repr(e)
            try:
                after=allhosts(SNAP);save(d/'after.json',after);assert_identity(BASELINE,after)
                rec['pid_tun_unchanged']=True
                rec['endpoint_radio']=verify_radio(after,expected)
                endpoint=api('status');save(d/'endpoint-status.json',endpoint)
                if endpoint['http']!=200 or endpoint['body'].get('radio')!=expected:raise RuntimeError('endpoint status radio mismatch')
                rec['endpoint_status_matches']=True
                if any(':1144 ' in v['sockets']['out'] for v in after.values()):raise RuntimeError('1144 cleanup incomplete')
                rec['cleanup_complete']=True
            except Exception as e:rec['endpoint_error']=repr(e)
            if before:
                for h in HOSTS:
                    try:
                        link=before[h]['links'][0];log=json.loads(remote(h,GET_LOG,[link['log'],link['offset']]))
                        logs[h]=log.pop('text');(d/(h+'-link.log')).write_text(logs[h]);save(d/(h+'-link-range.json'),log)
                    except Exception as e:rec.setdefault('log_errors',{})[h]=repr(e)
            try:
                rec['rx_ant_targets']={}
                rec['server255_evidence']={}
                for h in NODES:
                    text=logs.get(h,'');freq=ORIGINAL_IW[h]['frequency']
                    sources=[line for line in text.splitlines() if re.search(r'\tPKT_SRC\t255:[1-9]\d*:',line)]
                    rec['server255_evidence'][h]=sources
                    epochs={line.split('\t')[0] for line in sources}
                    antennas=[line for line in text.splitlines() if re.search(r'\tRX_ANT\t'+f'{freq}:{mcs}:20'+r'\t',line) and line.split('\t')[0] in epochs]
                    rec.setdefault('rx_ant_server255_lines',{})[h]=antennas
                    rec['rx_ant_targets'][h]=bool(antennas)
                rec['tx_application_lines']={h:[x for x in logs.get(h,'').splitlines() if 'v6_radio_update' in x] for h in HOSTS}
                updates={h:[tuple(map(int,u)) for u in re.findall(r'v6_radio_update generation=(\d+) bandwidth=(\d+) data_mcs_index=(\d+) control_mcs_index=(\d+)',logs.get(h,''))] for h in HOSTS}
                rec['radio_updates']=updates;rec['tx_mcs_audit']={}
                for h in HOSTS:
                    target=(20,mcs if h=='fl00' else ORIGINAL['uplink_mcs'],0)
                    previous=before[h]['links'][0]['runtime_radio'] if before else None
                    current=after[h]['links'][0]['runtime_radio'] if rec.get('pid_tun_unchanged') else None
                    def config_tuple(v):return (v['bandwidth'],v['data_mcs_index'],v['control_mcs_index']) if v else None
                    required=index==1 or config_tuple(previous)!=target
                    window=updates[h]
                    generation_ok=bool(current) and (not previous or current['generation']>=previous['generation'])
                    if window:
                        generation_ok=generation_ok and all(b[0]>a[0] for a,b in zip(window,window[1:])) and (not previous or window[0][0]>previous['generation']) and current['generation']==window[-1][0]
                    elif previous:
                        generation_ok=generation_ok and current['generation']==previous['generation']
                    passed=bool(current) and config_tuple(current)==target and generation_ok and (bool(window) if required else config_tuple(previous)==target) and all(u[1:]==target for u in window)
                    rec['tx_mcs_audit'][h]={'before':previous,'endpoint':current,'requires_window_update':required,'passed':passed}
                rec['tx_mcs_stable']=all(rec['tx_mcs_audit'][h]['passed'] for h in HOSTS)
                sender=(d/'uftp.log').read_text(errors='replace') if (d/'uftp.log').exists() else ''
                group=sender.split('Finishing group')[-1] if 'Finishing group' in sender else ''
                rec['group_complete']='Group complete' in group and all('Got COMPLETE from client '+f'0x{n:08X}' in group for n in TARGET_NODES)
                rec['nak_lines']=[x for x in sender.splitlines() if 'NAK' in x]
                rec['pass_lines']=[x for x in sender.splitlines() if 'Starting pass' in x]
                received=rec.get('received',{})
                rec['all_hashes_match']=len(received)==3 and all(v.get('sha256')==SOURCE[mib]['sha256'] and v.get('bytes')==mib*1048576 and v.get('receiver_stopped') for v in received.values())
                rec['success']=not any(rec.get(k) for k in ('error','cleanup_errors','receiver_probe_errors','endpoint_error','log_errors')) and rec.get('sender_rc')==0 and all(rec.get(k) for k in ('actual_radio','endpoint_radio','endpoint_status_matches','cleanup_complete','group_complete','all_hashes_match','pid_tun_unchanged','tx_mcs_stable')) and all(rec['rx_ant_targets'].values())
                rec['goodput_mbps']=mib*1048576*8/rec['sender_wall_seconds']/1e6 if rec['success'] else None
            except BaseException as e:rec['evaluation_error']=repr(e);rec['success']=False
        except BaseException as e:
            rec['finalization_error']=repr(e);rec['success']=False
        finally:
            rec['phase']='finished';rec['ended_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
            record_attempt(d,rec)
        event('END '+name+' success='+str(rec['success']))
    if not rec['success']:raise RuntimeError('attempt failed: '+name)
    return rec

PREFLIGHT=r'''
import os,sys,json,pathlib,hashlib,subprocess
os.chdir('/tmp');import wfb_ng
host,commit,native_sha=sys.argv[1:]
def sha(p):return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
def tick(p):return (p/'stat').read_text().rsplit(')',1)[1].split()[19]
def require(ok,msg):
 if not ok:raise RuntimeError(msg)
package=pathlib.Path(wfb_ng.__file__).parent
identity=json.loads((package/'fl/build_identity.json').read_text())
require(identity=={'schema_version':1,'commit':commit},'installed identity mismatch')
unit='wfb-fl-server-daemon.service' if host=='fl00' else 'wfb-fl-client-daemon.service'
pid=int(subprocess.check_output(['systemctl','show',unit,'--property=MainPID','--value']))
proc=pathlib.Path('/proc')/str(pid);started=tick(proc)
argv=[a.decode() for a in (proc/'cmdline').read_bytes().split(b'\0') if a]
require(len(argv)>=9 and argv[:2]==['/usr/bin/python3','-B'] and argv[3]=='daemon' and argv[4]==commit and argv[5]==host and argv[7]=='--','protected wrapper argv mismatch')
root=pathlib.Path(argv[6]);require(argv[2]==str(root/'release-node.py'),'wrapper path mismatch')
loaded=json.loads((root/'python-loaded.json').read_text())
require(loaded['pid']==pid and loaded['tick']==started and loaded['host']==host and loaded['expectedCommit']==commit,'loaded manifest identity mismatch')
require(loaded['argv']==argv[8:]==json.loads((root/'expected-args.json').read_text()),'loaded argv mismatch')
require(loaded['cwd']==os.readlink(proc/'cwd')=='/' and loaded['interpreter_exe']==os.readlink(proc/'exe')==os.path.realpath('/usr/bin/python3'),'loaded interpreter/cwd mismatch')
python=loaded['pythonLoaded'];expected=json.loads((root/'expected-python.json').read_text())
installed={'__init__.py':sha(package/'__init__.py')}
installed.update({str(p.relative_to(package)):sha(p) for p in sorted((package/'fl').rglob('*')) if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'})
require(python['identity']==identity and python['installed']==expected==installed,'loaded/installed Python manifest mismatch')
require(python['files'],'empty loaded files')
for entry in python['files'].values():
 require(expected.get(entry['relative'])==entry['sha256']==sha(entry['path']),'loaded module SHA mismatch')
natives=[]
for p in pathlib.Path('/proc').glob('[0-9]*'):
 try:
  if os.readlink(p/'exe')=='/usr/bin/wfb_v6_uplink':
   nt=tick(p);status=(p/'status').read_text();parent=int(next(x.split()[1] for x in status.splitlines() if x.startswith('PPid:')))
   digest=sha(p/'exe');require(digest==native_sha,'running native SHA mismatch')
   natives.append({'pid':int(p.name),'start_ticks':nt,'sha256':digest,'ppid':parent})
 except (FileNotFoundError,ProcessLookupError):pass
require(len(natives)==1 and natives[0]['ppid']==pid,'native must be unique direct daemon child')
require(sha('/usr/bin/wfb_v6_uplink')==native_sha,'installed native SHA mismatch')
require(tick(proc)==started and tick(pathlib.Path('/proc')/str(natives[0]['pid']))==natives[0]['start_ticks'],'process changed during audit')
require((proc/'cmdline').read_bytes().split(b'\0')[:-1]==[a.encode() for a in argv],'daemon argv changed')
print(json.dumps({'installed':identity,'native':natives[0],'daemon':{'pid':pid,'start_ticks':started,'argv':argv},'loaded_json':loaded,'loaded_path':str(root/'python-loaded.json')}))
'''

def main():
    global RUN,REMOTE,BASELINE,SOURCE,ORIGINAL,ORIGINAL_IW
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-new-build',required=True)
    parser.add_argument('--expected-native-sha256',required=True)
    parser.add_argument('--mode',choices=('single','matrix'),required=True)
    parser.add_argument('--single-mib',type=int,choices=(8,40),default=8,
                        help='single模式的固定点或40MiB故障复现载荷；matrix始终40MiB')
    parser.add_argument('--output',type=pathlib.Path,required=True)
    args=parser.parse_args()
    if not re.fullmatch('[0-9a-f]{40}',args.execute_new_build):parser.error('--execute-new-build requires 40 lowercase hex characters')
    if not re.fullmatch('[0-9a-f]{64}',args.expected_native_sha256):parser.error('--expected-native-sha256 requires 64 lowercase hex characters')
    RUN=args.output.resolve();RUN.mkdir(parents=True,exist_ok=False)
    REMOTE='/srv/wfb-ng-fl/calibration/09/'+RUN.name
    shutil.copyfile(__file__,RUN/'run_measurements.py')
    with (RUN/'run_measurements.py').open('rb') as f:os.fsync(f.fileno())
    save(RUN/'runner-identity.json',{'sha256':hashlib.sha256((RUN/'run_measurements.py').read_bytes()).hexdigest(),'argv':sys.argv})
    active=ROOT/'active-run.txt';tmp=active.with_suffix('.tmp');tmp.write_text(str(RUN)+'\n');os.replace(tmp,active)
    save(RUN/'execution-contract.json',{'mode':args.mode,'expected_commit':args.execute_new_build,'expected_native_sha256':args.expected_native_sha256,'frozen_normal_radio':FROZEN_NORMAL,'matrix_mib':40,'single_mib':args.single_mib,'rates':[2000,4000],'mcs':[3,4,5,6],'repeats':3,'matrix_attempts':24,'admission_rule':'预检完整记录，按正式空闲全ready API准入；不得推断零丢包','no_training':True,'no_deployment':True})
    builds={}
    with cf.ThreadPoolExecutor(4) as ex:
        futures={ex.submit(remote,h,PREFLIGHT,[h,args.execute_new_build,args.expected_native_sha256]):h for h in HOSTS}
        for f in cf.as_completed(futures):
            h=futures[f]
            try:builds[h]=json.loads(f.result())
            except Exception as e:builds[h]={'error':repr(e)}
    save(RUN/'new-build-admission.json',{'expected_commit':args.execute_new_build,'expected_native_sha256':args.expected_native_sha256,'hosts':builds})
    if set(builds)!=set(HOSTS) or any('error' in v for v in builds.values()):raise RuntimeError('installed/running identity preflight failed')
    BASELINE=allhosts(SNAP)
    for h in HOSTS:
        link=BASELINE[h]['links'][0]
        argv=link['argv']
        try:
            startup_bandwidth=int(argv[argv.index('--radio-bandwidth')+1])
            startup_mcs=int(argv[argv.index('--radio-mcs-index')+1])
        except (ValueError,IndexError):
            continue
        expected_mcs=3 if h=='fl00' else 4
        if (startup_bandwidth,startup_mcs)==(40,expected_mcs):
            runtime=link.get('runtime_radio')
            if runtime is None or (runtime['bandwidth'],runtime['data_mcs_index'],runtime['control_mcs_index'])!=(40,expected_mcs,0):
                link['runtime_radio']={'generation':0,'bandwidth':40,'data_mcs_index':expected_mcs,'control_mcs_index':0,
                    'line':'startup argv (current native process): radio-bandwidth=40 radio-mcs-index='+str(expected_mcs)+' control_mcs_index=0'}
    save(RUN/'baseline.json',BASELINE)
    if not all(len(x['links'])==1 and len(x['tun'])==1 for x in BASELINE.values()):raise RuntimeError('expected one persistent link/TUN per board')
    for h in HOSTS:
        if BASELINE[h]['links'][0]['pid']!=builds[h]['native']['pid'] or BASELINE[h]['links'][0]['start_ticks']!=builds[h]['native']['start_ticks']:raise RuntimeError(h+' native changed after preflight')
    assert_no_pytest()
    status=wait_ready(RUN/'initial-ready.json')
    ORIGINAL=status['body']['radio']
    if not isinstance(ORIGINAL,dict) or not set(PATCH_KEYS)<=ORIGINAL.keys():raise RuntimeError('missing actual radio config')
    save(RUN/'original-radio.json',ORIGINAL)
    if any(ORIGINAL.get(k)!=v for k,v in FROZEN_NORMAL.items()):raise RuntimeError('actual baseline differs from frozen normal radio contract')
    ORIGINAL_IW=iw_radio(BASELINE);save(RUN/'original-iw.json',ORIGINAL_IW)
    for h,v in ORIGINAL_IW.items():
        if v['channel']!=ORIGINAL['channel'] or v['width']!=ORIGINAL['bandwidth_mhz'] or v['center']!=v['frequency']+10:raise RuntimeError(h+' baseline radio config/iw HT40+ mismatch')
        runtime=BASELINE[h]['links'][0]['runtime_radio']
        if runtime and (runtime['bandwidth'],runtime['data_mcs_index'],runtime['control_mcs_index'])!=(40,ORIGINAL['downlink_mcs'] if h=='fl00' else ORIGINAL['uplink_mcs'],0):raise RuntimeError(h+' baseline native runtime differs from frozen normal config')
    SOURCE={};mib=args.single_mib if args.mode=='single' else 40
    p=pathlib.Path(REMOTE)/'source'/f'model-{mib}mib.bin';p.parent.mkdir(parents=True,exist_ok=False);t=time.monotonic()
    with p.open('xb') as f:
        for _ in range(mib):f.write(os.urandom(1048576))
    generation=time.monotonic()-t;t=time.monotonic();digest=hashlib.sha256(p.read_bytes()).hexdigest()
    SOURCE[mib]={'path':str(p),'bytes':p.stat().st_size,'sha256':digest,'generation_seconds':generation,'hash_seconds':time.monotonic()-t}
    save(RUN/'sources.json',SOURCE)
    restore={'confirmed':True,'target_nodes':TARGET_NODES,'patch':{k:ORIGINAL[k] for k in PATCH_KEYS}}
    save(RUN/'restore-request.json',restore)
    failed=False
    try:
        if args.mode=='single':measure(1,3,2000,args.single_mib)
        else:
            index=0
            for mcs in [3,4,5,6]:
                for repeat in range(3):
                    for rate in [2000,4000]:
                        index+=1;measure(index,mcs,rate,40)
            save(RUN/'matrix-complete.json',{'complete':True,'attempts':index})
    except BaseException as e:
        failed=True;save(RUN/'execution-error.json',{'error':repr(e)});event('STOP '+repr(e))
    finally:
        event('RESTORE start');result={'success':False,'errors':{}}
        try:
            r=api('radio/reconfigure',restore);save(RUN/'restore-response.json',r)
            if r['http']!=200 or r['body'].get('status')!='finalized' or r['body'].get('effective_config')!=ORIGINAL:raise RuntimeError('restore not finalized with original config')
        except BaseException as e:result['errors']['request']=repr(e)
        try:wait_ready(RUN/'final-ready.json')
        except BaseException as e:result['errors']['ready']=repr(e)
        try:
            final=allhosts(SNAP);save(RUN/'final.json',final);assert_identity(BASELINE,final)
            result['iw']=verify_radio(final,ORIGINAL,original=True)
            if any(':1144 ' in v['sockets']['out'] for v in final.values()):raise RuntimeError('1144 cleanup incomplete')
        except BaseException as e:result['errors']['snapshot']=repr(e)
        try:
            status=api('status');save(RUN/'final-status.json',status)
            if status['http']!=200 or status['body'].get('radio')!=ORIGINAL:raise RuntimeError('full original radio config not restored')
            save(RUN/'final-state.json',api('state'))
        except BaseException as e:result['errors']['status']=repr(e)
        result['success']=not result['errors'];save(RUN/'restore-result.json',result)
        save(RUN/'runner-finished.json',{'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'success':not failed and result['success']})
        event('RESTORE end '+json.dumps(result))
    return 1 if failed or not result['success'] else 0

if __name__=='__main__':
    sys.exit(main())
