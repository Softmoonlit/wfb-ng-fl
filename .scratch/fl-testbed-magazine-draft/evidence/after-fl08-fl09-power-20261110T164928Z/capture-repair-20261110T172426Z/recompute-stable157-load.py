#!/usr/bin/env python3
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / 'model-20261110T180211Z'
CAPTURE = MODEL / 'capture'
MARKERS = [json.loads(line) for line in (MODEL / 'phase-markers.jsonl').read_text().splitlines()]
MODEL_START = next(x['wall_ns'] for x in MARKERS if x['phase'] == 'model_start')
MODEL_END = next(x['wall_ns'] for x in MARKERS if x['phase'] == 'model_end')
HOSTS = ['fl02', 'fl08']

def percentile(values, q):
    values = sorted(values)
    if not values:
        return None
    return values[max(0, min(len(values) - 1, math.ceil(q * len(values)) - 1))]

def load(host):
    return json.loads((CAPTURE / (host + '.raw.json')).read_text())

def request_epoch_map(events):
    groups = defaultdict(list)
    for index, event in enumerate(events):
        if event.get('kind') != 'heartbeat' or event.get('duplicate_packet'):
            continue
        ident = event.get('identity', {})
        if ident.get('node_id') != int(event.get('body', {}).get('node_id', -1)):
            continue
        key = (ident.get('node_id'), ident.get('boot_id'), ident.get('session'))
        groups[key].append((index, event))
    epochs = {}
    for key, rows in groups.items():
        rows.sort(key=lambda pair: pair[1].get('mono_ns', 0))
        epoch = -1
        previous_channel = object()
        for index, event in rows:
            channel = event.get('identity', {}).get('channel')
            if channel != previous_channel:
                epoch += 1
                previous_channel = channel
            epochs[index] = (key, channel, epoch)
    return epochs

def summarize(host):
    raw = load(host)
    events = raw['events']
    samples = raw['analysis'].get('samples', [])
    epochs = request_epoch_map(events)
    event_by_index = {event['event_index']: event for event in events}
    stable = []
    all149 = []
    for sample in samples:
        request = event_by_index.get(sample['request_event_index'], {})
        ack = event_by_index.get(sample['ack_event_index'], {})
        request_wall = request.get('wall_ns')
        ack_wall = ack.get('wall_ns')
        channel = sample.get('channel')
        state = (request.get('body') or {}).get('state')
        base = dict(sample=sample, request=request, ack=ack, epoch=epochs.get(sample['request_event_index']))
        if channel == 157 and state == 'IDLE' and request_wall is not None and ack_wall is not None and MODEL_START <= request_wall <= MODEL_END and MODEL_START <= ack_wall <= MODEL_END:
            stable.append(base)
        if channel == 149:
            all149.append(base)
    def rtts(rows):
        return [row['sample']['ack_rtt_ms'] for row in rows]
    stable_rtts = rtts(stable)
    thresholds = {str(ms): sum(value > ms for value in stable_rtts) for ms in (230, 350, 500, 1000)}
    def gaps(rows, value_key):
        groups = defaultdict(list)
        for row in rows:
            epoch = row['epoch']
            if epoch is None:
                continue
            groups[epoch].append(row['sample'][value_key])
        by_epoch = {}
        for epoch, vals in groups.items():
            vals = sorted(vals)
            by_epoch[str(epoch)] = max((right - left) / 1_000_000.0 for left, right in zip(vals, vals[1:])) if len(vals) > 1 else None
        finite = [value for value in by_epoch.values() if value is not None]
        return {'by_epoch': by_epoch, 'max_within_epoch_ms': max(finite) if finite else None}
    stable_timely = [row for row in stable if row['sample'].get('within_230ms_candidate')]
    summary = {
        'host': host,
        'model_wall_bounds_ns': {'start': MODEL_START, 'end': MODEL_END},
        'phase_filter': 'paired samples whose request and ACK wall_ns fall inside model_start..model_end, request body.state=IDLE, channel=157',
        'phase_wall_alignment_note': 'remote host wall and local orchestrator wall are used only as broad alignment metadata; no cross-host monotonic subtraction',
        'stable157_idle_paired_count': len(stable),
        'stable157_idle_timely_count': len(stable_timely),
        'stable157_idle_late_count': len(stable) - len(stable_timely),
        'stable157_idle_rtt_ms': {
            'p50': percentile(stable_rtts, .50),
            'p95': percentile(stable_rtts, .95),
            'p99': percentile(stable_rtts, .99),
            'max': max(stable_rtts) if stable_rtts else None,
        },
        'stable157_idle_over_deadline_counts': thresholds,
        'stable157_idle_candidate_ratio_paired': (len(stable_timely) / len(stable)) if stable else None,
        'stable157_idle_max_timely_send_gap_ms_within_epoch': gaps(stable_timely, 'request_mono_ns'),
        'stable157_idle_max_wire_ack_gap_ms_within_epoch': gaps(stable, 'ack_mono_ns'),
        'channel149_all_window_paired_count': len(all149),
        'channel149_all_window_max_timely_send_gap_ms_within_epoch': gaps([row for row in all149 if row['sample'].get('within_230ms_candidate')], 'request_mono_ns'),
        'channel149_all_window_max_wire_ack_gap_ms_within_epoch': gaps(all149, 'ack_mono_ns'),
        'channel149_gap_use': 'descriptive only; excludes channel-switch gaps from within-epoch maxima and is not evidence for 10s liveness',
        'actual_accepted_anchor_gap_ms': None,
        'daemon_hunt': None,
        'application_acceptance_observed': False,
    }
    return summary

result = {
    'model_run_id': 'after-fl08-fl09-power-20261110T180211Z-model',
    'computed_from': ['fl02.raw.json', 'fl08.raw.json', 'phase-markers.jsonl'],
    'model_load_recompute': [summarize(host) for host in HOSTS],
    'unavailable_nodes': {
        host: {'reason': 'remote collector annotate TypeError at line 231: max(None, float); raw stdout empty',
               'metrics': None}
        for host in ['fl01', 'fl03', 'fl05', 'fl06', 'fl07', 'fl09']
    },
}
print(json.dumps(result, ensure_ascii=False, indent=2))
