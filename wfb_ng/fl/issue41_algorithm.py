#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import time

from .artifacts import archive_file, file_sha256, inspect_artifact, read_json, write_json_atomic
from .errors import FLRuntimeError


ALGORITHM_VERSION = 'issue41-opaque-file-placeholder-v1'


def server_main(runtime, config):
    """运行服务端多轮作业：发布模型、收齐 update、聚合下一轮模型。"""
    rounds = _positive_int(config.get('rounds', 1), 'rounds')
    expected_nodes = _node_ids(
        config.get('participant_node_ids',
                   getattr(runtime, 'participant_node_ids', ())),
        'participant_node_ids')
    initial_model_path = _required_artifact(
        config.get('initial_model_path'), 'initial_model_path')
    required_size = _required_artifact_size(config)
    _require_size(initial_model_path, required_size, 'initial_model_path')
    artifact_dir = _artifact_dir(runtime, config)
    result_path = config.get('result_path') or os.path.join(
        runtime.work_dir, 'issue41-server-result.json')
    events = []
    rounds_result = []
    model_path = initial_model_path

    for round_index in range(1, rounds + 1):
        published_model_path = model_path
        input_model_sha256 = file_sha256(published_model_path)
        events.append(_event(
            'publish_model_start', round_index=round_index,
            model_sha256=input_model_sha256))
        runtime.publish_model(published_model_path)
        events.append(_event(
            'publish_model_done', round_index=round_index,
            model_sha256=input_model_sha256))

        events.append(_event(
            'wait_for_updates_start', round_index=round_index))
        updates_by_node = runtime.wait_for_updates()
        ordered_nodes = tuple(updates_by_node)
        round_id = _round_id_for_update(updates_by_node[ordered_nodes[0]])
        if ordered_nodes != expected_nodes:
            raise FLRuntimeError(
                'algorithm_failed', 'server 收到的 update 集合不完整或顺序错误')
        events.append(_event(
            'wait_for_updates_done', round_index=round_index,
            round_id=round_id, update_node_ids=list(ordered_nodes)))

        output_model_path = os.path.join(
            artifact_dir, 'global-model-round-%04d.bin' % round_index)
        events.append(_event(
            'aggregate_start', round_index=round_index,
            update_node_ids=list(ordered_nodes)))
        model_path = aggregate(
            model_path=published_model_path,
            updates_by_node=updates_by_node,
            output_model_path=output_model_path,
            config=config)
        output_model_sha256 = file_sha256(model_path)
        events.append(_event(
            'aggregate_done', round_index=round_index,
            output_model_sha256=output_model_sha256))

        rounds_result.append({
            'round_index': round_index,
            'round_id': round_id,
            'input_model_path': published_model_path,
            'input_model_size_bytes': os.path.getsize(published_model_path),
            'input_model_sha256': input_model_sha256,
            'update_node_ids': list(ordered_nodes),
            'updates': [
                {
                    'node_id': node_id,
                    'path': updates_by_node[node_id],
                    'size_bytes': os.path.getsize(updates_by_node[node_id]),
                    'sha256': file_sha256(updates_by_node[node_id]),
                }
                for node_id in ordered_nodes
            ],
            'aggregation_delay_ms': _aggregation_delay_ms(config),
            'output_model_path': model_path,
            'output_model_size_bytes': os.path.getsize(model_path),
            'output_model_sha256': output_model_sha256,
        })

    write_json_atomic(result_path, {
        'schema_version': 1,
        'role': 'server',
        'algorithm_version': ALGORITHM_VERSION,
        'initial_model_path': initial_model_path,
        'initial_model_size_bytes': os.path.getsize(initial_model_path),
        'initial_model_sha256': file_sha256(initial_model_path),
        'rounds': rounds_result,
        'final_model_path': model_path,
        'final_model_sha256': file_sha256(model_path),
        'events': events,
        'conclusion': 'succeeded',
    })


