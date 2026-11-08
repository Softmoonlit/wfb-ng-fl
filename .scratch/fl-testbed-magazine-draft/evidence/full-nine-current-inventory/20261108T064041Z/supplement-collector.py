import concurrent.futures,json,pathlib,subprocess
root=pathlib.Path('.scratch/fl-testbed-magazine-draft/evidence/full-nine-current-inventory/20261108T064041Z')
manifest=json.loads((root/'fl00.json').read_text())['manifest']
probe='''import hashlib,json,pathlib,subprocess,socket
P=pathlib.Path
m=json.loads(%r)
r={'hostname':socket.gethostname(),'disk_hashes':{},'disk_diff_vs_fl00_manifest':[]}
for name,expected in m['files'].items():
 try:
  digest=hashlib.sha256((P(m['installed'])/name).read_bytes()).hexdigest();r['disk_hashes'][name]=digest
  if digest!=expected:r['disk_diff_vs_fl00_manifest'].append(name)
 except OSError as e:r['disk_hashes'][name]={'error':str(e)};r['disk_diff_vs_fl00_manifest'].append(name)
p=P(m['installed'])/'build_identity.json'
try:r['disk_build_identity']=json.loads(p.read_text())
except Exception as e:r['disk_build_identity_error']=repr(e)
p=subprocess.run(['journalctl','-b','-u','wfb-fl-client-daemon.service','--no-pager','-n','25','-o','short-iso'],capture_output=True,text=True,timeout=3)
r['journal']={'exit':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
print(json.dumps(r,ensure_ascii=False))
'''%json.dumps(manifest)
(root/'supplement-probe.py').write_text(probe)
def collect(host):
 try:
  p=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=4',host,'sudo -n timeout 8s python3 -'],input=probe,text=True,capture_output=True,timeout=10)
  (root/(host+'-supplement-transport.json')).write_text(json.dumps({'exit':p.returncode,'stdout':p.stdout,'stderr':p.stderr},ensure_ascii=False,indent=2))
  d=json.loads(p.stdout);(root/(host+'-supplement.json')).write_text(json.dumps(d,ensure_ascii=False,indent=2));print(host,d['disk_diff_vs_fl00_manifest'],d.get('disk_build_identity'));print(d['journal']['stdout'][-2600:] if host=='fl05' else '')
 except Exception as e:(root/(host+'-supplement-error.txt')).write_text(repr(e));print(host,repr(e))
with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:list(pool.map(collect,['fl05','fl07','fl08','fl09','fl10']))
