"""有边界的精确缓存回收；仅由获得父 GO 授权的 worker 导入。"""
import concurrent.futures, hashlib, json, pathlib, threading, time
ROOT = pathlib.Path(__file__).resolve().parent
BATCHROOT = ROOT.parent
MINIMUM = 512 * 1024 * 1024
TARGET = 1024 * 1024 * 1024  # 代理选择的执行预算，不是用户明确指定的阈值。
B2 = 'comm-rsmc20261109T060537Z-g30-d5-model'
LOCK = threading.Lock()

# 复用已经审计的精确文件机制，只提取常量，不执行兄弟阶段 worker。
import ast
SOURCE = BATCHROOT/'fec14-remaining/storage-cleanup-continuation-20261109T065239Z/worker.py'
for node in ast.parse(SOURCE.read_text()).body:
    if isinstance(node, ast.Assign):
        for name in node.targets:
            if isinstance(name, ast.Name) and name.id in {'HASH','ARCHIVE','DELETE'}:
                globals()[name.id] = ast.literal_eval(node.value)

def storage_rows(d):
    code="import os,json,socket\ns=os.statvfs('/srv/wfb-ng-fl');print(json.dumps(dict(host=socket.gethostname(),available_bytes=s.f_bavail*s.f_frsize,available_inodes=s.f_favail,total_bytes=s.f_blocks*s.f_frsize)))"
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        return list(pool.map(lambda host:d.remote(host,code,timeout=8), d.HOSTS))

def gate(d, root):
    rows = storage_rows(d)
    d.write(root/'pre-model-full10-storage-before.json', dict(rows=rows, minimum_available_bytes=MINIMUM))
    if any(r.get('returncode')!=0 for r in rows):
        raise RuntimeError('storage management probe failed')
    low = [r['host'] for r in rows if r['result']['available_bytes']<MINIMUM]
    if any(h not in ['fl01','fl02'] for h in low):
        raise RuntimeError('storage below 512MiB outside authorized cleanup hosts')
    if low:
        reclaim(d, root, low)
    rows = storage_rows(d)
    ok = all(r.get('returncode')==0 and r['result']['available_bytes']>=MINIMUM and r['result']['available_inodes']>0 for r in rows)
    d.write(root/'pre-model-full10-storage-admission.json', dict(ok=ok, minimum_available_bytes=MINIMUM, agent_cleanup_target_bytes=TARGET, rows=rows, wall=time.time()))
    if not ok:
        raise RuntimeError('full ten model storage admission below 512MiB or no inodes')

