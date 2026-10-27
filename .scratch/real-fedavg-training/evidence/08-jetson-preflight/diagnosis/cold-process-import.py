import sys,time,datetime,json,os,hashlib,pathlib,importlib
start=time.monotonic();wall_start=datetime.datetime.now(datetime.timezone.utc).isoformat()
assert 'torch' not in sys.modules and 'wfb_ng.fl.mnist' not in sys.modules
before={'torch_in_sys_modules':False,'mnist_in_sys_modules':False}
t=time.monotonic()
try:
 module=importlib.import_module('wfb_ng.fl.mnist');elapsed=time.monotonic()-t
 import torch
 result={'hostname':os.uname().nodename,'utc_start':wall_start,'utc_end':datetime.datetime.now(datetime.timezone.utc).isoformat(),'python':sys.version,'executable':sys.executable,'before':before,'mnist_module_path':module.__file__,'mnist_module_sha256':hashlib.sha256(pathlib.Path(module.__file__).read_bytes()).hexdigest(),'torch_version':str(torch.__version__),'torch_path':torch.__file__,'cuda_initialized':torch.cuda.is_initialized(),'direct_mnist_import_seconds':elapsed,'total_probe_seconds':time.monotonic()-start,'meaning':'new process import; OS cache not flushed; no GPU initialization/training','status':'pass'}
 print(json.dumps(result,ensure_ascii=False),flush=True)
except Exception as e:
 print(json.dumps({'hostname':os.uname().nodename,'utc_start':wall_start,'elapsed_s':time.monotonic()-t,'error':repr(e),'status':'failed'},ensure_ascii=False),flush=True);raise
