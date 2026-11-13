#!/usr/bin/env python3
"""Build a read-only exact duplicate review for fl05/fl10; never unlinks."""
import hashlib
import json
import pathlib
import re
import time

ROOT = pathlib.Path(__file__).resolve().parent
EVIDENCE = ROOT.parent.parent
TARGET_HOSTS = {'fl05', 'fl10'}
PAYLOAD_SIZE = 44770302
PAYLOAD_SHA = '4b55cee2f0bdcfbbf610167a505efee0e2c50e3306fed02bda3613807c234e96'
B2_RUN_ID = 'rsmc20261109T060537Z-g30-d5-model'
B2_COMM = 'comm-' + B2_RUN_ID


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def run_id_from_path(path):
    match = re.search(r'/comm-([A-Za-z0-9_-]+)/', path)
    return match.group(1) if match else None


def final_success(progress):
    rows = [read_json_line for read_json_line in progress.read_text().splitlines()]
    rows = [json.loads(row) for row in rows if row.strip()]
    final = next((row for row in reversed(rows) if row.get('phase') == 'result'), None)
    return final is not None and all(final.get(key) is True
                                     for key in ('transfer_verified', 'cleanup_ok', 'restore_ok'))


def independent_cleanup_ok(path, host):
    value = read_json(path)
    if isinstance(value, list):
        for row in value:
            if row.get('host') == host:
                result = row.get('result')
                return isinstance(result, dict) and result.get('ok') is True
    if isinstance(value, dict):
        rows = value.get('rows', value.get('results', []))
        if isinstance(rows, list):
            for row in rows:
                if row.get('host') == host:
                    result = row.get('result', row)
                    return isinstance(result, dict) and result.get('ok') is True
    return False


# A run is eligible only if an existing progress.jsonl proves all three terminal flags.
successful_runs = {}
for progress in EVIDENCE.rglob('progress.jsonl'):
    try:
        if final_success(progress):
            successful_runs.setdefault(progress.parent.name, []).append({
                'progress': str(progress),
                'progress_sha256': sha(progress),
            })
    except (OSError, ValueError, UnicodeDecodeError):
        continue

# Build independent cleanup lookup by run id and host.
cleanup = {}
for path in EVIDENCE.rglob('*independent-exact-cleanup.json'):
    text = str(path)
    matched = [run_id for run_id in successful_runs if run_id in path.name or run_id in text]
    for run_id in matched:
        cleanup.setdefault(run_id, []).append(path)

candidates = {host: [] for host in sorted(TARGET_HOSTS)}
seen = set()
source_count = 0
for source in sorted(EVIDENCE.rglob('*exact-uftp-kernel-files.json')):
    value = read_json(source)
    if not isinstance(value, dict):
        continue
    source_count += 1
    for row in value.get('rows', []):
        host = row.get('host')
        result = row.get('result', {})
        if host not in TARGET_HOSTS or row.get('returncode') != 0 or not result.get('logs'):
            continue
        files = result.get('files', [])
        by_run = {}
        for item in files:
            path = item.get('path')
            run_id = run_id_from_path(path or '')
            if not run_id or run_id == B2_RUN_ID or run_id not in successful_runs:
                continue
            if item.get('size_bytes') != PAYLOAD_SIZE or item.get('sha256') != PAYLOAD_SHA:
                continue
            if pathlib.PurePosixPath(path).name not in {'model.bin', 'update.bin'}:
                continue
            by_run.setdefault(run_id, []).append({
                'path': path,
                'size_bytes': item.get('size_bytes'),
                'sha256': item.get('sha256'),
            })
        for run_id, items in by_run.items():
            if len(items) != 3:
                continue
            key = (host, run_id, tuple(sorted(item['path'] for item in items)))
            if key in seen:
                continue
            seen.add(key)
            cleanup_paths = cleanup.get(run_id, [])
            cleanup_ok = any(independent_cleanup_ok(path, host) for path in cleanup_paths)
            candidates[host].append({
                'host': host,
                'run_id': run_id,
                'comm_root': '/srv/wfb-ng-fl/comm-' + run_id,
                'source_exact_evidence': str(source),
                'source_exact_evidence_sha256': sha(source),
                'successful_progress': successful_runs[run_id],
                'independent_cleanup_evidence': [str(path) for path in cleanup_paths],
                'independent_cleanup_ok': cleanup_ok,
                'files': sorted(items, key=lambda item: item['path']),
                'candidate_bytes': sum(item['size_bytes'] for item in items),
                'decision': 'eligible_pending_current_remote_recheck' if cleanup_ok
                            else 'hold_missing_independent_cleanup_evidence',
            })

for host in candidates:
    candidates[host].sort(key=lambda item: (item['run_id'], item['files'][0]['path']))

manifest = {
    'schema_version': 1,
    'status': 'review-only-no-delete',
    'created_wall': time.time(),
    'root': str(ROOT),
    'hosts': sorted(TARGET_HOSTS),
    'payload': {'size_bytes': PAYLOAD_SIZE, 'sha256': PAYLOAD_SHA},
    'b2': {
        'run_id': B2_RUN_ID,
        'comm_root': '/srv/wfb-ng-fl/' + B2_COMM,
        'policy': 'retain one original per target host; never candidate for deletion',
    },
    'protection': {
        'excluded': ['failure partials', 'all logs', 'all evidence', 'real training artifacts', 'install-cache', 'APT caches', 'symlinks', 'nlink != 1'],
        'requires_current_remote_recheck': True,
        'requires_current_b2_recheck': True,
        'requires_archive_before_unlink': True,
        'requires_exact_unlink_guard': True,
    },
    'source_exact_evidence_file_count': source_count,
    'successful_run_count': len(successful_runs),
    'candidates': candidates,
    'summary': {
        host: {
            'candidate_run_count': len(rows),
            'candidate_file_count': sum(len(row['files']) for row in rows),
            'candidate_bytes': sum(row['candidate_bytes'] for row in rows),
            'cleanup_evidence_ok_count': sum(row['independent_cleanup_ok'] for row in rows),
        } for host, rows in candidates.items()
    },
    'note': 'Local evidence review only. No remote file existence/hash probe and no unlink performed by this script.',
}
(ROOT / 'fl05-fl10-storage-candidate-review.json').write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(manifest['summary'], ensure_ascii=False, indent=2))
