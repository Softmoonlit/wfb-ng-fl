#!/usr/bin/env python3
"""09只读取证。经python stdin在各板执行；不导入工作区产品代码。"""
import subprocess, json, time, pathlib, hashlib, os
os.chdir('/tmp')

def run(args):
    t=time.monotonic()
    try:
        p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=15)
        return {'args':args,'rc':p.returncode,'elapsed':time.monotonic()-t,'output':p.stdout}
    except Exception as e: return {'args':args,'error':repr(e)}
r={'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'commands':[]}
for a in [['hostname'],['uname','-a'],['cat','/proc/loadavg'],['cat','/proc/stat'],['cat','/proc/diskstats'],['ps','-eo','pid,etimes,pcpu,pmem,args','--sort=-pcpu'],['iw','dev'],['ip','-j','address'],['ip','route'],['ss','-lunp'],['lsusb','-t'],['df','-h','/var/tmp'],['systemctl','show','wfb-fl-server-daemon','wfb-fl-client-daemon','-p','MainPID','-p','ActiveState','-p','ExecStart'],['sha256sum','/usr/bin/wfb_v6_uplink'],['pgrep','-a','-x','wfb_v6_uplink'],['uftp','-h'],['uftpd','-h']]:
    r['commands'].append(run(a))
try:
    import wfb_ng
    base=pathlib.Path(wfb_ng.__file__).parent
    r['installed_path']=str(base);r['installed_files']={}
    for rel in ['fl/build_identity.json','fl/server_daemon.py','fl/client_daemon.py','fl/radio.py']:
        p=base/rel
        if p.exists():
            b=p.read_bytes();r['installed_files'][rel]={'sha256':hashlib.sha256(b).hexdigest(),'size':len(b)}
            if rel.endswith('.json'):r['installed_files'][rel]['content']=b.decode()
except Exception as e:r['installed_error']=repr(e)
r['link_processes']=[]
for p in pathlib.Path('/proc').glob('[0-9]*'):
    try:
        args=(p/'cmdline').read_bytes().replace(b'\0',b' ').decode()
        if args.startswith('/usr/bin/wfb_v6_uplink '):
            r['link_processes'].append({'pid':int(p.name),'args':args,'stat':(p/'stat').read_text(),'stdout':os.readlink(p/'fd/1')})
    except (OSError,UnicodeError):pass
print(json.dumps(r,ensure_ascii=False,indent=2))
