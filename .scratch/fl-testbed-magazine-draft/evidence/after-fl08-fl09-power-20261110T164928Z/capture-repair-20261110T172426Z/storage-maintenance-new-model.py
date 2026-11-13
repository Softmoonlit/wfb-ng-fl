#!/usr/bin/env python3
"""新 model admission 前的 exact success duplicate maintenance；保留最近成功原件。"""
import hashlib
import json
import os
from pathlib import Path
import time

SIZE=44770302
SHA='4b55cee2f0bdcfbbf610167a505efee0e2c50e3306fed02bda3613807c234e96'
REQUIRED=671181818
SELECTED={
'fl01':[
'/srv/wfb-ng-fl/comm-fec14-20261109T082509Z-g10-d10-model/role/rounds/372d5927-3d0b-4160-9eb8-73b77d5121dd/model.bin',
'/srv/wfb-ng-fl/comm-fec14-20261109T082509Z-g10-d10-model/role/rounds/372d5927-3d0b-4160-9eb8-73b77d5121dd/update.bin',
'/srv/wfb-ng-fl/comm-fec12-apr-20261109T105004Z-t1-g30-d5-model/role/rounds/e9feb28d-3aa7-4590-b453-ba3e7ba7eab8/model.bin',
'/srv/wfb-ng-fl/comm-fec12-apr-20261109T105004Z-t1-g30-d5-model/role/rounds/e9feb28d-3aa7-4590-b453-ba3e7ba7eab8/update.bin'],
'fl02':[
'/srv/wfb-ng-fl/comm-fec14-20261109T082509Z-g10-d10-model/role/rounds/372d5927-3d0b-4160-9eb8-73b77d5121dd/model.bin',
'/srv/wfb-ng-fl/comm-fec14-20261109T082509Z-g10-d10-model/role/rounds/372d5927-3d0b-4160-9eb8-73b77d5121dd/update.bin',
'/srv/wfb-ng-fl/comm-fec12-apr-20261109T185342Z-t2-g30-d5-model/role/rounds/28602245-36ff-47c5-b587-932a31c49623/model.bin'],
'fl05':[
'/srv/wfb-ng-fl/comm-fec12-20261109T071504Z-g30-d5-model/role/rounds/89b243af-8753-4c9a-9f1a-4d959fa849b6/model.bin',
'/srv/wfb-ng-fl/comm-fec12-20261109T071504Z-g30-d5-model/role/rounds/89b243af-8753-4c9a-9f1a-4d959fa849b6/update.bin'],
'fl06':[
'/srv/wfb-ng-fl/comm-fec12-20261109T071504Z-g30-d5-model/role/rounds/89b243af-8753-4c9a-9f1a-4d959fa849b6/model.bin',
'/srv/wfb-ng-fl/comm-fec12-20261109T071504Z-g30-d5-model/role/rounds/89b243af-8753-4c9a-9f1a-4d959fa849b6/update.bin',
'/srv/wfb-ng-fl/comm-model-fec14-g10-d10/role/rounds/c26f3c4d-a2f6-400e-a85a-c784c45a2607/model.bin',
'/srv/wfb-ng-fl/comm-model-fec14-g10-d10/role/rounds/c26f3c4d-a2f6-400e-a85a-c784c45a2607/update.bin'],
'fl07':[
'/srv/wfb-ng-fl/comm-fec12-20261109T071504Z-g20-d10-model/role/rounds/cee8e9d4-af9e-465e-8df7-9881158f8423/update.bin',
'/srv/wfb-ng-fl/comm-fec12-20261109T071504Z-g30-d5-model/role/rounds/89b243af-8753-4c9a-9f1a-4d959fa849b6/model.bin',
'/srv/wfb-ng-fl/comm-fec12-20261109T071504Z-g30-d5-model/role/rounds/89b243af-8753-4c9a-9f1a-4d959fa849b6/update.bin'],
'fl09':[
'/srv/wfb-ng-fl/comm-fec12-20261109T071504Z-g30-d5-model/role/rounds/89b243af-8753-4c9a-9f1a-4d959fa849b6/model.bin',
'/srv/wfb-ng-fl/comm-fec12-20261109T071504Z-g30-d5-model/role/rounds/89b243af-8753-4c9a-9f1a-4d959fa849b6/update.bin',
'/srv/wfb-ng-fl/comm-fec12-20261109T071504Z-g40-d10-model/role/rounds/f80703de-e660-496e-b684-812e75dbe672/model.bin']}
PROTECTED_TEMPLATE=[
'/srv/wfb-ng-fl/comm-after-fl08-fl09-power-20261110T171130Z-model/role/rounds/627c858e-fd76-4d1d-9202-2283373028f4/model.bin',
'/srv/wfb-ng-fl/comm-rsmc20261109T060537Z-g30-d5-model/role/rounds/81f1fdf3-d962-48d1-890e-c4ffb6583d86/model.bin',
'/srv/wfb-ng-fl/comm-post-fl02-20261110T142107Z-model/role/rounds/21b2b57d-6f48-48af-9eea-e2d74c89a83c/model.bin',
'/srv/wfb-ng-fl/comm-second-eight-model-20261110T150044Z-model/role/rounds/26d375cd-7c5a-478c-ae4d-c4802b176958/model.bin']

def fact(value):
 p=Path(value)
 try:i=p.lstat()
 except FileNotFoundError:return {'path':value,'exists':False}
 r={'path':value,'exists':True,'regular':p.is_file() and not p.is_symlink(),'size_bytes':i.st_size,'inode':i.st_ino,'device':i.st_dev,'nlink':i.st_nlink,'mode':oct(i.st_mode),'mtime_ns':i.st_mtime_ns}
 if r['regular']:
  h=hashlib.sha256()
  with p.open('rb') as f:
   for c in iter(lambda:f.read(1<<20),b''):h.update(c)
  r['sha256']=h.hexdigest()
 return r

def free():
 s=os.statvfs('/srv/wfb-ng-fl');return s.f_bavail*s.f_frsize
host=os.uname().nodename; selected=SELECTED.get(host,[]); protected=PROTECTED_TEMPLATE
before=free(); pb=[fact(x) for x in protected]; sb=[fact(x) for x in selected]
if any(not x.get('regular') or x.get('size_bytes')!=SIZE or x.get('sha256')!=SHA for x in pb):
 print(json.dumps({'status':'blocked','reason':'protected_mismatch','host':host,'free_before':before,'protected_before':pb},ensure_ascii=False,indent=2));raise SystemExit(2)
if any(not x.get('regular') or x.get('size_bytes')!=SIZE or x.get('sha256')!=SHA or x.get('nlink')!=1 for x in sb):
 print(json.dumps({'status':'blocked','reason':'selected_precondition_mismatch','host':host,'free_before':before,'selected_before':sb},ensure_ascii=False,indent=2));raise SystemExit(2)
rows=[]
for path,row in zip(selected,sb):
 Path(path).unlink();rows.append({'before':row,'after':fact(path),'free_after':free()})
 if free()>=REQUIRED:break
result={'status':'complete' if free()>=REQUIRED else 'below_required','host':host,'wall_ns':time.time_ns(),'required_bytes':REQUIRED,'free_before':before,'free_after':free(),'target':{'size_bytes':SIZE,'sha256':SHA},'selected':selected,'deleted_rows':rows,'protected_before':pb,'protected_after':[fact(x) for x in protected],'recent_success_preserved':True,'failed_attempts_deleted':False}
print(json.dumps(result,ensure_ascii=False,indent=2));raise SystemExit(0 if result['status']=='complete' else 3)
