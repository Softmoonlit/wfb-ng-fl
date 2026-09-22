#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
import sys

_CUR_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_CUR_DIR, '..', '..'))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from tests.real_hardware.issue41_build_summary import is_sha256 as _is_sha256
from tests.real_hardware.issue41_gate import GateConfig, safe_uftp_rate_range, validate_gate_summary
from tests.real_hardware.issue41_lifecycle import validate_lifecycle_summary


REQUIRED_TOP_LEVEL = (
    'orchestration',
    'pre_runtime_smoke',
    'formal_runtime_loop',
    'lifecycle',
    'conclusion',
)


def main(argv=None):
    parser = argparse.ArgumentParser(description='校验 issue41 真实硬件归档摘要')
    parser.add_argument('archive_dir', help='tests/logs/v8_issue41_<timestamp> 归档目录')
    args = parser.parse_args(argv)
    errors = validate_archive(args.archive_dir)
    if errors:
        for error in errors:
            print('FAIL: %s' % error, file=sys.stderr)
        return 1
    print('OK: issue41 archive validation passed')
    return 0


def validate_archive(archive_dir):
    errors = []
    summary_path = os.path.join(archive_dir, 'issue41_summary.json')
    result_path = os.path.join(archive_dir, 'result.md')
    summary = _read_json(summary_path, errors)
    if summary is None:
        return errors
    if not os.path.isfile(result_path):
        errors.append('缺少 result.md')

    for key in REQUIRED_TOP_LEVEL:
        if key not in summary:
            errors.append('summary 缺少分区：%s' % key)
    if errors:
        return errors

    _check_candidate_feedback(summary, errors, 'summary')
    _validate_run_ids(summary, errors)
    _validate_phase_dependencies(summary, errors)

    _require_status(summary['orchestration'], 'orchestration', errors)
    smoke = summary['pre_runtime_smoke']
    if not isinstance(smoke, dict):
        errors.append('pre_runtime_smoke 必须是对象')
        return errors
    _validate_smoke_gate(archive_dir, smoke, summary, errors)

    _validate_runtime(summary['formal_runtime_loop'], errors)
    _validate_lifecycle(archive_dir, summary['lifecycle'], errors)
    _validate_conclusion(summary['conclusion'], summary, errors)
    _validate_envelope(archive_dir, summary, errors)
    return errors


def _check_candidate_feedback(val, errors, path='summary'):
    if isinstance(val, str):
        if '--feedback-window-start-immediately' in val:
            errors.append('%s 违规包含未接受的 --feedback-window-start-immediately 候选行为' % path)
    elif isinstance(val, dict):
        if val.get('feedback_window_start_immediately') is True:
            errors.append('%s 违规包含未接受的 feedback_window_start_immediately 候选行为' % path)
        for k, v in val.items():
            _check_candidate_feedback(v, errors, f'{path}.{k}')
    elif isinstance(val, (list, tuple)):
        for i, item in enumerate(val):
            _check_candidate_feedback(item, errors, f'{path}[{i}]')


def _validate_run_ids(summary, errors):
    run_id = summary.get('run_id')
    if not run_id:
        return
    for part_name in ('pre_runtime_smoke', 'formal_runtime_loop', 'lifecycle'):
        part = summary.get(part_name)
        if isinstance(part, dict) and 'run_id' in part and part['run_id'] != run_id:
            errors.append('%s 与 summary 的 run_id 不一致' % part_name)


def _validate_phase_dependencies(summary, errors):
    smoke = summary.get('pre_runtime_smoke', {})
    formal = summary.get('formal_runtime_loop', {})
    lifecycle = summary.get('lifecycle', {})

    if isinstance(smoke, dict) and isinstance(formal, dict):
        if smoke.get('status') != 'passed' and formal.get('status') == 'passed':
            errors.append('数据面 Gate 未通过时 formal_runtime_loop 不得标记为 passed')

    if isinstance(formal, dict) and isinstance(lifecycle, dict):
        if formal.get('status') != 'passed' and lifecycle.get('status') == 'passed':
            errors.append('formal_runtime_loop 未通过时 lifecycle 不得标记为 passed')


