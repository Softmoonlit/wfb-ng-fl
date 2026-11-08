import concurrent.futures,json,pathlib,subprocess
p=pathlib.Path(__file__).parent
mapping=json.loads((p/'actual-mapping.json').read_text())
def update(h):
 before=json.loads((p/(h+'-before.json')).read_text());target='/etc/systemd/system/wfb-fl-client-daemon.service.d/zzzzz-heartbeat-release.conf';old=before['dropins'][target]
 (p/(h+'-dropin-backup.conf')).write_text(old)
 script="""import pathlib,re,shutil,subprocess,json
P=pathlib.Path
spec=SPEC
cards=[c for c in P('/sys/class/net').iterdir() if (c/'device/driver').resolve().name=='rtl88xxau_wfb']
assert len(cards)==1
assert cards[0].name==spec['interface'] and (cards[0]/'address').read_text().strip()==spec['mac']
target=P(TARGET)
old=OLD
assert target.read_text()==old
new,n=re.subn(r'--interface \S+', '--interface '+spec['interface'],old)
assert n==1
backup=target.with_name(target.name+'.card-swap-STAMP.bak')
assert not backup.exists()
shutil.copy2(target,backup)
target.write_text(new)
subprocess.run(['systemctl','daemon-reload'],check=True)
subprocess.run(['systemctl','restart','wfb-fl-client-daemon.service'],check=True,timeout=35)
print(json.dumps({'backup':str(backup),'old':old,'new':new,'restart':'success'}))
""".replace('SPEC',repr(mapping[h])).replace('TARGET',repr(target)).replace('OLD',repr(old)).replace('STAMP',p.name.removeprefix('card-swap-full-nine-'))
 (p/(h+'-rebind-script.py')).write_text(script)
 r=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=5',h,'sudo -n python3 -'],input=script,text=True,capture_output=True,timeout=45)
 (p/(h+'-rebind-result.json')).write_text(json.dumps({'exit':r.returncode,'stdout':r.stdout,'stderr':r.stderr},indent=2));print(h,r.returncode,r.stderr,flush=True)
 assert r.returncode==0
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:list(pool.map(update,mapping))
