#!/usr/bin/env python
# -*- coding: utf-8 -*-

import argparse
import importlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time

from .errors import FLRuntimeError
from .role import ClientRole, ServerRole


_COMMON_FIELDS = {
    'schema_version', 'role', 'work_dir', 'node_id', 'uftp_port',
    'max_update_size_bytes', 'link_args',
}
_SERVER_FIELDS = {
    'participant_node_ids', 'participant_uftp_uids', 'server_uftp_uid',
    'http_host', 'http_port', 'uftp_bind_host', 'uftp_multicast_host',
}
_CLIENT_FIELDS = {
    'uftp_uid', 'server_http_host', 'server_http_port', 'uftp_bind_host',
}


class LinkProcess(object):
    def __init__(self, command, readiness_probe=None, startup_timeout=5,
                 stop_grace_period=2):
        self.command = list(command)
        self.readiness_probe = readiness_probe
        self.startup_timeout = startup_timeout
        self.stop_grace_period = stop_grace_period
        self._process = None

    def start(self):
        if self._process is not None:
            raise FLRuntimeError(
                'link_process_already_started', '链路进程已经启动')
        try:
            self._process = subprocess.Popen(
                self.command, stdin=subprocess.DEVNULL)
        except OSError as exc:
            self._process = None
            raise FLRuntimeError(
                'link_process_start_failed', '链路进程启动失败') from exc
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            return_code = self._process.poll()
            if return_code is not None:
                self._process.wait()
                self._process = None
                raise FLRuntimeError(
                    'link_process_start_failed',
                    '链路进程启动期退出，退出码为 %d' % return_code)
            if self.readiness_probe is None or self.readiness_probe():
                return
            time.sleep(0.02)
        self.close()
        raise FLRuntimeError(
            'link_process_start_failed', '链路进程未进入 ready 状态')

    def poll(self):
        if self._process is None:
            return None
        return self._process.poll()

    def close(self):
        process = self._process
        if process is None:
            return
        self._process = None
        if process.poll() is not None:
            process.wait()
            return
        process.terminate()
        try:
            process.wait(timeout=self.stop_grace_period)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


class RoleService(object):
    def __init__(self, role, link):
        self.role = role
        self.link = link
        self.ready = False
        self._started = False
        self._closed = False

    @property
    def runtime(self):
        return self.role.runtime

    def start(self):
        if self._started or self._closed:
            raise FLRuntimeError(
                'role_service_already_started', '角色服务不能重复启动')
        self._started = True
        try:
            self.link.start()
            runtime = self.role.start()
        except Exception:
            self.close()
            raise
        self.ready = True
        return runtime

    def wait(self, interval=0.2):
        if not self.ready:
            return
        return_code = self.link.poll()
        if return_code is not None:
            self.ready = False
            raise FLRuntimeError(
                'link_process_failed',
                '链路进程意外退出，退出码为 %d' % return_code)
        transport_error = self.role.poll_failure()
        if transport_error is not None:
            self.ready = False
            raise transport_error
        time.sleep(interval)

    def close(self):
        if self._closed:
            return
        self.ready = False
        self._closed = True
        try:
            self.role.close_transport()
        finally:
            try:
                self.link.close()
            finally:
                self.role.close_runtime()


