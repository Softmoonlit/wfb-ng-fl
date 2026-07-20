#!/usr/bin/env python
# -*- coding: utf-8 -*-

import math
import os
import time
import uuid
from datetime import datetime, timezone

from .artifacts import file_sha256, read_json, write_json_atomic
from .errors import FLRuntimeError


_SERVER_FIELDS = {'model_path', 'participant_node_ids'}
_SERVER_OPTIONAL_FIELDS = {'result_path', 'start_gate_path',
                           'start_gate_timeout_seconds'}
_CLIENT_FIELDS = {'node_id', 'participant_node_ids', 'delay_seconds'}
_MODEL_MANIFEST_FIELDS = {
    'schema_version', 'artifact_type', 'round_id', 'size_bytes', 'sha256',
    'participant_node_ids',
}
_UPDATE_FIELDS = {
    'schema_version', 'artifact_type', 'node_id', 'participant_node_ids',
    'model_size_bytes', 'model_sha256',
}


def server(runtime, config):
    """Run one deterministic server acceptance round."""
    config = _validate_server_config(runtime, config)
    model_path = config['model_path']
    participants = config['participant_node_ids']
    model_size = _file_size(model_path, '无法读取模型源文件')
    model_sha256 = _file_digest(model_path, '无法校验模型源文件')
    timings = {}

    _wait_start_gate(config)
    _call_timed(timings, 'publish_model', runtime.publish_model, model_path)
    updates = _call_timed(timings, 'wait_for_updates', runtime.wait_for_updates)
    update_mapping = _validate_updates(
        updates, participants, model_size, model_sha256)

    result = {
        'schema_version': 1,
        'role': 'server',
        'status': 'succeeded',
        'participant_node_ids': list(participants),
        'interface_timings': timings,
        'input_sha256': model_sha256,
        'output_sha256_by_node': {
            str(item['node_id']): item['sha256'] for item in update_mapping
        },
        'update_mapping': update_mapping,
    }
    _write_result(config['result_path'], result)
    return None


def client(runtime, config):
    """Run one deterministic client acceptance round."""
    config = _validate_client_config(runtime, config)
    node_id = config['node_id']
    participants = config['participant_node_ids']
    timings = {}

    model_path = _call_timed(
        timings, 'wait_for_model', runtime.wait_for_model)
    manifest = _validate_managed_model(model_path, participants)
    model_sha256 = _file_digest(
        model_path, '无法校验 Runtime 托管模型')
    model_size = _file_size(model_path, '无法读取 Runtime 托管模型')
    if model_size != manifest['size_bytes'] or model_sha256 != manifest['sha256']:
        raise FLRuntimeError(
            'acceptance_validation_failed', '模型大小或 SHA-256 校验失败')

    time.sleep(config['delay_seconds'])
    update_path = os.path.join(
        runtime.work_dir, 'acceptance-fixture', 'update-%d.json' % node_id)
    update = {
        'schema_version': 1,
        'artifact_type': 'deterministic-update',
        'node_id': node_id,
        'participant_node_ids': list(participants),
        'model_size_bytes': model_size,
        'model_sha256': model_sha256,
    }
    try:
        write_json_atomic(update_path, update)
    except (OSError, TypeError, ValueError) as exc:
        raise FLRuntimeError(
            'acceptance_update_failed', '确定性 update 写入失败',
            node_id=node_id) from exc
    update_sha256 = _file_digest(update_path, '无法校验确定性 update')
    _call_timed(
        timings, 'submit_update', runtime.submit_update, update_path)

    result = {
        'schema_version': 1,
        'role': 'client',
        'status': 'succeeded',
        'node_id': node_id,
        'participant_node_ids': list(participants),
        'interface_timings': timings,
        'configured_delay_seconds': config['delay_seconds'],
        'input_sha256': model_sha256,
        'output_sha256': update_sha256,
        'update_mapping': [{'node_id': node_id, 'sha256': update_sha256}],
    }
    _write_result(config['result_path'], result)
    return None


