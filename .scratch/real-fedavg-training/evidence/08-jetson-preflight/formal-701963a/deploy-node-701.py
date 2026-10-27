#!/usr/bin/env python3
"""08维护窗口节点动作；从冻结源码安装，非作业推进工具。"""
import subprocess,pathlib,sys,os,json,time,hashlib,tarfile,glob
SHA='701963ac254261c31c56a074d06966a9217346f6'
ROOT=pathlib.Path('/srv/wfb-ng-fl/deploy-701963a'); SRC=ROOT/'source'
role=sys.argv[1];phase=sys.argv[2];unit='wfb-fl-'+role+'-daemon.service'
def run(args,timeout=60,check=True):
 print('UTC',time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'命令',args,flush=True)
 r=subprocess.run(args,timeout=timeout);print('退出码',r.returncode,flush=True)
 if check:r.check_returncode()
 return r
print('节点',os.uname().nodename,'角色',role,'阶段',phase,flush=True)
if phase=='backup-build':
 ROOT.mkdir(parents=True,exist_ok=False)
 backup=ROOT/'backup';backup.mkdir(mode=0o700)
 paths=[]
 for pattern in ['/etc/wfb-ng-fl','/etc/wfb-ng','/etc/systemd/system/wfb-fl*','/run/systemd/system/wfb-fl*','/usr/lib/systemd/system/wfb-fl*','/lib/systemd/system/wfb-fl*']:
  paths.extend(glob.glob(pattern))
 with tarfile.open(backup/'configs-units.tar','w') as tar:
  for name in sorted(set(paths)):tar.add(name,arcname=name.lstrip('/'))
 for args in [['systemctl','cat',unit],['systemctl','show',unit,'-p','ExecStart','-p','User','-p','Group','-p','MainPID','-p','KillMode'],['iw','dev'],['lsusb','-t'],['df','-h','/'],['cat','/etc/wfb-ng-fl/server.json' if role=='server' else '/etc/wfb-ng-fl/node.json']]:run(args,check=False)
 archive=pathlib.Path('/tmp/wfb08-701963a.tar');print('源码归档sha256',hashlib.sha256(archive.read_bytes()).hexdigest(),flush=True)
 SRC.mkdir();run(['tar','-xf',str(archive),'-C',str(SRC)])
 run(['make','-C',str(SRC),'build_v6','COMMIT='+SHA,'VERSION=0.0.0','PYTHON=/usr/bin/python3'],180)
elif phase=='stop':
 run(['systemctl','stop',unit],90)
 state=subprocess.check_output(['systemctl','show',unit,'-p','ActiveState','--value'],text=True).strip();assert state=='inactive',state
 pid=subprocess.check_output(['systemctl','show',unit,'-p','MainPID','--value'],text=True).strip();assert pid=='0',pid
 run(['systemd-cgls','--unit',unit],check=False)
 # 每板只允许本角色常驻链路；停止后不得留下角色或链路/UFTP进程。
 r=subprocess.run(['pgrep','-a','-x','wfb_v6_uplink|uftp|uftpd|wfb-fl-server|wfb-fl-client'],capture_output=True,text=True)
 print('停止后进程检查',r.stdout,'退出码',r.returncode,flush=True);assert r.returncode==1
elif phase=='install-config':
 assert subprocess.check_output(['systemctl','show',unit,'-p','MainPID','--value'],text=True).strip()=='0'
 env=dict(os.environ,COMMIT=SHA,VERSION='0.0.0',PYTHON='/usr/bin/python3',PREFIX='/usr')
 runargs=[str(SRC/'scripts/install/install-v8.sh')];print('安装env',{'COMMIT':SHA,'VERSION':'0.0.0','PREFIX':'/usr'},flush=True)
 r=subprocess.run(runargs,cwd='/tmp',env=env,timeout=90);r.check_returncode()
 work=pathlib.Path('/srv/wfb-ng-fl')/role;work.mkdir(mode=0o700,exist_ok=True);work.chmod(0o700)
 if role=='server':
  p=pathlib.Path('/etc/wfb-ng-fl/server.json');config=json.loads(p.read_text());config.update(work_dir=str(work),mnist_data_dir='/srv/wfb-ng-fl/mnist',client_entry='wfb_ng.fl.mnist:client_entry',aggregate_entry='wfb_ng.fl.mnist:aggregate_entry',evaluate_entry='wfb_ng.fl.mnist:evaluate_entry',algorithm_config={'data_seed':2026},preparation_timeout_seconds=300.0,round_timeout_seconds=1800.0,io_timeout_seconds=120.0,channel=157,bandwidth_mhz=40,radio_txpower_dbm=14,downlink_mcs=3,uplink_mcs=4,uftp_rate_kbps=15000)
  from wfb_ng.fl.server_daemon import ServerDaemonConfig
  # 源码安装后另一个干净子进程验证配置，避免长期进程缓存。
  new=p.with_suffix('.json.08-new');new.write_text(json.dumps(config,indent=2)+'\n');new.chmod(0o644)
  run(['/usr/bin/python3','-B','-c',"from wfb_ng.fl.server_daemon import load_server_config; c=load_server_config('/etc/wfb-ng-fl/server.json.08-new'); print(c)"])
  os.replace(new,p)
  command='/usr/bin/wfb-fl-server-daemon --config /etc/wfb-ng-fl/server.json'
 else:
  pathlib.Path('/var/lib/wfb-ng-fl/evidence').mkdir(mode=0o700,parents=True,exist_ok=True)
  command='/usr/bin/wfb-fl-client-daemon --config /etc/wfb-ng-fl/node.json --channel 157 --bandwidth-mhz 40 --radio-txpower-dbm 14 --uplink-mcs 4 --mnist-data-dir /srv/wfb-ng-fl/mnist --work-dir /srv/wfb-ng-fl/client'
 # 备份已完成。移走本角色现有drop-in，再以一个明确drop-in整合；原始文件仍在backup归档。
 drop=pathlib.Path('/etc/systemd/system')/(unit+'.d')
 if drop.exists():os.rename(drop,ROOT/'backup'/('original-'+unit+'.d'))
 drop.mkdir(parents=True);(drop/'08-mnist.conf').write_text('[Service]\nExecStart=\nExecStart='+command+'\nEnvironment=OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1\n')
 run(['systemctl','daemon-reload']);run(['systemctl','cat',unit]);run(['systemctl','show',unit,'-p','ExecStart','-p','User','-p','Group','-p','KillMode'])
 r=subprocess.run(['/usr/bin/python3','-B','-c',"import pathlib,json,wfb_ng; from wfb_ng.fl.mnist import client_entry,aggregate_entry,evaluate_entry; p=pathlib.Path(wfb_ng.__file__).parent/'fl/build_identity.json'; d=json.loads(p.read_text()); print(wfb_ng.__file__,d); assert d['commit']=='"+SHA+"'"],cwd='/tmp',env={'PATH':'/usr/bin:/bin','PYTHONPATH':''},timeout=30);r.check_returncode()
elif phase=='start':run(['systemctl','start',unit]);run(['systemctl','is-active',unit])
else:raise ValueError(phase)
print('NODE_PHASE_PASS',role,phase,flush=True)
