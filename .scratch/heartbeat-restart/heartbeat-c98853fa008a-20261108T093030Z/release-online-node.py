"""在线目标的冻结源码发布节点执行器；不含软件依赖或驱动变更。"""
import hashlib
import ast
import importlib
import json
import os
import pathlib
import shutil
import socket
import struct
import subprocess
import sys
import sysconfig
import tarfile
import time
import urllib.request

phase, commit, release_id, archive_sha, native_sha = sys.argv[1:]
host = socket.gethostname()
assert host in {f'fl{i:02d}' for i in range(11)}
assert os.geteuid() == 0
assert len(commit) == 40 and all(c in '0123456789abcdef' for c in commit)
assert release_id.replace('-', '').isalnum()
root = pathlib.Path('/srv/wfb-ng-fl/releases') / release_id
stage = pathlib.Path('/tmp') / release_id
unit = 'wfb-fl-server-daemon.service' if host == 'fl00' else 'wfb-fl-client-daemon.service'
role = 'server' if host == 'fl00' else 'client'
purelib = pathlib.Path(sysconfig.get_path('purelib', vars={'base': '/usr', 'platbase': '/usr'}))
installed = purelib / 'wfb_ng/fl'


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def run(args, **kwargs):
    result = subprocess.run(args, text=True, capture_output=True, timeout=45, **kwargs)
    if result.returncode:
        raise RuntimeError(json.dumps({'args': args, 'exit': result.returncode,
                                       'out': result.stdout, 'err': result.stderr}))
    return result.stdout


def get_radio_runtime(port):
    request_id = 0x52474918
    request = struct.pack('!IB', request_id, 4)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(6)
        sock.sendto(request, ('127.0.0.1', port))
        raw, address = sock.recvfrom(4096)
    assert address == ('127.0.0.1', port) and len(raw) == struct.calcsize('!II7B')
    response = struct.unpack('!II7B', raw)
    assert response[0] == request_id and response[1] == 0
    return {'raw_hex': raw.hex(), 'address': list(address), 'request_hex': request.hex(),
            'req_id': response[0], 'rc': response[1], 'stbc': response[2],
            'ldpc': response[3], 'short_gi': response[4], 'bandwidth': response[5],
            'mcs_index': response[6], 'vht_mode': response[7], 'vht_nss': response[8]}


def emit(data):
    print(json.dumps(data, ensure_ascii=False), flush=True)


def interfaces():
    return [p for p in pathlib.Path('/sys/class/net').iterdir()
            if (p / 'device/driver').exists() and (p / 'device/driver').resolve().name == 'rtl88xxau_wfb']


cards = interfaces()
pid = run(['systemctl', 'show', unit, '-p', 'MainPID', '--value']).strip()
if phase in {'install', 'start'}:
    iface = (root / 'interface').read_text().strip()
else:
    assert int(pid) > 0
    bound_args = (pathlib.Path('/proc') / pid / 'cmdline').read_bytes().split(b'\0')
    assert b'--interface' in bound_args, bound_args
    iface = bound_args[bound_args.index(b'--interface') + 1].decode()
assert iface in {p.name for p in cards}, {'bound': iface, 'cards': [p.name for p in cards]}
assert sha('/usr/bin/wfb_v6_uplink') == native_sha
if phase == 'preflight':
    assert not root.exists()
    state = run(['systemctl', 'show', unit, '-p', 'ActiveState', '-p', 'MainPID', '-p', 'ExecStart'])
    assert 'ActiveState=active' in state
    if host == 'fl00':
        status = json.loads(urllib.request.urlopen('http://127.0.0.1:9090/api/v1/status', timeout=6).read())
        assert status.get('active_job') is None, status
        assert status.get('server_state') == 'IDLE', status

    else:
        identity = json.loads(pathlib.Path('/etc/wfb-ng-fl/node.json').read_text())
        assert identity['node_id'] == int(host[2:])
        assert identity['tun_ip'].split('/')[0] == '10.80.0.' + str(10 + int(host[2:]))
    native_pids = [p for p in pathlib.Path('/proc').iterdir() if p.name.isdigit()
                   and (p / 'exe').exists() and (p / 'exe').resolve() == pathlib.Path('/usr/bin/wfb_v6_uplink')]
    assert len(native_pids) == 1
    assert sha(native_pids[0] / 'exe') == native_sha
    emit({'host': host, 'unit': state, 'iface': iface,
          'native_pid': native_pids[0].name,
          'native_sha': native_sha, 'control_sha': sha(installed / 'control.py'),
          'radio': run(['iw', 'dev', iface, 'info']),
          'nvpmodel': run(['/usr/sbin/nvpmodel', '-q'])})
