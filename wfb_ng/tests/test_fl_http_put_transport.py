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
from unittest import mock

from wfb_ng.fl import FLRuntimeError, ServerRuntime
from wfb_ng.fl.transport import ClientTransport, ServerTransport


class OneShotHttpPeer(object):
    def __init__(self, behavior):
        self.behavior = behavior
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen(2)
        self.listener.settimeout(1)
        self.address = self.listener.getsockname()
        self.connection_count = 0
        self.body = b''
        self.error = None
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        try:
            connection, _ = self.listener.accept()
            self.connection_count += 1
            with connection:
                connection.settimeout(1)
                request = b''
                while b'\r\n\r\n' not in request:
                    chunk = connection.recv(4096)
                    if not chunk:
                        return
                    request += chunk
                headers, remainder = request.split(b'\r\n\r\n', 1)
                content_length = 0
                for line in headers.split(b'\r\n')[1:]:
                    name, value = line.split(b':', 1)
                    if name.lower() == b'content-length':
                        content_length = int(value.strip())
                if self.behavior == 'silent':
                    time.sleep(0.2)
                    self.body = remainder
                    return
                if self.behavior == 'hold_body':
                    connection.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
                    connection.sendall(b'HTTP/1.1 100 Continue\r\n\r\n')
                    time.sleep(0.3)
                    self.body = remainder
                    return
                connection.sendall(b'HTTP/1.1 100 Continue\r\n\r\n')
                body = remainder
                while len(body) < content_length:
                    chunk = connection.recv(content_length - len(body))
                    if not chunk:
                        break
                    body += chunk
                self.body = body
                if self.behavior == 'hold_final':
                    time.sleep(0.3)
                elif self.behavior == 'success':
                    connection.sendall(
                        b'HTTP/1.1 201 Created\r\nContent-Length: 0\r\n\r\n')
        except BaseException as exc:
            self.error = exc

    def wait(self):
        self.thread.join(2)
        if self.thread.is_alive():
            raise AssertionError('测试 HTTP peer 未结束')
        if self.error is not None:
            raise self.error

    def close(self):
        self.listener.close()
        if self.thread.is_alive():
            self.thread.join(1)


class RuntimeHttpTransport(ServerTransport):
    def start_downlink(self, round_id, model_path, manifest_path):
        self.round_id = round_id
        return self

    def wait_downlink(self, operation):
        if operation is not self:
            raise RuntimeError('unexpected operation handle')

    def publish_model(self, round_id, model_path, manifest_path):
        self.round_id = round_id