def _validate_envelope(archive_dir, summary, errors):
    envelope_path = os.path.join(archive_dir, 'envelope.json')
    if not os.path.isfile(envelope_path):
        return
    envelope = _read_json_file(envelope_path, 'envelope.json', errors)
    if envelope is None:
        return
    _check_candidate_feedback(envelope, errors, 'envelope.json')
    if 'run_id' in summary and envelope.get('run_id') != summary.get('run_id'):
        errors.append('envelope.json 与 summary 的 run_id 不一致')
    if envelope.get('overwritten') is True:
        errors.append('归档目录被覆盖，不可作为有效证据')
    mode = envelope.get('mode', 'formal')
    if mode != 'formal' and summary.get('conclusion', {}).get('status') == 'passed':
        errors.append('非 formal 模式（%s）归档不可标记为 passed' % mode)
    isolation = envelope.get('network_isolation')
    if not isinstance(isolation, dict) or not isolation.get('prohibit_management_as_data_plane'):
        errors.append('envelope.json 必须明确限制管理网不可作为数据平面')
    resolved = envelope.get('resolved_config')
    if resolved is not None:
        if not isinstance(resolved, dict) or not resolved:
            errors.append('envelope.json 缺少 resolved_config 解析配置')
        else:
            for deadline_key in ('smoke_cycle_deadline_seconds', 'runtime_timeout_seconds', 'io_timeout_seconds'):
                val = resolved.get(deadline_key)
                if not isinstance(val, (int, float)) or val <= 0:
                    errors.append('envelope.json resolved_config 缺少有效 deadline/超时配置：%s' % deadline_key)
            for scheduling_key, allow_zero in (
                    ('grant_duration_ms', False), ('guard_interval_ms', True)):
                value = resolved.get(scheduling_key)
                if type(value) is not int or value < 0 or (not allow_zero and value == 0):
                    errors.append('envelope.json resolved_config 缺少有效调度参数：%s' % scheduling_key)
            rate = resolved.get('uftp_rate_kbps')
            if type(rate) is not int or rate <= 0:
                errors.append('envelope.json resolved_config 缺少有效 UFTP 速率')
            else:
                mcs_idx = resolved.get('server_radio_mcs_index', resolved.get('radio_mcs_index'))
                safe_range = safe_uftp_rate_range(
                    mcs_idx, resolved.get('radio_bandwidth'),
                    resolved.get('channel_width'))
                if safe_range is None or not safe_range[0] <= rate <= safe_range[1]:
                    errors.append('envelope.json UFTP 速率超出射频安全区间')
            is_passed = summary.get('conclusion', {}).get('status') == 'passed'
            io_timeout_cfg = resolved.get('io_timeout_seconds')
            if isinstance(io_timeout_cfg, (int, float)) and is_passed and io_timeout_cfg != 120 and mode == 'formal':
                errors.append('formal 模式下单次 I/O 超时 (io_timeout_seconds) 必须严格锁定为 120 秒')
            elif isinstance(io_timeout_cfg, (int, float)) and io_timeout_cfg > 120 and mode == 'formal':
                errors.append('formal 模式下单次 I/O 超时 (io_timeout_seconds) 不得大于 120 秒')

            smoke_io_cfg = resolved.get('smoke_io_timeout_seconds')
            if isinstance(smoke_io_cfg, (int, float)) and is_passed and smoke_io_cfg != 120 and mode == 'formal':
                errors.append('formal 模式下门禁单次 I/O 超时 (smoke_io_timeout_seconds) 必须严格锁定为 120 秒')
            elif isinstance(smoke_io_cfg, (int, float)) and smoke_io_cfg > 120 and mode == 'formal':
                errors.append('formal 模式下门禁单次 I/O 超时 (smoke_io_timeout_seconds) 不得大于 120 秒')

            rt_timeout_cfg = resolved.get('runtime_timeout_seconds')
            if isinstance(rt_timeout_cfg, (int, float)) and is_passed and rt_timeout_cfg != 400 and mode == 'formal':
                errors.append('formal 模式下整轮超时 (runtime_timeout_seconds) 必须严格锁定为 400 秒')
            elif isinstance(rt_timeout_cfg, (int, float)) and rt_timeout_cfg > 400 and mode == 'formal':
                errors.append('formal 模式下整轮超时 (runtime_timeout_seconds) 不得大于 400 秒')
            smoke = summary.get('pre_runtime_smoke', {})
            if isinstance(smoke, dict):
                if 'cycle_deadline_seconds' in smoke and smoke['cycle_deadline_seconds'] != resolved.get('smoke_cycle_deadline_seconds'):
                    errors.append('运行中修改配置：pre_runtime_smoke cycle_deadline_seconds 与 envelope 不一致')
                if 'io_timeout_seconds' in smoke and smoke['io_timeout_seconds'] != resolved.get('io_timeout_seconds'):
                    errors.append('运行中修改配置：pre_runtime_smoke io_timeout_seconds 与 envelope 不一致')
            formal = summary.get('formal_runtime_loop', {})
            if is_passed and isinstance(formal, dict):
                equivalence = formal.get('config_equivalence')
                if not isinstance(equivalence, dict):
                    equivalence = {}
                gate_configs = equivalence.get('gate_configs')
                runtime_configs = equivalence.get('runtime_configs')
                gate_server = gate_configs.get('server') if isinstance(gate_configs, dict) else None
                runtime_server = runtime_configs.get('server') if isinstance(runtime_configs, dict) else None
                gate_rate = gate_server.get('uftp_rate_kbps') if isinstance(gate_server, dict) else None
                runtime_rate = runtime_server.get('uftp_rate_kbps') if isinstance(runtime_server, dict) else None
                if gate_rate != rate or runtime_rate != rate:
                    errors.append('运行中修改配置：Gate/Runtime UFTP 速率与 envelope 不一致')
            if isinstance(formal, dict) and 'scenario' in formal and isinstance(formal['scenario'], dict):
                sc = formal['scenario']
                if 'round_deadline_seconds' in sc and sc['round_deadline_seconds'] != resolved.get('runtime_timeout_seconds'):
                    errors.append('运行中修改配置：formal_runtime_loop round_deadline_seconds 与 envelope 不一致')
                if 'io_timeout_seconds' in sc and sc['io_timeout_seconds'] != resolved.get('io_timeout_seconds'):
                    errors.append('运行中修改配置：formal_runtime_loop io_timeout_seconds 与 envelope 不一致')
                if 'rounds' in resolved and 'round_count' in sc and sc['round_count'] != resolved.get('rounds'):
                    errors.append('运行中修改配置：formal_runtime_loop round_count 与 envelope 不一致')
                if 'artifact_size_bytes' in resolved and 'artifact_size_bytes' in sc and sc['artifact_size_bytes'] != resolved.get('artifact_size_bytes'):
                    errors.append('运行中修改配置：formal_runtime_loop artifact_size_bytes 与 envelope 不一致')
                if 'training_delay_ms_by_node' in resolved and 'training_delay_ms_by_node' in sc and sc['training_delay_ms_by_node'] != resolved.get('training_delay_ms_by_node'):
                    errors.append('运行中修改配置：formal_runtime_loop training_delay_ms_by_node 与 envelope 不一致')
    conclusion = summary.get('conclusion', {})
    category = conclusion.get('category')
    if category and category not in ('environment', 'tooling', 'implementation', 'link_capability'):
        errors.append('conclusion.category 无效：%s' % category)



