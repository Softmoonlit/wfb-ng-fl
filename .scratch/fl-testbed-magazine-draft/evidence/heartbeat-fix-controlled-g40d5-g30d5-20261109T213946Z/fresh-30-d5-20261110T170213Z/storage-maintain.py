#!/usr/bin/env python3
"""Execute the reviewed fl05/fl10 exact duplicate plan; no broad cleanup."""
import concurrent.futures
import hashlib
import importlib.util
import json
import pathlib
import shlex
import subprocess
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent
PLAN = json.loads((ROOT / 'fl05-fl10-storage-delete-plan.json').read_text())
TARGET = PLAN['target_bytes']
PAYLOAD_SHA = '4b55cee2f0bdcfbbf610167a505efee0e2c50e3306fed02bda3613807c234e96'
B2_RUN_ID = 'rsmc20261109T060537Z-g30-d5-model'
CURRENT_405_RUN_ID = 'fec12-g40-d5-model-20261110'
ARCHIVE_ROOT = ROOT / 'storage-archives'
ARCHIVE_ROOT.mkdir(exist_ok=True)
LOCK = threading.Lock()

spec = importlib.util.spec_from_file_location('fresh_storage_constants', ROOT / 'storage.py')
storage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(storage)
HASH = storage.HASH
ARCHIVE = storage.ARCHIVE
DELETE = storage.DELETE


def remote(host, code, timeout=180):
    command = ['ssh', '-T', '-oBatchMode=yes', '-oConnectTimeout=5', host,
               shlex.join(['sudo', '-n', 'python3', '-c', code])]
    p = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(dict(host=host, returncode=p.returncode, stderr=p.stderr, stdout=p.stdout))
    return json.loads(p.stdout)


def local_sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def storage_fact(host):
    return remote(host, "import os,json\ns=os.statvfs('/srv/wfb-ng-fl');print(json.dumps(dict(host=__import__('socket').gethostname(),available_bytes=s.f_bavail*s.f_frsize,available_inodes=s.f_favail)))", 20)


def one(host):
    data = PLAN['hosts'][host]
    if not data['target_reached'] or not data['deletion']:
        raise RuntimeError(host + ' plan is not target-reaching')
    protected = set(data['protected_paths'])
    b2 = data['b2_protected']['path']
    current_405 = data['current_40_5_protected']['path']
    if b2 not in protected or current_405 not in protected:
        raise RuntimeError(host + ' protected paths incomplete')
    before = storage_fact(host)
    result = dict(host=host, before=before, plan_files=len(data['deletion']), archives=[], deleted=[], held=data['held'])
    archived = set()
    for item in data['deletion']:
        path = item['path']
        run_id = item['run_id']
        if run_id in {B2_RUN_ID, CURRENT_405_RUN_ID} or path in protected:
            raise RuntimeError('protected path entered deletion list: ' + path)
        if not (path.startswith('/srv/wfb-ng-fl/comm-') and path.endswith(('/model.bin', '/update.bin'))):
            raise RuntimeError('path outside exact model/update scope: ' + path)
        comm_root = '/srv/wfb-ng-fl/comm-' + run_id
        if comm_root not in archived:
            logs = remote(host, 'COMM=' + repr(comm_root) + '\n' + ARCHIVE, 60)
            archive_path = ARCHIVE_ROOT / (host + '-' + run_id + '-metadata.json')
            archive_path.write_text(json.dumps(logs, ensure_ascii=False, indent=2) + '\n')
            result['archives'].append(dict(path=str(archive_path), sha256=local_sha(archive_path), comm_root=comm_root, file_count=len(logs.get('files', []))))
            archived.add(comm_root)
        fact = remote(host, 'PATH=' + repr(path) + '\n' + HASH, 60)
        if fact['path'] != path or fact['size_bytes'] != 44770302 or fact['sha256'] != PAYLOAD_SHA or fact['nlink'] != 1:
            raise RuntimeError('pre-delete exact fact mismatch: ' + repr(fact))
        expected = dict(path=path, size_bytes=fact['size_bytes'], sha256=fact['sha256'], inode=fact['inode'], device=fact['device'], nlink=fact['nlink'])
        deleted = remote(host, 'EXPECTED=' + repr(expected) + '\nKEEP=' + repr(current_405) + '\nSHA=' + repr(PAYLOAD_SHA) + '\n' + DELETE, 60)
        if deleted.get('deleted') is not True:
            raise RuntimeError('delete did not report deleted: ' + repr(deleted))
        result['deleted'].append(dict(run_id=run_id, **fact, deletion=deleted))
    after = storage_fact(host)
    result['after'] = after
    result['target_reached'] = after['available_bytes'] >= TARGET
    result['b2_after'] = remote(host, 'PATH=' + repr(b2) + '\n' + HASH, 60)
    result['current_40_5_after'] = remote(host, 'PATH=' + repr(current_405) + '\n' + HASH, 60)
    if result['b2_after']['sha256'] != PAYLOAD_SHA or result['current_40_5_after']['sha256'] != PAYLOAD_SHA:
        raise RuntimeError(host + ' protected original verification failed')
    if not result['target_reached']:
        raise RuntimeError(host + ' did not reach 1GiB after reviewed deletion')
    return result

outputs = {}
errors = {}
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    jobs = {pool.submit(one, host): host for host in PLAN['hosts']}
    for job, host in [(job, host) for job, host in jobs.items()]:
        try:
            outputs[host] = job.result()
        except Exception as exc:
            errors[host] = repr(exc)
result = dict(status='complete' if not errors else 'stopped', wall=time.time(), target_bytes=TARGET, hosts=outputs, errors=errors, note='Only reviewed exact full-success duplicate paths were processed; B2/current-40/5 originals and non-manifest material were protected.')
(ROOT / 'fl05-fl10-storage-maintenance-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(dict(status=result['status'], hosts={h: dict(before=v.get('before',{}).get('available_bytes'), after=v.get('after',{}).get('available_bytes'), deleted=len(v.get('deleted',[])), target=v.get('target_reached')) for h,v in outputs.items()}, errors=errors), ensure_ascii=False, indent=2))
raise SystemExit(1 if errors else 0)
