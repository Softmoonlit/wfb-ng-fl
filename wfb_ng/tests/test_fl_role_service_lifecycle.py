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
from unittest import mock

from wfb_ng.fl import FLRuntimeError
from wfb_ng.fl.role import ServerRole
from wfb_ng.fl.service import LinkProcess, RoleService, load_role_service
from wfb_ng.fl.transport import ClientTransport, ServerTransport


class RoleLifecycleTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='wfb-v8-role-service-')
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_server_transport_rejects_duplicate_receiver(self):
        transport = ServerTransport((1,), 2, 9000)
        fake_server = mock.Mock()
        fake_server.server_address = ('127.0.0.1', 1234)
        with mock.patch('wfb_ng.fl.transport.shutil.which', return_value='/usr/bin/uftp'), \
                mock.patch('wfb_ng.fl.transport.http.server.ThreadingHTTPServer',
                           return_value=fake_server):
            transport.start()
            with self.assertRaises(FLRuntimeError) as raised:
                transport.start()
            transport.close()

        self.assertEqual('transport_already_started', raised.exception.error_code)
        self.assertIs(transport._http_server, None)

    def test_server_transport_observes_listener_failure(self):
        transport = ServerTransport((1,), 2, 9000)
        fake_server = mock.Mock()
        fake_server.server_address = ('127.0.0.1', 1234)
        fake_server.serve_forever.return_value = None
        with mock.patch('wfb_ng.fl.transport.shutil.which', return_value='/usr/bin/uftp'), \
                mock.patch('wfb_ng.fl.transport.http.server.ThreadingHTTPServer',
                           return_value=fake_server):
            transport.start()
            transport._http_thread.join(1)
            error = transport.poll_failure()
            transport.close()

        self.assertIsNotNone(error)
        self.assertEqual('transport_failed', error.error_code)
        self.assertFalse(transport.ready)

    def test_client_transport_rejects_duplicate_receiver(self):
        transport = ClientTransport(self.root, 1, 9000, ('127.0.0.1', 8080))
        process = mock.Mock()
        process.poll.return_value = None
        with mock.patch('wfb_ng.fl.transport.shutil.which', return_value='/usr/bin/uftpd'), \
                mock.patch('wfb_ng.fl.transport.subprocess.Popen', return_value=process), \
                mock.patch('wfb_ng.fl.transport._process_listens_udp', return_value=True):
            transport.start()
            with self.assertRaises(FLRuntimeError) as raised:
                transport.start()
            transport.close()

        self.assertEqual('transport_already_started', raised.exception.error_code)

    def test_service_rolls_back_link_and_role_when_receiver_start_fails(self):
        events = []
        link = StubLink(events)
        role = StubRole(events, start_error=FLRuntimeError(
            'transport_start_failed', '接收器启动失败'))
        service = RoleService(role, link)

        with self.assertRaises(FLRuntimeError) as raised:
            service.start()

        self.assertEqual('transport_start_failed', raised.exception.error_code)
        self.assertEqual([
            'link.start', 'role.start', 'role.close_transport',
            'link.close', 'role.close_runtime'], events)
        self.assertFalse(service.ready)

    def test_service_stops_transport_before_link_and_runtime_lock(self):
        events = []
        link = StubLink(events)
        role = StubRole(events)
        service = RoleService(role, link)
        service.start()
        service.close()

        self.assertEqual([
            'link.start', 'role.start', 'role.close_transport',
            'link.close', 'role.close_runtime'], events)
        self.assertFalse(service.ready)

    def test_service_observes_link_failure(self):
        link = StubLink([])
        role = StubRole([])
        service = RoleService(role, link)
        service.start()
        link.return_code = 7

        with self.assertRaises(FLRuntimeError) as raised:
            service.wait(0.01)

        self.assertEqual('link_process_failed', raised.exception.error_code)
        self.assertFalse(service.ready)

    def test_client_close_interrupts_and_waits_for_active_put(self):
        update_path = os.path.join(self.root, 'update.bin')
        with open(update_path, 'wb') as fh:
            fh.write(b'update')
        client_sock, server_sock = socket.socketpair()
        self.addCleanup(server_sock.close)
        transport = ClientTransport(
            self.root, 1, 9000, ('127.0.0.1', 8080), io_timeout=5)
        transport.ready = True
        transport._state = 'ready'
        result = ThreadResult(lambda: transport.submit_update(
            '00000000-0000-4000-8000-000000000001', 1, update_path,
            6, '2937013f21818106b0311e32151d1e1977d804c3e5e7572bd3913db16b36fbb2'))
        with mock.patch('wfb_ng.fl.transport.socket.create_connection',
                        return_value=client_sock):
            result.start()
            server_file = server_sock.makefile('rwb')
            self.addCleanup(server_file.close)
            while server_file.readline() not in (b'\r\n', b'\n'):
                pass
            server_file.write(b'HTTP/1.1 100 Continue\r\n\r\n')
            server_file.flush()
            self.assertEqual(b'update', server_file.read(6))
            transport.close()

        with self.assertRaises(FLRuntimeError) as raised:
            result.join()
        self.assertIn(raised.exception.error_code, (
            'invalid_http_response', 'update_submit_failed'))
        self.assertFalse(result.is_alive())

    def test_config_builds_role_and_exact_link_command(self):
        config_path = os.path.join(self.root, 'server.json')
        with open(config_path, 'w', encoding='utf-8') as fh:
            json.dump({
                'schema_version': 1,
                'role': 'server',
                'work_dir': os.path.join(self.root, 'work'),
                'node_id': 10,
                'participant_node_ids': [1, 2],
                'participant_uftp_uids': [101, 102],
                'server_uftp_uid': 100,
                'uftp_port': 9000,
                'http_host': '10.0.0.1',
                'http_port': 8080,
                'max_update_size_bytes': 4096,
                'link_args': [
                    '--tun-name', 'wfb0', '--tun-addr', '10.0.0.1/24',
                    '--known-clients', '1,2'],
            }, fh)

        with mock.patch('wfb_ng.fl.service.shutil.which',
                        side_effect=lambda name: '/usr/bin/' + name):
            service = load_role_service(config_path, expected_role='server')

        self.addCleanup(service.close)
        self.assertIsInstance(service.role, ServerRole)
        self.assertEqual([
            '/usr/bin/wfb_v6_uplink', '--role', 'server', '--node-id', '10',
            '--tun-name', 'wfb0', '--tun-addr', '10.0.0.1/24',
            '--known-clients', '1,2'],
            service.link.command)

    def test_config_passes_uftp_bind_and_multicast_hosts(self):
        config_path = self.write_server_config(
            participant_node_ids=[1, 2],
            link_args=['--tun-name', 'wfb0', '--known-clients', '1,2'])
        with open(config_path, 'r', encoding='utf-8') as fh:
            config = json.load(fh)
        config['uftp_bind_host'] = '10.80.0.1'
        config['uftp_multicast_host'] = '239.80.41.1'
        with open(config_path, 'w', encoding='utf-8') as fh:
            json.dump(config, fh)

        with mock.patch('wfb_ng.fl.service.shutil.which',
                        side_effect=lambda name: '/usr/bin/' + name):
            service = load_role_service(config_path, expected_role='server')

        self.addCleanup(service.close)
        self.assertEqual('10.80.0.1', service.role.transport.uftp_bind_host)
        self.assertEqual('239.80.41.1', service.role.transport.uftp_multicast_host)

    def test_client_config_defaults_uftp_bind_host_for_compatibility(self):
        config_path = os.path.join(self.root, 'client-default-bind.json')
        with open(config_path, 'w', encoding='utf-8') as fh:
            json.dump({
                'schema_version': 1,
                'role': 'client',
                'work_dir': os.path.join(self.root, 'work'),
                'node_id': 1,
                'uftp_uid': 1,
                'uftp_port': 9000,
                'server_http_host': '10.0.0.1',
                'server_http_port': 8080,
                'max_update_size_bytes': 4096,
                'link_args': ['--tun-name', 'wfb0', '--tun-addr', '10.0.0.2/24'],
            }, fh)

        with mock.patch('wfb_ng.fl.service.shutil.which',
                        side_effect=lambda name: '/usr/bin/' + name):
            service = load_role_service(config_path, expected_role='client')

        self.addCleanup(service.close)
        self.assertEqual('127.0.0.1', service.role.transport.uftp_bind_host)

    def test_algorithm_entry_runs_after_service_ready(self):
        events = []
        algorithm_config = {'rounds': 1}

        def algorithm(runtime, config):
            events.append(('algorithm', runtime, dict(config)))

        with mock.patch('wfb_ng.fl.service._load_algorithm', return_value=algorithm), \
                mock.patch('wfb_ng.fl.service._read_algorithm_config',
                           return_value=algorithm_config), \
                mock.patch('wfb_ng.fl.service.load_role_service',
                           return_value=StubService(events)), \
                mock.patch('wfb_ng.fl.service._notify_ready',
                           side_effect=lambda: events.append(('ready',))):
            from wfb_ng.fl import service
            with mock.patch('sys.argv', [
                    'wfb-fl-server', '--config', 'ignored', '--algorithm',
                    'fixture:main', '--algorithm-config', 'job.json']):
                self.assertEqual(0, service.main('server'))

        self.assertEqual('service.start', events[0])
        self.assertEqual(('ready',), events[1])
        self.assertEqual('algorithm', events[2][0])
        self.assertEqual(algorithm_config, events[2][2])
        self.assertEqual('service.close', events[-1])

    def test_algorithm_failure_exits_nonzero_and_closes_service(self):
        events = []

        def algorithm(runtime, config):
            raise FLRuntimeError('fixture_failed', 'fixture failed')

        with mock.patch('wfb_ng.fl.service._load_algorithm', return_value=algorithm), \
                mock.patch('wfb_ng.fl.service._read_algorithm_config', return_value={}), \
                mock.patch('wfb_ng.fl.service.load_role_service',
                           return_value=StubService(events)), \
                mock.patch('wfb_ng.fl.service._notify_ready'), \
                mock.patch('sys.argv', [
                    'wfb-fl-server', '--config', 'ignored', '--algorithm',
                    'fixture:main']):
            from wfb_ng.fl import service
            self.assertEqual(1, service.main('server'))

        self.assertEqual('service.close', events[-1])

    def test_server_config_rejects_participant_outside_known_clients(self):
        config_path = self.write_server_config(
            participant_node_ids=[1, 2],
            link_args=['--tun-name', 'wfb0', '--known-clients', '1,3'])

        with mock.patch('wfb_ng.fl.service.shutil.which',
                        side_effect=lambda name: '/usr/bin/' + name):
            with self.assertRaises(FLRuntimeError) as raised:
                load_role_service(config_path, expected_role='server')

        self.assertEqual('invalid_configuration', raised.exception.error_code)
        self.assertFalse(os.path.exists(os.path.join(self.root, 'work')))

    def test_server_config_rejects_missing_or_invalid_known_clients(self):
        invalid_arguments = (
            ['--tun-name', 'wfb0'],
            ['--tun-name', 'wfb0', '--known-clients', '1,1'],
            ['--tun-name', 'wfb0', '--known-clients', '0'],
            ['--tun-name', 'wfb0', '--known-clients', '1,,2'],
        )
        for index, link_args in enumerate(invalid_arguments):
            config_path = self.write_server_config(
                participant_node_ids=[1], link_args=link_args,
                name='invalid-known-clients-%d.json' % index)
            with self.subTest(link_args=link_args), mock.patch(
                    'wfb_ng.fl.service.shutil.which',
                    side_effect=lambda name: '/usr/bin/' + name):
                with self.assertRaises(FLRuntimeError) as raised:
                    load_role_service(config_path, expected_role='server')
                self.assertEqual(
                    'invalid_configuration', raised.exception.error_code)

    def test_config_rejects_missing_runtime_dependencies(self):
        config_path = os.path.join(self.root, 'client.json')
        with open(config_path, 'w', encoding='utf-8') as fh:
            json.dump({
                'schema_version': 1,
                'role': 'client',
                'work_dir': os.path.join(self.root, 'work'),
                'node_id': 1,
                'uftp_uid': 1,
                'uftp_port': 9000,
                'server_http_host': '10.0.0.1',
                'server_http_port': 8080,
                'max_update_size_bytes': 4096,
                'link_args': ['--tun-name', 'wfb0', '--tun-addr', '10.0.0.2/24'],
            }, fh)

        def which(name):
            return None if name == 'uftpd' else '/usr/bin/' + name

        with mock.patch('wfb_ng.fl.service.shutil.which', side_effect=which):
            with self.assertRaises(FLRuntimeError) as raised:
                load_role_service(config_path, expected_role='client')

        self.assertEqual('transport_unavailable', raised.exception.error_code)


    def test_service_observes_transport_failure(self):
        link = StubLink([])
        role = StubRole([])
        service = RoleService(role, link)
        service.start()
        role.transport_error = FLRuntimeError(
            'transport_failed', 'uftpd 意外退出')

        with self.assertRaises(FLRuntimeError) as raised:
            service.wait(0.01)

        self.assertEqual('transport_failed', raised.exception.error_code)
        self.assertFalse(service.ready)

    def test_server_start_wraps_http_bind_failure(self):
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        self.addCleanup(listener.close)
        transport = ServerTransport(
            (1,), 2, 9000, http_port=listener.getsockname()[1])
        with mock.patch('wfb_ng.fl.transport.shutil.which',
                        return_value='/usr/bin/uftp'):
            with self.assertRaises(FLRuntimeError) as raised:
                transport.start()

        self.assertEqual('transport_start_failed', raised.exception.error_code)
        self.assertFalse(transport.ready)

    def test_client_start_wraps_process_creation_failure(self):
        transport = ClientTransport(
            self.root, 1, 9000, ('127.0.0.1', 8080))
        with mock.patch('wfb_ng.fl.transport.shutil.which',
                        return_value='/usr/bin/uftpd'), mock.patch(
                            'wfb_ng.fl.transport.subprocess.Popen',
                            side_effect=OSError('cannot execute')):
            with self.assertRaises(FLRuntimeError) as raised:
                transport.start()

        self.assertEqual('transport_start_failed', raised.exception.error_code)
        self.assertFalse(transport.ready)

    def test_config_rejects_existing_tun_before_role_creation(self):
        config_path = os.path.join(self.root, 'server-existing-tun.json')
        with open(config_path, 'w', encoding='utf-8') as fh:
            json.dump({
                'schema_version': 1,
                'role': 'server',
                'work_dir': os.path.join(self.root, 'work'),
                'node_id': 10,
                'participant_node_ids': [1],
                'participant_uftp_uids': [101],
                'server_uftp_uid': 100,
                'uftp_port': 9000,
                'http_host': '10.0.0.1',
                'http_port': 8080,
                'max_update_size_bytes': 4096,
                'link_args': [
                    '--tun-name', 'wfb0', '--known-clients', '1'],
            }, fh)
        with mock.patch('wfb_ng.fl.service.shutil.which',
                        side_effect=lambda name: '/usr/bin/' + name), mock.patch(
                            'wfb_ng.fl.service.os.path.exists', return_value=True):
            with self.assertRaises(FLRuntimeError) as raised:
                load_role_service(config_path, expected_role='server')

        self.assertEqual('link_interface_exists', raised.exception.error_code)

    def test_role_entry_handles_sigterm_and_exits_cleanly(self):
        marker = os.path.join(self.root, 'closed')
        started = os.path.join(self.root, 'started')
        entry = os.path.join(self.root, 'entry.py')
        with open(entry, 'w', encoding='utf-8') as fh:
            fh.write(
                'import threading\n'
                'from wfb_ng.fl import service\n'
                'class Service:\n'
                '    def start(self):\n'
                '        with open(%r, "w") as output: output.write("started")\n'
                '    def wait(self, interval): threading.Event().wait(interval)\n'
                '    def close(self):\n'
                '        with open(%r, "w") as output: output.write("closed")\n'
                'service.load_role_service = lambda path, expected_role=None: Service()\n'
                'service._notify_ready = lambda: None\n'
                'raise SystemExit(service.main("server"))\n' % (started, marker))
        env = os.environ.copy()
        env['PYTHONPATH'] = os.path.abspath(os.path.join(
            os.path.dirname(__file__), '..', '..'))
        process = subprocess.Popen(
            [shutil.which('python3'), entry, '--config', 'ignored'], env=env)
        self.addCleanup(self.stop_process, process)
        deadline = time.monotonic() + 2
        while not os.path.isfile(started) and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(os.path.isfile(started))
        process.send_signal(signal.SIGTERM)

        self.assertEqual(0, process.wait(timeout=2))
        self.assertTrue(os.path.isfile(marker))

    def write_server_config(self, participant_node_ids, link_args,
                            name='server.json'):
        config_path = os.path.join(self.root, name)
        with open(config_path, 'w', encoding='utf-8') as fh:
            json.dump({
                'schema_version': 1,
                'role': 'server',
                'work_dir': os.path.join(self.root, 'work'),
                'node_id': 10,
                'participant_node_ids': participant_node_ids,
                'participant_uftp_uids': [100 + node_id
                                          for node_id in participant_node_ids],
                'server_uftp_uid': 100,
                'uftp_port': 9000,
                'http_host': '10.0.0.1',
                'http_port': 8080,
                'max_update_size_bytes': 4096,
                'link_args': link_args,
            }, fh)
        return config_path

    def stop_process(self, process):
        if process.poll() is None:
            process.kill()
            process.wait()


class LinkProcessTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='wfb-v8-link-process-')
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_close_gracefully_stops_and_reaps_child(self):
        pid_path = os.path.join(self.root, 'link.pid')
        term_path = os.path.join(self.root, 'link.term')
        executable = self.write_fixture(pid_path, term_path)
        link = LinkProcess([executable], startup_timeout=0.05,
                           stop_grace_period=0.5)
        link.start()
        pid = self.wait_for_pid(pid_path)
        link.close()

        self.assertTrue(os.path.isfile(term_path))
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def write_fixture(self, pid_path, term_path):
        path = os.path.join(self.root, 'link-fixture')
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(
                '#!/usr/bin/env python3\n'
                'import os\n'
                'import signal\n'
                'import time\n'
                'with open(%r, "w") as output:\n'
                '    output.write(str(os.getpid()))\n'
                'def stop(signum, frame):\n'
                '    with open(%r, "w") as output:\n'
                '        output.write("TERM")\n'
                '    raise SystemExit(0)\n'
                'signal.signal(signal.SIGTERM, stop)\n'
                'while True:\n'
                '    time.sleep(1)\n' % (pid_path, term_path))
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        return path

    def wait_for_pid(self, path):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                with open(path, 'r', encoding='ascii') as fh:
                    return int(fh.read())
            except (FileNotFoundError, ValueError):
                time.sleep(0.01)
        self.fail('link fixture did not start')


