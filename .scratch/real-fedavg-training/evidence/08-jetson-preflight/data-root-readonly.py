import os,pathlib,time,hashlib
print('免密sudo只读补查 UTC',time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),flush=True)
start=time.monotonic(); count=0
for root in ['/var/lib/wfb-ng','/var/lib/wfb-ng-fl']:
 print('范围',root,flush=True)
 if not os.path.isdir(root):print('不存在',flush=True);continue
 for parent,ds,fs in os.walk(root,followlinks=False,onerror=lambda e:print('失败',repr(e),flush=True)):
  if time.monotonic()-start>20:raise TimeoutError('限定检索超时')
  for f in fs:
   if 'idx1' in f or 'idx3' in f:
    count+=1;p=pathlib.Path(parent)/f
    print('候选',str(p),'字节',p.stat().st_size,flush=True)
    if p.stat().st_size<=60000000:print('sha256',hashlib.sha256(p.read_bytes()).hexdigest(),flush=True)
print('IDX候选总数',count,flush=True)
