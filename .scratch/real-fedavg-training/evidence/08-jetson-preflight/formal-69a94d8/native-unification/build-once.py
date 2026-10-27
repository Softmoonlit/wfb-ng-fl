import pathlib,subprocess,json,hashlib,os,time
root=pathlib.Path('/srv/wfb-ng-fl/native-69a94d8-unified');root.mkdir(exist_ok=False)
archive=pathlib.Path('/tmp/wfb08-69a94d8.tar');assert hashlib.sha256(archive.read_bytes()).hexdigest()=='d723b33001929f5ff4ae687ff4ad0150c75b15abc2369725511b4209de20701b'
src=root/'source';src.mkdir();subprocess.run(['tar','-xf',str(archive),'-C',str(src)],check=True)
cmd=['make','-C',str(src),'build_v6','COMMIT=69a94d84b006666d01e257155bc7a2fc969bedef','VERSION=0.0.0','PYTHON=/usr/bin/python3'];print('COMMAND',cmd,flush=True);subprocess.run(cmd,check=True,timeout=180)
exe=src/'wfb_v6_uplink';facts={'commit':'69a94d84b006666d01e257155bc7a2fc969bedef','archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'command':cmd,'cwd':str(src),'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'executable_sha256':hashlib.sha256(exe.read_bytes()).hexdigest(),'object_sha256':{str(f.relative_to(src)):hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted((src/'src').glob('*.o'))},'source_sha256':{str(f.relative_to(src)):hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted((src/'src').glob('*')) if f.suffix in ['.c','.cpp','.hpp','.h']},'checks':[]}
for args in [['g++','--version'],['readelf','-h',str(exe)],['readelf','-l',str(exe)],['readelf','-n',str(exe)],['readelf','-p','.comment',str(exe)],['readelf','--version-info',str(exe)],['ldd',str(exe)]]:
 r=subprocess.run(args,capture_output=True,text=True,timeout=10);facts['checks'].append({'args':args,'rc':r.returncode,'stdout':r.stdout,'stderr':r.stderr});r.check_returncode()
(root/'build-manifest.json').write_text(json.dumps(facts,ensure_ascii=False,indent=2)+'\n');print('UNIFIED_NATIVE_BUILD_PASS',facts['executable_sha256'],flush=True)
