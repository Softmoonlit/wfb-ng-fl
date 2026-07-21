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
    timing = value.get('update_timing', {})
    if not isinstance(timing, dict) or not timing.get('client1_before_client2'):
        errors.append('formal_runtime_loop 缺少 client1 早于 client2 的证据')
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