class DeploymentContractTestCase(unittest.TestCase):
    def setUp(self):
        self.root = os.path.abspath(os.path.join(
            os.path.dirname(__file__), '..', '..'))

    def test_units_keep_role_and_children_in_one_cgroup(self):
        for role in ('server', 'client'):
            path = os.path.join(
                self.root, 'scripts', 'systemd', 'wfb-fl-%s.service' % role)
            with self.subTest(role=role), open(path, 'r', encoding='utf-8') as fh:
                unit = fh.read()
            self.assertIn('Type=notify', unit)
            self.assertIn('KillMode=control-group', unit)
            self.assertIn('TimeoutStopSec=15s', unit)
            self.assertIn('Restart=on-failure', unit)
            self.assertIn(
                'ExecStart=/usr/bin/wfb-fl-%s --config /etc/wfb-ng/fl-%s.json' %
                (role, role), unit)
            self.assertNotIn('/bin/sh', unit)
            self.assertNotIn('/bin/bash', unit)

    def test_package_contains_role_services_and_runtime_dependencies(self):
        install_path = os.path.join(self.root, 'scripts', 'install-v8.sh')
        with open(install_path, 'r', encoding='utf-8') as fh:
            installer = fh.read()
        for value in (
                'require_command uftp',
                'require_command uftpd',
                'wfb_ng/fl/*.py',
                'wfb_v6_uplink',
                'scripts/wfb-fl-server',
                'scripts/wfb-fl-client',
                'scripts/v8-wfb-ng-init.py',
                'scripts/systemd/wfb-fl-server.service',
                'scripts/systemd/wfb-fl-client.service',
                'scripts/default/fl-server.json',
                'scripts/default/fl-client.json'):
            self.assertIn(value, installer)
        self.assertTrue(os.access(install_path, os.X_OK))

        with open(os.path.join(self.root, 'Makefile'), 'r', encoding='utf-8') as fh:
            makefile = fh.read()
        self.assertIn('install_v8: build_v6', makefile)
        self.assertIn('./scripts/install-v8.sh', makefile)
        self.assertIn('test_v8_systemd:', makefile)


