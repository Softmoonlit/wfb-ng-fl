#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json
import os
from pathlib import Path
import re
import shutil
from setuptools import setup, find_packages
from setuptools.command.build_py import build_py


class BuildWithIdentity(build_py):
    def run(self):
        super().run()
        # 每次构建都复制本轮身份，避免 build_py 按秒级 mtime/相同大小跳过更新。
        shutil.copyfile(build_identity_path, Path(self.build_lib) / build_identity_path)


# Mark deb package as binary
try:
    import stdeb.util
    class DebianInfo(stdeb.util.DebianInfo):
        def __init__(self, *args, **kwargs):
            kwargs['has_ext_modules'] = True
            super().__init__(*args, **kwargs)

    stdeb.util.DebianInfo = DebianInfo
except ImportError:
    pass

version = os.environ.get('VERSION')
commit = os.environ.get('COMMIT')
install_data_files = not bool(os.environ.get('OMIT_DATA_FILES'))

if not version:
    raise SystemExit('VERSION is required')
if not commit or re.fullmatch(r'[0-9a-f]{40}', commit) is None:
    raise SystemExit('COMMIT must be a full 40-character lowercase Git SHA')

build_identity_path = Path('wfb_ng/fl/build_identity.json')
build_identity = {'schema_version': 1, 'commit': commit}
# An sdist has no Git checkout. Its packaged identity is the source of truth:
# refuse to relabel that source when stdeb invokes setup.py again.
if Path('PKG-INFO').exists():
    try:
        packaged_identity = json.loads(build_identity_path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise SystemExit('Missing or invalid sdist build identity: %s' % exc)
    if (not isinstance(packaged_identity, dict)
            or type(packaged_identity.get('schema_version')) is not int
            or packaged_identity != build_identity):
        raise SystemExit('sdist build identity does not match COMMIT and schema_version=1')
else:
    build_identity_path.write_text(
        json.dumps(build_identity, sort_keys=True) + '\n', encoding='utf-8')

with open('wfb_ng/conf/site.cfg', 'w') as fd:
    fd.write("# Don't make any changes here, use local.cfg instead!\n\n[common]\nversion = %r\ncommit = %r\n" % (version, commit))

def _long_description():
    with open('docs/README.md', encoding='utf-8') as fd:
        return fd.read()

setup(
    url="http://wfb-ng.org",
    name="wfb_ng",
    version=version,
    packages=find_packages(exclude=["*.tests", "*.tests.*", "tests.*", "tests"]),
    cmdclass={'build_py': BuildWithIdentity},
    zip_safe=False,
    entry_points={'console_scripts': ['wfb-fl-server=wfb_ng.fl.service:server_main',
                                      'wfb-fl-client=wfb_ng.fl.service:client_main',
                                      'wfb-fl-client-daemon=wfb_ng.fl.client_daemon:main',
                                      'wfb-fl-server-daemon=wfb_ng.fl.server_daemon:main',
                                      'wfb-fl-radio=wfb_ng.fl.radio:main']},
    package_data={'wfb_ng.conf': ['master.cfg', 'site.cfg'],
                  'wfb_ng.fl': ['build_identity.json']},
    data_files = [('/usr/bin', ['wfb_v6_uplink']),
                  ('/lib/systemd/system', ['scripts/systemd/wfb-fl-server.service',
                                           'scripts/systemd/wfb-fl-client.service',
                                           'scripts/systemd/wfb-fl-client-daemon.service',
                                           'scripts/systemd/wfb-fl-server-daemon.service']),
                  ('/etc/wfb-ng', ['scripts/default/fl-server.json',
                                   'scripts/default/fl-client.json']),
                  ('/etc/wfb-ng-fl', ['scripts/default/node.json',
                                       'scripts/default/server.json']),
                  ('/etc/sysctl.d', ['scripts/sysctl/98-wifibroadcast.conf'])] if install_data_files else [],

    keywords="wfb-ng, wifibroadcast",
    author="Vasily Evseenko",
    author_email="svpcom@p2ptech.org",
    description="Long-range packet radio link based on raw WiFi radio",
    long_description=_long_description(),
    long_description_content_type='text/markdown',
    license="GPLv3",
)