def _read_json(path, errors):
    return _read_json_file(path, 'issue41_summary.json', errors)


def _read_json_file(path, name, errors):
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            value = json.load(fh)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append('无法读取 %s：%s' % (name, exc))
        return None
    if not isinstance(value, dict):
        errors.append('%s 顶层必须是对象' % name)
        return None
    return value


def _validate_lifecycle(archive_dir, value, errors):
    _require_status(value, 'lifecycle', errors)
    if not isinstance(value, dict):
        return
    lc_errors = validate_lifecycle_summary(value, archive_dir)
    for err in lc_errors:
        errors.append('lifecycle 校验失败：%s' % err)


def _require_status(value, name, errors):
    if not isinstance(value, dict):
        errors.append('%s 必须是对象' % name)
        return
    if value.get('status') not in ('passed', 'failed', 'skipped'):
        errors.append('%s.status 无效' % name)


def _validate_smoke_gate(archive_dir, value, summary, errors):
    _require_status(value, 'pre_runtime_smoke', errors)
    if not isinstance(value, dict):
        return
    if value.get('status') != 'passed':
        return
    marker_path = os.path.join(
        archive_dir, 'pre_runtime_smoke', 'passed.json')
    marker = _read_json_file(marker_path, 'smoke gate marker', errors)
    if marker is not None:
        if marker.get('schema_version') != 1:
            errors.append('pre_runtime_smoke marker schema_version 无效')
        if (marker.get('gate_type') != 'three_cycle_bidirectional' or
                marker.get('status') != 'passed'):
            errors.append('pre_runtime_smoke marker 内容与通过状态不一致')
        if summary and 'run_id' in summary and marker.get('run_id') and marker.get('run_id') != summary.get('run_id'):
            errors.append('pre_runtime_smoke marker 与 summary 的 run_id 不一致')

    env_path = os.path.join(archive_dir, 'envelope.json')
    resolved_cfg = {}
    if os.path.isfile(env_path):
        env_obj = _read_json_file(env_path, 'envelope.json', [])
        if isinstance(env_obj, dict):
            resolved_cfg = env_obj.get('resolved_config', {})

    gate_kwargs = {}
    config_keys = [
        'channel', 'channel_width', 'link_id', 'fec_k', 'fec_n',
        'radio_bandwidth', 'radio_mcs_index', 'server_radio_mcs_index', 'client_radio_mcs_index',
        'radio_short_gi', 'uftp_rate_kbps',
        'grant_duration_ms', 'guard_interval_ms',
        'uplink_stream', 'downlink_stream', 'server_tun', 'server_tun_addr',
    ]
    for i in range(1, 11):
        config_keys.extend([f'client{i}_tun', f'client{i}_tun_addr', f'client{i}_radio_mcs_index'])
    for k in config_keys:
        if k in resolved_cfg:
            gate_kwargs[k] = resolved_cfg[k]

    if not resolved_cfg:
        errors.append('缺少 envelope.json 或 resolved_config')
        return

    for req_key, gate_arg in (
        ('smoke_cycle_count', 'cycle_count'),
        ('smoke_io_timeout_seconds', 'io_timeout_seconds'),
        ('smoke_cycle_deadline_seconds', 'cycle_deadline_seconds'),
        ('artifact_size_bytes', 'artifact_size_bytes'),
    ):
        if req_key in resolved_cfg:
            gate_kwargs[gate_arg] = int(resolved_cfg[req_key])
        else:
            errors.append('envelope.json resolved_config 缺少门禁参数：%s' % req_key)

    config = GateConfig(**gate_kwargs)
    gate_errors = validate_gate_summary(value, config)
    for ge in gate_errors:
        errors.append('pre_runtime_smoke 校验失败：%s' % ge)


