import pathlib,json,time,hashlib,importlib.util,concurrent.futures,threading,traceback,os
ROOT=pathlib.Path(__file__).parent;STAGE=ROOT.parent
s=importlib.util.spec_from_file_location('cleanup_driver',STAGE/'driver-base.py');d=importlib.util.module_from_spec(s);s.loader.exec_module(d);d.checkpoint=lambda *a,**k:None
LOCK=threading.Lock();TARGET=1073741824;SHA=d.PAYLOAD_SHA;SIZE=44770302
M=dict(status='running',purpose='Reclaim exact duplicate complete model payload caches; preserve failed samples and all logs/metadata',prior_no_delete_record=str(STAGE/'storage-closure/cleanup-manifest.json'),threshold_change=dict(previous_bytes=1610612736,current_bytes=TARGET,reason='执行编排根据单次模型约201MB及两次模型预算，将自定空间预留由1.5GiB调整为1GiB；这是代理的执行选择，用户授权的是继续测试。',single_model_budget_bytes=3*SIZE+64*1024*1024,future_fec12_pre_model_statvfs_minimum=512*1024*1024),canonical=dict(path=str(d.PAYLOAD),size=SIZE,sha256=SHA),hosts={},files=[],deleted_count=0,tests_started=0)
def save():
 d.write(ROOT/'cleanup-manifest.json',M);d.write(ROOT/'checkpoint.json',dict(status=M['status'],wall=time.time(),pid=os.getpid(),deleted_count=M['deleted_count'],hosts=M['hosts']))
def remote(h,code,timeout=50):
 a=d.remote(h,code,timeout=timeout)
 if a.get('returncode')!=0:raise RuntimeError(str(a)[:1000])
 return a['result']
HASH=r"""import pathlib,hashlib,json,stat,os
p=pathlib.Path(PATH);s=p.lstat();assert stat.S_ISREG(s.st_mode) and not p.is_symlink() and s.st_size==44770302
assert p.resolve()==p and p.name in ['model.bin','update.bin'] and p.parts[3].startswith('comm-') and '/uftp-tmp/' not in str(p)
h=hashlib.sha256()
with p.open('rb') as f:
 for chunk in iter(lambda:f.read(1048576),b''):h.update(chunk)
print(json.dumps(dict(path=str(p),size_bytes=s.st_size,sha256=h.hexdigest(),inode=s.st_ino,device=s.st_dev,allocated_bytes=s.st_blocks*512,nlink=s.st_nlink)))
"""
ARCHIVE=r"""import pathlib,hashlib,json,base64,subprocess
p=pathlib.Path(COMM);files=[]
for f in p.rglob('*'):
 if f.is_symlink() or not f.is_file():continue
 if f.suffix in ['.log','.status','.json','.jsonl']:
  b=f.read_bytes();files.append(dict(path=str(f),size_bytes=len(b),sha256=hashlib.sha256(b).hexdigest(),base64=base64.b64encode(b).decode()))
print(json.dumps(dict(comm=COMM,files=files,read_only=True)))
"""
DELETE=r"""import pathlib,hashlib,json,stat,os,subprocess
p=pathlib.Path(EXPECTED['path']);s=p.lstat()
assert stat.S_ISREG(s.st_mode) and not p.is_symlink() and p.resolve()==p
assert s.st_size==44770302 and s.st_ino==EXPECTED['inode'] and s.st_dev==EXPECTED['device'] and s.st_nlink==1
assert p.name in ['model.bin','update.bin'] and p.parts[3].startswith('comm-') and '/uftp-tmp/' not in str(p) and str(p)!=KEEP
units=subprocess.check_output(['systemctl','list-units','--all','--no-legend','--plain','comm-smoke-*'],text=True,timeout=3)
assert not [x for x in units.splitlines() if len(x.split())>=4 and x.split()[2] not in ['inactive','failed']]
h=hashlib.sha256()
with p.open('rb') as f:
 for chunk in iter(lambda:f.read(1048576),b''):h.update(chunk)
assert h.hexdigest()==EXPECTED['sha256']==SHA
s2=p.lstat();assert (s2.st_ino,s2.st_dev,s2.st_size,s2.st_mtime_ns)==(s.st_ino,s.st_dev,s.st_size,s.st_mtime_ns)
p.unlink()
v=os.statvfs('/srv/wfb-ng-fl')
print(json.dumps(dict(path=str(p),deleted=True,sha256=h.hexdigest(),size_bytes=s.st_size,available_bytes=v.f_bavail*v.f_frsize)))
"""
def one(host,inv):
 keep=next(f['path'] for f in inv['files'] if '/comm-rsmc20261109T060537Z-g30-d5-model/role/rounds/' in f['path'] and f['path'].endswith('/model.bin'))
 kept=remote(host,'PATH='+repr(keep)+'\n'+HASH);assert kept['sha256']==SHA
 with LOCK:M['hosts'][host]=dict(status='inventory',retained_b2=kept,deleted_count=0);save()
 candidates=[f for f in inv['files'] if f['path']!=keep and '/uftp-tmp/' not in f['path'] and pathlib.PurePosixPath(f['path']).name in ['model.bin','update.bin']]
 archived={}
 for candidate in candidates:
  v=remote(host,"import os,json\ns=os.statvfs('/srv/wfb-ng-fl');print(json.dumps(dict(available_bytes=s.f_bavail*s.f_frsize)))",timeout=8)
  if v['available_bytes']>=TARGET:break
  path=candidate['path'];comm='/'.join(path.split('/')[:4]);runid=pathlib.PurePosixPath(comm).name[5:]
  if comm not in archived:
   logs=remote(host,'COMM='+repr(comm)+'\n'+ARCHIVE,timeout=24)
   assert logs['files'],'no metadata/log archive'
   archive=ROOT/(host+'-'+runid+'-metadata-logs.json');d.write(archive,logs)
   # Verify exact serialized local archive before any deletion.
   reread=json.loads(archive.read_text());assert reread==logs
   archived[comm]=str(archive)
  fact=remote(host,'PATH='+repr(path)+'\n'+HASH)
  entry=dict(host=host,runid=runid,**fact,local_log_archive=archived[comm],canonical_local=str(d.PAYLOAD),status='sha_mismatch_preserved')
  if fact['sha256']!=SHA or fact['nlink']!=1:
   with LOCK:M['files'].append(entry);save()
   continue
  entry['status']='verified_pending_exact_unlink'
  with LOCK:M['files'].append(entry);save()
  result=remote(host,'EXPECTED='+repr(fact)+'\nKEEP='+repr(keep)+'\nSHA='+repr(SHA)+'\n'+DELETE)
  with LOCK:
   entry.update(status='deleted_exact_duplicate',deletion_result=result);M['deleted_count']+=1;M['hosts'][host]['deleted_count']+=1;M['hosts'][host]['available_bytes']=result['available_bytes'];save()
 kept_after=remote(host,'PATH='+repr(keep)+'\n'+HASH);assert kept_after['sha256']==SHA
 with LOCK:M['hosts'][host].update(status='complete',retained_b2_final=kept_after,available_bytes=v['available_bytes']);save()
 if v['available_bytes']<TARGET:raise RuntimeError(host+' verified candidates insufficient for 1GiB')
