#!/usr/bin/env python3
"""Read-only recheck and delete-plan builder for exact fl05/fl10 duplicates."""
import json
import pathlib
import shlex
import subprocess
import time

ROOT = pathlib.Path(__file__).resolve().parent
EVIDENCE = ROOT.parent.parent
MANIFEST = json.loads((ROOT / 'fl05-fl10-storage-candidate-review.json').read_text())
HOSTS = ['fl05', 'fl10']
TARGET = 1024 * 1024 * 1024
PAYLOAD_SIZE = 44770302
PAYLOAD_SHA = '4b55cee2f0bdcfbbf610167a505efee0e2c50e3306fed02bda3613807c234e96'
B2_RUN_ID = 'rsmc20261109T060537Z-g30-d5-model'
CURRENT_405_RUN_ID = 'fec12-g40-d5-model-20261110'


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def exact_files_for_run(host, run_id):
    out = []
    for source in sorted(EVIDENCE.rglob('*exact-uftp-kernel-files.json')):
        value = read_json(source)
        if not isinstance(value, dict):
            continue
        for row in value.get('rows', []):
            if row.get('host') != host or row.get('returncode') != 0:
                continue
            for item in row.get('result', {}).get('files', []):
                path = item.get('path', '')
                if ('/comm-' + run_id + '/') not in path:
                    continue
                if item.get('size_bytes') == PAYLOAD_SIZE and item.get('sha256') == PAYLOAD_SHA:
                    out.append(dict(path=path, source=str(source), source_sha256=sha(source)))
    unique = {}
    for item in out:
        unique[item['path']] = item
    return sorted(unique.values(), key=lambda item: item['path'])


def sha(path):
    import hashlib
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def remote_facts(host, paths):
    code = r'''import hashlib,json,os,pathlib,stat,socket
paths=json.loads(PATHS)
out=[]
for name in paths:
 p=pathlib.Path(name)
 try:
  s=p.lstat()
  h=hashlib.sha256()
  if stat.S_ISREG(s.st_mode) and not p.is_symlink():
   with p.open('rb') as f:
    for b in iter(lambda:f.read(1048576),b''):h.update(b)
  out.append(dict(path=name,exists=True,regular=stat.S_ISREG(s.st_mode),symlink=p.is_symlink(),size_bytes=s.st_size,inode=s.st_ino,device=s.st_dev,nlink=s.st_nlink,sha256=h.hexdigest() if stat.S_ISREG(s.st_mode) and not p.is_symlink() else None,mode=oct(s.st_mode)))
 except FileNotFoundError:
  out.append(dict(path=name,exists=False))
 except Exception as exc:
  out.append(dict(path=name,error=repr(exc)))
s=os.statvfs('/srv/wfb-ng-fl')
print(json.dumps(dict(host=socket.gethostname(),available_bytes=s.f_bavail*s.f_frsize,available_inodes=s.f_favail,files=out)))
'''.replace('PATHS', repr(json.dumps(paths)))
    command = ['ssh', '-T', '-oBatchMode=yes', '-oConnectTimeout=5', host,
               shlex.join(['sudo', '-n', 'python3', '-c', code])]
    try:
        p = subprocess.run(command, capture_output=True, text=True, timeout=180)
        value = json.loads(p.stdout) if p.returncode == 0 else None
        return dict(host=host, returncode=p.returncode, result=value, stderr=p.stderr)
    except Exception as exc:
        return dict(host=host, error=repr(exc))


def choose_protected(host):
    b2 = exact_files_for_run(host, B2_RUN_ID)
    current = exact_files_for_run(host, CURRENT_405_RUN_ID)
    b2_round = next((item for item in b2 if '/role/rounds/' in item['path'] and item['path'].endswith('/model.bin')), None)
    current_round = next((item for item in current if '/role/rounds/' in item['path'] and item['path'].endswith('/model.bin')), None)
    return dict(b2=b2, b2_protected=b2_round, current_40_5=current,
                current_40_5_protected=current_round)