def _validate_runtime(value, errors):
    _require_status(value, 'formal_runtime_loop', errors)
    if not isinstance(value, dict):
        return
    if value.get('status') != 'passed':
        return
    scenario = value.get('scenario') if isinstance(value.get('scenario'), dict) else {}
    template_hashes = scenario.get('update_template_sha256_by_node', {})
    expected_node_ids = sorted([int(k) for k in template_hashes.keys()]) if template_hashes else [1, 2]

    required = {
        'runtime_interfaces': [
            'publish_model', 'wait_for_model', 'submit_update',
            'wait_for_updates'],
        'data_plane': '10.80.0.0/24',
        'server_wait_for_updates_returned_node_ids': expected_node_ids,
    }
    for key, expected in required.items():
        if value.get(key) != expected:
            errors.append('formal_runtime_loop.%s 不满足通过条件' % key)
    _validate_formal_scenario(value, errors)
    if value.get('partial_result_returned') is not False:
        errors.append('formal_runtime_loop 必须证明 server 未返回 partial result')
    required_roles = ['server'] + [f'client{nid}' for nid in expected_node_ids]
    for r in required_roles:
        for suffix in ('result', 'journal'):
            key = f'{r}_{suffix}'
            path = value.get(key)
            if not _archive_file_exists(path):
                errors.append(f'formal_runtime_loop 缺少 {key} 文件证据')
    route_evidence = value.get('route_evidence')
    expected_route_count = 6 * len(expected_node_ids)
    if (not isinstance(route_evidence, list) or len(route_evidence) != expected_route_count or
            any(not _archive_file_exists(path) for path in route_evidence)):
        errors.append('formal_runtime_loop 缺少六组双向 UFTP 路由证据')

    config_eq = value.get('config_equivalence')
    if config_eq is None or not isinstance(config_eq, dict) or config_eq.get('status') != 'passed' or config_eq.get('errors'):
        errors.append('formal_runtime_loop 角色服务配置等价性未通过')

    controlled_stop = value.get('controlled_stop')
    if controlled_stop is None or not isinstance(controlled_stop, dict) or controlled_stop.get('status') != 'passed':
        errors.append('formal_runtime_loop 角色服务受控停止未通过')


