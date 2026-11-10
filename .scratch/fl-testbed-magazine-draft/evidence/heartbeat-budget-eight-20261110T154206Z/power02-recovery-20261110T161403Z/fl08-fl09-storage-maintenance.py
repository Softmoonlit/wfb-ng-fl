#!/usr/bin/env python3
import hashlib,json,os,pathlib,time
SIZE=44770302; SHA='4b55cee2f0bdcfbbf610167a505efee0e2c50e3306fed02bda3613807c234e96'; REQUIRED=671181818
SELECTED=[
'/srv/wfb-ng-fl/comm-fec12-g40-d5-model-20261110/role/inbox/9934eb94-fe3e-48ac-a0ad-4b62acfa2e87/model.bin',
'/srv/wfb-ng-fl/comm-fec12-g40-d5-model-20261110/role/rounds/9934eb94-fe3e-48ac-a0ad-4b62acfa2e87/model.bin',
'/srv/wfb-ng-fl/comm-fec12-g40-d5-model-20261110/role/rounds/9934eb94-fe3e-48ac-a0ad-4b62acfa2e87/update.bin']
PROTECTED=[
'/srv/wfb-ng-fl/comm-rsmc20261109T060537Z-g30-d5-model/role/rounds/81f1fdf3-d962-48d1-890e-c4ffb6583d86/model.bin',
'/srv/wfb-ng-fl/comm-post-fl02-20261110T142107Z-model/role/rounds/21b2b57d-6f48-48af-9eea-e2d74c89a83c/model.bin']
def fact(path):
 p=pathlib.Path(path)
 if not p.exists() or not p.is_file() or p.is_symlink(): return {'path':path,'exists':False}
 h=hashlib.sha256()
 with p.open('rb') as f:
  for c in iter(lambda:f.read(1<<20),b''): h.update(c)
 s=p.stat(); return {'path':path,'exists':True,'size_bytes':s.st_size,'sha256':h.hexdigest(),'nlink':s.st_nlink,'inode':s.st_ino,'device':s.st_dev}
def free():
 s=os.statvfs('/srv/wfb-ng-fl'); return s.f_bavail*s.f_frsize
before=free(); protected_before=[fact(x) for x in PROTECTED]
if any(not x.get('exists') or x.get('size_bytes')!=SIZE or x.get('sha256')!=SHA for x in protected_before): raise SystemExit(json.dumps({'status':'blocked','reason':'protected_mismatch','protected':protected_before},ensure_ascii=False))
rows=[]
for path in SELECTED:
 b=fact(path)
 if not b.get('exists') or b.get('size_bytes')!=SIZE or b.get('sha256')!=SHA or b.get('nlink')!=1: raise SystemExit(json.dumps({'status':'blocked','reason':'selected_precondition_mismatch','selected':b,'rows':rows},ensure_ascii=False))
 pathlib.Path(path).unlink(); a=fact(path); rows.append({'before':b,'after':a,'free_bytes':free(),'source_success_evidence':'local completed progress for fec12-g40-d5-model-20261110'})
 if free()>=REQUIRED: break
result={'status':'complete' if free()>=REQUIRED else 'below_required','host':os.uname().nodename,'wall_ns':time.time_ns(),'required_bytes':REQUIRED,'free_before':before,'free_after':free(),'target':{'size_bytes':SIZE,'sha256':SHA},'deleted_rows':rows,'protected_before':protected_before,'protected_after':[fact(x) for x in PROTECTED],'failed_attempts_deleted':False,'old_reclamation_paths_reused':False}
print(json.dumps(result,ensure_ascii=False,indent=2))
