import subprocess,pathlib,time,json,hashlib,os
base=pathlib.Path(__file__).resolve().parent
wheel=pathlib.Path('/home/fl/torch-2.5.0a0+872d972e41.nv24.08-cp310-cp310-linux_aarch64.whl')
files=[wheel,*sorted((pathlib.Path('/srv/wfb-ng-fl/install-cache')).glob('*.whl')),pathlib.Path('/srv/wfb-ng-fl/install-cache/libcusparselt0-direct_0.7.1.0-1_arm64.deb')]
manifest={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in files};(base/'install-artifact-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
files.extend([base/'install-artifact-manifest.json',base/'install-client.py'])
for node,ip in [('fl04','204')]:
 with (base/(node+'-installation.txt')).open('ab',buffering=0) as log:
  def run(args,timeout):
   log.write(('\nUTC '+time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())+' 命令 '+repr(args)+'\n').encode());r=subprocess.run(args,stdout=log,stderr=subprocess.STDOUT,timeout=timeout);log.write(('\n退出码 '+str(r.returncode)+'\n').encode());r.check_returncode()
  try:
   host='fl@192.168.108.'+ip; stage='/tmp/wfb08-install-cache'
   run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=7',host,'mkdir '+stage],15)
   run(['scp','-o','BatchMode=yes','-o','ConnectTimeout=7',*[str(f) for f in files],host+':'+stage+'/'],240)
   run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=7',host,'cd /tmp && OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1 timeout 240 python3 -B '+stage+'/install-client.py '+stage],260)
   (base/(node+'-installation-result.json')).write_text(json.dumps({'node':node,'status':'pass','utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())})+'\n')
  except Exception as e:
   log.write(('失败 '+repr(e)+'\n').encode());(base/(node+'-installation-result.json')).write_text(json.dumps({'node':node,'status':'failed','error':repr(e)})+'\n')