def _validate_server_config(runtime, config):
    _validate_config_object(
        config, _SERVER_FIELDS, optional_fields=_SERVER_OPTIONAL_FIELDS)
    model_path = _validate_absolute_path(config['model_path'], '模型源路径')
    participants = _validate_participants(config['participant_node_ids'])
    runtime_participants = getattr(runtime, 'participant_node_ids', None)
    if runtime_participants is not None and tuple(runtime_participants) != participants:
        raise FLRuntimeError(
            'invalid_acceptance_config', '配置参与集合与 Runtime 不一致')
    result = {
        'model_path': model_path,
        'participant_node_ids': participants,
        'result_path': _result_path(runtime, config, 'server-result.json'),
    }
    gate_path = config.get('start_gate_path')
    if gate_path is not None:
        result['start_gate_path'] = _validate_absolute_path(
            gate_path, '启动门路径')
        timeout = config.get('start_gate_timeout_seconds', 300)
        if (type(timeout) not in (int, float) or isinstance(timeout, bool) or
                not math.isfinite(timeout) or timeout <= 0):
            raise FLRuntimeError(
                'invalid_acceptance_config', '启动门超时配置无效')
        result['start_gate_timeout_seconds'] = timeout
    elif 'start_gate_timeout_seconds' in config:
        raise FLRuntimeError(
            'invalid_acceptance_config', '启动门超时缺少启动门路径')
    return result


def _validate_client_config(runtime, config):
    _validate_config_object(config, _CLIENT_FIELDS)
    node_id = config['node_id']
    if type(node_id) is not int or node_id <= 0:
        raise FLRuntimeError(
            'invalid_acceptance_config', '客户端节点标识无效')
    participants = _validate_participants(config['participant_node_ids'])
    if node_id not in participants:
        raise FLRuntimeError(
            'invalid_acceptance_config', '客户端节点不在参与集合中')
    runtime_node_id = getattr(runtime, 'node_id', None)
    if runtime_node_id is not None and runtime_node_id != node_id:
        raise FLRuntimeError(
            'invalid_acceptance_config', '配置节点与 Runtime 不一致')
    delay = config['delay_seconds']
    if (type(delay) not in (int, float) or isinstance(delay, bool) or
            not math.isfinite(delay) or delay < 0):
        raise FLRuntimeError(
            'invalid_acceptance_config', '客户端 delay 配置无效')
    return {
        'node_id': node_id,
        'participant_node_ids': participants,
        'delay_seconds': delay,
        'result_path': _result_path(
            runtime, config, 'client-%d-result.json' % node_id),
    }


def _validate_config_object(config, required_fields, optional_fields=None):
    if not isinstance(config, dict):
        raise FLRuntimeError(
            'invalid_acceptance_config', '验收算法配置必须是 JSON object')
    optional_fields = set(optional_fields or ()) | {'result_path'}
    fields = set(config)
    if not required_fields.issubset(fields) or fields - required_fields - optional_fields:
        raise FLRuntimeError(
            'invalid_acceptance_config', '验收算法配置字段无效')


def _validate_participants(value):
    if (not isinstance(value, list) or not value or
            any(type(node_id) is not int or node_id <= 0 for node_id in value) or
            value != sorted(set(value))):
        raise FLRuntimeError(
            'invalid_acceptance_config', '参与节点集合必须数值升序且无重复')
    return tuple(value)


def _validate_absolute_path(value, name):
    if not isinstance(value, str) or not value or not os.path.isabs(value):
        raise FLRuntimeError(
            'invalid_acceptance_config', '%s必须是绝对路径' % name)
    return os.path.abspath(value)


def _result_path(runtime, config, default_name):
    if not isinstance(getattr(runtime, 'work_dir', None), str):
        raise FLRuntimeError(
            'invalid_acceptance_config', 'Runtime 工作目录无效')
    value = config.get('result_path')
    if value is None:
        return os.path.join(
            runtime.work_dir, 'acceptance-fixture', default_name)
    return _validate_absolute_path(value, '作业结果路径')


