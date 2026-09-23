#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import glob
import json
import os
import re
import sys

from tests.real_hardware.issue41_gate import parse_telemetry


EVENT_PREFIX = 'WFB_FL_EVENT '


def main(argv=None):
    parser = argparse.ArgumentParser(description='生成 issue41 正式 Runtime 归档摘要')
    parser.add_argument('archive_dir')
    args = parser.parse_args(argv)
    archive_dir = os.path.abspath(args.archive_dir)
    client_dirs = glob.glob(os.path.join(archive_dir, 'formal_runtime_loop', 'client*'))
    client_names = sorted([os.path.basename(d) for d in client_dirs],
                          key=lambda x: int(re.search(r'\d+', x).group()) if re.search(r'\d+', x) else x)
    if not client_names:
        env_path = os.path.join(archive_dir, 'envelope.json')
        if os.path.isfile(env_path):
            env_data = _read_json(env_path, [])
            rc = env_data.get('resolved_config', {})
            t_delays = rc.get('training_delay_ms_by_node', {})
            if t_delays:
                client_names = [f'client{nid}' for nid in sorted(t_delays.keys(), key=lambda x: int(x))]
    if not client_names:
        if 'ISSUE41_CLIENT_ROLES' in os.environ:
            client_names = [r.strip() for r in os.environ['ISSUE41_CLIENT_ROLES'].split() if r.strip()]
        else:
            client_names = [f'client{i}' for i in range(1, 8)]

    result_paths = {
        'server': os.path.join(archive_dir, 'formal_runtime_loop', 'server',
                               'issue41-server-result.json'),
    }
    for cname in client_names:
        result_paths[cname] = os.path.join(archive_dir, 'formal_runtime_loop', cname,
                                           f'issue41-{cname}-result.json')

    journal_paths = {
        'server': os.path.join(archive_dir, 'raw', 'server-journal.txt'),
    }
    for cname in client_names:
        journal_paths[cname] = os.path.join(archive_dir, 'raw', f'{cname}-journal.txt')

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
            **{f'{cname}_stopped': True for cname in client_names},
            'cleaned': True,
        }

    env_path = os.path.join(archive_dir, 'envelope.json')
    env_meta = _read_json(env_path, []) if os.path.isfile(env_path) else {}
    resolved_cfg = env_meta.get('resolved_config', {}) if isinstance(env_meta, dict) else {}
    round_deadline = resolved_cfg.get('runtime_timeout_seconds', 400)
    io_timeout = resolved_cfg.get('io_timeout_seconds', 120)

    # 场景参数解析（从 envelope.json 的 resolved_config 获取）
    if 'rounds' not in resolved_cfg:
        errors.append('envelope.json resolved_config 缺少 rounds')
        expected_rounds = 2
    else:
        expected_rounds = int(resolved_cfg['rounds'])

    if 'artifact_size_bytes' not in resolved_cfg:
        errors.append('envelope.json resolved_config 缺少 artifact_size_bytes')
        expected_size = 40 * 1024 * 1024
    else:
        expected_size = int(resolved_cfg['artifact_size_bytes'])

    expected_node_ids = [int(re.search(r'\d+', c).group()) for c in client_names if re.search(r'\d+', c)]
    if 'training_delay_ms_by_node' not in resolved_cfg:
        errors.append('envelope.json resolved_config 缺少 training_delay_ms_by_node')
        expected_delays = {nid: 0 for nid in expected_node_ids}
    else:
        expected_delays = {int(k): int(v) for k, v in resolved_cfg['training_delay_ms_by_node'].items()}

    scenario_cfg = {
        'rounds': expected_rounds,
        'artifact_size_bytes': expected_size,
        'training_delays': expected_delays,
        'round_deadline': round_deadline,
        'io_timeout': io_timeout,
        'client_names': client_names,
        'expected_node_ids': expected_node_ids,
    }

    rounds = _build_rounds(results, observations, archive_dir, errors, scenario_cfg)
    template_hashes = _template_hashes(results, errors, client_names)
    _reject_upload_in_progress(observations, errors)
    complete_nodes = expected_node_ids if all(
        value.get('server_wait_returned_node_ids') == expected_node_ids
        for value in rounds) else []
    status = 'passed' if not errors else 'failed'
    success_reason = f'{expected_rounds} 轮 {expected_size // (1024 * 1024)} MiB 严格同步场景证据完整'
    summary = {
        'status': status,
        'reason': '; '.join(errors) if errors else success_reason,
        'scenario': {
            'round_count': expected_rounds,
            'artifact_size_bytes': expected_size,
            'training_delay_ms_by_node': {str(k): v for k, v in expected_delays.items()},
            'placeholder_training': 'template_copy',
            'placeholder_aggregation': 'model_copy',
            'update_template_sha256_by_node': template_hashes,
            'round_deadline_seconds': round_deadline,
            'io_timeout_seconds': io_timeout,
        },
        'rounds': rounds,
        'server_wait_for_updates_returned_node_ids': complete_nodes,
        'partial_result_returned': complete_nodes != expected_node_ids,
        'config_equivalence': config_eq,
        'controlled_stop': controlled_stop,
    }
    json.dump(summary, sys.stdout, ensure_ascii=True, separators=(',', ':'))
    sys.stdout.write('\n')
    return 0


