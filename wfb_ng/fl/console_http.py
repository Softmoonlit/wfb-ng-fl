"""Same-origin console HTTP adapter and management listener lifecycle."""
import ipaddress
import json
import logging
from email.parser import Parser
from pathlib import Path
import re
import socket
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, BinaryIO, Callable, Dict, Optional, Tuple, cast
from urllib.parse import urlparse

from pyroute2 import IPRoute

from .console import ConsoleApplicationService, RadioPreparationError
from .errors import FLRuntimeError
from .model_library import ModelLibrary, ModelLibraryError

logger = logging.getLogger(__name__)
ASSETS = Path(__file__).with_name('static')
STATIC_ROUTES = {'/': ('index.html', 'text/html; charset=utf-8'),
                 '/radio': ('radio.html', 'text/html; charset=utf-8'),
                 '/assets/radio.js': ('radio.js', 'text/javascript; charset=utf-8'),
                 '/assets/radio.css': ('radio.css', 'text/css; charset=utf-8'),
                 '/models': ('models.html', 'text/html; charset=utf-8'),
                 '/assets/models.js': ('models.js', 'text/javascript; charset=utf-8'),
                 '/assets/models.css': ('models.css', 'text/css; charset=utf-8'),
                 '/assets/console.css': ('console.css', 'text/css; charset=utf-8'),
                 '/assets/console.js': ('console.js', 'text/javascript; charset=utf-8')}
REPLACED_ROUTES = {'/api/v1/jobs/start': '/api/v1/jobs',
                   '/api/v1/jobs/abort': '/api/v1/jobs/{job_id}/abort',
                   '/api/v1/radio/reconfigure': '/api/v1/radio/config/apply',
                   '/api/v1/survey': '/api/v1/radio/config'}


class MultipartFileStream:
    """One bounded file part; validate the closing frame before publication."""

    def __init__(self, handler: BaseHTTPRequestHandler) -> None:
        lengths: list = handler.headers.get_all('Content-Length', [])
        types: list = handler.headers.get_all('Content-Type', [])
        if (len(lengths) != 1 or re.fullmatch('[0-9]{1,12}', lengths[0]) is None
                or handler.headers.get_all('Transfer-Encoding') or len(types) != 1):
            raise ModelLibraryError('INVALID_REQUEST', '必须提供唯一合法的 Content-Length 与 Content-Type')
        length = int(lengths[0])
        if length > ModelLibrary.MAX_FILE_BYTES + 16384:
            raise ModelLibraryError('MODEL_TOO_LARGE', '单文件不能超过 1 GiB', 413)
        content_type = Parser().parsestr('Content-Type: ' + types[0] + '\r\n\r\n')
        boundary = content_type.get_boundary()
        if (content_type.get_content_type() != 'multipart/form-data' or not boundary
                or re.fullmatch(r"[0-9A-Za-z'()+_,./:=? -]{1,70}", boundary) is None
                or boundary.endswith(' ')):
            raise ModelLibraryError('INVALID_MULTIPART', '必须上传 multipart/form-data 单个文件')
        self._stream = handler.rfile
        self._remaining = length
        self._marker = b'\r\n--' + boundary.encode('ascii')
        self._footer = self._marker + b'--\r\n'
        self._tail = b''
        expected = b'--' + boundary.encode('ascii') + b'\r\n'
        if self._line() != expected:
            raise ModelLibraryError('INVALID_MULTIPART', 'multipart 起始边界非法')
        header_lines = []
        header_size = 0
        while True:
            line = self._line()
            header_size += len(line)
            if header_size > 8192:
                raise ModelLibraryError('INVALID_MULTIPART', '文件部分头过长')
            if line == b'\r\n':
                break
            header_lines.append(line)
        try:
            headers = Parser().parsestr(b''.join(header_lines).decode('utf-8') + '\r\n')
        except UnicodeError as exc:
            raise ModelLibraryError('INVALID_MULTIPART', '文件部分头编码非法') from exc
        filename = headers.get_filename()
        if (headers.defects or len(headers.get_all('Content-Disposition', [])) != 1
                or headers.get_content_disposition() != 'form-data'
                or headers.get_param('name', header='content-disposition') != 'file'
                or not filename or headers.get('Content-Transfer-Encoding')):
            raise ModelLibraryError('INVALID_MULTIPART', '必须提供一个名为 file 的文件部分')
        self.filename = filename
        self.size = self._remaining - len(self._footer)
        if self.size < 0:
            raise ModelLibraryError('INVALID_MULTIPART', 'multipart 长度非法')
        self._file_remaining = self.size

    def _line(self) -> bytes:
        line = self._stream.readline(min(8193, self._remaining + 1))
        self._remaining -= len(line)
        if self._remaining < 0 or not line.endswith(b'\r\n'):
            raise ModelLibraryError('INVALID_MULTIPART', 'multipart 头不完整或格式非法')
        return line

    def read(self, size: int) -> bytes:
        chunk = self._stream.read(min(size, self._file_remaining))
        self._file_remaining -= len(chunk)
        combined = self._tail + chunk
        if self._marker + b'\r\n' in combined or self._marker + b'--' in combined:
            raise ModelLibraryError('INVALID_MULTIPART', '只能上传一个文件部分')
        self._tail = combined[-(len(self._marker) + 1):]
        return chunk

    def finish(self) -> None:
        if self._file_remaining or self._stream.read(len(self._footer)) != self._footer:
            raise ModelLibraryError('INVALID_MULTIPART', 'multipart 结束边界不完整或格式非法')