def group_candidates(host):
    groups = {}
    for row in MANIFEST['candidates'][host]:
        groups.setdefault(row['run_id'], []).extend(row['files'])
    return {run_id: sorted({item['path']: item for item in files}.values(), key=lambda item: item['path'])
            for run_id, files in groups.items()}

protected = {host: choose_protected(host) for host in HOSTS}
paths_by_host = {}
for host in HOSTS:
    paths = []
    for row in MANIFEST['candidates'][host]:
        paths.extend(item['path'] for item in row['files'])
    paths.extend(item['path'] for item in protected[host]['b2'])
    paths.extend(item['path'] for item in protected[host]['current_40_5'])
    paths_by_host[host] = sorted(set(paths))

facts = {host: remote_facts(host, paths_by_host[host]) for host in HOSTS}
(ROOT / 'fl05-fl10-storage-remote-recheck.json').write_text(json.dumps(dict(
    status='read-only-recheck', wall=time.time(), hosts=HOSTS, target_bytes=TARGET,
    payload=dict(size_bytes=PAYLOAD_SIZE, sha256=PAYLOAD_SHA), protected=protected, facts=facts,
    note='No unlink; exact lstat/SHA only on manifest paths and protected B2/current-40/5 paths.'), ensure_ascii=False, indent=2) + '\n')

# Plan full historical groups first, excluding B2 and the current 40/5 run. Keep one current 40/5 path.
plan = dict(status='review-only-no-delete', wall=time.time(), target_bytes=TARGET, hosts={})
for host in HOSTS:
    fact = facts[host]
    rows = fact.get('result', {}).get('files', []) if fact.get('returncode') == 0 and fact.get('result') else []
    by_path = {row.get('path'): row for row in rows}
    protected_paths = set()
    for item in (protected[host].get('b2_protected'), protected[host].get('current_40_5_protected')):
        if item:
            protected_paths.add(item['path'])
    current = fact.get('result', {}).get('available_bytes') if fact.get('result') else None
    groups = group_candidates(host)
    deletion = []
    held = []
    # Historical repeated runs first, lexicographically stable. Only delete complete present groups.
    for run_id in sorted(groups):
        files = groups[run_id]
        if run_id in {B2_RUN_ID, CURRENT_405_RUN_ID}:
            held.extend(dict(run_id=run_id, path=item['path'], reason='protected run') for item in files)
            continue
        observed = [by_path.get(item['path']) for item in files]
        valid = all(item and item.get('exists') and item.get('regular') and not item.get('symlink')
                    and item.get('size_bytes') == PAYLOAD_SIZE and item.get('sha256') == PAYLOAD_SHA
                    and item.get('nlink') == 1 for item in observed)
        if not valid:
            held.extend(dict(run_id=run_id, path=item['path'], reason='missing_or_current_recheck_mismatch') for item in files)
            continue
        if current is not None and current < TARGET:
            deletion.extend(dict(run_id=run_id, **item) for item in files)
            current += sum(item['size_bytes'] for item in files)
        else:
            held.extend(dict(run_id=run_id, path=item['path'], reason='not_needed_for_target') for item in files)
    plan['hosts'][host] = dict(before_available_bytes=fact.get('result', {}).get('available_bytes'),
        protected_paths=sorted(protected_paths), held=held, deletion=deletion,
        planned_available_bytes=current, target_reached=current is not None and current >= TARGET,
        current_40_5_protected=protected[host].get('current_40_5_protected'),
        b2_protected=protected[host].get('b2_protected'))
(ROOT / 'fl05-fl10-storage-delete-plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({host: dict(before=plan['hosts'][host]['before_available_bytes'], planned=plan['hosts'][host]['planned_available_bytes'], delete_files=len(plan['hosts'][host]['deletion']), target=plan['hosts'][host]['target_reached']) for host in HOSTS}, ensure_ascii=False, indent=2))
