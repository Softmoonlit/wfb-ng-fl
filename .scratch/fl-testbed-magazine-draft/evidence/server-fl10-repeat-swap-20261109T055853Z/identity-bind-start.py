import pathlib,json,importlib.util,concurrent.futures
ROOT=pathlib.Path(__file__).parent
s=importlib.util.spec_from_file_location('prep',ROOT.parent/'server-fl10-revert-preparation-20261109T051909Z/prepare.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
def save(n,x):(ROOT/n).write_text(json.dumps(x,indent=2))
rows=json.loads((ROOT/'before-nic-identities.json').read_text())
expected={'fl00':'fc:a3:86:b3:86:72','fl10':'fc:22:1c:10:01:19','fl05':'fc:22:1c:10:01:57'}
assert all(x['returncode']==0 and len(x['result']['interfaces'])==1 and x['result']['interfaces'][0]['mac']==expected[x['host']] and x['result']['interfaces'][0]['driver'].endswith('/rtl88xxau_wfb') for x in rows)
assert all(not x['result']['native'] and 'ActiveState=inactive' in x['result']['service'] for x in rows if x['host']!='fl05')
save('identity-gate.json',{'ok':True,'expected':expected})
CODE=r'''import pathlib,json,subprocess,socket,os
host=socket.gethostname();unit='wfb-fl-server-daemon.service' if host=='fl00' else 'wfb-fl-client-daemon.service'
old='wlxfc221c100119' if host=='fl00' else 'wlxfca386b38672';new='wlxfca386b38672' if host=='fl00' else 'wlxfc221c100119'
assert pathlib.Path('/sys/class/net',new,'address').read_text().strip()==EXPECTED
p=pathlib.Path('/etc/systemd/system',unit+'.d/zzzzz-heartbeat-release.conf');before=p.read_text();assert before.count('--interface '+old)==1
a=pathlib.Path('/srv/wfb-ng-fl/releases/heartbeat-1cd6264d5d6b-20261108T115743Z/actualargs.json');ab=a.read_text();av=json.loads(ab);assert av[av.index('--interface')+1]==old
config=pathlib.Path('/etc/wfb-ng-fl/server.json' if host=='fl00' else '/etc/wfb-ng-fl/node.json');cb=config.read_text()
after=before.replace('--interface '+old,'--interface '+new);av[av.index('--interface')+1]=new;aa=json.dumps(av,indent=2)+'\n'
p.write_text(after);a.write_text(aa)
subprocess.run(['systemctl','daemon-reload'],check=True,timeout=8)
assert config.read_text()==cb
print(json.dumps(dict(host=host,dropin=str(p),before=before,after=after,actualargs_path=str(a),actualargs_before=ab,actualargs_after=aa,config_path=str(config),config_before=cb,config_after=config.read_text())))
'''
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as e:changes=list(e.map(lambda h:m.invoke(h,'EXPECTED='+repr(expected[h])+'\n'+CODE),['fl00','fl10']))
save('explicit-interface-binding-changes.json',changes);assert all(x['returncode']==0 for x in changes),changes
START=r'''import subprocess,json,socket
unit='wfb-fl-server-daemon.service' if socket.gethostname()=='fl00' else 'wfb-fl-client-daemon.service'
p=subprocess.run(['systemctl','start',unit],capture_output=True,text=True,timeout=15)
s=subprocess.run(['systemctl','show',unit,'-p','MainPID','-p','ActiveState','-p','SubState'],capture_output=True,text=True,timeout=3)
print(json.dumps(dict(start_returncode=p.returncode,start_stdout=p.stdout,start_stderr=p.stderr,service=s.stdout)))
'''
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as e:starts=list(e.map(lambda h:m.invoke(h,START),['fl00','fl10']))
save('standard-service-start-results.json',starts);assert all(x['returncode']==0 and x['result']['start_returncode']==0 for x in starts),starts
print(json.dumps({'binding_restored':True,'starts':starts}))