class ConsoleHTTPServer(ThreadingHTTPServer):
    # Snapshot requests may wait for core work; they must not hold up listener close.
    daemon_threads = True
    application: ConsoleApplicationService
    expected_host: Optional[str] = None

    def get_request(self) -> Tuple[socket.socket, Any]:
        connection, address = super().get_request()
        connection.settimeout(1.0)
        return connection, address


class ConsoleRequestHandler(BaseHTTPRequestHandler):
    """Own Web routes; IPC handlers are deliberately outside this adapter."""

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug(format, *args)

    def _send(self, status: int, payload: bytes, content_type: str,
              allow: Optional[str] = None) -> None:
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        if allow:
            self.send_header('Allow', allow)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(payload)

    def _error(self, status: int, code: str, message: str,
               details: Optional[Dict[str, Any]] = None, allow: Optional[str] = None) -> None:
        self._send(status, json.dumps({'error': {
            'code': code, 'message': message, 'details': details or {},
            'request_id': str(uuid.uuid4()),
        }}).encode(), 'application/json; charset=utf-8', allow)

    def send_error(self, code: int, message: Optional[str] = None,
                   explain: Optional[str] = None) -> None:
        # BaseHTTPRequestHandler dispatches arbitrary unsupported verbs here.
        # Route them through the same API error mapping as standard methods.
        if code == 501:
            self._dispatch()
        else:
            self._error(code, 'INVALID_REQUEST', 'HTTP 请求非法')

    def _read_json(self) -> Any:
        lengths: list = self.headers.get_all('Content-Length', [])
        types: list = self.headers.get_all('Content-Type', [])
        if (len(lengths) != 1 or re.fullmatch('[0-9]{1,5}', lengths[0]) is None
                or not 0 < int(lengths[0]) <= 8192 or self.headers.get_all('Transfer-Encoding')
                or len(types) != 1 or types[0].split(';')[0].strip().lower() != 'application/json'):
            raise RadioPreparationError('INVALID_REQUEST', '必须提供不超过 8 KiB 的 JSON 请求体')
        def unique_object(pairs: list) -> dict:
            result: dict = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('重复 JSON 字段')
                result[key] = value
            return result
        try:
            data = self.rfile.read(int(lengths[0]))
            if len(data) != int(lengths[0]):
                raise ValueError('请求体不完整')
            return json.loads(data, object_pairs_hook=unique_object)
        except (ValueError, UnicodeError, TimeoutError) as exc:
            raise RadioPreparationError('INVALID_REQUEST', 'JSON 请求体非法或不完整') from exc

    def _dispatch(self) -> None:
        server: ConsoleHTTPServer = self.server  # type: ignore[assignment]
        path = urlparse(self.path).path
        if server.expected_host and self.headers.get('Host') != server.expected_host:
            self._error(400, 'INVALID_HOST', 'Host 必须匹配管理网地址与端口')
            return
        # Each request owns its body and closes after the response.
        self.close_connection = True
        if path in REPLACED_ROUTES:
            self._error(410, 'ROUTE_REPLACED', '该路由已退出 Web 契约',
                        {'replacement': REPLACED_ROUTES[path]})
        elif path in ('/api/v1/state', '/api/v1/status'):
            if self.command != 'GET':
                self._error(405, 'METHOD_NOT_ALLOWED', '该资源仅支持 GET', allow='GET')
                return
            try:
                payload = json.dumps(server.application.snapshot()).encode()
            except Exception:
                logger.exception('Console snapshot unavailable')
                self._error(503, 'STATE_UNAVAILABLE', '暂时无法读取状态')
                return
            self._send(200, payload, 'application/json; charset=utf-8')
        elif path in ('/api/v1/radio/config', '/api/v1/radio/config/validate', '/api/v1/radio/config/apply'):
            method = 'GET' if path == '/api/v1/radio/config' else 'POST'
            if self.command != method:
                self._error(405, 'METHOD_NOT_ALLOWED', f'该资源仅支持 {method}', allow=method)
                return
            try:
                if method == 'GET':
                    result = server.application.radio_configuration()
                elif path.endswith('/validate'):
                    result = server.application.validate_radio_configuration(self._read_json())
                else:
                    result = server.application.apply_radio_configuration(self._read_json())
                self._send(200, json.dumps(result).encode(), 'application/json; charset=utf-8')
            except RadioPreparationError as exc:
                self._error(exc.status, exc.code, str(exc))
            except FLRuntimeError as exc:
                status = 409 if exc.error_code in ('engine_busy', 'radio_target_nodes_not_ready') else 400
                self._error(status, exc.error_code.upper(), str(exc), exc.details)
            except Exception:
                logger.exception('Console radio operation failed')
                self._error(503, 'RADIO_UNAVAILABLE', '射频操作失败，请检查当前状态')
        elif path == '/api/v1/models' or path.startswith('/api/v1/models/'):
            try:
                if path == '/api/v1/models':
                    if self.command == 'GET':
                        result = server.application.list_models()
                        status = 200
                    elif self.command == 'POST':
                        stream = MultipartFileStream(self)
                        result = server.application.upload_model(cast(BinaryIO, stream), stream.size, stream.filename)
                        status = 200 if result['deduplicated'] else 201
                    else:
                        self._error(405, 'METHOD_NOT_ALLOWED', '该资源仅支持 GET、POST', allow='GET, POST')
                        return
                else:
                    if self.command != 'DELETE':
                        self._error(405, 'METHOD_NOT_ALLOWED', '该资源仅支持 DELETE', allow='DELETE')
                        return
                    result = server.application.delete_model(path[len('/api/v1/models/'):])
                    status = 200
                self._send(status, json.dumps(result).encode(), 'application/json; charset=utf-8')
            except ModelLibraryError as exc:
                self._error(exc.status, exc.code, str(exc))
            except (TimeoutError, ConnectionError):
                self._error(400, 'UPLOAD_INTERRUPTED', '上传连接中断，请重新上传')
            except Exception:
                logger.exception('Console model library unavailable')
                self._error(503, 'MODEL_STORAGE_FAILED', '模型库暂时不可用，请重试')
        elif path in STATIC_ROUTES:
            if self.command not in ('GET', 'HEAD'):
                self._error(405, 'METHOD_NOT_ALLOWED', '静态资源仅支持 GET、HEAD', allow='GET, HEAD')
                return
            name, content_type = STATIC_ROUTES[path]
            self._send(200, (ASSETS / name).read_bytes(), content_type)
        else:
            self._error(404, 'NOT_FOUND', '未知资源')

    do_GET = _dispatch
    do_HEAD = _dispatch
    do_POST = _dispatch
    do_PUT = _dispatch
    do_PATCH = _dispatch
    do_DELETE = _dispatch
    do_OPTIONS = _dispatch
    do_TRACE = _dispatch