def client_main(runtime, config):
    """运行客户端多轮作业：等待模型、本地训练、提交 update。"""
    rounds = _positive_int(config.get('rounds', 1), 'rounds')
    node_id = _positive_int(
        config.get('node_id', getattr(runtime, 'node_id', None)), 'node_id')
    update_template_path = _required_artifact(
        config.get('update_template_path'), 'update_template_path')
    required_size = _required_artifact_size(config)
    _require_size(update_template_path, required_size, 'update_template_path')
    artifact_dir = _artifact_dir(runtime, config)
    result_path = config.get('result_path') or os.path.join(
        runtime.work_dir, 'issue41-client-result.json')
    events = []
    rounds_result = []

    for round_index in range(1, rounds + 1):
        model_receive_start = time.monotonic()
        events.append(_event(
            'wait_for_model_start', round_index=round_index,
            node_id=node_id))
        model_path = runtime.wait_for_model()
        model_sha256 = file_sha256(model_path)
        round_id = _round_id_for_model(model_path)
        _require_size(model_path, required_size, 'received model')
        events.append(_event(
            'wait_for_model_done', round_index=round_index,
            round_id=round_id, node_id=node_id, model_sha256=model_sha256))
        model_receive_end = time.monotonic()

        update_path = os.path.join(
            artifact_dir,
            'local-update-round-%04d-node-%d.bin' % (round_index, node_id))
        events.append(_event(
            'train_start', round_index=round_index, node_id=node_id))
        update_path = train(
            model_path=model_path,
            output_update_path=update_path,
            config=config)
        update_sha256 = file_sha256(update_path)
        events.append(_event(
            'train_done', round_index=round_index, node_id=node_id,
            update_sha256=update_sha256))

        submit_start = time.monotonic()
        events.append(_event(
            'submit_update_start', round_index=round_index,
            round_id=round_id, node_id=node_id, update_sha256=update_sha256))
        runtime.submit_update(update_path)
        submit_end = time.monotonic()
        events.append(_event(
            'submit_update_done', round_index=round_index,
            round_id=round_id, node_id=node_id, update_sha256=update_sha256))

        rounds_result.append({
            'round_index': round_index,
            'round_id': round_id,
            'node_id': node_id,
            'model_path': model_path,
            'model_size_bytes': os.path.getsize(model_path),
            'model_sha256': model_sha256,
            'model_receive_interval': {
                'start': model_receive_start, 'end': model_receive_end,
            },
            'update_path': update_path,
            'update_size_bytes': os.path.getsize(update_path),
            'update_sha256': update_sha256,
            'put_interval': {'start': submit_start, 'end': submit_end},
            'training_delay_ms': _training_delay_ms(config),
        })

    write_json_atomic(result_path, {
        'schema_version': 1,
        'role': 'client',
        'algorithm_version': ALGORITHM_VERSION,
        'node_id': node_id,
        'update_template_path': update_template_path,
        'update_template_sha256': file_sha256(update_template_path),
        'rounds': rounds_result,
        'events': events,
        'conclusion': 'succeeded',
    })


def train(model_path, output_update_path, config):
    """占位训练边界；真实实现应读取模型和本地数据并写出 update。"""
    inspect_artifact(model_path)
    update_template_path = _required_artifact(
        config.get('update_template_path'), 'update_template_path')
    delay_ms = _training_delay_ms(config)
    if delay_ms:
        time.sleep(delay_ms / 1000.0)

    # 当前只复制任意参数文件；替换真实训练时保留函数签名和返回约定。
    archive_file(update_template_path, output_update_path)
    return output_update_path


def aggregate(model_path, updates_by_node, output_model_path, config):
    """占位聚合边界；真实实现应读取全部 update 并写出下一轮模型。"""
    inspect_artifact(model_path)
    if not updates_by_node:
        raise FLRuntimeError('algorithm_failed', '聚合输入不能为空')
    for update_path in updates_by_node.values():
        inspect_artifact(update_path)

    delay_ms = _aggregation_delay_ms(config)
    if delay_ms:
        time.sleep(delay_ms / 1000.0)

    # 当前不改变模型内容；替换真实聚合时在 output_model_path 写入新模型。
    archive_file(model_path, output_model_path)
    return output_model_path


def _artifact_dir(runtime, config):
    path = config.get('artifact_dir') or os.path.join(
        runtime.work_dir, 'issue41-algorithm')
    try:
        return os.path.abspath(os.fspath(path))
    except (TypeError, ValueError) as exc:
        raise FLRuntimeError(
            'invalid_configuration', 'artifact_dir 必须是有效路径') from exc


def _required_artifact(path, name):
    if path is None:
        raise FLRuntimeError(
            'invalid_configuration', '%s 为必填项' % name)
    try:
        return inspect_artifact(path)
    except FLRuntimeError as exc:
        raise FLRuntimeError(
            'invalid_configuration', '%s 必须指向可读取普通文件' % name) from exc


def _round_id_for_model(model_path):
    manifest = read_json(os.path.join(os.path.dirname(model_path), 'model.manifest.json'))
    return manifest['round_id']


def _round_id_for_update(update_path):
    manifest = read_json(os.path.join(os.path.dirname(update_path), 'update.manifest.json'))
    return manifest['round_id']


def _required_artifact_size(config):
    value = config.get('required_artifact_size_bytes')
    if value is None:
        return None
    return _positive_int(value, 'required_artifact_size_bytes')


def _require_size(path, expected_size, name):
    if expected_size is not None and os.path.getsize(path) != expected_size:
        raise FLRuntimeError(
            'invalid_configuration', '%s 必须恰好为 %d 字节' % (name, expected_size))


def _training_delay_ms(config):
    return _non_negative_int(
        config.get('training_delay_ms', 0), 'training_delay_ms')


def _aggregation_delay_ms(config):
    return _non_negative_int(
        config.get('aggregation_delay_ms', 0), 'aggregation_delay_ms')


def _event(name, **fields):
    value = {'name': name, 'monotonic_time': time.monotonic()}
    value.update(fields)
    return value


def _positive_int(value, name):
    if type(value) is not int or value <= 0:
        raise FLRuntimeError('invalid_configuration', '%s 必须是正整数' % name)
    return value


def _non_negative_int(value, name):
    if type(value) is not int or value < 0:
        raise FLRuntimeError('invalid_configuration', '%s 必须是非负整数' % name)
    return value


def _node_ids(values, name):
    result = tuple(values)
    if (not result or
            any(type(item) is not int or item <= 0 for item in result) or
            tuple(sorted(set(result))) != result):
        raise FLRuntimeError('invalid_configuration', '%s 无效' % name)
    return result
