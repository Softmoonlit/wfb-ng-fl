import subprocess,sys,os,importlib.metadata as m,hashlib,pathlib,time
print('准备前环境 UTC',time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),flush=True)
for args in [['hostname'],['df','-h','/','/srv','/home/fl'],['cat','/etc/nv_tegra_release'],['dpkg-query','-W','nvidia-l4t-core','cuda-toolkit-12-6','libcudnn9-cuda-12','libcusparselt0','libopenblas0-pthread'],['python3','-m','pip','--version'],['systemctl','show','wfb-fl-server-daemon.service','wfb-fl-client-daemon.service','-p','Id','-p','MainPID','-p','ActiveState'],['bash','-c',"ldconfig -p | rg 'cusparseLt|openblas|libcudnn.so'"],['sudo','-n','find','/home/fl','/var/cache','-maxdepth','5','-type','f','-name','*.whl'],['sudo','-n','ls','-ld','/srv/wfb-ng-fl/mnist']]:
 print('命令',args,flush=True)
 try:
  r=subprocess.run(args,capture_output=True,text=True,timeout=15);print(r.stdout+r.stderr,'退出码',r.returncode,flush=True)
 except Exception as e:print('失败',repr(e),flush=True)
for name in ['numpy','torch','filelock','typing_extensions','networkx','jinja2','fsspec','sympy','mpmath','MarkupSafe','pip','setuptools']:
 try: print('依赖',name,m.version(name),flush=True)
 except Exception as e:print('依赖缺失',name,repr(e),flush=True)
