#!/usr/bin/env python
# -*- coding: utf-8 -*-

import base64
import hashlib
import json
import os
import shutil
import socket
import tempfile
import threading
import time
import unittest
import uuid
from types import SimpleNamespace

from wfb_ng.fl import (
    ClientRole,
    ClientRuntime,
    FLRuntimeError,
    ServerRole,
    ServerRuntime,
)
from wfb_ng.fl.transport import ServerTransport


def reserve_udp_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def read_json(path):
    with open(path, 'r', encoding='utf-8') as fh:
        return json.load(fh)


def write_test_json(path, value):
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(value, fh)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as fh:
        while True:
            chunk = fh.read(64 * 1024)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)


class V8SingleClientLoopTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='wfb-v8-loop-')
        self.addCleanup(shutil.rmtree, self.root, True)
        self.uftp_port = reserve_udp_port()

    def make_server(self):
        return ServerRole(
            work_dir=os.path.join(self.root, 'server'),
            participant_node_id=1,
            participant_uftp_uid=1,
            server_uftp_uid=2,
            uftp_port=self.uftp_port,
            http_port=0,
            max_update_size_bytes=1024 * 1024,
        )

    def make_client(self, server_address=('127.0.0.1', 1)):
        return ClientRole(
            work_dir=os.path.join(self.root, 'client'),
            node_id=1,
            uftp_uid=1,
            uftp_port=self.uftp_port,
            server_http_address=server_address,
            max_update_size_bytes=1024 * 1024,
        )

    def test_runtime_interfaces_reject_calls_until_transport_is_ready(self):
        server = self.make_server()
        client = self.make_client()
        model_path = os.path.join(self.root, 'model.input')
        update_path = os.path.join(self.root, 'update.input')

        for call in (
                lambda: server.runtime.publish_model(model_path),
                server.runtime.wait_for_updates,
                client.runtime.wait_for_model,
                lambda: client.runtime.submit_update(update_path)):
            with self.subTest(call=call):
                with self.assertRaises(FLRuntimeError) as raised:
                    call()
                self.assertEqual('transport_not_ready', raised.exception.error_code)

    @unittest.skipUnless(shutil.which('uftp') and shutil.which('uftpd'),
                         'uftp and uftpd are required')
    def test_four_interfaces_complete_real_single_client_loop(self):
        model_source = os.path.join(self.root, 'model.input')
        update_source = os.path.join(self.root, 'update.input')
        with open(model_source, 'wb') as fh:
            fh.write(b'global-model-v1\nweights=1,2,3\n')

        server = self.make_server()
        self.addCleanup(server.close)
        server.start()
        client = self.make_client(server.http_address)
        self.addCleanup(client.close)
        client.start()

        publish_result = self.run_in_thread(
            lambda: server.runtime.publish_model(model_source), 'publish-model')
        self.wait_for_round(os.path.join(self.root, 'server', 'rounds'))
        updates_result = self.run_in_thread(
            server.runtime.wait_for_updates, 'wait-for-updates')

        managed_model = client.runtime.wait_for_model()
        self.assertTrue(os.path.isabs(managed_model))
        self.assertNotEqual(os.path.abspath(model_source), managed_model)
        with open(managed_model, 'rb') as source, open(update_source, 'wb') as target:
            target.write(b'trained-node-1\n' + source.read().upper())

        self.assertIsNone(client.runtime.submit_update(update_source))
        self.assertIsNone(publish_result.join())
        updates = updates_result.join()

        self.assertEqual([1], list(updates))
        managed_update = updates[1]
        self.assertTrue(os.path.isabs(managed_update))
        self.assertEqual(sha256(update_source), sha256(managed_update))

        server_rounds = os.path.join(self.root, 'server', 'rounds')
        round_ids = os.listdir(server_rounds)
        self.assertEqual(1, len(round_ids))
        round_id = round_ids[0]
        parsed_round_id = uuid.UUID(round_id)
        self.assertEqual(4, parsed_round_id.version)
        self.assertEqual(str(parsed_round_id), round_id)

        server_round = os.path.join(server_rounds, round_id)
        client_round = os.path.join(self.root, 'client', 'rounds', round_id)
        model_manifest = read_json(os.path.join(server_round, 'model.manifest.json'))
        update_manifest = read_json(os.path.join(
            server_round, 'updates', '1', 'update.manifest.json'))
        server_state = read_json(os.path.join(server_round, 'round-state.json'))
        client_state = read_json(os.path.join(client_round, 'round-state.json'))

        self.assertEqual({
            'artifact_type': 'model',
            'participant_node_ids': [1],
            'round_id': round_id,
            'schema_version': 1,
            'sha256': sha256(model_source),
            'size_bytes': os.path.getsize(model_source),
        }, model_manifest)
        self.assertEqual({
            'artifact_type': 'update',
            'node_id': 1,
            'round_id': round_id,
            'schema_version': 1,
            'sha256': sha256(update_source),
            'size_bytes': os.path.getsize(update_source),
        }, update_manifest)
        self.assertEqual('succeeded', server_state['state'])
        self.assertEqual([1], server_state['participant_node_ids'])
        self.assertEqual([1], server_state['committed_update_node_ids'])
        self.assertEqual('succeeded', client_state['state'])

        status_path = os.path.join(server_round, 'uftp.status')
        with open(status_path, 'r', encoding='utf-8') as fh:
            status = fh.read()
        self.assertIn('CONNECT;success;0x00000001', status)
        self.assertIn('%s/model.bin' % round_id, status)
        self.assertIn('%s/model.manifest.json' % round_id, status)

    def test_continue_write_failure_reports_failure_and_releases_upload_slot(self):
        failures = []
        transport = ServerTransport(1, 2, self.uftp_port)
        round_id = str(uuid.uuid4())
        transport.install_round(
            round_id, (1,), os.path.join(self.root, 'server-failure'), 1024,
            lambda *args: failures.append(args))
        handler = make_upload_handler(round_id, 1, b'update')

        self.assertIsNone(transport._reserve_upload(handler))
        transport._fail_accepted_upload(
            handler._upload_context,
            'continue_write_failed',
            '100 Continue 写回失败')

        self.assertEqual([
            (round_id, 1, 'continue_write_failed', '100 Continue 写回失败'),
        ], failures)
        retry_handler = make_upload_handler(round_id, 1, b'update')
        self.assertEqual(
            (409, 'update_submission_used', 'update 提交机会已占用'),
            transport._reserve_upload(retry_handler))

    def test_submit_update_wraps_transport_error_and_persists_failure(self):
        work_dir = os.path.join(self.root, 'client-failure')
        round_id = str(uuid.uuid4())
        candidate_dir = self.make_model_candidate(round_id, 1)
        transport = FailingClientTransport(candidate_dir)
        runtime = ClientRuntime(work_dir, 1, 1024, transport)
        runtime.wait_for_model()
        update_path = os.path.join(self.root, 'failed-update.input')
        with open(update_path, 'wb') as fh:
            fh.write(b'update')

        with self.assertRaises(FLRuntimeError) as raised:
            runtime.submit_update(update_path)

        self.assertEqual('update_submit_failed', raised.exception.error_code)
        state_path = os.path.join(work_dir, 'rounds', round_id, 'round-state.json')
        state = read_json(state_path)
        self.assertEqual('failed', state['state'])
        self.assertEqual('update_submit_failed', state['error_code'])

    def test_wait_for_updates_observes_reliable_transport_failure(self):
        work_dir = os.path.join(self.root, 'server-failure')
        transport = ControlledServerTransport()
        runtime = ServerRuntime(work_dir, 1, 1024, transport)
        model_path = os.path.join(self.root, 'failed-model.input')
        with open(model_path, 'wb') as fh:
            fh.write(b'model')
        runtime.publish_model(model_path)
        result = self.run_in_thread(runtime.wait_for_updates, 'wait-for-failed-update')

        transport.fail_upload('upload_incomplete', 'update body 接收失败')

        with self.assertRaises(FLRuntimeError) as raised:
            result.join()
        self.assertEqual('upload_incomplete', raised.exception.error_code)
        round_dir = os.path.join(work_dir, 'rounds', transport.round_id)
        state = read_json(os.path.join(round_dir, 'round-state.json'))
        self.assertEqual('failed', state['state'])
        self.assertEqual('upload_incomplete', state['error_code'])

    def make_model_candidate(self, round_id, node_id):
        candidate_dir = os.path.join(self.root, 'candidate', round_id)
        os.makedirs(candidate_dir)
        model_path = os.path.join(candidate_dir, 'model.bin')
        with open(model_path, 'wb') as fh:
            fh.write(b'model')
        write_test_json(os.path.join(candidate_dir, 'model.manifest.json'), {
            'schema_version': 1,
            'artifact_type': 'model',
            'round_id': round_id,
            'size_bytes': os.path.getsize(model_path),
            'sha256': sha256(model_path),
            'participant_node_ids': [node_id],
        })
        return candidate_dir

    def run_in_thread(self, call, name):
        result = ThreadResult(call, name)
        self.addCleanup(result.join_if_running)
        result.start()
        return result

    def wait_for_round(self, rounds_dir):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if os.path.isdir(rounds_dir) and os.listdir(rounds_dir):
                return
            time.sleep(0.02)
        self.fail('server round was not created')


