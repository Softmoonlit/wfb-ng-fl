#!/usr/bin/env python3
"""四端发布的节点执行器，操作者应从 release-four.py 进入。

标准输出只返回一个 JSON 结果；命令输出与诊断写入标准错误。
daemon 子命令也是实际服务启动器：同一解释器在进入 main() 前记录
真正已导入的模块及摘要，不用其他进程的 find_spec 充当已加载证据。
"""
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import time

PYTHON = '/usr/bin/python3'
VERSION = '0.0.0'
NATIVE = Path('/usr/bin/wfb_v6_uplink')
RADIO = dict(channel=149, bandwidth_mhz=40, radio_txpower_dbm=14,
             downlink_mcs=3, uplink_mcs=4, uftp_rate_kbps=15000)
WHITELIST_IDS = tuple(range(1, 11))
PARTICIPANT_IDS = (1, 2)
UNITS = ['wfb-fl-server.service', 'wfb-fl-client.service',
         'wfb-fl-server-daemon.service', 'wfb-fl-client-daemon.service']


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path = Path(path)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temp.chmod(0o600)
    os.replace(temp, path)


def run(args, timeout=60, check=True, **kwargs):
    print('ARGV', json.dumps(args), file=sys.stderr, flush=True)
    env = dict(kwargs.pop('env', os.environ))
    env['LC_ALL'] = 'C'
    env['LANG'] = 'C'
    kwargs['env'] = env
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, **kwargs)
    record = {'argv': args, 'rc': result.returncode, 'out': result.stdout, 'err': result.stderr}
    print(json.dumps(record, ensure_ascii=False), file=sys.stderr, flush=True)
    if check:
        result.check_returncode()
    return record


def props(unit):
    result = run(['systemctl', 'show', unit, '-p', 'MainPID', '-p', 'ActiveState',
                  '-p', 'SubState', '-p', 'ControlGroup', '-p', 'KillMode', '-p', 'User', '-p', 'ExecStart'])
    return dict(line.split('=', 1) for line in result['out'].splitlines() if '=' in line)


