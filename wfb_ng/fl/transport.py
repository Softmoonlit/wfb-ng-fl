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
from dataclasses import dataclass

from .artifacts import write_json_atomic
from .errors import FLRuntimeError


@dataclass(frozen=True)
class RoundContext:
    round_id: str
    participant_node_id: int
    round_dir: str
    max_update_size_bytes: int
    failure_callback: object


class ServerTransport(object):
    def __init__(self, participant_uftp_uid, server_uftp_uid, uftp_port,
                 http_host='127.0.0.1', http_port=0, io_timeout=10):
        self.participant_uftp_uid = participant_uftp_uid
        self.server_uftp_uid = server_uftp_uid
        self.uftp_port = uftp_port
        self.http_host = http_host
        self.http_port = http_port
        self.io_timeout = io_timeout
        self.ready = False
        self._context = None
        self._context_lock = threading.Lock()
        self._upload_active = False
        self._update_event = threading.Event()
        self._http_server = None
        self._http_thread = None
        self._uftp_process = None

    @property
    def http_address(self):
        if self._http_server is None:
            return self.http_host, self.http_port
        return self._http_server.server_address

    def start(self):
        if not shutil.which('uftp'):
            raise FLRuntimeError('transport_unavailable', '缺少 uftp 可执行文件')
        transport = self

        class UpdateHandler(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

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
                self.connection.settimeout(transport.io_timeout)
                try:
                    transport._receive_update(self, self._upload_context)
                finally:
                    transport._release_upload()

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
            daemon_threads = True
            allow_reuse_address = True

        self._http_server = UpdateServer((self.http_host, self.http_port), UpdateHandler)
        self._http_thread = threading.Thread(
            target=self._http_server.serve_forever,
            name='fl-update-listener', daemon=True)
        self._http_thread.start()
        self.ready = True

    def install_round(self, round_id, participant_node_id, round_dir,
                      max_update_size_bytes, failure_callback):
        with self._context_lock:
            self._context = RoundContext(
                round_id, participant_node_id, round_dir,
                max_update_size_bytes, failure_callback)
            self._update_event.clear()

    def publish_model(self, round_id, model_path, manifest_path):
        status_path = os.path.join(os.path.dirname(model_path), 'uftp.status')
        log_path = os.path.join(os.path.dirname(model_path), 'uftp.log')
        if os.path.exists(status_path):
            raise FLRuntimeError('transport_failed', 'UFTP status 文件已存在')
        command = [
            shutil.which('uftp'),
            '-q',
            '-I', '127.0.0.1',
            '-M', '127.0.0.1',
            '-p', str(self.uftp_port),
            '-U', _format_uid(self.server_uftp_uid),
            '-H', _format_uid(self.participant_uftp_uid),
            '-Y', 'none',
            '-R', '10000',
            '-r', '0.1:0.01:2.0',
            '-s', '10',
            '-L', log_path,
            '-S', status_path,
            '-D', round_id,
            os.path.basename(model_path),
            os.path.basename(manifest_path),
        ]
        self._uftp_process = subprocess.Popen(
            command, cwd=os.path.dirname(model_path), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return_code = self._uftp_process.wait()
        self._uftp_process = None
        if return_code != 0:
            raise FLRuntimeError(
                'transport_failed', 'UFTP 退出码为 %d' % return_code,
                round_id=round_id)
        _validate_uftp_status(
            status_path, self.participant_uftp_uid,
            ('%s/model.bin' % round_id, '%s/model.manifest.json' % round_id))

    def wait_for_update(self, timeout):
        self._update_event.wait(timeout)

    def close(self):
        self.ready = False
        if self._uftp_process is not None:
            _stop_process(self._uftp_process)
            self._uftp_process = None
        if self._http_server is not None:
            self._http_server.shutdown()
            self._http_server.server_close()
            self._http_server = None
        if self._http_thread is not None:
            self._http_thread.join(2)
            self._http_thread = None

    def _reserve_upload(self, handler):
        with self._context_lock:
            context = self._context
            if context is None:
                return 404, 'round_not_found', '轮次不存在'
            expected_path = '/v1/rounds/%s/updates/%d' % (
                context.round_id, context.participant_node_id)
            if handler.command != 'PUT' or handler.path != expected_path:
                return 404, 'round_not_found', '轮次或节点不存在'
            if self._upload_active:
                return 409, 'upload_in_progress', '已有 update 正在上传'
            try:
                content_length = int(handler.headers['Content-Length'])
            except (KeyError, TypeError, ValueError):
                return 411, 'length_required', '缺少合法 Content-Length'
            if content_length < 0 or content_length > context.max_update_size_bytes:
                return 413, 'update_too_large', 'update 超过大小限制'
            if handler.headers.get('Content-Type') != 'application/octet-stream':
                return 415, 'unsupported_media_type', 'Content-Type 无效'
            digest = _parse_content_digest(handler.headers.get('Content-Digest'))
            if digest is None:
                return 400, 'invalid_content_digest', 'Content-Digest 无效'
            self._upload_active = True
            handler._upload_context = (context, content_length, digest)
            return None

    def _release_upload(self):
        with self._context_lock:
            self._upload_active = False

    def _fail_accepted_upload(self, upload_context, error_code, error_message):
        context, _, _ = upload_context
        try:
            context.failure_callback(
                context.round_id, context.participant_node_id,
                error_code, error_message)
        finally:
            self._release_upload()

    def _receive_update(self, handler, upload_context):
        context, content_length, expected_digest = upload_context
        update_dir = os.path.join(
            context.round_dir, 'updates', str(context.participant_node_id))
        os.makedirs(update_dir, exist_ok=True)
        temp_path = None
        try:
            digest = hashlib.sha256()
            received = 0
            with tempfile.NamedTemporaryFile(
                    mode='wb', dir=update_dir, prefix='.update.bin.',
                    delete=False) as target:
                temp_path = target.name
                while received < content_length:
                    chunk = handler.rfile.read(min(64 * 1024, content_length - received))
                    if not chunk:
                        raise FLRuntimeError('upload_incomplete', 'update body 提前结束')
                    target.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                target.flush()
                os.fsync(target.fileno())
            if digest.digest() != expected_digest:
                error_code = 'digest_mismatch'
                error_message = 'update 摘要不匹配'
                context.failure_callback(
                    context.round_id, context.participant_node_id,
                    error_code, error_message)
                handler._send_error(422, error_code, error_message)
                return

            update_path = os.path.join(update_dir, 'update.bin')
            os.replace(temp_path, update_path)
            temp_path = None
            write_json_atomic(os.path.join(update_dir, 'update.manifest.json'), {
                'schema_version': 1,
                'artifact_type': 'update',
                'round_id': context.round_id,
                'node_id': context.participant_node_id,
                'size_bytes': content_length,
                'sha256': digest.hexdigest(),
            })
            self._update_event.set()
            handler.send_response(201)
            handler.send_header('Content-Length', '0')
            handler.send_header('Connection', 'close')
            handler.end_headers()
            handler.close_connection = True
        except (FLRuntimeError, OSError, socket.timeout) as exc:
            error_code = 'upload_incomplete'
            error_message = 'update body 接收失败'
            context.failure_callback(
                context.round_id, context.participant_node_id,
                error_code, error_message)
            try:
                handler._send_error(422, error_code, error_message)
            except OSError:
                pass
        finally:
            if temp_path is not None:
                try:
                    os.unlink(temp_path)
                except FileNotFoundError:
                    pass


class ClientTransport(object):
    def __init__(self, work_dir, uftp_uid, uftp_port, server_http_address,
                 io_timeout=10):
        self.work_dir = os.path.abspath(work_dir)
        self.uftp_uid = uftp_uid
        self.uftp_port = uftp_port
        self.server_http_address = server_http_address
        self.io_timeout = io_timeout
        self.ready = False
        self._inbox_dir = os.path.join(self.work_dir, 'inbox')
        self._temp_dir = os.path.join(self.work_dir, 'uftp-tmp')
        self._uftpd_process = None
        self._uftpd_stderr = None

    def start(self):
        executable = shutil.which('uftpd')
        if not executable:
            raise FLRuntimeError('transport_unavailable', '缺少 uftpd 可执行文件')
        os.makedirs(self._inbox_dir, exist_ok=True)
        os.makedirs(self._temp_dir, exist_ok=True)
        stderr_path = os.path.join(self.work_dir, 'uftpd.log')
        self._uftpd_stderr = open(stderr_path, 'ab')
        command = [
            executable,
            '-d',
            '-q',
            '-I', '127.0.0.1',
            '-p', str(self.uftp_port),
            '-U', _format_uid(self.uftp_uid),
            '-D', self._inbox_dir,
            '-T', self._temp_dir,
            '-F', os.path.join(self.work_dir, 'uftpd.status'),
        ]
        self._uftpd_process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=self._uftpd_stderr)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self._uftpd_process.poll() is not None:
                raise FLRuntimeError('transport_start_failed', 'uftpd 启动失败')
            if _process_listens_udp(self._uftpd_process.pid, self.uftp_port):
                self.ready = True
                return
            time.sleep(0.02)
        raise FLRuntimeError('transport_start_failed', 'uftpd 未进入监听状态')

    def wait_for_model_candidate(self):
        while self.ready:
            try:
                names = sorted(os.listdir(self._inbox_dir))
            except FileNotFoundError:
                names = []
            for name in names:
                candidate = os.path.join(self._inbox_dir, name)
                if (os.path.isdir(candidate) and
                        os.path.isfile(os.path.join(candidate, 'model.manifest.json'))):
                    return candidate
            if self._uftpd_process.poll() is not None:
                raise FLRuntimeError('transport_failed', 'uftpd 意外退出')
            time.sleep(0.02)
        raise FLRuntimeError('transport_not_ready', 'Transport 尚未 ready')

    def submit_update(self, round_id, node_id, update_path, size_bytes, digest_hex):
        host, port = self.server_http_address
        sock = socket.create_connection((host, port), timeout=self.io_timeout)
        sock.settimeout(self.io_timeout)
        response_file = sock.makefile('rb')
        try:
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
                raise FLRuntimeError(
                    'update_rejected', 'server 在 body 前返回 HTTP %d' % status,
                    round_id=round_id, node_id=node_id)
            with open(update_path, 'rb') as fh:
                while True:
                    chunk = fh.read(64 * 1024)
                    if not chunk:
                        break
                    sock.sendall(chunk)
            status, response_headers = _read_http_response(response_file)
            content_length = int(response_headers.get('content-length', '0'))
            if status != 201 or content_length != 0:
                raise FLRuntimeError(
                    'update_submit_failed', 'server 最终响应不是空 body 201',
                    round_id=round_id, node_id=node_id)
        finally:
            response_file.close()
            sock.close()

    def close(self):
        self.ready = False
        if self._uftpd_process is not None:
            _stop_process(self._uftpd_process)
            self._uftpd_process = None
        if self._uftpd_stderr is not None:
            self._uftpd_stderr.close()
            self._uftpd_stderr = None


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


def _validate_uftp_status(path, expected_uid, expected_files):
    connect = []
    results = []
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            for raw_line in fh:
                fields = raw_line.rstrip('\n').split(';')
                if fields[0] == 'CONNECT' and len(fields) >= 3:
                    connect.append((fields[1], int(fields[2], 16)))
                elif fields[0] == 'RESULT' and len(fields) >= 6:
                    results.append((int(fields[1], 16), fields[2], fields[4]))
    except (OSError, ValueError) as exc:
        raise FLRuntimeError('transport_failed', '无法解析 UFTP status') from exc
    if connect != [('success', expected_uid)]:
        raise FLRuntimeError('transport_failed', 'UFTP client 连接矩阵不完整')
    expected = [(expected_uid, filename, 'copy') for filename in expected_files]
    if results != expected:
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
        name, value = line.decode('iso-8859-1').split(':', 1)
        headers[name.strip().lower()] = value.strip()
    return int(parts[1]), headers


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