elif phase == 'prepare':
    assert sha(stage / 'source.tar') == archive_sha
    root.mkdir(parents=True)
    (root / 'interface').write_text(iface + '\n')
    old_args = [s.decode() for s in bound_args if s]
    options = old_args[old_args.index('--config'):]
    (root / 'actualargs.json').write_text(json.dumps(['/usr/bin/python3', '-I', '-B', '-m', 'wfb_ng.fl.' + role + '_daemon', *options]))
    (root / 'configuration-before.json').write_text(pathlib.Path('/etc/wfb-ng-fl/' + ('server.json' if role == 'server' else 'node.json')).read_text())
    (root / 'uftp-before.json').write_text(json.dumps({n: {'path': shutil.which(n), 'sha256': sha(shutil.which(n))} for n in ('uftp', 'uftpd')}))
    backup = root / 'backup'
    backup.mkdir()
    shutil.copytree(installed, backup / 'fl')
    shutil.copy2('/usr/bin/wfb_v6_uplink', backup / 'wfb_v6_uplink')
    shutil.copytree('/etc/wfb-ng-fl', backup / 'config')
    unit_dir = pathlib.Path('/etc/systemd/system') / (unit + '.d')
    if unit_dir.exists():
        shutil.copytree(unit_dir, backup / 'dropins')
    source = root / 'source'
    source.mkdir()
    with tarfile.open(stage / 'source.tar') as archive:
        members = archive.getmembers()
        names = [m.name for m in members]
        assert len(names) == len(set(names)), 'duplicate archive member'
        assert all(not pathlib.Path(m.name).is_absolute() and '..' not in pathlib.Path(m.name).parts
                   for m in members)
        assert all(m.isfile() or m.isdir() for m in members)
        archive.extractall(source)
        assert all((source / name).exists() and (not m.isfile() or (source / name).stat().st_size == m.size)
                   for m, name in zip(members, names))
    shutil.copy2('/usr/bin/wfb_v6_uplink', source / 'wfb_v6_uplink')
    for name in ('uftp', 'uftpd', 'make'):
        assert shutil.which(name), name
    runtime_files = sorted(str(p.relative_to(source / 'wfb_ng/fl'))
                           for p in (source / 'wfb_ng/fl').rglob('*') if p.is_file()
                           and p.suffix in {'.py', '.html', '.css', '.js'})
    assert 'cifar10_model.py' in runtime_files
    for name in runtime_files:
        if name.endswith('.py'):
            ast.parse((source / 'wfb_ng/fl' / name).read_text(), filename=name)
    check = """import sys,types,pathlib,importlib,importlib.util,json
base=pathlib.Path(sys.argv[1])
pkg=types.ModuleType('wfb_ng');pkg.__path__=[str(base/'wfb_ng')];sys.modules['wfb_ng']=pkg
importlib.import_module('wfb_ng.fl.server_daemon')
importlib.import_module('wfb_ng.fl.client_daemon')
assert 'torch' not in sys.modules and 'torchvision' not in sys.modules
import setuptools
print(json.dumps({'daemon_import_without_torch':True,'model_dependencies':{n:importlib.util.find_spec(n) is not None for n in ['torch','torchvision']}}))
"""
    deps = json.loads(run(['/usr/bin/python3', '-I', '-B', '-c', check, str(source)]))
    (root / 'runtime-files.json').write_text(json.dumps(runtime_files))
    emit({'host': host, 'phase': phase, 'archive_sha': archive_sha, 'root': str(root),
          'runtime_files': runtime_files, 'dependencies': deps, 'iface': iface})
elif phase == 'stop':
    run(['systemctl', 'stop', unit])
    assert run(['systemctl', 'show', unit, '-p', 'MainPID', '--value']).strip() == '0'
    natives = [p for p in pathlib.Path('/proc').glob('[0-9]*/exe')
               if p.exists() and 'wfb_v6_uplink' in os.readlink(p)]
    assert not natives, [str(p) for p in natives]
    emit({'host': host, 'phase': phase})
elif phase == 'install':
    source = root / 'source'
    expected_names = {p.name for p in (source / 'wfb_ng/fl').glob('*.py')}
    for path in installed.glob('*.py'):
        if path.name not in expected_names:
            path.unlink()
    for cache in installed.rglob('__pycache__'):
        shutil.rmtree(cache)
    env = dict(os.environ, COMMIT=commit, VERSION='26.11.6', PREFIX='/usr',
               PYTHON='/usr/bin/python3', PYTHONDONTWRITEBYTECODE='1')
    run(['sh', 'scripts/install/install-v8.sh'], cwd=source, env=env)
    runtime_files = json.loads((root / 'runtime-files.json').read_text())
    actual_files = sorted(str(p.relative_to(installed)) for p in installed.rglob('*')
                          if p.is_file() and p.suffix in {'.py', '.html', '.css', '.js'})
    assert actual_files == runtime_files, {'actual': actual_files, 'expected': runtime_files}
    for name in runtime_files:
        assert sha(source / 'wfb_ng/fl' / name) == sha(installed / name), name
    expected = {str(p.relative_to(installed)): sha(p) for p in installed.rglob('*')
                if p.is_file() and p.suffix in {'.py', '.json', '.html', '.css', '.js'}}
    for path in (source / 'wfb_ng/fl').glob('*.py'):
        assert sha(path) == sha(installed / path.name)
    assert json.loads((installed / 'build_identity.json').read_text())['commit'] == commit
    (root / 'manifest.json').write_text(json.dumps({'commit': commit, 'files': expected,
        'installed': str(installed), 'native_sha': native_sha}, indent=2))
    args = json.loads((root / 'actualargs.json').read_text())
    drop = pathlib.Path('/etc/systemd/system') / (unit + '.d') / 'zzzzz-heartbeat-release.conf'
    drop.parent.mkdir(parents=True, exist_ok=True)
    drop.write_text('[Service]\nExecStart=\nExecStart=' + ' '.join(args) +
                    '\nWorkingDirectory=/\nUnsetEnvironment=PYTHONPATH PYTHONHOME\nKillMode=control-group\n')
    run(['systemctl', 'daemon-reload'])
    emit({'host': host, 'phase': phase, 'commit': commit, 'files': expected,
          'args': args, 'native_sha': sha('/usr/bin/wfb_v6_uplink')})
