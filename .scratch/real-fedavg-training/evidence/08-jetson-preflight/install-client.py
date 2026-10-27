import pathlib,sys,hashlib,subprocess,os,json,time,shutil
stage=pathlib.Path(sys.argv[1] if len(sys.argv)>1 else '/srv/wfb-ng-fl/install-cache')
print('客户端安装 UTC',time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),flush=True)
checks=json.loads((stage/'install-artifact-manifest.json').read_text())
for name,expected in checks.items():
 p=stage/name; actual=hashlib.sha256(p.read_bytes()).hexdigest();print('制品',name,actual,flush=True);assert actual==expected
assert shutil.disk_usage('/').free>3500000000,'安装可用空间不足 3.5 GB'
l4t=subprocess.check_output(['dpkg-query','-W','-f=${Version}','nvidia-l4t-core'],text=True).strip();assert l4t=='36.4.3-20250107174145'
print('L4T',l4t,flush=True)
def run(args,timeout=180):
 print('命令',args,flush=True);r=subprocess.run(args,timeout=timeout);print('退出码',r.returncode,flush=True);r.check_returncode()
run(['sudo','-n','dpkg','-i',str(stage/'libcusparselt0-direct_0.7.1.0-1_arm64.deb')])
run(['sudo','-n','python3','-m','pip','install','--no-cache-dir','--no-index','--find-links',str(stage),str(stage/'torch-2.5.0a0+872d972e41.nv24.08-cp310-cp310-linux_aarch64.whl')])
run(['python3','-B','-c',"import torch,numpy,sys; print(sys.executable,torch.__file__,torch.__version__,torch.version.cuda,torch.backends.cudnn.version(),numpy.__version__); assert torch.cuda.is_available()"])
run(['df','-h','/'])
print('CLIENT_INSTALL_PASS',flush=True)
