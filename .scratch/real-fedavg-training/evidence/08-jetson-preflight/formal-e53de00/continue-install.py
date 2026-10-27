import subprocess,pathlib,time,os
p=pathlib.Path(__file__).resolve().parent
script=(p/'deploy-node.py').read_bytes()
def remote(node,ip,role,phase):
 log=p/f'{node}-{phase}-continuation.txt';cmd=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=7',f'fl@192.168.108.{ip}','sudo -n env PYTHONDONTWRITEBYTECODE=1 python3 -B - '+role+' '+phase]
 with log.open('wb') as f:
  r=subprocess.run(cmd,input=script,stdout=f,stderr=subprocess.STDOUT,timeout=240);f.write(f'\n退出码 {r.returncode}\n'.encode());r.check_returncode()
for n,ip in [('fl01','201'),('fl02','202'),('fl04','204')]:remote(n,ip,'client','install-config')
# fl00 only rerun verification without any mutation.
with (p/'fl00-identity-retry.txt').open('wb') as f:
 r=subprocess.run(['sudo','-n','env','PYTHONPATH=','/usr/bin/python3','-B','-c',"import pathlib,json,wfb_ng; from wfb_ng.fl.mnist import client_entry,aggregate_entry,evaluate_entry; p=pathlib.Path(wfb_ng.__file__).parent/'fl/build_identity.json'; d=json.loads(p.read_text()); print(wfb_ng.__file__,d); assert d['commit']=='e53de0076c6ed118d824a2d31261e75de3223333'"],cwd='/tmp',stdout=f,stderr=subprocess.STDOUT,timeout=30);f.write(f'\n退出码 {r.returncode}\n'.encode());r.check_returncode()
print('CONTINUATION_INSTALL_PASS')
