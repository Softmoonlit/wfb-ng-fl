import pathlib,subprocess,json,concurrent.futures,datetime
E=pathlib.Path(__file__).parent
probe='''import pathlib,subprocess,json,socket,hashlib,os
r={'host':socket.gethostname()}
def run(a):
 p=subprocess.run(a,text=True,capture_output=True,timeout=20);return {'exit':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
r['service']=run(['systemctl','show','wfb-fl-client-daemon.service','-p','MainPID','-p','ExecStart','-p','ActiveState','-p','SubState'])
r['unit']=run(['systemctl','cat','wfb-fl-client-daemon.service'])
r['identity']=pathlib.Path('/etc/wfb-ng-fl/node.json').read_text()
r['power']=run(['nvpmodel','-q','--verbose']);r['usb']=run(['lsusb','-t']);r['usb_ids']=run(['lsusb']);r['iw']=run(['iw','dev'])
r['cards']=[{'name':p.name,'mac':(p/'address').read_text().strip(),'driver':str((p/'device/driver').resolve()),'usb':str((p/'device').resolve())} for p in pathlib.Path('/sys/class/net').iterdir() if (p/'wireless').exists() or (p/'phy80211').exists()]
r['native_sha']=hashlib.sha256(pathlib.Path('/usr/bin/wfb_v6_uplink').read_bytes()).hexdigest()
r['uftp']={n:run(['sh','-c','command -v '+n+'; '+n+' -h 2>&1 | head -5; sha256sum $(command -v '+n+')']) for n in ['uftp','uftpd']}
r['processes']=run(['ps','-eo','pid,args']);r['releases']=run(['find','/srv/wfb-ng-fl/releases','-maxdepth','2','-name','manifest.json'])
print(json.dumps(r))
'''
def execute(host,label,code):
 cmd=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=6','-o','StrictHostKeyChecking=accept-new',host,'sudo -n /usr/bin/python3 -']
 p=subprocess.run(cmd,input=code,text=True,capture_output=True,timeout=45)
 r={'command':cmd,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'exit':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
 (E/(label+'-'+host+'.json')).write_text(json.dumps(r,indent=2));return r
if __name__=='__main__':
 with concurrent.futures.ThreadPoolExecutor() as pool:
  for host,r in zip(['fl03','fl04'],pool.map(lambda h:execute(h,'inventory',probe),['fl03','fl04'])):
   print(host,r['exit'],r['stderr']);
   if r['exit']==0:
    x=json.loads(r['stdout']);print(json.dumps({k:x[k] for k in ['host','service','cards','native_sha','uftp','releases','identity','power','usb']},indent=2))
