#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json
import os
import shutil
import signal
import socket
import stat
import subprocess
import tempfile
import threading
import time
import unittest
import uuid


_SYSTEMD_TEST_ENABLED = (
    os.environ.get('WFB_RUN_SYSTEMD_TESTS') == '1' and
    hasattr(os, 'geteuid') and os.geteuid() == 0 and
    shutil.which('systemctl') is not None
)


@unittest.skipUnless(
    _SYSTEMD_TEST_ENABLED,
    '设置 WFB_RUN_SYSTEMD_TESTS=1 并以 root 运行 systemd 生命周期测试')
class RoleSystemdLifecycleTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='wfb-v8-systemd-')
        self.repo_root = os.path.abspath(os.path.join(
            os.path.dirname(__file__), '..', '..'))
        self.bin_dir = os.path.join(self.root, 'bin')
        self.hook_dir = os.path.join(self.root, 'hooks')
        os.makedirs(self.bin_dir)
        os.makedirs(self.hook_dir)
        self.tun_name = 'wf%s' % uuid.uuid4().hex[:8]
        self.uftp_port = self.reserve_udp_port()
        self.http_server = BlockingUpdateServer(self.root)
        self.http_server.start()
        self.addCleanup(self.http_server.close)
        self.model_path = os.path.join(self.root, 'model.bin')
        self.update_path = os.path.join(self.root, 'update.bin')
        with open(self.model_path, 'wb') as fh:
            fh.write(b'model')
        with open(self.update_path, 'wb') as fh:
            fh.write(b'update')
        self.write_sitecustomize()
        self.write_link_fixture()
        self.write_uftpd_fixture()
        self.write_uftp_fixture()
        self.units = []
        self.addCleanup(self.cleanup)

    def test_real_client_stop_kill_and_restart_have_one_generation(self):
        work_dir = os.path.join(self.root, 'client-work')
        config_path = self.write_config('client', work_dir)
        unit_name = self.write_unit('client', config_path)

        subprocess.run(['systemctl', 'start', unit_name], check=True)
        first = self.wait_for_generation(1, receiver=True)
        first_main = self.main_pid(unit_name)
        self.http_server.wait_for_request(1)

        subprocess.run(['systemctl', 'stop', unit_name], check=True)
        self.assert_process_gone(first_main)
        self.assert_generation_gone(first)
        self.http_server.wait_for_closed(1)

        restart_work_dir = os.path.join(self.root, 'client-restart-work')
        restart_config_path = self.write_config(
            'client', restart_work_dir, name='client-restart.json')
        restart_unit_name = self.write_unit('client', restart_config_path)
        subprocess.run(['systemctl', 'start', restart_unit_name], check=True)
        second = self.wait_for_generation(2, receiver=True)
        second_main = self.main_pid(restart_unit_name)
        self.http_server.wait_for_request(2)
        second_state_path = self.wait_for_round_state(
            restart_work_dir, 'submitting_update')
        os.kill(second_main, signal.SIGKILL)

        third = self.wait_for_generation(3, receiver=True)
        third_main = self.main_pid(restart_unit_name)
        self.assertNotEqual(second_main, third_main)
        self.assert_generation_gone(second)
        self.http_server.wait_for_closed(2)
        self.assertEqual(
            'runtime_restarted', self.read_json(second_state_path)['error_code'])
        self.assertFalse(self.http_server.overlap_detected)
        self.assertFalse(os.path.exists(os.path.join(self.root, 'overlap')))

        subprocess.run(['systemctl', 'stop', restart_unit_name], check=True)
        self.assert_process_gone(third_main)
        self.assert_generation_gone(third)

    def test_real_server_active_downlink_stop_kill_and_restart_do_not_overlap(self):
        work_dir = os.path.join(self.root, 'server-work')
        config_path = self.write_config('server', work_dir)
        unit_name = self.write_unit('server', config_path)

        subprocess.run(['systemctl', 'start', unit_name], check=True)
        first = self.wait_for_generation(1, receiver=False, operation=True)
        first_main = self.main_pid(unit_name)

        subprocess.run(['systemctl', 'stop', unit_name], check=True)
        self.assert_process_gone(first_main)
        self.assert_generation_gone(first)

        restart_work_dir = os.path.join(self.root, 'server-restart-work')
        restart_config_path = self.write_config(
            'server', restart_work_dir, name='server-restart.json')
        restart_unit_name = self.write_unit('server', restart_config_path)
        subprocess.run(['systemctl', 'start', restart_unit_name], check=True)
        second = self.wait_for_generation(2, receiver=False, operation=True)
        second_main = self.main_pid(restart_unit_name)
        second_state_path = self.wait_for_round_state(restart_work_dir)
        os.kill(second_main, signal.SIGKILL)

        third = self.wait_for_generation(3, receiver=False)
        third_main = self.main_pid(restart_unit_name)
        self.assertNotEqual(second_main, third_main)
        self.assert_generation_gone(second)
        self.assertEqual(
            'runtime_restarted', self.read_json(second_state_path)['error_code'])
        self.assertFalse(os.path.exists(os.path.join(self.root, 'overlap')))

        subprocess.run(['systemctl', 'stop', restart_unit_name], check=True)
        self.assert_process_gone(third_main)
        self.assert_generation_gone(third)

    def write_unit(self, role, config_path):
        unit_name = 'wfb-fl-%s-%s.service' % (role, uuid.uuid4().hex)
        unit_path = os.path.join('/run/systemd/system', unit_name)
        entry = os.path.join(self.repo_root, 'scripts', 'wfb-fl-%s' % role)
        path = self.bin_dir + ':' + os.environ.get('PATH', '/usr/bin:/bin')
        with open(unit_path, 'w', encoding='utf-8') as fh:
            fh.write(
                '[Unit]\n'
                'Description=WFB-ng FL real role lifecycle test\n\n'
                '[Service]\n'
                'Type=notify\n'
                'NotifyAccess=main\n'
                'ExecStart=%s --config %s\n'
                'Environment=PYTHONPATH=%s:%s\n'
                'Environment=PATH=%s\n'
                'Environment=WFB_TEST_ROLE=%s\n'
                'Environment=WFB_TEST_MODEL=%s\n'
                'Environment=WFB_TEST_UPDATE=%s\n'
                'KillMode=control-group\n'
                'TimeoutStartSec=10s\n'
                'TimeoutStopSec=2s\n'
                'Restart=on-failure\n'
                'RestartSec=100ms\n' % (
                    entry, config_path, self.hook_dir, self.repo_root, path,
                    role, self.model_path, self.update_path))
        self.units.append((unit_name, unit_path))
        subprocess.run(['systemctl', 'daemon-reload'], check=True)
        return unit_name

    def write_config(self, role, work_dir, name=None):
        config = {
            'schema_version': 1,
            'role': role,
            'work_dir': work_dir,
            'node_id': 255 if role == 'server' else 1,
            'uftp_port': self.uftp_port,
            'max_update_size_bytes': 4096,
            'link_args': ['--tun-name', self.tun_name],
        }
        if role == 'server':
            config.update({
                'participant_node_ids': [1],
                'participant_uftp_uids': [1],
                'server_uftp_uid': 255,
                'http_host': '127.0.0.1',
                'http_port': 0,
            })
            config['link_args'] += ['--known-clients', '1']
        else:
            config.update({
                'uftp_uid': 1,
                'server_http_host': '127.0.0.1',
                'server_http_port': self.http_server.port,
            })
        path = os.path.join(self.root, name or '%s.json' % role)
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(config, fh)
        return path

    def write_sitecustomize(self):
        path = os.path.join(self.hook_dir, 'sitecustomize.py')
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write('''import os
import threading
if os.environ.get("WFB_TEST_ROLE") in ("server", "client"):
    from wfb_ng.fl import service
    original_notify_ready = service._notify_ready
    original_start = service.RoleService.start
    def start_and_capture(self):
        runtime = original_start(self)
        service._SYSTEMD_TEST_RUNTIME = runtime
        return runtime
    service.RoleService.start = start_and_capture
    if os.environ["WFB_TEST_ROLE"] == "server":
        def notify_ready_and_run():
            original_notify_ready()
            threading.Thread(
                target=service._SYSTEMD_TEST_RUNTIME.publish_model,
                args=(os.environ["WFB_TEST_MODEL"],),
                daemon=True).start()
    else:
        def run_client():
            runtime = service._SYSTEMD_TEST_RUNTIME
            runtime.wait_for_model()
            runtime.submit_update(os.environ["WFB_TEST_UPDATE"])
        def notify_ready_and_run():
            original_notify_ready()
            threading.Thread(target=run_client, daemon=True).start()
    service._notify_ready = notify_ready_and_run
''')

    def write_link_fixture(self):
        self.write_executable('wfb_v6_uplink', '''#!/usr/bin/env python3
import fcntl
import os
import struct
import sys
import time
name = sys.argv[sys.argv.index("--tun-name") + 1]
root = os.path.dirname(os.path.dirname(os.path.realpath(sys.argv[0])))
generation_path = os.path.join(root, "generation")
try:
    with open(generation_path, "r") as source:
        generation = int(source.read()) + 1
except FileNotFoundError:
    generation = 1
with open(generation_path, "w") as output:
    output.write(str(generation))
if generation > 1:
    for prefix in ("link", "operation"):
        previous_path = os.path.join(root, "%s-%d.pid" % (prefix, generation - 1))
        try:
            with open(previous_path, "r") as source:
                previous_pid = int(source.read())
            os.kill(previous_pid, 0)
        except (FileNotFoundError, ProcessLookupError):
            continue
        with open(os.path.join(root, "overlap"), "w") as output:
            output.write("%s %d" % (prefix, previous_pid))
    previous_put = os.path.join(root, "put-%d.active" % (generation - 1))
    previous_put_closed = os.path.join(root, "put-%d.closed" % (generation - 1))
    if os.path.exists(previous_put) and not os.path.exists(previous_put_closed):
        with open(os.path.join(root, "overlap"), "w") as output:
            output.write("put %d" % (generation - 1))
tun = os.open("/dev/net/tun", os.O_RDWR)
fcntl.ioctl(tun, 0x400454ca, struct.pack("16sH", name.encode("ascii"), 0x1001))
with open(os.path.join(root, "link-%d.pid" % generation), "w") as output:
    output.write(str(os.getpid()))
while True:
    time.sleep(3600)
''')

    def write_uftpd_fixture(self):
        self.write_executable('uftpd', '''#!/usr/bin/env python3
import hashlib
import json
import os
import socket
import sys
import time
import uuid
port = int(sys.argv[sys.argv.index("-p") + 1])
inbox = sys.argv[sys.argv.index("-D") + 1]
root = os.path.dirname(os.path.dirname(os.path.realpath(sys.argv[0])))
with open(os.path.join(root, "generation"), "r") as source:
    generation = int(source.read())
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("127.0.0.1", port))
round_id = str(uuid.uuid4())
candidate = os.path.join(inbox, round_id)
os.makedirs(candidate)
model = b"model"
with open(os.path.join(candidate, "model.bin"), "wb") as output:
    output.write(model)
with open(os.path.join(candidate, "model.manifest.json"), "w") as output:
    json.dump({
        "schema_version": 1,
        "artifact_type": "model",
        "round_id": round_id,
        "size_bytes": len(model),
        "sha256": hashlib.sha256(model).hexdigest(),
        "participant_node_ids": [1],
    }, output)
with open(os.path.join(root, "receiver-%d.pid" % generation), "w") as output:
    output.write(str(os.getpid()))
while True:
    time.sleep(3600)
''')

    def write_uftp_fixture(self):
        self.write_executable('uftp', '''#!/usr/bin/env python3
import os
import signal
import sys
import time
root = os.path.dirname(os.path.dirname(os.path.realpath(sys.argv[0])))
with open(os.path.join(root, "generation"), "r") as source:
    generation = int(source.read())
with open(os.path.join(root, "operation-%d.pid" % generation), "w") as output:
    output.write(str(os.getpid()))
signal.signal(signal.SIGTERM, lambda signum, frame: None)
while True:
    time.sleep(3600)
''')

    def write_executable(self, name, content):
        path = os.path.join(self.bin_dir, name)
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(content)
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

    def wait_for_generation(self, generation, receiver, operation=False):
        paths = {'link': os.path.join(self.root, 'link-%d.pid' % generation)}
        if receiver:
            paths['receiver'] = os.path.join(
                self.root, 'receiver-%d.pid' % generation)
        if operation:
            paths['operation'] = os.path.join(
                self.root, 'operation-%d.pid' % generation)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            result = {}
            try:
                for name, path in paths.items():
                    with open(path, 'r', encoding='ascii') as fh:
                        result[name] = int(fh.read())
                if all(self.process_exists(pid) for pid in result.values()):
                    return result
            except (FileNotFoundError, ValueError):
                pass
            time.sleep(0.02)
        self.fail('角色服务 generation %d 未进入 ready' % generation)

    def wait_for_round_state(self, work_dir, expected_state='publishing_model'):
        rounds_dir = os.path.join(work_dir, 'rounds')
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                names = os.listdir(rounds_dir)
                if len(names) == 1:
                    path = os.path.join(
                        rounds_dir, names[0], 'round-state.json')
                    with open(path, 'r', encoding='utf-8') as fh:
                        state = json.load(fh)
                    if state.get('state') == expected_state:
                        return path
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            time.sleep(0.02)
        self.fail('角色未建立预期活动轮次状态 %s' % expected_state)

    def main_pid(self, unit_name):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            value = int(self.unit_property(unit_name, 'MainPID'))
            if value > 0 and self.process_exists(value):
                return value
            time.sleep(0.02)
        self.fail('角色主进程未启动')

    def unit_property(self, unit_name, name):
        return subprocess.check_output([
            'systemctl', 'show', unit_name, '--property', name, '--value'
        ], text=True).strip()

    def assert_generation_gone(self, generation):
        for pid in generation.values():
            self.assert_process_gone(pid)

    def assert_process_gone(self, pid):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if not self.process_exists(pid):
                return
            time.sleep(0.02)
        self.fail('进程 %d 未从角色 unit cgroup 清理' % pid)

    def process_exists(self, pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True

    def reserve_udp_port(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(('127.0.0.1', 0))
            return sock.getsockname()[1]
        finally:
            sock.close()

    def read_json(self, path):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                with open(path, 'r', encoding='utf-8') as fh:
                    value = json.load(fh)
                if value.get('error_code') == 'runtime_restarted':
                    return value
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            time.sleep(0.02)
        self.fail('重启后的轮次状态未持久化')

    def cleanup(self):
        for unit_name, _ in self.units:
            subprocess.run(
                ['systemctl', 'stop', unit_name], check=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _, unit_path in self.units:
            try:
                os.unlink(unit_path)
            except FileNotFoundError:
                pass
        if self.units:
            subprocess.run(
                ['systemctl', 'daemon-reload'], check=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            subprocess.run(
                ['ip', 'link', 'delete', self.tun_name], check=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        finally:
            shutil.rmtree(self.root, ignore_errors=True)


class BlockingUpdateServer(object):
    def __init__(self, root):
        self.root = root
        self._listener = socket.socket()
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(('127.0.0.1', 0))
        self._listener.listen()
        self._listener.settimeout(0.2)
        self.port = self._listener.getsockname()[1]
        self._stop = threading.Event()
        self._condition = threading.Condition()
        self._connections = {}
        self._closed = set()
        self.overlap_detected = False
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self):
        self._thread.start()

    def _serve(self):
        request_id = 0
        while not self._stop.is_set():
            try:
                connection, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            request_id += 1
            threading.Thread(
                target=self._handle, args=(request_id, connection),
                daemon=True).start()

    def _handle(self, request_id, connection):
        connection.settimeout(5)
        source = connection.makefile('rb')
        try:
            content_length = None
            while True:
                line = source.readline()
                if line in (b'\r\n', b'\n'):
                    break
                if line.lower().startswith(b'content-length:'):
                    content_length = int(line.split(b':', 1)[1])
            connection.sendall(b'HTTP/1.1 100 Continue\r\n\r\n')
            remaining = content_length
            while remaining:
                data = source.read(remaining)
                if not data:
                    return
                remaining -= len(data)
            with self._condition:
                if any(identifier not in self._closed
                       for identifier in self._connections):
                    self.overlap_detected = True
                self._connections[request_id] = connection
                with open(os.path.join(
                        self.root, 'put-%d.active' % request_id), 'w') as fh:
                    fh.write(str(request_id))
                self._condition.notify_all()
            while connection.recv(1):
                pass
        except (OSError, socket.timeout, TypeError):
            pass
        finally:
            source.close()
            connection.close()
            with self._condition:
                self._closed.add(request_id)
                with open(os.path.join(
                        self.root, 'put-%d.closed' % request_id), 'w') as fh:
                    fh.write(str(request_id))
                self._condition.notify_all()

    def wait_for_request(self, request_id):
        self._wait_for(lambda: request_id in self._connections,
                       'HTTP PUT %d 未进入活动状态' % request_id)

    def wait_for_closed(self, request_id):
        self._wait_for(lambda: request_id in self._closed,
                       'HTTP PUT %d 未被角色停止流程关闭' % request_id)

    def _wait_for(self, predicate, message):
        deadline = time.monotonic() + 10
        with self._condition:
            while not predicate():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError(message)
                self._condition.wait(remaining)

    def close(self):
        self._stop.set()
        self._listener.close()
        with self._condition:
            connections = list(self._connections.values())
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        self._thread.join(2)


if __name__ == '__main__':
    unittest.main()
