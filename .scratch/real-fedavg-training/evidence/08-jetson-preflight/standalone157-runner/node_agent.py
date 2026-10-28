#!/usr/bin/env python3
"""08 standalone节点代理。只由受审runner启动；EOF/TERM/阶段异常最终回收自有子进程并恢复原unit。"""
import base64
import glob
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tarfile
import time

COMMIT = '701963ac254261c31c56a074d06966a9217346f6'
NATIVE_SHA = 'f7c73f41b730ddb3547e1730b5a5b7b40a55dd7a8e3e5f265dcc6563c6c432b1'
HELPER = Path('/srv/wfb-ng-fl/deploy-701963a/source/tests/demo/fl_demo_metrics.py')
HELPER_SHA = 'b425156d618ac219df7a5e376c6014a5b664ed18ad38e93861f09d8adf0bf162'
NATIVE = '/usr/bin/wfb_v6_uplink'
BASE = Path('/srv/wfb-ng-fl')
UNIT_NAMES = {'fl00': 'wfb-fl-server-daemon.service', 'fl01': 'wfb-fl-client-daemon.service',
              'fl02': 'wfb-fl-client-daemon.service', 'fl04': 'wfb-fl-client-daemon.service'}
GROUP = '239.80.41.1/32'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def proc_tick(pid):
    return Path('/proc', str(pid), 'stat').read_text().rsplit(')', 1)[1].split()[19]


def option(argv, key, default=None):
    indices = [i for i, word in enumerate(argv) if word == key]
    if not indices:
        return default
    if len(indices) != 1 or indices[0] + 1 == len(argv):
        raise ValueError('重复/缺值native参数: ' + key)
    return argv[indices[0] + 1]


