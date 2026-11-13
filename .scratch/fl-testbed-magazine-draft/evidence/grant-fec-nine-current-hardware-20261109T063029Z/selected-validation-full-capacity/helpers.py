import pathlib,json,concurrent.futures,hashlib,os,time,subprocess
def local_runner_gate(label):
    suspects=[]
    for p in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
        try:a=p.read_bytes().decode().split('\0')
        except (OSError,UnicodeError):continue
        if len(a)>1 and any(pathlib.Path(x).name=='runner.py' or pathlib.Path(x).name.startswith('orchestration-') or (pathlib.Path(x).name=='worker.py' and int(p.parent.name)!=os.getpid()) for x in a[1:3]):suspects.append(dict(pid=p.parent.name,argv=a))
    write(ROOT/(label+'-local-runner-gate.json'),dict(ok=not suspects,suspects=suspects));assert not suspects,suspects

def collect(run_id,since,label=""):
    st=d.status();assert st.get('active_job') is None and st.get('server_state')=='IDLE'
    inventory=r"""import pathlib,json,subprocess,socket
root=pathlib.Path('/srv/wfb-ng-fl')/('comm-'+RUN_ID);logs={};files=[]
if root.exists():
 for p in root.rglob('*'):
  if p.is_symlink() or not p.is_file():continue
  if p.name.endswith(('.log','.status','.json','.jsonl')):logs[str(p)]=p.read_text(errors='replace')
  elif p.stat().st_size>=1048576:files.append(dict(path=str(p),size_bytes=p.stat().st_size))
k=subprocess.run(['journalctl','-k','--since','@'+str(SINCE),'--no-pager','-o','cat'],capture_output=True,text=True,timeout=4)
print(json.dumps(dict(host=socket.gethostname(),run_id=RUN_ID,logs=logs,files=files,kernel=k.stdout,kernel_stderr=k.stderr)))
"""
    def one(host):
        row=d.remote(host,'RUN_ID='+repr(run_id)+'\nSINCE='+repr(since)+'\n'+inventory,timeout=24)
        if row.get('returncode')!=0:raise RuntimeError('collection management failure: '+host)
        for f in row['result']['files']:
            code="import pathlib,hashlib,json\np=pathlib.Path("+repr(f['path'])+");h=hashlib.sha256()\nwith p.open('rb') as s:\n for b in iter(lambda:s.read(1048576),b''):h.update(b)\nprint(json.dumps(dict(sha256=h.hexdigest(),size_bytes=p.stat().st_size)))"
            fact=d.remote(host,code,timeout=30)
            if fact.get('returncode')!=0:raise RuntimeError('single-file archive hash failed: '+host)
            f.update(fact['result']);f['complete_model']=f['size_bytes']==44770302 and f['sha256']==d.PAYLOAD_SHA
        return row
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:rows=list(pool.map(one,d.HOSTS))
    write(ROOT/(run_id+label+'-exact-uftp-kernel-files.json'),dict(idle_status=st,rows=rows))
    return rows

def exact_cleanup(run_id):
    src=(d.TOOL/'recover.py').read_text()
    code='import sys\nsys.argv='+repr(['recover.py',run_id])+'\n'+src
    # 恢复脚本仅含 shebang，无 future 导入；以 __main__ 执行原有精确恢复逻辑。
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


# 冷启动后的条件探测独立保存；未满足全十板实际状态与九 READY 前不调用发布 audit。
POLL_CODE = r'''import pathlib,json,socket,struct,subprocess,hashlib,re
items=[]
for p in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
 try:
  a=p.read_bytes().decode().rstrip(chr(0)).split(chr(0))
  if a[0]!='/usr/bin/wfb_v6_uplink':continue
  dev=pathlib.Path('/sys/class/net')/a[a.index('--air-interface')+1]
  usb=next(q for q in [(dev/'device').resolve(),*(dev/'device').resolve().parents] if (q/'idVendor').exists())
  queries={}
  for cmd,fmt,key in [(4,'!II7B','radio'),(3,'!II2B','fec')]:
   with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
    s.settimeout(.5);s.sendto(struct.pack('!IB',0x434f4d4d,cmd),('127.0.0.1',int(a[a.index('--radio-control-port')+1])));raw,peer=s.recvfrom(4096);queries[key]=list(struct.unpack(fmt,raw))
  items.append(dict(argv=a,pid=p.parent.name,sha256=hashlib.sha256((p.parent/'exe').read_bytes()).hexdigest(),interface=dev.name,mac=(dev/'address').read_text().strip(),driver=(dev/'device/driver').resolve().name,usb_speed=(usb/'speed').read_text().strip(),queries=queries,iw=subprocess.check_output(['iw','dev',dev.name,'info'],text=True,timeout=2)))
 except (OSError,UnicodeError):continue
k=subprocess.run(['journalctl','-k','--since','@'+str(SINCE),'--no-pager','-o','cat'],capture_output=True,text=True,timeout=2)
u=subprocess.check_output(['systemctl','show','wfb-fl-'+('server' if HOST=='fl00' else 'client')+'-daemon.service','-p','ActiveState'],text=True,timeout=2)
print(json.dumps(dict(native=items,service=u,usb_reset=bool(re.search(r'(?i)(reset\s+(?:high|full|super|low)[^\n]*USB|USB[^\n]*reset)',k.stdout)))))
'''