def _validate_managed_model(model_path, participants):
    try:
        model_path = os.path.abspath(os.fspath(model_path))
    except (TypeError, ValueError, OSError) as exc:
        raise FLRuntimeError(
            'acceptance_validation_failed', 'Runtime 托管模型路径无效') from exc
    try:
        manifest = read_json(
            os.path.join(os.path.dirname(model_path), 'model.manifest.json'))
    except FLRuntimeError as exc:
        raise FLRuntimeError(
            'acceptance_validation_failed', '无法独立读取模型 manifest') from exc
    if not isinstance(manifest, dict) or set(manifest) != _MODEL_MANIFEST_FIELDS:
        raise FLRuntimeError(
            'acceptance_validation_failed', '模型 manifest 字段无效')
    try:
        parsed_round_id = uuid.UUID(manifest['round_id'])
        round_id = str(parsed_round_id)
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise FLRuntimeError(
            'acceptance_validation_failed', '模型 manifest 轮次标识无效') from exc
    if (type(manifest['schema_version']) is not int or
            manifest['schema_version'] != 1 or
            manifest['artifact_type'] != 'model' or
            parsed_round_id.version != 4 or
            round_id != manifest['round_id'] or
            type(manifest['size_bytes']) is not int or
            manifest['size_bytes'] < 0 or
            not _is_sha256(manifest['sha256']) or
            manifest['participant_node_ids'] != list(participants)):
        raise FLRuntimeError(
            'acceptance_validation_failed', '模型 manifest 校验失败')
    return manifest


def _validate_updates(updates, participants, model_size, model_sha256):
    try:
        node_ids = list(updates)
    except (TypeError, ValueError) as exc:
        raise FLRuntimeError(
            'acceptance_validation_failed', 'update 映射无效') from exc
    if node_ids != list(participants):
        raise FLRuntimeError(
            'acceptance_validation_failed', 'update 映射未按数值升序完整返回')
    mapping = []
    for node_id in participants:
        try:
            update_path = os.path.abspath(os.fspath(updates[node_id]))
        except (KeyError, TypeError, ValueError, OSError) as exc:
            raise FLRuntimeError(
                'acceptance_validation_failed', 'update 映射路径无效') from exc
        try:
            update = read_json(update_path)
        except FLRuntimeError as exc:
            raise FLRuntimeError(
                'acceptance_validation_failed',
                '无法独立读取 Runtime 托管 update') from exc
        if (not isinstance(update, dict) or set(update) != _UPDATE_FIELDS or
                type(update['schema_version']) is not int or
                update['schema_version'] != 1 or
                update['artifact_type'] != 'deterministic-update' or
                type(update['node_id']) is not int or
                update['node_id'] != node_id or
                update['participant_node_ids'] != list(participants) or
                type(update['model_size_bytes']) is not int or
                update['model_size_bytes'] != model_size or
                update['model_sha256'] != model_sha256):
            raise FLRuntimeError(
                'acceptance_validation_failed', '确定性 update 内容校验失败')
        mapping.append({
            'node_id': node_id,
            'path': update_path,
            'sha256': _file_digest(
                update_path, '无法校验 Runtime 托管 update'),
        })
    return mapping


def _wait_start_gate(config):
    path = config.get('start_gate_path')
    if path is None:
        return
    deadline = time.monotonic() + config['start_gate_timeout_seconds']
    while time.monotonic() < deadline:
        if os.path.isfile(path):
            return
        time.sleep(0.1)
    raise FLRuntimeError(
        'acceptance_start_timeout', '等待验收启动门超时')


def _call_timed(timings, name, call, *args):
    started_at = _timestamp()
    started_monotonic = time.monotonic()
    try:
        return call(*args)
    finally:
        timings[name] = {
            'started_at': started_at,
            'completed_at': _timestamp(),
            'elapsed_seconds': time.monotonic() - started_monotonic,
        }


def _timestamp():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _file_size(path, message):
    try:
        return os.path.getsize(path)
    except (OSError, TypeError, ValueError) as exc:
        raise FLRuntimeError('acceptance_validation_failed', message) from exc


def _file_digest(path, message):
    try:
        return file_sha256(path)
    except FLRuntimeError as exc:
        raise FLRuntimeError('acceptance_validation_failed', message) from exc


def _is_sha256(value):
    return (isinstance(value, str) and len(value) == 64 and
            all(character in '0123456789abcdef' for character in value))


def _write_result(path, result):
    try:
        write_json_atomic(path, result)
    except (OSError, TypeError, ValueError) as exc:
        raise FLRuntimeError(
            'acceptance_result_failed', '验收作业结果写入失败') from exc


__all__ = ('client', 'server')
