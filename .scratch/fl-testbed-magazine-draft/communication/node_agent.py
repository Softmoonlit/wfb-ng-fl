#!/usr/bin/env python3
"""冻结安装角色的 JSONL 代理；不拥有常驻 native、daemon 或 TUN。"""
import contextlib
import fcntl
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import signal
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.request

COMMIT = '98dda459fb9a1cecf64fd03e224427b8b7f689fe'
NATIVE_SHA = 'ad3340e51eecc6e5691a6edb84f26f3a262b518c3555497df62ea6f7865e1e56'
WRAPPER_SHA = 'd68d2819baf2cc611e324ae7d7bd707b92f2ca243c7810249abbe2d941d00fa6'
# fl00 实际 PATH 中的 UFTP 5.0.3；运行依赖独立于 release 源码 manifest。
UFTP_SHA256 = {
    'uftp': '71d9c15497b3bd4286a2c60c9d37871a50b7032793eae8d4bf7b3b8977e015e3',
    'uftpd': '0d7a31c6cbf783ad5ecced0bdce6705c30530a258fdf605d555c748827aa85b9',
}
NODES = [1, 2, 4, 5, 6, 7, 8, 9, 10]
GROUPS = ['239.80.41.1/32', '239.80.42.1/32']
PATCH = dict(channel=157, bandwidth_mhz=40, downlink_mcs=3, uplink_mcs=4,
             radio_txpower_dbm=18, uftp_rate_kbps=12000)


def require(value, message):
    if not value:
        raise RuntimeError(message)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def verify_uftp_tools():
    """与 transport 使用相同 PATH 解析；SHA 是准入条件，版本仅作证据。"""
    tools = {}
    for name, expected_sha in UFTP_SHA256.items():
        executable = shutil.which(name)
        require(executable, 'missing UFTP executable ' + name)
        digest = sha(executable)
        require(digest == expected_sha, 'UFTP SHA mismatch ' + name + ': ' + executable
                + ' actual=' + digest + ' expected=' + expected_sha)
        # 5.0.3 的帮助参数不提供可靠版本/退出码。读取实际文件内的版本
        # banner，避免启动 sender/daemon，也不会把超长 usage 或退出码当失败。
        banner = re.search(rb'UFTP version ([0-9]+\.[0-9]+\.[0-9]+)',
                           Path(executable).read_bytes())
        tools[name] = dict(path=executable, resolved_path=str(Path(executable).resolve()),
                           sha256=digest,
                           version=banner.group(1).decode('ascii') if banner else None,
                           version_source='binary_banner')
    return tools


def verify_source_files(base, source, files):
    """校验安全相对路径、安装文件 SHA 和冻结文件，严格匹配顶层 Python 集合。"""
    require(isinstance(files, dict) and bool(files), 'manifest files required')
    require(all(isinstance(name, str) and bool(name)
                and not Path(name).is_absolute()
                and all(part not in ('', '.', '..') for part in name.split('/'))
                for name in files), 'manifest must contain only safe relative paths')
    require(source.is_dir(), 'frozen release source absent')
    installed_names = {path.name for path in base.glob('*.py')}
    source_names = {path.name for path in source.glob('*.py')}
    require(installed_names == source_names == {name for name in files if '/' not in name and name.endswith('.py')}, 'Python source file set mismatch')
    for name, digest in files.items():
        require(sha(base / name) == digest, 'installed manifest mismatch ' + name)
        # build_identity 是安装时生成的元数据，不属于冻结源码。
        if name != 'build_identity.json':
            require(sha(source / name) == digest, 'release source mismatch ' + name)


def run(argv):
    return subprocess.run(argv, check=True, capture_output=True, text=True,
                          timeout=6, cwd='/').stdout


def option(argv, key):
    require(argv.count(key) == 1, 'missing/duplicate option ' + key)
    return argv[argv.index(key) + 1]


def get_json(host, path):
    with urllib.request.urlopen('http://' + host + ':9090' + path, timeout=6) as response:
        return json.load(response)


