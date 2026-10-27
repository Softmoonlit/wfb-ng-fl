import subprocess,pathlib,time,hashlib,gzip,json,os,struct
BASE=pathlib.Path(__file__).resolve().parent
cache=pathlib.Path('/srv/wfb-ng-fl/mnist-downloads'); cache.mkdir(exist_ok=True)
resources=[('train-images-idx3-ubyte', 'f68b3c2dcbeaaa9fbdd348bbdeb94873',47040016,2051,60000),('train-labels-idx1-ubyte','d53e105ee54ea40749a09fcbcd1e9432',60008,2049,60000),('t10k-images-idx3-ubyte','9fb629c4189551a2d022fa330f9573f3',7840016,2051,10000),('t10k-labels-idx1-ubyte','ec29112dd5afa0611ce80d1b7f02629c',10008,2049,10000)]
manifest={'created_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'provenance':'torchvision MNIST resources; HTTPS OSSCI mirror; gzip MD5 matched maintenance metadata','files':[]}
for raw,md5,size,magic,count in resources:
 f=cache/(raw+'.gz')
 if f.exists(): raise FileExistsError('禁止覆盖已有压缩包 '+str(f))
 url='https://ossci-datasets.s3.amazonaws.com/mnist/'+raw+'.gz'
 args=['curl','--fail','--location','--connect-timeout','8','--max-time','120','--retry','2','--dump-header',str(BASE/(raw+'.headers.txt')),'--output',str(f),url]
 print('下载命令',args,flush=True);subprocess.run(args,check=True,timeout=380)
 data=f.read_bytes(); actual=hashlib.md5(data).hexdigest(); assert actual==md5,(raw,actual,md5)
 plain=gzip.decompress(data); assert len(plain)==size
 assert struct.unpack('>II',plain[:8])==(magic,count)
 if magic==2051:assert struct.unpack('>II',plain[8:16])==(28,28)
 else:assert min(plain[8:])>=0 and max(plain[8:])<=9
 item={'raw_name':raw,'source_url':url,'obtained_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'gz_bytes':len(data),'gz_md5':actual,'gz_sha256':hashlib.sha256(data).hexdigest(),'raw_bytes':size,'raw_sha256':hashlib.sha256(plain).hexdigest(),'magic':magic,'sample_count':count}
 manifest['files'].append(item);print(json.dumps(item),flush=True)
(BASE/'mnist-source-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
print('MNIST_DOWNLOAD_AND_VALIDATION_PASS',flush=True)