class Node:
    def __init__(self):
        self.host = os.uname().nodename
        if self.host not in UNIT_NAMES:
            raise RuntimeError('不是权威四板主机')
        self.node_id = 255 if self.host == 'fl00' else int(self.host[-2:])
        self.unit = UNIT_NAMES[self.host]
        self.root = None
        self.snapshot = None
        self.children = {}
        self.command_records = []
        self.stop_attempted = False
        self.tun_index = None
        self.owned_route = False
        self.cleaned = False
        self.restore_result = None
        self.channel_write_count = 0

    def record(self, value):
        value = {'mono': time.monotonic(), 'wall': time.time(), **value}
        self.command_records.append(value)
        if self.root:
            with (self.root / 'commands.jsonl').open('a') as out:
                out.write(json.dumps(value, ensure_ascii=False) + '\n')
        return value

    def run(self, argv, timeout=8, check=True):
        try:
            result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, errors='backslashreplace', timeout=timeout,
                                    cwd='/', env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        except subprocess.TimeoutExpired as exc:
            self.record({'argv': argv, 'timeout_seconds': timeout, 'error': repr(exc)})
            raise
        self.record({'argv': argv, 'rc': result.returncode, 'out': result.stdout, 'err': result.stderr})
        if check and result.returncode:
            raise RuntimeError('命令失败: ' + repr(argv) + ': ' + result.stderr[-2000:])
        return result.stdout

    def unit_props(self):
        text = self.run(['systemctl', 'show', self.unit, '-p', 'MainPID', '-p', 'ActiveState',
                         '-p', 'ExecStart', '-p', 'WorkingDirectory', '-p', 'Environment',
                         '-p', 'DropInPaths', '-p', 'ControlGroup', '-p', 'User'])
        return dict(line.split('=', 1) for line in text.splitlines() if '=' in line)

    def native_processes(self):
        result = []
        for process in Path('/proc').iterdir():
            if not process.name.isdigit():
                continue
            try:
                exe = os.readlink(process / 'exe')
                if exe == NATIVE or exe.startswith(NATIVE + ' (deleted)'):
                    result.append({'pid': int(process.name), 'exe': exe,
                                   'argv': (process / 'cmdline').read_bytes().rstrip(b'\0').decode().split('\0'),
                                   'sha256': sha(process / 'exe')})
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                continue
        return result

    def radio_info(self):
        return self.run(['/usr/sbin/iw', 'dev', self.snapshot['interface'], 'info'])

    def prepare(self, run_id, expected_hashes):
        if self.root or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,70}', run_id):
            raise ValueError('非法/重复runid')
        props = self.unit_props()
        if props.get('ActiveState') != 'active' or not props.get('MainPID', '').isdigit():
            raise RuntimeError('原角色服务必须active')
        extra_units = {}
        for unit in ['wfb-fl-server.service', 'wfb-fl-client.service',
                     'wfb-fl-server-daemon.service', 'wfb-fl-client-daemon.service']:
            if unit == self.unit:
                continue
            state = self.run(['systemctl', 'show', unit, '-p', 'ActiveState', '--value'], check=False).strip()
            extra_units[unit] = state
            if state in ['active', 'activating', 'deactivating']:
                raise RuntimeError('发现并发其他角色unit: ' + unit)
        pid = int(props['MainPID'])
        cwd = os.readlink('/proc/%d/cwd' % pid)
        env = dict(item.split('=', 1) for item in Path('/proc', str(pid), 'environ').read_text().split('\0') if '=' in item)
        if cwd != '/' or env.get('PYTHONPATH') or env.get('PYTHONHOME'):
            raise RuntimeError('运行来源可能shadow，拒绝维护')
        identity_path = Path('/usr/local/lib/python3.10/dist-packages/wfb_ng/fl/build_identity.json')
        identity = json.loads(identity_path.read_text())
        if identity != {'commit': COMMIT, 'schema_version': 1}:
            raise RuntimeError('Python构建身份不匹配')
        for relative, expected in expected_hashes.items():
            target = Path('/usr/local/lib/python3.10/dist-packages/wfb_ng/fl') / relative
            if sha(target) != expected:
                raise RuntimeError('关键安装文件SHA不一致: ' + relative)
        specs = {}
        code = "import importlib.util,json; print(json.dumps({m:importlib.util.find_spec(m).origin for m in ['wfb_ng.fl.service','wfb_ng.fl.client_daemon','wfb_ng.fl.server_daemon','wfb_ng.fl.console']}))"
        probe = subprocess.run(['/usr/bin/python3', '-B', '-c', code], cwd=cwd, env=env,
                               capture_output=True, text=True, timeout=8, check=True)
        specs = json.loads(probe.stdout)
        if not all(str(value).startswith('/usr/local/lib/python3.10/dist-packages/wfb_ng/fl/') for value in specs.values()):
            raise RuntimeError('find_spec不是安装来源')
        native = self.native_processes()
        if len(native) != 1 or native[0]['sha256'] != NATIVE_SHA or native[0]['exe'] != NATIVE:
            raise RuntimeError('运行native不是唯一同SHA701')
        if sha(NATIVE) != NATIVE_SHA or sha(HELPER) != HELPER_SHA:
            raise RuntimeError('native/helper安装SHA错误')
        argv = native[0]['argv']
        if argv[0] != NATIVE or option(argv, '--role') != ('server' if self.node_id == 255 else 'client'):
            raise RuntimeError('native实际argv身份错误')
        if option(argv, '--node-id') != str(self.node_id):
            raise RuntimeError('node_id不一致')
        if self.node_id == 255:
            known = set(option(argv, '--known-clients', '').split(','))
            targets = [argv[i + 1] for i, word in enumerate(argv[:-1]) if word == '--client-target']
            if not {'1', '2', '4'}.issubset(known):
                raise RuntimeError('server已知集合缺失124')
            for node in [1, 2, 4]:
                if not any(value.startswith('%d:10.80.0.%d:' % (node, 10 + node)) for value in targets):
                    raise RuntimeError('server实际target缺失/地址不符: ' + str(node))
        # 仅验证实际argv，不用模板重建；feedback省略时必须为冻结701默认0。
        required = {'--radio-bandwidth': '40', '--link-id': '7669206', '--uplink-stream': '1',
                    '--downlink-stream': '2', '--fec-k': '8', '--fec-n': '14',
                    '--radio-mcs-index': '3' if self.node_id == 255 else '4'}
        if any(option(argv, key) != value for key, value in required.items()):
            raise RuntimeError('实际native参数偏离701基线')
        if option(argv, '--feedback-window-period-ms', '0') != '0' or option(argv, '--feedback-window-duration-ms', '0') != '0':
            raise RuntimeError('feedback必须保持APP0')
        interface = option(argv, '--air-interface')
        if not interface or not os.path.realpath('/sys/class/net/' + interface + '/device/driver').endswith('/rtl88xxau_wfb'):
            raise RuntimeError('不是实际RTL空口')
        tun = option(argv, '--tun-name')
        cidr = option(argv, '--tun-addr')
        expected_ip = '10.80.0.1' if self.node_id == 255 else '10.80.0.' + str(10 + self.node_id)
        if str(ipaddress.ip_interface(cidr).ip) != expected_ip:
            raise RuntimeError('TUN地址错误')
        links = json.loads(self.run(['/usr/sbin/ip', '-j', 'link', 'show', 'dev', tun]))
        routes = json.loads(self.run(['/usr/sbin/ip', '-j', 'route', 'show', 'exact', GROUP]))
        if routes:
            raise RuntimeError('公共组播已有路由，拒绝覆盖既有路由')
        self.snapshot = {'host': self.host, 'unit': self.unit, 'unit_props': props,
                         'daemon_pid': pid, 'daemon_tick': proc_tick(pid), 'cwd': cwd,
                         'other_role_unit_states': extra_units,
                         'expected_hashes': expected_hashes,
                         'runtime_import_env': {k: env.get(k) for k in ['PATH', 'PYTHONPATH', 'PYTHONHOME']},
                         'runtime_specs': specs, 'identity': identity, 'native': native[0],
                         'interface': interface, 'tun': tun, 'cidr': cidr, 'ip': expected_ip,
                         'tun_link': links[0], 'multicast_route_before': routes,
                         'helper_sha256': sha(HELPER)}
        self.snapshot['iw_before'] = self.radio_info()
        if 'type monitor' not in self.snapshot['iw_before']:
            raise RuntimeError('原空口不是monitor')
        root = BASE / ('standalone157-' + run_id)
        root.mkdir(mode=0o700, exist_ok=False)
        self.root = root
        with (root / 'commands.jsonl').open('x') as output:
            for value in self.command_records:
                output.write(json.dumps(value, ensure_ascii=False) + '\n')
        for name in ['backup', 'received', 'tmp', 'source', 'uploads']:
            (root / name).mkdir(mode=0o700)
        with tarfile.open(root / 'backup/config-systemd.tar', 'w') as archive:
            paths = []
            for pattern in ['/etc/wfb-ng-fl', '/etc/wfb-ng', '/etc/systemd/system/wfb-fl*',
                            '/run/systemd/system/wfb-fl*', '/usr/lib/systemd/system/wfb-fl*', '/lib/systemd/system/wfb-fl*']:
                paths.extend(glob.glob(pattern))
            for path in sorted(set(paths)):
                archive.add(path, arcname=path.lstrip('/'))
        (root / 'backup/snapshot.json').write_text(json.dumps(self.snapshot, ensure_ascii=False, indent=2) + '\n')
        self.record({'event': 'prepared', 'snapshot': self.snapshot})
        return self.snapshot

    def stop_original(self):
        if not self.root or self.stop_attempted:
            raise RuntimeError('准备缺失/重复停服')
        if self.unit_props()['MainPID'] != str(self.snapshot['daemon_pid']):
            raise RuntimeError('停服前原daemon身份改变')
        self.stop_attempted = True
        self.record({'event': 'original_stop_attempt'})
        self.run(['systemctl', 'stop', self.unit], timeout=25)
        props = self.unit_props()
        if props['ActiveState'] != 'inactive' or props['MainPID'] != '0':
            raise RuntimeError('daemon尚未退出')
        cgroup = props.get('ControlGroup')
        if cgroup:
            procs = Path('/sys/fs/cgroup' + cgroup + '/cgroup.procs')
            if procs.exists() and procs.read_text().strip():
                raise RuntimeError('原cgroup尚有子进程')
        if self.native_processes():
            raise RuntimeError('尚有native，不允许standalone竞争')
        for name in ['uftp', 'uftpd', 'wfb-fl-client', 'wfb-fl-server']:
            result = subprocess.run(['pgrep', '-x', name], capture_output=True, text=True, timeout=3)
            if result.returncode == 0:
                raise RuntimeError('尚有角色/UFTP进程: ' + name)
        if Path('/sys/class/net', self.snapshot['tun']).exists():
            raise RuntimeError('原TUN未消失，拒绝复用')
        return props

    def align_channel(self):
        if not self.stop_attempted or self.unit_props()['MainPID'] != '0' or self.channel_write_count:
            raise RuntimeError('set channel前提不成立或重复调用')
        before = self.radio_info()
        power = re.search(r'txpower\s+(-?[\d.]+)', before)
        if power is None:
            raise RuntimeError('无法取得写前功率')
        self.channel_write_count += 1
        self.record({'event': 'explicit_channel_write', 'before_iw': before,
                     'argv': ['/usr/sbin/iw', 'dev', self.snapshot['interface'], 'set', 'channel', '157', 'HT40+']})
        self.run(['/usr/sbin/iw', 'dev', self.snapshot['interface'], 'set', 'channel', '157', 'HT40+'], timeout=6)
        after = self.radio_info()
        match = re.search(r'txpower\s+(-?[\d.]+)', after)
        self.record({'event': 'channel_readback', 'after_iw': after})
        if 'channel 157 ' not in after or 'width: 40 MHz' not in after or 'type monitor' not in after:
            raise RuntimeError('157/HT40+读回失败')
        if match is None or match[1] != power[1]:
            raise RuntimeError('功率读回变化；不擅自改功率')
        return {'before': before, 'after': after}

    def spawn(self, name, argv, deadline):
        if name in self.children:
            raise RuntimeError('重复子进程: ' + name)
        stdout = (self.root / (name + '.log')).open('xb')
        child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=stdout, stderr=subprocess.STDOUT,
                                 cwd='/', env={**os.environ, 'PYTHONPATH': '', 'PYTHONDONTWRITEBYTECODE': '1'},
                                 start_new_session=True)
        info = {'process': child, 'stream': stdout, 'start_tick': proc_tick(child.pid),
                'argv': argv, 'deadline': time.monotonic() + deadline}
        self.children[name] = info
        self.record({'event': 'owned_start', 'name': name, 'pid': child.pid, 'start_tick': info['start_tick'], 'argv': argv})
        return {'pid': child.pid, 'start_tick': info['start_tick'], 'argv': argv}

    def stop_child(self, name):
        entry = self.children.get(name)
        if entry is None:
            return {'name': name, 'absent': True}
        child = entry['process']
        if child.poll() is None:
            try:
                if proc_tick(child.pid) != entry['start_tick'] or os.getpgid(child.pid) != child.pid:
                    raise RuntimeError('自有PID/starttime/PGID不一致，拒绝kill')
                os.killpg(child.pid, signal.SIGTERM)
            except (FileNotFoundError, ProcessLookupError):
                child.wait(timeout=2)
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    if proc_tick(child.pid) != entry['start_tick']:
                        raise RuntimeError('kill前PID复用')
                    os.killpg(child.pid, signal.SIGKILL)
                except (FileNotFoundError, ProcessLookupError):
                    pass
                child.wait(timeout=2)
        else:
            child.wait()
        entry['stream'].close()
        self.record({'event': 'owned_reaped', 'name': name, 'pid': child.pid, 'rc': child.returncode})
        return {'name': name, 'pid': child.pid, 'rc': child.returncode}

    def check_children(self):
        for name, entry in self.children.items():
            if entry['process'].poll() is None and time.monotonic() > entry['deadline']:
                self.stop_child(name)
                raise TimeoutError('自有子进程期限: ' + name)

    def start_native(self):
        if self.unit_props()['MainPID'] != '0' or self.native_processes():
            raise RuntimeError('原服务/native仍在运行')
        argv = list(self.snapshot['native']['argv'])
        if sha(NATIVE) != NATIVE_SHA:
            raise RuntimeError('native变更')
        info = self.spawn('native', argv, 210)
        limit = time.monotonic() + 6
        tun = self.snapshot['tun']
        while time.monotonic() < limit:
            if self.children['native']['process'].poll() is not None:
                raise RuntimeError('standalone native启动退出')
            if Path('/sys/class/net', tun).exists():
                addresses = json.loads(self.run(['/usr/sbin/ip', '-j', 'addr', 'show', 'dev', tun]))
                if any(a.get('local') == self.snapshot['ip'] for link in addresses for a in link.get('addr_info', [])):
                    link = json.loads(self.run(['/usr/sbin/ip', '-j', 'link', 'show', 'dev', tun]))[0]
                    self.tun_index = link['ifindex']
                    # 仅对自有新TUN恢复原MTU/txqueuelen，不是射频写操作。
                    old = self.snapshot['tun_link']
                    self.run(['/usr/sbin/ip', 'link', 'set', 'dev', tun, 'mtu', str(old['mtu']),
                              'txqueuelen', str(old.get('txqlen', 5000))])
                    self.run(['/usr/sbin/ip', 'route', 'add', GROUP, 'dev', tun])
                    self.owned_route = True
                    self.record({'event': 'owned_tun', 'ifindex': self.tun_index, 'name': tun})
                    return {**info, 'tun': tun, 'ifindex': self.tun_index, 'native_sha256': sha('/proc/%d/exe' % info['pid'])}
            time.sleep(.1)
        raise TimeoutError('standalone TUN未就绪')

    def receiver(self):
        if self.node_id == 255:
            raise RuntimeError('server不能启动client uftpd')
        info = self.spawn('uftpd', ['/usr/bin/uftpd', '-d', '-q', '-I', self.snapshot['ip'],
                                   '-M', '239.80.41.1', '-p', '1044', '-U', '0x%08x' % self.node_id,
                                   '-D', str(self.root / 'received'), '-T', str(self.root / 'tmp'),
                                   '-F', str(self.root / 'uftpd.status')], 120)
        limit = time.monotonic() + 5
        while time.monotonic() < limit:
            if self.children['uftpd']['process'].poll() is not None:
                raise RuntimeError('uftpd启动退出')
            sockets = self.run(['ss', '-uapn'])
            if any('pid=%d,' % info['pid'] in line and ':1044' in line for line in sockets.splitlines()):
                return info
            time.sleep(.1)
        raise TimeoutError('自有uftpd未监听1044')

    def downlink(self):
        if self.node_id != 255:
            raise RuntimeError('仅server生成/发送payload')
        source = self.root / 'source/diagnostic-64k.bin'
        if source.exists():
            raise RuntimeError('拒绝覆盖payload')
        self.run(['/usr/bin/python3', '-I', '-B', str(HELPER), 'generate-file', '--output', str(source),
                  '--size', '65536', '--seed', '08-standalone157-64k-v1'])
        expected = {'size_bytes': source.stat().st_size, 'sha256': sha(source)}
        if expected['size_bytes'] != 65536:
            raise RuntimeError('源文件不是64KiB')
        self.spawn('uftp', ['/usr/bin/uftp', '-q', '-I', '10.80.0.1', '-M', '239.80.41.1', '-p', '1044',
                           '-U', '0x00000064', '-H', '0x00000001,0x00000002,0x00000004',
                           '-Y', 'none', '-R', '15000', '-r', '0.1:0.01:2.0', '-s', '20',
                           '-L', str(self.root / 'uftp-transfer.log'), '-S', str(self.root / 'uftp.status'),
                           str(source)], 45)
        child = self.children['uftp']['process']
        try:
            rc = child.wait(timeout=45)
        except subprocess.TimeoutExpired:
            self.stop_child('uftp')
            raise TimeoutError('单次UFTP发送超时，不重试')
        self.stop_child('uftp')
        if rc:
            raise RuntimeError('UFTP非零退出: ' + str(rc))
        self.record({'event': 'downlink_source', **expected})
        return expected

    def verify_received(self, expected):
        limit = time.monotonic() + 15
        target = self.root / 'received/diagnostic-64k.bin'
        while time.monotonic() < limit:
            if target.is_file() and target.stat().st_size == 65536 and sha(target) == expected['sha256']:
                fact = {'node_id': self.node_id, 'path': str(target), 'size_bytes': target.stat().st_size, 'sha256': sha(target)}
                self.record({'event': 'verified_air_downlink', **fact})
                self.stop_child('uftpd')
                return fact
            self.check_children()
            time.sleep(.2)
        raise TimeoutError('节点%d空口接收大小/SHA失败，不skip' % self.node_id)

    def http_receiver(self):
        if self.node_id != 255:
            raise RuntimeError('仅server启动HTTP接收器')
        ready = self.root / 'http.ready'
        info = self.spawn('http', ['/usr/bin/python3', '-I', '-B', str(HELPER), 'http-receiver',
                                   '--host', '10.80.0.1', '--port', '8080', '--output-dir', str(self.root / 'uploads'),
                                   '--ready-file', str(ready)], 70)
        limit = time.monotonic() + 5
        while time.monotonic() < limit:
            if self.children['http']['process'].poll() is not None:
                raise RuntimeError('HTTP helper启动退出')
            if ready.exists() and ready.read_text().strip() == '8080':
                return info
            time.sleep(.1)
        raise TimeoutError('HTTP未就绪')

    def upload(self, expected):
        source = self.root / 'received/diagnostic-64k.bin'
        if source.stat().st_size != 65536 or sha(source) != expected['sha256']:
            raise RuntimeError('回传前文件变化')
        self.spawn('http-send', ['/usr/bin/python3', '-I', '-B', str(HELPER), 'http-sender',
                                '--host', '10.80.0.1', '--port', '8080', '--file', str(source),
                                '--node-id', str(self.node_id), '--source-ip', self.snapshot['ip'], '--timeout', '120'], 45)
        try:
            rc = self.children['http-send']['process'].wait(timeout=45)
        except subprocess.TimeoutExpired:
            self.stop_child('http-send')
            raise TimeoutError('单次HTTP回传超过诊断墙钟期限')
        self.stop_child('http-send')
        body = json.loads((self.root / 'http-send.log').read_text())
        if rc or body['status_code'] != 201 or body['size_bytes'] != 65536 or body['sha256'] != expected['sha256']:
            raise RuntimeError('HTTP回传结果失败: ' + repr(body))
        return body

    def verify_uploads(self, expected):
        facts = {}
        for node in [1, 2, 4]:
            target = self.root / ('uploads/client_%d_update.bin' % node)
            if not target.is_file() or target.stat().st_size != 65536 or sha(target) != expected['sha256']:
                raise RuntimeError('server缺失/损坏节点%d文件' % node)
            facts[str(node)] = {'path': str(target), 'size_bytes': 65536, 'sha256': sha(target)}
        return facts

    def cleanup(self):
        errors = []
        for name in ['http-send', 'uftp', 'http', 'uftpd', 'native']:
            try:
                self.stop_child(name)
            except Exception as exc:
                errors.append(name + ': ' + repr(exc))
        tun = self.snapshot['tun'] if self.snapshot else None
        if tun and self.tun_index and Path('/sys/class/net', tun).exists():
            links = json.loads(self.run(['/usr/sbin/ip', '-j', 'link', 'show', 'dev', tun]))
            if links[0]['ifindex'] != self.tun_index:
                errors.append('TUN ifindex改变，拒绝删除')
            else:
                if self.owned_route:
                    self.run(['/usr/sbin/ip', 'route', 'del', GROUP, 'dev', tun], check=False)
                self.run(['/usr/sbin/ip', 'link', 'delete', tun])
        self.owned_route = False
        self.tun_index = None
        if tun and self.stop_attempted and self.restore_result is None and Path('/sys/class/net', tun).exists():
            errors.append('自有standalone清理后仍存在TUN，恢复被阻断')
        self.cleaned = not errors
        self.record({'event': 'cleanup', 'errors': errors})
        if errors:
            raise RuntimeError('自有进程清理不完整: ' + repr(errors))
        return {'cleaned': True}

    def restore(self):
        if self.restore_result is not None:
            return self.restore_result
        if not self.stop_attempted:
            return {'untouched': True}
        if not self.cleaned or any(e['process'].poll() is None for e in self.children.values()):
            raise RuntimeError('自有资源未回收，不并发启动原服务')
        if sha(NATIVE) != NATIVE_SHA or json.loads(Path('/usr/local/lib/python3.10/dist-packages/wfb_ng/fl/build_identity.json').read_text()).get('commit') != COMMIT:
            raise RuntimeError('恢复前安装身份变化，不启动混合版本')
        for relative, expected in self.snapshot['expected_hashes'].items():
            if sha(Path('/usr/local/lib/python3.10/dist-packages/wfb_ng/fl') / relative) != expected:
                raise RuntimeError('恢复前安装文件变更: ' + relative)
        self.run(['systemctl', 'start', self.unit], timeout=25)
        props = self.unit_props()
        if props['ActiveState'] != 'active' or props['MainPID'] == '0':
            raise RuntimeError('原服务未恢复active')
        self.restore_result = {'props': props, 'iw': self.radio_info()}
        self.record({'event': 'original_restored', **self.restore_result})
        return self.restore_result

    def collect(self):
        logs = {}
        if self.root:
            for file in self.root.glob('*.log'):
                if file.stat().st_size > 2_000_000:
                    logs[file.name] = {'size_bytes': file.stat().st_size, 'sha256': sha(file), 'tail': file.read_bytes()[-150000:].decode(errors='backslashreplace')}
                else:
                    logs[file.name] = file.read_text(errors='backslashreplace')
            logs['commands.jsonl'] = (self.root / 'commands.jsonl').read_text() if (self.root / 'commands.jsonl').exists() else ''
        return {'host': self.host, 'snapshot': self.snapshot, 'restore': self.restore_result, 'logs': logs,
                'unit': self.unit_props(), 'iw': self.radio_info() if self.snapshot else None}


