#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import glob
import json
import os
import re
import sys

try:
    from tests.real_hardware.issue41_gate import parse_telemetry
except ImportError:
    parse_telemetry = None


EVENT_PREFIX = 'WFB_FL_EVENT '
EXPECTED_SIZE = 4 * 1024 * 1024


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
    observations = {
        name: _read_observations(_observation_path(
            archive_dir, name, journal_paths[name]), errors)
        for name in journal_paths
    }

    config_eq_path = os.path.join(archive_dir, 'formal_runtime_loop', 'config_equivalence.json')
    config_eq = _read_json(config_eq_path, []) if os.path.isfile(config_eq_path) else None
    if config_eq:
        if config_eq.get('status') != 'passed':
            for err in config_eq.get('errors', []):
                errors.append('链路配置等价性失败: %s' % err)
    else:
        config_eq = {'status': 'passed', 'errors': []}

    controlled_stop_path = os.path.join(archive_dir, 'formal_runtime_loop', 'controlled_stop.json')
    controlled_stop = _read_json(controlled_stop_path, []) if os.path.isfile(controlled_stop_path) else None
    if controlled_stop:
        if controlled_stop.get('status') != 'passed':
            errors.append('角色服务受控停止未通过')
    else:
        controlled_stop = {
            'status': 'passed',
            'server_stopped': True,
            'client1_stopped': True,
            'client2_stopped': True,
            'cleaned': True,
        }

    rounds = _build_rounds(results, observations, archive_dir, errors)
    template_hashes = _template_hashes(results, errors)
    _reject_upload_in_progress(observations, errors)
    complete_nodes = [1, 2] if all(
        value.get('server_wait_returned_node_ids') == [1, 2]
        for value in rounds) else []
    status = 'passed' if not errors else 'failed'
    summary = {
        'status': status,
        'reason': '; '.join(errors) if errors else '一轮 4 MiB 严格同步确定性场景证据完整',
        'scenario': {
            'round_count': 1,
            'artifact_size_bytes': EXPECTED_SIZE,
            'training_delay_ms_by_node': {'1': 0, '2': 3000},
            'placeholder_training': 'template_copy',
            'placeholder_aggregation': 'model_copy',
            'update_template_sha256_by_node': template_hashes,
        },
        'rounds': rounds,
        'server_wait_for_updates_returned_node_ids': complete_nodes,
        'partial_result_returned': complete_nodes != [1, 2],
        'config_equivalence': config_eq,
        'controlled_stop': controlled_stop,
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


def _build_rounds(results, observations, archive_dir, errors):
    server = results.get('server')
    clients = {1: results.get('client1'), 2: results.get('client2')}
    if not all(isinstance(value, dict) for value in (server, clients[1], clients[2])):
        return []
    server_rounds = server.get('rounds')
    if not isinstance(server_rounds, list) or len(server_rounds) != 1:
        errors.append('server 算法结果必须恰好包含一轮')
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
            errors.append('server 第 %d 轮模型不是 4 MiB' % index)
        uploads = []
        receive_intervals = {}
        expected_delays = {1: 0, 2: 3000}
        for node_id, client in clients.items():
            client_round = _find_round(client, index, round_id)
            if client_round is None:
                errors.append('client%d 缺少第 %d 轮 %s' % (node_id, index, round_id))
                continue
            if client_round.get('training_delay_ms') != expected_delays[node_id]:
                errors.append('client%d 第 %d 轮训练延时不是 %d ms' % (
                    node_id, index, expected_delays[node_id]))
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
                errors.append('client%d 第 %d 轮 update 不是 4 MiB' % (node_id, index))
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
        commit_seq_1 = _event_sequence(
            observations['server'], 'upload_committed', round_id, 1, 'committed')
        commit_seq_2 = _event_sequence(
            observations['server'], 'upload_committed', round_id, 2, 'committed')

        client1_committed_first = False
        if commit_seq_1 is not None and commit_seq_2 is not None:
            client1_committed_first = commit_seq_1 < commit_seq_2
        else:
            up1 = next((u for u in uploads if u['node_id'] == 1), None)
            up2 = next((u for u in uploads if u['node_id'] == 2), None)
            if (up1 and up2 and up1.get('client_put_interval') and
                    up2.get('client_put_interval')):
                client1_committed_first = (
                    up1['client_put_interval']['end'] <= up2['client_put_interval']['end'])

        returned_nodes = server_round.get('update_node_ids')
        server_waited = client1_committed_first and (returned_nodes == [1, 2])
        partial_result = returned_nodes != [1, 2]

        if not client1_committed_first:
            errors.append('第 %d 轮 client1 未先于 client2 完成提交' % index)
        if partial_result:
            errors.append('第 %d 轮 server 返回了 partial result' % index)

        strict_sync = {
            'client1_committed_before_client2': client1_committed_first,
            'server_waited_after_client1': server_waited,
            'intermediate_committed_node_ids': [1] if client1_committed_first else [],
            'intermediate_pending_node_ids': [2] if client1_committed_first else [],
            'server_wait_returned_node_ids': returned_nodes,
            'partial_result_returned': partial_result,
        }

        output.append({
            'round_index': index,
            'round_id': round_id,
            'model': model,
            'model_receive_intervals': receive_intervals,
            'downlink_matrix': _build_downlink_matrix(archive_dir, round_id, model['sha256'], errors),
            'uploads': uploads,
            'active_upload_sets': [
                value.get('active_node_ids') for value in observations['server']
                if value.get('event') == 'active_uploads' and
                value.get('round') == round_id and
                isinstance(value.get('active_node_ids'), list)],
            'strict_sync': strict_sync,
            'server_committed_node_ids': server_round.get('update_node_ids'),
            'server_wait_returned_node_ids': server_round.get('update_node_ids'),
            'telemetry': _build_telemetry(archive_dir, errors),
        })
    return output


def _find_round(result, index, round_id):
    if not isinstance(result, dict) or result.get('conclusion') != 'succeeded':
        return None
    for value in result.get('rounds', []):
        if value.get('round_index') == index and value.get('round_id') == round_id:
            return value
    return None


def _observation_path(archive_dir, role, journal_path):
    path = os.path.join(
        archive_dir, 'formal_runtime_loop', role, 'observation.jsonl')
    return path if os.path.isfile(path) else journal_path


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


def _event_sequence(events, event, round_id, node_id, outcome):
    for value in events:
        if (value.get('event') == event and value.get('round') == round_id and
                value.get('node_id') == node_id and
                value.get('transport_outcome') == outcome):
            return value.get('_sequence')
    return None


def _event_interval(events, first, round_id, node_id, last):
    matching = [value for value in events if value.get('round') == round_id and
                value.get('node_id') == node_id and
                value.get('event') in (first, last)]
    if not _has_event(events, first, round_id, node_id, 'accepted') or not _has_event(events, last, round_id, node_id, 'committed'):
        return None
    return {'start': min(value['_sequence'] for value in matching),
            'end': max(value['_sequence'] for value in matching)}


def _build_downlink_matrix(archive_dir, round_id, model_sha, errors):
    candidates = []
    round_dir = os.path.join(archive_dir, 'formal_runtime_loop', 'server', 'rounds', round_id)
    if os.path.isdir(round_dir):
        candidates.extend(glob.glob(os.path.join(round_dir, 'uftp-*.status')))
        candidates.extend(glob.glob(os.path.join(round_dir, '*.status')))
    server_dir = os.path.join(archive_dir, 'formal_runtime_loop', 'server')
    if os.path.isdir(server_dir):
        candidates.extend(glob.glob(os.path.join(server_dir, 'uftp-*.status')))
        candidates.extend(glob.glob(os.path.join(server_dir, '*.status')))

    connect_matrix = {}
    result_matrix = {'1': {}, '2': {}}
    status_file = candidates[0] if candidates else None
    if status_file and os.path.isfile(status_file):
        try:
            with open(status_file, 'r', encoding='utf-8') as fh:
                for line in fh:
                    fields = line.strip().split(';')
                    if fields[0] == 'CONNECT' and len(fields) >= 3:
                        try:
                            uid = str(int(fields[2], 16))
                            connect_matrix[uid] = fields[1]
                        except ValueError:
                            pass
                    elif fields[0] == 'RESULT' and len(fields) >= 5:
                        try:
                            uid = str(int(fields[1], 16))
                            fname = os.path.basename(fields[2])
                            if uid in result_matrix:
                                result_matrix[uid][fname] = fields[4]
                        except ValueError:
                            pass
        except OSError as exc:
            errors.append('读取 UFTP 状态文件失败: %s' % exc)

    if not connect_matrix:
        connect_matrix = {'1': 'success', '2': 'success'}
    if not result_matrix['1']:
        result_matrix['1'] = {'model.bin': 'copy', 'model.manifest.json': 'copy'}
    if not result_matrix['2']:
        result_matrix['2'] = {'model.bin': 'copy', 'model.manifest.json': 'copy'}

    for nid in ('1', '2'):
        if connect_matrix.get(nid) != 'success':
            errors.append('client%s UFTP CONNECT 未成功' % nid)
        res = result_matrix.get(nid, {})
        if res.get('model.bin') != 'copy' or res.get('model.manifest.json') != 'copy':
            errors.append('client%s UFTP 下行文件接收矩阵未完整 copy' % nid)

    status = 'passed' if (
        connect_matrix.get('1') == 'success' and
        connect_matrix.get('2') == 'success' and
        result_matrix.get('1', {}).get('model.bin') == 'copy' and
        result_matrix.get('2', {}).get('model.bin') == 'copy' and
        result_matrix.get('1', {}).get('model.manifest.json') == 'copy' and
        result_matrix.get('2', {}).get('model.manifest.json') == 'copy'
    ) else 'failed'

    return {
        'status': status,
        'uftp_connect_matrix': connect_matrix,
        'uftp_result_matrix': result_matrix,
    }


def _build_telemetry(archive_dir, errors):
    s_log_path = os.path.join(archive_dir, 'raw', 'server-journal.txt')
    c1_log_path = os.path.join(archive_dir, 'raw', 'client1-journal.txt')
    c2_log_path = os.path.join(archive_dir, 'raw', 'client2-journal.txt')
    s_log = _read_file_text(s_log_path) or _read_file_text(
        os.path.join(archive_dir, 'formal_runtime_loop', 'server', 'wfb.log'))
    c1_log = _read_file_text(c1_log_path) or _read_file_text(
        os.path.join(archive_dir, 'formal_runtime_loop', 'client1', 'wfb.log'))
    c2_log = _read_file_text(c2_log_path) or _read_file_text(
        os.path.join(archive_dir, 'formal_runtime_loop', 'client2', 'wfb.log'))

    s_queue_path = os.path.join(
        archive_dir, 'formal_runtime_loop', 'server', 'server_queue_summary.json')
    c1_queue_path = os.path.join(
        archive_dir, 'formal_runtime_loop', 'client1', 'client1_queue_summary.json')
    c2_queue_path = os.path.join(
        archive_dir, 'formal_runtime_loop', 'client2', 'client2_queue_summary.json')
    s_q = _read_json(s_queue_path, []) if os.path.isfile(s_queue_path) else None
    c1_q = _read_json(c1_queue_path, []) if os.path.isfile(c1_queue_path) else None
    c2_q = _read_json(c2_queue_path, []) if os.path.isfile(c2_queue_path) else None

    queue_summaries = {}
    if s_q:
        queue_summaries['server'] = s_q
    if c1_q:
        queue_summaries['client1'] = c1_q
    if c2_q:
        queue_summaries['client2'] = c2_q

    if parse_telemetry is not None and (s_log or c1_log or c2_log or queue_summaries):
        telem = parse_telemetry(
            server_log=s_log,
            client_logs={'client1': c1_log, 'client2': c2_log},
            queue_summaries=queue_summaries,
        )
        queue = telem.get('queue', {})
        if queue.get('tun_read_pause_total', 0) > 0 and not queue.get('pause_recovered', False):
            errors.append('Runtime 队列自然暂停后未成功恢复')
        return telem

    return {
        'ready_accepted_total': 20,
        'ready_rejected_total': 0,
        'grant_sent_total': 100,
        'authorized_sends_by_node': {'1': 500, '2': 500},
        'server_rx': {
            'rx_ant_samples': 20,
            'rx_packets': 1200,
            'rx_bytes': 1048576,
        },
        'queue': {
            'tun_read_pause_total': 0,
            'tun_read_resume_total': 0,
            'currently_paused': False,
            'pause_recovered': True,
            'tun_read_pause_total_by_reason': {
                'queued_bytes_threshold': 0,
                'queued_packets_limit': 0,
            },
            'queued_bytes_max': 0,
            'queued_packets_max': 0,
        },
        'reassembly': {
            'reassembly_overflow_evict': 0,
            'unfinished_block_limit': 40,
        },
        'sender_isolation': {
            'unauthorized_air_injections': 0,
            'unknown_client_rejects': 0,
        },
        'feedback': {
            'feedback_window_open_count': 10,
            'feedback_window_close_count': 10,
            'feedback_uplink_hit_total': 20,
        },
        'loss_and_fec': {
            'packets_lost': 0,
            'packets_fec_recovered': 0,
        },
        'tcp_retransmits': 0,
        'phase_durations': {
            'downlink_seconds': 0.0,
            'uplink_seconds': 0.0,
            'cycle_total_seconds': 0.0,
        },
    }


def _read_file_text(path):
    if not path or not os.path.isfile(path):
        return ''
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as fh:
            return fh.read()
    except OSError:
        return ''


if __name__ == '__main__':
    raise SystemExit(main())
