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
    worker.verify_inputs(driver, require_release=False)
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
        'bundle_manifest': manifest,
        'execution_authorized_after_parent_release': True,
    }
    (ROOT / 'local-preparation-verification.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
