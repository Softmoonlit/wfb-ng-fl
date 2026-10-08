#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
from setuptools import setup, find_packages

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

assert version and commit

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
    zip_safe=False,
    entry_points={'console_scripts': ['wfb-fl-server=wfb_ng.fl.service:server_main',
                                      'wfb-fl-client=wfb_ng.fl.service:client_main']},
    package_data={'wfb_ng.conf': ['master.cfg', 'site.cfg']},
    data_files = [('/usr/bin', ['wfb_v6_uplink']),
                  ('/lib/systemd/system', ['scripts/systemd/wfb-fl-server.service',
                                           'scripts/systemd/wfb-fl-client.service']),
                  ('/etc/wfb-ng', ['scripts/default/fl-server.json',
                                   'scripts/default/fl-client.json']),
                  ('/etc/sysctl.d', ['scripts/sysctl/98-wifibroadcast.conf'])] if install_data_files else [],

    keywords="wfb-ng, wifibroadcast",
    author="Vasily Evseenko",
    author_email="svpcom@p2ptech.org",
    description="Long-range packet radio link based on raw WiFi radio",
    long_description=_long_description(),
    long_description_content_type='text/markdown',
    license="GPLv3",
)