def tick(pid):
    # 进程名称可能含有空格或括号，必须从最后一个右括号之后解析。
    return (Path('/proc') / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()[19]


def processes():
    rows = []
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():
            continue
        try:
            argv = [x.decode() for x in (p / 'cmdline').read_bytes().split(b'\0') if x]
            status = (p / 'status').read_text()
            exe = os.readlink(p / 'exe')
            row = {'pid': int(p.name), 'ppid': int(re.search(r'^PPid:\s+(\d+)', status, re.M)[1]),
                   'tick': tick(p.name), 'exe': exe, 'argv': argv}
            if 'wfb_v6_uplink' in exe:
                row['exe_sha256'] = sha(p / 'exe')
            rows.append(row)
        except (FileNotFoundError, ProcessLookupError):
            continue
    return rows


def option(args, flag):
    positions = [i for i, value in enumerate(args) if value == flag or value.startswith(flag + '=')]
    require(len(positions) == 1, 'need one explicit argument: ' + flag)
    i = positions[0]
    return args[i].split('=', 1)[1] if '=' in args[i] else args[i + 1]


def replace_option(args, flag, value):
    result = []
    i = 0
    while i < len(args):
        if args[i] == flag:
            require(i + 1 < len(args), 'missing value: ' + flag)
            i += 2
        elif args[i].startswith(flag + '='):
            i += 1
        else:
            result.append(args[i])
            i += 1
    return result + [flag, str(value)]


def systemd_word(value):
    require(not any(c in value for c in '\0\n\r'), 'unsafe unit argument')
    # 按 systemd 的引号和变量展开规则编码，不能用 shell 引号代替。
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'


def python_snapshot():
    import wfb_ng
    root = Path(wfb_ng.__file__).resolve().parent
    identity = json.loads((root / 'fl/build_identity.json').read_text())
    files = {}
    for name, module in sorted(sys.modules.items()):
        if name != 'wfb_ng' and not name.startswith('wfb_ng.'):
            continue
        filename = getattr(module, '__file__', None)
        require(filename is not None, 'module has no file: ' + name)
        path = Path(filename).resolve()
        require(path.suffix == '.py' and path.is_relative_to(root), 'shadowed/bytecode-only module: ' + name)
        files[name] = {'path': str(path), 'relative': str(path.relative_to(root)), 'sha256': sha(path)}
    # 校验所有安装的 Python 与静态文件，包括尚未导入的模块。
    installed = {'__init__.py': sha(root / '__init__.py')}
    installed.update({str(p.relative_to(root)): sha(p) for p in sorted((root / 'fl').rglob('*'))
                      if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'})
    return {'root': str(root), 'identity': identity, 'files': files, 'installed': installed,
            'identity_sha256': sha(root / 'fl/build_identity.json')}


def validate_python(snapshot, expected, commit):
    require(snapshot['identity'] == {'schema_version': 1, 'commit': commit}, 'Python commit identity mismatch')
    require(snapshot['installed'] == expected, 'installed Python/static files differ from frozen source (or stale files remain)')
    for item in snapshot['files'].values():
        require(expected.get(item['relative']) == item['sha256'], 'loaded module differs from frozen source')
    require(snapshot['files'], 'no loaded Python evidence')


MANAGEMENT = Path('.scratch/real-fedavg-training/evidence/08-jetson-preflight/heartbeat-149')


def protected_path(path, directory=False):
    # 拒绝符号链接和非 root 可写的路径，包括每一级父目录。
    path = Path(path)
    for item in (path, *path.parents):
        info = item.lstat()
        require(info.st_uid == 0 and not info.st_mode & 0o022,
                'unprotected release path: ' + str(item))
        require(stat.S_ISDIR(info.st_mode) if item != path or directory else stat.S_ISREG(info.st_mode),
                'unexpected release path type: ' + str(item))
    return path


def protected_source_file(source, relative):
    source = protected_path(source, directory=True)
    require(stat.S_IMODE(source.lstat().st_mode) == 0o700, 'source root must be private 0700')
    relative = Path(relative)
    require(not relative.is_absolute() and relative.parts and '..' not in relative.parts,
            'invalid source-relative path')
    path = source
    for index, part in enumerate(relative.parts):
        path = path / part
        info = path.lstat()
        require(info.st_uid == 0, 'source component must be root owned: ' + str(path))
        require(stat.S_ISREG(info.st_mode) if index == len(relative.parts) - 1 else stat.S_ISDIR(info.st_mode),
                'unexpected source path type: ' + str(path))
    return path


def source_python(source):
    data = {'__init__.py': sha(source / 'scripts/install/v8-wfb-ng-init.py')}
    for p in sorted((source / 'wfb_ng/fl').glob('*.py')):
        data['fl/' + p.name] = sha(p)
    for p in sorted((source / 'wfb_ng/fl/static').glob('*')):
        if p.is_file():
            data['fl/static/' + p.name] = sha(p)
    data['fl/build_identity.json'] = sha(source / 'wfb_ng/fl/build_identity.json')
    return data


def release_dropin(command):
    return ('[Service]\nExecStart=\nExecStart=' + ' '.join(systemd_word(x) for x in command) +
            '\nEnvironment=PYTHONDONTWRITEBYTECODE=1\nKillMode=control-group\n')


def dropin_snapshot(path, expected_text):
    protected_path(path)
    content = path.read_bytes()
    require(content == expected_text.encode(), 'release drop-in differs from owned wrapper command')
    return {'path': str(path), 'sha256': hashlib.sha256(content).hexdigest(), 'text': expected_text}


def unchanged_dropin(path, before):
    require(before['path'] == str(path), 'release drop-in path changed')
    current = dropin_snapshot(path, before['text'])
    require(current == before, 'release drop-in changed since preflight')


def current_wrapper(process, host, proc_root=Path('/proc')):
    argv = process['argv']
    require(len(argv) >= 9 and argv[:2] == [PYTHON, '-B'] and argv[3] == 'daemon'
            and argv[7] == '--', 'unexpected protected daemon argv')
    commit, actual_host, root_text = argv[4:7]
    require(re.fullmatch('[0-9a-f]{40}', commit), 'invalid current wrapper commit')
    require(actual_host == host, 'current wrapper host mismatch')
    require(re.fullmatch('/srv/wfb-ng-fl/release-' + commit + r'-[0-9TZ]+', root_text),
            'invalid current wrapper root')
    root = protected_path(Path(root_text), directory=True)
    wrapper = protected_path(root / 'release-node.py')
    require(argv[2] == str(wrapper), 'current wrapper path mismatch')
    frozen = protected_source_file(root / 'source', MANAGEMENT / 'release-node.py')
    require(sha(wrapper) == sha(frozen), 'current wrapper differs from frozen source')
    expected_path = protected_path(root / 'expected-python.json')
    expected = json.loads(expected_path.read_text())
    require(expected == source_python(root / 'source'), 'previous Python manifest differs from frozen source')
    args = json.loads(protected_path(root / 'expected-args.json').read_text())
    require(isinstance(args, list) and all(isinstance(a, str) for a in args) and argv[8:] == args,
            'current wrapper args differ from expected args')
    loaded = json.loads(protected_path(root / 'python-loaded.json').read_text())
    pid = process['pid']
    proc = proc_root / str(pid)
    require(loaded['pid'] == pid and loaded['tick'] == process['tick'] == tick(pid),
            'previous pythonLoaded does not belong to current daemon')
    require(loaded['host'] == host and loaded['expectedCommit'] == commit, 'previous loaded host/commit mismatch')
    require(loaded['argv'] == args and loaded['cwd'] == os.readlink(proc / 'cwd') == '/',
            'previous loaded args/cwd mismatch')
    require(loaded['interpreter_exe'] == process['exe'] == os.readlink(proc / 'exe') == os.path.realpath(PYTHON),
            'previous loaded interpreter mismatch')
    validate_python(loaded['pythonLoaded'], expected, commit)
    for entry in loaded['pythonLoaded']['files'].values():
        require(sha(entry['path']) == entry['sha256'], 'previous loaded file changed after import')
    # 完成所有读取后重查 PID start tick 和命令行，拒绝复用 PID 或审计中切换进程。
    live_argv = [x.decode() for x in (proc / 'cmdline').read_bytes().split(b'\0') if x]
    require(tick(pid) == loaded['tick'] and live_argv == argv, 'current wrapper changed during preflight')
    return {'commit': commit, 'root': str(root), 'wrapper_sha256': sha(wrapper),
            'pythonLoaded': loaded, 'args': args}


def daemon(commit, host, root, args):
    require(os.uname().nodename == host, 'daemon host mismatch')
    role = 'server' if host == 'fl00' else 'client'
    module = importlib.import_module('wfb_ng.fl.' + role + '_daemon')
    expected = json.loads((root / 'expected-python.json').read_text())
    snapshot = python_snapshot()
    validate_python(snapshot, expected, commit)
    record = {'pid': os.getpid(), 'tick': tick(os.getpid()), 'host': host,
              'interpreter_exe': os.readlink('/proc/self/exe'), 'cwd': os.getcwd(),
              'expectedCommit': commit, 'pythonLoaded': snapshot, 'argv': args}
    write_json(root / 'python-loaded.json', record)
    # 同一解释器直接调用刚刚导入并留证的服务模块。
    sys.argv = ['wfb-fl-' + role + '-daemon', *args]
    return module.main()


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'daemon':
        require(len(sys.argv) >= 7 and sys.argv[5] == '--', 'invalid daemon argv')
        return daemon(sys.argv[2], sys.argv[3], Path(sys.argv[4]), sys.argv[6:])
    require(len(sys.argv) == 8, 'use release-four.py FULL_COMMIT on fl00')
    phase, commit, host, root_text, inbox_text, archive_sha, native_sha = sys.argv[1:]
    require(re.fullmatch('[0-9a-f]{40}', commit), 'invalid commit')
    require(host in ('fl00', 'fl01', 'fl02') and os.uname().nodename == host, 'host mismatch')
    require(os.geteuid() == 0, 'must run via sudo -n')
    root, inbox = Path(root_text), Path(inbox_text)
    require(str(root).startswith('/srv/wfb-ng-fl/release-' + commit + '-') and
            re.fullmatch(r'/srv/wfb-ng-fl/release-[0-9a-f]{40}-[0-9TZ]+', str(root)), 'invalid release root')
    require(str(inbox).startswith('/tmp/wfb-release-' + commit + '-') and
            re.fullmatch(r'/tmp/wfb-release-[0-9a-f]{40}-[0-9TZ]+', str(inbox)), 'invalid inbox')
    role = 'server' if host == 'fl00' else 'client'
    node_id = 255 if role == 'server' else int(host[-2:])
    unit = 'wfb-fl-' + role + '-daemon.service'
    source = root / 'source'
    out = {'host': host, 'node_id': node_id, 'phase': phase, 'commit': commit}
    clean_env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'PYTHONDONTWRITEBYTECODE': '1',
                 'COMMIT': commit, 'VERSION': VERSION, 'PYTHON': PYTHON, 'PREFIX': '/usr'}

    def snapshot_probe(pid=None):
        # 此结果明确标为独立探针，不能作为实际进程的 pythonLoaded。
        env, cwd, exe = dict(clean_env), '/tmp', PYTHON
        if pid:
            proc = Path('/proc') / str(pid)
            env = dict(item.split('=', 1) for item in (proc / 'environ').read_text().split('\0') if '=' in item)
            env['PYTHONDONTWRITEBYTECODE'] = '1'
            cwd, exe = os.readlink(proc / 'cwd'), str(proc / 'exe')
        code = ('import importlib,runpy,json; ns=runpy.run_path(' + repr(str(root / 'release-node.py')) + '); '
                'importlib.import_module("wfb_ng.fl.' + role + '_daemon"); '
                'print(json.dumps(ns["python_snapshot"]()))')
        result = run([exe, '-B', '-c', code], cwd=cwd, env=env)
        return {'cwd': cwd, 'exe': exe, 'import_env': {k: env.get(k) for k in ('PYTHONPATH', 'PYTHONHOME')},
                'probe': json.loads(result['out'])}

    def abi():
        commands = [['uname', '-m'], ['getconf', 'GNU_LIBC_VERSION'], ['g++', '-dumpmachine'],
                    ['g++', '--version'], ['file', str(NATIVE)], ['readelf', '-h', str(NATIVE)],
                    ['readelf', '-l', str(NATIVE)], ['readelf', '-n', str(NATIVE)],
                    ['readelf', '-p', '.comment', str(NATIVE)], ['readelf', '-d', str(NATIVE)],
                    ['readelf', '--version-info', str(NATIVE)], ['ldd', str(NATIVE)]]
        checks = [run(cmd) for cmd in commands]
        ldd = checks[-1]['out']
        require('not found' not in ldd, 'native has unresolved dependency')
        libraries = sorted(set(re.findall(r'(?:=>\s+|^\s*)(/[^\s()]+)', ldd, re.M)))
        require(libraries, 'no dynamic ABI libraries')
        library_hashes = {str(Path(p).resolve()): sha(p) for p in libraries}
        header = checks[5]['out']
        fields = {key: re.search(r'^\s*' + re.escape(key) + r':\s*(.+)$', header, re.M)[1]
                  for key in ('Class', 'Data', 'OS/ABI', 'ABI Version', 'Machine', 'Flags')}
        fingerprint = {'machine': checks[0]['out'].strip(), 'libc': checks[1]['out'].strip(),
                       'compiler_target': checks[2]['out'].strip(), 'elf': fields, 'libraries': library_hashes}
        return {'fingerprint': fingerprint, 'checks': checks}

    def stopped():
        state = props(unit)
        require(state['ActiveState'] == 'inactive' and state['MainPID'] == '0', 'daemon not stopped')
        audit = json.loads((root / 'preflight.json').read_text())
        cgroup = audit['unit']['ControlGroup']
        members = {}
        require(cgroup.startswith('/') and cgroup != '/' and '..' not in Path(cgroup).parts, 'invalid service cgroup')
        # 同时覆盖 Jetson 的 cgroup v1 systemd 层级和统一 cgroup v2。
        for base in (Path('/sys/fs/cgroup'), Path('/sys/fs/cgroup/systemd')):
            cg = base / cgroup.lstrip('/')
            if cg.exists():
                for filename in ('cgroup.procs', 'tasks'):
                    for path in cg.rglob(filename):
                        members[str(path)] = path.read_text().split()
        require(not any(members.values()), 'service cgroup still populated')
        rows = processes()
        leftovers = [r for r in rows if any(r['pid'] == old['pid'] and r['tick'] == old['tick']
                                           for old in audit['owned_processes'])]
        rogue = [r for r in rows if Path(r['exe']).name in
                 ('wfb_v6_uplink', 'uftp', 'uftpd', 'wfb-fl-server', 'wfb-fl-client') or
                 any(Path(a).name in ('wfb-fl-server-daemon', 'wfb-fl-client-daemon') for a in r['argv'])]
        require(not leftovers and not rogue, 'native/children/UFTP/daemon process remains')
        links = json.loads(run(['ip', '-j', 'link', 'show'])['out'])
        require(not any(link['ifname'] in audit['tun_names'] for link in links), 'TUN remains after stop')
        return {'unit': state, 'cgroup_members': members, 'leftover_processes': leftovers,
                'other_link_processes': rogue, 'links': links}

    try:
        if phase == 'preflight':
            root.mkdir(mode=0o700, parents=True, exist_ok=False)
            shutil.copy2(inbox / 'release-node.py', root / 'release-node.py')
            (root / 'release-node.py').chmod(0o600)
            require(sha(inbox / 'source.tar') == archive_sha, 'archive hash mismatch')
            source.mkdir(mode=0o700)
            with tarfile.open(inbox / 'source.tar') as tar:
                for member in tar.getmembers():
                    require(not Path(member.name).is_absolute() and '..' not in Path(member.name).parts
                            and (member.isfile() or member.isdir()), 'unsafe archive entry: ' + member.name)
                tar.extractall(source)
            management = source / '.scratch/real-fedavg-training/evidence/08-jetson-preflight/heartbeat-149'
            require(sha(management / 'release-node.py') == sha(root / 'release-node.py'),
                    'executing helper differs from frozen commit archive')
            out['archive_sha256'] = archive_sha
            state = props(unit)
            require(state['ActiveState'] == 'active' and int(state['MainPID']) > 0, 'expected daemon missing')
            require(state['KillMode'] == 'control-group', 'must use KillMode=control-group')
            require(state['User'] in ('', 'root', '0'), 'current service must run as root for protected runtime evidence')
            for other in UNITS:
                if other != unit:
                    require(props(other)['ActiveState'] not in ('active', 'activating', 'deactivating'), 'competing service: ' + other)
            pid = int(state['MainPID'])
            rows = processes()
            process = next(row for row in rows if row['pid'] == pid)
            previous = current_wrapper(process, host)
            args = previous['args']
            dropfile = Path('/etc/systemd/system') / (unit + '.d/zzzz-release-149.conf')
            out['previous_wrapper'] = previous
            out['release_dropin'] = dropin_snapshot(dropfile, release_dropin(process['argv']))
            config = '/etc/wfb-ng-fl/server.json' if role == 'server' else '/etc/wfb-ng-fl/node.json'
            require(option(args, '--config') == config, 'config must be explicit installed path')
            identity = json.loads(Path('/etc/wfb-ng-fl/node.json').read_text())
            if role == 'client':
                require(identity['node_id'] == node_id and identity['tun_ip'] == '10.80.0.' + str(10 + node_id) + '/24', 'node identity mismatch')
                work_dir = option(args, '--work-dir')
                require(Path(option(args, '--mnist-data-dir')).is_dir(), 'existing MNIST directory missing')
                tun_names = ['fl-c' + str(node_id)]
            else:
                server = json.loads(Path(config).read_text())
                for name in ('mnist_data_dir', 'algorithm_config', 'preparation_timeout_seconds',
                             'round_timeout_seconds', 'io_timeout_seconds'):
                    require(name in server, 'preserved server field must be explicit: ' + name)
                require(Path(server['mnist_data_dir']).is_dir(), 'existing MNIST directory missing')
                work_dir = server['work_dir']
                tun_names = [server['tun_name']]
                out['server_config'] = server
            require(Path(work_dir).is_absolute() and Path(work_dir).is_dir(), 'existing absolute work_dir required')
            owned = {pid}
            while True:
                expanded = owned | {r['pid'] for r in rows if r['ppid'] in owned}
                if expanded == owned:
                    break
                owned = expanded
            natives = [r for r in rows if 'wfb_v6_uplink' in r['exe']]
            require(len(natives) == 1, 'need exactly one current native')
            previous_native = protected_source_file(Path(previous['root']) / 'source', 'wfb_v6_uplink')
            require(natives[0]['exe'] == str(NATIVE) and
                    sha(previous_native) == sha(NATIVE) == natives[0]['exe_sha256'],
                    'current native differs from previous protected release')
            for native in natives:
                require(native['pid'] in owned, 'native outside daemon process tree')
                require(option(native['argv'], '--node-id') == str(node_id), 'native node ID mismatch')
                require(option(native['argv'], '--role') == role, 'native role mismatch')
                tun_names.append(option(native['argv'], '--tun-name'))
                native['elf_notes'] = run(['readelf', '-n', '/proc/' + str(native['pid']) + '/exe'])
            out['runtime_import_probe'] = snapshot_probe(pid)
            require(not any(out['runtime_import_probe']['import_env'].values()), 'daemon PYTHONPATH/PYTHONHOME must be unset')
            require(out['runtime_import_probe']['cwd'] == '/', 'daemon WorkingDirectory must be /')
            out.update(unit=state, daemon=process, daemon_args=args, node_identity=identity,
                       work_dir=work_dir, tun_names=sorted(set(tun_names)), native=natives,
                       owned_processes=[r for r in rows if r['pid'] in owned],
                       abi=abi(), installed_native_sha256=sha(NATIVE))
            # 备份配置和服务单元，不收集无线密钥、完整进程环境或秘密目录。
            backup = root / 'backup'
            backup.mkdir(mode=0o700)
            paths = [Path('/etc/wfb-ng-fl/server.json'), Path('/etc/wfb-ng-fl/node.json')]
            paths.extend(Path('/etc/wfb-ng').glob('*.json'))
            for base in ('/etc/systemd/system', '/usr/lib/systemd/system', '/lib/systemd/system'):
                paths.extend(Path(base).glob('wfb-fl*'))
            for cache in ('channel_cache.json', 'cached_channel.json'):
                paths.append(Path(work_dir) / cache)
            with tarfile.open(backup / 'configs-units.tar', 'w') as tar:
                for path in sorted(set(paths)):
                    if path.exists() or path.is_symlink():
                        tar.add(path, arcname=str(path).lstrip('/'))
            (backup / 'configs-units.tar').chmod(0o600)
            (backup / 'zzzz-release-149.conf').write_text(out['release_dropin']['text'])
            (backup / 'zzzz-release-149.conf').chmod(0o600)
            require(sha(backup / 'zzzz-release-149.conf') == out['release_dropin']['sha256'],
                    'release drop-in backup mismatch')
            unchanged_dropin(dropfile, out['release_dropin'])
            require(tick(pid) == process['tick'] and tick(natives[0]['pid']) == natives[0]['tick'],
                    'process changed during preflight')
            out['backup_sha256'] = sha(backup / 'configs-units.tar')
        elif phase == 'build':
            require(host == 'fl00', 'native build only on fl00')
            # 只使用归档；若归档带有构建产物，则拒绝复用，保证本次确实编译。
            require(not (source / 'wfb_v6_uplink').exists() and not list((source / 'src').glob('*.o')), 'archive contains build outputs')
            out['build'] = run(['make', '-C', str(source), 'build_v6', 'COMMIT=' + commit,
                                'VERSION=' + VERSION, 'PYTHON=' + PYTHON], timeout=600, env=clean_env, cwd='/tmp')
            out['native_sha256'] = sha(source / 'wfb_v6_uplink')
            out['elf'] = [run(['readelf', flag, str(source / 'wfb_v6_uplink')]) for flag in ('-h', '-n', '--version-info')]
        elif phase == 'stage':
            if host != 'fl00':
                require(sha(inbox / 'wfb_v6_uplink') == native_sha, 'transferred binary hash mismatch')
                shutil.copy2(inbox / 'wfb_v6_uplink', source / 'wfb_v6_uplink')
                (source / 'wfb_v6_uplink').chmod(0o755)
            require(sha(source / 'wfb_v6_uplink') == native_sha, 'staged binary mismatch')
            out['ldd'] = run(['ldd', str(source / 'wfb_v6_uplink')])
            require('not found' not in out['ldd']['out'], 'new native dependency unavailable')
            libraries = sorted(set(re.findall(r'(?:=>\s+|^\s*)(/[^\s()]+)', out['ldd']['out'], re.M)))
            require(libraries, 'no staged native ABI libraries')
            out['dependency_sha256'] = {str(Path(p).resolve()): sha(p) for p in libraries}
        elif phase == 'stop':
            out['stop'] = run(['systemctl', 'stop', unit], timeout=90)
        elif phase == 'stopped':
            out.update(stopped())
        elif phase == 'install':
            stopped()
            require(sha(source / 'wfb_v6_uplink') == native_sha, 'install binary hash mismatch')
            before = json.loads((root / 'preflight.json').read_text())
            dropfile = Path('/etc/systemd/system') / (unit + '.d/zzzz-release-149.conf')
            unchanged_dropin(dropfile, before['release_dropin'])
            package_root = Path(before['runtime_import_probe']['probe']['root'])
            require(package_root.is_absolute() and package_root.name == 'wfb_ng' and
                    str(package_root).startswith('/usr/'), 'unexpected installed package location')
            # issue41_algorithm.py 已从冻结源码删除；直接安装不会清除旧版本遗留文件。
            (package_root / 'fl/issue41_algorithm.py').unlink(missing_ok=True)
            # 安装器只调用 build_identity，不调用 build_v6；输入全部来自冻结归档。
            out['install'] = run(['/bin/sh', str(source / 'scripts/install/install-v8.sh')],
                                  timeout=150, cwd='/tmp', env=clean_env)
            require(sha(NATIVE) == native_sha, 'installed binary mismatch')
            # -B 只禁止写入，仍可能读取旧字节码，因此只清除自动生成的缓存。
            for cache in sorted(package_root.rglob('__pycache__'), reverse=True):
                shutil.rmtree(cache)
            require(not list((package_root / 'fl').rglob('*.pyc')), 'legacy bytecode remains')
            write_json(root / 'expected-python.json', source_python(source))
            args = before['daemon_args']
            if role == 'server':
                config = Path('/etc/wfb-ng-fl/server.json')
                current = json.loads(config.read_text())
                require(current == before['server_config'], 'server config changed during maintenance')
                current.update(RADIO, known_clients=list(WHITELIST_IDS))
                mode = config.stat().st_mode & 0o777
                write_json(config, current)
                config.chmod(mode)
                for flag, value in [('--channel', 149), ('--bandwidth-mhz', 40),
                                    ('--txpower', 14), ('--downlink-mcs', 3),
                                    ('--uplink-mcs', 4), ('--uftp-rate-kbps', 15000)]:
                    # 显式启动参数必须与已锁定的服务端配置一致。
                    args = replace_option(args, flag, value)
            else:
                require(json.loads(Path('/etc/wfb-ng-fl/node.json').read_text()) == before['node_identity'], 'node identity changed')
                for flag, value in [('--channel', 149), ('--bandwidth-mhz', 40),
                                    ('--radio-txpower-dbm', 14), ('--uplink-mcs', 4)]:
                    args = replace_option(args, flag, value)
                # 当前服务读取 channel_cache.json，同时固化要求中的 cached_channel.json。
                for name in ('channel_cache.json', 'cached_channel.json'):
                    path = Path(before['work_dir']) / name
                    data = json.loads(path.read_text()) if path.exists() else {}
                    data['channel'] = 149
                    write_json(path, data)
            command = [PYTHON, '-B', str(root / 'release-node.py'), 'daemon', commit, host, str(root), '--', *args]
            unchanged_dropin(dropfile, before['release_dropin'])
            dropfile.write_text(release_dropin(command))
            dropfile.chmod(0o644)
            write_json(root / 'expected-args.json', args)
            run(['systemctl', 'daemon-reload'])
            out['effective_unit'] = props(unit)
        elif phase == 'verify-install':
            stopped()
            require(sha(NATIVE) == native_sha, 'installed native differs')
            out['python_import_probe'] = snapshot_probe()
            validate_python(out['python_import_probe']['probe'], json.loads((root / 'expected-python.json').read_text()), commit)
            require(sha(Path(out['python_import_probe']['probe']['root']) / '__init__.py') ==
                    sha(source / 'scripts/install/v8-wfb-ng-init.py'), 'package root mismatch')
            if role == 'server':
                before = json.loads((root / 'preflight.json').read_text())
                expected_config = {**before['server_config'], **RADIO,
                                   'known_clients': list(WHITELIST_IDS)}
                require(json.loads(Path('/etc/wfb-ng-fl/server.json').read_text()) == expected_config,
                        'server config/topology changed or preserved training fields differ')
                out['known_clients'] = expected_config['known_clients']
                result = run([PYTHON, '-B', '-c',
                              'from wfb_ng.fl.server_daemon import load_server_config; load_server_config("/etc/wfb-ng-fl/server.json")'],
                             cwd='/tmp', env=clean_env)
                out['config_validation'] = result
        elif phase == 'start':
            require((root / 'verify-install.json').is_file(), 'missing successful install barrier')
            (root / 'python-loaded.json').unlink(missing_ok=True)
            out['start'] = run(['systemctl', 'start', unit])
        elif phase == 'verify-runtime':
            # 用真实逐次采样允许初始 HUNTING 收敛；总观察窗口最多 75 秒。
            attempts = []
            out['samples'] = attempts
            consecutive = 0
            deadline = time.monotonic() + 75
            for attempt in range(40):
                sample = {'attempt': attempt + 1, 'utc': time.time(), 'state': 'observing'}
                try:
                    state = props(unit)
                    sample['unit'] = state
                    pid = int(state['MainPID'])
                    require(pid > 0, 'daemon PID missing')
                    loaded = json.loads((root / 'python-loaded.json').read_text())
                    sample['pythonLoaded'] = loaded
                    sample['daemon'] = next(row for row in processes() if row['pid'] == pid)
                    require(loaded['interpreter_exe'] == os.readlink('/proc/' + str(pid) + '/exe') == os.path.realpath(PYTHON),
                            'actual daemon interpreter path mismatch')
                    require(loaded['pid'] == pid and loaded['tick'] == tick(pid), 'pythonLoaded does not belong to current daemon')
                    require(loaded['host'] == host and loaded['expectedCommit'] == commit, 'loaded host/commit mismatch')
                    require(loaded['argv'] == json.loads((root / 'expected-args.json').read_text()), 'daemon args differ')
                    validate_python(loaded['pythonLoaded'], json.loads((root / 'expected-python.json').read_text()), commit)
                    for entry in loaded['pythonLoaded']['files'].values():
                        require(sha(entry['path']) == entry['sha256'], 'loaded file changed after import')
                    rows = processes()
                    natives = [r for r in rows if 'wfb_v6_uplink' in r['exe']]
                    sample['native'] = natives
                    require(len(natives) == 1, 'need exactly one running native')
                    native = natives[0]
                    require(native['exe'] == str(NATIVE) and native['exe_sha256'] == native_sha and sha(NATIVE) == native_sha,
                            'running /proc/exe native hash mismatch')
                    require(native['ppid'] == pid, 'native not a direct daemon child')
                    for flag, value in [('--node-id', str(node_id)), ('--role', role), ('--radio-bandwidth', '40'),
                                        ('--radio-mcs-index', '3' if role == 'server' else '4')]:
                        require(option(native['argv'], flag) == value, 'native argument mismatch: ' + flag)
                    if role == 'server':
                        known = option(native['argv'], '--known-clients').split(',')
                        expected_known = [str(n) for n in WHITELIST_IDS]
                        require(sorted(known, key=int) == expected_known,
                                'native known-clients must expose whitelist slots 1..10')
                        targets = []
                        for index, value in enumerate(native['argv']):
                            if value == '--client-target':
                                targets.append(native['argv'][index + 1])
                            elif value.startswith('--client-target='):
                                targets.append(value.split('=', 1)[1])
                        expected_targets = [str(n) + ':10.80.0.' + str(10 + n) + ':127.0.0.1:1'
                                            for n in WHITELIST_IDS]
                        require(sorted(targets) == sorted(expected_targets),
                                'native client-targets differ from whitelist slots 1..10')
                        server_config = json.loads(Path('/etc/wfb-ng-fl/server.json').read_text())
                        before = json.loads((root / 'preflight.json').read_text())
                        require(server_config == {**before['server_config'], **RADIO,
                                                  'known_clients': list(WHITELIST_IDS)},
                                'running server configuration/whitelist drift')
                        sample['topology_sets'] = {
                            'whitelist_ids': list(WHITELIST_IDS),
                            'participant_ids': list(PARTICIPANT_IDS),
                            'known_clients': known,
                            'client_targets': targets,
                        }
                    else:
                        identity = json.loads(Path('/etc/wfb-ng-fl/node.json').read_text())
                        before = json.loads((root / 'preflight.json').read_text())
                        require(identity == before['node_identity'] and identity['node_id'] == node_id,
                                'running node identity drift')
                        sample['node_identity'] = identity
                    interface = option(native['argv'], '--air-interface')
                    require(re.fullmatch(r'[A-Za-z0-9_.-]+', interface), 'invalid air interface')
                    iw = run(['iw', 'dev', interface, 'info'], timeout=8)
                    sample['iw'] = iw
                    require(re.search(r'channel\s+149\s+\(5745 MHz\),\s+width:\s+40 MHz,\s+center1:\s+5755 MHz', iw['out']), 'iw channel/width not 149/HT40+')
                    power = re.search(r'txpower\s+(-?\d+(?:\.\d+)?)\s+dBm', iw['out'])
                    # 该 RTL 驱动把配置 14 读回为 -14.00 dBm；严格保存并核对原始读数。
                    require(power and float(power[1]) == -14.0, 'iw txpower readback must be -14.00 dBm')
                    require(tick(pid) == loaded['tick'] and tick(native['pid']) == native['tick'], 'process changed during audit')
                    consecutive += 1
                    if consecutive < 3:
                        require(attempt < 39 and time.monotonic() < deadline, 'insufficient stable runtime samples within window')
                    sample.update(state='passed' if consecutive >= 3 else 'converging', consecutive=consecutive)
                    attempts.append(sample)
                    write_json(root / 'runtime-samples.json', attempts)
                    if consecutive < 3:
                        time.sleep(min(2, max(0, deadline - time.monotonic())))
                        continue
                    out.update(unit=state, pythonLoaded=loaded, native=native, iw=iw,
                               txpower_readback_dbm=float(power[1]), samples=attempts)
                    break
                except (RuntimeError, OSError, ValueError, StopIteration, subprocess.SubprocessError) as exc:
                    consecutive = 0
                    sample.update(state='pending', error=repr(exc))
                    attempts.append(sample)
                    write_json(root / 'runtime-samples.json', attempts)
                    if attempt == 39 or time.monotonic() >= deadline:
                        raise RuntimeError('runtime convergence window exhausted; inspect runtime-samples.json') from exc
                    time.sleep(min(2, max(0, deadline - time.monotonic())))
        else:
            raise RuntimeError('unknown phase: ' + phase)
        out['state'] = 'passed'
        write_json(root / (phase + '.json'), out)
        print(json.dumps(out, ensure_ascii=False))
    except Exception as exc:
        out.update(state='failed', error=repr(exc))
        if root.is_dir():
            write_json(root / (phase + '-failed.json'), out)
        print(json.dumps(out, ensure_ascii=False))
        raise


if __name__ == '__main__':
    sys.exit(main())
