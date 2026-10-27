#!/usr/bin/env python3
import os, sys, subprocess, time, json, hashlib, pathlib, struct, importlib.util, importlib.metadata

def run(args, timeout=12):
    print('\n命令:', ' '.join(args), flush=True)
    try:
        p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=timeout)
        print(p.stdout, end='' if p.stdout.endswith('\n') else '\n',flush=True); print('退出码:',p.returncode,flush=True)
    except Exception as e: print('失败:',repr(e),flush=True)
print('采集 UTC:',time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),flush=True)
for args in [['hostname'],['id'],['uname','-a'],['cat','/etc/os-release'],['cat','/etc/nv_tegra_release'],['sudo','-n','true'],['ip','-brief','address'],['iw','dev'],['lsusb'],['lsusb','-t'],['dpkg-query','-W','nvidia-jetpack','nvidia-l4t-core','cuda-toolkit-12-6','libcudnn9-cuda-12'],['systemctl','show','wfb-fl-server-daemon.service','wfb-fl-client-daemon.service','-p','Id','-p','ActiveState','-p','ExecStart','-p','FragmentPath','-p','User','-p','WorkingDirectory'],['systemctl','cat','wfb-fl-server-daemon.service','wfb-fl-client-daemon.service']]: run(args)
for name in ['/etc/wfb-ng-fl/node.json','/etc/wfb-ng-fl/server.json','/etc/wfb-ng/fl-client.json','/etc/wfb-ng/fl-server.json','/usr/local/cuda/version.json']:
    run(['sudo','-n','cat',name])
for exe in ['python3','wfb-fl-server','wfb-fl-client','wfb-fl-server-daemon','wfb-fl-client-daemon','wfb_v6_uplink','uftp','uftpd','nvcc','nvidia-smi']:
    run(['bash','-c','command -v "$1"','probe',exe])
run(['nvidia-smi']); run(['/usr/local/cuda/bin/nvcc','--version'])
for name in ['88XXau_wfb','rtl88xxau_wfb']: run(['modinfo',name])
print('\nPython:',sys.version,'解释器:',sys.executable,flush=True)
for dist in ['wfb-ng','torch','torchvision','numpy','pyroute2']:
    try: print('安装发行版',dist,importlib.metadata.version(dist),flush=True)
    except Exception as e: print('发行版查询失败',dist,repr(e),flush=True)
# 查找安装位置而不从项目工作目录导入。
try:
    import wfb_ng
    print('wfb_ng 安装位置:',wfb_ng.__file__,flush=True)
    base=pathlib.Path(wfb_ng.__file__).parent
    for rel in ['fl/build_identity.json','fl/mnist.py','fl/mnist_data.py','fl/reference_model.py','fl/client_daemon.py','fl/server_daemon.py']:
        p=base/rel
        try:
            b=p.read_bytes(); print('安装文件:',p,'字节:',len(b),'sha256:',hashlib.sha256(b).hexdigest(),flush=True)
            if p.suffix=='.json': print(b.decode(),flush=True)
        except Exception as e: print('安装文件失败:',p,repr(e),flush=True)
except Exception as e: print('安装包导入失败:',repr(e),flush=True)
print('\nGPU 加法子进程（限时 45 秒，无训练）',flush=True)
run([sys.executable,'-B','-c', '''import sys,json,time,torch
print('python',sys.version,'executable',sys.executable,flush=True)
print('torch',torch.__version__,'path',torch.__file__,'cuda',torch.version.cuda,'cudnn',torch.backends.cudnn.version(),flush=True)
print('cuda_available',torch.cuda.is_available(),'device_count',torch.cuda.device_count(),flush=True)
assert torch.cuda.is_available(), 'CUDA unavailable; no CPU fallback'
p=torch.cuda.get_device_properties(0)
print('device',p,'total_memory_bytes',p.total_memory,'capability',torch.cuda.get_device_capability(0),flush=True)
t=time.monotonic(); a=torch.tensor([1.,2.,3.],device='cuda'); b=a+torch.tensor([4.,5.,6.],device='cuda'); torch.cuda.synchronize()
print('actual_device',b.device,'addition_result',b.cpu().tolist(),'elapsed_s',time.monotonic()-t,flush=True)
assert b.cpu().tolist()==[5.,7.,9.]
print('GPU_ADDITION_PASS',flush=True)
'''],45)
print('\n限定数据检索：/var/lib/wfb-ng、/home/fl、/srv/wfb-ng-fl；不跟随目录符号链接，排除 .git/.cache/node_modules',flush=True)
roots=['/var/lib/wfb-ng','/home/fl','/srv/wfb-ng-fl']; start=time.monotonic(); count=0
for root in roots:
    if not os.path.isdir(root): print('目录不存在:',root,flush=True); continue
    def error(e): print('目录读取失败:',repr(e),flush=True)
    for parent, dirs, files in os.walk(root,onerror=error,followlinks=False):
        dirs[:]=[d for d in dirs if d not in ['.git','.cache','node_modules','__pycache__']]
        if time.monotonic()-start>35: print('数据检索超时，结果不完整',flush=True); break
        for f in files:
            if any(s in f.lower() for s in ['idx3','idx1','mnist']):
                p=pathlib.Path(parent)/f; count+=1
                try:
                    st=p.stat(); print('候选:',p,'字节:',st.st_size,flush=True)
                    if ('idx3' in f or 'idx1' in f) and st.st_size<=60000000:
                        data=p.read_bytes(); print('sha256:',hashlib.sha256(data).hexdigest(),'头16字节hex:',data[:16].hex(),'来源状态:未凭结构或摘要认定官方',flush=True)
                except Exception as e: print('候选读取失败:',p,repr(e),flush=True)
print('候选数量:',count,'检索耗时秒:',time.monotonic()-start,flush=True)
