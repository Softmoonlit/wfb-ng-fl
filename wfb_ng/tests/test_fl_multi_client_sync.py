#!/usr/bin/env python
# -*- coding: utf-8 -*-

import io
import json
import os
import shutil
import tempfile
import threading
import time
import unittest

from types import SimpleNamespace

from wfb_ng.fl import ClientRuntime, FLRuntimeError, ServerRuntime
from wfb_ng.fl.transport import (
    ClientTransport,
    ServerTransport,
    _validate_uftp_status,
)


class V8MultiClientSyncTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='wfb-v8-multi-')
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_early_updates_wait_for_shared_downlink_and_return_complete_ordered_map(self):
        transport = SharedRoundTransport(os.path.join(self.root, 'inbox'))
        server = ServerRuntime(
            os.path.join(self.root, 'server'), (9, 2), 1024, transport)
        clients = {
            node_id: ClientRuntime(
                os.path.join(self.root, 'client-%d' % node_id), node_id, 1024,
                transport.client(node_id))
            for node_id in (9, 2)
        }
        model_path = self.write_file('model.input', b'model')
        updates = {
            9: self.write_file('update-9.input', b'update-9'),
            2: self.write_file('update-2.input', b'update-2'),
        }

        publishing = ThreadResult(lambda: server.publish_model(model_path))
        publishing.start()
        transport.wait_until_publishing()
        waiting = ThreadResult(server.wait_for_updates)
        waiting.start()

        clients[9].wait_for_model()
        clients[9].submit_update(updates[9])
        clients[2].wait_for_model()
        clients[2].submit_update(updates[2])

        time.sleep(0.05)
        self.assertTrue(waiting.is_alive())
        transport.complete_downlink()

        self.assertIsNone(publishing.join())
        result = waiting.join()
        self.assertEqual([2, 9], list(result))
        self.assertEqual(b'update-2', self.read_file(result[2]))
        self.assertEqual(b'update-9', self.read_file(result[9]))

    def test_early_complete_round_blocks_new_round_until_result_is_consumed(self):
        server, clients, transport = self.make_round((1, 2))
        publishing = ThreadResult(lambda: server.publish_model(
            self.write_file('pending-model.input', b'model')))
        publishing.start()
        transport.wait_until_publishing()

        for node_id in (1, 2):
            clients[node_id].wait_for_model()
            clients[node_id].submit_update(
                self.write_file('pending-update-%d.input' % node_id,
                                ('update-%d' % node_id).encode('ascii')))
        transport.complete_downlink()
        self.assertIsNone(publishing.join())

        with self.assertRaises(FLRuntimeError) as pending:
            server.publish_model(self.write_file('blocked-model.input', b'blocked'))
        self.assertEqual('round_result_pending', pending.exception.error_code)
        self.assertEqual([1, 2], list(server.wait_for_updates()))

    def test_server_round_gates_distinguish_reentry_active_and_pending_result(self):
        server, clients, transport = self.make_round((1, 2))
        first_model = self.write_file('first-model.input', b'first')
        publishing = ThreadResult(lambda: server.publish_model(first_model))
        publishing.start()
        transport.wait_until_publishing()

        with self.assertRaises(FLRuntimeError) as reentry:
            server.publish_model(self.write_file('reentry-model.input', b'reentry'))
        self.assertEqual('operation_in_progress', reentry.exception.error_code)

        transport.complete_downlink()
        self.assertIsNone(publishing.join())
        with self.assertRaises(FLRuntimeError) as active:
            server.publish_model(self.write_file('active-model.input', b'active'))
        self.assertEqual('round_in_progress', active.exception.error_code)

        for node_id in (1, 2):
            clients[node_id].wait_for_model()
            clients[node_id].submit_update(
                self.write_file('gate-update-%d.input' % node_id,
                                ('update-%d' % node_id).encode('ascii')))
        self.assertEqual([1, 2], list(server.wait_for_updates()))

        second_model = self.write_file('second-model.input', b'second')
        self.assertIsNone(server.publish_model(second_model))

    def test_missing_update_blocks_and_concurrent_wait_is_rejected(self):
        server, clients, transport = self.make_round((1, 2))
        model_path = self.write_file('missing-model.input', b'model')
        publishing = ThreadResult(lambda: server.publish_model(model_path))
        publishing.start()
        transport.wait_until_publishing()
        waiting = ThreadResult(server.wait_for_updates)
        waiting.start()

        clients[1].wait_for_model()
        clients[1].submit_update(self.write_file('only-update.input', b'only'))
        transport.complete_downlink()
        self.assertIsNone(publishing.join())

        time.sleep(0.05)
        self.assertTrue(waiting.is_alive())
        with self.assertRaises(FLRuntimeError) as raised:
            server.wait_for_updates()
        self.assertEqual('operation_in_progress', raised.exception.error_code)

        clients[2].wait_for_model()
        clients[2].submit_update(self.write_file('last-update.input', b'last'))
        self.assertEqual([1, 2], list(waiting.join()))

    def test_cancel_failure_does_not_replace_participant_submit_failure(self):
        server, _, transport = self.make_round((1, 2))
        transport.cancel_error = OSError('cancel failed')
        publishing = ThreadResult(lambda: server.publish_model(
            self.write_file('cancel-error-model.input', b'model')))
        publishing.start()
        transport.wait_until_publishing()

        transport.fail_upload(2, 'upload_incomplete', '节点 2 提交失败')

        with self.assertRaises(FLRuntimeError) as raised:
            publishing.join()
        self.assertEqual('upload_incomplete', raised.exception.error_code)
        with self.assertRaises(FLRuntimeError) as waiting:
            server.wait_for_updates()
        self.assertEqual('upload_incomplete', waiting.exception.error_code)
        state_path = os.path.join(
            server._round_dir, 'round-state.json')
        with open(state_path, 'r', encoding='utf-8') as fh:
            state = json.load(fh)
        self.assertEqual('upload_incomplete', state['error_code'])
        self.assertEqual(2, state['node_id'])
        self.assertEqual({
            'cancel_error': {
                'exception_type': 'OSError',
                'message': 'cancel failed',
            },
            'natural_result': 'succeeded',
        }, state['downlink_diagnostics'])

    def test_participant_submit_failure_cancels_active_downlink(self):
        server, _, transport = self.make_round((1, 2))
        publishing = ThreadResult(lambda: server.publish_model(
            self.write_file('cancel-model.input', b'model')))
        publishing.start()
        transport.wait_until_publishing()

        transport.fail_upload(2, 'upload_incomplete', '节点 2 提交失败')

        with self.assertRaises(FLRuntimeError) as raised:
            publishing.join()
        self.assertEqual('upload_incomplete', raised.exception.error_code)
        self.assertEqual(1, transport.cancel_count)
        self.assertIs(transport.operation_handle, transport.cancelled_handle)
        self.assertTrue(transport.cancel_completed)

    def test_participant_submit_failure_fails_round_without_partial_result(self):
        server, clients, transport = self.make_round((1, 2))
        model_path = self.write_file('failed-model.input', b'model')
        publishing = ThreadResult(lambda: server.publish_model(model_path))
        publishing.start()
        transport.wait_until_publishing()
        waiting = ThreadResult(server.wait_for_updates)
        waiting.start()

        clients[1].wait_for_model()
        clients[1].submit_update(self.write_file('partial-update.input', b'partial'))
        transport.fail_upload(2, 'upload_incomplete', '节点 2 提交失败')

        with self.assertRaises(FLRuntimeError) as raised:
            waiting.join()
        self.assertEqual('upload_incomplete', raised.exception.error_code)
        self.assertEqual(2, raised.exception.node_id)
        transport.complete_downlink()
        with self.assertRaises(FLRuntimeError) as publish_raised:
            publishing.join()
        self.assertEqual('upload_incomplete', publish_raised.exception.error_code)

    def test_client_rejects_concurrent_model_wait_and_repeated_submit(self):
        round_id = '550e8400-e29b-41d4-a716-446655440000'
        candidate = self.make_candidate(round_id, (1, 2))
        transport = BlockingClientTransport(candidate)
        client = ClientRuntime(
            os.path.join(self.root, 'gated-client'), 1, 1024, transport)

        waiting = ThreadResult(client.wait_for_model)
        waiting.start()
        transport.wait_until_blocked()
        with self.assertRaises(FLRuntimeError) as concurrent_wait:
            client.wait_for_model()
        self.assertEqual('operation_in_progress', concurrent_wait.exception.error_code)

        transport.release_model()
        waiting.join()
        with self.assertRaises(FLRuntimeError) as active_round:
            client.wait_for_model()
        self.assertEqual('round_in_progress', active_round.exception.error_code)

        update_path = self.write_file('gated-update.input', b'update')
        self.assertIsNone(client.submit_update(update_path))
        with self.assertRaises(FLRuntimeError) as repeated_submit:
            client.submit_update(update_path)
        self.assertEqual(
            'round_already_succeeded', repeated_submit.exception.error_code)
        self.assertEqual(1, transport.submit_count)

    def test_client_transport_delivers_each_model_candidate_once(self):
        work_dir = os.path.join(self.root, 'candidate-transport')
        transport = ClientTransport(work_dir, 1, 9000, ('127.0.0.1', 1))
        transport.ready = True
        transport._uftpd_process = SimpleNamespace(poll=lambda: None)
        for name in ('round-a', 'round-b'):
            candidate = os.path.join(work_dir, 'inbox', name)
            os.makedirs(candidate)
            with open(os.path.join(candidate, 'model.manifest.json'), 'w') as fh:
                fh.write('{}')

        first = transport.wait_for_model_candidate()
        second = transport.wait_for_model_candidate()

        self.assertEqual('round-a', os.path.basename(first))
        self.assertEqual('round-b', os.path.basename(second))

    def test_committed_update_stays_successful_when_response_write_fails(self):
        round_id = '550e8400-e29b-41d4-a716-446655440000'
        round_dir = os.path.join(self.root, 'committed-round')
        failures = []
        transport = ServerTransport((1,), 2, 9000)
        transport.install_round(
            round_id, (1,), round_dir, 1024,
            lambda *args: failures.append(args))
        handler = self.make_handler(round_id, 1)
        handler.headers['Content-Digest'] = (
            'sha-256=:LXEWQrcmsEQBYnyp+6wy9chTD7GQPMTbAiWHF5IaSIE=:')
        handler.rfile = io.BytesIO(b'x')
        handler.send_response = lambda status: (_ for _ in ()).throw(
            OSError('response lost'))
        handler.send_header = lambda *args: None
        handler.end_headers = lambda: None
        handler._send_error = lambda *args: None
        self.assertIsNone(transport._reserve_upload(handler))

        transport._receive_update(handler, handler._upload_context)

        self.assertEqual([], failures)
        self.assertTrue(os.path.isfile(os.path.join(
            round_dir, 'updates', '1', 'update.manifest.json')))
        transport._release_upload()

    def test_model_manifest_rejects_boolean_node_id(self):
        round_id = '550e8400-e29b-41d4-a716-446655440000'
        candidate = self.make_candidate(round_id, (True,))
        transport = BlockingClientTransport(candidate)
        transport.release_model()
        client = ClientRuntime(
            os.path.join(self.root, 'boolean-client'), 1, 1024, transport)

        with self.assertRaises(FLRuntimeError) as raised:
            client.wait_for_model()
        self.assertEqual('invalid_manifest', raised.exception.error_code)

    def test_transport_rejects_old_nonparticipant_and_duplicate_updates(self):
        round_id = '550e8400-e29b-41d4-a716-446655440000'
        round_dir = os.path.join(self.root, 'transport-round')
        transport = ServerTransport((1, 2), 3, 9000)
        transport.install_round(round_id, (1, 2), round_dir, 1024, lambda *args: None)

        old_round = self.make_handler(
            '550e8400-e29b-41d4-a716-446655440001', 1)
        self.assertEqual(
            (404, 'round_not_found', '轮次不存在'),
            transport._reserve_upload(old_round))
        nonparticipant = self.make_handler(round_id, 8)
        self.assertEqual(
            (404, 'node_not_participant', '节点不属于本轮'),
            transport._reserve_upload(nonparticipant))

        update_dir = os.path.join(round_dir, 'updates', '1')
        os.makedirs(update_dir)
        with open(os.path.join(update_dir, 'update.manifest.json'), 'w') as fh:
            fh.write('{}')
        duplicate = self.make_handler(round_id, 1)
        self.assertEqual(
            (409, 'update_already_submitted', 'update 已提交'),
            transport._reserve_upload(duplicate))

    def test_uftp_status_requires_complete_multi_client_matrix_in_any_order(self):
        status_path = os.path.join(self.root, 'multi.status')
        lines = [
            'RESULT;0x00000002;round/model.manifest.json;1;copy;0',
            'CONNECT;success;0x00000001',
            'RESULT;0x00000001;round/model.bin;1;copy;0',
            'CONNECT;success;0x00000002',
            'RESULT;0x00000002;round/model.bin;1;copy;0',
            'RESULT;0x00000001;round/model.manifest.json;1;copy;0',
        ]
        with open(status_path, 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(lines) + '\n')
        _validate_uftp_status(
            status_path, (1, 2),
            ('round/model.bin', 'round/model.manifest.json'))

        with open(status_path, 'a', encoding='utf-8') as fh:
            fh.write(lines[-1] + '\n')
        with self.assertRaises(FLRuntimeError) as duplicate:
            _validate_uftp_status(
                status_path, (1, 2),
                ('round/model.bin', 'round/model.manifest.json'))
        self.assertEqual('transport_failed', duplicate.exception.error_code)

    def make_handler(self, round_id, node_id):
        return SimpleNamespace(
            command='PUT',
            path='/v1/rounds/%s/updates/%d' % (round_id, node_id),
            headers={
                'Content-Length': '1',
                'Content-Type': 'application/octet-stream',
                'Content-Digest': 'sha-256=:%s:' % ('A' * 43 + '='),
                'Expect': '100-continue',
                'Connection': 'close',
            },
        )

    def make_candidate(self, round_id, participant_node_ids):
        candidate = os.path.join(self.root, 'candidate', round_id)
        os.makedirs(candidate)
        model_path = os.path.join(candidate, 'model.bin')
        with open(model_path, 'wb') as fh:
            fh.write(b'model')
        import hashlib
        digest = hashlib.sha256(b'model').hexdigest()
        with open(os.path.join(candidate, 'model.manifest.json'), 'w',
                  encoding='utf-8') as fh:
            json.dump({
                'schema_version': 1,
                'artifact_type': 'model',
                'round_id': round_id,
                'size_bytes': 5,
                'sha256': digest,
                'participant_node_ids': list(participant_node_ids),
            }, fh)
        return candidate

    def make_round(self, node_ids):
        transport = SharedRoundTransport(os.path.join(self.root, 'inbox'))
        server = ServerRuntime(
            os.path.join(self.root, 'server'), node_ids, 1024, transport)
        clients = {
            node_id: ClientRuntime(
                os.path.join(self.root, 'client-%d' % node_id), node_id, 1024,
                transport.client(node_id))
            for node_id in node_ids
        }
        return server, clients, transport

    def write_file(self, name, content):
        path = os.path.join(self.root, name)
        with open(path, 'wb') as fh:
            fh.write(content)
        return path

    def read_file(self, path):
        with open(path, 'rb') as fh:
            return fh.read()