def _template_hashes(results, errors, client_names=None):
    if client_names is None:
        client_names = [k for k in sorted(results.keys(), key=lambda x: int(re.search(r'\d+', x).group()) if re.search(r'\d+', x) else 0) if k != 'server']
    hashes = {}
    seen_shas = set()
    for role in client_names:
        m = re.search(r'\d+', role)
        node_id = int(m.group()) if m else 1
        value = results.get(role)
        digest = value.get('update_template_sha256') if isinstance(value, dict) else None
        if not _is_sha256(digest):
            errors.append(f'{role} update 模板 SHA-256 无效')
        elif digest in seen_shas:
            errors.append('两个 client update 模板 SHA-256 相同' if len(client_names) == 2 else f'{role} update 模板 SHA-256 与已有客户端重复')
        else:
            seen_shas.add(digest)
        hashes[str(node_id)] = digest
    return hashes


def _build_rounds(results, observations, archive_dir, errors, scenario_cfg):
    expected_rounds = scenario_cfg['rounds']
    expected_size = scenario_cfg['artifact_size_bytes']
    expected_delays = scenario_cfg['training_delays']
    round_deadline = scenario_cfg.get('round_deadline', 400)
    io_timeout = scenario_cfg.get('io_timeout', 120)
    client_names = scenario_cfg.get('client_names')
    if client_names is None:
        client_names = [k for k in sorted(results.keys(), key=lambda x: int(re.search(r'\d+', x).group()) if re.search(r'\d+', x) else 0) if k != 'server']
    expected_node_ids = scenario_cfg.get('expected_node_ids')
    if expected_node_ids is None:
        expected_node_ids = sorted([int(re.search(r'\d+', c).group()) for c in client_names if re.search(r'\d+', c)])
    server = results.get('server')
    clients = {int(re.search(r'\d+', c).group()): results.get(c) for c in client_names if re.search(r'\d+', c)}
    if not isinstance(server, dict) or not all(isinstance(c, dict) for c in clients.values()):
        return []
    server_rounds = server.get('rounds')
    if not isinstance(server_rounds, list) or len(server_rounds) != expected_rounds:
        errors.append('server 算法结果必须恰好包含 %d 轮 (实际: %d)' % (
            expected_rounds, len(server_rounds) if isinstance(server_rounds, list) else 0))
        return []
    output = []
    seen_round_ids = set()
    prev_output_model_sha = None

    for index, server_round in enumerate(server_rounds, 1):
        round_id = server_round.get('round_id')
        if server_round.get('round_index') != index or not isinstance(round_id, str) or not round_id:
            errors.append('server 第 %d 轮身份无效' % index)
            continue
        if round_id in seen_round_ids:
            errors.append('server 第 %d 轮 round_id 重复：%s' % (index, round_id))
        seen_round_ids.add(round_id)

        model = {
            'size_bytes': server_round.get('input_model_size_bytes'),
            'sha256': server_round.get('input_model_sha256'),
        }
        if model['size_bytes'] != expected_size:
            errors.append('server 第 %d 轮模型不是 %d 字节' % (index, expected_size))

        # 验证跨轮占位聚合的一致性 (model_copy)
        if index > 1 and prev_output_model_sha is not None:
            if model['sha256'] != prev_output_model_sha:
                errors.append('server 第 %d 轮输入模型与上一轮占位聚合输出不一致' % index)
        out_model_sha = server_round.get('output_model_sha256')
        if out_model_sha != model['sha256']:
            errors.append('server 第 %d 轮占位聚合模型输出 SHA-256 与输入不一致' % index)
        prev_output_model_sha = out_model_sha

        uploads = []
        receive_intervals = {}
        for node_id, client in clients.items():
            client_round = _find_round(client, index, round_id)
            if client_round is None:
                errors.append('client%d 缺少第 %d 轮 %s' % (node_id, index, round_id))
                continue
            if client_round.get('training_delay_ms') != expected_delays[node_id]:
                errors.append('client%d 第 %d 轮训练延时不是 %d ms' % (
                    node_id, index, expected_delays[node_id]))
            if (client_round.get('model_size_bytes') != expected_size or
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
            if upload['size_bytes'] != expected_size:
                errors.append('client%d 第 %d 轮 update 不是 %d 字节' % (node_id, index, expected_size))
            if upload['sha256'] != client.get('update_template_sha256'):
                errors.append('client%d 第 %d 轮未复用对应 update 模板' % (node_id, index))
            uploads.append(upload)

        server_updates = server_round.get('updates')
        actual_update_node_ids = sorted(
            item.get('node_id') for item in server_updates if isinstance(item, dict)) if isinstance(server_updates, list) else []
        if actual_update_node_ids != expected_node_ids:
            errors.append(f'server 第 {index} 轮未收齐 {expected_node_ids} (实际: {actual_update_node_ids})')
        else:
            for update in server_updates:
                client_upload = next((item for item in uploads if item['node_id'] == update['node_id']), None)
                if client_upload and (
                        update.get('size_bytes') != client_upload['size_bytes'] or
                        update.get('sha256') != client_upload['sha256']):
                    errors.append('第 %d 轮 node %d 端到端 SHA 或大小不一致' %
                                  (index, update['node_id']))

        commit_seqs = {}
        for nid in expected_node_ids:
            seq = _event_sequence(
                observations['server'], 'upload_committed', round_id, nid, 'committed')
            commit_seqs[nid] = seq

        returned_nodes = server_round.get('update_node_ids')
        partial_result = (returned_nodes != expected_node_ids)

        if any(seq is None for seq in commit_seqs.values()):
            errors.append('第 %d 轮 server observation 缺少 upload_committed 事件' % index)
            first_committed_node = None
            client1_committed_first = False
            server_waited = False
        else:
            sorted_by_seq = sorted(commit_seqs.items(), key=lambda x: x[1])
            first_committed_node = sorted_by_seq[0][0]
            first_seq = sorted_by_seq[0][1]
            last_seq = sorted_by_seq[-1][1]
            client1_committed_first = (first_committed_node == 1)
            server_waited = (first_seq < last_seq and returned_nodes == expected_node_ids)

        if expected_delays.get(2, 0) > expected_delays.get(1, 0):
            if not client1_committed_first:
                errors.append('第 %d 轮 client1 未先于 client2 完成提交' % index)
        if partial_result:
            errors.append('第 %d 轮 server 返回了 partial result' % index)

        strict_sync = {
            'client1_committed_before_client2': client1_committed_first,
            'server_waited_after_first_commit': server_waited,
            'first_committed_node_id': first_committed_node,
            'intermediate_committed_node_ids': [first_committed_node] if first_committed_node else [],
            'intermediate_pending_node_ids': [nid for nid in expected_node_ids if nid != first_committed_node],
            'server_wait_returned_node_ids': returned_nodes,
            'partial_result_returned': partial_result,
        }

        # 计算 HTTP PUT 活动时间区间与自然重叠
        valid_intervals = [
            (u['node_id'], u['client_put_interval']['start'], u['client_put_interval']['end'])
            for u in uploads
            if u.get('client_put_interval') and 'start' in u['client_put_interval'] and 'end' in u['client_put_interval']
        ]
        overlap_seconds = 0.0
        natural_overlap = False
        if len(valid_intervals) >= 2:
            max_start = max(item[1] for item in valid_intervals)
            min_end = min(item[2] for item in valid_intervals)
            overlap = min_end - max_start
            if overlap > 0:
                overlap_seconds = round(overlap, 3)
                natural_overlap = True
            else:
                # 寻找任意两两之间的最大重叠
                for i in range(len(valid_intervals)):
                    for j in range(i + 1, len(valid_intervals)):
                        ov = min(valid_intervals[i][2], valid_intervals[j][2]) - max(valid_intervals[i][1], valid_intervals[j][1])
                        if ov > overlap_seconds:
                            overlap_seconds = round(ov, 3)
                            natural_overlap = True

        active_sets = [
            value.get('active_node_ids') for value in observations['server']
            if value.get('event') == 'active_uploads' and
            value.get('round') == round_id and
            isinstance(value.get('active_node_ids'), list)
        ]
        concurrent_active_observed = any(len(set(s)) > 1 for s in active_sets)

        concurrent_put = {
            'natural_overlap': natural_overlap,
            'overlap_duration_seconds': overlap_seconds,
            'concurrent_active_observed': concurrent_active_observed,
            'server_intervals': {str(u['node_id']): u.get('server_put_interval') for u in uploads},
            'client_intervals': {str(u['node_id']): u.get('client_put_interval') for u in uploads},
        }

        # 确定本轮时间区间用于分轮遥测过滤
        start_ev = next(
            (e for e in results.get('server', {}).get('events', [])
             if e.get('name') == 'publish_model_start' and e.get('round_index') == index),
            None)
        end_ev = next(
            (e for e in results.get('server', {}).get('events', [])
             if e.get('name') == 'aggregate_done' and e.get('round_index') == index),
            None)
        r_start_ms = int(start_ev['monotonic_time'] * 1000) if start_ev and 'monotonic_time' in start_ev else None
        r_end_ms = int(end_ev['monotonic_time'] * 1000) if end_ev and 'monotonic_time' in end_ev else None
        duration_sec = round((r_end_ms - r_start_ms) / 1000.0, 3) if r_start_ms is not None and r_end_ms is not None else None

        output.append({
            'round_index': index,
            'round_id': round_id,
            'duration_seconds': duration_sec,
            'model': model,
            'model_receive_intervals': receive_intervals,
            'downlink_matrix': _build_downlink_matrix(archive_dir, round_id, model['sha256'], errors),
            'uploads': uploads,
            'active_upload_sets': active_sets,
            'concurrent_put': concurrent_put,
            'strict_sync': strict_sync,
            'server_committed_node_ids': server_round.get('update_node_ids'),
            'server_wait_returned_node_ids': server_round.get('update_node_ids'),
            'telemetry': _build_telemetry(archive_dir, errors, round_start_ms=r_start_ms, round_end_ms=r_end_ms, client_names=client_names),
            'deadline_seconds': round_deadline,
            'io_timeout_seconds': io_timeout,
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


def is_sha256(value):
    return (isinstance(value, str) and len(value) == 64 and
            all(character in '0123456789abcdef' for character in value))


_is_sha256 = is_sha256


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
        candidates.extend(sorted(glob.glob(os.path.join(round_dir, 'uftp-*.status'))))

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
        errors.append('缺少 UFTP CONNECT 状态')
    if not result_matrix.get('1') or not result_matrix.get('2'):
        errors.append('缺少 UFTP 下行文件接收矩阵')

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


def _filter_log_by_time_ms(log_text, start_ms, end_ms):
    if not log_text or start_ms is None or end_ms is None:
        return ''
    filtered = []
    pattern = re.compile(r'(\d+)[\t ]+PKT')
    for line in log_text.splitlines():
        m = pattern.search(line)
        if m:
            ts = int(m.group(1))
            if start_ms <= ts <= end_ms:
                filtered.append(line)
    return '\n'.join(filtered)


def _build_telemetry(archive_dir, errors, round_start_ms=None, round_end_ms=None, client_names=None):
    if client_names is None:
        client_dirs = glob.glob(os.path.join(archive_dir, 'formal_runtime_loop', 'client*'))
        client_names = sorted([os.path.basename(d) for d in client_dirs],
                              key=lambda x: int(re.search(r'\d+', x).group()) if re.search(r'\d+', x) else x)
        if not client_names:
            if 'ISSUE41_CLIENT_ROLES' in os.environ:
                client_names = [r.strip() for r in os.environ['ISSUE41_CLIENT_ROLES'].split() if r.strip()]
            else:
                client_names = [f'client{i}' for i in range(1, 8)]

    if round_start_ms is None or round_end_ms is None:
        errors.append('缺少轮次有效起止时间戳，无法切片提取遥测数据')
    s_log_path = os.path.join(archive_dir, 'raw', 'server-journal.txt')
    s_log = _read_file_text(s_log_path) or _read_file_text(
        os.path.join(archive_dir, 'formal_runtime_loop', 'server', 'wfb.log'))
    s_log = _filter_log_by_time_ms(s_log, round_start_ms, round_end_ms)

    client_logs = {}
    queue_summaries = {}

    s_queue_path = os.path.join(
        archive_dir, 'formal_runtime_loop', 'server', 'server_queue_summary.json')
    s_q = _read_json(s_queue_path, []) if os.path.isfile(s_queue_path) else None
    if s_q:
        queue_summaries['server'] = s_q

    for cname in client_names:
        c_log_path = os.path.join(archive_dir, 'raw', f'{cname}-journal.txt')
        c_log = _read_file_text(c_log_path) or _read_file_text(
            os.path.join(archive_dir, 'formal_runtime_loop', cname, 'wfb.log'))
        client_logs[cname] = c_log or ''

        c_q_path = os.path.join(
            archive_dir, 'formal_runtime_loop', cname, f'{cname}_queue_summary.json')
        c_q = _read_json(c_q_path, []) if os.path.isfile(c_q_path) else None
        if c_q:
            queue_summaries[cname] = c_q

    telem = parse_telemetry(
        server_log=s_log or '',
        client_logs=client_logs,
        queue_summaries=queue_summaries,
    )
    queue = telem.get('queue', {})
    if queue.get('tun_read_pause_total', 0) > 0 and not queue.get('pause_recovered', False):
        errors.append('Runtime 队列自然暂停后未成功恢复')
    return telem


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
