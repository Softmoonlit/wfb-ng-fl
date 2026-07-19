#!/usr/bin/env python
# -*- coding: utf-8 -*-

import hashlib
import json
import multiprocessing
import os
import shutil
import tempfile
import unittest
import uuid

from wfb_ng.fl import ClientRuntime, FLRuntimeError, ServerRuntime


class ReadyServerTransport(object):
    ready = True

    def install_round(self, round_id, participant_node_ids, round_dir,
                      max_update_size_bytes, failure_callback):
        self.round_id = round_id
        self.round_dir = round_dir

    def start_downlink(self, round_id, model_path, manifest_path):
        self.publish_model(round_id, model_path, manifest_path)
        return self

    def wait_downlink(self, operation):
        if operation is not self:
            raise RuntimeError('unexpected operation handle')

    def publish_model(self, round_id, model_path, manifest_path):
        update = b'update'
        update_dir = os.path.join(self.round_dir, 'updates', '1')
        os.makedirs(update_dir)
        with open(os.path.join(update_dir, 'update.bin'), 'wb') as fh:
            fh.write(update)
        with open(os.path.join(update_dir, 'update.manifest.json'), 'w',
                  encoding='utf-8') as fh:
            json.dump({
                'schema_version': 1,
                'artifact_type': 'update',
                'round_id': round_id,
                'node_id': 1,
                'size_bytes': len(update),
                'sha256': hashlib.sha256(update).hexdigest(),
            }, fh)

    def wait_for_update(self, timeout):
        return


class ReadyClientTransport(object):
    ready = True


class CandidateClientTransport(object):
    ready = True

    def __init__(self, candidate):
        self.candidate = candidate
        self.submit_count = 0

    def wait_for_model_candidate(self):
        return self.candidate

    def submit_update(self, round_id, node_id, update_path, size_bytes, digest):
        self.submit_count += 1


class BlockingServerTransport(ReadyServerTransport):
    def __init__(self, entered, release):
        self.entered = entered
        self.release = release

    def publish_model(self, round_id, model_path, manifest_path):
        self.entered.set()
        self.release.wait()


class BlockingClientTransport(CandidateClientTransport):
    def __init__(self, candidate, entered, release):
        super().__init__(candidate)
        self.entered = entered
        self.release = release

    def submit_update(self, round_id, node_id, update_path, size_bytes, digest):
        self.entered.set()
        self.release.wait()


def hold_server_runtime(work_dir, ready, release):
    runtime = ServerRuntime(work_dir, 1, 1024, ReadyServerTransport())
    ready.set()
    release.wait()
    runtime.close()


def publish_until_stopped(work_dir, model_path, entered, release):
    runtime = ServerRuntime(
        work_dir, 1, 1024, BlockingServerTransport(entered, release))
    runtime.publish_model(model_path)


def submit_until_stopped(work_dir, candidate, update_path, entered, release):
    runtime = ClientRuntime(
        work_dir, 1, 1024,
        BlockingClientTransport(candidate, entered, release))
    runtime.wait_for_model()
    runtime.submit_update(update_path)


def recover_result_and_publish(work_dir, model_path, result_queue):
    transport = ReadyServerTransport()
    runtime = ServerRuntime(work_dir, 1, 1024, transport)
    try:
        result = runtime.wait_for_updates()
        with open(result[1], 'rb') as fh:
            update = fh.read()
        runtime.publish_model(model_path)
        result_queue.put((update, transport.round_id))
    finally:
        runtime.close()