class V8HttpPutTransportTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='wfb-v8-http-')
        self.addCleanup(shutil.rmtree, self.root, True)
        self.transport = ServerTransport((1, 2), 3, 9000, '127.0.0.1', '230.4.4.1', http_port=0)
        with mock.patch('wfb_ng.fl.transport.shutil.which', return_value='/bin/true'):
            self.transport.start()
        self.addCleanup(self.transport.close)
        self.round_id = str(uuid.uuid4())
        self.round_dir = os.path.join(self.root, 'round')
        self.failures = []
        self.transport.install_round(
            self.round_id, (1, 2), self.round_dir, 1024,
            lambda *args: self.failures.append(args))

    def test_storage_preflight_rejects_before_continue(self):
        update_parent = os.path.join(self.round_dir, 'updates')
        os.makedirs(update_parent)
        with open(os.path.join(update_parent, '1'), 'wb') as fh:
            fh.write(b'not-a-directory')

        sock, response = self.send_headers(
            self.make_request(self.round_id, 1, b'update'))
        self.addCleanup(response.close)
        self.addCleanup(sock.close)
        status, _, body = read_response(response)

        self.assertEqual(507, status)
        self.assertEqual(
            'storage_unavailable',
            json.loads(body.decode('utf-8'))['error_code'])
        self.assertEqual([], self.failures)

    def test_unknown_round_is_rejected_before_continue_with_json_error(self):
        request = self.make_request(str(uuid.uuid4()), 1, b'update')

        sock, response = self.send_headers(request)
        self.addCleanup(sock.close)
        status, headers, body = read_response(response)

        self.assertEqual(404, status)
        self.assertEqual('application/json; charset=utf-8', headers['content-type'])
        self.assertEqual('close', headers['connection'])
        self.assertEqual({
            'error_code': 'round_not_found',
            'error_message': '轮次不存在',
        }, json.loads(body.decode('utf-8')))
        self.assertEqual([], self.failures)

    def test_installing_new_round_does_not_release_accepted_old_round_body(self):
        old_body = b'old-round-update'
        old_sock, old_response = self.send_headers(
            self.make_request(self.round_id, 1, old_body))
        self.addCleanup(old_response.close)
        self.addCleanup(old_sock.close)
        self.assertEqual(100, read_response(old_response)[0])

        new_round_id = str(uuid.uuid4())
        new_round_dir = os.path.join(self.root, 'new-round')
        self.transport.install_round(
            new_round_id, (1, 2), new_round_dir, 1024,
            lambda *args: self.failures.append(args))
        new_sock, new_response = self.send_headers(
            self.make_request(new_round_id, 2, b'new-round-update'))
        status, _, body = read_response(new_response)
        self.assertEqual(409, status)
        self.assertEqual(
            'upload_in_progress', json.loads(body.decode('utf-8'))['error_code'])
        new_response.close()
        new_sock.close()

        old_sock.sendall(old_body)
        self.assertEqual(201, read_response(old_response)[0])

        accepted_sock, accepted_response = self.send_headers(
            self.make_request(new_round_id, 2, b'accepted'))
        self.addCleanup(accepted_response.close)
        self.addCleanup(accepted_sock.close)
        self.assertEqual(100, read_response(accepted_response)[0])
        accepted_sock.sendall(b'accepted')
        self.assertEqual(201, read_response(accepted_response)[0])

    def test_accepted_storage_failure_returns_507_and_reports_runtime_failure(self):
        body = b'update'
        sock, response = self.send_headers(
            self.make_request(self.round_id, 1, body))
        self.addCleanup(response.close)
        self.addCleanup(sock.close)
        self.assertEqual(100, read_response(response)[0])

        with mock.patch(
                'wfb_ng.fl.transport.os.fsync',
                side_effect=OSError('disk full')):
            sock.sendall(body)
            status, _, response_body = read_response(response)

        self.assertEqual(507, status)
        self.assertEqual(
            'storage_failed', json.loads(
                response_body.decode('utf-8'))['error_code'])
        self.assertEqual('storage_failed', self.failures[0][2])

    def test_manifest_commit_failure_returns_500_and_reports_runtime_failure(self):
        body = b'update'
        sock, response = self.send_headers(
            self.make_request(self.round_id, 1, body))
        self.addCleanup(response.close)
        self.addCleanup(sock.close)
        self.assertEqual(100, read_response(response)[0])

        with mock.patch(
                'wfb_ng.fl.transport.write_json_atomic',
                side_effect=OSError('manifest failed')):
            sock.sendall(body)
            status, _, response_body = read_response(response)

        self.assertEqual(500, status)
        self.assertEqual(
            'manifest_commit_failed', json.loads(
                response_body.decode('utf-8'))['error_code'])
        self.assertEqual('manifest_commit_failed', self.failures[0][2])

    def test_digest_mismatch_is_a_formal_accepted_failure(self):
        body = b'corrupted-update'
        request = self.make_request(self.round_id, 1, b'expected-update')
        sock, response = self.send_headers(request)
        self.addCleanup(response.close)
        self.addCleanup(sock.close)
        self.assertEqual(100, read_response(response)[0])

        sock.sendall(body[:len(b'expected-update')])
        status, _, response_body = read_response(response)

        self.assertEqual(422, status)
        self.assertEqual(
            'digest_mismatch',
            json.loads(response_body.decode('utf-8'))['error_code'])
        self.assertEqual([
            (self.round_id, 1, 'digest_mismatch', 'update 摘要不匹配'),
        ], self.failures)
        self.assertFalse(os.path.exists(os.path.join(
            self.round_dir, 'updates', '1', 'update.manifest.json')))

    def test_runtime_failure_persistence_error_becomes_fatal(self):
        self.transport.close()
        self.transport = RuntimeHttpTransport(
            (1,), 3, 9000, '127.0.0.1', '230.4.4.1', http_port=0)
        with mock.patch('wfb_ng.fl.transport.shutil.which', return_value='/bin/true'):
            self.transport.start()
        runtime = ServerRuntime(
            os.path.join(self.root, 'fatal-runtime-server'), (1,), 1024,
            self.transport)
        self.addCleanup(runtime.close)
        model_path = os.path.join(self.root, 'fatal-model.bin')
        with open(model_path, 'wb') as fh:
            fh.write(b'model')
        runtime.publish_model(model_path)

        body = b'evil-digest'
        request = self.make_request(self.transport.round_id, 1, b'good-digest')
        sock, response = self.send_headers(request)
        self.addCleanup(response.close)
        self.addCleanup(sock.close)
        self.assertEqual(100, read_response(response)[0])
        original_write_state = runtime._write_state

        def fail_failed_state(state, **extra):
            if state == 'failed':
                runtime._fatal_error = FLRuntimeError(
                    'state_persistence_failed', 'server 轮次状态持久化失败',
                    round_id=runtime._round_id)
                raise runtime._fatal_error
            return original_write_state(state, **extra)

        with mock.patch.object(runtime, '_write_state', side_effect=fail_failed_state):
            sock.sendall(body)
            status, _, response_body = read_response(response)
            with self.assertRaises(FLRuntimeError) as raised:
                runtime.wait_for_updates()

        self.assertEqual(500, status)
        self.assertEqual(
            'runtime_result_failed',
            json.loads(response_body.decode('utf-8'))['error_code'])
        self.assertEqual('state_persistence_failed', raised.exception.error_code)

    def test_accepted_failure_terminates_server_runtime_strict_round(self):
        self.transport.close()
        self.transport = RuntimeHttpTransport(
            (1,), 3, 9000, '127.0.0.1', '230.4.4.1', http_port=0)
        with mock.patch('wfb_ng.fl.transport.shutil.which', return_value='/bin/true'):
            self.transport.start()
        runtime = ServerRuntime(
            os.path.join(self.root, 'runtime-server'), (1,), 1024,
            self.transport)
        self.addCleanup(runtime.close)
        model_path = os.path.join(self.root, 'model.bin')
        with open(model_path, 'wb') as fh:
            fh.write(b'model')
        runtime.publish_model(model_path)

        body = b'evil-digest'
        request = self.make_request(self.transport.round_id, 1, b'good-digest')
        sock, response = self.send_headers(request)
        self.addCleanup(response.close)
        self.addCleanup(sock.close)
        self.assertEqual(100, read_response(response)[0])
        sock.sendall(body)
        self.assertEqual(422, read_response(response)[0])

        with self.assertRaises(FLRuntimeError) as raised:
            runtime.wait_for_updates()
        self.assertEqual('digest_mismatch', raised.exception.error_code)
        self.assertEqual(self.transport.round_id, raised.exception.round_id)
        self.assertEqual(1, raised.exception.node_id)

    def test_only_one_body_is_accepted_and_success_is_streamed_to_final_files(self):
        first_body = b'node-1-update' * 50
        first_request = self.make_request(self.round_id, 1, first_body)
        first_sock, first_response = self.send_headers(first_request)
        self.addCleanup(first_response.close)
        self.addCleanup(first_sock.close)
        self.assertEqual(100, read_response(first_response)[0])

        second_request = self.make_request(self.round_id, 2, b'node-2-update')
        second_sock, second_response = self.send_headers(second_request)
        status, _, body = read_response(second_response)
        self.assertEqual(409, status)
        self.assertEqual(
            'upload_in_progress', json.loads(body.decode('utf-8'))['error_code'])
        second_response.close()
        second_sock.close()

        first_sock.sendall(first_body)
        status, headers, body = read_response(first_response)
        self.assertEqual(201, status)
        self.assertEqual('0', headers['content-length'])
        self.assertEqual(b'', body)
        first_response.close()
        first_sock.close()

        update_dir = os.path.join(self.round_dir, 'updates', '1')
        with open(os.path.join(update_dir, 'update.bin'), 'rb') as fh:
            self.assertEqual(first_body, fh.read())
        with open(os.path.join(update_dir, 'update.manifest.json'),
                  'r', encoding='utf-8') as fh:
            manifest = json.load(fh)
        self.assertEqual({
            'schema_version': 1,
            'artifact_type': 'update',
            'round_id': self.round_id,
            'node_id': 1,
            'size_bytes': len(first_body),
            'sha256': hashlib.sha256(first_body).hexdigest(),
        }, manifest)
        self.assertEqual([], self.failures)

    def write_update(self, name, body):
        update_path = os.path.join(self.root, name)
        with open(update_path, 'wb') as fh:
            fh.write(body)
        return update_path

    def test_client_persists_verified_single_put_201_result(self):
        peer = OneShotHttpPeer('success')
        self.addCleanup(peer.close)
        body = b'accepted-update'
        update_path = self.write_update('accepted-update.bin', body)
        work_dir = os.path.join(self.root, 'successful-client')
        client = ClientTransport(
            work_dir, 1, 9000, '127.0.0.1', '230.4.4.1',
            peer.address, io_timeout=1)

        client.submit_update(
            self.round_id, 1, update_path, len(body),
            hashlib.sha256(body).hexdigest())

        peer.wait()
        self.assertEqual(1, peer.connection_count)
        with open(os.path.join(
                work_dir, 'rounds', self.round_id,
                'http-put-result.json'), encoding='utf-8') as fh:
            result = json.load(fh)
        self.assertEqual({
            'schema_version': 1,
            'round_id': self.round_id,
            'node_id': 1,
            'connection_count': 1,
            'put_request_count': 1,
            'continue_status': 100,
            'final_status': 201,
            'final_content_length': 0,
        }, result)

    def test_client_connect_timeout_is_bounded_and_structured(self):
        body = b'update'
        update_path = self.write_update('connect-timeout.bin', body)
        client = ClientTransport(
            os.path.join(self.root, 'connect-timeout-client'), 1, 9000,
            '127.0.0.1', '230.4.4.1',
            ('127.0.0.1', 1), io_timeout=0.1)

        with mock.patch(
                'wfb_ng.fl.transport.socket.create_connection',
                side_effect=socket.timeout('connect timeout')) as connect:
            started = time.monotonic()
            with self.assertRaises(FLRuntimeError) as raised:
                client.submit_update(
                    self.round_id, 1, update_path, len(body),
                    hashlib.sha256(body).hexdigest())

        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual('update_submit_failed', raised.exception.error_code)
        connect.assert_called_once_with(('127.0.0.1', 1), timeout=0.1)

    def test_server_body_read_timeout_is_a_formal_failure(self):
        self.transport.close()
        self.transport = ServerTransport(
            (1, 2), 3, 9000, '127.0.0.1', '230.4.4.1',
            http_port=0, io_timeout=0.1)
        with mock.patch('wfb_ng.fl.transport.shutil.which', return_value='/bin/true'):
            self.transport.start()
        self.transport.install_round(
            self.round_id, (1, 2), self.round_dir, 1024,
            lambda *args: self.failures.append(args))
        sock, response = self.send_headers(
            self.make_request(self.round_id, 1, b'expected-body'))
        self.addCleanup(response.close)
        self.addCleanup(sock.close)
        self.assertEqual(100, read_response(response)[0])

        status, _, body = read_response(response)

        self.assertEqual(422, status)
        self.assertEqual(
            'upload_incomplete', json.loads(body.decode('utf-8'))['error_code'])
        self.assertEqual('upload_incomplete', self.failures[0][2])

    def test_client_body_write_timeout_is_bounded_and_not_retried(self):
        peer = OneShotHttpPeer('hold_body')
        self.addCleanup(peer.close)
        body = b'x' * (8 * 1024 * 1024)
        update_path = self.write_update('body-write-timeout.bin', body)
        client = ClientTransport(
            os.path.join(self.root, 'body-timeout-client'), 1, 9000,
            '127.0.0.1', '230.4.4.1', peer.address, io_timeout=0.05)

        started = time.monotonic()
        with self.assertRaises(FLRuntimeError) as raised:
            client.submit_update(
                self.round_id, 1, update_path, len(body),
                hashlib.sha256(body).hexdigest())

        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual('update_submit_failed', raised.exception.error_code)
        peer.wait()
        self.assertEqual(1, peer.connection_count)

    def test_client_final_response_timeout_is_bounded_and_not_retried(self):
        peer = OneShotHttpPeer('hold_final')
        self.addCleanup(peer.close)
        body = b'committed-update'
        update_path = self.write_update('final-timeout.bin', body)
        client = ClientTransport(
            os.path.join(self.root, 'final-timeout-client'), 1, 9000,
            '127.0.0.1', '230.4.4.1', peer.address, io_timeout=0.05)

        started = time.monotonic()
        with self.assertRaises(FLRuntimeError) as raised:
            client.submit_update(
                self.round_id, 1, update_path, len(body),
                hashlib.sha256(body).hexdigest())

        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual('update_submit_failed', raised.exception.error_code)
        peer.wait()
        self.assertEqual(1, peer.connection_count)
        self.assertEqual(body, peer.body)

    def test_client_times_out_waiting_for_continue_without_sending_body(self):
        peer = OneShotHttpPeer('silent')
        self.addCleanup(peer.close)
        body = b'update-body'
        update_path = os.path.join(self.root, 'timeout-update.bin')
        with open(update_path, 'wb') as fh:
            fh.write(body)
        client = ClientTransport(
            os.path.join(self.root, 'timeout-client'), 1, 9000,
            '127.0.0.1', '230.4.4.1', peer.address, io_timeout=0.1)

        started = time.monotonic()
        with self.assertRaises(FLRuntimeError) as raised:
            client.submit_update(
                self.round_id, 1, update_path, len(body),
                hashlib.sha256(body).hexdigest())

        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual('update_submit_failed', raised.exception.error_code)
        peer.wait()
        self.assertEqual(1, peer.connection_count)
        self.assertEqual(b'', peer.body)

    def test_client_does_not_retry_when_final_response_is_lost(self):
        peer = OneShotHttpPeer('drop_final')
        self.addCleanup(peer.close)
        body = b'committed-update'
        update_path = os.path.join(self.root, 'lost-response-update.bin')
        with open(update_path, 'wb') as fh:
            fh.write(body)
        client = ClientTransport(
            os.path.join(self.root, 'lost-response-client'), 1, 9000,
            '127.0.0.1', '230.4.4.1', peer.address, io_timeout=0.2)

        with self.assertRaises(FLRuntimeError) as raised:
            client.submit_update(
                self.round_id, 1, update_path, len(body),
                hashlib.sha256(body).hexdigest())

        self.assertIn(raised.exception.error_code, (
            'invalid_http_response', 'update_submit_failed'))
        peer.wait()
        self.assertEqual(1, peer.connection_count)
        self.assertEqual(body, peer.body)

    def test_client_uses_server_error_code_and_sends_no_body_when_rejected(self):
        update_path = os.path.join(self.root, 'rejected-update.bin')
        body = b'rejected-update'
        with open(update_path, 'wb') as fh:
            fh.write(body)
        client = ClientTransport(
            os.path.join(self.root, 'client'), 1, 9000,
            '127.0.0.1', '230.4.4.1',
            self.transport.http_address, io_timeout=1)

        with self.assertRaises(FLRuntimeError) as raised:
            client.submit_update(
                str(uuid.uuid4()), 1, update_path, len(body),
                hashlib.sha256(body).hexdigest())

        self.assertEqual('round_not_found', raised.exception.error_code)
        self.assertEqual([], self.failures)

    def test_route_and_admission_errors_have_deterministic_statuses(self):
        valid = self.make_request(self.round_id, 1, b'update')
        cases = (
            (
                'method', valid.replace(b'PUT ', b'GET ', 1),
                400, 'method_not_allowed',
            ),
            (
                'noncanonical node', valid.replace(b'/updates/1 ', b'/updates/01 '),
                400, 'invalid_node_id',
            ),
            (
                'missing length', valid.replace(b'Content-Length: 6\r\n', b''),
                411, 'length_required',
            ),
            (
                'invalid length', valid.replace(b'Content-Length: 6', b'Content-Length: nope'),
                400, 'invalid_content_length',
            ),
            (
                'negative length', valid.replace(b'Content-Length: 6', b'Content-Length: -1'),
                400, 'invalid_content_length',
            ),
            (
                'too large', valid.replace(b'Content-Length: 6', b'Content-Length: 1025'),
                413, 'update_too_large',
            ),
            (
                'media type', valid.replace(
                    b'application/octet-stream', b'application/json'),
                415, 'unsupported_media_type',
            ),
            (
                'digest', valid.replace(b'sha-256=:', b'md5=:', 1),
                400, 'invalid_content_digest',
            ),
        )

        for name, request, expected_status, expected_code in cases:
            with self.subTest(name=name):
                sock, response = self.send_headers(request)
                status, _, body = read_response(response)
                response.close()
                sock.close()
                self.assertEqual(expected_status, status)
                self.assertEqual(
                    expected_code, json.loads(body.decode('utf-8'))['error_code'])
        self.assertEqual([], self.failures)

    def test_invalid_headers_are_rejected_before_body(self):
        valid = self.make_request(self.round_id, 1, b'update')
        cases = (
            (
                'missing connection close',
                valid.replace(b'Connection: close\r\n', b''),
                400, 'connection_close_required',
            ),
            (
                'persistent connection',
                valid.replace(b'Connection: close', b'Connection: keep-alive'),
                400, 'connection_close_required',
            ),
            (
                'duplicate expect',
                valid.replace(
                    b'\r\n\r\n', b'\r\nExpect: nonsense\r\n\r\n'),
                400, 'invalid_request_headers',
            ),
            (
                'duplicate connection',
                valid.replace(
                    b'\r\n\r\n', b'\r\nConnection: keep-alive\r\n\r\n'),
                400, 'connection_close_required',
            ),
            (
                'transfer encoding',
                valid.replace(
                    b'\r\n\r\n', b'\r\nTransfer-Encoding: chunked\r\n\r\n'),
                400, 'invalid_request_headers',
            ),
            (
                'duplicate content length',
                valid.replace(
                    b'\r\n\r\n', b'\r\nContent-Length: 6\r\n\r\n'),
                400, 'invalid_request_headers',
            ),
            (
                'duplicate content digest',
                valid.replace(
                    b'\r\n\r\n',
                    b'\r\nContent-Digest: sha-256=:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=:\r\n\r\n'),
                400, 'invalid_request_headers',
            ),
        )

        for name, request, expected_status, expected_code in cases:
            with self.subTest(name=name):
                sock, response = self.send_headers(request)
                status, headers, body = read_response(response)
                response.close()
                sock.close()
                self.assertEqual(expected_status, status)
                self.assertEqual('close', headers['connection'])
                self.assertEqual(expected_code, json.loads(
                    body.decode('utf-8'))['error_code'])
        self.assertEqual([], self.failures)

    def make_request(self, round_id, node_id, body, extra_headers=()):
        digest = base64.b64encode(hashlib.sha256(body).digest()).decode('ascii')
        headers = [
            'PUT /v1/rounds/%s/updates/%d HTTP/1.1' % (round_id, node_id),
            'Host: 127.0.0.1',
            'Content-Length: %d' % len(body),
            'Content-Digest: sha-256=:%s:' % digest,
            'Content-Type: application/octet-stream',
            'Expect: 100-continue',
            'Connection: close',
        ]
        headers.extend(extra_headers)
        return ('\r\n'.join(headers) + '\r\n\r\n').encode('ascii')

    def send_headers(self, request):
        sock = socket.create_connection(self.transport.http_address, timeout=2)
        sock.settimeout(2)
        response = sock.makefile('rb')
        self.addCleanup(response.close)
        sock.sendall(request)
        return sock, response


def read_response(response):
    status_line = response.readline().decode('iso-8859-1').rstrip('\r\n')
    parts = status_line.split(' ', 2)
    if len(parts) < 2:
        raise AssertionError('HTTP 响应状态行无效: %r' % status_line)
    headers = {}
    while True:
        line = response.readline()
        if line in (b'\r\n', b'\n'):
            break
        if not line:
            raise AssertionError('HTTP 响应 headers 不完整')
        name, value = line.decode('iso-8859-1').split(':', 1)
        headers[name.strip().lower()] = value.strip()
    body = response.read(int(headers.get('content-length', '0')))
    return int(parts[1]), headers, body


if __name__ == '__main__':
    unittest.main()
