#!/usr/bin/env python3
"""工单09固定四板28次测量。仅调用已部署03 IPC和独立1144 UFTP，不部署/停服务。"""
import concurrent.futures as cf
import datetime, hashlib, json, math, os, pathlib, re, shlex, subprocess, sys, time, urllib.request, urllib.error
ROOT=pathlib.Path(__file__).resolve().parent
RUN=pathlib.Path((ROOT/'active-run.txt').read_text().strip())
BATCH=RUN.name
REMOTE='/srv/wfb-ng-fl/calibration/09/'+BATCH
HOSTS=['fl00','fl01','fl02','fl04']
NODES={'fl01':1,'fl02':2,'fl04':4}
OPENER=urllib.request.build_opener(urllib.request.ProxyHandler({}))

def save(p,v):
    pathlib.Path(p).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
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
import os,sys,json,pathlib,subprocess,time
os.chdir('/tmp'); r={'utc':time.time(),'monotonic':time.monotonic()}
def cmd(a):
 p=subprocess.run(a,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=10);return {'rc':p.returncode,'out':p.stdout}
r['iw']=cmd(['iw','dev']);r['ip']=cmd(['ip','-j','address']);r['sockets']=cmd(['ss','-lunp']);r['processes']=cmd(['ps','-eo','pid,etimes,pcpu,pmem,args','--sort=-pcpu'])
r['loadavg']=pathlib.Path('/proc/loadavg').read_text();r['cpu']=pathlib.Path('/proc/stat').read_text();r['disk']=pathlib.Path('/proc/diskstats').read_text();r['links']=[]
for p in pathlib.Path('/proc').glob('[0-9]*'):
 try:
  a=(p/'cmdline').read_bytes().replace(b'\0',b' ').decode()
  if a.startswith('/usr/bin/wfb_v6_uplink '):
   log=os.readlink(p/'fd/1');r['links'].append({'pid':int(p.name),'args':a,'start_ticks':(p/'stat').read_text().split()[21],'log':log,'offset':os.stat(log).st_size})
 except (OSError,UnicodeError):pass
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
if p.poll() is None:r['start_ticks']=pathlib.Path(f'/proc/{p.pid}/stat').read_text().split()[21]
(root/'receiver.json').write_text(json.dumps(r));print(json.dumps(r))
'''

FINISH_RX=r'''
import sys,pathlib,subprocess,json,time,hashlib,os,signal
root=pathlib.Path(sys.argv[1]);r={};p=root/'received'/sys.argv[2]
t=time.monotonic()
if p.exists():
 h=hashlib.sha256()
 with p.open('rb') as f:
  for block in iter(lambda:f.read(1048576),b''):h.update(block)
 r.update(bytes=p.stat().st_size,sha256=h.hexdigest())
else:r.update(bytes=None,sha256=None,error='final_file_missing')
r['hash_seconds']=time.monotonic()-t;r['received_files']=[str(x.relative_to(root)) for x in root.rglob('*') if x.is_file()]
info=json.loads((root/'receiver.json').read_text());pid=info['pid'];proc=pathlib.Path(f'/proc/{pid}')
if proc.exists():
 stat=(proc/'stat').read_text().split();a=(proc/'cmdline').read_bytes()
 if stat[21]!=info.get('start_ticks') or b'uftpd' not in a or str(root).encode() not in a:raise RuntimeError('PID identity mismatch')
 os.kill(pid,signal.SIGTERM)
 for _ in range(30):
  if not proc.exists() or (proc/'stat').read_text().split()[2]=='Z':break
  time.sleep(.1)
 r['receiver_stopped']=not proc.exists() or (proc/'stat').read_text().split()[2]=='Z'
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
    ok=r['http']==200 and s.get('server_state')=='IDLE' and s.get('active_job') is None and online==[1,2,4] and all(s['nodes'][str(n)]['readiness']=='READY' and s['nodes'][str(n)]['reported_state']=='IDLE' for n in [1,2,4])
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
        if [(x['ifname'],x['ifindex'])for x in a['tun']] != [(x['ifname'],x['ifindex'])for x in b['tun']]:raise RuntimeError(h+' TUN changed')

def assert_no_pytest():
    active=[]
    for p in pathlib.Path('/proc').glob('[0-9]*'):
        try:
            args=(p/'cmdline').read_bytes().split(b'\0')
            if len(args)>2 and args[1:3]==[b'-m',b'pytest']:active.append({'pid':int(p.name),'args':[a.decode(errors='replace') for a in args if a]})
        except OSError:pass
    if active:raise RuntimeError('concurrent pytest: '+json.dumps(active))

