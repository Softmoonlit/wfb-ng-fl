import os,sys,json,subprocess,concurrent.futures,pathlib,hashlib
ROOT=pathlib.Path(__file__).resolve().parent
HOSTS=['fl00','fl01','fl02','fl04','fl05','fl06','fl07','fl08','fl09','fl10']
PROBE=r'''
import os,json,subprocess,shutil,pathlib,hashlib,platform

def run(a):
 p=subprocess.run(a,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=8);return {'rc':p.returncode,'out':p.stdout}
r={'host':platform.node(),'arch':platform.machine(),'path':os.environ.get('PATH'),'bins':{},'processes':[],'daemon_paths':{}}
for d in pathlib.Path('/proc').iterdir():
 if not d.name.isdigit():continue
 try:
  args=(d/'cmdline').read_bytes().split(b'\0'); args=[x.decode(errors='replace') for x in args if x]
  if not args or '-c' in args:continue
  if pathlib.Path(args[0]).name in ('uftp','uftpd') or any(('node_agent.py' in x or 'runner.py' in x or 'pytest' in x) for x in args[:3]):r['processes'].append({'pid':int(d.name),'args':args})
  if any('daemon.py' in x or 'wfb-fl-' in x for x in args) and pathlib.Path(args[0]).name.startswith('python'):
   env=dict(x.split(b'=',1) for x in (d/'environ').read_bytes().split(b'\0') if b'=' in x);p=env.get(b'PATH',b'').decode();r['daemon_paths'][d.name]={'PATH':p,'which':{b:shutil.which(b,path=p) for b in ('uftp','uftpd')}}
 except (OSError,ProcessLookupError):pass
for b in ('uftp','uftpd'):
 p=shutil.which(b);r['bins'][b]={'which':p,'real':os.path.realpath(p),'sha256':hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest(),'version':{'out':next((s.decode() for s in pathlib.Path(p).read_bytes().split(b'\0') if s.startswith(b'UFTP version ')),'unknown')},'ldd':run(['ldd',p]),'file':run(['file',p])}
r['units']=run(['systemctl','list-units','--state=running','--no-pager','--no-legend'])
print(json.dumps(r))
'''
def remote(h,code):
 cmd=['sudo','-n','python3','-c',code] if h=='fl00' else ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=5',h,'sudo -n python3 -c '+__import__('shlex').quote(code)]
 p=subprocess.run(cmd,capture_output=True,text=True,timeout=25)
 if p.returncode:raise RuntimeError(h+': '+p.stderr)
 return json.loads(p.stdout)
def audit(stage):
 with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
  results=dict(zip(HOSTS,pool.map(lambda h:remote(h,PROBE),HOSTS)))
 (ROOT/(stage+'.json')).write_text(json.dumps(results,indent=2))
 for h,r in results.items():print(h,{b:(v['which'],v['version']['out'].splitlines()[:1],v['sha256'][:12]) for b,v in r['bins'].items()},'active=',r['processes'],'daemon=',r['daemon_paths'])
 return results
if __name__=='__main__':audit(sys.argv[1] if len(sys.argv)>1 else 'before')
