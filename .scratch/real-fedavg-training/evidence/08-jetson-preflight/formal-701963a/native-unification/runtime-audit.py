import subprocess,pathlib,os,json,hashlib,time
h=os.uname().nodename; role='server' if h=='fl00' else 'client'; unit='wfb-fl-'+role+'-daemon.service'
def run(a):
 r=subprocess.run(a,capture_output=True,text=True,timeout=8);return {'rc':r.returncode,'out':r.stdout,'err':r.stderr}
z={'hostname':h,'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'unit':run(['systemctl','show',unit,'-p','MainPID','-p','ActiveState','-p','NRestarts','-p','ExecStart','-p','Environment','-p','WorkingDirectory','-p','DropInPaths'])}
pid=subprocess.check_output(['systemctl','show',unit,'-p','MainPID','--value'],text=True).strip();z['pid']=pid;proc=pathlib.Path('/proc')/pid;cwd=os.readlink(proc/'cwd');z['proc_cwd']=cwd;z['proc_exe']=os.readlink(proc/'exe');z['proc_cmdline']=(proc/'cmdline').read_bytes().replace(b'\0',b' ').decode();env=dict(x.split('=',1) for x in (proc/'environ').read_text().split('\0') if '=' in x);z['runtime_import_env']={k:env.get(k) for k in ['PATH','PYTHONPATH','PYTHONHOME','OMP_NUM_THREADS','OPENBLAS_NUM_THREADS']}
code="import importlib.util,json,pathlib,hashlib; names=['wfb_ng','wfb_ng.fl.service','wfb_ng.fl.console','wfb_ng.fl.client_daemon','wfb_ng.fl.server_daemon','wfb_ng.fl.mnist']; specs={m:importlib.util.find_spec(m).origin for m in names}; root=pathlib.Path(specs['wfb_ng']).parent; print(json.dumps({'specs':specs,'identity':json.loads((root/'fl/build_identity.json').read_text()),'hashes':{m:hashlib.sha256(pathlib.Path(f).read_bytes()).hexdigest() for m,f in specs.items()}}))"
r=subprocess.run([str(proc/'exe'),'-B','-c',code],cwd=cwd,env={**env,'PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True,timeout=12);z['find_spec_in_daemon_cwd_env']={'rc':r.returncode,'out':r.stdout,'err':r.stderr}
z['native']=[]
for process in pathlib.Path('/proc').iterdir():
 if not process.name.isdigit():continue
 try:
  exe=os.readlink(process/'exe'); cmd=(process/'cmdline').read_bytes().replace(b'\0',b' ').decode()
  if 'wfb_v6_uplink' in exe:z['native'].append({'pid':process.name,'exe':exe,'cmdline':cmd,'running_exe_sha256':hashlib.sha256((process/'exe').read_bytes()).hexdigest()})
 except (OSError,PermissionError):pass
z['artifact_hashes']={}
for f in ['/usr/bin/wfb_v6_uplink','/srv/wfb-ng-fl/deploy-701963a/source/wfb_v6_uplink','/srv/wfb-ng-fl/deploy-701963a/source/src/v6_uplink.cpp']:
 q=pathlib.Path(f);z['artifact_hashes'][f]=hashlib.sha256(q.read_bytes()).hexdigest() if q.exists() else None
z['backgrounds']=run(['pgrep','-af','diff-experiment|capture-phase|capture-tun|deploy-four|deploy-node']);z['iw']=run(['iw','dev']);z['compiler']=run(['g++','--version']);z['unit_state']=run(['systemctl','is-active',unit]);print(json.dumps(z,ensure_ascii=False,indent=2))
