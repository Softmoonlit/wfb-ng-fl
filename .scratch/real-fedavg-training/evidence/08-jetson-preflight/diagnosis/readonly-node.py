import subprocess,pathlib,json,time,hashlib,os
print('只读诊断 UTC',time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),flush=True)
def run(args,timeout=10):
 print('\n命令',args,flush=True)
 try:
  r=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,errors='backslashreplace',timeout=timeout); print(r.stdout,'退出码',r.returncode,flush=True)
 except Exception as e: print('失败',repr(e),flush=True)
host=os.uname().nodename;role='server' if host=='fl00' else 'client';unit='wfb-fl-'+role+'-daemon.service'
for args in [['date','--iso-8601=ns'],['cat','/proc/uptime'],['systemctl','cat',unit],['systemctl','show',unit,'-p','ExecStart','-p','MainPID','-p','ActiveState','-p','SubState','-p','NRestarts','-p','Environment','-p','WorkingDirectory','-p','DropInPaths'],['ps','-eLo','pid,ppid,lwp,nlwp,stat,wchan:28,comm,args'],['ss','-uapn'],['ss','-ltpn'],['ip','-brief','address'],['ip','route','show','table','main'],['iw','dev'],['lsusb','-t'],['lsusb'],['journalctl','-k','-n','100','--no-pager','-o','short-unix'],['journalctl','-u',unit,'-n','150','--no-pager','-o','short-unix']]:
 # 避免输出无关应用进程命令行；仅本角色进程及线程。
 if args[0]=='ps':
  r=subprocess.run(args,capture_output=True,text=True,timeout=10); print('\n角色进程和线程（从ps筛选）',flush=True);print('\n'.join(x for x in r.stdout.splitlines() if any(k in x for k in ['wfb-fl','wfb_v6','uftp','fl.service']) or x.startswith('    PID')),flush=True)
 else:run(args)
for root in ['/etc/systemd/system/'+unit+'.d','/run/systemd/system/'+unit+'.d','/usr/lib/systemd/system/'+unit+'.d']:
 p=pathlib.Path(root);print('\nDrop-in目录',root,'存在',p.exists(),flush=True)
 if p.exists():
  for f in sorted(p.glob('*.conf')):print(str(f),f.read_text(),flush=True)
base=pathlib.Path('/usr/local/lib/python3.10/dist-packages/wfb_ng/fl')
for n in ['build_identity.json','client_daemon.py','server_daemon.py','control.py','service.py','mnist.py','mnist_data.py']:
 p=base/n;print('安装文件',str(p),'sha256',hashlib.sha256(p.read_bytes()).hexdigest(),flush=True)
 if n.endswith('.json'):print(p.read_text(),flush=True)
root=pathlib.Path('/var/lib/wfb-ng-fl/evidence')
jobs=['web-6727da200d4e49b992134ea35989a4a1','web-f317d54238864f64ab66164f151d3622']
for job in jobs:
 d=root/job;print('\n失败作业小文件归档',str(d),'存在',d.exists(),flush=True)
 if d.exists():
  for f in sorted(d.rglob('*')):
   if f.is_file():
    print('归档文件',str(f),f.stat().st_size,flush=True)
    if f.suffix in ['.json','.log','.jsonl'] and f.stat().st_size<=150000:
     print('---内容---',f.read_text(errors='backslashreplace')[-90000:],flush=True)
# 不导入torch、不重跑数据预检，仅读目录/来源清单。
p=pathlib.Path('/srv/wfb-ng-fl/mnist/SOURCE-MANIFEST.json');print('\n官方数据manifest',p.read_text() if p.exists() else '不存在',flush=True)
