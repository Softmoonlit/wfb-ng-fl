import torch,time,sys
print('torch',torch.__version__,'cuda',torch.version.cuda,'cudnn',torch.backends.cudnn.version(),flush=True)
assert torch.cuda.is_available()
p=torch.cuda.get_device_properties(0)
for field in ['name','total_memory','major','minor','multi_processor_count']:
 try: print(field,getattr(p,field),flush=True)
 except Exception as e: print('属性失败',field,repr(e),flush=True)
t=time.monotonic()
a=torch.tensor([1.,2.,3.],device='cuda'); b=a+torch.tensor([4.,5.,6.],device='cuda'); torch.cuda.synchronize()
result=b.cpu().tolist(); print('actual_device',b.device,'result',result,'elapsed_s',time.monotonic()-t,flush=True)
assert result==[5.,7.,9.]
print('GPU_ADDITION_PASS',flush=True)
