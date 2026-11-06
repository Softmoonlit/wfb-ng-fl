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
import socket
import struct
import subprocess
import sys
import time
import urllib.request

COMMIT = 'c6659488f64976185ffe02a7c856f7fd8c821ee0'
NATIVE_SHA = 'ad3340e51eecc6e5691a6edb84f26f3a262b518c3555497df62ea6f7865e1e56'
WRAPPER_SHA = 'd68d2819baf2cc611e324ae7d7bd707b92f2ca243c7810249abbe2d941d00fa6'
NODES = [1, 2, 4, 5]
GROUPS = ['239.80.41.1/32', '239.80.42.1/32']
SIZE = 1048576
PAYLOAD = bytes(range(256)) * (SIZE // 256)
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()
PATCH = dict(channel=157, bandwidth_mhz=40, downlink_mcs=3, uplink_mcs=4,
             radio_txpower_dbm=18, uftp_rate_kbps=15000)


def require(value, message):
    if not value:
        raise RuntimeError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


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
        require(self.host in ['fl00', 'fl01', 'fl02', 'fl04', 'fl05'], 'unexpected host')
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
                     original=getattr(self, 'original', None))
        temporary = self.root / 'cleanup-state.tmp'
        temporary.write_text(json.dumps(state))
        os.replace(temporary, self.root / 'cleanup-state.json')

    def prepare(self, run_id):
        require(not self.root and re.fullmatch(r'[A-Za-z0-9_-]{1,64}', run_id), 'invalid run id/state')
        require(os.geteuid() == 0, 'root required')
        self.lock = open('/srv/wfb-ng-fl/communication.lock', 'a')
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.root = Path('/srv/wfb-ng-fl') / ('comm-' + run_id)
        self.root.mkdir(mode=0o700)
        unit = 'wfb-fl-' + ('server' if self.node == 0 else 'client') + '-daemon.service'
        pid = int(run(['systemctl', 'show', unit, '-p', 'MainPID', '--value']).strip())
        require(pid > 0, 'daemon absent')
        daemon_argv = Path('/proc', str(pid), 'cmdline').read_bytes().decode().rstrip('\0').split('\0')
        wrappers = [Path(a) for a in daemon_argv if a.endswith('/daemon.py')]
        require(len(wrappers) == 1, 'release wrapper absent')
        release = wrappers[0].parent
        require(str(release).startswith('/srv/wfb-ng-fl/releases/heartbeat-c6659488f649-'), 'unexpected release')
        require(sha(wrappers[0]) == WRAPPER_SHA, 'release wrapper SHA mismatch')
        manifest = json.loads((release / 'manifest.json').read_text())
        loaded = json.loads((release / 'loaded.json').read_text())
        require(manifest['commit'] == loaded['commit'] == COMMIT and loaded['pid'] == pid, 'release identity mismatch')
        base = Path(manifest['installed']).resolve()
        require(str(base).startswith('/usr/') and base.parts[-2:] == ('wfb_ng', 'fl'), 'installed source required')
        for name, digest in manifest['files'].items():
            require(sha(base / name) == digest, 'installed manifest mismatch ' + name)
        source = release / 'source/wfb_ng/fl'
        require(source.is_dir(), 'frozen release source absent')
        for path in source.glob('*.py'):
            require(sha(path) == manifest['files'].get(path.name), 'release source mismatch ' + path.name)
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
        radio_fact = self.read_radio(channels=(149, 157))
        links = json.loads(run(['ip', '-j', 'addr', 'show', 'dev', self.tun]))
        require(any(a.get('local') == self.ip for a in links[0]['addr_info']), 'actual TUN IP mismatch')
        self.ifindex = links[0]['ifindex']
        self.daemon_pid = pid
        if self.node == 0:
            status = self.registry_gate()
            self.original = {key: status['radio'][key] for key in PATCH}
            require(self.original == dict(PATCH, channel=self.original['channel'])
                    and self.original['channel'] in (149, 157), 'original radio baseline mismatch')
        sockets = run(['ss', '-H', '-lntu'])
        require(not re.search(r':(?:1144|18080)\s', sockets), 'communication port occupied')
        self.role_module = role_module
        self.prepared = True
        self.persist_cleanup()
        return self.fact('prepared', daemon_pid=pid, native_pid=int(proc.name), native_sha=NATIVE_SHA,
                         tun=self.tun, ifindex=self.ifindex, radio=radio_fact,
                         wrapper_sha=WRAPPER_SHA, installed=str(base), source=str(source),
                         scope='transport only; no job lifecycle or RF ownership claim')

    def read_radio(self, channels=(157,)):
        require(Path('/sys/class/net', self.interface, 'device/driver').resolve().name == 'rtl88xxau_wfb', 'driver mismatch')
        iw = run(['iw', 'dev', self.interface, 'info'])
        require(any('channel ' + str(channel) + ' ' in iw for channel in channels)
                and all(text in iw for text in ['width: 40 MHz', 'txpower -18.00 dBm', 'type monitor']), 'iw mismatch')
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(3)
            sock.sendto(struct.pack('!IB', 0x434f4d4d, 4), ('127.0.0.1', self.control_port))
            raw, address = sock.recvfrom(4096)
        radio = struct.unpack('!II7B', raw)
        require(address == ('127.0.0.1', self.control_port) and radio[:2] == (0x434f4d4d, 0)
                and radio[4:7] == (0, 40, 3 if self.node == 0 else 4), 'GET radio mismatch')
        return dict(iw=iw, get_radio=list(radio))

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
        result = self.radio_transaction('reconfigure', PATCH)
        return self.fact('radio_acquired', response=result,
                         proof='普通重配事务，不持有守护进程作业准入租约')

    def verify_radio(self):
        require(self.prepared, 'prepare required')
        result = self.read_radio()
        if self.node == 0:
            status = self.registry_gate()
            require(all(status['radio'][key] == value for key, value in PATCH.items()), 'server radio configuration mismatch')
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
                      max_update_size_bytes=SIZE, uftp_bind_host=self.ip,
                      uftp_multicast_host='239.80.41.1', io_timeout=60)
        if self.node == 0:
            self.role = self.role_module.ServerRole(participant_node_id=NODES, participant_uftp_uid=NODES,
                server_uftp_uid=100, http_port=18080, http_host=self.ip,
                uftp_private_multicast_host='239.80.42.1', **common)
        else:
            self.role = self.role_module.ClientRole(node_id=self.node, uftp_uid=self.node,
                server_http_address=('10.80.0.1', 18080), **common)
        self.runtime = self.role.start()
        self.started = True
        return self.fact('receive_started')

    def check_file(self, path):
        path = Path(path)
        require(path.stat().st_size == SIZE and sha(path) == DIGEST, 'payload integrity mismatch')
        return dict(path=str(path), size=SIZE, sha256=DIGEST)

    def publish(self):
        require(self.started and not self.closed, 'role not running')
        if self.node == 0:
            path = self.root / 'payload.bin'
            path.write_bytes(PAYLOAD)
            self.runtime.publish_model(str(path))
            return self.fact('send_complete', round_id=self.runtime.round_id, **self.check_file(path))
        self.received = self.runtime.wait_for_model()
        return self.fact('receive_verified', **self.check_file(self.received))

    def submit(self):
        require(self.started and not self.closed, 'role not running')
        if self.node == 0:
            self.updates = self.runtime.wait_for_updates(min_updates=4, timeout=60)
            require(set(self.updates) == set(NODES), 'missing updates')
            return self.fact('updates_received', nodes=sorted(self.updates))
        require(self.received is not None, 'receive barrier absent')
        self.check_file(self.received)
        self.runtime.submit_update(self.received)
        return self.fact('update_sent', node=self.node)

    def verify(self):
        require(self.node == 0 and self.updates is not None and not self.closed, 'update verify gate')
        return self.fact('updates_verified', files={str(n): self.check_file(self.updates[n]) for n in NODES})

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
            (node.root / 'cleanup_timeout').write_text('清理超过十秒；临时 unit 回收剩余自有进程\n')
        os._exit(124)
    def begin_cleanup():
        if not node.closing:
            node.closing = True
            signal.signal(signal.SIGALRM, hard_timeout)
            signal.alarm(10)
    def interrupted(signum, frame):
        raise SystemExit(128 + signum)
    for sig in [signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGALRM]:
        signal.signal(sig, interrupted)
    signal.alarm(225)  # 整体期限届满后，finally 最多再清理十秒。
    expires = time.monotonic() + 225
    actions = ['prepare', 'acquire_radio', 'verify_radio', 'start_receive',
               'publish', 'submit', 'verify', 'close', 'release_radio']
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
                if request.get('action') == 'close' and node.closed:
                    node.closing = False
                    signal.signal(signal.SIGALRM, interrupted)
                    signal.alarm(max(1, int(expires - time.monotonic())))
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
        signal.alarm(0)


if __name__ == '__main__':
    main()
