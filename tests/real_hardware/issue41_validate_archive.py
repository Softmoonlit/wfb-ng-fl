#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
import sys

from tests.real_hardware.issue41_gate import GateConfig, validate_gate_summary
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
            smoke = summary.get('pre_runtime_smoke', {})
            if isinstance(smoke, dict):
                if 'cycle_deadline_seconds' in smoke and smoke['cycle_deadline_seconds'] != resolved.get('smoke_cycle_deadline_seconds'):
                    errors.append('运行中修改配置：pre_runtime_smoke cycle_deadline_seconds 与 envelope 不一致')
                if 'io_timeout_seconds' in smoke and smoke['io_timeout_seconds'] != resolved.get('io_timeout_seconds'):
                    errors.append('运行中修改配置：pre_runtime_smoke io_timeout_seconds 与 envelope 不一致')
            formal = summary.get('formal_runtime_loop', {})
            if isinstance(formal, dict) and 'scenario' in formal and isinstance(formal['scenario'], dict):
                sc = formal['scenario']
                if 'round_deadline_seconds' in sc and sc['round_deadline_seconds'] != resolved.get('runtime_timeout_seconds'):
                    errors.append('运行中修改配置：formal_runtime_loop round_deadline_seconds 与 envelope 不一致')
                if 'io_timeout_seconds' in sc and sc['io_timeout_seconds'] != resolved.get('io_timeout_seconds'):
                    errors.append('运行中修改配置：formal_runtime_loop io_timeout_seconds 与 envelope 不一致')
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

    config = GateConfig()
    gate_errors = validate_gate_summary(value, config)
    for ge in gate_errors:
        errors.append('pre_runtime_smoke 校验失败：%s' % ge)