def load_role_service(path, expected_role=None):
    config = _read_config(path)
    role_name = config['role']
    if expected_role is not None and role_name != expected_role:
        raise FLRuntimeError(
            'invalid_configuration', '配置角色与入口角色不一致')

    dependencies = ('wfb_v6_uplink', 'uftp' if role_name == 'server' else 'uftpd')
    executables = {}
    for name in dependencies:
        executable = shutil.which(name)
        if not executable:
            raise FLRuntimeError(
                'transport_unavailable', '缺少 %s 可执行文件' % name)
        executables[name] = executable

    link_args = config['link_args']
    tun_name = _single_option_value(link_args, '--tun-name')
    if role_name == 'server':
        known_client_node_ids = _parse_known_client_node_ids(
            _single_option_value(link_args, '--known-clients'))
        if not set(config['participant_node_ids']).issubset(known_client_node_ids):
            raise FLRuntimeError(
                'invalid_configuration', '参与节点不属于链路静态已知 client 集')
    tun_path = os.path.join('/sys/class/net', tun_name)
    if os.path.exists(tun_path):
        raise FLRuntimeError(
            'link_interface_exists', '链路 TUN 已存在，拒绝复用旧接口')
    link = LinkProcess([
        executables['wfb_v6_uplink'],
        '--role', role_name,
        '--node-id', str(config['node_id']),
    ] + link_args, readiness_probe=lambda: os.path.isdir(tun_path))
    try:
        if role_name == 'server':
            role = ServerRole(
                work_dir=config['work_dir'],
                participant_node_id=config['participant_node_ids'],
                participant_uftp_uid=config['participant_uftp_uids'],
                server_uftp_uid=config['server_uftp_uid'],
                uftp_port=config['uftp_port'],
                http_host=config['http_host'],
                http_port=config['http_port'],
                uftp_bind_host=config['uftp_bind_host'],
                uftp_multicast_host=config['uftp_multicast_host'],
                max_update_size_bytes=config['max_update_size_bytes'],
            )
        else:
            role = ClientRole(
                work_dir=config['work_dir'],
                node_id=config['node_id'],
                uftp_uid=config['uftp_uid'],
                uftp_port=config['uftp_port'],
                server_http_address=(
                    config['server_http_host'], config['server_http_port']),
                max_update_size_bytes=config['max_update_size_bytes'],
                uftp_bind_host=config['uftp_bind_host'],
            )
    except FLRuntimeError:
        raise
    except (TypeError, ValueError) as exc:
        raise FLRuntimeError(
            'invalid_configuration', '角色专属配置无效') from exc
    return RoleService(role, link)


def _read_config(path):
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            config = json.load(fh)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FLRuntimeError(
            'invalid_configuration', '角色服务配置无法读取') from exc
    if not isinstance(config, dict):
        raise FLRuntimeError('invalid_configuration', '角色服务配置结构无效')
    role = config.get('role')
    if role == 'server':
        config.setdefault('uftp_bind_host', '127.0.0.1')
        config.setdefault('uftp_multicast_host', '127.0.0.1')
    elif role == 'client':
        config.setdefault('uftp_bind_host', '127.0.0.1')
    allowed = _COMMON_FIELDS | (
        _SERVER_FIELDS if role == 'server' else _CLIENT_FIELDS)
    if role not in ('server', 'client') or set(config) != allowed:
        raise FLRuntimeError('invalid_configuration', '角色服务配置字段无效')
    if config.get('schema_version') != 1:
        raise FLRuntimeError('invalid_configuration', '角色服务配置版本无效')
    if (not isinstance(config.get('work_dir'), str) or
            not os.path.isabs(config['work_dir'])):
        raise FLRuntimeError('invalid_configuration', '工作目录必须是绝对路径')
    for name in ('node_id', 'uftp_port', 'max_update_size_bytes'):
        if type(config.get(name)) is not int or config[name] <= 0:
            raise FLRuntimeError(
                'invalid_configuration', '角色服务整数参数无效')
    for name in ('uftp_bind_host', 'uftp_multicast_host'):
        if name in config and (not isinstance(config[name], str) or not config[name]):
            raise FLRuntimeError(
                'invalid_configuration', 'UFTP 地址配置无效')
    link_args = config.get('link_args')
    if (not isinstance(link_args, list) or not link_args or
            any(not isinstance(value, str) or not value for value in link_args)):
        raise FLRuntimeError('invalid_configuration', '链路参数无效')
    if '--role' in link_args or '--node-id' in link_args:
        raise FLRuntimeError(
            'invalid_configuration', '链路角色参数不能重复指定')
    _single_option_value(link_args, '--tun-name')
    return config


def _single_option_value(arguments, option):
    positions = [index for index, value in enumerate(arguments) if value == option]
    if (len(positions) != 1 or positions[0] + 1 >= len(arguments) or
            arguments[positions[0] + 1].startswith('--')):
        raise FLRuntimeError(
            'invalid_configuration', '链路参数缺少唯一 %s' % option)
    return arguments[positions[0] + 1]


