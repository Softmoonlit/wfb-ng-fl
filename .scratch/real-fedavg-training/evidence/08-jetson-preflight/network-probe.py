import subprocess,time
print('采集 UTC:',time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),flush=True)
urls=[
('作者官方网页','https://yann.lecun.com/exdb/mnist/'),
('作者历史官方网页','http://yann.lecun.com/exdb/mnist/'),
('作者主源训练图像','https://yann.lecun.com/exdb/mnist/train-images-idx3-ubyte.gz'),
('PyTorch 维护镜像训练图像','https://ossci-datasets.s3.amazonaws.com/mnist/train-images-idx3-ubyte.gz'),
('Google 维护镜像训练图像','https://storage.googleapis.com/cvdf-datasets/mnist/train-images-idx3-ubyte.gz')]
for label,url in urls:
 print('\n来源:',label,url,'仅 HEAD，不下载数据',flush=True)
 try:
  r=subprocess.run(['curl','--head','--location','--connect-timeout','4','--max-time','8','--silent','--show-error',url],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=10)
  print(r.stdout,'退出码:',r.returncode,flush=True)
 except Exception as e: print('失败:',repr(e),flush=True)