class SharedRoundTransport(object):
    ready = True

    def __init__(self, inbox_root):
        self.inbox_root = inbox_root
        self.round_id = None
        self.participant_node_ids = ()
        self.round_dir = None
        self.failure_callback = None
        self._publishing = threading.Event()
        self._downlink_complete = threading.Event()
        self._update_event = threading.Event()
        self.cancel_count = 0
        self.cancel_error = None
        self.operation_handle = object()
        self.cancelled_handle = None
        self.cancel_completed = False

    def client(self, node_id):
        return SharedClientTransport(self, node_id)

    def install_round(self, round_id, participant_node_ids, round_dir,
                      max_update_size_bytes, failure_callback):
        self.round_id = round_id
        self.participant_node_ids = tuple(participant_node_ids)
        self.round_dir = round_dir
        self.failure_callback = failure_callback

    def start_downlink(self, round_id, model_path, manifest_path):
        for node_id in self.participant_node_ids:
            candidate = os.path.join(self.inbox_root, str(node_id), round_id)
            os.makedirs(candidate)
            shutil.copyfile(model_path, os.path.join(candidate, 'model.bin'))
            shutil.copyfile(
                manifest_path, os.path.join(candidate, 'model.manifest.json'))
        self._publishing.set()
        return self.operation_handle

    def wait_downlink(self, operation_handle):
        if operation_handle is not self.operation_handle:
            raise RuntimeError('unexpected operation handle')
        self._downlink_complete.wait(5)

    def publish_model(self, round_id, model_path, manifest_path):
        operation = self.start_downlink(round_id, model_path, manifest_path)
        return self.wait_downlink(operation)

    def wait_until_publishing(self):
        if not self._publishing.wait(5):
            raise AssertionError('shared downlink did not start')

    def cancel_downlink(self, operation_handle):
        if operation_handle is not self.operation_handle:
            raise RuntimeError('unexpected operation handle')
        self.cancel_count += 1
        self.cancelled_handle = operation_handle
        self._downlink_complete.set()
        if self.cancel_error is not None:
            raise self.cancel_error
        self.cancel_completed = True
        return 'cancelled'

    def complete_downlink(self):
        self._downlink_complete.set()

    def fail_upload(self, node_id, error_code, error_message):
        self.failure_callback(
            self.round_id, node_id, error_code, error_message)

    def wait_for_update(self, timeout):
        self._update_event.wait(timeout)
        self._update_event.clear()