def ready_before_audit(root,label,grant,guard,since,seconds=60):
    end=min(time.monotonic()+seconds,d.MATRIX['deadline_monotonic'])
    attempts=[]
    while time.monotonic()<end:
        def one(host):
            return d.remote(host,'HOST='+repr(host)+'\nSINCE='+repr(since)+'\n'+POLL_CODE,timeout=min(8,max(.1,end-time.monotonic())))
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:rows=list(pool.map(one,d.HOSTS))
        failures=[];fatal=[]
        for row in rows:
            host=row['host']
            if row.get('returncode')!=0:fatal.append(host+': management/probe failed');continue
            data=row['result'];natives=data['native']
            if data['usb_reset']:fatal.append(host+': USB reset')
            if len(natives)>1:fatal.append(host+': multiple native')
            if len(natives)!=1 or 'ActiveState=active' not in data['service']:failures.append(host);continue
            n=natives[0];a=n['argv'];q=n['queries']
            if (n['sha256']!=d.SHA or n['interface']!=d.HARDWARE[host]['interface'] or n['mac']!=d.EXPECTED_MAC[host] or n['driver']!='rtl88xxau_wfb'):
                fatal.append(host+': hardware/native identity mismatch')
            if q['fec']!=[0x434f4d4d,0,8,12] or q['radio']!=[0x434f4d4d,0,0,0,0,40,3 if host=='fl00' else 4,0,1]:fatal.append(host+': actual FEC/radio mismatch')
            if host=='fl00' and (a[a.index('--grant-duration-ms')+1]!=str(grant) or a[a.index('--guard-interval-ms')+1]!=str(guard)):fatal.append(host+': actual schedule mismatch')
            if not all(x in n['iw'] for x in ['channel 149 ','width: 40 MHz','type monitor','txpower -18.00 dBm']):failures.append(host)
        try:status=d.status()
        except Exception as exc:
            write(root/(label+'-actual-ready-poll.json'),dict(passed=False,attempts=attempts,failed_rows=rows,error=repr(exc)))
            raise RuntimeError('管理 HTTP 条件探测失败: '+repr(exc)) from exc
        online=sorted(int(k) for k,v in status.get('nodes',{}).items() if v.get('readiness')!='OFFLINE')
        bad=[n for n in d.NODES if any(status.get('nodes',{}).get(str(n),{}).get(k)!=v for k,v in [('readiness','READY'),('reported_state','IDLE'),('current_channel',149)])]
        passed=not failures and not fatal and not bad and online==d.NODES and status.get('active_job') is None and status.get('server_state')=='IDLE'
        attempts.append(dict(wall=time.time(),rows=rows,status=status,fail_hosts=failures,fail_nodes=bad,fatal=fatal,passed=passed))
        write(root/(label+'-actual-ready-poll.json'),dict(passed=passed,attempts=attempts,deadline_seconds=seconds))
        if fatal:raise RuntimeError('实际条件探测出现管理/USB/身份错误: '+repr(fatal))
        if passed and time.monotonic()<=end:return attempts[-1]
        d.checkpoint('condition_poll_'+label)
        if time.monotonic()<end:time.sleep(min(1,end-time.monotonic()))
    raise TimeoutError('全十板实际状态与九 READY 条件未在界限内满足')


def independent_nine(root,c,rows):
    # 用归档时现场重新计算的逐文件 SHA 对照 runner 的九个精确路径。
    expected=c['server_verified_files'];assert set(expected)==set(map(str,d.NODES)), '服务端必须有九份独立文件'
    server=next(row['result'] for row in rows if row['host']=='fl00')
    actual={f['path']:f for f in server['files']}
    facts={}
    for node,f in expected.items():
        a=actual[f['path']]
        assert f['size_bytes']==a['size_bytes']==44770302 and f['sha256']==a['sha256']==d.PAYLOAD_SHA
        facts[node]=dict(path=f['path'],size_bytes=a['size_bytes'],sha256=a['sha256'])
    assert len({f['path'] for f in facts.values()})==9
    write(root/'independent-server-nine-size-sha.json',dict(ok=True,files=facts))
    return facts