class V8RuntimePersistenceTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='wfb-v8-persistence-')
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_work_dir_is_exclusive_until_runtime_closes(self):
        work_dir = os.path.join(self.root, 'server')
        first = ServerRuntime(work_dir, 1, 1024, ReadyServerTransport())
        self.addCleanup(first.close)

        with self.assertRaises(FLRuntimeError) as raised:
            ServerRuntime(work_dir, 1, 1024, ReadyServerTransport())
        self.assertEqual('work_dir_locked', raised.exception.error_code)

        first.close()
        replacement = ServerRuntime(work_dir, 1, 1024, ReadyServerTransport())
        replacement.close()

    def test_process_exit_releases_work_dir_lock(self):
        work_dir = os.path.join(self.root, 'process-lock')
        ready = multiprocessing.Event()
        release = multiprocessing.Event()
        process = multiprocessing.Process(
            target=hold_server_runtime, args=(work_dir, ready, release))
        process.start()
        self.addCleanup(self.stop_process, process)
        self.assertTrue(ready.wait(5))

        with self.assertRaises(FLRuntimeError) as raised:
            ServerRuntime(work_dir, 1, 1024, ReadyServerTransport())
        self.assertEqual('work_dir_locked', raised.exception.error_code)

        process.terminate()
        process.join(5)
        self.assertFalse(process.is_alive())
        replacement = ServerRuntime(work_dir, 1, 1024, ReadyServerTransport())
        replacement.close()

    def test_server_and_client_crashes_are_failed_by_replacement_process(self):
        server_dir = os.path.join(self.root, 'crashed-server')
        model_path = os.path.join(self.root, 'crash-model.input')
        update_path = os.path.join(self.root, 'crash-update.input')
        with open(model_path, 'wb') as fh:
            fh.write(b'model')
        with open(update_path, 'wb') as fh:
            fh.write(b'update')

        server_entered = multiprocessing.Event()
        server_release = multiprocessing.Event()
        server_process = multiprocessing.Process(
            target=publish_until_stopped,
            args=(server_dir, model_path, server_entered, server_release))
        server_process.start()
        self.addCleanup(self.stop_process, server_process)
        self.assertTrue(server_entered.wait(5))
        server_process.terminate()
        server_process.join(5)

        replacement_server = ServerRuntime(
            server_dir, 1, 1024, ReadyServerTransport())
        self.addCleanup(replacement_server.close)
        with self.assertRaises(FLRuntimeError) as server_error:
            replacement_server.wait_for_updates()
        self.assertEqual('runtime_restarted', server_error.exception.error_code)

        client_dir = os.path.join(self.root, 'crashed-client')
        candidate = self.make_model_candidate(str(uuid.uuid4()))
        client_entered = multiprocessing.Event()
        client_release = multiprocessing.Event()
        client_process = multiprocessing.Process(
            target=submit_until_stopped,
            args=(client_dir, candidate, update_path,
                  client_entered, client_release))
        client_process.start()
        self.addCleanup(self.stop_process, client_process)
        self.assertTrue(client_entered.wait(5))
        client_process.terminate()
        client_process.join(5)

        replacement_client = ClientRuntime(
            client_dir, 1, 1024, ReadyClientTransport())
        replacement_client.close()
        client_round_id = os.path.basename(candidate)
        with open(os.path.join(
                client_dir, 'rounds', client_round_id, 'round-state.json'),
                'r', encoding='utf-8') as fh:
            client_state = json.load(fh)
        self.assertEqual('runtime_restarted', client_state['error_code'])

    def test_restart_fails_all_nonterminal_rounds_and_preserves_diagnostics(self):
        cases = (
            ('server', 'publishing_model'),
            ('server', 'waiting_for_updates'),
            ('client', 'model_received'),
            ('client', 'submitting_update'),
        )
        for role, state in cases:
            with self.subTest(role=role, state=state):
                work_dir = os.path.join(self.root, '%s-%s' % (role, state))
                round_id = str(uuid.uuid4())
                round_dir = os.path.join(work_dir, 'rounds', round_id)
                os.makedirs(round_dir)
                state_path = os.path.join(round_dir, 'round-state.json')
                with open(state_path, 'w', encoding='utf-8') as fh:
                    json.dump({
                        'schema_version': 1,
                        'round_id': round_id,
                        'role': role,
                        'state': state,
                        'diagnostic_marker': 'preserved',
                    }, fh)

                if role == 'server':
                    runtime = ServerRuntime(
                        work_dir, 1, 1024, ReadyServerTransport())
                else:
                    runtime = ClientRuntime(
                        work_dir, 1, 1024, ReadyClientTransport())
                runtime.close()

                with open(state_path, 'r', encoding='utf-8') as fh:
                    recovered = json.load(fh)
                self.assertEqual('failed', recovered['state'])
                self.assertEqual('runtime_restarted', recovered['error_code'])
                self.assertEqual(state, recovered['restart_previous_state'])
                self.assertEqual('preserved', recovered['diagnostic_marker'])

    def test_server_success_result_is_rebuilt_once_after_restart(self):
        work_dir = os.path.join(self.root, 'successful-server')
        model_path = os.path.join(self.root, 'model.input')
        with open(model_path, 'wb') as fh:
            fh.write(b'model')

        first_transport = ReadyServerTransport()
        first = ServerRuntime(work_dir, 1, 1024, first_transport)
        first.publish_model(model_path)
        first_result = first.wait_for_updates()
        self.assertEqual(b'update', self.read_file(first_result[1]))
        first_round_id = first_transport.round_id
        first.close()

        result_queue = multiprocessing.Queue()
        process = multiprocessing.Process(
            target=recover_result_and_publish,
            args=(work_dir, model_path, result_queue))
        process.start()
        self.addCleanup(self.stop_process, process)
        process.join(5)
        self.assertEqual(0, process.exitcode)
        recovered_update, second_round_id = result_queue.get(timeout=1)
        self.assertEqual(b'update', recovered_update)

        parsed_round_id = uuid.UUID(second_round_id)
        self.assertEqual(4, parsed_round_id.version)
        self.assertEqual(str(parsed_round_id), second_round_id)
        self.assertNotEqual(first_round_id, second_round_id)

    def test_recovered_success_reports_removed_and_corrupted_artifacts(self):
        for damage, error_code in (
                ('removed', 'round_artifacts_removed'),
                ('corrupted', 'round_artifacts_corrupted')):
            with self.subTest(damage=damage):
                work_dir = os.path.join(self.root, 'artifact-%s' % damage)
                transport = ReadyServerTransport()
                runtime = ServerRuntime(work_dir, 1, 1024, transport)
                model_path = os.path.join(self.root, 'artifact-model.input')
                with open(model_path, 'wb') as fh:
                    fh.write(b'model')
                runtime.publish_model(model_path)
                runtime.wait_for_updates()
                round_id = transport.round_id
                runtime.close()

                update_path = os.path.join(
                    work_dir, 'rounds', round_id, 'updates', '1', 'update.bin')
                if damage == 'removed':
                    os.unlink(update_path)
                else:
                    with open(update_path, 'wb') as fh:
                        fh.write(b'tamper')

                recovered = ServerRuntime(
                    work_dir, 1, 1024, ReadyServerTransport())
                self.addCleanup(recovered.close)
                with self.assertRaises(FLRuntimeError) as raised:
                    recovered.wait_for_updates()
                self.assertEqual(error_code, raised.exception.error_code)

    def test_corrupted_round_state_fails_startup_with_stable_error(self):
        work_dir = os.path.join(self.root, 'corrupted-state')
        round_id = str(uuid.uuid4())
        round_dir = os.path.join(work_dir, 'rounds', round_id)
        os.makedirs(round_dir)
        with open(os.path.join(round_dir, 'round-state.json'), 'w',
                  encoding='utf-8') as fh:
            fh.write('{"schema_version":1,"schema_version":1}')

        with self.assertRaises(FLRuntimeError) as raised:
            ServerRuntime(work_dir, 1, 1024, ReadyServerTransport())
        self.assertEqual('round_state_corrupted', raised.exception.error_code)

    def test_active_round_rejects_downlink_diagnostics(self):
        work_dir = os.path.join(self.root, 'active-diagnostics')
        round_id = str(uuid.uuid4())
        round_dir = os.path.join(work_dir, 'rounds', round_id)
        os.makedirs(round_dir)
        self.write_json(os.path.join(round_dir, 'round-state.json'), {
            'schema_version': 1,
            'round_id': round_id,
            'role': 'server',
            'state': 'publishing_model',
            'downlink_diagnostics': {'cancel_result': 'cancelled'},
        })

        with self.assertRaises(FLRuntimeError) as raised:
            ServerRuntime(work_dir, 1, 1024, ReadyServerTransport())
        self.assertEqual('round_state_corrupted', raised.exception.error_code)

    def test_corrupted_downlink_diagnostics_fail_startup(self):
        cases = (None, {}, {
            'natural_result': None,
        }, {
            'cancel_result': None,
        }, {
            'cancel_error': None,
        }, {
            'cancel_result': 'unknown',
        }, {
            'cancel_result': 'cancelled',
            'cancel_error': {
                'exception_type': 'OSError',
                'message': 'cancel failed',
            },
        }, {
            'unknown': 'value',
        })
        for index, diagnostics in enumerate(cases):
            with self.subTest(diagnostics=diagnostics):
                work_dir = os.path.join(
                    self.root, 'corrupted-diagnostics-%d' % index)
                round_id = str(uuid.uuid4())
                round_dir = os.path.join(work_dir, 'rounds', round_id)
                os.makedirs(round_dir)
                self.write_json(os.path.join(
                    round_dir, 'round-state.json'), {
                        'schema_version': 1,
                        'round_id': round_id,
                        'role': 'server',
                        'state': 'failed',
                        'error_code': 'upload_incomplete',
                        'error_message': 'update body 接收失败',
                        'downlink_diagnostics': diagnostics,
                    })
                self.write_json(os.path.join(
                    work_dir, 'current-round.json'), {
                        'schema_version': 1,
                        'role': 'server',
                        'round_id': round_id,
                    })

                with self.assertRaises(FLRuntimeError) as raised:
                    ServerRuntime(work_dir, 1, 1024, ReadyServerTransport())
                self.assertEqual(
                    'round_state_corrupted', raised.exception.error_code)

    def test_noncurrent_terminal_state_is_strictly_validated(self):
        work_dir = os.path.join(self.root, 'noncurrent-corrupted-state')
        current_round_id = str(uuid.uuid4())
        old_round_id = str(uuid.uuid4())
        for round_id in (current_round_id, old_round_id):
            round_dir = os.path.join(work_dir, 'rounds', round_id)
            os.makedirs(round_dir)
            self.write_json(os.path.join(round_dir, 'round-state.json'), {
                'schema_version': 1,
                'round_id': round_id,
                'role': 'server',
                'state': 'failed',
                'error_code': 'runtime_restarted',
                'error_message': 'restarted',
            })
        old_state_path = os.path.join(
            work_dir, 'rounds', old_round_id, 'round-state.json')
        self.write_json(old_state_path, {
            'schema_version': 1,
            'round_id': old_round_id,
            'role': 'server',
            'state': 'succeeded',
        })
        self.write_json(os.path.join(work_dir, 'current-round.json'), {
            'schema_version': 1,
            'role': 'server',
            'round_id': current_round_id,
        })

        with self.assertRaises(FLRuntimeError) as raised:
            ServerRuntime(work_dir, 1, 1024, ReadyServerTransport())
        self.assertEqual('round_state_corrupted', raised.exception.error_code)

    def test_server_restart_failure_is_replayed_before_new_round(self):
        work_dir = os.path.join(self.root, 'restarted-server')
        round_id = str(uuid.uuid4())
        round_dir = os.path.join(work_dir, 'rounds', round_id)
        os.makedirs(round_dir)
        self.write_json(os.path.join(round_dir, 'round-state.json'), {
            'schema_version': 1,
            'round_id': round_id,
            'role': 'server',
            'state': 'waiting_for_updates',
        })
        self.write_json(os.path.join(work_dir, 'current-round.json'), {
            'schema_version': 1,
            'role': 'server',
            'round_id': round_id,
        })

        transport = ReadyServerTransport()
        runtime = ServerRuntime(work_dir, 1, 1024, transport)
        self.addCleanup(runtime.close)
        with self.assertRaises(FLRuntimeError) as raised:
            runtime.wait_for_updates()
        self.assertEqual('runtime_restarted', raised.exception.error_code)
        self.assertEqual(round_id, raised.exception.round_id)

        model_path = os.path.join(self.root, 'restarted-model.input')
        with open(model_path, 'wb') as fh:
            fh.write(b'model')
        runtime.publish_model(model_path)
        self.assertNotEqual(round_id, transport.round_id)

    def test_client_restart_does_not_resume_old_submission(self):
        work_dir = os.path.join(self.root, 'restarted-client')
        old_round_id = str(uuid.uuid4())
        old_round_dir = os.path.join(work_dir, 'rounds', old_round_id)
        os.makedirs(old_round_dir)
        old_state_path = os.path.join(old_round_dir, 'round-state.json')
        self.write_json(old_state_path, {
            'schema_version': 1,
            'round_id': old_round_id,
            'role': 'client',
            'state': 'submitting_update',
        })
        self.write_json(os.path.join(work_dir, 'current-round.json'), {
            'schema_version': 1,
            'role': 'client',
            'round_id': old_round_id,
        })
        new_round_id = str(uuid.uuid4())
        candidate = self.make_model_candidate(new_round_id)
        transport = CandidateClientTransport(candidate)

        runtime = ClientRuntime(work_dir, 1, 1024, transport)
        self.addCleanup(runtime.close)
        with self.assertRaises(FLRuntimeError) as pending:
            runtime.wait_for_model()
        self.assertEqual('round_result_pending', pending.exception.error_code)

        with self.assertRaises(FLRuntimeError) as restarted:
            runtime.submit_update(os.path.join(self.root, 'missing-update'))
        self.assertEqual('runtime_restarted', restarted.exception.error_code)
        self.assertEqual(old_round_id, restarted.exception.round_id)
        self.assertEqual(0, transport.submit_count)

        with self.assertRaises(FLRuntimeError) as consumed:
            runtime.submit_update(os.path.join(self.root, 'missing-update'))
        self.assertEqual('round_already_failed', consumed.exception.error_code)

        model_path = runtime.wait_for_model()
        self.assertIn(new_round_id, model_path)
        self.assertEqual(0, transport.submit_count)
        with open(old_state_path, 'r', encoding='utf-8') as fh:
            old_state = json.load(fh)
        self.assertEqual('runtime_restarted', old_state['error_code'])

    def test_client_success_result_is_replayed_before_new_round(self):
        work_dir = os.path.join(self.root, 'successful-client')
        first_round_id = str(uuid.uuid4())
        update_path = os.path.join(self.root, 'successful-update.input')
        with open(update_path, 'wb') as fh:
            fh.write(b'update')

        first_transport = CandidateClientTransport(
            self.make_model_candidate(first_round_id))
        first = ClientRuntime(work_dir, 1, 1024, first_transport)
        first.wait_for_model()
        first.submit_update(update_path)
        self.assertEqual(1, first_transport.submit_count)
        first.close()

        second_round_id = str(uuid.uuid4())
        second_transport = CandidateClientTransport(
            self.make_model_candidate(second_round_id))
        recovered = ClientRuntime(work_dir, 1, 1024, second_transport)
        self.addCleanup(recovered.close)
        with self.assertRaises(FLRuntimeError) as pending:
            recovered.wait_for_model()
        self.assertEqual('round_result_pending', pending.exception.error_code)

        self.assertIsNone(recovered.submit_update(
            os.path.join(self.root, 'missing-update')))
        self.assertEqual(0, second_transport.submit_count)
        with self.assertRaises(FLRuntimeError) as consumed:
            recovered.submit_update(os.path.join(self.root, 'missing-update'))
        self.assertEqual('round_already_succeeded', consumed.exception.error_code)

        model_path = recovered.wait_for_model()
        self.assertIn(second_round_id, model_path)
        self.assertNotEqual(first_round_id, second_round_id)

    def test_client_persisted_failure_replays_original_error(self):
        work_dir = os.path.join(self.root, 'failed-client')
        round_id = str(uuid.uuid4())
        round_dir = os.path.join(work_dir, 'rounds', round_id)
        os.makedirs(round_dir)
        self.write_json(os.path.join(round_dir, 'round-state.json'), {
            'schema_version': 1,
            'round_id': round_id,
            'role': 'client',
            'state': 'failed',
            'error_code': 'update_submit_failed',
            'error_message': '原始提交错误',
            'node_id': 1,
        })
        self.write_json(os.path.join(work_dir, 'current-round.json'), {
            'schema_version': 1,
            'role': 'client',
            'round_id': round_id,
        })

        runtime = ClientRuntime(work_dir, 1, 1024, ReadyClientTransport())
        self.addCleanup(runtime.close)
        with self.assertRaises(FLRuntimeError) as replayed:
            runtime.submit_update(os.path.join(self.root, 'missing-update'))
        self.assertEqual('update_submit_failed', replayed.exception.error_code)
        self.assertEqual('原始提交错误', replayed.exception.error_message)
        self.assertEqual(round_id, replayed.exception.round_id)
        self.assertEqual(1, replayed.exception.node_id)

        with self.assertRaises(FLRuntimeError) as consumed:
            runtime.submit_update(os.path.join(self.root, 'missing-update'))
        self.assertEqual('round_already_failed', consumed.exception.error_code)

    def make_model_candidate(self, round_id):
        candidate = os.path.join(self.root, 'candidate', round_id)
        os.makedirs(candidate)
        model = b'model'
        with open(os.path.join(candidate, 'model.bin'), 'wb') as fh:
            fh.write(model)
        self.write_json(os.path.join(candidate, 'model.manifest.json'), {
            'schema_version': 1,
            'artifact_type': 'model',
            'round_id': round_id,
            'size_bytes': len(model),
            'sha256': hashlib.sha256(model).hexdigest(),
            'participant_node_ids': [1],
        })
        return candidate

    def write_json(self, path, value):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(value, fh)

    def read_file(self, path):
        with open(path, 'rb') as fh:
            return fh.read()

    def stop_process(self, process):
        if process.is_alive():
            process.terminate()
        process.join(5)


if __name__ == '__main__':
    unittest.main()
