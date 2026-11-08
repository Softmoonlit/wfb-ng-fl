import audit,json,pathlib,base64,concurrent.futures
R=audit.ROOT
before=json.loads((R/'before.json').read_text());ref=before['fl00']['bins']
art={b:base64.b64encode(pathlib.Path(v['real']).read_bytes()).decode() for b,v in ref.items()}
for b,v in ref.items():
 p=R/'artifact'/b;p.parent.mkdir(exist_ok=True);p.write_bytes(base64.b64decode(art[b]))
targets=[h for h,r in before.items() if any(r['bins'][b]['sha256']!=ref[b]['sha256'] for b in ref)]
CODE=r'''
import os,pathlib,json,base64,hashlib,subprocess,shutil,platform
art=__ARTIFACT_DATA__
ref=__REFERENCE_DATA__
assert platform.machine()=='aarch64'
backup=pathlib.Path('/var/backups/uftp-nine-unification-20261108T075403Z');backup.mkdir(parents=True,exist_ok=False)
r={'backup':str(backup),'bins':{}}
for b in art:
 p=pathlib.Path(shutil.which(b));assert str(p)=='/usr/bin/'+b
 stage=p.with_name(b+'.unification-20261108T075403Z');stage.write_bytes(base64.b64decode(art[b]));stage.chmod(0o755)
 assert hashlib.sha256(stage.read_bytes()).hexdigest()==ref[b]['sha256']
 dep=subprocess.run(['ldd','-r',str(stage)],capture_output=True,text=True,timeout=8)
 assert dep.returncode==0 and 'not found' not in dep.stdout+dep.stderr and 'undefined symbol' not in dep.stdout+dep.stderr,dep.stdout+dep.stderr
 usage=subprocess.run([str(stage),'-V'],capture_output=True,text=True,timeout=5)
 assert 'Usage' in usage.stdout+usage.stderr or ' -R ' in usage.stdout+usage.stderr or ' -d ' in usage.stdout+usage.stderr
 shutil.copy2(p,backup/b)
 r['bins'][b]={'old_sha':hashlib.sha256((backup/b).read_bytes()).hexdigest(),'ldd_r':dep.stdout+dep.stderr,'execution_rc':usage.returncode,'execution':usage.stdout+usage.stderr,'destination':str(p)}
# Recheck transfer/test activity immediately before atomic replacements.
for proc in pathlib.Path('/proc').iterdir():
 if not proc.name.isdigit():continue
 try:args=[x.decode(errors='replace') for x in (proc/'cmdline').read_bytes().split(b'\0') if x]
 except OSError:continue
 if not args or '-c' in args:continue
 assert pathlib.Path(args[0]).name not in ('uftp','uftpd') and not any('node_agent.py' in x or 'runner.py' in x or 'pytest' in x for x in args[:3]),args
for b in art:os.replace('/usr/bin/'+b+'.unification-20261108T075403Z','/usr/bin/'+b)
print(json.dumps(r))
'''.replace('__ARTIFACT_DATA__',repr(art)).replace('__REFERENCE_DATA__',repr(ref))
def deploy(h):
 # Send code on stdin to avoid SSH command argument length limits.
 import subprocess
 p=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=5',h,'sudo -n python3 -'],input=CODE,capture_output=True,text=True,timeout=30)
 (R/(h+'-deployment.json')).write_text(json.dumps({'rc':p.returncode,'stdout':p.stdout,'stderr':p.stderr},indent=2))
 assert p.returncode==0,(h,p.stderr)
 print(h,'deployed and backed up')
with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:list(pool.map(deploy,targets))
after=audit.audit('after')
for h,r in after.items():
 assert not r['processes']
 for b in ref:
  assert r['bins'][b]['sha256']==ref[b]['sha256']
  assert '5.0.3' in r['bins'][b]['version']['out']
  assert 'not found' not in r['bins'][b]['ldd']['out']
  assert all(d['which'][b]=='/usr/bin/'+b for d in r['daemon_paths'].values())
(R/'RESULT.json').write_text(json.dumps({'verified':True,'hosts':audit.HOSTS,'replaced':targets,'reference':ref,'daemon_pids_unchanged':all(set(before[h]['daemon_paths'])==set(after[h]['daemon_paths']) for h in audit.HOSTS)},indent=2))
