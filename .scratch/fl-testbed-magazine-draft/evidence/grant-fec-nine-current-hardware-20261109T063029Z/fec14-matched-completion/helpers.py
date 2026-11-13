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
            fact=d.remote(host,code,timeout=50)
            if fact.get('returncode')!=0:raise RuntimeError('single-file archive hash failed: '+host)
            f.update(fact['result']);f['complete_model']=f['size_bytes']==44770302 and f['sha256']==d.PAYLOAD_SHA
        return row
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:rows=list(pool.map(one,d.HOSTS))
    write(ROOT/(run_id+label+'-exact-uftp-kernel-files.json'),dict(idle_status=st,rows=rows))

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


def infrastructure_failure(run_id, root):
    # 检查已归档日志与本轮进度；故障清理和独立恢复后停止矩阵。
    import re
    paths=[ROOT/(run_id+'-after-exact-uftp-kernel-files.json'),root/run_id/'progress.jsonl',root/'runner.log']
    patterns=[r'not\s*enough\s*disk',r'no\s+space\s+left',r'\bENOSPC\b',r'UFTP\s*退出码为\s*8\b',r'(?i)uftp[^\n]{0,100}(?:exit(?:ed)?(?:\s+(?:with|status|code))*|return(?:code)?|code|rc)[\s:=]+8\b']
    matches=[]
    for path in paths:
        if path.exists():
            text=path.read_text(errors='replace')
            if path.suffix=='.json':text=json.dumps(json.loads(text),ensure_ascii=False)
            elif path.suffix=='.jsonl':text='\n'.join(json.dumps(json.loads(line),ensure_ascii=False) for line in text.splitlines() if line.strip())
            for pattern in patterns:
                found=re.search(pattern,text,re.IGNORECASE)
                if found:matches.append(dict(path=str(path),pattern=pattern,match=found.group()))
    write(root/'infrastructure-failure-check.json',dict(failed=bool(matches),matches=matches))
    return bool(matches)
