import concurrent.futures,json,pathlib,subprocess,sys
root=pathlib.Path(__file__).parent
probe=(root/'probe.py').read_text()
def collect(h):
 p=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=5',h,'sudo -n timeout 15s python3 -'],input=probe,text=True,capture_output=True,timeout=22)
 (root/(h+'-'+sys.argv[1]+'-transport.json')).write_text(json.dumps({'exit':p.returncode,'stdout':p.stdout,'stderr':p.stderr},indent=2))
 d=json.loads(p.stdout);(root/(h+'-'+sys.argv[1]+'.json')).write_text(json.dumps(d,indent=2));print(h,d['interfaces'],d.get('manifest',{}).get('commit'),d.get('dropins'),flush=True)
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:list(pool.map(collect,['fl01','fl02','fl04','fl06','fl07','fl09']))
