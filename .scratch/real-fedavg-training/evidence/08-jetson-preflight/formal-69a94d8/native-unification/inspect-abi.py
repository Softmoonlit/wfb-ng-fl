import subprocess,pathlib,hashlib,json,os,time
exe='/usr/bin/wfb_v6_uplink';out={'host':os.uname().nodename,'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'checks':[]}
for args in [['uname','-m'],['getconf','GNU_LIBC_VERSION'],['g++','--version'],['file',exe],['readelf','-h',exe],['readelf','-l',exe],['readelf','-n',exe],['readelf','-p','.comment',exe],['ldd',exe],['readelf','-d',exe]]:
 r=subprocess.run(args,capture_output=True,text=True,timeout=10);out['checks'].append({'args':args,'rc':r.returncode,'out':r.stdout,'err':r.stderr})
root=pathlib.Path('/srv/wfb-ng-fl/deploy-69a94d8/source');out['build_artifact_manifest']={str(f.relative_to(root)):hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted((root/'src').glob('*.o'))};out['native_sha']=hashlib.sha256(pathlib.Path(exe).read_bytes()).hexdigest();print(json.dumps(out,ensure_ascii=False,indent=2))
