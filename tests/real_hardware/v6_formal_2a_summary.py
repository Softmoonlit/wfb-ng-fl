#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
import re
import sys
from typing import Dict, Iterable, List

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from wfb_ng.tests.v6_formal_summary import (  # noqa: E402
    PAUSE_REASONS,
    READY_REJECTION_REASONS,
    SCENARIO_V6_REAL_HARDWARE_DOWNLINK_SHARED,
    SCENARIO_V6_REAL_HARDWARE_DOWNLINK_SINGLE,
    SCENARIO_V6_REAL_HARDWARE_UPLINK,
    build_summary,
    write_summary,
)


def existing_paths(paths: Iterable[str]) -> List[str]:
    return [path for path in paths if path and os.path.exists(path)]


def load_queue_summary(path: str) -> Dict[str, object]:
    with open(path, 'r') as fh:
        data = json.load(fh)
    reasons = data.get('tun_read_pause_total_by_reason', {})
    return {
        'tun_read_pause_total': int(data.get('tun_read_pause_total', 0)),
        'tun_read_resume_total': int(data.get('tun_read_resume_total', 0)),
        'tun_read_pause_total_by_reason': {
            reason: int(reasons.get(reason, 0)) for reason in PAUSE_REASONS
        },
    }


def sum_queue_summaries(paths: Iterable[str]) -> Dict[str, object]:
    totals = {
        'tun_read_pause_total': 0,
        'tun_read_resume_total': 0,
        'tun_read_pause_total_by_reason': {reason: 0 for reason in PAUSE_REASONS},
    }
    for path in existing_paths(paths):
        summary = load_queue_summary(path)
        totals['tun_read_pause_total'] += summary['tun_read_pause_total']
        totals['tun_read_resume_total'] += summary['tun_read_resume_total']
        for reason in PAUSE_REASONS:
            totals['tun_read_pause_total_by_reason'][reason] += summary['tun_read_pause_total_by_reason'][reason]
    return totals


def parse_reassembly(paths: Iterable[str]) -> Dict[str, int]:
    total = 0
    limits = []
    for path in existing_paths(paths):
        last_total = None
        last_limit = None
        with open(path, 'r', errors='replace') as fh:
            for line in fh:
                match = re.search(r'\tREASSEMBLY\t(\d+):(\d+)', line)
                if match:
                    last_total = int(match.group(1))
                    last_limit = int(match.group(2))
        if last_total is not None:
            total += last_total
            limits.append(last_limit)
    return {
        'reassembly_overflow_evict': total,
        'unfinished_block_limit': max(limits) if limits else 0,
    }


def parse_ready_filter(path: str) -> Dict[str, int]:
    counters = {reason: 0 for reason in READY_REJECTION_REASONS}
    accepted = 0
    if not path or not os.path.exists(path):
        return {'accepted': accepted, **counters}

    with open(path, 'r', errors='replace') as fh:
        for line in fh:
            match = re.search(r'\tREADY_FILTER\t(\d+):(\d+):(\d+):(\d+):(\d+)', line)
            if match:
                accepted = int(match.group(2))
                counters['invalid_source'] = int(match.group(3))
                counters['wrong_ingress_or_link_domain'] = int(match.group(4))
                counters['unknown_client'] = int(match.group(5))
                continue
            if line.startswith('ready_accept node_id='):
                accepted += 1
            reject = re.search(r'ready_reject .*reason=([a-z_]+)', line)
            if reject and reject.group(1) in counters:
                counters[reject.group(1)] += 1
    return {'accepted': accepted, **counters}


def parse_feedback_and_grants(path: str) -> Dict[str, object]:
    grant_sent_total = 0
    feedback_window_open_count = 0
    feedback_window_close_count = 0
    feedback_hits = {}
    if not path or not os.path.exists(path):
        return {
            'grant_sent_total': 0,
            'feedback_window_open_count': 0,
            'feedback_window_close_count': 0,
            'feedback_uplink_hit_total_by_node': {},
            'feedback_uplink_hit_total': 0,
        }

    with open(path, 'r', errors='replace') as fh:
        for line in fh:
            if line.startswith('grant seq='):
                grant_sent_total += 1
            if line.startswith('feedback_window_open '):
                feedback_window_open_count += 1
            if line.startswith('feedback_window_close '):
                feedback_window_close_count += 1
            match = re.match(r'feedback_uplink_hit node_id=(\d+) sequence=(\d+) total=(\d+)', line)
            if match:
                feedback_hits[match.group(1)] = int(match.group(3))
    return {
        'grant_sent_total': grant_sent_total,
        'feedback_window_open_count': feedback_window_open_count,
        'feedback_window_close_count': feedback_window_close_count,
        'feedback_uplink_hit_total_by_node': feedback_hits,
        'feedback_uplink_hit_total': sum(feedback_hits.values()),
    }


def build_downlink(args):
    scenario_id = (
        SCENARIO_V6_REAL_HARDWARE_DOWNLINK_SHARED
        if args.scenario == 'shared'
        else SCENARIO_V6_REAL_HARDWARE_DOWNLINK_SINGLE
    )
    queue = sum_queue_summaries(args.queue_summary)
    reassembly = parse_reassembly(args.reassembly_log)
    ready = parse_ready_filter(args.server_log)
    control = parse_feedback_and_grants(args.server_log)
    fields = {
        'grant_sent_total': control['grant_sent_total'],
        'ready_accepted_total': ready['accepted'],
        'ready_rejected_total_by_reason': {
            reason: ready[reason] for reason in READY_REJECTION_REASONS
        },
        **queue,
        **reassembly,
    }
    if scenario_id == SCENARIO_V6_REAL_HARDWARE_DOWNLINK_SHARED:
        fields.update({
            'feedback_window_open_count': control['feedback_window_open_count'],
            'feedback_window_close_count': control['feedback_window_close_count'],
            'feedback_uplink_hit_total_by_node': control['feedback_uplink_hit_total_by_node'],
            'feedback_uplink_hit_total': control['feedback_uplink_hit_total'],
        })
    return build_summary(scenario_id, **fields)


def build_uplink(args):
    queue = sum_queue_summaries(args.queue_summary)
    reassembly = parse_reassembly(args.reassembly_log)
    return build_summary(
        SCENARIO_V6_REAL_HARDWARE_UPLINK,
        **queue,
        **reassembly,
    )


def main():
    parser = argparse.ArgumentParser(description='生成 v6 real-hardware 统一 2A 摘要')
    parser.add_argument('--mode', choices=('uplink', 'downlink'), required=True)
    parser.add_argument('--scenario', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--server-log', default='')
    parser.add_argument('--queue-summary', action='append', default=[])
    parser.add_argument('--reassembly-log', action='append', default=[])
    args = parser.parse_args()

    if args.mode == 'downlink' and args.scenario not in ('single', 'shared'):
        raise SystemExit('downlink scenario 必须是 single 或 shared')
    if args.mode == 'uplink' and args.scenario != 'dual-long-run':
        raise SystemExit('uplink scenario 必须是 dual-long-run')

    summary = build_downlink(args) if args.mode == 'downlink' else build_uplink(args)
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    write_summary(args.output, summary)
    print(args.output)


if __name__ == '__main__':
    main()