class SharedClientTransport(object):
    ready = True

    def __init__(self, shared, node_id):
        self.shared = shared
        self.node_id = node_id

    def wait_for_model_candidate(self):
        while self.shared.round_id is None:
            time.sleep(0.01)
        return os.path.join(
            self.shared.inbox_root, str(self.node_id), self.shared.round_id)

    def submit_update(self, round_id, node_id, update_path, size_bytes, digest):
        update_dir = os.path.join(
            self.shared.round_dir, 'updates', str(node_id))
        os.makedirs(update_dir)
        shutil.copyfile(update_path, os.path.join(update_dir, 'update.bin'))
        with open(os.path.join(update_dir, 'update.manifest.json'), 'w',
                  encoding='utf-8') as fh:
            json.dump({
                'schema_version': 1,
                'artifact_type': 'update',
                'round_id': round_id,
                'node_id': node_id,
                'size_bytes': size_bytes,
                'sha256': digest,
            }, fh)
        self.shared._update_event.set()


class BlockingClientTransport(object):
    ready = True

    def __init__(self, candidate):
        self.candidate = candidate
        self.submit_count = 0
        self._blocked = threading.Event()
        self._released = threading.Event()

    def wait_for_model_candidate(self):
        self._blocked.set()
        self._released.wait(5)
        return self.candidate

    def wait_until_blocked(self):
        if not self._blocked.wait(5):
            raise AssertionError('model wait did not block')

    def release_model(self):
        self._released.set()

    def submit_update(self, round_id, node_id, update_path, size_bytes, digest):
        self.submit_count += 1


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