elif phase == 'start':
    run(['systemctl', 'start', unit])
    emit({'host': host, 'phase': phase})
elif phase == 'audit':
    pid = int(run(['systemctl', 'show', unit, '-p', 'MainPID', '--value']).strip())
    assert pid > 0
    assert run(['systemctl', 'is-active', unit]).strip() == 'active'
    actualargs = [s.decode() for s in (pathlib.Path('/proc') / str(pid) / 'cmdline').read_bytes().split(b'\0') if s]
    assert actualargs == json.loads((root / 'actualargs.json').read_text()), actualargs
    assert (pathlib.Path('/proc') / str(pid) / 'exe').resolve() == pathlib.Path('/usr/bin/python3').resolve()
    probe = 'import importlib,json; m=importlib.import_module("wfb_ng.fl.' + role + '_daemon"); print(json.dumps({"module":m.__file__}))'
    loaded = json.loads(run(['/usr/bin/python3', '-I', '-B', '-c', probe]))
    assert pathlib.Path(loaded['module']).parent == installed
    config_path = pathlib.Path('/etc/wfb-ng-fl/' + ('server.json' if role == 'server' else 'node.json'))
    config = json.loads(config_path.read_text())
    loaded.update(pid=pid, commit=commit, actualargs=actualargs, config=config, config_sha256=sha(config_path), verification='standard module process arguments plus isolated interpreter source resolution')
    (root / 'loaded.json').write_text(json.dumps(loaded, indent=2))
    native = []
    for p in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
        try:
            parts = p.read_bytes().split(bytes([0]))
            if parts and parts[0] == b'/usr/bin/wfb_v6_uplink':
                native.append((p.parent, parts))
        except OSError:
            pass
    assert len(native) == 1, len(native)
    path, args = native[0]
    assert sha(path / 'exe') == native_sha
    assert args[args.index(b'--air-interface') + 1].decode() == iface
    if role == 'server':
        assert args[args.index(b'--grant-duration-ms') + 1] == str(config.get('grant_duration_ms', 10)).encode()
        assert args[args.index(b'--guard-interval-ms') + 1] == str(config.get('guard_interval_ms', 10)).encode()
        assert b'/usr/bin/wfb_v6_uplink' == args[0]
    assert b'--radio-short-gi' not in args
    assert b'--radio-control-port' in args
    control_port = int(args[args.index(b'--radio-control-port') + 1])
    radio_runtime = get_radio_runtime(control_port)
    assert radio_runtime['short_gi'] == 0, radio_runtime
    assert radio_runtime['bandwidth'] == 40, radio_runtime
    assert radio_runtime['mcs_index'] == (3 if role == 'server' else 4), radio_runtime
    manifest = json.loads((root / 'manifest.json').read_text())
    assert all(sha(installed / name) == digest for name, digest in manifest['files'].items())
    service_args = run(['systemctl', 'show', unit, '-p', 'ExecStart'])
    assert '--radio-short-gi' not in service_args
    assert '--channel 149' in service_args and ('--bandwidth-mhz 40' in service_args or '--radio-bandwidth 40' in service_args)
    info = run(['iw', 'dev', iface, 'info'])
    assert 'txpower -18.00 dBm' in info
    assert 'channel 149 ' in info and 'width: 40 MHz' in info
    power = run(['/usr/sbin/nvpmodel', '-q'])
    assert '15W' in power, power
    uftp = {n: {'path': shutil.which(n), 'sha256': sha(shutil.which(n))} for n in ('uftp', 'uftpd')}
    assert uftp == json.loads((root / 'uftp-before.json').read_text())
    assert sha(root / 'source/wfb_v6_uplink') == native_sha
    assert all(sha(root / 'source/wfb_ng/fl' / n) == sha(installed / n) for n in json.loads((root / 'runtime-files.json').read_text()))
    emit({'host': host, 'pid': pid, 'power': power, 'uftp': uftp, 'actualargs': actualargs, 'native_pid': path.name, 'loaded': loaded,
          'native_sha': native_sha, 'native_args': [s.decode() for s in args if s],
          'radio_runtime': radio_runtime, 'radio': info})
else:
    raise ValueError(phase)