try:
 assert d.PAYLOAD.stat().st_size==SIZE and hashlib.sha256(d.PAYLOAD.read_bytes()).hexdigest()==SHA
 st=d.status();assert st.get('active_job') is None and st.get('server_state')=='IDLE'
 inv=json.loads((STAGE/'storage-closure/candidate-size-inventory.json').read_text())
 save()
 with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
  jobs=[pool.submit(one,x['host'],x['result']) for x in inv['rows']]
  for j in jobs:j.result()
 M['status']='cleanup_complete_pending_baseline';save()
 a=d.audit(ROOT,'post-clean-full10',10,10)
 st=d.status();ready=st.get('active_job') is None and st.get('server_state')=='IDLE' and all(st.get('nodes',{}).get(str(n),{}).get('readiness')=='READY' and st['nodes'][str(n)].get('reported_state')=='IDLE' and st['nodes'][str(n)].get('current_channel')==149 for n in d.NODES)
 d.write(ROOT/'post-clean-ready.json',dict(ok=ready,status=st,wall=time.time()))
 M['full10_baseline_ok']=a['ok'];M['nine_ready_idle']=ready
 assert a['ok'] and ready
 code="import subprocess,json\nprint(json.dumps(dict(df_bytes=subprocess.check_output(['df','-B1','/srv/wfb-ng-fl'],text=True,timeout=3),df_inodes=subprocess.check_output(['df','-i','/srv/wfb-ng-fl'],text=True,timeout=3))))"
 with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:rows=list(pool.map(lambda h:d.remote(h,code,timeout=8),d.HOSTS))
 d.write(ROOT/'post-clean-full10-df.json',dict(rows=rows,wall=time.time()))
 M['status']='complete';save()
except BaseException as exc:
 M.update(status='stopped',error=repr(exc),traceback=traceback.format_exc());save()
