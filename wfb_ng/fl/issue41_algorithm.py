#!/usr/bin/env python
# -*- coding: utf-8 -*-

import hashlib
import json
import os
import time

from .artifacts import file_sha256, read_json, write_json_atomic
from .errors import FLRuntimeError


FIXTURE_VERSION = 'issue41-v1'


def server_main(runtime, config):
    rounds = _positive_int(config.get('rounds', 1), 'rounds')
    expected_nodes = tuple(config.get(
        'participant_node_ids', getattr(runtime, 'participant_node_ids', ())))
    expected_nodes = _node_ids(expected_nodes, 'participant_node_ids')
    result_path = config.get('result_path') or os.path.join(
        runtime.work_dir, 'issue41-server-result.json')
    fixture_dir = os.path.join(runtime.work_dir, 'issue41-fixture')
    events = []
    rounds_result = []

    for index in range(1, rounds + 1):
        model_path = os.path.join(fixture_dir, 'round-%04d-model.bin' % index)
        _write_bytes(model_path, _model_bytes(index, expected_nodes, config))
        model_sha256 = file_sha256(model_path)
        events.append(_event('publish_model_start', round_index=index,
                             model_sha256=model_sha256))
        runtime.publish_model(model_path)
        round_id = _current_round_id(runtime.work_dir, 'server')
        events.append(_event('publish_model_done', round_index=index,
                             round_id=round_id))
        events.append(_event('wait_for_updates_start', round_index=index,
                             round_id=round_id))
        updates = runtime.wait_for_updates()
        ordered_nodes = list(updates)
        if ordered_nodes != list(expected_nodes):
            raise FLRuntimeError(
                'issue41_fixture_failed', 'server 收到的 update 集合不完整或顺序错误')
        update_entries = []
        for node_id in ordered_nodes:
            update_sha256 = file_sha256(updates[node_id])
            update_entries.append({
                'node_id': node_id,
                'path': updates[node_id],
                'sha256': update_sha256,
                'expected_sha256': _update_sha256(
                    round_id, node_id, model_sha256,
                    _client_seed(config, node_id),
                    config.get('fixture_version', FIXTURE_VERSION)),
            })
        for entry in update_entries:
            if entry['sha256'] != entry['expected_sha256']:
                raise FLRuntimeError(
                    'issue41_fixture_failed', 'update 确定性摘要不匹配')
        events.append(_event('wait_for_updates_done', round_index=index,
                             round_id=round_id, update_node_ids=ordered_nodes))
        rounds_result.append({
            'round_index': index,
            'round_id': round_id,
            'model_sha256': model_sha256,
            'update_node_ids': ordered_nodes,
            'updates': update_entries,
        })

    write_json_atomic(result_path, {
        'schema_version': 1,
        'role': 'server',
        'fixture_version': config.get('fixture_version', FIXTURE_VERSION),
        'rounds': rounds_result,
        'events': events,
        'conclusion': 'succeeded',
    })


def client_main(runtime, config):
    rounds = _positive_int(config.get('rounds', 1), 'rounds')
    node_id = _positive_int(config.get('node_id', getattr(runtime, 'node_id', None)),
                            'node_id')
    delay_ms = _non_negative_int(config.get('training_delay_ms', 0),
                                 'training_delay_ms')
    seed = _client_seed(config, node_id)
    result_path = config.get('result_path') or os.path.join(
        runtime.work_dir, 'issue41-client-result.json')
    fixture_dir = os.path.join(runtime.work_dir, 'issue41-fixture')
    events = []
    rounds_result = []

    for index in range(1, rounds + 1):
        events.append(_event('wait_for_model_start', round_index=index,
                             node_id=node_id))
        model_path = runtime.wait_for_model()
        manifest = read_json(os.path.join(os.path.dirname(model_path),
                                          'model.manifest.json'))
        round_id = manifest['round_id']
        model_sha256 = file_sha256(model_path)
        if model_sha256 != manifest['sha256']:
            raise FLRuntimeError(
                'issue41_fixture_failed', 'client 模型摘要与 manifest 不一致')
        if node_id not in manifest['participant_node_ids']:
            raise FLRuntimeError(
                'issue41_fixture_failed', 'client 不属于模型参与集合')
        events.append(_event('wait_for_model_done', round_index=index,
                             round_id=round_id, model_sha256=model_sha256))
        if delay_ms:
            time.sleep(delay_ms / 1000.0)
        update_path = os.path.join(
            fixture_dir, '%s-node-%d-update.bin' % (round_id, node_id))
        update_content = _update_bytes(
            round_id, node_id, model_sha256, seed,
            config.get('fixture_version', FIXTURE_VERSION))
        _write_bytes(update_path, update_content)
        update_sha256 = file_sha256(update_path)
        events.append(_event('submit_update_start', round_index=index,
                             round_id=round_id, node_id=node_id,
                             update_sha256=update_sha256))
        runtime.submit_update(update_path)
        events.append(_event('submit_update_done', round_index=index,
                             round_id=round_id, node_id=node_id,
                             update_sha256=update_sha256))
        rounds_result.append({
            'round_index': index,
            'round_id': round_id,
            'node_id': node_id,
            'model_sha256': model_sha256,
            'update_sha256': update_sha256,
            'training_delay_ms': delay_ms,
            'client_dataset_seed': seed,
        })

    write_json_atomic(result_path, {
        'schema_version': 1,
        'role': 'client',
        'fixture_version': config.get('fixture_version', FIXTURE_VERSION),
        'node_id': node_id,
        'rounds': rounds_result,
        'events': events,
        'conclusion': 'succeeded',
    })


def _model_bytes(round_index, participant_node_ids, config):
    payload = {
        'fixture_version': config.get('fixture_version', FIXTURE_VERSION),
        'round_index': round_index,
        'participant_node_ids': list(participant_node_ids),
        'model_seed': config.get('model_seed', 'issue41-model-seed'),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8') + b'\n'


def _update_bytes(round_id, node_id, model_sha256, client_dataset_seed,
                  fixture_version):
    payload = {
        'fixture_version': fixture_version,
        'round_id': round_id,
        'node_id': node_id,
        'model_sha256': model_sha256,
        'client_dataset_seed': client_dataset_seed,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8') + b'\n'


def _update_sha256(round_id, node_id, model_sha256, client_dataset_seed,
                   fixture_version):
    return hashlib.sha256(_update_bytes(
        round_id, node_id, model_sha256, client_dataset_seed,
        fixture_version)).hexdigest()


def _client_seed(config, node_id):
    seeds = config.get('client_dataset_seeds', {})
    if isinstance(seeds, dict):
        value = seeds.get(str(node_id), seeds.get(node_id))
        if value is not None:
            return value
    return config.get('client_dataset_seed', 'issue41-client-%d' % node_id)


def _current_round_id(work_dir, role):
    current = read_json(os.path.join(work_dir, 'current-round.json'))
    if current.get('role') != role:
        raise FLRuntimeError('issue41_fixture_failed', '当前轮次角色不一致')
    return current['round_id']


def _event(name, **fields):
    value = {'name': name, 'monotonic_time': time.monotonic()}
    value.update(fields)
    return value


def _write_bytes(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as fh:
        fh.write(content)


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
    if (not result or any(type(item) is not int or item <= 0 for item in result) or
            tuple(sorted(set(result))) != result):
        raise FLRuntimeError('invalid_configuration', '%s 无效' % name)
    return result