def measure(index,mcs,rate,mib):
    name=f'{index:02d}-mcs{mcs}-r{rate}-{mib}mib';d=RUN/name;d.mkdir(exist_ok=False)
    rec={'attempt':name,'mcs':mcs,'rate_kbps':rate,'mib':mib,'target_nodes':[1,2,4],'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()};save(d/'result.json',rec)
    event('START '+name)
    receivers={};before=None
    try:
        assert_no_pytest()
        wait_ready(d/'admission.json')
        before=allhosts(SNAP);save(d/'before.json',before);assert_identity(BASELINE,before)
        for h in HOSTS:
            if ':1144 ' in before[h]['sockets']['out']:raise RuntimeError(h+' 1144 occupied')
        request={'confirmed':True,'target_nodes':[1,2,4],'patch':{'bandwidth_mhz':20,'downlink_mcs':mcs,'uplink_mcs':4,'radio_txpower_dbm':14,'uftp_rate_kbps':rate}}
        save(d/'calibrate-request.json',request)
        wait_ready(d/'immediate-admission.json')
        r=api('radio/calibrate',request);save(d/'calibrate-response.json',r)
        if r['http']!=200 or r['body'].get('status')!='finalized' or r['body'].get('calibration') is not True:raise RuntimeError('calibration not finalized')
        effective=r['body']['effective_config']
        if any(effective.get(k)!=v for k,v in request['patch'].items()):raise RuntimeError('effective config mismatch')
        applied=allhosts(SNAP);save(d/'applied.json',applied);assert_identity(BASELINE,applied)
        if not all('width: 20 MHz' in applied[h]['iw']['out'] and 'channel 157 (5785 MHz)' in applied[h]['iw']['out'] for h in HOSTS):raise RuntimeError('iw width/channel mismatch')
        with cf.ThreadPoolExecutor(3) as ex:
            futures={ex.submit(remote,h,START_RX,[REMOTE+'/'+name,NODES[h]]):h for h in NODES}
            for f in cf.as_completed(futures):receivers[futures[f]]=json.loads(f.result())
        save(d/'receivers.json',receivers)
        if len(receivers)!=3 or any(v['poll'] is not None for v in receivers.values()):raise RuntimeError('receiver failed')
        path=SOURCE[mib]['path'];budget=max(180,math.ceil(3*mib*1048576*8/(rate*1000)+60))
        args=['sudo','-n','timeout','--signal=TERM','--kill-after=10s',str(budget),'uftp','-Y','none','-p','1144','-R',str(rate),'-I','fl-s','-M','239.255.42.1','-P','239.255.42.2','-H','0x00000001,0x00000002,0x00000004','-L',str(d/'uftp.log'),'-S',str(d/'uftp.status'),path]
        save(d/'sender-command.json',args);t=time.monotonic()
        with (d/'sender-console.log').open('wb') as out:p=subprocess.run(args,stdout=out,stderr=subprocess.STDOUT,timeout=budget+20)
        rec.update(sender_rc=p.returncode,sender_wall_seconds=time.monotonic()-t,timeout_seconds=budget,source=SOURCE[mib])
    except Exception as e:rec['error']=repr(e)
    finally:
        for h in receivers:
            try:
                data=json.loads(remote(h,FINISH_RX,[REMOTE+'/'+name,pathlib.Path(SOURCE[mib]['path']).name]));save(d/(h+'-receiver.json'),data)
                for n in ['uftpd.log','uftpd.status','receiver-console.log']:
                    if data.get(n) is not None:(d/(h+'-'+n)).write_text(data[n])
                rec.setdefault('received',{})[h]={k:v for k,v in data.items() if k in ['bytes','sha256','hash_seconds','receiver_stopped','error']}
            except Exception as e:rec.setdefault('cleanup_errors',{})[h]=repr(e)
        after=allhosts(SNAP);save(d/'after.json',after)
        try:assert_identity(BASELINE,after);rec['pid_tun_unchanged']=True
        except Exception as e:rec['identity_error']=repr(e)
        logs={}
        if before:
            for h in HOSTS:
                link=before[h]['links'][0]
                log=json.loads(remote(h,GET_LOG,[link['log'],link['offset']]))
                (d/(h+'-link.log')).write_text(log.pop('text'));save(d/(h+'-link-range.json'),log)
                logs[h]=(d/(h+'-link.log')).read_text()
        rec['rx_ant_targets']={h:(f'RX_ANT\t5785:{mcs}:20' in logs.get(h,'')) for h in NODES}
        rec['tx_application_lines']={h:[x for x in logs.get(h,'').splitlines() if 'radio' in x.lower() and any(w in x.lower() for w in ['appl','generation','data','control'])] for h in HOSTS}
        sender=(d/'uftp.log').read_text(errors='replace') if (d/'uftp.log').exists() else ''
        group=sender.split('Finishing group')[-1] if 'Finishing group' in sender else ''
        rec['group_complete']=('Group complete' in group and all('Got COMPLETE from client '+f'0x{n:08X}' in group for n in [1,2,4]))
        rec['nak_lines']=[x for x in sender.splitlines() if 'NAK' in x];rec['pass_lines']=[x for x in sender.splitlines() if 'Starting pass' in x]
        received=rec.get('received',{})
        rec['all_hashes_match']=len(received)==3 and all(v.get('sha256')==SOURCE[mib]['sha256'] and v.get('bytes')==mib*1048576 and v.get('receiver_stopped') for v in received.values())
        server_updates=[int(v) for v in re.findall(r'v6_radio_update[^\n]*bandwidth=20 mcs_index=(\d+)',logs.get('fl00',''))]
        rec['server_tx_mcs_stable']=all(v==mcs for v in server_updates)
        rec['success']=not rec.get('error') and not rec.get('cleanup_errors') and rec.get('sender_rc')==0 and rec['group_complete'] and rec['all_hashes_match'] and rec.get('pid_tun_unchanged') and all(rec['rx_ant_targets'].values()) and rec['server_tx_mcs_stable']
        if rec.get('sender_wall_seconds'):rec['goodput_mbps']=mib*1048576*8/rec['sender_wall_seconds']/1e6 if rec['success'] else None
        rec['ended_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat();save(d/'result.json',rec)
        with (RUN/'attempts.jsonl').open('a') as f:f.write(json.dumps(rec,ensure_ascii=False)+'\n')
        event('END '+name+' success='+str(rec['success'])+' sender_seconds='+str(rec.get('sender_wall_seconds'))+' error='+str(rec.get('error')))
    if not rec['success']:raise RuntimeError('attempt failed: '+name)
    return rec

if __name__=='__main__':
    save(RUN/'execution-contract.json',{'matrix_mib':8,'rates':[2000,4000],'mcs':[3,4,5,6],'repeats':3,'long_mib':40,'long_repeats':1,'recommendation_rule':'choose 2000 only if three matrix repeats succeed; one 40MiB confirmation does not extend range','positions':'unchanged by experiment; coordinates/distances not measured','no_training':True,'no_deployment':True})
    BASELINE=allhosts(SNAP);save(RUN/'baseline.json',BASELINE)
    assert all(len(x['links'])==1 and len(x['tun'])==1 for x in BASELINE.values()),'expected exactly one persistent link and TUN per board'
    assert_no_pytest()
    wait_ready(RUN/'initial-ready.json', checks=450)
    SOURCE={}
    for mib in [8,40]:
        p=pathlib.Path(REMOTE)/'source'/f'model-{mib}mib.bin';p.parent.mkdir(parents=True,exist_ok=True);t=time.monotonic()
        with p.open('wb') as f:
            for _ in range(mib):f.write(os.urandom(1048576))
        generation=time.monotonic()-t;t=time.monotonic();digest=hashlib.sha256(p.read_bytes()).hexdigest()
        SOURCE[mib]={'path':str(p),'bytes':p.stat().st_size,'sha256':digest,'generation_seconds':generation,'hash_seconds':time.monotonic()-t}
    save(RUN/'sources.json',SOURCE)
    restore={'confirmed':True,'target_nodes':[1,2,4],'patch':{'channel':157,'bandwidth_mhz':40,'radio_txpower_dbm':14,'downlink_mcs':3,'uplink_mcs':4,'uftp_rate_kbps':15000}}
    save(RUN/'restore-request.json',restore)
    try:
        index=0
        for mcs in [3,4,5,6]:
            for repeat in range(3):
                for rate in [2000,4000]:index+=1;measure(index,mcs,rate,8)
        for mcs in [3,4,5,6]:index+=1;measure(index,mcs,2000,40)
        save(RUN/'matrix-complete.json',{'complete':True,'attempts':index})
    except Exception as e:
        save(RUN/'execution-error.json',{'error':repr(e)});event('STOP '+repr(e))
    finally:
        event('RESTORE start');r=api('radio/reconfigure',restore);save(RUN/'restore-response.json',r)
        time.sleep(3)
        try:wait_ready(RUN/'final-ready.json')
        except Exception as e:save(RUN/'final-ready-error.json',{'error':repr(e)})
        final=allhosts(SNAP);save(RUN/'final.json',final)
        save(RUN/'final-status.json',api('status'));save(RUN/'final-state.json',api('state'))
        event('RESTORE end '+json.dumps(r));(RUN/'runner-finished.txt').write_text(datetime.datetime.now(datetime.timezone.utc).isoformat())