def _validate_formal_scenario(value, errors):
    scenario = value.get('scenario')
    if not isinstance(scenario, dict):
        errors.append('formal_runtime_loop 缺少正式场景配置')
        return

    round_count = scenario.get('round_count')
    artifact_size = scenario.get('artifact_size_bytes')
    delays = scenario.get('training_delay_ms_by_node')

    template_hashes = scenario.get('update_template_sha256_by_node')
    if not isinstance(template_hashes, dict) or len(template_hashes) < 2 or not all(_is_sha256(digest) for digest in template_hashes.values()):
        errors.append('formal_runtime_loop.scenario 必须包含有效的 client 模板 SHA-256')
        return

    expected_node_ids = sorted([int(k) for k in template_hashes.keys()])

    if round_count != 2:
        errors.append('formal_runtime_loop.scenario.round_count 必须为 2 轮')
    if artifact_size != 40 * 1024 * 1024:
        errors.append('formal_runtime_loop.scenario.artifact_size_bytes 必须为 40 MiB (41943040 字节)')
    if delays != {str(nid): 0 for nid in expected_node_ids}:
        errors.append('formal_runtime_loop.scenario.training_delay_ms_by_node 必须全为 0 延时并发')

    if scenario.get('placeholder_training') != 'template_copy':
        errors.append('formal_runtime_loop.scenario.placeholder_training 不满足通过条件')
    if scenario.get('placeholder_aggregation') != 'model_copy':
        errors.append('formal_runtime_loop.scenario.placeholder_aggregation 不满足通过条件')

    if len(template_hashes) != len(set(template_hashes.values())):
        errors.append('formal_runtime_loop.scenario 必须包含不同的 client 模板 SHA-256')

    rounds = value.get('rounds')
    if not isinstance(rounds, list) or len(rounds) != 2:
        errors.append('formal_runtime_loop 必须包含 2 轮完整证据')
        return

    seen_round_ids = set()
    prev_output_model_sha = None
    for index, round_value in enumerate(rounds, 1):
        _validate_round(
            round_value, index, artifact_size, template_hashes,
            seen_round_ids, errors,
            prev_output_model_sha=prev_output_model_sha)
        if isinstance(round_value, dict) and isinstance(round_value.get('model'), dict):
            prev_output_model_sha = round_value['model'].get('sha256')

    total_runtime_seconds = sum(
        float(r.get('duration_seconds') or 0.0) for r in rounds if isinstance(r, dict)
    )
    max_allowed_runtime = float(scenario.get('round_deadline_seconds', 400))
    if total_runtime_seconds > max_allowed_runtime:
        errors.append('formal_runtime_loop 实际总耗时 (%.2fs) 超过上限 (%.2fs)' % (
            total_runtime_seconds, max_allowed_runtime))