class ManagementAddressError(OSError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = code


def validate_management_address(host: str, tun_name: str, air_interface: Optional[str]) -> None:
    """Validate IPv4 ownership using existing Linux netlink dependency."""
    address = ipaddress.IPv4Address(host)
    if address.is_loopback or address.is_unspecified or address.is_multicast or address.is_reserved:
        raise ManagementAddressError('WEB_ADDRESS_FORBIDDEN', '管理地址必须为单播非回环 IPv4')
    with IPRoute() as ip:
        # This installed pyroute2 exposes transaction timeout attributes and the
        # socket timeout API, not an IPRoute(timeout=...) constructor parameter.
        ip.get_timeout = 1.0
        ip.get_timeout_exception = TimeoutError
        ip.settimeout(1.0)
        owners = [a['index'] for a in ip.get_addr(family=socket.AF_INET)
                  if a.get_attr('IFA_LOCAL') == host or a.get_attr('IFA_ADDRESS') == host]
        if not owners:
            raise ManagementAddressError('WEB_ADDRESS_NOT_ASSIGNED', '管理地址尚未分配到本机接口')
        for link in ip.get_links(*owners):
            name = link.get_attr('IFLA_IFNAME')
            info = link.get_attr('IFLA_LINKINFO')
            kind = info.get_attr('IFLA_INFO_KIND') if info else None
            if (link['flags'] & 8 or name in (tun_name, air_interface)
                    or name.startswith(('wl', 'tun', 'tap', 'fl-')) or kind in ('tun', 'wireguard')
                    or (Path('/sys/class/net') / name / 'wireless').exists()
                    or (Path('/sys/class/net') / name / 'phy80211').exists()):
                raise ManagementAddressError('WEB_INTERFACE_FORBIDDEN', '管理地址不得归属无线、TUN 或回环接口')


class ManagementWebListener:
    """One worker owns bind, requests and close; stop joins that same owner."""

    def __init__(self, host: Optional[str], port: int, application: ConsoleApplicationService,
                 validate: Callable[[str], None], on_status: Callable[[str, Optional[str]], None]) -> None:
        self.host = host
        self.port = port
        self.application = application
        self.validate = validate
        self.on_status = on_status
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._server: Optional[ConsoleHTTPServer] = None
        self._lock = threading.Lock()

    def start(self) -> None:
        if self.host is None:
            self.on_status('management_web_unavailable', 'WEB_HOST_NOT_CONFIGURED')
            return
        self._thread = threading.Thread(target=self._run, name='wfb-server-management-web', daemon=True)
        self._thread.start()

    def _run(self) -> None:
        assert self.host is not None
        while not self._stop.is_set():
            server = None
            error_code = 'WEB_ADDRESS_CHECK_FAILED'
            try:
                self.validate(self.host)
                if self._stop.is_set():
                    return
                error_code = 'WEB_BIND_FAILED'
                server = ConsoleHTTPServer((self.host, self.port), ConsoleRequestHandler, bind_and_activate=False)
                with self._lock:
                    if self._stop.is_set():
                        return
                    self._server = server
                # Publish ownership before bind; stop can close the socket even
                # when bind/activation is racing it.
                server.server_bind()
                server.server_activate()
                server.application = self.application
                server.expected_host = f'{self.host}:{self.port}'
                server.timeout = 0.25
                # A stop racing bind is observed before any requests are served.
                if not self._stop.is_set():
                    self.on_status('ready', None)
                while not self._stop.is_set():
                    server.handle_request()
                return
            except Exception as exc:
                logger.warning('Management Web unavailable', exc_info=True)
                if not self._stop.is_set():
                    self.on_status('management_web_unavailable', getattr(exc, 'error_code', error_code))
            finally:
                if server is not None:
                    server.server_close()
                with self._lock:
                    self._server = None
            self._stop.wait(1.0)

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            if self._server is not None:
                self._server.server_close()
        if self._thread is not None:
            # Production netlink I/O is bounded above; request workers are
            # independent. A stuck listener is an error, never silent success.
            self._thread.join(timeout=4.0)
            if self._thread.is_alive():
                raise RuntimeError('Management Web worker did not stop within its I/O deadline')
        self.on_status('stopped', None)
