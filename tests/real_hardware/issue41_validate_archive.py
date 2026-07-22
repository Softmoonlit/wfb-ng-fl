#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
import sys


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

    _require_status(summary['orchestration'], 'orchestration', errors)
    smoke = summary['pre_runtime_smoke']
    if not isinstance(smoke, dict):
        errors.append('pre_runtime_smoke 必须是对象')
        return errors
    _validate_smoke_section(archive_dir, smoke.get('downlink_uftp'),
                            'downlink_uftp', errors)
    _validate_smoke_section(archive_dir, smoke.get('uplink_http_put'),
                            'uplink_http_put', errors)
    _validate_runtime(summary['formal_runtime_loop'], errors)
    _require_status(summary['lifecycle'], 'lifecycle', errors)
    _validate_conclusion(summary['conclusion'], summary, errors)
    return errors


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


def _require_status(value, name, errors):
    if not isinstance(value, dict):
        errors.append('%s 必须是对象' % name)
        return
    if value.get('status') not in ('passed', 'failed', 'skipped'):
        errors.append('%s.status 无效' % name)


def _validate_smoke_section(archive_dir, value, name, errors):
    section_name = 'pre_runtime_smoke.%s' % name
    _require_status(value, section_name, errors)
    if not isinstance(value, dict):
        return
    if value.get('status') != 'passed':
        return
    marker_path = os.path.join(
        archive_dir, 'pre_runtime_smoke', name, 'passed.json')
    marker = _read_json_file(marker_path, 'smoke marker %s' % name, errors)
    if marker is None:
        return
    if marker.get('schema_version') != 1:
        errors.append('%s marker schema_version 无效' % section_name)
    if marker.get('smoke') != name or marker.get('status') != 'passed':
        errors.append('%s marker 内容与通过状态不一致' % section_name)


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


def _validate_formal_scenario(value, errors):
    scenario = value.get('scenario')
    expected_size = 40 * 1024 * 1024
    if not isinstance(scenario, dict):
        errors.append('formal_runtime_loop 缺少正式场景配置')
        return
    expected = {
        'round_count': 2,
        'artifact_size_bytes': expected_size,
        'training_delay_ms_by_node': {'1': 0, '2': 0},
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
    if not isinstance(rounds, list) or len(rounds) != 2:
        errors.append('formal_runtime_loop 必须包含两轮完整证据')
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
        errors.append('%s.model 必须是 40 MiB 且含 SHA-256' % prefix)
    intervals = value.get('model_receive_intervals')
    if not isinstance(intervals, dict) or set(intervals) != {'1', '2'} or any(not _is_interval(intervals[node]) for node in intervals):
        errors.append('%s 缺少两个节点的模型接收时间区间' % prefix)
    if value.get('server_committed_node_ids') != [1, 2]:
        errors.append('%s.server_committed_node_ids 必须为 [1, 2]' % prefix)
    if value.get('server_wait_returned_node_ids') != [1, 2]:
        errors.append('%s.server_wait_returned_node_ids 必须为 [1, 2]' % prefix)
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
            errors.append('%s node %d update 必须是 40 MiB 且含 SHA-256' % (prefix, node_id))
        if upload.get('sha256') != template_hashes[str(node_id)]:
            errors.append('%s node %d 未复用对应 update 模板' % (prefix, node_id))
        if not _is_interval(upload.get('client_put_interval')) or not _is_interval(upload.get('server_put_interval')):
            errors.append('%s node %d 缺少 PUT 时间区间' % (prefix, node_id))
        if upload.get('client_http_status') != 201 or upload.get('server_http_status') != 201 or upload.get('server_outcome') != 'committed':
            errors.append('%s node %d 客户端与服务端提交事实不一致或包含 upload_in_progress' % (prefix, node_id))


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
        summary.get('pre_runtime_smoke', {}).get('downlink_uftp')
        if isinstance(summary.get('pre_runtime_smoke'), dict) else None,
        summary.get('pre_runtime_smoke', {}).get('uplink_http_put')
        if isinstance(summary.get('pre_runtime_smoke'), dict) else None,
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
