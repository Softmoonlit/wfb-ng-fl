import sys,os,time,json,hashlib,pathlib,inspect,math,struct,subprocess,importlib.metadata as metadata
import numpy as np
import torch
from wfb_ng.fl.reference_model import MNISTReferenceModel
print('固定模型 GPU 检查 UTC',time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),flush=True)
print('用途：本地环境单批次前向/反向/SGD检查；不启动daemon作业，不写模型或update',flush=True)
torch.set_num_threads(1)
assert torch.cuda.is_available(),'禁止CPU回退'
print('解释器',sys.executable,'torch路径',torch.__file__,'torch',torch.__version__,'CUDA',torch.version.cuda,'cuDNN',torch.backends.cudnn.version(),'numpy',np.__version__,flush=True)
print('固定模型来源',inspect.getfile(MNISTReferenceModel),'源文件sha256',hashlib.sha256(pathlib.Path(inspect.getfile(MNISTReferenceModel)).read_bytes()).hexdigest(),flush=True)
print('固定模型类代码\n'+inspect.getsource(MNISTReferenceModel),flush=True)
for name in ['numpy','torch','filelock','typing_extensions','networkx','jinja2','fsspec','sympy','mpmath']:
 try:print('依赖',name,metadata.version(name),flush=True)
 except Exception as e:print('依赖元数据失败',name,repr(e),flush=True)
prop=torch.cuda.get_device_properties(0)
print('GPU',prop.name,'total_memory_bytes',prop.total_memory,'capability',(prop.major,prop.minor),'SM_count',prop.multi_processor_count,'cudnn_enabled',torch.backends.cudnn.enabled,'cudnn_available',torch.backends.cudnn.is_available(),flush=True)
# 使用刚通过来源/完整性检查的官方训练文件前64个样本；这只是一次环境运算检查。
root=pathlib.Path('/srv/wfb-ng-fl/mnist')
images=(root/'train-images-idx3-ubyte').read_bytes(); labels=(root/'train-labels-idx1-ubyte').read_bytes()
assert struct.unpack('>IIII',images[:16])==(2051,60000,28,28)
assert struct.unpack('>II',labels[:8])==(2049,60000)
x=torch.from_numpy(np.frombuffer(images,dtype=np.uint8,offset=16,count=64*784).copy()).reshape(64,1,28,28).to(device='cuda',dtype=torch.float32).div_(255)
y=torch.from_numpy(np.frombuffer(labels,dtype=np.uint8,offset=8,count=64).copy()).to(device='cuda',dtype=torch.long)
with torch.random.fork_rng(devices=[]):
 torch.manual_seed(2026);model=MNISTReferenceModel();generator=torch.Generator(device='cpu').manual_seed(2026)
 for layer in (model.conv1,model.conv2,model.classifier):
  torch.nn.init.xavier_uniform_(layer.weight,generator=generator);torch.nn.init.zeros_(layer.bias)
model=model.to('cuda');model.train()
assert sum(p.numel() for p in model.parameters())==9098
opt=torch.optim.SGD(model.parameters(),lr=0.01,momentum=0,weight_decay=0)
before={n:p.detach().clone() for n,p in model.named_parameters()}
t=time.monotonic();opt.zero_grad(set_to_none=True);output=model(x)
assert output.shape==(64,10) and output.is_cuda and torch.isfinite(output).all()
loss=torch.nn.functional.cross_entropy(output,y);assert loss.is_cuda and torch.isfinite(loss)
loss.backward();grads={n:p.grad.detach().clone() for n,p in model.named_parameters()}
for n,p in model.named_parameters():assert p.is_cuda and p.grad is not None and p.grad.is_cuda and torch.isfinite(p.grad).all(),n
opt.step();torch.cuda.synchronize();elapsed=time.monotonic()-t
changed=0;max_change=0.;max_sgd_error=0.
for n,p in model.named_parameters():
 assert torch.isfinite(p).all(),n
 delta=(p-before[n]).abs().max().item();err=(p-(before[n]-0.01*grads[n])).abs().max().item()
 if delta>0:changed+=1
 max_change=max(max_change,delta);max_sgd_error=max(max_sgd_error,err)
 assert torch.allclose(p,before[n]-0.01*grads[n],rtol=1e-5,atol=1e-7),n
 print('参数',n,'device',p.device,'shape',list(p.shape),'grad_max_abs',grads[n].abs().max().item(),'step_max_abs_change',delta,'sgd_max_abs_error',err,flush=True)
assert changed>0
result={'hostname':subprocess.check_output(['hostname'],text=True).strip(),'torch':torch.__version__,'torch_path':torch.__file__,'cuda':torch.version.cuda,'cudnn':torch.backends.cudnn.version(),'device':str(x.device),'gpu_name':prop.name,'model_parameter_count':9098,'batch_size':64,'optimizer_steps':1,'sample_scope':'official training first 64; environment probe only','loss':loss.item(),'elapsed_s':elapsed,'changed_parameter_tensors':changed,'max_parameter_change':max_change,'max_sgd_error':max_sgd_error,'status':'pass'}
print('GPU_FIXED_MODEL_RESULT '+json.dumps(result,ensure_ascii=False),flush=True)
print('GPU_FORWARD_BACKWARD_OPTIMIZER_PASS',flush=True)