class ThreadResult(object):
    def __init__(self, call):
        self.call = call
        self.error = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def _run(self):
        try:
            self.call()
        except BaseException as exc:
            self.error = exc

    def is_alive(self):
        return self.thread.is_alive()

    def join(self, timeout=2):
        self.thread.join(timeout)
        if self.thread.is_alive():
            raise AssertionError('operation did not finish')
        if self.error is not None:
            raise self.error


class StubService(object):
    def __init__(self, events):
        self.events = events
        self.runtime = object()

    def start(self):
        self.events.append('service.start')
        return self.runtime

    def wait(self, interval):
        self.events.append('service.wait')
        time.sleep(interval)

    def close(self):
        self.events.append('service.close')


class StubLink(object):
    def __init__(self, events):
        self.events = events
        self.return_code = None

    def start(self):
        self.events.append('link.start')

    def poll(self):
        return self.return_code

    def close(self):
        self.events.append('link.close')


class StubRole(object):
    def __init__(self, events, start_error=None):
        self.events = events
        self.start_error = start_error
        self.transport_error = None
        self.runtime = object()

    def poll_failure(self):
        return self.transport_error

    def start(self):
        self.events.append('role.start')
        if self.start_error is not None:
            raise self.start_error
        return self.runtime

    def close_transport(self):
        self.events.append('role.close_transport')

    def close_runtime(self):
        self.events.append('role.close_runtime')


if __name__ == '__main__':
    unittest.main()
