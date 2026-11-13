#!/usr/bin/env python3
import concurrent.futures
import json
import pathlib
import re
import time
import importlib.util

ROOT = pathlib.Path(__file__).resolve().parent
HOSTS = ['fl00', 'fl01', 'fl02', 'fl03', 'fl05', 'fl06', 'fl07', 'fl08', 'fl09', 'fl10']
spec = importlib.util.spec_from_file_location('fresh_driver_packagekit', ROOT / 'driver-base.py')
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)
d.MATRIX['parent_GO_verified'] = True
d.MATRIX['deadline_monotonic'] = time.monotonic() + 300

CODE = r'''import json, re, socket, subprocess

def run(argv):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=5)
        return dict(returncode=p.returncode, stdout=p.stdout, stderr=p.stderr)
    except Exception as exc:
        return dict(error=repr(exc))

base = ['busctl', 'call', 'org.freedesktop.PackageKit', '/org/freedesktop/PackageKit',
        'org.freedesktop.PackageKit', 'GetTransactionList']
listing = run(base)
paths = re.findall(r'"(/[^" ]+)"', listing.get('stdout', ''))
props = ['Role', 'Status', 'Percentage', 'AllowCancel', 'CallerActive',
         'LastPackage', 'DownloadSizeRemaining', 'RemainingTime', 'TransactionFlags']
transactions = []
for path in paths:
    values = {}
    for prop in props:
        values[prop] = run(['busctl', 'get-property', 'org.freedesktop.PackageKit', path,
                            'org.freedesktop.PackageKit.Transaction', prop])
    introspect = run(['busctl', 'introspect', 'org.freedesktop.PackageKit', path])
    transactions.append(dict(path=path, properties=values, introspect=introspect))
print(json.dumps(dict(host=socket.gethostname(), listing=listing, paths=paths,
                      transactions=transactions), ensure_ascii=False))
'''

def one(host):
    return d.remote(host, CODE, timeout=15)
with concurrent.futures.ThreadPoolExecutor(max_workers=len(HOSTS)) as pool:
    rows = list(pool.map(one, HOSTS))
value = dict(wall=time.time(), hosts=HOSTS, rows=rows,
             note='DBus current transaction properties only; no historical journal inference and no transaction mutation')
(ROOT / 'packagekit-current-readonly.json').write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({row.get('host'): dict(returncode=row.get('returncode'), error=row.get('error'), stderr=row.get('stderr')) for row in rows}, ensure_ascii=False))
