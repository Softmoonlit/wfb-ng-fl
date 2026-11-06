"""为fl01<-fl08、fl05<-fl10网卡替换保存现场并受控停服。"""
import concurrent.futures
import datetime
import json
import pathlib
import subprocess
import sys
import urllib.request

phase, evidence_arg = sys.argv[1:]
root = pathlib.Path(evidence_arg)
root.mkdir(parents=True, exist_ok=True)
hosts = ['fl01', 'fl05']
script = '''import hashlib,json,pathlib,socket,subprocess,sys
phase=sys.argv[1]
p=pathlib.Path
host=socket.gethostname()
assert host in {'fl01','fl05'}
unit='wfb-fl-client-daemon.service'
def run(args):return subprocess.check_output(args,text=True,timeout=25)
def native():
 result=[]
 for f in p('/proc').glob('[0-9]*/cmdline'):
  try:
   a=f.read_bytes().split(bytes([0]))
   if a and a[0]==b'/usr/bin/wfb_v6_uplink':result.append((f.parent,a))
  except OSError:pass
 return result
if phase=='before':
 pid=int(run(['systemctl','show',unit,'-p','MainPID','--value']).strip())
 assert pid>0
 entries=native();assert len(entries)==1
 proc,args=entries[0]
 iface=args[args.index(b'--air-interface')+1].decode()
 card=p('/sys/class/net')/iface
 assert (card/'device/driver').resolve().name=='rtl88xxau_wfb'
 daemon_args=[s.decode() for s in (p('/proc')/str(pid)/'cmdline').read_bytes().split(bytes([0])) if s]
 wrapper=next(p(a) for a in daemon_args if a.endswith('/daemon.py'))
 loaded=json.loads((wrapper.parent/'loaded.json').read_text())
 assert loaded['pid']==pid and loaded['commit'].startswith('c6659488f649')
 result={'host':host,'kernel_boot_id':p('/proc/sys/kernel/random/boot_id').read_text().strip(),'daemon_pid':pid,'daemon_args':daemon_args,'loaded':loaded,'native_pid':int(proc.name),'native_args':[s.decode() for s in args if s],'native_sha':hashlib.sha256((proc/'exe').read_bytes()).hexdigest(),'interface':iface,'mac':(card/'address').read_text().strip(),'device_path':str((card/'device').resolve()),'driver':str((card/'device/driver').resolve()),'radio':run(['iw','dev',iface,'info']),'nvpmodel':run(['/usr/sbin/nvpmodel','-q']),'journal':run(['journalctl','-u',unit,'_PID='+str(pid),'-n','100','--no-pager','-o','short-monotonic']),'service':run(['systemctl','show',unit,'-p','ExecStart','-p','ActiveState'])}
 assert result['native_sha']=='ad3340e51eecc6e5691a6edb84f26f3a262b518c3555497df62ea6f7865e1e56'
elif phase=='stop':
 subprocess.run(['systemctl','stop',unit],check=True,timeout=30)
 assert run(['systemctl','show',unit,'-p','MainPID','--value']).strip()=='0'
 assert not native()
 result={'host':host,'phase':phase,'main_pid':0,'native_count':0,'active_state':run(['systemctl','show',unit,'-p','ActiveState','--value']).strip()}
else:raise ValueError(phase)
print(json.dumps(result,ensure_ascii=False))
'''
if phase == 'before':
    status = json.load(urllib.request.urlopen('http://127.0.0.1:9090/api/v1/status', timeout=4))
    assert status['server_state'] == 'IDLE' and status.get('active_job') is None
    (root / 'server-before.json').write_text(json.dumps(status, ensure_ascii=False, indent=2) + '\n')
elif phase == 'stop':
    for host in hosts:
        before = json.loads((root / (host + '-before.json')).read_text())
        assert before['exit'] == 0
else:
    raise ValueError(phase)

def action(host):
    command = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=6', host,
               'sudo -n /usr/bin/python3 - ' + phase]
    try:
        proc = subprocess.run(command, input=script, text=True, capture_output=True, timeout=40)
        result = {'host': host, 'exit': proc.returncode, 'stdout': proc.stdout, 'stderr': proc.stderr}
    except Exception as exc:
        result = {'host': host, 'exit': -1, 'error': repr(exc)}
    (root / (host + '-' + phase + '.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return result

with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    results = list(pool.map(action, hosts))
for result in results:
    print(result['host'], phase, result['exit'])
    if result['exit'] == 0:
        data = json.loads(result['stdout'])
        print(json.dumps({k: data[k] for k in ['mac', 'interface', 'device_path', 'active_state'] if k in data}))
    else:
        print(result)
assert all(r['exit'] == 0 for r in results), '任一目标失败即停止推进'
