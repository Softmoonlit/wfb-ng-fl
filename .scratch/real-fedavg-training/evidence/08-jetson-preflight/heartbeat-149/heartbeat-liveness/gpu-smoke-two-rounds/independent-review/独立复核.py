#!/usr/bin/env python3
"""只读源制品；独立 FP64 加权和 CPU MNIST 评估，不调用生产聚合/评估。"""
import hashlib, io, json, pathlib, struct, subprocess, sys, time
import torch
from torch import nn
from torch.nn import functional as F
OUT=pathlib.Path(__file__).resolve().parent
SMOKE=OUT.parent
JOB='web-7383444963c54f558b721675d592b43b'
BASE=pathlib.Path('/srv/wfb-ng-fl/server')
commands=[]; provenance=[]
def read(p):
    p=pathlib.Path(p)
    cmd=['sudo','cat',str(p)]
    commands.append(cmd)
    data=subprocess.check_output(cmd)
    provenance.append({'path':str(p),'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)})
    dest=OUT/'inputs'/str(p).lstrip('/')
    dest.parent.mkdir(parents=True,exist_ok=True); dest.write_bytes(data)
    return data
def payload(p): return torch.load(io.BytesIO(read(p)),map_location='cpu',weights_only=True)
def digest(p): return hashlib.sha256(read(p)).hexdigest()
terminal_bytes=(SMOKE/'terminal.json').read_bytes()
(OUT/'terminal.snapshot.json').write_bytes(terminal_bytes)
terminal=json.loads(terminal_bytes); job=terminal['job']
provenance.append({'path':str(SMOKE/'terminal.json'),'sha256':hashlib.sha256(terminal_bytes).hexdigest(),'bytes':len(terminal_bytes)})
root=BASE/('job_'+JOB); role=BASE/('job_'+JOB+'_role')
for name in ['coordinator_summary.json','job_config.json','coordinator_summary.manifest.json']:
    read(root/name)
models=[root/'artifacts'/'initial.pt']+[root/'artifacts'/f'global-model-round-{r:04d}.bin' for r in (1,2)]
values=[payload(p) for p in models]
hashes=[next(x['sha256'] for x in provenance if x['path']==str(p)) for p in models]
expected=[job['model_sha256']]+[r['output_model_sha256'] for r in job['evaluation']['rounds']]
assert hashes==expected
commands.append(['sudo','find',str(role/'rounds'),'-maxdepth','1','-mindepth','1','-type','d'])
rounddirs=subprocess.check_output(commands[-1],text=True).splitlines()
updates={1:[],2:[]}; chain=[]
for d in rounddirs:
    d=pathlib.Path(d)
    state=json.loads(read(d/'round-state.json'))
    modelhash=digest(d/'model.bin'); read(d/'model.manifest.json')
    for n in (1,2,4):
        p=d/'updates'/str(n)/'update.bin'; u=payload(p); read(p.with_name('update.manifest.json'))
        r=u['round_index']; assert r in (1,2) and u['node_id']==n
        assert u['base_model_sha256']==hashes[r-1]==modelhash
        report=next(x for x in job['evaluation']['rounds'][r-1]['training'] if x['node_id']==n)
        metadata={k:v for k,v in u.items() if k!='state_dict'}
        assert all(metadata[k]==v for k,v in report.items())
        assert u['sample_count']==60000 and u['optimizer_steps']==938 and u['device']['type']=='cuda'
        updates[r].append(u)
        chain.append({'source':str(p),'source_sha256':next(x['sha256'] for x in provenance if x['path']==str(p)),'metadata':metadata,'transport_model_sha256':modelhash,'round_state':state})
weighted=[]
for r in (1,2):
    us=sorted(updates[r],key=lambda u:u['node_id']); assert len(us)==3
    total=sum(u['sample_count'] for u in us); tensors=[]
    for name, actual in values[r]['state_dict'].items():
        exact=sum((u['state_dict'][name].double()*u['sample_count'] for u in us))/total
        diff=(actual.double()-exact).abs()
        tol=4*torch.finfo(torch.float32).eps*max(1.,exact.abs().max().item())
        tensors.append({'name':name,'shape':list(actual.shape),'elements':actual.numel(),'max_abs_error_fp64':diff.max().item(),'mean_abs_error_fp64':diff.mean().item(),'tolerance':tol,'pass':bool(torch.isfinite(actual).all() and diff.max().item()<=tol)})
    weighted.append({'round':r,'total_samples':total,'weights':{str(u['node_id']):u['sample_count']/total for u in us},'tensors':tensors,'pass':all(t['pass'] for t in tensors)})
result={'job_id':JOB,'torch_version':str(torch.__version__),'model_sha256':hashes,'hash_chain_pass':True,'training':chain,'weighted_comparison':weighted}
def save():
    (OUT/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    (OUT/'input-hashes.json').write_text(json.dumps(provenance,ensure_ascii=False,indent=2)+'\n')
    (OUT/'commands.json').write_text(json.dumps(commands,ensure_ascii=False,indent=2)+'\n')
save()
class Model(nn.Module):
    def __init__(self):
        super().__init__(); self.conv1=nn.Conv2d(1,8,3,padding=1); self.conv2=nn.Conv2d(8,16,3,padding=1); self.classifier=nn.Linear(784,10)
    def forward(self,x):
        x=F.max_pool2d(F.relu(self.conv1(x)),2); x=F.max_pool2d(F.relu(self.conv2(x)),2)
        return self.classifier(x.flatten(1))
torch.set_num_threads(2)
images=read('/srv/wfb-ng-fl/mnist/t10k-images-idx3-ubyte'); labels=read('/srv/wfb-ng-fl/mnist/t10k-labels-idx1-ubyte')
assert struct.unpack('>IIII',images[:16])==(2051,10000,28,28)
assert struct.unpack('>II',labels[:8])==(2049,10000)
for name,data in [('t10k-images-idx3-ubyte',images),('t10k-labels-idx1-ubyte',labels)]:
    assert hashlib.sha256(data).hexdigest()==job['server_preparation']['data_sha256'][name]
x=torch.frombuffer(bytearray(images[16:]),dtype=torch.uint8).reshape(10000,1,28,28)
y=torch.frombuffer(bytearray(labels[8:]),dtype=torch.uint8).long()
metrics=[]
for r,value in enumerate(values):
    model=Model().cpu().eval(); model.load_state_dict(value['state_dict'],strict=True)
    start=time.monotonic(); loss_sum=0.; correct=0
    with torch.inference_mode():
        for i in range(0,10000,64):
            logits=model(x[i:i+64].float()/255.)
            logp=logits.double()-torch.logsumexp(logits.double(),dim=1,keepdim=True)
            loss_sum+=(-logp[torch.arange(len(logits)),y[i:i+64]]).sum().item()
            correct+=int((logits.argmax(1)==y[i:i+64]).sum())
    recorded=job['initial_metrics'] if r==0 else job['evaluation']['rounds'][r-1]['metrics']
    row={'model':f'M{r}','count':10000,'device':'cpu','batch_size':64,'loss':loss_sum/10000,'accuracy':correct/10000,'correct':correct,'recorded':recorded,'loss_abs_difference':abs(loss_sum/10000-recorded['loss']),'accuracy_equal':correct/10000==recorded['accuracy'],'seconds':time.monotonic()-start}
    row['pass']=row['accuracy_equal'] and row['loss_abs_difference']<1e-6
    metrics.append(row); result['independent_evaluation']=metrics; save(); print(json.dumps(row),flush=True)
result['pass']=all(w['pass'] for w in weighted) and all(m['pass'] for m in metrics)
save()
print('独立复核完成：'+str(result['pass']),flush=True)