def main():
    node = Node()
    def interrupted(signum, frame):
        raise SystemExit(128 + signum)
    for signum in [signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGALRM]:
        signal.signal(signum, interrupted)
    signal.alarm(300)  # 控制连接丢失/卡住时，本节点300秒后仍会finally回收并恢复。
    try:
        for line in sys.stdin:
            request = json.loads(line)
            ident = request['id']
            try:
                action = request['action']
                if action not in ['cleanup', 'restore', 'collect', 'finish']:
                    node.check_children()
                if action not in ['prepare', 'stop_original', 'align_channel', 'start_native', 'receiver', 'downlink',
                                  'verify_received', 'http_receiver', 'upload', 'verify_uploads', 'cleanup', 'restore', 'collect', 'finish']:
                    raise ValueError('未知动作')
                result = {'finished': True} if action == 'finish' else getattr(node, action)(**request.get('params', {}))
                print(json.dumps({'id': ident, 'ok': True, 'result': result}, ensure_ascii=False), flush=True)
                if action == 'finish':
                    break
            except Exception as exc:
                print(json.dumps({'id': ident, 'ok': False, 'error': repr(exc)}, ensure_ascii=False), flush=True)
    finally:
        signal.alarm(0)
        # 不因EOF/SSH/HUP丢失而遗留standalone；先收自有进程，再恢复本节点原unit。
        for operation in [node.cleanup, node.restore]:
            try:
                operation()
            except Exception as exc:
                node.record({'event': 'finally_error', 'operation': operation.__name__, 'error': repr(exc)})


if __name__ == '__main__':
    main()
