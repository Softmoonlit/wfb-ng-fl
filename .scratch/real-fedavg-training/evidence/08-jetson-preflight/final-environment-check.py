import pathlib,json,hashlib,subprocess,time,os,struct
print('最终环境核验 UTC',time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),flush=True)
for args in [['hostname'],['df','-B1','/'],['systemctl','show','wfb-fl-server-daemon.service','wfb-fl-client-daemon.service','-p','Id','-p','MainPID','-p','ActiveState'],['iw','dev'],['lsusb','-t'],['dpkg-query','-W','nvidia-l4t-core','cuda-toolkit-12-6','libcudnn9-cuda-12','libcusparselt0'],['python3','-m','pip','check']]:
 print('命令',args,flush=True)
 try:
  r=subprocess.run(args,capture_output=True,text=True,timeout=20);print(r.stdout+r.stderr,'退出码',r.returncode,flush=True)
 except Exception as e:print('失败',repr(e),flush=True)
root=pathlib.Path('/srv/wfb-ng-fl/mnist');manifest=json.loads((root/'SOURCE-MANIFEST.json').read_text());files=[]
for i in manifest['files']:
 f=root/i['raw_name'];data=f.read_bytes();sha=hashlib.sha256(data).hexdigest()
 assert sha==i['raw_sha256'] and len(data)==i['raw_bytes']
 assert struct.unpack('>II',data[:8])==(i['magic'],i['sample_count'])
 if i['magic']==2051:assert struct.unpack('>II',data[8:16])==(28,28)
 else:assert max(data[8:])<=9
 assert os.access(f,os.R_OK)
 files.append({'name':f.name,'bytes':len(data),'sha256':sha,'mode':oct(f.stat().st_mode & 0o777),'uid':f.stat().st_uid,'gid':f.stat().st_gid})
print('MNIST_FINAL_RESULT '+json.dumps({'hostname':subprocess.check_output(['hostname'],text=True).strip(),'directory':str(root),'files':files,'manifest_sha256':hashlib.sha256((root/'SOURCE-MANIFEST.json').read_bytes()).hexdigest(),'status':'pass'}),flush=True)
base=pathlib.Path('/usr/local/lib/python3.10/dist-packages/wfb_ng/fl')
for name in ['build_identity.json','client_daemon.py','server_daemon.py','reference_model.py','mnist.py','mnist_data.py']:
 f=base/name
 if f.exists(): print('安装产物',name,'sha256',hashlib.sha256(f.read_bytes()).hexdigest(),flush=True)
 else:print('安装产物仍未部署',name,flush=True)
print('FINAL_DATA_AND_IDENTITY_CHECK_PASS',flush=True)
