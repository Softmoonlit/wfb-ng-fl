#!/usr/bin/env python
# -*- coding: utf-8 -*-

import glob
import json
import os
import shutil
import stat
import tempfile
import threading
import time
import unittest
from unittest import mock

from wfb_ng.fl import FLRuntimeError, ServerRuntime
from wfb_ng.fl.transport import ServerTransport


class UFTPDownlinkOperationTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='wfb-v8-uftp-operation-')
        self.addCleanup(shutil.rmtree, self.root, True)
        self.executable = os.path.join(self.root, 'uftp-fixture')
        with open(self.executable, 'w', encoding='utf-8') as fh:
            fh.write(
                '#!/usr/bin/env python3\n'
                'import os\n'
                'import shutil\n'
                'import sys\n'
                'status_path = sys.argv[sys.argv.index("-S") + 1]\n'
                'shutil.copyfile(os.path.join(os.getcwd(), "fixture.status"), status_path)\n')
        os.chmod(self.executable, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

    def test_each_operation_uses_fresh_status_and_requires_complete_matrix(self):
        round_dir = os.path.join(self.root, 'round')
        os.makedirs(round_dir)
        model_path = self.write_file(round_dir, 'model.bin', b'model')
        manifest_path = self.write_file(
            round_dir, 'model.manifest.json', b'{}')
        transport = ServerTransport((1, 2), 3, 9000, '127.0.0.1', '230.4.4.1')

        self.write_status(round_dir, [
            'CONNECT;success;0x00000001',
            'CONNECT;success;0x00000002',
            'RESULT;0x00000001;round/model.bin;1;copy;0',
            'RESULT;0x00000001;round/model.manifest.json;1;copy;0',
            'RESULT;0x00000002;round/model.bin;1;copy;0',
            'RESULT;0x00000002;round/model.manifest.json;1;copy;0',
        ])
        with mock.patch('wfb_ng.fl.transport.shutil.which',
                        return_value=self.executable), mock.patch(
                            'wfb_ng.fl.transport.subprocess.Popen',
                            wraps=__import__('subprocess').Popen) as popen:
            first = transport.start_downlink('round', model_path, manifest_path)
            self.assertIsNone(transport.wait_downlink(first))
            command = popen.call_args.args[0]
            self.assertEqual('127.0.0.1', command[command.index('-I') + 1])
            self.assertEqual('230.4.4.1', command[command.index('-M') + 1])

            self.write_status(round_dir, [
                'CONNECT;success;0x00000001',
                'CONNECT;success;0x00000002',
                'RESULT;0x00000001;round/model.bin;1;copy;0',
                'RESULT;0x00000001;round/model.manifest.json;1;copy;0',
                'RESULT;0x00000002;round/model.bin;1;copy;0',
            ])
            second = transport.start_downlink('round', model_path, manifest_path)
            with self.assertRaises(FLRuntimeError) as raised:
                transport.wait_downlink(second)

        self.assertEqual('transport_failed', raised.exception.error_code)
        status_paths = glob.glob(os.path.join(round_dir, 'uftp-*.status'))
        self.assertEqual(2, len(status_paths))
        self.assertNotEqual(status_paths[0], status_paths[1])

    def test_rejects_invalid_status_matrix_and_nonzero_exit(self):
        success = self.success_status()
        cases = {
            'missing_connect': success[:1] + success[2:],
            'duplicate_connect': success + [success[0]],
            'rejected_connect': self.replace_line(
                success, 1, 'CONNECT;rejected;0x00000002'),
            'unknown_connect_client': success + [
                'CONNECT;success;0x00000009'],
            'unknown_result_client': self.replace_line(
                success, 2,
                'RESULT;0x00000009;round/model.bin;1;copy;0'),
            'missing_result': success[:-1],
            'duplicate_result': success + [success[2]],
            'unknown_file': self.replace_line(
                success, 2,
                'RESULT;0x00000001;round/other.bin;1;copy;0'),
            'skipped': self.replace_line(
                success, 2,
                'RESULT;0x00000001;round/model.bin;1;skipped;0'),
            'overwrite': self.replace_line(
                success, 2,
                'RESULT;0x00000001;round/model.bin;1;overwrite;0'),
            'other_non_copy': self.replace_line(
                success, 2,
                'RESULT;0x00000001;round/model.bin;1;failed;0'),
            'malformed_connect': self.replace_line(
                success, 0, 'CONNECT;success;invalid'),
            'malformed_result': self.replace_line(
                success, 2, 'RESULT;0x00000001;broken'),
        }
        for name, lines in cases.items():
            with self.subTest(name=name):
                self.assert_operation_failed(lines)

        self.assert_operation_failed(success, exit_code=7)

    def test_existing_operation_status_path_is_rejected_before_start(self):
        transport, model_path, manifest_path = self.make_transport()
        status_path = os.path.join(os.path.dirname(model_path), 'reserved.status')
        self.write_file(os.path.dirname(model_path), 'reserved.status', b'old')
        executable = self.write_status_fixture(0)

        with mock.patch('wfb_ng.fl.transport.shutil.which',
                        return_value=executable), mock.patch(
                            'wfb_ng.fl.transport._new_uftp_status_path',
                            return_value=status_path):
            with self.assertRaises(FLRuntimeError) as raised:
                transport.start_downlink('round', model_path, manifest_path)

        self.assertEqual('transport_failed', raised.exception.error_code)

    def test_cancel_reports_already_completed_after_natural_completion(self):
        transport, model_path, manifest_path = self.make_transport()
        round_dir = os.path.dirname(model_path)
        self.write_status(round_dir, self.success_status())
        executable = self.write_status_fixture(0)

        with mock.patch('wfb_ng.fl.transport.shutil.which',
                        return_value=executable):
            operation = transport.start_downlink(
                'round', model_path, manifest_path)
            self.assertIsNone(transport.wait_downlink(operation))
            self.assertEqual(
                'already_completed', transport.cancel_downlink(operation))

        with self.assertRaises(RuntimeError):
            transport.cancel_downlink(object())

    def test_cancel_waits_for_natural_result_already_being_decided(self):
        transport, model_path, manifest_path = self.make_transport()
        round_dir = os.path.dirname(model_path)
        self.write_status(round_dir, self.success_status())
        executable = self.write_status_fixture(0)
        parsing = threading.Event()
        exit_observed = threading.Event()
        release = threading.Event()

        def validate(*args):
            parsing.set()
            release.wait(2)

        with mock.patch('wfb_ng.fl.transport.shutil.which',
                        return_value=executable), mock.patch(
                            'wfb_ng.fl.transport._validate_uftp_status',
                            side_effect=validate):
            operation = transport.start_downlink(
                'round', model_path, manifest_path)
            self.assertTrue(parsing.wait(2))
            process = transport._downlink_operation.process
            original_poll = process.poll

            def observe_poll():
                return_code = original_poll()
                if return_code is not None:
                    exit_observed.set()
                return return_code

            with mock.patch.object(process, 'poll', side_effect=observe_poll):
                cancelling = ThreadResult(
                    lambda: transport.cancel_downlink(operation))
                cancelling.start()
                self.assertTrue(exit_observed.wait(2))
                self.assertTrue(cancelling.is_alive())
                release.set()
                self.assertEqual('already_completed', cancelling.join())

        self.assertIsNone(transport.wait_downlink(operation))

    def test_runtime_reaps_real_process_before_preserving_primary_error(self):
        pid_path = os.path.join(self.root, 'runtime.pid')
        executable = self.write_blocking_fixture(
            pid_path, os.path.join(self.root, 'runtime.term'), False)
        transport = ServerTransport((1,), 2, 9000, '127.0.0.1', '230.4.4.1', cancel_grace_period=0.5)
        transport.ready = True
        runtime_root = os.path.join(self.root, 'runtime')
        runtime = ServerRuntime(runtime_root, (1,), 1024, transport)
        self.addCleanup(runtime.close)
        model_path = self.write_file(self.root, 'runtime-model.bin', b'model')

        with mock.patch('wfb_ng.fl.transport.shutil.which',
                        return_value=executable):
            publishing = ThreadResult(lambda: runtime.publish_model(model_path))
            publishing.start()
            pid = self.wait_for_pid(pid_path)
            round_id = self.wait_for_round_id(runtime_root)
            runtime.report_update_failure(
                round_id, 1, 'upload_incomplete', 'update body 接收失败')
            self.assert_process_reaped(pid)
            with self.assertRaises(FLRuntimeError) as raised:
                publishing.join()

        self.assertEqual('upload_incomplete', raised.exception.error_code)
        state_path = os.path.join(
            runtime_root, 'rounds', round_id, 'round-state.json')
        with open(state_path, 'r', encoding='utf-8') as fh:
            state = json.load(fh)
        self.assertEqual('upload_incomplete', state['error_code'])
        self.assertEqual(1, state['node_id'])
        self.assertEqual(
            {'cancel_result': 'cancelled'}, state['downlink_diagnostics'])

    def test_runtime_reaps_process_when_failure_persistence_is_fatal(self):
        pid_path = os.path.join(self.root, 'fatal-runtime.pid')
        executable = self.write_blocking_fixture(
            pid_path, os.path.join(self.root, 'fatal-runtime.term'), False)
        transport = ServerTransport((1,), 2, 9000, '127.0.0.1', '230.4.4.1', cancel_grace_period=0.5)
        transport.ready = True
        runtime_root = os.path.join(self.root, 'fatal-runtime')
        runtime = ServerRuntime(runtime_root, (1,), 1024, transport)
        self.addCleanup(runtime.close)
        model_path = self.write_file(self.root, 'fatal-runtime-model.bin', b'model')
        original_write_state = runtime._write_state

        def fail_failed_state(state, **extra):
            if state == 'failed':
                runtime._fatal_error = FLRuntimeError(
                    'state_persistence_failed', 'server 轮次状态持久化失败',
                    round_id=runtime._round_id)
                raise runtime._fatal_error
            return original_write_state(state, **extra)

        with mock.patch('wfb_ng.fl.transport.shutil.which',
                        return_value=executable), mock.patch.object(
                            runtime, '_write_state', side_effect=fail_failed_state):
            publishing = ThreadResult(lambda: runtime.publish_model(model_path))
            publishing.start()
            pid = self.wait_for_pid(pid_path)
            round_id = self.wait_for_round_id(runtime_root)
            with self.assertRaises(FLRuntimeError) as callback_error:
                runtime.report_update_failure(
                    round_id, 1, 'upload_incomplete', 'update body 接收失败')
            self.assert_process_reaped(pid)
            with self.assertRaises(FLRuntimeError) as publish_error:
                publishing.join()

        self.assertEqual(
            'state_persistence_failed', callback_error.exception.error_code)
        self.assertEqual(
            'state_persistence_failed', publish_error.exception.error_code)

    def test_cancel_gracefully_stops_and_reaps_process(self):
        pid_path = os.path.join(self.root, 'graceful.pid')
        term_path = os.path.join(self.root, 'graceful.term')
        executable = self.write_blocking_fixture(pid_path, term_path, False)
        transport, model_path, manifest_path = self.make_transport(
            cancel_grace_period=0.5)

        with mock.patch('wfb_ng.fl.transport.shutil.which',
                        return_value=executable):
            operation = transport.start_downlink(
                'round', model_path, manifest_path)
            pid = self.wait_for_pid(pid_path)
            self.assertEqual('cancelled', transport.cancel_downlink(operation))

        self.assertTrue(os.path.isfile(term_path))
        self.assert_process_reaped(pid)

    def test_cancel_force_stops_and_reaps_process_after_grace_period(self):
        pid_path = os.path.join(self.root, 'forced.pid')
        executable = self.write_blocking_fixture(
            pid_path, os.path.join(self.root, 'forced.term'), True)
        transport, model_path, manifest_path = self.make_transport(
            cancel_grace_period=0.05)

        with mock.patch('wfb_ng.fl.transport.shutil.which',
                        return_value=executable):
            operation = transport.start_downlink(
                'round', model_path, manifest_path)
            pid = self.wait_for_pid(pid_path)
            self.assertEqual('cancelled', transport.cancel_downlink(operation))

        self.assert_process_reaped(pid)

    def success_status(self):
        return [
            'CONNECT;success;0x00000001',
            'CONNECT;success;0x00000002',
            'RESULT;0x00000001;round/model.bin;1;copy;0',
            'RESULT;0x00000001;round/model.manifest.json;1;copy;0',
            'RESULT;0x00000002;round/model.bin;1;copy;0',
            'RESULT;0x00000002;round/model.manifest.json;1;copy;0',
        ]

    def replace_line(self, lines, index, value):
        replaced = list(lines)
        replaced[index] = value
        return replaced

    def assert_operation_failed(self, lines, exit_code=0):
        transport, model_path, manifest_path = self.make_transport()
        round_dir = os.path.dirname(model_path)
        self.write_status(round_dir, lines)
        executable = self.write_status_fixture(exit_code)
        with mock.patch('wfb_ng.fl.transport.shutil.which',
                        return_value=executable):
            operation = transport.start_downlink(
                'round', model_path, manifest_path)
            with self.assertRaises(FLRuntimeError) as raised:
                transport.wait_downlink(operation)
        self.assertEqual('transport_failed', raised.exception.error_code)

    def make_transport(self, **kwargs):
        round_dir = tempfile.mkdtemp(prefix='round-', dir=self.root)
        model_path = self.write_file(round_dir, 'model.bin', b'model')
        manifest_path = self.write_file(
            round_dir, 'model.manifest.json', b'{}')
        return ServerTransport((1, 2), 3, 9000, '127.0.0.1', '230.4.4.1', **kwargs), model_path, manifest_path

    def write_status_fixture(self, exit_code):
        path = os.path.join(self.root, 'uftp-fixture-%d' % exit_code)
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(
                '#!/usr/bin/env python3\n'
                'import os\n'
                'import shutil\n'
                'import sys\n'
                'status_path = sys.argv[sys.argv.index("-S") + 1]\n'
                'shutil.copyfile(os.path.join(os.getcwd(), "fixture.status"), status_path)\n'
                'sys.exit(%d)\n' % exit_code)
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        return path

    def write_blocking_fixture(self, pid_path, term_path, ignore_term):
        path = os.path.join(self.root, 'uftp-blocking-%s' % ignore_term)
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
                'signal.signal(signal.SIGTERM, %s)\n'
                'while True:\n'
                '    time.sleep(1)\n' % (
                    pid_path, term_path,
                    'signal.SIG_IGN' if ignore_term else 'stop'))
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
        self.fail('fixture process did not start')

    def wait_for_round_id(self, runtime_root):
        rounds_dir = os.path.join(runtime_root, 'rounds')
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                names = os.listdir(rounds_dir)
            except FileNotFoundError:
                names = []
            if names:
                return names[0]
            time.sleep(0.01)
        self.fail('runtime round did not start')

    def assert_process_reaped(self, pid):
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def write_file(self, directory, name, content):
        path = os.path.join(directory, name)
        with open(path, 'wb') as fh:
            fh.write(content)
        return path

    def write_status(self, directory, lines):
        with open(os.path.join(directory, 'fixture.status'), 'w',
                  encoding='utf-8') as fh:
            fh.write('\n'.join(lines) + '\n')


class ThreadResult(object):
    def __init__(self, call):
        self.call = call
        self.value = None
        self.error = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def _run(self):
        try:
            self.value = self.call()
        except BaseException as exc:
            self.error = exc

    def is_alive(self):
        return self.thread.is_alive()

    def join(self, timeout=5):
        self.thread.join(timeout)
        if self.thread.is_alive():
            raise AssertionError('operation did not finish')
        if self.error is not None:
            raise self.error
        return self.value


if __name__ == '__main__':
    unittest.main()
