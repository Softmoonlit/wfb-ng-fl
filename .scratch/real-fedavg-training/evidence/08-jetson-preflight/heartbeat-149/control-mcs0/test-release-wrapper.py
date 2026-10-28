import copy
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
from unittest.mock import patch

script = Path('/home/fl/projects/wfb-ng-fl/.scratch/real-fedavg-training/evidence/08-jetson-preflight/heartbeat-149/release-node.py')
spec = importlib.util.spec_from_file_location('release_node', script)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

with tempfile.TemporaryDirectory(prefix='release-wrapper-test-') as temp:
    base = Path(temp)
    class FakePath:
        def __init__(self, value): self.virtual = PurePosixPath(str(value))
        def __str__(self): return str(self.virtual)
        def __fspath__(self): return str(self.real)
        @property
        def real(self): return base / str(self.virtual).lstrip('/')
        def __truediv__(self, other): return FakePath(self.virtual / str(other))
        def __eq__(self, other): return str(self) == str(other)
        def __lt__(self, other): return str(self) < str(other)
        @property
        def parts(self): return self.virtual.parts
        def is_absolute(self): return self.virtual.is_absolute()
        @property
        def parents(self): return tuple(FakePath(p) for p in self.virtual.parents)
        @property
        def name(self): return self.virtual.name
        def lstat(self):
            values = list(self.real.lstat())
            values[4] = owners.get(str(self), 0)  # fake root-owned filesystem, retain real mode/type
            return os.stat_result(values)
        def read_text(self): return self.real.read_text()
        def read_bytes(self): return self.real.read_bytes()
        def is_file(self): return self.real.is_file()
        def glob(self, pattern):
            return (FakePath('/' + str(p.relative_to(base))) for p in self.real.glob(pattern))
    owners = {}
    def put(path, value):
        path = FakePath(path)
        path.real.parent.mkdir(parents=True, exist_ok=True)
        for parent in path.real.parents:
            if parent == base.parent: break
            parent.chmod(0o700)
        path.real.write_text(value if isinstance(value, str) else json.dumps(value))
        path.real.chmod(0o600)
        return path
    commit = '607c064150b661d1e8a568e29a101998ac06b0b7'
    root = FakePath('/srv/wfb-ng-fl/release-' + commit + '-20261028T115651441253Z')
    wrapper = root / 'release-node.py'
    put(wrapper, 'frozen wrapper\n')
    put(root / 'source' / m.MANAGEMENT / 'release-node.py', 'frozen wrapper\n')
    put(root / 'source/scripts/install/v8-wfb-ng-init.py', 'init\n')
    put(root / 'source/wfb_ng/fl/client_daemon.py', 'client\n')
    put(root / 'source/wfb_ng/fl/build_identity.json', {'schema_version': 1, 'commit': commit})
    installed = put('/usr/lib/python3/dist-packages/wfb_ng/fl/client_daemon.py', 'client\n')
    args = ['--config', '/etc/wfb-ng-fl/node.json', '--work-dir', '/srv/work']
    argv = [m.PYTHON, '-B', str(wrapper), 'daemon', commit, 'fl01', str(root), '--', *args]
    pid = 1234
    proc = FakePath('/proc/1234')
    put(proc / 'cmdline', '\0'.join(argv) + '\0')
    put(proc / 'stat', '1234 (python worker) ' + ' '.join(['S'] + ['0'] * 18 + ['4242']))
    (proc / 'cwd').real.symlink_to('/')
    (proc / 'exe').real.symlink_to(os.path.realpath(m.PYTHON))
    process = {'pid': pid, 'tick': '4242', 'exe': os.path.realpath(m.PYTHON), 'argv': argv}
    with patch.object(m, 'Path', FakePath):
        expected = m.source_python(root / 'source')
        put(root / 'expected-python.json', expected)
        put(root / 'expected-args.json', args)
        loaded = {'pid': pid, 'tick': '4242', 'host': 'fl01', 'expectedCommit': commit,
                  'argv': args, 'cwd': '/', 'interpreter_exe': process['exe'],
                  'pythonLoaded': {'root': '/usr/lib/python3/dist-packages/wfb_ng',
                      'identity': {'schema_version': 1, 'commit': commit}, 'installed': expected,
                      'files': {'wfb_ng.fl.client_daemon': {'path': str(installed),
                          'relative': 'fl/client_daemon.py', 'sha256': m.sha(installed)}}}}
        put(root / 'python-loaded.json', loaded)
        def check(): return m.current_wrapper(process, 'fl01', FakePath('/proc'))
        def rejects(label, action):
            try: action()
            except (RuntimeError, FileNotFoundError) as exc: print('PASS reject', label, ':', exc)
            else: raise AssertionError('accepted ' + label)
        assert check()['args'] == args
        print('PASS valid current 607c064 wrapper and fake /proc')
        frozen = root / 'source' / m.MANAGEMENT / 'release-node.py'
        frozen.real.chmod(0o664); assert check()['args'] == args
        print('PASS root-owned frozen source 0664 under source 0700')
        (root / 'source').real.chmod(0o750); rejects('source root is not 0700', check); (root / 'source').real.chmod(0o700)
        owners[str(frozen)] = 1000; rejects('non-root source file', check); owners.clear()
        saved = frozen.real.read_text(); frozen.real.unlink(); frozen.real.symlink_to(wrapper.real)
        rejects('source file symlink', check); frozen.real.unlink(); put(frozen, saved); frozen.real.chmod(0o664)
        native_source = put(root / 'source/wfb_v6_uplink', 'native'); native_source.real.chmod(0o755)
        assert m.protected_source_file(root / 'source', 'wfb_v6_uplink') == native_source
        print('PASS root-owned native source 0755 under source 0700')
        for label, field, value in [('wrong loaded PID', 'pid', 999), ('wrong loaded host', 'host', 'fl02'),
                                    ('stale tick', 'tick', '4241'), ('wrong loaded commit', 'expectedCommit', 'a' * 40),
                                    ('wrong loaded args', 'argv', ['--bad'])]:
            bad = copy.deepcopy(loaded); bad[field] = value
            put(root / 'python-loaded.json', bad)
            rejects(label, check)
        put(root / 'python-loaded.json', loaded)
        argv[5] = 'fl02'; rejects('wrong argv host', check); argv[5] = 'fl01'
        process['pid'] = 999; rejects('wrong MainPID', check); process['pid'] = pid
        saved_argv = process['argv']; process['argv'] = ['python3', '/usr/bin/wfb-fl-client-daemon', *args]
        rejects('legacy launcher', check); process['argv'] = saved_argv
        argv[4] = 'a' * 40; rejects('root commit mismatch', check); argv[4] = commit
        put(root / 'expected-args.json', ['--bad']); rejects('expected args mutation', check); put(root / 'expected-args.json', args)
        put(proc / 'cmdline', '\0'.join([*argv, '--changed']) + '\0'); rejects('live proc cmdline mutation', check)
        put(proc / 'cmdline', '\0'.join(argv) + '\0')
        process['tick'] = '4241'; rejects('stale process tick', check); process['tick'] = '4242'
        put(wrapper, 'mutated wrapper'); rejects('wrapper mutation', check); put(wrapper, 'frozen wrapper\n')
        put(installed, 'mutated module'); rejects('loaded file mutation', check); put(installed, 'client\n')
        owners[str(root)] = 1000; rejects('non-root owned root', check); owners.clear()
        wrapper.real.chmod(0o666); rejects('writable wrapper', check); wrapper.real.chmod(0o600)
        original = wrapper.real.read_text(); wrapper.real.unlink(); wrapper.real.symlink_to((root / 'source' / m.MANAGEMENT / 'release-node.py').real)
        rejects('symlink wrapper', check); wrapper.real.unlink(); put(wrapper, original)
        drop = put('/etc/systemd/system/wfb-fl-client-daemon.service.d/zzzz-release-149.conf', m.release_dropin(argv))
        before = m.dropin_snapshot(drop, m.release_dropin(argv))
        m.unchanged_dropin(drop, before)
        print('PASS existing owned drop-in accepted for replacement')
        put(drop, before['text'] + '# mutation\n'); rejects('drop-in mutation', lambda: m.unchanged_dropin(drop, before))
        put(drop, '[Service]\nExecStart=/foreign\n'); rejects('foreign drop-in', lambda: m.dropin_snapshot(drop, m.release_dropin(argv)))
        put(drop, before['text']); drop.real.unlink(); rejects('missing drop-in', lambda: m.unchanged_dropin(drop, before))
        put(drop, before['text']); assert check()['args'] == args
        print('PASS valid wrapper remains accepted after restorations')
