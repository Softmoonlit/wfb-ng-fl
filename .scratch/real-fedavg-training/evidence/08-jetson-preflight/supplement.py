import subprocess,os,time,pathlib,hashlib,importlib.metadata
print('补充采集 UTC:',time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),flush=True)
for d in importlib.metadata.distributions():
 name=d.metadata.get('Name','')
 if any(x in name.lower() for x in ['wfb','wifibroadcast']): print('实际发行版名称:',name,'版本:',d.version,flush=True)
for f in ['/usr/bin/wfb-fl-server-daemon','/usr/bin/wfb-fl-client-daemon','/usr/bin/wfb_v6_uplink','/proc/device-tree/model']:
 try:
  b=pathlib.Path(f).read_bytes();print('文件:',f,'sha256:',hashlib.sha256(b).hexdigest(),'字节:',len(b),flush=True)
  if f.endswith('daemon') or f.endswith('model'):print('首行/设备型号:',b.split(b'\n')[0].decode(errors='backslashreplace'),flush=True)
 except Exception as e:print('文件失败:',repr(e),flush=True)
root='/var/lib/wfb-ng-fl';print('补充限定数据根:',root,flush=True)
if not os.path.isdir(root): print('目录不存在',flush=True)
else:
 start=time.monotonic();count=0
 for parent,ds,fs in os.walk(root,onerror=lambda e:print('目录读取失败:',repr(e),flush=True),followlinks=False):
  if time.monotonic()-start>15: print('检索超时，结果不完整',flush=True);break
  for f in fs:
   if 'idx1' in f or 'idx3' in f:
    count+=1;p=pathlib.Path(parent)/f
    try:print('IDX候选:',str(p),'字节:',p.stat().st_size,'sha256:',hashlib.sha256(p.read_bytes()).hexdigest(),'官方来源尚未证实',flush=True)
    except Exception as e:print('候选读取失败:',repr(e),flush=True)
 print('IDX候选数:',count,flush=True)
for args in [['systemctl','show','wfb-fl-server-daemon.service','wfb-fl-client-daemon.service','-p','Id','-p','ActiveState','-p','MainPID','-p','Environment'],['nvidia-smi','--query-gpu=name,memory.total,driver_version','--format=csv,noheader']]:
 print('命令:',args,flush=True)
 try:
  p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=8);print(p.stdout,'退出码:',p.returncode,flush=True)
 except Exception as e:print('命令失败:',repr(e),flush=True)
