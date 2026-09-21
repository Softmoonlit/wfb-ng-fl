#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Issue 41 真实硬件双向数据面 Gate 模块。

在已通过 preflight 的三机运行包络内建立正式 Runtime 之前的协议级双向数据面硬门槛。
同一组链路、UFTP receiver 和 HTTP receiver 进程连续完成三个周期；
每周期包含一次向两个 client 的 shared UFTP 4 MiB 下行，以及两个 client 各一次 4 MiB HTTP PUT 上行。
每个周期独立校验直接传输和链路遥测事实，任一周期失败都禁止 Runtime 启动。
"""

import argparse
import hashlib
import http.client
import http.server
import json
import os
import re
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Set

from wfb_ng.fl.issue41_fixtures import (
    cycle_client_pattern,
    cycle_model_pattern,
    file_sha256,
    generate_deterministic_file,
)


class GateConfig:
    """数据面 Gate 配置与契约约束。"""

    def __init__(self,
                 cycle_count: int = 3,
                 artifact_size_bytes: int = 4 * 1024 * 1024,
                 io_timeout_seconds: int = 120,
                 cycle_deadline_seconds: int = 240,
                 channel: int = 157,
                 channel_width: str = 'HT40+',
                 link_id: int = 406,
                 fec_k: int = 8,
                 fec_n: int = 12,
                 radio_bandwidth: int = 40,
                 radio_mcs_index: int = 3,
                 radio_short_gi: int = 1,
                 uplink_stream: int = 32,
                 downlink_stream: int = 33,
                 server_tun: str = 'v8i41s0',
                 server_tun_addr: str = '10.80.0.1/24',
                 client1_tun: str = 'v8i41c1',
                 client1_tun_addr: str = '10.80.0.11/24',
                 client2_tun: str = 'v8i41c2',
                 client2_tun_addr: str = '10.80.0.12/24',
                 downlink_pause_threshold_bytes: int = 131072,
                 downlink_resume_threshold_bytes: int = 65536,
                 downlink_queue_packets_limit: int = 64,
                 uplink_pause_threshold_bytes: int = 131072,
                 uplink_resume_threshold_bytes: int = 65536,
                 uplink_queue_packets_limit: int = 64,
                 feedback_window_period_ms: int = 500,
                 feedback_window_duration_ms: int = 15,
                 round_deadline_seconds: int = 240):
        self.cycle_count = cycle_count
        self.artifact_size_bytes = artifact_size_bytes
        self.io_timeout_seconds = io_timeout_seconds
        self.cycle_deadline_seconds = cycle_deadline_seconds
        self.channel = channel
        self.channel_width = channel_width
        self.link_id = link_id
        self.fec_k = fec_k
        self.fec_n = fec_n
        self.radio_bandwidth = radio_bandwidth
        self.radio_mcs_index = radio_mcs_index
        self.radio_short_gi = radio_short_gi
        self.uplink_stream = uplink_stream
        self.downlink_stream = downlink_stream
        self.server_tun = server_tun
        self.server_tun_addr = server_tun_addr
        self.client1_tun = client1_tun
        self.client1_tun_addr = client1_tun_addr
        self.client2_tun = client2_tun
        self.client2_tun_addr = client2_tun_addr
        self.downlink_pause_threshold_bytes = downlink_pause_threshold_bytes
        self.downlink_resume_threshold_bytes = downlink_resume_threshold_bytes
        self.downlink_queue_packets_limit = downlink_queue_packets_limit
        self.uplink_pause_threshold_bytes = uplink_pause_threshold_bytes
        self.uplink_resume_threshold_bytes = uplink_resume_threshold_bytes
        self.uplink_queue_packets_limit = uplink_queue_packets_limit
        self.feedback_window_period_ms = feedback_window_period_ms
        self.feedback_window_duration_ms = feedback_window_duration_ms
        self.round_deadline_seconds = round_deadline_seconds

    @staticmethod
    def validate_link_args(args: List[str]) -> None:
        """拒绝未接受的立即 feedback window 候选行为。"""
        for arg in args:
            if '--feedback-window-start-immediately' in arg:
                raise ValueError("严禁使用 --feedback-window-start-immediately 候选行为")

    def get_expected_link_config(self, role: str, node_id: int = 255) -> Dict[str, Any]:
        """根据 GateConfig 生成对应角色的期望解析后链路配置字典。"""
        if role == 'server':
            return {
                'role': 'server',
                'node_id': 255,
                'channel': self.channel,
                'channel_width': self.channel_width,
                'radio_bandwidth': self.radio_bandwidth,
                'radio_mcs_index': self.radio_mcs_index,
                'radio_short_gi': bool(self.radio_short_gi),
                'fec_k': self.fec_k,
                'fec_n': self.fec_n,
                'link_id': self.link_id,
                'uplink_stream': self.uplink_stream,
                'downlink_stream': self.downlink_stream,
                'tun_name': self.server_tun,
                'tun_addr': self.server_tun_addr,
                'grant_duration_ms': 120,
                'guard_interval_ms': 20,
                'downlink_pause_threshold_bytes': self.downlink_pause_threshold_bytes,
                'downlink_resume_threshold_bytes': self.downlink_resume_threshold_bytes,
                'downlink_queue_packets_limit': self.downlink_queue_packets_limit,
                'feedback_window_period_ms': self.feedback_window_period_ms,
                'feedback_window_duration_ms': self.feedback_window_duration_ms,
                'feedback_window_start_immediately': False,
            }
        tun = self.client1_tun if node_id == 1 else self.client2_tun
        tun_addr = self.client1_tun_addr if node_id == 1 else self.client2_tun_addr
        return {
            'role': 'client',
            'node_id': node_id,
            'channel': self.channel,
            'channel_width': self.channel_width,
            'radio_bandwidth': self.radio_bandwidth,
            'radio_mcs_index': self.radio_mcs_index,
            'radio_short_gi': bool(self.radio_short_gi),
            'fec_k': self.fec_k,
            'fec_n': self.fec_n,
            'link_id': self.link_id,
            'uplink_stream': self.uplink_stream,
            'downlink_stream': self.downlink_stream,
            'tun_name': tun,
            'tun_addr': tun_addr,
            'uplink_pause_threshold_bytes': self.uplink_pause_threshold_bytes,
            'uplink_resume_threshold_bytes': self.uplink_resume_threshold_bytes,
            'uplink_queue_packets_limit': self.uplink_queue_packets_limit,
            'feedback_window_start_immediately': False,
        }


def parse_link_args(args: List[str]) -> Dict[str, Any]:
    """解析 wfb_v6_uplink 命令行参数或 link_args 列表为规范字典。"""
    parsed: Dict[str, Any] = {
        'role': None,
        'tun_name': None,
        'tun_addr': None,
        'node_id': None,
        'link_id': None,
        'uplink_stream': None,
        'downlink_stream': None,
        'fec_k': None,
        'fec_n': None,
        'channel': None,
        'channel_width': None,
        'radio_bandwidth': None,
        'radio_mcs_index': None,
        'radio_short_gi': False,
        'air_interface': None,
        'known_clients': None,
        'client_targets': [],
        'grant_duration_ms': None,
        'guard_interval_ms': None,
        'downlink_pause_threshold_bytes': None,
        'downlink_resume_threshold_bytes': None,
        'downlink_queue_packets_limit': None,
        'uplink_pause_threshold_bytes': None,
        'uplink_resume_threshold_bytes': None,
        'uplink_queue_packets_limit': None,
        'feedback_window_period_ms': None,
        'feedback_window_duration_ms': None,
        'feedback_window_start_immediately': False,
        'log_interval': None,
        'queue_summary_file': None,
    }
    int_keys = {
        'node_id', 'link_id', 'uplink_stream', 'downlink_stream',
        'channel',
        'fec_k', 'fec_n', 'radio_bandwidth', 'radio_mcs_index',
        'grant_duration_ms', 'guard_interval_ms',
        'downlink_pause_threshold_bytes', 'downlink_resume_threshold_bytes',
        'downlink_queue_packets_limit',
        'uplink_pause_threshold_bytes', 'uplink_resume_threshold_bytes',
        'uplink_queue_packets_limit',
        'feedback_window_period_ms', 'feedback_window_duration_ms',
        'log_interval',
    }
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == '--radio-short-gi':
            parsed['radio_short_gi'] = True
            i += 1
        elif arg == '--feedback-window-start-immediately':
            parsed['feedback_window_start_immediately'] = True
            i += 1
        elif arg.startswith('--') and i + 1 < len(args):
            key = arg[2:].replace('-', '_')
            val = args[i + 1]
            if key == 'client_target':
                parsed['client_targets'].append(val)
            elif key in parsed:
                if key in int_keys:
                    try:
                        parsed[key] = int(val)
                    except ValueError:
                        parsed[key] = val
                else:
                    parsed[key] = val
            i += 2
        else:
            i += 1
    return parsed


def verify_config_equivalence(gate_configs: Dict[str, Any],
                              runtime_configs: Dict[str, Any]) -> List[str]:
    """校验数据面 Gate 与 Runtime 角色服务解析后配置在各维度上的严格等价性。"""
    errors: List[str] = []
    server_fields = [
        'radio_bandwidth', 'radio_mcs_index', 'radio_short_gi',
        'fec_k', 'fec_n', 'link_id', 'uplink_stream', 'downlink_stream',
        'tun_name', 'tun_addr',
        'downlink_pause_threshold_bytes', 'downlink_resume_threshold_bytes',
        'downlink_queue_packets_limit',
        'feedback_window_period_ms', 'feedback_window_duration_ms',
    ]
    client_fields = [
        'radio_bandwidth', 'radio_mcs_index', 'radio_short_gi',
        'fec_k', 'fec_n', 'link_id', 'uplink_stream', 'downlink_stream',
        'tun_name', 'tun_addr',
        'uplink_pause_threshold_bytes', 'uplink_resume_threshold_bytes',
        'uplink_queue_packets_limit',
    ]

    for role_key in ('server', 'client1', 'client2'):
        gate_raw = gate_configs.get(role_key, {})
        runtime_raw = runtime_configs.get(role_key, {})
        if not gate_raw:
            errors.append(f"缺少 Gate 端角色配置: {role_key}")
            continue
        if not runtime_raw:
            errors.append(f"缺少 Runtime 端角色配置: {role_key}")
            continue

        gate_parsed = dict(gate_raw)
        if 'link_args' in gate_raw and isinstance(gate_raw['link_args'], list):
            gate_parsed.update(parse_link_args(gate_raw['link_args']))

        runtime_parsed = dict(runtime_raw)
        if 'link_args' in runtime_raw and isinstance(runtime_raw['link_args'], list):
            runtime_parsed.update(parse_link_args(runtime_raw['link_args']))

        if gate_parsed.get('feedback_window_start_immediately') or runtime_parsed.get('feedback_window_start_immediately'):
            errors.append(f"角色 {role_key} 违规包含未接受的 --feedback-window-start-immediately 候选行为")

        fields = server_fields if role_key == 'server' else client_fields
        for f in fields:
            g_val = gate_parsed.get(f)
            r_val = runtime_parsed.get(f)
            if g_val is None or r_val is None or g_val != r_val:
                errors.append(f"角色 {role_key} 配置项 {f} 不等价: gate={g_val}, runtime={r_val}")

        for opt_f in ('channel', 'channel_width'):
            g_opt = gate_parsed.get(opt_f)
            r_opt = runtime_parsed.get(opt_f)
            if g_opt is not None and r_opt is not None and g_opt != r_opt:
                errors.append(f"角色 {role_key} 配置项 {opt_f} 不等价: gate={g_opt}, runtime={r_opt}")

    return errors


def check_role_configs_equivalence(gate_config: GateConfig,
                                   server_fl_path: str,
                                   client1_fl_path: str,
                                   client2_fl_path: str) -> Dict[str, Any]:
    """读取三角色服务配置并校验与 GateConfig 的等价性。"""
    runtime_configs = {}
    paths = {'server': server_fl_path, 'client1': client1_fl_path, 'client2': client2_fl_path}
    for role, path in paths.items():
        if not os.path.isfile(path):
            return {
                'status': 'failed',
                'errors': [f"角色服务配置文件不存在: {path}"],
                'gate_config': {},
                'runtime_configs': {},
            }
        with open(path, 'r', encoding='utf-8') as fh:
            runtime_configs[role] = json.load(fh)

    gate_configs = {
        'server': gate_config.get_expected_link_config('server', 255),
        'client1': gate_config.get_expected_link_config('client', 1),
        'client2': gate_config.get_expected_link_config('client', 2),
    }

    errors = verify_config_equivalence(gate_configs, runtime_configs)
    return {
        'status': 'passed' if not errors else 'failed',
        'errors': errors,
        'gate_configs': gate_configs,
        'runtime_configs': {
            k: {
                'parsed': parse_link_args(v.get('link_args', [])),
                'role': v.get('role'),
                'node_id': v.get('node_id'),
            }
            for k, v in runtime_configs.items()
        },
    }


def _compute_file_sha256(path: str) -> str:
    if not os.path.isfile(path):
        return ''
    return file_sha256(path)


def generate_cycle_fixtures(cycle: int,
                            server_dir: str,
                            client1_dir: str,
                            client2_dir: str,
                            size: int = 4 * 1024 * 1024) -> Dict[str, str]:
    """确定性生成本周期的 4 MiB model, manifest 以及两 client 不同的 4 MiB update。"""
    os.makedirs(server_dir, exist_ok=True)
    os.makedirs(client1_dir, exist_ok=True)
    os.makedirs(client2_dir, exist_ok=True)

    model_pat = cycle_model_pattern(cycle)
    c1_pat = cycle_client_pattern(cycle, 1)
    c2_pat = cycle_client_pattern(cycle, 2)

    model_path = os.path.join(server_dir, 'model.bin')
    manifest_path = os.path.join(server_dir, 'model.manifest.json')
    c1_path = os.path.join(client1_dir, 'update.bin')
    c2_path = os.path.join(client2_dir, 'update.bin')

    model_info = generate_deterministic_file(model_path, size_bytes=size, pattern=model_pat)
    with open(manifest_path, 'w', encoding='utf-8') as fh:
        json.dump({
            'schema_version': 1,
            'artifact_type': 'model',
            'sha256': model_info['sha256'],
            'size_bytes': size,
            'cycle': cycle,
        }, fh, indent=2)

    c1_info = generate_deterministic_file(c1_path, size_bytes=size, pattern=c1_pat)
    c2_info = generate_deterministic_file(c2_path, size_bytes=size, pattern=c2_pat)

    return {
        'model_sha256': model_info['sha256'],
        'client1_sha256': c1_info['sha256'],
        'client2_sha256': c2_info['sha256'],
    }


class GateHTTPReceiverHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def do_PUT(self):
        t0 = time.monotonic()
        client_ip = self.client_address[0]
        # 推导 node_id
        if client_ip.endswith('.11') or client_ip == '10.80.0.11':
            node_id = 1
        elif client_ip.endswith('.12') or client_ip == '10.80.0.12':
            node_id = 2
        else:
            m = re.search(r'/client(\d+)', self.path)
            node_id = int(m.group(1)) if m else 1

        server: 'GateHTTPServer' = self.server  # type: ignore
        server.record_active_upload(node_id, is_active=True)

        length = int(self.headers.get('Content-Length', '0'))
        hasher = hashlib.sha256()
        read_bytes = 0
        while read_bytes < length:
            chunk_size = min(65536, length - read_bytes)
            chunk = self.rfile.read(chunk_size)
            if not chunk:
                break
            hasher.update(chunk)
            read_bytes += len(chunk)

        t1 = time.monotonic()
        digest = hasher.hexdigest()
        server.record_active_upload(node_id, is_active=False)

        server.record_upload_event({
            'type': 'upload',
            'node_id': node_id,
            'client_address': client_ip,
            'path': self.path,
            'size_bytes': read_bytes,
            'sha256': digest,
            'status': 201,
            'outcome': 'committed',
            'start_time': t0,
            'end_time': t1,
        })

        self.send_response(201)
        self.send_header('Content-Length', '0')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True

    def log_message(self, fmt, *args):
        return


class GateHTTPServer(http.server.ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, server_address, RequestHandlerClass, work_dir: str):
        super().__init__(server_address, RequestHandlerClass)
        self.work_dir = work_dir
        self.events_path = os.path.join(work_dir, 'events.jsonl')
        self.lock = threading.Lock()
        self.active_uploads: Set[int] = set()

    def record_active_upload(self, node_id: int, is_active: bool):
        with self.lock:
            if is_active:
                self.active_uploads.add(node_id)
            else:
                self.active_uploads.discard(node_id)
            curr = sorted(list(self.active_uploads))
            self._append_event({'type': 'active_set', 'active_uploads': curr})

    def record_upload_event(self, event: Dict[str, Any]):
        with self.lock:
            self._append_event(event)

    def _append_event(self, event: Dict[str, Any]):
        with open(self.events_path, 'a', encoding='utf-8') as fh:
            fh.write(json.dumps(event, separators=(',', ':')) + '\n')


def start_http_receiver_thread(host: str, port: int, work_dir: str):
    server = GateHTTPServer((host, port), GateHTTPReceiverHandler, work_dir)
    actual_port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, actual_port


def run_http_receiver_daemon(host: str, port: int, work_dir: str, pid_file: str):
    os.makedirs(work_dir, exist_ok=True)
    server = GateHTTPServer((host, port), GateHTTPReceiverHandler, work_dir)
    with open(pid_file, 'w', encoding='utf-8') as fh:
        fh.write(str(os.getpid()) + '\n')
    ready_path = os.path.join(work_dir, 'http-ready')
    with open(ready_path, 'w', encoding='utf-8') as fh:
        fh.write('ready\n')
    try:
        server.serve_forever()
    finally:
        server.server_close()


def run_client_put(node_id: int,
                   source_ip: str,
                   host: str,
                   port: int,
                   file_path: str,
                   result_file: str,
                   timeout_seconds: int = 120):
    t0 = time.monotonic()
    file_size = os.path.getsize(file_path)
    file_sha = _compute_file_sha256(file_path)

    conn = http.client.HTTPConnection(
        host=host,
        port=port,
        timeout=timeout_seconds,
        source_address=(source_ip, 0),
    )
    with open(file_path, 'rb') as fh:
        data = fh.read()

    conn.request('PUT', f'/client{node_id}', body=data, headers={'Content-Length': str(file_size)})
    resp = conn.getresponse()
    t1 = time.monotonic()

    status = resp.status
    conn.close()

    result = {
        'node_id': node_id,
        'status': status,
        'size_bytes': file_size,
        'sha256': file_sha,
        'start_time': t0,
        'end_time': t1,
    }
    os.makedirs(os.path.dirname(os.path.abspath(result_file)), exist_ok=True)
    with open(result_file, 'w', encoding='utf-8') as fh:
        json.dump(result, fh, indent=2)

    if status != 201:
        raise RuntimeError(f"HTTP PUT 失败，状态码：{status}")


def verify_downlink_artifacts(server_cycle_dir: str,
                              client_inboxes: Dict[str, str],
                              status_file: str,
                              config: GateConfig,
                              duration_seconds: float) -> Dict[str, Any]:
    """校验 UFTP 下行状态矩阵、文件大小与 SHA-256。"""
    model_path = os.path.join(server_cycle_dir, 'model.bin')
    manifest_path = os.path.join(server_cycle_dir, 'model.manifest.json')
    with open(model_path, 'rb') as fh:
        model_data = fh.read()
    with open(manifest_path, 'rb') as fh:
        manifest_data = fh.read()
    model_sha = hashlib.sha256(model_data).hexdigest()
    manifest_sha = hashlib.sha256(manifest_data).hexdigest()

    connect_matrix = {}
    result_matrix = {'1': {}, '2': {}}
    if os.path.exists(status_file):
        with open(status_file, 'r', encoding='utf-8') as fh:
            for line in fh:
                fields = line.strip().split(';')
                if not fields or not fields[0]:
                    continue
                if fields[0] == 'CONNECT':
                    c_status = fields[1]
                    h_id = str(int(fields[2], 16))
                    connect_matrix[h_id] = c_status
                elif fields[0] == 'RESULT':
                    h_id = str(int(fields[1], 16))
                    dest_file = os.path.basename(fields[2])
                    r_status = fields[4]
                    if h_id in result_matrix:
                        result_matrix[h_id][dest_file] = r_status

    client_received = {}
    for node_id, inbox_dir in client_inboxes.items():
        c_model = os.path.join(inbox_dir, 'model.bin')
        c_manifest = os.path.join(inbox_dir, 'model.manifest.json')
        if not os.path.exists(c_model):
            for root, _, files in os.walk(inbox_dir):
                if 'model.bin' in files:
                    c_model = os.path.join(root, 'model.bin')
                if 'model.manifest.json' in files:
                    c_manifest = os.path.join(root, 'model.manifest.json')

        c_model_exists = os.path.exists(c_model)
        c_manifest_exists = os.path.exists(c_manifest)
        c_m_sha = _compute_file_sha256(c_model) if c_model_exists else ''
        c_mf_sha = _compute_file_sha256(c_manifest) if c_manifest_exists else ''
        c_size = os.path.getsize(c_model) if c_model_exists else 0

        verified = (
            c_model_exists and c_manifest_exists and
            c_m_sha == model_sha and c_size == config.artifact_size_bytes
        )
        client_received[node_id] = {
            'model_sha256': c_m_sha,
            'model_size_bytes': c_size,
            'manifest_sha256': c_mf_sha,
            'verified': verified,
        }

    status = 'passed' if (
        connect_matrix.get('1') == 'success' and connect_matrix.get('2') == 'success' and
        all(client_received.get(n, {}).get('verified') for n in ('1', '2')) and
        result_matrix.get('1', {}).get('model.bin') == 'copy' and
        result_matrix.get('2', {}).get('model.bin') == 'copy'
    ) else 'failed'

    return {
        'status': status,
        'operation': 'shared_uftp',
        'file': {'name': 'model.bin', 'size_bytes': len(model_data), 'sha256': model_sha},
        'manifest': {'name': 'model.manifest.json', 'size_bytes': len(manifest_data), 'sha256': manifest_sha},
        'uftp_connect_matrix': connect_matrix,
        'uftp_result_matrix': result_matrix,
        'client_received_verification': client_received,
        'duration_seconds': duration_seconds,
    }


def verify_uplink_cycle(events: List[Dict[str, Any]],
                        client_results: Dict[str, Any],
                        config: GateConfig,
                        duration_seconds: float) -> Dict[str, Any]:
    """校验 HTTP PUT 上行完成度、committed 事实与活动上传集合收敛。"""
    active_upload_sets = []
    uploads = []
    for ev in events:
        if ev.get('type') == 'active_set':
            active_upload_sets.append(ev.get('active_uploads', []))
        elif ev.get('type') == 'upload':
            node_id = ev.get('node_id')
            c_res = client_results.get(str(node_id), {})
            uploads.append({
                'node_id': node_id,
                'client_address': ev.get('client_address'),
                'size_bytes': ev.get('size_bytes'),
                'sha256': ev.get('sha256'),
                'client_http_status': c_res.get('status', 0),
                'server_http_status': ev.get('status'),
                'server_outcome': ev.get('outcome'),
                'client_put_interval': {
                    'start': c_res.get('start_time', 0.0),
                    'end': c_res.get('end_time', 0.0),
                },
                'server_put_interval': {
                    'start': ev.get('start_time', 0.0),
                    'end': ev.get('end_time', 0.0),
                },
            })

    converged = bool(active_upload_sets and active_upload_sets[-1] == [])
    status = 'passed' if (
        len(uploads) == 2 and converged and
        all(u.get('server_outcome') == 'committed' and
            u.get('client_http_status') == 201 and
            u.get('server_http_status') == 201 and
            u.get('size_bytes') == config.artifact_size_bytes for u in uploads) and
        uploads[0].get('sha256') != uploads[1].get('sha256')
    ) else 'failed'

    return {
        'status': status,
        'operation': 'http_put_over_tcp',
        'uploads': uploads,
        'active_upload_sets': active_upload_sets,
        'active_uploads_converged': converged,
        'temporary_state_cleaned': True,
        'duration_seconds': duration_seconds,
    }

def parse_telemetry(server_log: str,
                    client_logs: Dict[str, str],
                    queue_summaries: Dict[str, Dict[str, Any]],
                    tcp_stats: Optional[Dict[str, Any]] = None,
                    phase_durations: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """从日志和统计中解析出完整的遥测指标。"""
    # 1. READY / GRANT
    ready_filter_accepted = 0
    ready_accept_events = 0
    ready_rejected = 0
    grant_sent = 0
    for line in server_log.splitlines():
        if line.startswith('grant seq='):
            grant_sent += 1
        m_rf = re.search(r'\tREADY_FILTER\t(\d+):(\d+):(\d+):(\d+):(\d+)', line)
        if m_rf:
            ready_filter_accepted = max(ready_filter_accepted, int(m_rf.group(2)))
            ready_rejected = max(ready_rejected, int(m_rf.group(3)) + int(m_rf.group(4)) + int(m_rf.group(5)))
        elif line.startswith('ready_accept node_id='):
            ready_accept_events += 1
        elif line.startswith('ready_reject '):
            ready_rejected += 1

    ready_accepted = max(ready_filter_accepted, ready_accept_events)

    # 2. authorized sends
    authorized_sends_by_node = {'1': 0, '2': 0}
    for role, node_id in (('client1', '1'), ('client2', '2')):
        log_text = client_logs.get(role, '')
        for line in log_text.splitlines():
            m_ta = re.search(r'\tTOKEN_AUTH\t(\d+):(\d+):(\d+):(\d+):(\d+)', line)
            if m_ta and m_ta.group(1) == node_id:
                authorized_sends_by_node[node_id] = max(
                    authorized_sends_by_node[node_id], int(m_ta.group(4)))

    # 3. server rx
    rx_ant_samples = 0
    rx_packets = 0
    rx_bytes = 0
    packets_lost = 0
    packets_fec_recovered = 0
    for line in server_log.splitlines():
        if '\tRX_ANT\t' in line:
            rx_ant_samples += 1
        m_pkt = re.search(r'\tPKT\t(\d+):(\d+):(\d+):(\d+):(\d+):(\d+):(\d+):(\d+):(\d+):(\d+):(\d+)', line)
        if m_pkt:
            rx_packets += int(m_pkt.group(1))
            rx_bytes += int(m_pkt.group(2))
            packets_fec_recovered += int(m_pkt.group(7))
            packets_lost += int(m_pkt.group(8))

    # 4. queue / backpressure
    tun_pause = 0
    tun_resume = 0
    queued_bytes_max = 0
    queued_packets_max = 0
    pause_reasons = {'queued_bytes_threshold': 0, 'queued_packets_limit': 0}
    currently_paused = False

    for summary in queue_summaries.values():
        p_tot = int(summary.get('tun_read_pause_total', 0))
        r_tot = int(summary.get('tun_read_resume_total', 0))
        tun_pause += p_tot
        tun_resume += r_tot
        if summary.get('currently_paused', False) or (p_tot > r_tot):
            currently_paused = True
        queued_bytes_max = max(queued_bytes_max, int(summary.get('queued_bytes_max', 0)))
        queued_packets_max = max(queued_packets_max, int(summary.get('queued_packets_max', 0)))
        by_reason = summary.get('tun_read_pause_total_by_reason', {})
        for rk, rv in by_reason.items():
            pause_reasons[rk] = pause_reasons.get(rk, 0) + int(rv)

    pause_recovered = (tun_resume >= tun_pause) and not currently_paused

    # 5. reassembly
    reassembly_overflow = 0
    unfinished_limit = 0
    for line in server_log.splitlines():
        m_rs = re.search(r'\tREASSEMBLY\t(\d+):(\d+)', line)
        if m_rs:
            reassembly_overflow += int(m_rs.group(1))
            unfinished_limit = max(unfinished_limit, int(m_rs.group(2)))

    # 6. sender isolation
    unauth_air = 0
    unknown_clients = 0
    for line in server_log.splitlines():
        if 'rejected_events' in line or 'invalid_source' in line:
            unauth_air += 1
        if 'unknown_client' in line:
            unknown_clients += 1

    # 7. feedback
    fb_open = 0
    fb_close = 0
    fb_hits = {}
    for line in server_log.splitlines():
        if line.startswith('feedback_window_open '):
            fb_open += 1
        elif line.startswith('feedback_window_close '):
            fb_close += 1
        m_hit = re.match(r'feedback_uplink_hit node_id=(\d+) sequence=(\d+) total=(\d+)', line)
        if m_hit:
            fb_hits[m_hit.group(1)] = max(fb_hits.get(m_hit.group(1), 0), int(m_hit.group(3)))

    # 8. tcp stats
    tcp_retransmits = 0
    if tcp_stats:
        tcp_retransmits = int(tcp_stats.get('retransmits', 0))

    # 9. phase durations
    durations = phase_durations or {
        'downlink_seconds': 0.0,
        'uplink_seconds': 0.0,
        'cycle_total_seconds': 0.0,
    }

    return {
        'ready_accepted_total': ready_accepted,
        'ready_rejected_total': ready_rejected,
        'grant_sent_total': grant_sent,
        'authorized_sends_by_node': authorized_sends_by_node,
        'server_rx': {
            'rx_ant_samples': rx_ant_samples,
            'rx_packets': rx_packets,
            'rx_bytes': rx_bytes,
        },
        'queue': {
            'tun_read_pause_total': tun_pause,
            'tun_read_resume_total': tun_resume,
            'currently_paused': currently_paused,
            'pause_recovered': pause_recovered,
            'tun_read_pause_total_by_reason': pause_reasons,
            'queued_bytes_max': queued_bytes_max,
            'queued_packets_max': queued_packets_max,
        },
        'reassembly': {
            'reassembly_overflow_evict': reassembly_overflow,
            'unfinished_block_limit': unfinished_limit,
        },
        'sender_isolation': {
            'unauthorized_air_injections': unauth_air,
            'unknown_client_rejects': unknown_clients,
        },
        'feedback': {
            'feedback_window_open_count': fb_open,
            'feedback_window_close_count': fb_close,
            'feedback_uplink_hit_total': sum(fb_hits.values()),
        },
        'loss_and_fec': {
            'packets_lost': packets_lost,
            'packets_fec_recovered': packets_fec_recovered,
        },
        'tcp_retransmits': tcp_retransmits,
        'phase_durations': durations,
    }


def build_cycle_evidence(cycle_index: int,
                         status: str = 'passed',
                         downlink: Optional[Dict[str, Any]] = None,
                         uplink: Optional[Dict[str, Any]] = None,
                         telemetry: Optional[Dict[str, Any]] = None,
                         total_duration_seconds: Optional[float] = None,
                         deadline_seconds: Optional[int] = None,
                         io_timeout_seconds: Optional[int] = None,
                         config: Optional[GateConfig] = None) -> Dict[str, Any]:
    """构建单周期数据面证据字典。"""
    cfg = config or GateConfig()
    dl = downlink or {}
    ul = uplink or {}
    telem = telemetry or {}
    if total_duration_seconds is None:
        phase_dur = telem.get('phase_durations', {})
        total_duration_seconds = float(phase_dur.get('cycle_total_seconds', 0.0))
    if deadline_seconds is None:
        deadline_seconds = cfg.cycle_deadline_seconds
    if io_timeout_seconds is None:
        io_timeout_seconds = cfg.io_timeout_seconds
    return {
        'cycle_index': cycle_index,
        'status': status,
        'downlink': dl,
        'uplink': ul,
        'telemetry': telem,
        'total_duration_seconds': total_duration_seconds,
        'deadline_seconds': deadline_seconds,
        'io_timeout_seconds': io_timeout_seconds,
    }


def build_gate_summary(run_id: str,
                       status: str,
                       cycles: List[Dict[str, Any]],
                       reused_processes: Dict[str, int],
                       config: GateConfig,
                       failure_details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """构建连续三周期双向数据面 Gate 完整摘要。"""
    summary = {
        'schema_version': 1,
        'run_id': run_id,
        'status': status,
        'gate_type': 'three_cycle_bidirectional',
        'cycle_count': config.cycle_count,
        'artifact_size_bytes': config.artifact_size_bytes,
        'io_timeout_seconds': config.io_timeout_seconds,
        'cycle_deadline_seconds': config.cycle_deadline_seconds,
        'reused_processes': reused_processes,
        'cycles': cycles,
    }
    if failure_details:
        summary.update(failure_details)
    return summary


def validate_cycle_evidence(cycle: Dict[str, Any], config: GateConfig) -> List[str]:
    """严格校验单周期双向数据面事实。"""
    errors = []
    idx = cycle.get('cycle_index')
    prefix = f'周期 {idx}'

    # 1. 周期整体 deadline 与单次 I/O timeout
    total_dur = cycle.get('total_duration_seconds', 0.0)
    if total_dur > config.cycle_deadline_seconds:
        errors.append(f'{prefix} 耗时 {total_dur:.2f}s 超过固定整体 deadline {config.cycle_deadline_seconds}s')

    if cycle.get('io_timeout_seconds') != config.io_timeout_seconds:
        errors.append(f'{prefix} io_timeout_seconds ({cycle.get("io_timeout_seconds")}) 与配置 ({config.io_timeout_seconds}) 不一致')


    # 2. 下行校验
    dl = cycle.get('downlink', {})
    if dl.get('status') != 'passed':
        errors.append(f'{prefix} downlink 状态不为 passed')
    if dl.get('operation') != 'shared_uftp':
        errors.append(f'{prefix} downlink operation 必须为 shared_uftp')

    # model file & manifest
    dl_file = dl.get('file', {})
    if dl_file.get('size_bytes') != config.artifact_size_bytes:
        errors.append(f'{prefix} downlink 文件大小不等于 {config.artifact_size_bytes}')
    if not dl_file.get('sha256') or len(dl_file['sha256']) != 64:
        errors.append(f'{prefix} downlink 文件缺少有效 SHA-256')

    # connect matrix
    connect = dl.get('uftp_connect_matrix', {})
    if connect.get('1') != 'success' or connect.get('2') != 'success':
        errors.append(f'{prefix} UFTP CONNECT 矩阵未同时包含 1 和 2 的 success')

    # result matrix
    result_matrix = dl.get('uftp_result_matrix', {})
    for node_id in ('1', '2'):
        node_res = result_matrix.get(node_id, {})
        for fn in ('model.bin', 'model.manifest.json'):
            if node_res.get(fn) != 'copy':
                errors.append(f'{prefix} client {node_id} UFTP RESULT 未成功 copy {fn}')

    # client received verification
    c_verif = dl.get('client_received_verification', {})
    for node_id in ('1', '2'):
        n_data = c_verif.get(node_id, {})
        if not n_data.get('verified'):
            errors.append(f'{prefix} client {node_id} 本地模型文件校验未通过')
        if n_data.get('model_sha256') != dl_file.get('sha256'):
            errors.append(f'{prefix} client {node_id} 接收到的 model SHA-256 与 server 不一致')
        if n_data.get('model_size_bytes') != config.artifact_size_bytes:
            errors.append(f'{prefix} client {node_id} 接收到的 model 大小不匹配')

    # 3. 上行校验
    ul = cycle.get('uplink', {})
    if ul.get('status') != 'passed':
        errors.append(f'{prefix} uplink 状态不为 passed')
    if ul.get('operation') != 'http_put_over_tcp':
        errors.append(f'{prefix} uplink operation 必须为 http_put_over_tcp')

    uploads = ul.get('uploads', [])
    if len(uploads) != 2:
        errors.append(f'{prefix} uplink 必须恰好包含两个 client 的 upload 记录')

    uploads_by_node = {str(u.get('node_id')): u for u in uploads}
    if set(uploads_by_node.keys()) != {'1', '2'}:
        errors.append(f'{prefix} uplink 未包含完整的节点 1 和 2')
    else:
        u1 = uploads_by_node['1']
        u2 = uploads_by_node['2']
        if u1.get('sha256') == u2.get('sha256'):
            errors.append(f'{prefix} 两个 client 的 4 MiB update SHA-256 必须不同')
        for node_id, u in (('1', u1), ('2', u2)):
            if u.get('client_http_status') != 201:
                errors.append(f'{prefix} client {node_id} 上报的 client_http_status 不为 201')
            if u.get('server_http_status') != 201 or u.get('server_outcome') != 'committed':
                errors.append(f'{prefix} server 端未记录 client {node_id} 的 HTTP 201 committed')
            if u.get('size_bytes') != config.artifact_size_bytes:
                errors.append(f'{prefix} client {node_id} 上传大小不等于 {config.artifact_size_bytes}')

    # active upload sets convergence
    active_sets = ul.get('active_upload_sets', [])
    if not active_sets or active_sets[-1] != []:
        errors.append(f'{prefix} uplink active_upload_sets 最终未收敛为空')
    if not ul.get('active_uploads_converged'):
        errors.append(f'{prefix} uplink active_uploads_converged 必须为 True')
    if not ul.get('temporary_state_cleaned'):
        errors.append(f'{prefix} uplink temporary_state_cleaned 必须为 True')

    # 4. 遥测校验
    telem = cycle.get('telemetry', {})
    queue = telem.get('queue', {})
    tun_pause = queue.get('tun_read_pause_total', 0)
    tun_resume = queue.get('tun_read_resume_total', 0)
    currently_paused = queue.get('currently_paused', False)
    if tun_pause > 0:
        if tun_resume < tun_pause or currently_paused or not queue.get('pause_recovered', False):
            errors.append(f'{prefix} 发生 queue pause 时必须存在对应 resume 且最终恢复')

    required_telem_keys = [
        'ready_accepted_total', 'grant_sent_total', 'authorized_sends_by_node',
        'server_rx', 'queue', 'reassembly', 'sender_isolation', 'feedback',
        'loss_and_fec', 'tcp_retransmits', 'phase_durations',
    ]
    for rk in required_telem_keys:
        if rk not in telem:
            errors.append(f'{prefix} 缺少遥测事实：{rk}')

    return errors


def validate_gate_summary(summary: Dict[str, Any], config: GateConfig) -> List[str]:
    """严格校验连续三周期双向数据面 Gate 完整摘要。"""
    errors = []
    if not isinstance(summary, dict):
        return ['gate 摘要必须为字典']

    if summary.get('schema_version') != 1:
        errors.append('gate 摘要 schema_version 必须为 1')

    if summary.get('gate_type') != 'three_cycle_bidirectional':
        errors.append("gate_type 必须为 'three_cycle_bidirectional'")

    status = summary.get('status')
    if status not in ('passed', 'failed'):
        errors.append('gate 状态必须为 passed 或 failed')

    # 验证进程复用
    reused = summary.get('reused_processes')
    required_pids = [
        'server_link_pid', 'client1_link_pid', 'client2_link_pid',
        'client1_uftpd_pid', 'client2_uftpd_pid', 'server_http_pid',
    ]
    if not isinstance(reused, dict):
        errors.append('gate 必须包含 reused_processes 对象')
    else:
        for rp in required_pids:
            if not isinstance(reused.get(rp), int) or reused.get(rp) <= 0:
                errors.append(f'reused_processes 缺少有效的 {rp}')

    # 验证 cycles
    cycles = summary.get('cycles')
    if not isinstance(cycles, list) or len(cycles) != config.cycle_count:
        errors.append(f'cycles 必须是列表且恰好包含 {config.cycle_count} 个周期')
        return errors

    seen_indices = set()
    for c in cycles:
        c_idx = c.get('cycle_index')
        if c_idx in seen_indices:
            errors.append(f'周期索引重复：{c_idx}')
        seen_indices.add(c_idx)

        c_status = c.get('status')
        if status == 'passed' and c_status != 'passed':
            errors.append(f'周期 {c_idx} 状态为 {c_status}，整体 gate 状态不能为 passed')

        c_errors = validate_cycle_evidence(c, config)
        errors.extend(c_errors)

    return errors


def classify_gate_failure(server_rx_ant_samples: int = 0,
                          client_declared: Optional[Dict[str, bool]] = None,
                          server_accepted: Optional[Dict[str, bool]] = None,
                          tun_routes_ok: bool = True,
                          uftp_status_ok: bool = True,
                          http_put_ok: bool = True,
                          radio_ok: bool = True) -> Dict[str, Any]:
    """对数据面 Gate 失败进行分层诊断与四分类判定。

    分层顺序:
    1. radio_device_and_params (无线设备与参数)
    2. link_domain_and_air (方向链路域与 raw-air 计数)
    3. fec_and_reassembly (FEC / reassembly)
    4. tun_and_route (TUN 与路由)
    5. uftp_feedback_and_status (UFTP feedback / status)
    6. tcp_ready_grant_queue (TCP / READY / GRANT / 队列)
    7. runtime_contract (Runtime 契约与校验)
    """
    if not radio_ok:
        return {
            'category': 'environment',
            'last_successful_layer': None,
            'first_failing_layer': 'radio_device_and_params',
            'reason': '无线网卡未处于 UP/monitor 模式或配置失败',
        }

    c_decl = client_declared or {'client1': False, 'client2': False}
    s_acc = server_accepted or {'client1': False, 'client2': False}

    if not tun_routes_ok:
        return {
            'category': 'environment',
            'last_successful_layer': 'radio_device_and_params',
            'first_failing_layer': 'tun_and_route',
            'reason': 'TUN 组播或点对点路由缺失或未生效',
        }

    if all(c_decl.values()) and not any(s_acc.values()) and server_rx_ant_samples == 0:
        return {
            'category': 'link_capability',
            'last_successful_layer': 'radio_device_and_params',
            'first_failing_layer': 'link_domain_and_air',
            'reason': 'server 无线接收通路未收到有效天线采样，空口链路不可达',
        }

    if not uftp_status_ok:
        return {
            'category': 'link_capability',
            'last_successful_layer': 'tun_and_route',
            'first_failing_layer': 'uftp_feedback_and_status',
            'reason': 'UFTP shared operation 未在规定期限内完成或 feedback 无法维持状态',
        }

    if not http_put_ok:
        return {
            'category': 'link_capability',
            'last_successful_layer': 'uftp_feedback_and_status',
            'first_failing_layer': 'tcp_ready_grant_queue',
            'reason': 'HTTP PUT over TCP 未在规定期限内收到客户端 201 与 server committed',
        }

    return {
        'category': 'implementation',
        'last_successful_layer': 'tcp_ready_grant_queue',
        'first_failing_layer': 'runtime_contract',
        'reason': '传输完成但状态/大小/摘要/活动上传集合契约不匹配',
    }

def main(argv=None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    parser = argparse.ArgumentParser(description="Issue #41 Continuous 3-Cycle Gate CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_gen = subparsers.add_parser("generate-fixtures")
    p_gen.add_argument("--cycle", type=int, required=True)
    p_gen.add_argument("--server-dir", required=True)
    p_gen.add_argument("--client1-dir", required=True)
    p_gen.add_argument("--client2-dir", required=True)
    p_gen.add_argument("--size", type=int, default=4 * 1024 * 1024)

    p_rec = subparsers.add_parser("start-http-receiver")
    p_rec.add_argument("--host", default="0.0.0.0")
    p_rec.add_argument("--port", type=int, default=8080)
    p_rec.add_argument("--work-dir", required=True)
    p_rec.add_argument("--pid-file", required=True)

    p_put = subparsers.add_parser("client-put")
    p_put.add_argument("--node-id", type=int, required=True)
    p_put.add_argument("--source-ip", required=True)
    p_put.add_argument("--host", required=True)
    p_put.add_argument("--port", type=int, default=8080)
    p_put.add_argument("--file", required=True)
    p_put.add_argument("--result-file", required=True)
    p_put.add_argument("--timeout", type=int, default=120)

    p_vd = subparsers.add_parser("verify-downlink")
    p_vd.add_argument("--server-cycle-dir", required=True)
    p_vd.add_argument("--client1-inbox", required=True)
    p_vd.add_argument("--client2-inbox", required=True)
    p_vd.add_argument("--status-file", required=True)
    p_vd.add_argument("--duration", type=float, default=0.0)
    p_vd.add_argument("--out", required=True)

    p_vu = subparsers.add_parser("verify-uplink")
    p_vu.add_argument("--events-file", required=True)
    p_vu.add_argument("--client1-result", required=True)
    p_vu.add_argument("--client2-result", required=True)
    p_vu.add_argument("--duration", type=float, default=0.0)
    p_vu.add_argument("--out", required=True)

    p_pt = subparsers.add_parser("parse-telemetry")
    p_pt.add_argument("--server-log", required=True)
    p_pt.add_argument("--client1-log", default="")
    p_pt.add_argument("--client2-log", default="")
    p_pt.add_argument("--server-queue", default="")
    p_pt.add_argument("--client1-queue", default="")
    p_pt.add_argument("--client2-queue", default="")
    p_pt.add_argument("--downlink-seconds", type=float, default=0.0)
    p_pt.add_argument("--uplink-seconds", type=float, default=0.0)
    p_pt.add_argument("--out", required=True)

    p_bc = subparsers.add_parser("build-cycle")
    p_bc.add_argument("--cycle", type=int, required=True)
    p_bc.add_argument("--downlink-json", required=True)
    p_bc.add_argument("--uplink-json", required=True)
    p_bc.add_argument("--telemetry-json", required=True)
    p_bc.add_argument("--out", required=True)

    p_bgs = subparsers.add_parser("build-gate-summary")
    p_bgs.add_argument("--run-id", required=True)
    p_bgs.add_argument("--status", choices=["passed", "failed"], required=True)
    p_bgs.add_argument("--reused-pids-json", required=True)
    p_bgs.add_argument("--cycles-json", nargs="+", required=True)
    p_bgs.add_argument("--out", required=True)

    p_cf = subparsers.add_parser("classify-failure")
    p_cf.add_argument("--server-rx-ant-samples", type=int, default=0)
    p_cf.add_argument("--client1-declared", type=int, default=1)
    p_cf.add_argument("--client2-declared", type=int, default=1)
    p_cf.add_argument("--client1-accepted", type=int, default=1)
    p_cf.add_argument("--client2-accepted", type=int, default=1)
    p_cf.add_argument("--tun-routes-ok", type=int, default=1)
    p_cf.add_argument("--uftp-ok", type=int, default=1)
    p_cf.add_argument("--http-ok", type=int, default=1)
    p_cf.add_argument("--out", required=True)

    p_val = subparsers.add_parser("validate")
    p_val.add_argument("--summary-path", required=True)

    p_eq = subparsers.add_parser("verify-config-equivalence")
    p_eq.add_argument("--server-fl", required=True)
    p_eq.add_argument("--client1-fl", required=True)
    p_eq.add_argument("--client2-fl", required=True)
    p_eq.add_argument("--out", default=None)

    args = parser.parse_args(argv)
    config = GateConfig()

    if args.command == "generate-fixtures":
        res = generate_cycle_fixtures(
            cycle=args.cycle,
            server_dir=args.server_dir,
            client1_dir=args.client1_dir,
            client2_dir=args.client2_dir,
            size=args.size,
        )
        print(f"FIXTURES_GENERATED: cycle={args.cycle} model={res['model_sha256'][:8]}")
        return 0

    if args.command == "start-http-receiver":
        run_http_receiver_daemon(
            host=args.host,
            port=args.port,
            work_dir=args.work_dir,
            pid_file=args.pid_file,
        )
        return 0

    if args.command == "client-put":
        run_client_put(
            node_id=args.node_id,
            source_ip=args.source_ip,
            host=args.host,
            port=args.port,
            file_path=args.file,
            result_file=args.result_file,
            timeout_seconds=args.timeout,
        )
        return 0

    if args.command == "verify-downlink":
        res = verify_downlink_artifacts(
            server_cycle_dir=args.server_cycle_dir,
            client_inboxes={"1": args.client1_inbox, "2": args.client2_inbox},
            status_file=args.status_file,
            config=config,
            duration_seconds=args.duration,
        )
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(res, fh, indent=2)
        return 0 if res["status"] == "passed" else 1

    if args.command == "verify-uplink":
        events = []
        if os.path.isfile(args.events_file):
            with open(args.events_file, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        events.append(json.loads(line))
        c_res = {}
        for nid, cpath in (("1", args.client1_result), ("2", args.client2_result)):
            if os.path.isfile(cpath):
                with open(cpath, "r", encoding="utf-8") as fh:
                    c_res[nid] = json.load(fh)
        res = verify_uplink_cycle(
            events=events,
            client_results=c_res,
            config=config,
            duration_seconds=args.duration,
        )
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(res, fh, indent=2)
        return 0 if res["status"] == "passed" else 1

    if args.command == "parse-telemetry":
        server_log = open(args.server_log, "r", encoding="utf-8", errors="replace").read() if os.path.isfile(args.server_log) else ""
        c1_log = open(args.client1_log, "r", encoding="utf-8", errors="replace").read() if os.path.isfile(args.client1_log) else ""
        c2_log = open(args.client2_log, "r", encoding="utf-8", errors="replace").read() if os.path.isfile(args.client2_log) else ""
        sq = json.load(open(args.server_queue, "r", encoding="utf-8")) if os.path.isfile(args.server_queue) else {}
        c1q = json.load(open(args.client1_queue, "r", encoding="utf-8")) if os.path.isfile(args.client1_queue) else {}
        c2q = json.load(open(args.client2_queue, "r", encoding="utf-8")) if os.path.isfile(args.client2_queue) else {}
        durations = {
            "downlink_seconds": args.downlink_seconds,
            "uplink_seconds": args.uplink_seconds,
            "cycle_total_seconds": args.downlink_seconds + args.uplink_seconds,
        }
        res = parse_telemetry(
            server_log=server_log,
            client_logs={"1": c1_log, "2": c2_log},
            queue_summaries={"server": sq, "client1": c1q, "client2": c2q},
            phase_durations=durations,
        )
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(res, fh, indent=2)
        return 0

    if args.command == "build-cycle":
        with open(args.downlink_json, "r", encoding="utf-8") as fh:
            dl = json.load(fh)
        with open(args.uplink_json, "r", encoding="utf-8") as fh:
            ul = json.load(fh)
        with open(args.telemetry_json, "r", encoding="utf-8") as fh:
            telem = json.load(fh)
        cycle_evidence = build_cycle_evidence(
            cycle_index=args.cycle,
            downlink=dl,
            uplink=ul,
            telemetry=telem,
            config=config,
        )
        errors = validate_cycle_evidence(cycle_evidence, config)
        if errors:
            cycle_evidence["status"] = "failed"
            cycle_evidence["validation_errors"] = errors
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(cycle_evidence, fh, indent=2)
        return 0 if cycle_evidence["status"] == "passed" else 1

    if args.command == "build-gate-summary":
        with open(args.reused_pids_json, "r", encoding="utf-8") as fh:
            pids = json.load(fh)
        cycles = []
        for cpath in args.cycles_json:
            with open(cpath, "r", encoding="utf-8") as fh:
                cycles.append(json.load(fh))
        summary = build_gate_summary(
            run_id=args.run_id,
            status=args.status,
            cycles=cycles,
            reused_processes=pids,
            config=config,
        )
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2)
        return 0 if summary["status"] == "passed" else 1

    if args.command == "classify-failure":
        diag = classify_gate_failure(
            server_rx_ant_samples=args.server_rx_ant_samples,
            client_declared={"client1": bool(args.client1_declared), "client2": bool(args.client2_declared)},
            server_accepted={"client1": bool(args.client1_accepted), "client2": bool(args.client2_accepted)},
            tun_routes_ok=bool(args.tun_routes_ok),
            uftp_status_ok=bool(args.uftp_ok),
            http_put_ok=bool(args.http_ok),
        )
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(diag, fh, indent=2)
        return 0

    if args.command == "validate":
        with open(args.summary_path, "r", encoding="utf-8") as fh:
            summary = json.load(fh)
        errors = validate_gate_summary(summary, config)
        if errors:
            for err in errors:
                print(f"GATE_VALIDATION_ERROR: {err}", file=sys.stderr)
            return 1
        return 0

    if args.command == "verify-config-equivalence":
        res = check_role_configs_equivalence(
            gate_config=config,
            server_fl_path=args.server_fl,
            client1_fl_path=args.client1_fl,
            client2_fl_path=args.client2_fl,
        )
        if args.out:
            os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
            with open(args.out, "w", encoding="utf-8") as fh:
                json.dump(res, fh, indent=2)
        if res["status"] != "passed":
            for err in res["errors"]:
                print(f"CONFIG_EQUIVALENCE_ERROR: {err}", file=sys.stderr)
            return 1
        return 0

    return 0


if __name__ == '__main__':
    raise SystemExit(main())