def reclaim(d, root, hosts):
    assert set(hosts)<= {'fl01','fl02'}
    assert d.PAYLOAD.stat().st_size==44770302 and hashlib.sha256(d.PAYLOAD.read_bytes()).hexdigest()==d.PAYLOAD_SHA
    stage=root/'exact-cache-reclamation';stage.mkdir()
    m=dict(status='running', canonical=dict(path=str(d.PAYLOAD),sha256=d.PAYLOAD_SHA,size=44770302),
           minimum_bytes=MINIMUM, agent_execution_budget_target_bytes=TARGET, files=[], hosts={})
    def save():d.write(stage/'cleanup-manifest.json',m)
    def remote(host,code,timeout=30):
        r=d.remote(host,code,timeout=timeout)
        if r.get('returncode')!=0:raise RuntimeError('cache management/hash probe failed: '+host+repr(r)[:500])
        return r['result']
    # 仅采用已经本地归档的完整日志与逐文件 size/SHA 证据。
    allowed={h:{} for h in hosts}
    bases=[BATCHROOT/'fec14-remaining', BATCHROOT/'fec12', BATCHROOT/'fec14-matched-completion', ROOT]
    sources=sorted({p for base in bases for p in base.rglob('*exact-uftp-kernel-files.json')})
    completed={}
    for progress in sorted({p for base in bases for p in base.rglob('progress.jsonl')}):
        lines=[json.loads(line) for line in progress.read_text().splitlines()]
        final=next((row for row in reversed(lines) if row.get('phase')=='result'),{})
        if all(final.get(k) is True for k in ['transfer_verified','cleanup_ok','restore_ok']):
            completed[progress.parent.name]=dict(path=str(progress),sha256=hashlib.sha256(progress.read_bytes()).hexdigest())
    for source in sources:
        data=json.loads(source.read_text())
        for row in data.get('rows',[]):
            host=row.get('host');result=row.get('result',{})
            if host not in allowed or row.get('returncode')!=0 or not result.get('logs'):continue
            for f in result.get('files',[]):
                p=pathlib.PurePosixPath(f['path'])
                if (len(p.parts)>4 and p.parts[:3]==('/','srv','wfb-ng-fl') and p.parts[3].startswith('comm-')
                    and p.name in ['model.bin','update.bin'] and 'uftp-tmp' not in p.parts and B2!=p.parts[3]
                    and p.parts[3][5:] in completed
                    and f.get('size_bytes')==44770302 and f.get('sha256')==d.PAYLOAD_SHA):
                    allowed[host][str(p)]=dict(local_full_evidence=str(source),evidence_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),successful_round=completed[p.parts[3][5:]])
    save()
    def one(host):
        keep_code="import pathlib,json\np=pathlib.Path('/srv/wfb-ng-fl')/"+repr(B2)+";f=[str(x) for x in p.rglob('model.bin') if '/role/rounds/' in str(x) and not x.is_symlink()];assert len(f)==1;print(json.dumps(dict(path=f[0])))"
        keep=remote(host,keep_code,8)['path']
        kept=remote(host,'PATH='+repr(keep)+'\n'+HASH);assert kept['sha256']==d.PAYLOAD_SHA
        with LOCK:m['hosts'][host]=dict(retained_b2=kept,deleted_count=0);save()
        archived={}
        for path,proof in sorted(allowed[host].items()):
            d.checkpoint('storage_exact_file')
            if time.monotonic()>=d.MATRIX['deadline_monotonic']-120:raise RuntimeError('stage time limit during cleanup')
            v=remote(host,"import os,json\ns=os.statvfs('/srv/wfb-ng-fl');print(json.dumps(dict(available_bytes=s.f_bavail*s.f_frsize)))",8)
            if v['available_bytes']>=TARGET:break
            # 仅逐一复核本地证据明确列出的精确文件，不扩大删除范围。
            exists=remote(host,"import pathlib,json\np=pathlib.Path("+repr(path)+");print(json.dumps(dict(exists=p.exists())))",8)
            if not exists['exists']:continue
            comm='/'.join(path.split('/')[:4]);runid=pathlib.PurePosixPath(comm).name[5:]
            if comm not in archived:
                logs=remote(host,'COMM='+repr(comm)+'\n'+ARCHIVE,24);assert logs['files']
                archive=stage/(host+'-'+runid+'-metadata-logs.json');d.write(archive,logs)
                assert json.loads(archive.read_text())==logs
                archived[comm]=dict(path=str(archive),sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
            fact=remote(host,'PATH='+repr(path)+'\n'+HASH)
            entry=dict(host=host,**fact,**proof,metadata_archive=archived[comm],status='preserved_mismatch')
            with LOCK:m['files'].append(entry);save()
            if fact['sha256']!=d.PAYLOAD_SHA or fact['nlink']!=1:continue
            entry['status']='verified_pending_exact_unlink'
            with LOCK:save()
            deletion=remote(host,'EXPECTED='+repr(fact)+'\nKEEP='+repr(keep)+'\nSHA='+repr(d.PAYLOAD_SHA)+'\n'+DELETE)
            with LOCK:
                entry.update(status='deleted_exact_duplicate',deletion_result=deletion)
                m['hosts'][host]['deleted_count']+=1;save()
        final=remote(host,"import os,json\ns=os.statvfs('/srv/wfb-ng-fl');print(json.dumps(dict(available_bytes=s.f_bavail*s.f_frsize)))",8)
        kept_after=remote(host,'PATH='+repr(keep)+'\n'+HASH);assert kept_after['sha256']==d.PAYLOAD_SHA
        with LOCK:m['hosts'][host].update(**final,retained_b2_final=kept_after,target_reached=final['available_bytes']>=TARGET);save()
        # 1GiB 为尽力达到的执行预算；硬性准入门槛为 512MiB。
        if final['available_bytes']<MINIMUM:raise RuntimeError(host+' cannot reach 512MiB within authorized duplicates')
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            jobs=[pool.submit(one,h) for h in hosts]
            for job in jobs:job.result()
        m['status']='complete';save()
    except BaseException as exc:
        m.update(status='stopped',error=repr(exc));save();raise