def _validate_round(value, index, expected_size, template_hashes, seen_round_ids, errors, prev_output_model_sha=None):
    prefix = 'formal_runtime_loop.rounds[%d]' % (index - 1)
    if not isinstance(value, dict):
        errors.append('%s 必须是对象' % prefix)
        return
    if value.get('round_index') != index:
        errors.append('%s.round_index 无效' % prefix)
    round_id = value.get('round_id')
    if not isinstance(round_id, str) or not round_id or round_id in seen_round_ids:
        errors.append('%s.round_id 无效或重复' % prefix)
    else:
        seen_round_ids.add(round_id)
    model = value.get('model')
    if not isinstance(model, dict) or model.get('size_bytes') != expected_size or not _is_sha256(model.get('sha256')):
        errors.append('%s.model 必须是 %d 字节且含 SHA-256' % (prefix, expected_size))
    if index > 1 and prev_output_model_sha is not None:
        if isinstance(model, dict) and model.get('sha256') != prev_output_model_sha:
            errors.append('%s 占位聚合模型 SHA-256 与前一轮输出不匹配' % prefix)

    expected_node_ids = sorted([int(k) for k in template_hashes.keys()])
    intervals = value.get('model_receive_intervals')
    if not isinstance(intervals, dict) or set(intervals) != set(template_hashes.keys()) or any(not _is_interval(intervals[node]) for node in intervals):
        errors.append('%s 缺少所有节点的模型接收时间区间' % prefix)
    if value.get('server_committed_node_ids') != expected_node_ids:
        errors.append(f'{prefix}.server_committed_node_ids 必须为 {expected_node_ids}')
    if value.get('server_wait_returned_node_ids') != expected_node_ids:
        errors.append(f'{prefix}.server_wait_returned_node_ids 必须为 {expected_node_ids}')
    strict_sync = value.get('strict_sync')
    if not isinstance(strict_sync, dict):
        errors.append('%s 缺少 strict_sync 严格同步事实' % prefix)
    else:
        if strict_sync.get('server_wait_returned_node_ids') != expected_node_ids:
            errors.append(f'{prefix}.strict_sync.server_wait_returned_node_ids 必须为 {expected_node_ids}')
        if strict_sync.get('partial_result_returned') is not False:
            errors.append('%s.strict_sync 必须证明未返回 partial result' % prefix)
        if strict_sync.get('server_waited_after_first_commit') is not True:
            errors.append('%s.strict_sync 必须证明首个客户端提交后 server 保持等待' % prefix)
    active_upload_sets = value.get('active_upload_sets')
    if (not isinstance(active_upload_sets, list) or not active_upload_sets or
            any(not isinstance(node_ids, list) or
                any(node_id not in expected_node_ids for node_id in node_ids)
                for node_ids in active_upload_sets) or
            active_upload_sets[-1] != []):
        errors.append('%s 缺少活动上传集合或上传未收敛' % prefix)
    uploads = value.get('uploads')
    if not isinstance(uploads, list) or len(uploads) != len(expected_node_ids):
        errors.append(f'{prefix} 必须包含 {len(expected_node_ids)} 个 update 提交事实')
        return
    uploads_by_node = {upload.get('node_id'): upload for upload in uploads if isinstance(upload, dict)}
    if set(uploads_by_node) != set(expected_node_ids):
        errors.append(f'{prefix}.update 节点集合必须为 {expected_node_ids}')
        return
    for node_id in expected_node_ids:
        upload = uploads_by_node[node_id]
        if upload.get('size_bytes') != expected_size or not _is_sha256(upload.get('sha256')):
            errors.append('%s node %d update 必须是 %d 字节且含 SHA-256' % (prefix, node_id, expected_size))
        if upload.get('sha256') != template_hashes[str(node_id)]:
            errors.append('%s node %d 未复用对应 update 模板' % (prefix, node_id))
        if not _is_interval(upload.get('client_put_interval')) or not _is_interval(upload.get('server_put_interval')):
            errors.append('%s node %d 缺少 PUT 时间区间' % (prefix, node_id))
        srv_status = upload.get('server_http_status')
        if srv_status == 409:
            errors.append('%s node %d 检测到 upload-in-progress 冲突 (HTTP 409)' % (prefix, node_id))
        elif srv_status in (408, 504):
            errors.append('%s node %d 检测到 HTTP timeout (HTTP %s)' % (prefix, node_id, srv_status))
        if upload.get('client_http_status') != 201 or srv_status != 201 or upload.get('server_outcome') != 'committed':
            errors.append('%s node %d 客户端与服务端提交事实不一致或包含 upload_in_progress' % (prefix, node_id))

    downlink_matrix = value.get('downlink_matrix')
    if downlink_matrix is None:
        errors.append('%s 缺少 downlink_matrix 下行证据' % prefix)
    elif not isinstance(downlink_matrix, dict) or downlink_matrix.get('status') != 'passed':
        errors.append('%s UFTP 下行逐 client 逐文件完成矩阵未通过' % prefix)
    else:
        conn = downlink_matrix.get('uftp_connect_matrix', {})
        files = downlink_matrix.get('uftp_result_matrix', {})
        if conn.get('1') != 'success' or conn.get('2') != 'success':
            errors.append('%s UFTP CONNECT 矩阵未全部通过' % prefix)
        for nid in ('1', '2'):
            node_files = files.get(nid, {})
            if node_files.get('model.bin') != 'copy':
                errors.append('%s client %s UFTP model.bin 接收未成功' % (prefix, nid))
            if node_files.get('model.manifest.json') != 'copy':
                errors.append('%s client %s UFTP model.manifest.json 接收未成功' % (prefix, nid))

    concurrent_put = value.get('concurrent_put')
    if concurrent_put is None or not isinstance(concurrent_put, dict):
        errors.append('%s 缺少 concurrent_put 并发观测事实' % prefix)
    else:
        if not isinstance(concurrent_put.get('natural_overlap'), bool):
            errors.append('%s.concurrent_put 缺少 natural_overlap 标记' % prefix)
        if not isinstance(concurrent_put.get('overlap_duration_seconds'), (int, float)):
            errors.append('%s.concurrent_put 缺少 overlap_duration_seconds' % prefix)
        client_intervals = concurrent_put.get('client_intervals')
        expected_client_str_ids = {str(nid) for nid in expected_node_ids}
        if not isinstance(client_intervals, dict) or set(client_intervals) != expected_client_str_ids:
            errors.append('%s.concurrent_put 缺少客户端 PUT 区间' % prefix)
        else:
            for nid in expected_node_ids:
                s_nid = str(nid)
                c_int = client_intervals.get(s_nid)
                if _is_interval(c_int):
                    if uploads_by_node.get(nid) and c_int != uploads_by_node[nid].get('client_put_interval'):
                        errors.append('%s.concurrent_put node %s 区间与 uploads 不一致' % (prefix, nid))

            valid_ints = [client_intervals[str(nid)] for nid in expected_node_ids if _is_interval(client_intervals.get(str(nid)))]
            if len(valid_ints) >= 2:
                max_start = max(item['start'] for item in valid_ints)
                min_end = min(item['end'] for item in valid_ints)
                calc_overlap = min_end - max_start
                if calc_overlap > 0:
                    expected_overlap = True
                    expected_dur = round(calc_overlap, 3)
                else:
                    max_ov = 0.0
                    for i in range(len(valid_ints)):
                        for j in range(i + 1, len(valid_ints)):
                            ov = min(valid_ints[i]['end'], valid_ints[j]['end']) - max(valid_ints[i]['start'], valid_ints[j]['start'])
                            if ov > max_ov:
                                max_ov = ov
                    expected_overlap = max_ov > 0
                    expected_dur = round(max_ov, 3) if expected_overlap else 0.0

                if concurrent_put.get('natural_overlap') != expected_overlap:
                    errors.append('%s.concurrent_put natural_overlap 标记与实际时间区间矛盾' % prefix)
                actual_dur = concurrent_put.get('overlap_duration_seconds')
                if not isinstance(actual_dur, (int, float)) or abs(actual_dur - expected_dur) > 0.001:
                    errors.append('%s.concurrent_put overlap_duration_seconds 与时间区间计算不符 (报告: %s, 期望: %s)' %
                                  (prefix, actual_dur, expected_dur))

    telem = value.get('telemetry')
    if telem is None:
        errors.append('%s 缺少 telemetry 遥测事实' % prefix)
    elif not isinstance(telem, dict):
        errors.append('%s.telemetry 必须是对象' % prefix)
    else:
        queue = telem.get('queue', {})
        if queue.get('tun_read_pause_total', 0) > 0 and not queue.get('pause_recovered', False):
            errors.append('%s 队列自然暂停后未成功恢复' % prefix)
        by_node = telem.get('loss_and_fec_by_node')
        if by_node is None:
            errors.append('%s telemetry 缺少 loss_and_fec_by_node 分源遥测' % prefix)
        elif not isinstance(by_node, dict):
            errors.append('%s.loss_and_fec_by_node 必须是对象' % prefix)
        else:
            for nid in ('1', '2'):
                if nid not in by_node:
                    errors.append('%s telemetry loss_and_fec_by_node 缺少 client %s' % (prefix, nid))
                elif not isinstance(by_node[nid], dict):
                    errors.append('%s telemetry client %s 遥测数据无效' % (prefix, nid))
                elif by_node[nid].get('sample_count', 0) <= 0:
                    errors.append('%s telemetry client %s 缺少有效 PKT_SRC 遥测采样' % (prefix, nid))
                elif by_node[nid].get('out_packets', 0) <= 0:
                    errors.append('%s telemetry client %s 交付包数 (out_packets) 必须大于 0' % (prefix, nid))


def _is_interval(value):
    return (isinstance(value, dict) and set(value) == {'start', 'end'} and
            isinstance(value['start'], (int, float)) and
            isinstance(value['end'], (int, float)) and
            value['end'] >= value['start'])


def _validate_conclusion(conclusion, summary, errors):
    if not isinstance(conclusion, dict):
        errors.append('conclusion 必须是对象')
        return
    status = conclusion.get('status')
    if status not in ('passed', 'failed'):
        errors.append('conclusion.status 无效')
        return
    sections = [
        summary.get('orchestration'),
        summary.get('pre_runtime_smoke'),
        summary.get('formal_runtime_loop'),
        summary.get('lifecycle'),
    ]
    failed = any(isinstance(section, dict) and section.get('status') != 'passed'
                 for section in sections)
    if status == 'passed' and failed:
        errors.append('存在未通过分区时 conclusion 不能 passed')
    if status == 'failed' and not conclusion.get('reason'):
        errors.append('failed conclusion 必须写明 reason')


def _archive_file_exists(path):
    return isinstance(path, str) and path and os.path.isfile(path)


if __name__ == '__main__':
    raise SystemExit(main())
