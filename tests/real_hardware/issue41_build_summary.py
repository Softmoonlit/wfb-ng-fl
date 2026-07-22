#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
import re
import sys


EVENT_PREFIX = 'WFB_FL_EVENT '
EXPECTED_SIZE = 40 * 1024 * 1024


def main(argv=None):
    parser = argparse.ArgumentParser(description='生成 issue41 正式 Runtime 归档摘要')
    parser.add_argument('archive_dir')
    args = parser.parse_args(argv)
    archive_dir = os.path.abspath(args.archive_dir)
    result_paths = {
        'server': os.path.join(archive_dir, 'formal_runtime_loop', 'server',
                               'issue41-server-result.json'),
        'client1': os.path.join(archive_dir, 'formal_runtime_loop', 'client1',
                                'issue41-client1-result.json'),
        'client2': os.path.join(archive_dir, 'formal_runtime_loop', 'client2',
                                'issue41-client2-result.json'),
    }
    journal_paths = {
        'server': os.path.join(archive_dir, 'raw', 'server-journal.txt'),
        'client1': os.path.join(archive_dir, 'raw', 'client1-journal.txt'),
        'client2': os.path.join(archive_dir, 'raw', 'client2-journal.txt'),
    }
    errors = []
    results = {name: _read_json(path, errors) for name, path in result_paths.items()}
    observations = {name: _read_observations(path, errors) for name, path in journal_paths.items()}
    rounds = _build_rounds(results, observations, errors)
    template_hashes = _template_hashes(results, errors)
    _reject_upload_in_progress(observations, errors)
    complete_nodes = [1, 2] if all(
        value.get('server_wait_returned_node_ids') == [1, 2]
        for value in rounds) else []
    status = 'passed' if not errors else 'failed'
    summary = {
        'status': status,
        'reason': '; '.join(errors) if errors else '两轮 40 MiB 正式 Runtime 闭环证据完整',
        'scenario': {
            'round_count': 2,
            'artifact_size_bytes': EXPECTED_SIZE,
            'training_delay_ms_by_node': {'1': 0, '2': 0},
            'placeholder_training': 'template_copy',
            'placeholder_aggregation': 'model_copy',
            'update_template_sha256_by_node': template_hashes,
        },
        'rounds': rounds,
        'server_wait_for_updates_returned_node_ids': complete_nodes,
        'partial_result_returned': complete_nodes != [1, 2],
    }
    json.dump(summary, sys.stdout, ensure_ascii=True, separators=(',', ':'))
    sys.stdout.write('\n')
    return 0


def _template_hashes(results, errors):
    hashes = {}
    for node_id, role in ((1, 'client1'), (2, 'client2')):
        value = results.get(role)
        digest = value.get('update_template_sha256') if isinstance(value, dict) else None
        if not _is_sha256(digest):
            errors.append('client%d update 模板 SHA-256 无效' % node_id)
        hashes[str(node_id)] = digest
    if hashes.get('1') == hashes.get('2'):
        errors.append('两个 client update 模板 SHA-256 相同')
    return hashes


