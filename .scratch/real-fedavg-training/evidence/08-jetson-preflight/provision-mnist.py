import pathlib,sys,json,gzip,hashlib,os,time,struct
stage=pathlib.Path(sys.argv[1]);out=pathlib.Path('/srv/wfb-ng-fl/mnist')
m=json.loads((stage/'mnist-source-manifest.json').read_text())
print('官方数据预置 UTC',time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'目标',out,flush=True)
if out.exists():
 assert not out.is_symlink()
 assert not any((out/i['raw_name']).exists() or (out/i['raw_name']).is_symlink() for i in m['files']),'拒绝覆盖已有 IDX'
 assert not (out/'SOURCE-MANIFEST.json').exists(),'拒绝覆盖已有来源记录'
# 在创建任何目标文件之前，先校验全部压缩包。
for i in m['files']:
 data=(stage/(i['raw_name']+'.gz')).read_bytes()
 assert hashlib.md5(data).hexdigest()==i['gz_md5']
 assert hashlib.sha256(data).hexdigest()==i['gz_sha256']
 raw=gzip.decompress(data);assert len(raw)==i['raw_bytes'];assert hashlib.sha256(raw).hexdigest()==i['raw_sha256']
 assert struct.unpack('>II',raw[:8])==(i['magic'],i['sample_count'])
 if i['magic']==2051:assert struct.unpack('>II',raw[8:16])==(28,28)
 else:assert max(raw[8:])<=9
out.mkdir(mode=0o755,parents=True,exist_ok=True)
for i in m['files']:
 raw=gzip.decompress((stage/(i['raw_name']+'.gz')).read_bytes());dest=out/i['raw_name']
 with dest.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
 dest.chmod(0o644); print('已创建',dest,'字节',len(raw),'sha256',hashlib.sha256(dest.read_bytes()).hexdigest(),flush=True)
with (out/'SOURCE-MANIFEST.json').open('x') as f:json.dump(m,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
(out/'SOURCE-MANIFEST.json').chmod(0o644)
print('MNIST_PROVISION_PASS',flush=True)
