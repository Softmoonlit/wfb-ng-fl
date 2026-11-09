#!/usr/bin/env python3
"""Offline preparation checks. Never contacts nodes or starts a worker."""
import hashlib
import importlib.util
import json
import py_compile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPT_NAMES = [
    'wrapper.py', 'worker.py', 'helpers.py', 'driver-base.py',
    'storage.py', 'runner.py', 'node_agent.py', 'recover.py', 'check-all-ready.py',
]


def load(name):
    spec = importlib.util.spec_from_file_location('offline_' + name.replace('-', '_'), ROOT / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def bundle_sha():
    digest = hashlib.sha256()
    for name in ['runner.py', 'node_agent.py', 'recover.py']:
        digest.update(name.encode() + b'\0' + (ROOT / name).read_bytes())
    return digest.hexdigest()


def main():
    for name in SCRIPT_NAMES:
        py_compile.compile(str(ROOT / name), doraise=True)
    driver = load('driver-base.py')
    wrapper = load('wrapper.py')
    helpers = load('helpers.py')
    for label, source in [
        ('driver-base.AUDIT_CODE', driver.AUDIT_CODE),
        ('wrapper.PROBE', wrapper.PROBE),
        ('helpers.POLL_CODE', helpers.POLL_CODE),
    ]:
        compile(source, '<' + label + '>', 'exec')
    manifest = json.loads((ROOT / 'bundle-manifest.json').read_text())
    actual_bundle = bundle_sha()
    if actual_bundle != manifest['bundle_sha256']:
        raise RuntimeError('bundle manifest mismatch')
    for name, expected in manifest['files'].items():
        actual = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError('bundle file hash mismatch: ' + name)
    worker = load('worker.py')
    storage = load('storage.py')
    worker.verify_inputs(driver, require_release=False)
    expected_batchroot = Path('/home/fl/projects/wfb-ng-fl/.scratch/fl-testbed-magazine-draft/evidence/grant-fec-nine-current-hardware-20261109T063029Z')
    if storage.BATCHROOT != expected_batchroot or not storage.SOURCE.is_file():
        raise RuntimeError('storage archived-success source root mismatch')
    storage_bases = [
        'fec14-remaining', 'fec12', 'fec14-matched-completion',
        'selected-validation',
        'selected-validation-after-client-restart-resume-20261109T1833Z',
        'selected-validation-after-storage-evidence-continuation-20261109T1840Z',
        'selected-validation-after-fl02-restart-resume-20261109T1915Z',
        'selected-validation-after-apt-generated-cache-reclamation-20261109T1930Z',
        'selected-validation-final-two-with-cache-hold',
    ]
    missing_bases = [str(expected_batchroot / name) for name in storage_bases
                     if not (expected_batchroot / name).is_dir()]
    if missing_bases:
        raise RuntimeError('storage reclaim base missing: ' + repr(missing_bases))
    verification = json.loads((ROOT / 'input-verification.json').read_text())
    if verification.get('execution_ready') is not True:
        raise RuntimeError('published parent release must be execution-ready')
    if (ROOT / 'STOP').exists() or (ROOT / 'PAUSE').exists():
        raise RuntimeError('fresh root contains an execution marker')
    result = {
        'ok': True,
        'offline_only': True,
        'py_compile': True,
        'embedded_code_compile': True,
        'verify_inputs': verification,
        'storage_index': {
            'batchroot': str(storage.BATCHROOT),
            'source': str(storage.SOURCE),
            'source_exists': storage.SOURCE.is_file(),
            'reclaim_bases': [str(storage.BATCHROOT / name) for name in storage_bases],
        },
        'bundle_manifest': manifest,
        'execution_authorized_after_parent_release': True,
    }
    (ROOT / 'local-preparation-verification.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