def _build_rounds(results, observations, errors):
    server = results.get('server')
    clients = {1: results.get('client1'), 2: results.get('client2')}
    if not all(isinstance(value, dict) for value in (server, clients[1], clients[2])):
        return []
    server_rounds = server.get('rounds')
    if not isinstance(server_rounds, list) or len(server_rounds) != 2:
        errors.append('server 算法结果必须包含两轮')
        return []
    output = []
    for index, server_round in enumerate(server_rounds, 1):
        round_id = server_round.get('round_id')
        if server_round.get('round_index') != index or not isinstance(round_id, str):
            errors.append('server 第 %d 轮身份无效' % index)
            continue
        model = {
            'size_bytes': server_round.get('input_model_size_bytes'),
            'sha256': server_round.get('input_model_sha256'),
        }
        if model['size_bytes'] != EXPECTED_SIZE:
            errors.append('server 第 %d 轮模型不是 40 MiB' % index)
        uploads = []
        receive_intervals = {}
        for node_id, client in clients.items():
            client_round = _find_round(client, index, round_id)
            if client_round is None:
                errors.append('client%d 缺少第 %d 轮 %s' % (node_id, index, round_id))
                continue
            if client_round.get('training_delay_ms') != 0:
                errors.append('client%d 第 %d 轮训练延时不是零' % (node_id, index))
            if (client_round.get('model_size_bytes') != EXPECTED_SIZE or
                    client_round.get('model_sha256') != model['sha256']):
                errors.append('client%d 第 %d 轮模型事实不一致' % (node_id, index))
            receive_intervals[str(node_id)] = client_round.get('model_receive_interval')
            upload = {
                'node_id': node_id,
                'size_bytes': client_round.get('update_size_bytes'),
                'sha256': client_round.get('update_sha256'),
                'client_put_interval': client_round.get('put_interval'),
                'client_http_status': 201 if _has_event(
                    observations['client%d' % node_id], 'upload_phase', round_id,
                    node_id, 'created') else None,
                'server_put_interval': _event_interval(
                    observations['server'], 'upload_accepted', round_id, node_id,
                    'upload_committed'),
                'server_http_status': 201 if _has_event(
                    observations['server'], 'upload_committed', round_id,
                    node_id, 'committed') else None,
                'server_outcome': 'committed' if _has_event(
                    observations['server'], 'upload_committed', round_id,
                    node_id, 'committed') else None,
            }
            if upload['size_bytes'] != EXPECTED_SIZE:
                errors.append('client%d 第 %d 轮 update 不是 40 MiB' % (node_id, index))
            if upload['sha256'] != client.get('update_template_sha256'):
                errors.append('client%d 第 %d 轮未复用同一 update 模板' % (node_id, index))
            uploads.append(upload)
        server_updates = server_round.get('updates')
        if not isinstance(server_updates, list) or sorted(
                item.get('node_id') for item in server_updates if isinstance(item, dict)) != [1, 2]:
            errors.append('server 第 %d 轮未收齐 [1, 2]' % index)
        else:
            for update in server_updates:
                client_upload = next(item for item in uploads if item['node_id'] == update['node_id'])
                if (update.get('size_bytes') != client_upload['size_bytes'] or
                        update.get('sha256') != client_upload['sha256']):
                    errors.append('第 %d 轮 node %d 端到端 SHA 或大小不一致' %
                                  (index, update['node_id']))
        output.append({
            'round_index': index,
            'round_id': round_id,
            'model': model,
            'model_receive_intervals': receive_intervals,
            'uploads': uploads,
            'active_upload_sets': [
                value.get('active_node_ids') for value in observations['server']
                if value.get('event') == 'active_uploads' and
                value.get('round') == round_id and
                isinstance(value.get('active_node_ids'), list)],
            'server_committed_node_ids': server_round.get('update_node_ids'),
            'server_wait_returned_node_ids': server_round.get('update_node_ids'),
        })
    return output


def _find_round(result, index, round_id):
    if not isinstance(result, dict) or result.get('conclusion') != 'succeeded':
        return None
    for value in result.get('rounds', []):
        if value.get('round_index') == index and value.get('round_id') == round_id:
            return value
    return None


def _read_json(path, errors):
    try:
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        errors.append('无法读取 %s：%s' % (path, exc))
        return None


def _read_observations(path, errors):
    try:
        with open(path, encoding='utf-8') as fh:
            lines = list(fh)
    except OSError as exc:
        errors.append('无法读取 %s：%s' % (path, exc))
        return []
    events = []
    for sequence, line in enumerate(lines):
        match = re.search(re.escape(EVENT_PREFIX) + r'(\{.*\})', line)
        if match is None:
            continue
        try:
            value = json.loads(match.group(1))
        except ValueError:
            continue
        value['_sequence'] = sequence
        events.append(value)
    return events


def _reject_upload_in_progress(observations, errors):
    for role, events in observations.items():
        for event in events:
            if event.get('error_code') == 'upload_in_progress':
                errors.append('%s journal 出现 upload_in_progress' % role)
                return


def _is_sha256(value):
    return (isinstance(value, str) and len(value) == 64 and
            all(character in '0123456789abcdef' for character in value))


def _has_event(events, event, round_id, node_id, outcome):
    return any(value.get('event') == event and value.get('round') == round_id and
               value.get('node_id') == node_id and
               value.get('transport_outcome') == outcome for value in events)


def _event_interval(events, first, round_id, node_id, last):
    matching = [value for value in events if value.get('round') == round_id and
                value.get('node_id') == node_id and
                value.get('event') in (first, last)]
    if not _has_event(events, first, round_id, node_id, 'accepted') or not _has_event(events, last, round_id, node_id, 'committed'):
        return None
    return {'start': min(value['_sequence'] for value in matching),
            'end': max(value['_sequence'] for value in matching)}


if __name__ == '__main__':
    raise SystemExit(main())
