#!/usr/bin/env python3
import hashlib
import json
import os
import pathlib
import time

TARGET_SIZE = 44770302
TARGET_SHA = '4b55cee2f0bdcfbbf610167a505efee0e2c50e3306fed02bda3613807c234e96'
REQUIRED = 671181818
SELECTED = [
    '/srv/wfb-ng-fl/comm-fec12-g40-d5-model-20261110/role/inbox/9934eb94-fe3e-48ac-a0ad-4b62acfa2e87/model.bin',
    '/srv/wfb-ng-fl/comm-fec12-g40-d5-model-20261110/role/rounds/9934eb94-fe3e-48ac-a0ad-4b62acfa2e87/model.bin',
    '/srv/wfb-ng-fl/comm-fec12-g40-d5-model-20261110/role/rounds/9934eb94-fe3e-48ac-a0ad-4b62acfa2e87/update.bin',
]
PROTECTED = [
    '/srv/wfb-ng-fl/comm-rsmc20261109T060537Z-g30-d5-model/role/rounds/81f1fdf3-d962-48d1-890e-c4ffb6583d86/model.bin',
    '/srv/wfb-ng-fl/comm-post-fl02-20261110T142107Z-model/role/rounds/21b2b57d-6f48-48af-9eea-e2d74c89a83c/model.bin',
]

def fact(path):
    p = pathlib.Path(path)
    if not p.exists() or not p.is_file() or p.is_symlink():
        return {'path': path, 'exists': False}
    h = hashlib.sha256()
    with p.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''): h.update(chunk)
    st = p.stat()
    return {'path': path, 'exists': True, 'regular': True, 'symlink': False,
            'size_bytes': st.st_size, 'sha256': h.hexdigest(), 'nlink': st.st_nlink,
            'inode': st.st_ino, 'device': st.st_dev}

def free_bytes():
    st = os.statvfs('/srv/wfb-ng-fl')
    return st.f_bavail * st.f_frsize

before = free_bytes()
protected_before = [fact(path) for path in PROTECTED]
if any(not row.get('exists') or row.get('size_bytes') != TARGET_SIZE or row.get('sha256') != TARGET_SHA
       for row in protected_before):
    raise SystemExit(json.dumps({'status': 'blocked', 'reason': 'protected_success_missing_or_mismatch',
                                 'protected': protected_before}, ensure_ascii=False))
rows = []
for path in SELECTED:
    current = fact(path)
    if not current.get('exists') or current.get('size_bytes') != TARGET_SIZE or current.get('sha256') != TARGET_SHA or current.get('nlink') != 1:
        raise SystemExit(json.dumps({'status': 'blocked', 'reason': 'selected_precondition_mismatch',
                                     'selected': current, 'rows': rows}, ensure_ascii=False))
    pathlib.Path(path).unlink()
    after = fact(path)
    rows.append({'before': current, 'after': after, 'free_bytes': free_bytes(),
                 'source_success_evidence': 'local completed progress for fec12-g40-d5-model-20261110'})
    if free_bytes() >= REQUIRED:
        break
result = {'status': 'complete' if free_bytes() >= REQUIRED else 'below_required_after_authorized_candidates',
          'host': os.uname().nodename, 'wall_ns': time.time_ns(), 'required_bytes': REQUIRED,
          'target': {'size_bytes': TARGET_SIZE, 'sha256': TARGET_SHA}, 'free_before': before,
          'free_after': free_bytes(), 'selected_candidates': SELECTED, 'deleted_rows': rows,
          'protected_before': protected_before, 'protected_after': [fact(path) for path in PROTECTED],
          'failed_attempts_deleted': False, 'old_reclamation_paths_reused': False}
print(json.dumps(result, ensure_ascii=False, indent=2))