def _parse_known_client_node_ids(value):
    items = value.split(',')
    try:
        node_ids = [int(item) for item in items]
    except ValueError as exc:
        raise FLRuntimeError(
            'invalid_configuration', '链路 known-clients 参数无效') from exc
    if (any(not item.isascii() or not item.isdigit() for item in items) or
            any(node_id <= 0 or node_id > 255 for node_id in node_ids) or
            len(set(node_ids)) != len(node_ids)):
        raise FLRuntimeError(
            'invalid_configuration', '链路 known-clients 参数无效')
    return set(node_ids)


def _run_algorithm(algorithm, runtime, config, errors, stop_event):
    try:
        algorithm(runtime, config)
    except FLRuntimeError as exc:
        errors.append(exc)
    except Exception:
        errors.append(FLRuntimeError(
            'algorithm_failed', '算法入口执行失败'))
    finally:
        stop_event.set()


def _read_algorithm_config(path):
    if path is None:
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            config = json.load(fh)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FLRuntimeError(
            'invalid_configuration', '算法配置无法读取') from exc
    if not isinstance(config, dict):
        raise FLRuntimeError('invalid_configuration', '算法配置结构无效')
    return config


def _load_algorithm(spec):
    if spec is None:
        return None
    if ':' not in spec:
        raise FLRuntimeError('invalid_configuration', '算法入口格式无效')
    module_name, callable_name = spec.split(':', 1)
    if not module_name or not callable_name:
        raise FLRuntimeError('invalid_configuration', '算法入口格式无效')
    try:
        module = importlib.import_module(module_name)
        target = module
        for part in callable_name.split('.'):
            if not part:
                raise AttributeError(part)
            target = getattr(target, part)
    except (ImportError, AttributeError) as exc:
        raise FLRuntimeError('invalid_configuration', '算法入口无法导入') from exc
    if not callable(target):
        raise FLRuntimeError('invalid_configuration', '算法入口不可调用')
    return target


def _notify_ready():
    address = os.environ.get('NOTIFY_SOCKET')
    if not address:
        return
    if address.startswith('@'):
        address = '\0' + address[1:]
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        sock.connect(address)
        sock.sendall(b'READY=1')
    finally:
        sock.close()


def main(role=None):
    parser = argparse.ArgumentParser(description='WFB-ng FL 角色服务')
    parser.add_argument('--config', required=True, help='角色服务 JSON 配置')
    parser.add_argument('--algorithm', help='算法入口，格式为 package.module:callable')
    parser.add_argument('--algorithm-config', help='算法 JSON object 配置')
    args = parser.parse_args()
    service = None
    algorithm_thread = None
    algorithm_error = []
    stop_event = threading.Event()

    def stop(signum, frame):
        stop_event.set()

    previous_sigterm = signal.getsignal(signal.SIGTERM)
    previous_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        algorithm = _load_algorithm(args.algorithm)
        algorithm_config = _read_algorithm_config(args.algorithm_config)
        service = load_role_service(args.config, expected_role=role)
        runtime = service.start()
        _notify_ready()
        if algorithm is not None:
            algorithm_thread = threading.Thread(
                target=_run_algorithm,
                args=(algorithm, runtime, algorithm_config, algorithm_error, stop_event),
                name='fl-algorithm', daemon=True)
            algorithm_thread.start()
        while not stop_event.is_set():
            if algorithm_thread is not None and not algorithm_thread.is_alive():
                stop_event.set()
                break
            service.wait(0.2)
        if algorithm_thread is not None:
            algorithm_thread.join(0)
            if algorithm_error:
                raise algorithm_error[0]
    except FLRuntimeError as exc:
        print('%s: %s' % (exc.error_code, exc.error_message), file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        signal.signal(signal.SIGINT, previous_sigint)
        if service is not None:
            service.close()
    return 0


def server_main():
    sys.exit(main('server'))


def client_main():
    sys.exit(main('client'))
