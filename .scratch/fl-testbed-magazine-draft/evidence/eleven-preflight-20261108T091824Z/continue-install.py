import pathlib,sys,json,subprocess,hashlib
E=pathlib.Path(__file__).parent;sys.path.insert(0,str(E));from run import execute
base=pathlib.Path('.scratch/heartbeat-restart/heartbeat-98dda459fb9a-20261108T073921Z');state=json.loads((base/'state.json').read_text());rid=state['release_id'];stage='/tmp/'+rid
commands=[['ssh','fl03','mkdir -p '+stage],['scp',str(base/'source.tar'),'/usr/bin/wfb_v6_uplink','/usr/bin/uftp','/usr/bin/uftpd','fl03:'+stage+'/']]
for i,c in enumerate(commands):
 p=subprocess.run(c,capture_output=True,text=True,timeout=30);(E/('transfer-'+str(i)+'.json')).write_text(json.dumps({'command':c,'exit':p.returncode,'stdout':p.stdout,'stderr':p.stderr},indent=2));assert p.returncode==0
code=f'''import pathlib,subprocess,shutil,hashlib,json,tarfile,ast
stage=pathlib.Path({stage!r});root=pathlib.Path('/srv/wfb-ng-fl/releases')/{rid!r}
assert not root.exists()
assert hashlib.sha256((stage/'source.tar').read_bytes()).hexdigest()=={state['archive_sha']!r}
expected={{'wfb_v6_uplink':{state['native_sha']!r},'uftp':'71d9c15497b3bd4286a2c60c9d37871a50b7032793eae8d4bf7b3b8977e015e3','uftpd':'0d7a31c6cbf783ad5ecced0bdce6705c30530a258fdf605d555c748827aa85b9'}}
for n,h in expected.items():assert hashlib.sha256((stage/n).read_bytes()).hexdigest()==h
identity=pathlib.Path('/etc/wfb-ng-fl/node.json').read_bytes();assert json.loads(identity)=={{'node_id':3,'tun_ip':'10.80.0.13/24'}}
iface='wlx5cffffaf6d8c';p=pathlib.Path('/sys/class/net')/iface;assert (p/'address').read_text().strip()=='5c:ff:ff:af:6d:8c' and (p/'device/driver').resolve().name=='rtl88xxau_wfb'
root.mkdir(parents=True);backup=root/'backup';backup.mkdir();shutil.copytree('/etc/wfb-ng-fl',backup/'config');shutil.copytree('/usr/local/lib/python3.10/dist-packages/wfb_ng/fl',backup/'fl')
for n in expected:shutil.copy2('/usr/bin/'+n,backup/n)
subprocess.run(['systemctl','stop','wfb-fl-client-daemon.service'],check=True,timeout=30)
assert subprocess.check_output(['systemctl','show','wfb-fl-client-daemon.service','-p','MainPID','--value'],text=True).strip()=='0'
for n in expected:shutil.copy2(stage/n,'/usr/bin/'+n)
source=root/'source';source.mkdir()
with tarfile.open(stage/'source.tar') as tar:
 members=tar.getmembers();assert all(not pathlib.Path(m.name).is_absolute() and '..' not in pathlib.Path(m.name).parts and (m.isfile() or m.isdir()) for m in members);tar.extractall(source)
shutil.copy2(stage/'wfb_v6_uplink',source/'wfb_v6_uplink');(root/'interface').write_text(iface+'\\n')
files=sorted(str(p.relative_to(source/'wfb_ng/fl')) for p in (source/'wfb_ng/fl').rglob('*') if p.is_file() and p.suffix in {{'.py','.html','.css','.js'}})
for n in files:
 if n.endswith('.py'):ast.parse((source/'wfb_ng/fl'/n).read_text())
(root/'runtime-files.json').write_text(json.dumps(files))
assert pathlib.Path('/etc/wfb-ng-fl/node.json').read_bytes()==identity
print(json.dumps({{'root':str(root),'iface':iface,'files':files,'binaries':expected}}))
'''
r=execute('fl03','prepare-authorized',code);print('prepare',r['exit'],r['stderr']);assert r['exit']==0
helper=(base/'release-online-node.py').read_text();old="assert host in {'fl00', 'fl01', 'fl02', *(f'fl{i:02d}' for i in range(4, 11))}";assert old in helper;helper=helper.replace(old,"assert host == 'fl03'")
(E/'fl03-release-helper.py').write_text(helper)
for phase in ['install','start']:
 code='import sys\nsys.argv='+repr(['-',phase,state['commit'],rid,state['archive_sha'],state['native_sha']])+'\n'+helper
 r=execute('fl03',phase,code);print(phase,r['exit'],r['stderr']);assert r['exit']==0
