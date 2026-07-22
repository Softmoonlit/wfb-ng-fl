#!/usr/bin/env python
# -*- coding: utf-8 -*-

import base64
import hashlib
import http.server
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass

from .artifacts import write_json_atomic
from .errors import FLRuntimeError


@dataclass(frozen=True)
class RoundContext:
    round_id: str
    participant_node_ids: tuple
    round_dir: str
    max_update_size_bytes: int
    failure_callback: object


@dataclass(frozen=True)
class _UploadContext:
    round_context: RoundContext
    node_id: int
    content_length: int
    expected_digest: bytes


class _DownlinkHandle(object):
    pass


@dataclass
class _DownlinkOperation:
    handle: object
    process: object
    status_path: str
    expected_files: tuple
    state: str = 'active'
    error: object = None


class ServerTransport(object):
    def __init__(self, participant_uftp_uids, server_uftp_uid, uftp_port,
                 http_host='127.0.0.1', http_port=0,
                 uftp_bind_host='127.0.0.1',
                 uftp_multicast_host='127.0.0.1',
                 uftp_private_multicast_host='239.255.0.1', io_timeout=10,
                 cancel_grace_period=2):
        if isinstance(participant_uftp_uids, int):
            participant_uftp_uids = (participant_uftp_uids,)
        self.participant_uftp_uids = tuple(sorted(participant_uftp_uids))
        self.server_uftp_uid = server_uftp_uid
        self.uftp_port = uftp_port
        self.http_host = http_host
        self.http_port = http_port
        self.uftp_bind_host = uftp_bind_host
        self.uftp_multicast_host = uftp_multicast_host
        self.uftp_private_multicast_host = uftp_private_multicast_host
        self.io_timeout = io_timeout
        self.cancel_grace_period = cancel_grace_period
        self.ready = False
        self._state = 'new'
        self._context = None
        self._context_lock = threading.Lock()
        self._active_uploads = set()
        self._used_uploads = set()
        self._update_event = threading.Event()
        self._http_server = None
        self._http_thread = None
        self._downlink_condition = threading.Condition()
        self._downlink_operation = None

    @property
    def http_address(self):
        if self._http_server is None:
            return self.http_host, self.http_port
        return self._http_server.server_address

    def start(self):
        if self._state != 'new':
            raise FLRuntimeError(
                'transport_already_started', 'Transport 不能重复启动')
        self._state = 'starting'
        if not shutil.which('uftp'):
            self._state = 'failed'
            raise FLRuntimeError('transport_unavailable', '缺少 uftp 可执行文件')
        transport = self

        class UpdateHandler(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def setup(self):
                super().setup()
                self.connection.settimeout(transport.io_timeout)

            def handle_expect_100(self):
                error = transport._reserve_upload(self)
                if error is not None:
                    self._send_error(*error)
                    return False
                try:
                    self.send_response_only(100)
                    self.end_headers()
                except OSError:
                    transport._fail_accepted_upload(
                        self._upload_context,
                        'continue_write_failed',
                        '100 Continue 写回失败')
                    self.close_connection = True
                    return False
                return True

            def do_PUT(self):
                if not getattr(self, '_upload_context', None):
                    self._send_error(400, 'expect_required', '必须使用 Expect: 100-continue')
                    return
                try:
                    transport._receive_update(self, self._upload_context)
                finally:
                    transport._release_upload(self._upload_context)

            def do_GET(self):
                self._send_error(400, 'method_not_allowed', '只允许 PUT 请求')

            do_POST = do_GET
            do_PATCH = do_GET
            do_DELETE = do_GET

            def _send_error(self, status, error_code, message):
                body = json.dumps({
                    'error_code': error_code,
                    'error_message': message,
                }, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(body)
                self.close_connection = True

            def log_message(self, fmt, *args):
                return

        class UpdateServer(http.server.ThreadingHTTPServer):
            daemon_threads = False
            block_on_close = True
            allow_reuse_address = True

        try:
            self._http_server = UpdateServer(
                (self.http_host, self.http_port), UpdateHandler)
            self._http_thread = threading.Thread(
                target=self._http_server.serve_forever,
                name='fl-update-listener', daemon=True)
            self._http_thread.start()
        except OSError as exc:
            if self._http_server is not None:
                self._http_server.server_close()
                self._http_server = None
            self._state = 'failed'
            raise FLRuntimeError(
                'transport_start_failed', 'HTTP listener 启动失败') from exc
        self.ready = True
        self._state = 'ready'

    def poll_failure(self):
        thread = self._http_thread
        if self.ready and thread is not None and not thread.is_alive():
            self.ready = False
            self._state = 'failed'
            return FLRuntimeError('transport_failed', 'HTTP listener 意外退出')
        return None

    def install_round(self, round_id, participant_node_ids, round_dir,
                      max_update_size_bytes, failure_callback):
        with self._context_lock:
            self._context = RoundContext(
                round_id, tuple(sorted(participant_node_ids)), round_dir,
                max_update_size_bytes, failure_callback)
            self._update_event.clear()

    def publish_model(self, round_id, model_path, manifest_path):
        operation = self.start_downlink(round_id, model_path, manifest_path)
        return self.wait_downlink(operation)

    def start_downlink(self, round_id, model_path, manifest_path):
        round_dir = os.path.dirname(model_path)
        status_path = _new_uftp_status_path(round_dir)
        try:
            descriptor = os.open(
                status_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            os.unlink(status_path)
        except OSError as exc:
            raise FLRuntimeError(
                'transport_failed', 'UFTP status 文件无法创建') from exc
        log_path = os.path.join(
            round_dir, 'uftp-%s.log' % uuid.uuid4())
        command = [
            shutil.which('uftp'),
            '-q',
            '-I', self.uftp_bind_host,
            '-M', self.uftp_multicast_host,
            '-P', self.uftp_private_multicast_host,
            '-p', str(self.uftp_port),
            '-U', _format_uid(self.server_uftp_uid),
            '-H', ','.join(_format_uid(uid) for uid in self.participant_uftp_uids),
            '-Y', 'none',
            '-R', '15000',
            '-r', '0.1:0.01:2.0',
            '-s', '20',
            '-L', log_path,
            '-S', status_path,
            '-D', round_id,
            os.path.basename(model_path),
            os.path.basename(manifest_path),
        ]
        handle = _DownlinkHandle()
        with self._downlink_condition:
            if (self._downlink_operation is not None and
                    self._downlink_operation.state != 'completed'):
                raise RuntimeError('已有活动下行 operation')
            process = subprocess.Popen(
                command, cwd=round_dir, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            operation = _DownlinkOperation(
                handle, process, status_path,
                ('%s/model.bin' % round_id,
                 '%s/model.manifest.json' % round_id))
            self._downlink_operation = operation
            threading.Thread(
                target=self._monitor_downlink, args=(operation,),
                name='fl-uftp-operation', daemon=True).start()
        return handle

    def wait_downlink(self, handle):
        with self._downlink_condition:
            operation = self._require_downlink(handle)
            while operation.state != 'completed':
                self._downlink_condition.wait()
            if operation.error is not None:
                raise operation.error

    def cancel_downlink(self, handle):
        with self._downlink_condition:
            operation = self._require_downlink(handle)
            if operation.state == 'completed':
                return 'already_completed'
            if operation.state == 'active' and operation.process.poll() is not None:
                while operation.state != 'completed':
                    self._downlink_condition.wait()
                return 'already_completed'
            if operation.state == 'active':
                operation.state = 'cancelling'
                operation.process.terminate()
            while operation.state != 'completed':
                try:
                    operation.process.wait(timeout=self.cancel_grace_period)
                except subprocess.TimeoutExpired:
                    operation.process.kill()
                    operation.process.wait()
                self._downlink_condition.wait()
            return 'cancelled'

    def _monitor_downlink(self, operation):
        return_code = operation.process.wait()
        error = None
        with self._downlink_condition:
            cancelled = operation.state == 'cancelling'
        if not cancelled:
            if return_code != 0:
                error = FLRuntimeError(
                    'transport_failed', 'UFTP 退出码为 %d' % return_code)
            else:
                try:
                    _validate_uftp_status(
                        operation.status_path, self.participant_uftp_uids,
                        operation.expected_files)
                except FLRuntimeError as exc:
                    error = exc
        with self._downlink_condition:
            operation.error = error
            operation.state = 'completed'
            self._downlink_condition.notify_all()

    def _require_downlink(self, handle):
        operation = self._downlink_operation
        if operation is None or operation.handle is not handle:
            raise RuntimeError('未知 UFTP operation handle')
        return operation

    def wait_for_update(self, timeout):
        self._update_event.wait(timeout)
        self._update_event.clear()

    def close(self):
        if self._state == 'closed':
            return
        self.ready = False
        self._state = 'stopping'
        with self._downlink_condition:
            operation = self._downlink_operation
        if operation is not None and operation.state != 'completed':
            self.cancel_downlink(operation.handle)
        if self._http_server is not None:
            self._http_server.shutdown()
            self._http_server.server_close()
            self._http_server = None
        if self._http_thread is not None:
            self._http_thread.join(2)
            self._http_thread = None
        self._state = 'closed'

    def _reserve_upload(self, handler):
        with self._context_lock:
            context = self._context
            if context is None:
                return 404, 'round_not_found', '轮次不存在'
            if handler.command != 'PUT':
                return 400, 'method_not_allowed', '只允许 PUT 请求'
            prefix = '/v1/rounds/%s/updates/' % context.round_id
            if not handler.path.startswith(prefix):
                return 404, 'round_not_found', '轮次不存在'
            node_id_text = handler.path[len(prefix):]
            if not node_id_text or not node_id_text.isascii() or not node_id_text.isdigit():
                return 400, 'invalid_node_id', '节点标识无效'
            try:
                node_id = int(node_id_text)
            except ValueError:
                return 400, 'invalid_node_id', '节点标识无效'
            if str(node_id) != node_id_text or node_id <= 0:
                return 400, 'invalid_node_id', '节点标识无效'
            if node_id not in context.participant_node_ids:
                return 404, 'node_not_participant', '节点不属于本轮'
            update_key = (context.round_id, node_id)
            update_dir = os.path.join(context.round_dir, 'updates', str(node_id))
            if os.path.isfile(os.path.join(update_dir, 'update.manifest.json')):
                return 409, 'update_already_submitted', 'update 已提交'
            if update_key in self._active_uploads:
                return 409, 'upload_in_progress', 'update 正在上传'
            if update_key in self._used_uploads:
                return 409, 'update_submission_used', 'update 提交机会已占用'
            if _header_values(handler.headers, 'Transfer-Encoding'):
                return 400, 'invalid_request_headers', '请求 headers 无效'
            expect_values = _header_values(handler.headers, 'Expect')
            connection_values = _header_values(handler.headers, 'Connection')
            if expect_values != ['100-continue']:
                return 400, 'invalid_request_headers', '请求 headers 无效'
            if connection_values != ['close']:
                return 400, 'connection_close_required', '必须使用 Connection: close'
            content_lengths = _header_values(handler.headers, 'Content-Length')
            content_digests = _header_values(handler.headers, 'Content-Digest')
            content_types = _header_values(handler.headers, 'Content-Type')
            if (len(content_lengths) > 1 or len(content_digests) != 1 or
                    len(content_types) != 1):
                return 400, 'invalid_request_headers', '请求 headers 无效'
            if not content_lengths:
                return 411, 'length_required', '缺少合法 Content-Length'
            try:
                content_length = int(content_lengths[0])
            except (TypeError, ValueError):
                return 400, 'invalid_content_length', 'Content-Length 无效'
            if content_length < 0:
                return 400, 'invalid_content_length', 'Content-Length 无效'
            if content_length > context.max_update_size_bytes:
                return 413, 'update_too_large', 'update 超过大小限制'
            if content_types[0] != 'application/octet-stream':
                return 415, 'unsupported_media_type', 'Content-Type 无效'
            digest = _parse_content_digest(content_digests[0])
            if digest is None:
                return 400, 'invalid_content_digest', 'Content-Digest 无效'
            try:
                os.makedirs(update_dir, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                        mode='wb', dir=update_dir, prefix='.upload-preflight.',
                        delete=True):
                    pass
            except OSError:
                return 507, 'storage_unavailable', 'update 目标存储不可用'
            self._active_uploads.add(update_key)
            self._used_uploads.add(update_key)
            handler._upload_context = _UploadContext(
                context, node_id, content_length, digest)
            return None

    def _release_upload(self, upload_context):
        with self._context_lock:
            self._active_uploads.discard((
                upload_context.round_context.round_id, upload_context.node_id))

    def _fail_accepted_upload(self, upload_context, error_code, error_message):
        context = upload_context.round_context
        try:
            context.failure_callback(
                context.round_id, upload_context.node_id, error_code, error_message)
        finally:
            self._release_upload(upload_context)

    def _receive_update(self, handler, upload_context):
        context = upload_context.round_context
        node_id = upload_context.node_id
        content_length = upload_context.content_length
        expected_digest = upload_context.expected_digest
        update_dir = os.path.join(context.round_dir, 'updates', str(node_id))
        update_path = os.path.join(update_dir, 'update.bin')
        temp_path = None
        data_placed = False
        committed = False
        try:
            try:
                os.makedirs(update_dir, exist_ok=True)
                target = tempfile.NamedTemporaryFile(
                    mode='wb', dir=update_dir, prefix='.update.bin.',
                    delete=False)
            except OSError:
                self._reject_accepted_upload(
                    handler, context, node_id, 507,
                    'storage_failed', 'update 数据落盘失败')
                return

            temp_path = target.name
            digest = hashlib.sha256()
            received = 0
            try:
                with target:
                    while received < content_length:
                        try:
                            chunk = handler.rfile.read(
                                min(64 * 1024, content_length - received))
                        except (OSError, socket.timeout):
                            raise FLRuntimeError(
                                'upload_incomplete', 'update body 接收失败')
                        if not chunk:
                            raise FLRuntimeError(
                                'upload_incomplete', 'update body 提前结束')
                        try:
                            target.write(chunk)
                        except OSError:
                            raise FLRuntimeError(
                                'storage_failed', 'update 数据落盘失败')
                        digest.update(chunk)
                        received += len(chunk)
                    try:
                        target.flush()
                        os.fsync(target.fileno())
                    except OSError:
                        raise FLRuntimeError(
                            'storage_failed', 'update 数据落盘失败')
            except FLRuntimeError as exc:
                status = 507 if exc.error_code == 'storage_failed' else 422
                self._reject_accepted_upload(
                    handler, context, node_id, status,
                    exc.error_code, exc.error_message)
                return

            if digest.digest() != expected_digest:
                self._reject_accepted_upload(
                    handler, context, node_id, 422,
                    'digest_mismatch', 'update 摘要不匹配')
                return

            try:
                os.replace(temp_path, update_path)
            except OSError:
                self._reject_accepted_upload(
                    handler, context, node_id, 507,
                    'storage_failed', 'update 数据落盘失败')
                return
            temp_path = None
            data_placed = True
            try:
                write_json_atomic(os.path.join(update_dir, 'update.manifest.json'), {
                    'schema_version': 1,
                    'artifact_type': 'update',
                    'round_id': context.round_id,
                    'node_id': node_id,
                    'size_bytes': content_length,
                    'sha256': digest.hexdigest(),
                })
            except Exception:
                self._reject_accepted_upload(
                    handler, context, node_id, 500,
                    'manifest_commit_failed', 'update manifest 提交失败')
                return
            committed = True
            self._update_event.set()
            handler.send_response(201)
            handler.send_header('Content-Length', '0')
            handler.send_header('Connection', 'close')
            handler.end_headers()
            handler.close_connection = True
        except OSError:
            if committed:
                handler.close_connection = True
                return
            self._reject_accepted_upload(
                handler, context, node_id, 422,
                'upload_incomplete', 'update body 接收失败')
        finally:
            if temp_path is not None:
                try:
                    os.unlink(temp_path)
                except FileNotFoundError:
                    pass
            if data_placed and not committed:
                try:
                    os.unlink(update_path)
                except FileNotFoundError:
                    pass

    def _reject_accepted_upload(
            self, handler, context, node_id, status, error_code, error_message):
        try:
            context.failure_callback(
                context.round_id, node_id, error_code, error_message)
        except Exception:
            status = 500
            error_code = 'runtime_result_failed'
            error_message = 'Runtime 无法可靠接收上传失败结果'
        try:
            handler._send_error(status, error_code, error_message)
        except OSError:
            handler.close_connection = True


class ClientTransport(object):
    def __init__(self, work_dir, uftp_uid, uftp_port, server_http_address,
                 uftp_bind_host='127.0.0.1', io_timeout=10,
                 uftp_multicast_host='127.0.0.1'):
        self.work_dir = os.path.abspath(work_dir)
        self.uftp_uid = uftp_uid
        self.uftp_port = uftp_port
        self.server_http_address = server_http_address
        self.uftp_bind_host = uftp_bind_host
        self.uftp_multicast_host = uftp_multicast_host
        self.io_timeout = io_timeout
        self.ready = False
        self._state = 'new'
        self._operation_condition = threading.Condition()
        self._operation_socket = None
        self._operation_active = False
        self._inbox_dir = os.path.join(self.work_dir, 'inbox')
        self._temp_dir = os.path.join(self.work_dir, 'uftp-tmp')
        self._uftpd_process = None
        self._uftpd_stderr = None
        self._delivered_candidates = set()

    def start(self):
        if self._state != 'new':
            raise FLRuntimeError(
                'transport_already_started', 'Transport 不能重复启动')
        self._state = 'starting'
        executable = shutil.which('uftpd')
        if not executable:
            self._state = 'failed'
            raise FLRuntimeError('transport_unavailable', '缺少 uftpd 可执行文件')
        try:
            os.makedirs(self._inbox_dir, exist_ok=True)
            os.makedirs(self._temp_dir, exist_ok=True)
            stderr_path = os.path.join(self.work_dir, 'uftpd.log')
            self._uftpd_stderr = open(stderr_path, 'ab')
            command = [
                executable,
                '-d',
                '-q',
                '-I', self.uftp_bind_host,
                '-M', self.uftp_multicast_host,
                '-p', str(self.uftp_port),
                '-U', _format_uid(self.uftp_uid),
                '-D', self._inbox_dir,
                '-T', self._temp_dir,
                '-F', os.path.join(self.work_dir, 'uftpd.status'),
            ]
            self._uftpd_process = subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=self._uftpd_stderr)
        except OSError as exc:
            self.close()
            self._state = 'failed'
            raise FLRuntimeError(
                'transport_start_failed', 'uftpd 启动失败') from exc
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self._uftpd_process.poll() is not None:
                self.close()
                self._state = 'failed'
                raise FLRuntimeError('transport_start_failed', 'uftpd 启动失败')
            if _process_listens_udp(self._uftpd_process.pid, self.uftp_port):
                self.ready = True
                self._state = 'ready'
                return
            time.sleep(0.02)
        self.close()
        self._state = 'failed'
        raise FLRuntimeError('transport_start_failed', 'uftpd 未进入监听状态')

    def poll_failure(self):
        process = self._uftpd_process
        if self.ready and process is not None:
            return_code = process.poll()
            if return_code is not None:
                self.ready = False
                self._state = 'failed'
                return FLRuntimeError(
                    'transport_failed',
                    'uftpd 意外退出，退出码为 %d' % return_code)
        return None

    def wait_for_model_candidate(self):
        while self.ready:
            try:
                names = sorted(os.listdir(self._inbox_dir))
            except FileNotFoundError:
                names = []
            for name in names:
                candidate = os.path.join(self._inbox_dir, name)
                if candidate in self._delivered_candidates:
                    continue
                if (os.path.isdir(candidate) and
                        os.path.isfile(os.path.join(candidate, 'model.manifest.json'))):
                    self._delivered_candidates.add(candidate)
                    return candidate
            if self._uftpd_process.poll() is not None:
                raise FLRuntimeError('transport_failed', 'uftpd 意外退出')
            time.sleep(0.02)
        raise FLRuntimeError('transport_not_ready', 'Transport 尚未 ready')

    def submit_update(self, round_id, node_id, update_path, size_bytes, digest_hex):
        with self._operation_condition:
            if self._operation_active:
                raise RuntimeError('已有活动 HTTP PUT operation')
            self._operation_active = True
        host, port = self.server_http_address
        sock = None
        response_file = None
        try:
            sock = socket.create_connection((host, port), timeout=self.io_timeout)
            with self._operation_condition:
                self._operation_socket = sock
                if self._state == 'stopping':
                    sock.close()
                    raise FLRuntimeError(
                        'transport_not_ready', 'Transport 尚未 ready',
                        round_id=round_id, node_id=node_id)
            sock.settimeout(self.io_timeout)
            response_file = sock.makefile('rb')
            path = '/v1/rounds/%s/updates/%d' % (round_id, node_id)
            digest = base64.b64encode(bytes.fromhex(digest_hex)).decode('ascii')
            headers = (
                'PUT %s HTTP/1.1\r\n'
                'Host: %s:%d\r\n'
                'Content-Length: %d\r\n'
                'Content-Digest: sha-256=:%s:\r\n'
                'Content-Type: application/octet-stream\r\n'
                'Expect: 100-continue\r\n'
                'Connection: close\r\n\r\n'
            ) % (path, host, port, size_bytes, digest)
            sock.sendall(headers.encode('ascii'))
            status, response_headers = _read_http_response(response_file)
            if status != 100:
                raise _read_http_error(
                    response_file, status, response_headers,
                    'update_rejected', 'server 在 body 前拒绝 update',
                    round_id, node_id)
            with open(update_path, 'rb') as fh:
                while True:
                    chunk = fh.read(64 * 1024)
                    if not chunk:
                        break
                    sock.sendall(chunk)
            status, response_headers = _read_http_response(response_file)
            if status != 201:
                raise _read_http_error(
                    response_file, status, response_headers,
                    'update_submit_failed', 'server 拒绝 update 提交',
                    round_id, node_id)
            content_lengths = response_headers.get('content-length', [])
            if content_lengths != ['0']:
                raise FLRuntimeError(
                    'update_submit_failed', 'server 最终响应不是空 body 201',
                    round_id=round_id, node_id=node_id)
        except FLRuntimeError:
            raise
        except (OSError, socket.timeout) as exc:
            raise FLRuntimeError(
                'update_submit_failed', 'update HTTP 提交失败',
                round_id=round_id, node_id=node_id) from exc
        finally:
            if response_file is not None:
                response_file.close()
            if sock is not None:
                sock.close()
            with self._operation_condition:
                self._operation_socket = None
                self._operation_active = False
                self._operation_condition.notify_all()

    def close(self):
        if self._state == 'closed':
            return
        self.ready = False
        self._state = 'stopping'
        with self._operation_condition:
            if self._operation_socket is not None:
                try:
                    self._operation_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            while self._operation_active:
                self._operation_condition.wait()
        if self._uftpd_process is not None:
            _stop_process(self._uftpd_process)
            self._uftpd_process = None
        if self._uftpd_stderr is not None:
            self._uftpd_stderr.close()
            self._uftpd_stderr = None
        self._state = 'closed'


def _header_values(headers, name):
    get_all = getattr(headers, 'get_all', None)
    if get_all is not None:
        return get_all(name, [])
    value = headers.get(name)
    return [] if value is None else [value]


def _format_uid(uid):
    return '0x%08X' % uid


def _parse_content_digest(value):
    prefix = 'sha-256=:'
    if not value or not value.startswith(prefix) or not value.endswith(':'):
        return None
    try:
        digest = base64.b64decode(value[len(prefix):-1], validate=True)
    except (ValueError, TypeError):
        return None
    return digest if len(digest) == 32 else None


def _new_uftp_status_path(round_dir):
    return os.path.join(round_dir, 'uftp-%s.status' % uuid.uuid4())


def _validate_uftp_status(path, expected_uids, expected_files):
    connect = []
    results = []
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            for raw_line in fh:
                fields = raw_line.rstrip('\n').split(';')
                if fields[0] == 'CONNECT':
                    if len(fields) != 3:
                        raise ValueError('invalid CONNECT record')
                    connect.append((fields[1], int(fields[2], 16)))
                elif fields[0] == 'RESULT':
                    if len(fields) != 6:
                        raise ValueError('invalid RESULT record')
                    results.append((int(fields[1], 16), fields[2], fields[4]))
    except (OSError, ValueError) as exc:
        raise FLRuntimeError('transport_failed', '无法解析 UFTP status') from exc
    expected_connect = sorted(('success', uid) for uid in expected_uids)
    if sorted(connect) != expected_connect:
        raise FLRuntimeError('transport_failed', 'UFTP client 连接矩阵不完整')
    expected = sorted(
        (uid, filename, 'copy')
        for uid in expected_uids
        for filename in expected_files)
    if sorted(results) != expected:
        raise FLRuntimeError('transport_failed', 'UFTP 文件结果矩阵不完整')


def _read_http_response(response_file):
    status_line = response_file.readline().decode('iso-8859-1').rstrip('\r\n')
    parts = status_line.split(' ', 2)
    if len(parts) < 2 or not parts[0].startswith('HTTP/'):
        raise FLRuntimeError('invalid_http_response', 'HTTP 响应状态行无效')
    headers = {}
    while True:
        line = response_file.readline()
        if line in (b'\r\n', b'\n'):
            break
        if not line:
            raise FLRuntimeError('invalid_http_response', 'HTTP 响应 headers 不完整')
        try:
            name, value = line.decode('iso-8859-1').split(':', 1)
        except ValueError as exc:
            raise FLRuntimeError(
                'invalid_http_response', 'HTTP 响应 header 无效') from exc
        headers.setdefault(name.strip().lower(), []).append(value.strip())
    try:
        return int(parts[1]), headers
    except ValueError as exc:
        raise FLRuntimeError('invalid_http_response', 'HTTP 响应状态码无效') from exc


def _read_http_error(
        response_file, status, headers, default_code, default_message,
        round_id, node_id):
    content_lengths = headers.get('content-length', [])
    if len(content_lengths) != 1:
        return FLRuntimeError(
            default_code, '%s，HTTP %d' % (default_message, status),
            round_id=round_id, node_id=node_id)
    try:
        content_length = int(content_lengths[0])
    except ValueError:
        content_length = -1
    if content_length < 0 or content_length > 16 * 1024:
        return FLRuntimeError(
            default_code, '%s，HTTP %d' % (default_message, status),
            round_id=round_id, node_id=node_id)
    try:
        body = response_file.read(content_length)
        value = json.loads(body.decode('utf-8'))
        error_code = value['error_code']
        error_message = value['error_message']
        if (not isinstance(error_code, str) or not error_code or
                not isinstance(error_message, str)):
            raise ValueError('invalid error body')
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        return FLRuntimeError(
            default_code, '%s，HTTP %d' % (default_message, status),
            round_id=round_id, node_id=node_id)
    return FLRuntimeError(
        error_code, error_message, round_id=round_id, node_id=node_id)


def _stop_process(process):
    if process.poll() is not None:
        process.wait()
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _process_listens_udp(pid, port):
    socket_inodes = set()
    try:
        for name in os.listdir('/proc/%d/fd' % pid):
            try:
                target = os.readlink('/proc/%d/fd/%s' % (pid, name))
            except OSError:
                continue
            if target.startswith('socket:['):
                socket_inodes.add(target[8:-1])
        with open('/proc/net/udp', 'r', encoding='ascii') as fh:
            next(fh)
            for line in fh:
                fields = line.split()
                local_port = int(fields[1].split(':')[1], 16)
                if local_port == port and fields[9] in socket_inodes:
                    return True
    except (OSError, ValueError, StopIteration):
        return False
    return False
