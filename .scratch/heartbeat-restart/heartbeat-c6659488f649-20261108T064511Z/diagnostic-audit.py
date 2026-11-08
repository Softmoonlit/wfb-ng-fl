"""在线目标的冻结源码发布节点执行器；不含软件依赖或驱动变更。"""
import hashlib
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
assert host in {'fl00', 'fl01', 'fl02', *(f'fl{i:02d}' for i in range(4, 11))}
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
assert len(cards) == 1, [str(p) for p in cards]
iface = cards[0].name
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
    repair = None
    if not native_pids:
        # 本轮 fl05 已换卡：仅接受旧服务明确绑定已消失网卡的可核实状态。
        pid = run(['systemctl', 'show', unit, '-p', 'MainPID', '--value']).strip()
        daemon_args = (pathlib.Path('/proc') / pid / 'cmdline').read_bytes().split(b'\0')
        assert host == 'fl05' and b'--interface' in daemon_args, daemon_args
        old_iface = daemon_args[daemon_args.index(b'--interface') + 1].decode()
        assert old_iface != iface and not (pathlib.Path('/sys/class/net') / old_iface).exists()
        repair = {'reason': 'replaced_card_stale_interface', 'old_iface': old_iface,
                  'current_iface': iface, 'daemon_pid': pid, 'daemon_args': [s.decode() for s in daemon_args if s]}
    else:
        assert len(native_pids) == 1
        assert sha(native_pids[0] / 'exe') == native_sha
    emit({'host': host, 'unit': state, 'iface': iface,
          'native_pid': native_pids[0].name if native_pids else None, 'repair': repair,
          'native_sha': native_sha, 'control_sha': sha(installed / 'control.py'),
          'radio': run(['iw', 'dev', iface, 'info']),
          'nvpmodel': run(['/usr/sbin/nvpmodel', '-q'])})
elif phase == 'prepare':
    assert sha(stage / 'source.tar') == archive_sha
    root.mkdir(parents=True)
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
    emit({'host': host, 'phase': phase, 'archive_sha': archive_sha, 'root': str(root)})
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
    expected = {str(p.relative_to(installed)): sha(p) for p in installed.rglob('*')
                if p.is_file() and p.suffix in {'.py', '.json'}}
    for path in (source / 'wfb_ng/fl').glob('*.py'):
        assert sha(path) == sha(installed / path.name)
    assert json.loads((installed / 'build_identity.json').read_text())['commit'] == commit
    (root / 'manifest.json').write_text(json.dumps({'commit': commit, 'files': expected,
        'installed': str(installed), 'native_sha': native_sha}, indent=2))
    wrapper = '''import hashlib,importlib,json,pathlib,sys,os
root=pathlib.Path(__file__).resolve().parent
manifest=json.loads((root/'manifest.json').read_text())
base=pathlib.Path(manifest['installed'])
for name,digest in manifest['files'].items():
 assert hashlib.sha256((base/name).read_bytes()).hexdigest()==digest,name
role=sys.argv.pop(1)
module=importlib.import_module('wfb_ng.fl.'+role+'_daemon')
loaded={name:{'path':m.__file__,'sha256':hashlib.sha256(pathlib.Path(m.__file__).read_bytes()).hexdigest()} for name,m in sys.modules.items() if name.startswith('wfb_ng.fl.') and getattr(m,'__file__',None) and m.__file__.endswith('.py')}
for name,item in loaded.items():
 assert pathlib.Path(item['path']).parent==base,(name,item['path'])
 assert item['sha256']==manifest['files'][pathlib.Path(item['path']).name]
(root/'loaded.json').write_text(json.dumps({'pid':os.getpid(),'commit':manifest['commit'],'modules':loaded}))
module.main()
'''
    (root / 'daemon.py').write_text(wrapper)
    args = ['/usr/bin/python3', '-I', '-B', str(root / 'daemon.py'), role]
    if role == 'server':
        args += ['--config', '/etc/wfb-ng-fl/server.json', '--channel', '149',
                 '--bandwidth-mhz', '40', '--txpower', '18', '--downlink-mcs', '3',
                 '--uplink-mcs', '4', '--uftp-rate-kbps', '15000', '--interface', iface]
    else:
        args += ['--config', '/etc/wfb-ng-fl/node.json', '--work-dir', '/srv/wfb-ng-fl/client',
                 '--channel', '149', '--bandwidth-mhz', '40', '--radio-txpower-dbm', '18',
                 '--uplink-mcs', '4', '--interface', iface]
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
    loaded = json.loads((root / 'loaded.json').read_text())
    assert loaded['pid'] == pid and loaded['commit'] == commit
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
    emit({'host': host, 'pid': pid, 'native_pid': path.name, 'loaded': loaded, 'native_sha': native_sha, 'native_args': [s.decode() for s in args if s], 'radio_runtime': radio_runtime, 'radio': info, 'diagnostic': True})
    assert 'txpower -18.00 dBm' in info
    assert 'channel 149 ' in info and 'width: 40 MHz' in info
    emit({'host': host, 'pid': pid, 'native_pid': path.name, 'loaded': loaded,
          'native_sha': native_sha, 'native_args': [s.decode() for s in args if s],
          'radio_runtime': radio_runtime, 'radio': info})
else:
    raise ValueError(phase)