class Node:
    def __init__(self):
        self.host = socket.gethostname()
        require(self.host in ['fl00', 'fl01', 'fl02', 'fl04', 'fl05', 'fl06', 'fl07', 'fl08', 'fl09', 'fl10'], 'unexpected host')
        self.node = int(self.host[2:])
        self.root = None
        self.role = None
        self.lock = None
        self.routes = {}
        self.prepared = False
        self.started = False
        self.received = None
        self.updates = None
        self.closed = False
        self.cleanup_result = None
        self.resident = {}
        self.radio_attempted = False
        self.release_result = None
        self.radio_verified = False
        self.closing = False
        self.started_at = time.monotonic()
        self.expires = None
        self.timing = None

    def fact(self, event, **data):
        record = dict(event=event, monotonic=time.monotonic(), **data)
        if self.root:
            with (self.root / 'facts.jsonl').open('a') as stream:
                stream.write(json.dumps(record) + '\n')
        return record

    def persist_cleanup(self):
        """在代理存活期间落盘所有权，供独立回收使用。"""
        state = dict(host=self.host, tun=getattr(self, 'tun', None),
                     ifindex=getattr(self, 'ifindex', None), routes=self.routes,
                     resident=self.resident, daemon_pid=getattr(self, 'daemon_pid', None),
                     radio_attempted=self.radio_attempted,
                     original=getattr(self, 'original', None),
                     original_channel=getattr(self, 'original_channel', None))
        temporary = self.root / 'cleanup-state.tmp'
        temporary.write_text(json.dumps(state))
        os.replace(temporary, self.root / 'cleanup-state.json')

    def prepare(self, run_id, payload, timing, patch=None):
        # 生命周期从代理启动起算；清理不得再次延长节点总期限。
        self.patch = dict(PATCH)
        if patch is not None:
            require(isinstance(patch, dict), 'invalid radio patch')
            self.patch.update(patch)
        require(self.patch['bandwidth_mhz'] in (20, 40) and self.patch['downlink_mcs'] == 3
                and self.patch['uplink_mcs'] == 4 and 0 < self.patch['uftp_rate_kbps'] <= 18000,
                'unsupported radio patch')
        if self.patch['bandwidth_mhz'] == 40:
            require(12000 <= self.patch['uftp_rate_kbps'] <= 18000, 'unsupported HT40 MCS3 rate')
        self.timing = timing
        self.expires = self.started_at + timing['node_timeout']
        signal.setitimer(signal.ITIMER_REAL, max(.001, self.expires - time.monotonic()))
        require(type(payload.get('size_bytes')) is int
                and 1 <= payload['size_bytes'] <= 64 * 1024 * 1024,
                'invalid payload size')
        require(isinstance(payload.get('sha256'), str)
                and re.fullmatch(r'[0-9a-f]{64}', payload['sha256']), 'invalid payload SHA')
        self.payload = dict(payload)
        if self.node == 0:
            require(isinstance(payload.get('path'), str) and Path(payload['path']).is_absolute(),
                    'server payload path required')
            require(Path(payload['path']).is_file(), 'server payload file absent')
            self.check_file(payload['path'])
        else:
            require(set(payload) == {'size_bytes', 'sha256'}, 'client metadata only')
        require(not self.root and re.fullmatch(r'[A-Za-z0-9_-]{1,64}', run_id), 'invalid run id/state')
        require(os.geteuid() == 0, 'root required')
        self.lock = open('/srv/wfb-ng-fl/communication.lock', 'a')
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.root = Path('/srv/wfb-ng-fl') / ('comm-' + run_id)
        self.root.mkdir(mode=0o700)
        self.runtime_dependencies = verify_uftp_tools()
        self.fact('runtime_dependencies', tools=self.runtime_dependencies)
        unit = 'wfb-fl-' + ('server' if self.node == 0 else 'client') + '-daemon.service'
        pid = int(run(['systemctl', 'show', unit, '-p', 'MainPID', '--value']).strip())
        require(pid > 0, 'daemon absent')
        daemon_argv = Path('/proc', str(pid), 'cmdline').read_bytes().decode().rstrip('\0').split('\0')
        wrappers = [Path(a) for a in daemon_argv if a.endswith('/daemon.py')]
        require(len(wrappers) == 1, 'release wrapper absent')
        release = wrappers[0].parent
        require(str(release).startswith('/srv/wfb-ng-fl/releases/heartbeat-' + COMMIT[:12] + '-'), 'unexpected release')
        require(sha(wrappers[0]) == WRAPPER_SHA, 'release wrapper SHA mismatch')
        manifest = json.loads((release / 'manifest.json').read_text())
        loaded = json.loads((release / 'loaded.json').read_text())
        require(manifest['commit'] == loaded['commit'] == COMMIT and loaded['pid'] == pid, 'release identity mismatch')
        base = Path(manifest['installed']).resolve()
        require(str(base).startswith('/usr/') and base.parts[-2:] == ('wfb_ng', 'fl'), 'installed source required')
        source = release / 'source/wfb_ng/fl'
        verify_source_files(base, source, manifest['files'])
        for item in loaded['modules'].values():
            require(Path(item['path']).resolve().parent == base and sha(item['path']) == item['sha256'], 'loaded mismatch')
        # 严格复用包装器的安装来源，排除当前目录及 PYTHONPATH 遮蔽。
        sys.path[:] = [str(base.parent.parent)] + [p for p in sys.path if p.startswith('/usr/')]
        role_module = importlib.import_module('wfb_ng.fl.role')
        require(Path(role_module.__file__).resolve().parent == base, 'role import origin mismatch')
        natives = []
        for proc in Path('/proc').glob('[0-9]*/cmdline'):
            try:
                argv = proc.read_bytes().decode().rstrip('\0').split('\0')
                if argv[0] == '/usr/bin/wfb_v6_uplink':
                    natives.append((proc.parent, argv))
            except (OSError, UnicodeError):
                continue
        require(len(natives) == 1, 'native must be unique')
        proc, argv = natives[0]
        require(sha(proc / 'exe') == NATIVE_SHA, 'native hash mismatch')
        self.resident = {str(pid): Path('/proc', str(pid), 'stat').read_text().rsplit(')', 1)[1].split()[19],
                         proc.name: (proc / 'stat').read_text().rsplit(')', 1)[1].split()[19]}
        self.tun = option(argv, '--tun-name')
        self.ip = '10.80.0.1' if self.node == 0 else '10.80.0.' + str(10 + self.node)
        require(self.tun == ('fl-s' if self.node == 0 else 'fl-c' + str(self.node)), 'TUN name mismatch')
        require(option(argv, '--tun-addr') == self.ip + '/24', 'native TUN IP mismatch')
        self.interface = option(argv, '--air-interface')
        self.control_port = int(option(argv, '--radio-control-port'))
        radio_fact = self.read_radio(channels=(149, 157), expected_bandwidth=40)
        self.original_channel = int(re.search(r'\bchannel (\d+) ', radio_fact['iw']).group(1))
        links = json.loads(run(['ip', '-j', 'addr', 'show', 'dev', self.tun]))
        require(any(a.get('local') == self.ip for a in links[0]['addr_info']), 'actual TUN IP mismatch')
        self.ifindex = links[0]['ifindex']
        self.daemon_pid = pid
        if self.node == 0:
            status = self.registry_gate()
            self.original = {key: status['radio'][key] for key in self.patch}
            require(self.original['channel'] in (149, 157)
                    and self.original['bandwidth_mhz'] == 40
                    and self.original['downlink_mcs'] == 3
                    and self.original['uplink_mcs'] == 4
                    and self.original['radio_txpower_dbm'] == 18
                    and 12000 <= self.original['uftp_rate_kbps'] <= 18000,
                    'original radio baseline mismatch')
        sockets = run(['ss', '-H', '-lntu'])
        require(not re.search(r':(?:1144|18080)\s', sockets), 'communication port occupied')
        self.role_module = role_module
        self.prepared = True
        self.persist_cleanup()
        return self.fact('prepared', daemon_pid=pid, native_pid=int(proc.name), native_sha=NATIVE_SHA,
                         tun=self.tun, ifindex=self.ifindex, radio=radio_fact,
                         wrapper_sha=WRAPPER_SHA, installed=str(base), source=str(source),
                         payload=self.payload, timing=self.timing,
                         runtime_dependencies=self.runtime_dependencies,
                         scope='等量通信；无模型语义验证、正式FL生命周期或射频所有权承诺')

    def read_radio(self, channels=None, expected_bandwidth=None):
        if channels is None:
            channels = (self.patch['channel'],)
        if expected_bandwidth is None:
            expected_bandwidth = self.patch['bandwidth_mhz']
        expected_mcs = self.patch['downlink_mcs'] if self.node == 0 else self.patch['uplink_mcs']
        require(Path('/sys/class/net', self.interface, 'device/driver').resolve().name == 'rtl88xxau_wfb', 'driver mismatch')
        iw = run(['iw', 'dev', self.interface, 'info'])
        require(any('channel ' + str(channel) + ' ' in iw for channel in channels)
                and f'width: {expected_bandwidth} MHz' in iw
                and all(text in iw for text in ['txpower -18.00 dBm', 'type monitor']), 'iw mismatch')
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(3)
            sock.sendto(struct.pack('!IB', 0x434f4d4d, 4), ('127.0.0.1', self.control_port))
            raw, address = sock.recvfrom(4096)
        radio = struct.unpack('!II7B', raw)
        require(address == ('127.0.0.1', self.control_port) and radio[:2] == (0x434f4d4d, 0)
                and radio[4] == 0 and radio[5:7] == (expected_bandwidth, expected_mcs), 'GET radio mismatch')
        return dict(iw=iw, get_radio=list(radio))

    def _old_read_radio_removed(self):
        raise AssertionError()

    def registry_gate(self):
        require(self.node == 0, 'registry IPC is server localhost only')
        status = get_json('127.0.0.1', '/api/v1/status')
        require(status.get('active_job') is None and status['server_state'] == 'IDLE', 'server job/state gate')
        online = sorted(int(key) for key, value in status['nodes'].items()
                        if value.get('readiness') != 'OFFLINE')
        require(online == NODES, 'online target set mismatch')
        for node in NODES:
            record = status['nodes'][str(node)]
            require(record['readiness'] == 'READY' and record['reported_state'] == 'IDLE'
                    and record.get('error_code') is None, 'target registry gate failed: ' + str(node))
        self.fact('registry_gate', status=status, online=online)
        return status

    def daemon_identity(self):
        pid = int(run(['systemctl', 'show', 'wfb-fl-server-daemon.service', '-p', 'MainPID', '--value']).strip())
        require(pid == self.daemon_pid, 'server daemon PID changed')
        tick = Path('/proc', str(pid), 'stat').read_text().rsplit(')', 1)[1].split()[19]
        require(tick == self.resident[str(pid)], 'server daemon identity changed')
        return pid

    def radio_transaction(self, endpoint, patch):
        before = self.daemon_identity()
        request_body = dict(confirmed=True, patch=patch, target_nodes=NODES)
        self.fact(endpoint + '_request', daemon_pid=before, request=request_body)
        request = urllib.request.Request('http://127.0.0.1:9090/api/v1/radio/' + endpoint,
            data=json.dumps(request_body).encode(), headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.load(response)
            self.fact(endpoint + '_response', response=result, daemon_pid_after=self.daemon_identity())
            require(result.get('status') == 'finalized' and result.get('calibration') is (endpoint == 'calibrate')
                    and not result.get('error_message') and not result.get('unresponsive_nodes'), 'radio transaction unsuccessful')
            return result
        except BaseException as exc:
            self.fact(endpoint + '_failure', error=repr(exc))
            raise

    def acquire_radio(self):
        require(self.node == 0 and self.prepared and not self.radio_attempted, 'acquire gate')
        self.registry_gate()
        self.radio_attempted = True
        self.persist_cleanup()
        self.fact('radio_attempted', original=self.original)
        endpoint = 'calibrate' if self.patch['bandwidth_mhz'] == 20 else 'reconfigure'
        result = self.radio_transaction(endpoint, self.patch)
        return self.fact('radio_acquired', response=result,
                         proof='普通重配事务，不持有守护进程作业准入租约')

    def verify_radio(self):
        require(self.prepared, 'prepare required')
        result = self.read_radio()
        if self.node == 0:
            status = self.registry_gate()
            require(all(status['radio'][key] == value for key, value in self.patch.items()), 'server radio configuration mismatch')
        self.radio_verified = True
        return self.fact('radio_verified', **result)

    def release_radio(self):
        require(self.node == 0, 'release only on server')
        if self.release_result is not None:
            return self.release_result
        if not self.radio_attempted:
            return self.fact('radio_release', attempted=False, ok=True)
        require(self.closed, 'close roles before release')
        try:
            response = self.radio_transaction('reconfigure', self.original)
            require(all(response['effective_config'][key] == value for key, value in self.original.items()), 'restore config mismatch')
            self.release_result = self.fact('radio_release', attempted=True, ok=True, response=response)
        except Exception as exc:
            self.release_result = self.fact('radio_release', attempted=True, ok=False, error=repr(exc))
        return self.release_result

    def start_receive(self):
        require(self.prepared and self.radio_verified and not self.started, 'prepare/radio/start gate')
        for group in GROUPS:
            routes = json.loads(run(['ip', '-j', 'route', 'show', 'exact', group]))
            if routes:
                require(len(routes) == 1 and routes[0].get('dev') == self.tun and not routes[0].get('gateway'), 'existing route mismatch')
            else:
                # 先持久化带专用协议号的意图，避免 add 与落盘之间退出后失去所有权记录。
                self.routes[group] = [dict(dst=group, dev=self.tun, protocol='186', scope='link', flags=[])]
                self.persist_cleanup()
                run(['ip', 'route', 'add', group, 'dev', self.tun, 'proto', '186'])
                self.routes[group] = json.loads(run(['ip', '-j', 'route', 'show', 'exact', group]))
                self.persist_cleanup()
        common = dict(work_dir=str(self.root / 'role'), uftp_port=1144,
                      max_update_size_bytes=self.payload['size_bytes'], uftp_bind_host=self.ip,
                      uftp_multicast_host='239.80.41.1', io_timeout=self.timing['io_timeout'])
        if self.node == 0:
            self.role = self.role_module.ServerRole(participant_node_id=NODES, participant_uftp_uid=NODES,
                server_uftp_uid=100, http_port=18080, http_host=self.ip,
                uftp_private_multicast_host='239.80.42.1',
                uftp_rate_kbps=self.patch['uftp_rate_kbps'], **common)
        else:
            self.role = self.role_module.ClientRole(node_id=self.node, uftp_uid=self.node,
                server_http_address=('10.80.0.1', 18080), **common)
        self.runtime = self.role.start()
        self.started = True
        actual_rate = self.role.transport.uftp_rate_kbps if self.node == 0 else None
        if self.node == 0:
            require(actual_rate == self.patch['uftp_rate_kbps'], 'actual UFTP rate mismatch')
        return self.fact('receive_started', actual_uftp_rate_kbps=actual_rate)

    def check_file(self, path):
        path = Path(path)
        require(path.stat().st_size == self.payload['size_bytes']
                and sha(path) == self.payload['sha256'], 'payload integrity mismatch')
        return dict(path=str(path), size_bytes=path.stat().st_size, sha256=self.payload['sha256'])

    def publish(self):
        require(self.started and not self.closed, 'role not running')
        if self.node == 0:
            path = self.root / 'payload.bin'
            # 只服务端复制真实字节；复制前后校验，避免源文件变动漏检。
            self.check_file(self.payload['path'])
            shutil.copyfile(self.payload['path'], path)
            self.check_file(path)
            self.runtime.publish_model(str(path))
            return self.fact('send_complete', round_id=self.runtime.round_id, **self.check_file(path))
        self.received = self.runtime.wait_for_model()
        return self.fact('receive_verified', **self.check_file(self.received))

    def submit(self):
        require(self.started and not self.closed, 'role not running')
        if self.node == 0:
            self.updates = self.runtime.wait_for_updates(min_updates=len(NODES), timeout=self.timing['io_timeout'])
            require(set(self.updates) == set(NODES), 'missing updates')
            return self.fact('updates_received', nodes=sorted(self.updates))
        require(self.received is not None, 'receive barrier absent')
        self.check_file(self.received)
        self.runtime.submit_update(self.received)
        return self.fact('update_sent', node=self.node)

    def verify(self):
        require(self.node == 0 and self.updates is not None and not self.closed, 'update verify gate')
        return self.fact('updates_verified', files={str(n): self.check_file(self.updates[n]) for n in NODES})

    def audit_final(self):
        require(self.prepared and self.closed, 'audit requires prepared/closed')
        actual = {}
        try:
            actual['radio'] = self.read_radio(channels=(self.original_channel,), expected_bandwidth=40)
            actual['resident'] = {}
            for pid, tick in self.resident.items():
                current = Path('/proc', pid, 'stat').read_text().rsplit(')', 1)[1].split()[19]
                actual['resident'][pid] = current
                require(current == tick, 'resident identity changed: ' + pid)
            actual['nvpmodel'] = run(['nvpmodel', '-q'])
            require(actual['nvpmodel'].strip() == 'NV Power Mode: 15W\n0', 'nvpmodel mismatch')
            # 路径来自既有十端 board-power 审计及 audit-jtop.py，仅只读。
            expected = {'/sys/devices/system/cpu/online': '0-5',
                        '/sys/devices/platform/17000000.gpu/devfreq_dev/max_freq': '612000000',
                        '/sys/kernel/nvpmodel_clk_cap/emc': '2133000000',
                        '/sys/kernel/debug/bpmp/debug/clk/emc/mrq_rate_locked': '0'}
            for core in range(6):
                base = '/sys/devices/system/cpu/cpu' + str(core)
                expected[base + '/online'] = '1'
                expected[base + '/cpufreq/scaling_max_freq'] = '1497600'
            actual['files'] = {path: Path(path).read_text().strip() for path in expected}
            require(actual['files'] == expected, 'power/clock mismatch')
            return self.fact('audit_final', ok=True, actual=actual)
        except Exception as exc:
            return self.fact('audit_final', ok=False, actual=actual, error=repr(exc))

    def close(self):
        if self.cleanup_result is not None:
            return self.cleanup_result
        errors = []
        role_close_ok = self.role is None
        if self.role:
            try:
                self.role.close()
                role_close_ok = True
            except Exception as exc:
                errors.append('role: ' + repr(exc))
        for group, owned in self.routes.items():
            try:
                links = json.loads(run(['ip', '-j', 'link', 'show', 'dev', self.tun]))
                require(links[0]['ifindex'] == self.ifindex, 'ifindex changed; retain route')
                current = json.loads(run(['ip', '-j', 'route', 'show', 'exact', group]))
                require(current == owned, 'route changed; retain route')
                run(['ip', 'route', 'del', group, 'dev', self.tun, 'proto', '186'])
            except Exception as exc:
                errors.append(group + ': ' + repr(exc))
        for pid, tick in self.resident.items():
            try:
                current = Path('/proc', pid, 'stat').read_text().rsplit(')', 1)[1].split()[19]
                require(current == tick, 'resident process identity changed')
            except Exception as exc:
                errors.append('resident ' + pid + ': ' + repr(exc))
        self.closed = True
        self.cleanup_result = self.fact('cleanup', ok=not errors, errors=errors, resident_pids=list(self.resident),
                                        role_close_returned=role_close_ok, owned_routes=list(self.routes))
        return self.cleanup_result


def main():
    node = Node()
    protocol = sys.stdout
    def hard_timeout(signum, frame):
        if node.root:
            (node.root / 'cleanup_timeout').write_text('清理期限届满；临时 unit 回收剩余自有进程\n')
        os._exit(124)
    def begin_cleanup():
        if not node.closing:
            node.closing = True
            signal.signal(signal.SIGALRM, hard_timeout)
            remaining = max(.001, node.expires - time.monotonic()) if node.expires else .001
            signal.setitimer(signal.ITIMER_REAL, min(node.timing['cleanup_timeout'], remaining)
                             if node.timing else remaining)
    def interrupted(signum, frame):
        raise SystemExit(128 + signum)
    for sig in [signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGALRM]:
        signal.signal(sig, interrupted)
    actions = ['prepare', 'acquire_radio', 'verify_radio', 'start_receive',
               'publish', 'submit', 'verify', 'close', 'release_radio', 'audit_final']
    try:
        for line in sys.stdin:
            request = json.loads(line)
            try:
                action = request['action']
                require(action in actions, 'unknown RPC')
                if action == 'close':
                    begin_cleanup()
                with contextlib.redirect_stdout(sys.stderr):
                    result = getattr(node, action)(**request.get('params', {}))
                response = dict(id=request['id'], ok=True, result=result)
            except Exception as exc:
                response = dict(id=request['id'], ok=False, error=repr(exc))
            finally:
                if request.get('action') == 'close' and node.closed and node.expires is not None:
                    node.closing = False
                    signal.signal(signal.SIGALRM, interrupted)
                    signal.setitimer(signal.ITIMER_REAL, max(.001, node.expires - time.monotonic()))
            protocol.write(json.dumps(response) + '\n')
            protocol.flush()
    finally:
        begin_cleanup()
        with contextlib.redirect_stdout(sys.stderr):
            try:
                node.close()
            finally:
                # 跨节点角色关闭和射频恢复由父编排及独立回收统一协调。
                if node.lock:
                    node.lock.close()
        signal.setitimer(signal.ITIMER_REAL, 0)


if __name__ == '__main__':
    main()
