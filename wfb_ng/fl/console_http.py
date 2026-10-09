"""Same-origin console HTTP adapter and management listener lifecycle."""
import ipaddress
import json
import logging
from pathlib import Path
import socket
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import urlparse

from pyroute2 import IPRoute

from .console import ConsoleApplicationService

logger = logging.getLogger(__name__)
ASSETS = Path(__file__).with_name('static')
STATIC_ROUTES = {'/': ('index.html', 'text/html; charset=utf-8'),
                 '/assets/console.css': ('console.css', 'text/css; charset=utf-8'),
                 '/assets/console.js': ('console.js', 'text/javascript; charset=utf-8')}
REPLACED_ROUTES = {'/api/v1/jobs/start': '/api/v1/jobs',
                   '/api/v1/jobs/abort': '/api/v1/jobs/{job_id}/abort',
                   '/api/v1/radio/reconfigure': '/api/v1/radio/config/apply',
                   '/api/v1/survey': '/api/v1/radio/config'}


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

    def _dispatch(self) -> None:
        server: ConsoleHTTPServer = self.server  # type: ignore[assignment]
        path = urlparse(self.path).path
        if server.expected_host and self.headers.get('Host') != server.expected_host:
            self._error(400, 'INVALID_HOST', 'Host 必须匹配管理网地址与端口')
            return
        # No writes in this slice; do not parse retired request bodies.
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