def make_upload_handler(round_id, node_id, body):
    digest = hashlib.sha256(body).digest()
    content_digest = base64.b64encode(digest).decode('ascii')
    return SimpleNamespace(
        command='PUT',
        path='/v1/rounds/%s/updates/%d' % (round_id, node_id),
        headers={
            'Content-Length': str(len(body)),
            'Content-Type': 'application/octet-stream',
            'Content-Digest': 'sha-256=:%s:' % content_digest,
            'Expect': '100-continue',
            'Connection': 'close',
        },
    )


class FailingClientTransport(object):
    ready = True

    def __init__(self, candidate_dir):
        self.candidate_dir = candidate_dir

    def wait_for_model_candidate(self):
        return self.candidate_dir

    def submit_update(self, round_id, node_id, update_path, size_bytes, digest):
        raise OSError('connection refused')


class ControlledServerTransport(object):
    ready = True

    def __init__(self):
        self.round_id = None
        self.node_id = None
        self.failure_callback = None

    def install_round(self, round_id, participant_node_ids, round_dir,
                      max_update_size_bytes, failure_callback):
        self.round_id = round_id
        self.node_id = participant_node_ids[0]
        self.failure_callback = failure_callback

    def publish_model(self, round_id, model_path, manifest_path):
        return None

    def wait_for_update(self, timeout):
        time.sleep(min(timeout, 0.01))

    def fail_upload(self, error_code, error_message):
        self.failure_callback(
            self.round_id, self.node_id, error_code, error_message)


class ThreadResult(object):
    def __init__(self, call, name):
        self.call = call
        self.value = None
        self.error = None
        self.thread = threading.Thread(target=self._run, name=name, daemon=True)

    def start(self):
        self.thread.start()

    def _run(self):
        try:
            self.value = self.call()
        except BaseException as exc:
            self.error = exc

    def join(self, timeout=15):
        self.thread.join(timeout)
        if self.thread.is_alive():
            raise AssertionError('%s did not finish' % self.thread.name)
        if self.error is not None:
            raise self.error
        return self.value

    def join_if_running(self):
        if self.thread.is_alive():
            self.thread.join(1)


if __name__ == '__main__':
    unittest.main()