def _validate_runtime(value, errors):
    _require_status(value, 'formal_runtime_loop', errors)
    if not isinstance(value, dict):
        return
    if value.get('status') != 'passed':
        return
    required = {
        'runtime_interfaces': [
            'publish_model', 'wait_for_model', 'submit_update',
            'wait_for_updates'],
        'data_plane': '10.80.0.0/24',
        'server_wait_for_updates_returned_node_ids': [1, 2],
    }
    for key, expected in required.items():
        if value.get(key) != expected:
            errors.append('formal_runtime_loop.%s 不满足通过条件' % key)
    _validate_formal_scenario(value, errors)
    if value.get('partial_result_returned') is not False:
        errors.append('formal_runtime_loop 必须证明 server 未返回 partial result')
    for role in ('server_result', 'client1_result', 'client2_result',
                 'server_journal', 'client1_journal', 'client2_journal'):
        path = value.get(role)
        if not _archive_file_exists(path):
            errors.append('formal_runtime_loop 缺少 %s 文件证据' % role)
    route_evidence = value.get('route_evidence')
    if (not isinstance(route_evidence, list) or len(route_evidence) != 12 or
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
    expected_size = 4 * 1024 * 1024
    if not isinstance(scenario, dict):
        errors.append('formal_runtime_loop 缺少正式场景配置')
        return
    expected = {
        'round_count': 1,
        'artifact_size_bytes': expected_size,
        'training_delay_ms_by_node': {'1': 0, '2': 3000},
        'placeholder_training': 'template_copy',
        'placeholder_aggregation': 'model_copy',
    }
    for key, expected_value in expected.items():
        if scenario.get(key) != expected_value:
            errors.append('formal_runtime_loop.scenario.%s 不满足通过条件' % key)
    template_hashes = scenario.get('update_template_sha256_by_node')
    if (not isinstance(template_hashes, dict) or set(template_hashes) != {'1', '2'} or
            not all(_is_sha256(digest) for digest in template_hashes.values()) or
            template_hashes['1'] == template_hashes['2']):
        errors.append('formal_runtime_loop.scenario 必须包含不同的 client 模板 SHA-256')

    rounds = value.get('rounds')
    if not isinstance(rounds, list) or len(rounds) != 1:
        errors.append('formal_runtime_loop 必须包含一轮完整证据')
        return
    seen_round_ids = set()
    for index, round_value in enumerate(rounds, 1):
        _validate_round(
            round_value, index, expected_size, template_hashes,
            seen_round_ids, errors)


def _validate_round(value, index, expected_size, template_hashes, seen_round_ids, errors):
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
        errors.append('%s.model 必须是 4 MiB 且含 SHA-256' % prefix)
    intervals = value.get('model_receive_intervals')
    if not isinstance(intervals, dict) or set(intervals) != {'1', '2'} or any(not _is_interval(intervals[node]) for node in intervals):
        errors.append('%s 缺少两个节点的模型接收时间区间' % prefix)
    if value.get('server_committed_node_ids') != [1, 2]:
        errors.append('%s.server_committed_node_ids 必须为 [1, 2]' % prefix)
    if value.get('server_wait_returned_node_ids') != [1, 2]:
        errors.append('%s.server_wait_returned_node_ids 必须为 [1, 2]' % prefix)
    strict_sync = value.get('strict_sync')
    if not isinstance(strict_sync, dict):
        errors.append('%s 缺少 strict_sync 严格同步事实' % prefix)
    else:
        if strict_sync.get('server_wait_returned_node_ids') != [1, 2]:
            errors.append('%s.strict_sync.server_wait_returned_node_ids 必须为 [1, 2]' % prefix)
        if strict_sync.get('partial_result_returned') is not False:
            errors.append('%s.strict_sync 必须证明未返回 partial result' % prefix)
        if strict_sync.get('client1_committed_before_client2') is not True:
            errors.append('%s.strict_sync 必须证明 client1 先于 client2 完成提交' % prefix)
        if strict_sync.get('server_waited_after_client1') is not True:
            errors.append('%s.strict_sync 必须证明 client1 提交后 server 保持等待' % prefix)
    active_upload_sets = value.get('active_upload_sets')
    if (not isinstance(active_upload_sets, list) or not active_upload_sets or
            any(not isinstance(node_ids, list) or
                any(node_id not in (1, 2) for node_id in node_ids)
                for node_ids in active_upload_sets) or
            active_upload_sets[-1] != []):
        errors.append('%s 缺少活动上传集合或上传未收敛' % prefix)
    uploads = value.get('uploads')
    if not isinstance(uploads, list) or len(uploads) != 2:
        errors.append('%s 必须包含两个 update 提交事实' % prefix)
        return
    uploads_by_node = {upload.get('node_id'): upload for upload in uploads if isinstance(upload, dict)}
    if set(uploads_by_node) != {1, 2}:
        errors.append('%s.update 节点集合必须为 [1, 2]' % prefix)
        return
    for node_id in (1, 2):
        upload = uploads_by_node[node_id]
        if upload.get('size_bytes') != expected_size or not _is_sha256(upload.get('sha256')):
            errors.append('%s node %d update 必须是 4 MiB 且含 SHA-256' % (prefix, node_id))
        if upload.get('sha256') != template_hashes[str(node_id)]:
            errors.append('%s node %d 未复用对应 update 模板' % (prefix, node_id))
        if not _is_interval(upload.get('client_put_interval')) or not _is_interval(upload.get('server_put_interval')):
            errors.append('%s node %d 缺少 PUT 时间区间' % (prefix, node_id))
        if upload.get('client_http_status') != 201 or upload.get('server_http_status') != 201 or upload.get('server_outcome') != 'committed':
            errors.append('%s node %d 客户端与服务端提交事实不一致或包含 upload_in_progress' % (prefix, node_id))

    downlink_matrix = value.get('downlink_matrix')
    if downlink_matrix is not None:
        if not isinstance(downlink_matrix, dict) or downlink_matrix.get('status') != 'passed':
            errors.append('%s UFTP 下行逐 client 逐文件完成矩阵未通过' % prefix)
        else:
            conn = downlink_matrix.get('uftp_connect_matrix', {})
            files = downlink_matrix.get('uftp_result_matrix', {})
            if conn.get('1') != 'success' or conn.get('2') != 'success':
                errors.append('%s UFTP CONNECT 矩阵未全部通过' % prefix)
            if (files.get('1', {}).get('model.bin') != 'copy' or
                    files.get('2', {}).get('model.bin') != 'copy'):
                errors.append('%s UFTP 文件接收矩阵未全部 copy' % prefix)

    telem = value.get('telemetry')
    if telem is not None:
        if not isinstance(telem, dict):
            errors.append('%s.telemetry 必须是对象' % prefix)
        else:
            queue = telem.get('queue', {})
            if queue.get('tun_read_pause_total', 0) > 0 and not queue.get('pause_recovered', False):
                errors.append('%s 队列自然暂停后未成功恢复' % prefix)


def _is_sha256(value):
    return (isinstance(value, str) and len(value) == 64 and
            all(char in '0123456789abcdef' for char in value))


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
