import concurrent.futures,datetime,json,pathlib,subprocess,time
root=pathlib.Path('.scratch/fl-testbed-magazine-draft/evidence/full-nine-current-inventory')/datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
root.mkdir(parents=True)
probe=r'''
import datetime,glob,hashlib,json,os,pathlib,socket,subprocess
P=pathlib.Path
r={'hostname':socket.gethostname(),'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'boot_id':P('/proc/sys/kernel/random/boot_id').read_text().strip()}
def run(args):
 try:
  p=subprocess.run(args,capture_output=True,text=True,timeout=2);return {'exit':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
 except Exception as e:return {'error':repr(e)}
def read(p):
 try:return P(p).read_text().strip()
 except OSError as e:return {'error':str(e)}
def sha(p):return hashlib.sha256(P(p).read_bytes()).hexdigest()
unit='wfb-fl-'+('server' if r['hostname']=='fl00' else 'client')+'-daemon.service'
r['unit']=unit
r['service']=run(['systemctl','show',unit,'-p','ActiveState','-p','SubState','-p','MainPID','-p','ExecStart'])
props=dict(x.split('=',1) for x in r['service'].get('stdout','').splitlines() if '=' in x)
pid=props.get('MainPID','0');r['main_pid']=pid
if pid!='0':
 try:
  args=P('/proc/'+pid+'/cmdline').read_bytes().decode().strip('\0').split('\0');r['daemon_cmdline']=args
  for arg in args:
   if arg.endswith('/daemon.py'):
    release=P(arg).parent;r['release']=str(release)
    for name in ['manifest','loaded']:
     try:r[name]=json.loads((release/(name+'.json')).read_text())
     except Exception as e:r[name]={'error':repr(e)}
    m=r.get('manifest',{});l=r.get('loaded',{});r['loaded_pid_matches']=l.get('pid')==int(pid);r['loaded_commit_matches_manifest']=l.get('commit')==m.get('commit')
    mismatches=[]
    for name,digest in m.get('files',{}).items():
     try:
      if sha(P(m['installed'])/name)!=digest:mismatches.append(name)
     except Exception as e:mismatches.append(name+':'+repr(e))
    r['installed_manifest_mismatches']=mismatches
 except Exception as e:r['daemon_error']=repr(e)
r['natives']=[]
for f in P('/proc').glob('[0-9]*/cmdline'):
 try:
  args=f.read_bytes().decode().strip('\0').split('\0')
  if args and P(args[0]).name=='wfb_v6_uplink':
   r['natives'].append({'pid':int(f.parent.name),'cmdline':args,'exe':os.readlink(f.parent/'exe'),'exe_sha256':sha(f.parent/'exe')})
 except (OSError,UnicodeError):pass
r['interfaces']=[]
for card in P('/sys/class/net').iterdir():
 if (card/'device/driver').resolve().name=='rtl88xxau_wfb':
  r['interfaces'].append({'interface':card.name,'driver':str((card/'device/driver').resolve()),'mac':read(card/'address'),'iw_info':run(['iw','dev',card.name,'info'])})
r['nvpmodel_config']=str(P('/etc/nvpmodel.conf').resolve())
r['nvpmodel_q']=run(['/usr/sbin/nvpmodel','-q'])
r['nvpmodel_verbose']=run(['/usr/sbin/nvpmodel','-q','--verbose'])
r['power_service']=run(['systemctl','show','nvpmodel.service','-p','Result','-p','ExecMainStatus'])
r['cpu_online']=read('/sys/devices/system/cpu/online')
r['limits']={}
patterns=['/sys/devices/system/cpu/cpufreq/policy*/scaling_min_freq','/sys/devices/system/cpu/cpufreq/policy*/scaling_max_freq','/sys/devices/platform/*gpu*/devfreq*/min_freq','/sys/devices/platform/*gpu*/devfreq*/max_freq','/sys/class/devfreq/*gpu*/min_freq','/sys/class/devfreq/*gpu*/max_freq','/sys/kernel/nvpmodel_clk_cap/emc','/sys/kernel/debug/bpmp/debug/clk/emc/rate','/sys/kernel/debug/bpmp/debug/clk/emc/min_rate','/sys/kernel/debug/bpmp/debug/clk/emc/max_rate','/sys/kernel/debug/bpmp/debug/clk/emc/mrq_rate_locked']
for pattern in patterns:
 for path in glob.glob(pattern):r['limits'][path]=read(path)
print(json.dumps(r,ensure_ascii=False))
'''
(root/'probe.py').write_text(probe)
hosts=['fl00','fl01','fl02','fl04','fl05','fl06','fl07','fl08','fl09','fl10']
def collect(host):
 cmd=['sudo','-n','python3','-'] if host=='fl00' else ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=4','-o','ServerAliveInterval=3','-o','ServerAliveCountMax=1',host,'sudo -n timeout 8s python3 -']
 start=time.monotonic()
 try:
  p=subprocess.run(cmd,input=probe,text=True,capture_output=True,timeout=10)
  record={'target':host,'elapsed_s':time.monotonic()-start,'exit':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
 except subprocess.TimeoutExpired as e:
  record={'target':host,'elapsed_s':time.monotonic()-start,'error':'SSH/process exceeded 10 seconds','stdout':(e.stdout or b'').decode() if isinstance(e.stdout,bytes) else e.stdout,'stderr':(e.stderr or b'').decode() if isinstance(e.stderr,bytes) else e.stderr}
 (root/(host+'-transport.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
 try:
  d=json.loads(record.get('stdout') or '');(root/(host+'.json')).write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
  print(host,d['hostname'],d.get('main_pid'),d.get('loaded',{}).get('commit'),[x['exe_sha256'] for x in d['natives']],flush=True)
 except Exception:print(host,'FAILED',record,flush=True)
 return record
with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:records=list(pool.map(collect,hosts))
(root/'scope.json').write_text(json.dumps({'hosts':hosts,'excluded':['fl03'],'read_only':True,'per_session_timeout_seconds':10,'records':[{k:v for k,v in x.items() if k not in ('stdout','stderr')} for x in records]},indent=2)+'\n')
print('EVIDENCE',root)
