#!/usr/bin/env python
# -*- coding: utf-8 -*-

import argparse
import ipaddress
import json
import math
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from typing import List, Union

from .algorithm import freeze_algorithm_config
from .errors import FLRuntimeError
from .radio import find_wl_interfaces
from .role import ClientRole, ServerRole
from .transport import validate_server_http_host


_COMMON_FIELDS = {
    'schema_version', 'role', 'work_dir', 'node_id', 'uftp_port',
    'max_update_size_bytes', 'link_args', 'live_observation',
    'observation_path', 'io_timeout_seconds', 'channel', 'channel_width',
}
_OPTIONAL_FIELDS = {'radio_txpower_dbm'}
_SERVER_FIELDS = {
    'participant_node_ids', 'participant_uftp_uids', 'server_uftp_uid',
    'uftp_rate_kbps', 'http_host', 'http_port', 'uftp_bind_host', 'uftp_multicast_host',
    'uftp_private_multicast_host',
}
_CLIENT_FIELDS = {
    'uftp_uid', 'server_http_host', 'server_http_port', 'uftp_bind_host',
    'uftp_multicast_host', 'uftp_private_multicast_host',
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


def _is_ipv4_multicast(host):
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return isinstance(address, ipaddress.IPv4Address) and address.is_multicast


def _is_tun_ready(tun_path):
    if not os.path.isdir(tun_path):
        return False
    flags_path = os.path.join(tun_path, 'flags')
    if os.path.isfile(flags_path):
        try:
            with open(flags_path, 'r') as fh:
                return bool(int(fh.read().strip(), 16) & 1)
        except (OSError, ValueError):
            return False
    return True


class MulticastRoute(object):
    """管理 UFTP 公共和私有组播地址到角色 TUN 的主机路由。"""

    def __init__(self, ip_executable, multicast_hosts, tun_name):
        self.ip_executable = ip_executable
        self.multicast_hosts = tuple(multicast_hosts)
        self.tun_name = tun_name
        self._installed = False

    def setup(self):
        if not all(_is_ipv4_multicast(host) for host in self.multicast_hosts):
            return
        try:
            for multicast_host in self.multicast_hosts:
                subprocess.run(
                    [
                        self.ip_executable, 'route', 'replace',
                        '%s/32' % multicast_host, 'dev', self.tun_name,
                    ], check=True, stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE, text=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise FLRuntimeError(
                'network_route_setup_failed',
                'UFTP 组播路由安装失败') from exc
        self._installed = True

    def close(self):
        # 路由绑定的 TUN 由 LinkProcess 负责销毁，内核会同步移除该路由。
        self._installed = False


class RoleService(object):
    def __init__(self, role, link, multicast_route=None):
        self.role = role
        self.link = link
        self.multicast_route = multicast_route
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
            if self.multicast_route is not None:
                self.multicast_route.setup()
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
                if self.multicast_route is not None:
                    self.multicast_route.close()
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
    if '--air-interface' in link_args:
        air_interface = _single_option_value(link_args, '--air-interface')
        if air_interface not in find_wl_interfaces():
            raise FLRuntimeError(
                'radio_interface_unavailable',
                '链路接口不是绑定 rtl88xxau_wfb 的专用 WFB 网卡')
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
    ip_executable = shutil.which('ip')
    if not ip_executable:
        raise FLRuntimeError('transport_unavailable', '缺少 ip 可执行文件')
    multicast_route = MulticastRoute(
        ip_executable,
        (config['uftp_multicast_host'],
         config['uftp_private_multicast_host']), tun_name)
    link = LinkProcess([
        executables['wfb_v6_uplink'],
        '--role', role_name,
        '--node-id', str(config['node_id']),
    ] + link_args, readiness_probe=lambda: _is_tun_ready(tun_path))
    role: Union[ClientRole, ServerRole]
    try:
        if role_name == 'server':
            role = ServerRole(
                work_dir=config['work_dir'],
                role_node_id=config['node_id'],
                participant_node_id=config['participant_node_ids'],
                participant_uftp_uid=config['participant_uftp_uids'],
                server_uftp_uid=config['server_uftp_uid'],
                uftp_port=config['uftp_port'],
                uftp_rate_kbps=config['uftp_rate_kbps'],
                http_host=config['http_host'],
                http_port=config['http_port'],
                uftp_bind_host=config['uftp_bind_host'],
                uftp_multicast_host=config['uftp_multicast_host'],
                uftp_private_multicast_host=(
                    config['uftp_private_multicast_host']),
                max_update_size_bytes=config['max_update_size_bytes'],
                live_observation=config['live_observation'],
                observation_path=config['observation_path'],
                io_timeout=config['io_timeout_seconds'],
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
                uftp_multicast_host=config['uftp_multicast_host'],
                live_observation=config['live_observation'],
                observation_path=config['observation_path'],
                io_timeout=config['io_timeout_seconds'],
            )
    except FLRuntimeError:
        raise
    except (TypeError, ValueError) as exc:
        raise FLRuntimeError(
            'invalid_configuration', '角色专属配置无效') from exc
    return RoleService(role, link, multicast_route)


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
    required = _COMMON_FIELDS | (
        _SERVER_FIELDS if role == 'server' else _CLIENT_FIELDS)
    allowed = required | _OPTIONAL_FIELDS
    config_keys = set(config)
    if role not in ('server', 'client') or not (config_keys >= required and config_keys <= allowed):
        raise FLRuntimeError('invalid_configuration', '角色服务配置字段无效')
    if type(config['channel']) is not int or config['channel'] <= 0:
        raise FLRuntimeError('invalid_configuration', '角色服务 channel 配置无效')
    if not isinstance(config['channel_width'], str) or not config['channel_width']:
        raise FLRuntimeError('invalid_configuration', '角色服务 channel_width 配置无效')
    if config.get('schema_version') != 1:
        raise FLRuntimeError('invalid_configuration', '角色服务配置版本无效')
    if type(config['live_observation']) is not bool:
        raise FLRuntimeError('invalid_configuration', '实时观测配置无效')
    if (config['observation_path'] is not None and
            (not isinstance(config['observation_path'], str) or
             not os.path.isabs(config['observation_path']))):
        raise FLRuntimeError('invalid_configuration', '实时观测路径无效')
    if (not isinstance(config.get('work_dir'), str) or
            not os.path.isabs(config['work_dir'])):
        raise FLRuntimeError('invalid_configuration', '工作目录必须是绝对路径')
    for name in ('node_id', 'uftp_port', 'max_update_size_bytes'):
        if type(config.get(name)) is not int or config[name] <= 0:
            raise FLRuntimeError(
                'invalid_configuration', '角色服务整数参数无效')
    io_timeout = config['io_timeout_seconds']
    if type(io_timeout) not in (int, float):
        raise FLRuntimeError('invalid_configuration', '角色服务 I/O 期限必须为有限正数')
    try:
        io_timeout = float(io_timeout)
    except OverflowError as exc:
        raise FLRuntimeError(
            'invalid_configuration', '角色服务 I/O 期限必须为有限正数') from exc
    if not math.isfinite(io_timeout) or io_timeout <= 0:
        raise FLRuntimeError('invalid_configuration', '角色服务 I/O 期限必须为有限正数')
    config['io_timeout_seconds'] = io_timeout
    if role == 'client':
        config['server_http_host'] = validate_server_http_host(config['server_http_host'])
    if 'radio_txpower_dbm' in config and (
            type(config['radio_txpower_dbm']) is not int or
            not 1 <= config['radio_txpower_dbm'] <= 63):
        raise FLRuntimeError(
            'invalid_configuration', '角色服务功率必须为 1..63 的驱动固定功率索引')
    if role == 'server' and (type(config['uftp_rate_kbps']) is not int or
                             config['uftp_rate_kbps'] <= 0):
        raise FLRuntimeError('invalid_configuration', 'UFTP 发送速率配置无效')
    for name in ('uftp_bind_host', 'uftp_multicast_host',
                 'uftp_private_multicast_host'):
        if not isinstance(config.get(name), str) or not config[name]:
            raise FLRuntimeError(
                'invalid_configuration', 'UFTP 地址配置无效')
    if (not _is_ipv4_multicast(config['uftp_multicast_host']) or
            not _is_ipv4_multicast(config['uftp_private_multicast_host']) or
            config['uftp_multicast_host'] ==
            config['uftp_private_multicast_host']):
        raise FLRuntimeError(
            'invalid_configuration', 'UFTP 组播地址配置无效')
    link_args = config.get('link_args')
    if (not isinstance(link_args, list) or not link_args or
            any(not isinstance(value, str) or not value for value in link_args)):
        raise FLRuntimeError('invalid_configuration', '链路参数无效')
    if '--role' in link_args or '--node-id' in link_args:
        raise FLRuntimeError(
            'invalid_configuration', '链路角色参数不能重复指定')
    if any((value.startswith('--a') and value != '--air-interface') or
           value.startswith('-W') for value in link_args):
        raise FLRuntimeError(
            'invalid_configuration',
            '链路网卡参数必须使用 --air-interface <name> 形式')
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


def _run_algorithm(algorithm, runtime, config, context, notifications, errors, stop_event):
    try:
        algorithm(runtime, freeze_algorithm_config(config), context)
        if not notifications.prepared_reported or notifications.completed_round != context.rounds:
            raise FLRuntimeError('algorithm_incomplete', '客户端算法提前返回或完成轮数不足')
    except Exception as exc:
        error = exc if isinstance(exc, FLRuntimeError) else FLRuntimeError(
            'algorithm_failed', '算法入口执行失败')
        errors.append(error)
        if not stop_event.is_set():
            try:
                notifications.error('prepare' if not notifications.prepared_reported else 'round',
                                    error.error_code, error.error_message,
                                    notifications.completed_round + 1 if notifications.prepared_reported else None)
            except Exception:
                pass
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


class AlgorithmNotifications:
    """通过沙箱私有 UNIX socket 同步报告算法事实。"""

    def __init__(self, address, job_id, stop_event, timeout=30):
        self.address = address
        self.job_id = job_id
        self.stop_event = stop_event
        self.timeout = timeout
        self.prepared_reported = False
        self.completed_round = 0

    def send(self, event, **fields):
        payload = json.dumps(dict(job_id=self.job_id, event=event, **fields),
                             allow_nan=False, separators=(',', ':')).encode('utf-8')
        if len(payload) > 4096:
            raise FLRuntimeError('control_message_too_large', '本机算法报告超过 4096 字节')
        if not self.address:
            raise FLRuntimeError('notification_unavailable', '缺少本机算法通知通路')
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.bind('\0wfb-fl-' + uuid.uuid4().hex)
            sock.connect(self.address)
            sock.settimeout(0.1)
            sock.send(payload)
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                if self.stop_event.is_set():
                    raise FLRuntimeError('job_stopped', '作业已经停止')
                try:
                    reply = sock.recv(4097)
                except socket.timeout:
                    continue
                try:
                    result = json.loads(reply)
                except (ValueError, UnicodeError) as exc:
                    raise FLRuntimeError('notification_rejected', '本机通知确认无效') from exc
                if len(reply) > 4096 or not isinstance(result, dict) or result.get('accepted') is not True:
                    raise FLRuntimeError('notification_rejected', '本机 Daemon 拒绝算法报告')
                return
        raise FLRuntimeError('notification_timeout', '等待本机算法报告确认超时')

    def prepared(self, report):
        self.send('prepared', report=report)
        self.prepared_reported = True

    def round_complete(self, index):
        self.send('round_complete', round_index=index)
        self.completed_round = index

    def error(self, phase, code, reason, round_index=None):
        return self.send('error', phase=phase, code=code, reason=reason,
                         round_index=round_index)


def main(role=None):
    parser = argparse.ArgumentParser(description='WFB-ng FL 角色服务')
    parser.add_argument('--config', required=True, help='角色服务 JSON 配置')
    parser.add_argument('--algorithm', help='算法入口，格式为 package.module:callable')
    parser.add_argument('--algorithm-config', help='算法 JSON object 配置')
    parser.add_argument('--job-context', help='Daemon 生成的本机作业上下文 JSON')
    args = parser.parse_args()
    service = None
    algorithm_thread = None
    algorithm_error: List[FLRuntimeError] = []
    stop_event = threading.Event()
    signal_received = threading.Event()
    notifications = None
    context = None

    def stop(signum, frame):
        signal_received.set()
        stop_event.set()

    previous_sigterm = signal.getsignal(signal.SIGTERM)
    previous_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        from .algorithm import AlgorithmContext, load_algorithm
        role_config = _read_config(args.config)
        role_name = role or role_config['role']
        if role_name == 'client' and (not args.algorithm or not args.algorithm_config or not args.job_context):
            raise FLRuntimeError('invalid_configuration', '客户端必须显式指定算法、配置及作业上下文')
        if role_name == 'client':
            host = _read_algorithm_config(args.job_context)
            notifications = AlgorithmNotifications(os.environ.get('WFB_FL_NOTIFY_SOCKET'),
                                                  host['job_id'], stop_event,
                                                  timeout=host['preparation_timeout_seconds'])
            os.makedirs(host['artifact_dir'], exist_ok=True)
            context = AlgorithmContext(
                job_id=host['job_id'], node_id=host['node_id'],
                target_nodes=tuple(host['target_nodes']), rounds=host['rounds'],
                artifact_dir=host['artifact_dir'], local_config=host['local_config'],
                stop_event=stop_event, prepared_callback=notifications.prepared,
                round_callback=notifications.round_complete)
        algorithm_config = _read_algorithm_config(args.algorithm_config)
        if stop_event.is_set():
            return 0
        service = load_role_service(args.config, expected_role=role)
        if stop_event.is_set():
            return 0
        runtime = service.start()
        if stop_event.is_set():
            return 0
        algorithm = load_algorithm(args.algorithm, arity=3) if args.algorithm else None
        if stop_event.is_set():
            return 0
        if algorithm is not None:
            algorithm_thread = threading.Thread(
                target=_run_algorithm,
                args=(algorithm, runtime, algorithm_config, context, notifications, algorithm_error, stop_event),
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
        if signal_received.is_set():
            return 0
        if notifications is not None and not algorithm_error:
            try:
                notifications.error('round' if notifications.prepared_reported else 'prepare',
                                    exc.error_code, exc.error_message,
                                    notifications.completed_round + 1 if notifications.prepared_reported else None)
            except Exception:
                pass
        print('%s: %s' % (exc.error_code, exc.error_message), file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        signal.signal(signal.SIGINT, previous_sigint)
        stop_event.set()
        if service is not None:
            service.close()
        if algorithm_thread is not None:
            algorithm_thread.join()
    return 0


def server_main():
    sys.exit(main('server'))


def client_main():
    sys.exit(main('client'))


if __name__ == '__main__':
    sys.exit(main())
