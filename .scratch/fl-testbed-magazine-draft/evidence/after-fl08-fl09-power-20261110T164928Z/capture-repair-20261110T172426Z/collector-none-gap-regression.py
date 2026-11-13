#!/usr/bin/env python3
"""Regression: one identity has singleton gap=None while another has a float gap."""
import importlib.util
from pathlib import Path

source = Path(__file__).with_name('capture-heartbeats-batch-reviewed.py')
spec = importlib.util.spec_from_file_location('collector', source)
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)

def pair(boot, session, timestamp, channel, request_mono, ack_mono):
    req_body = {'type': 'NODE_HEARTBEAT', 'node_id': 1, 'boot_id': boot,
                'session': session, 'timestamp_ms': timestamp,
                'current_channel': channel, 'state': 'IDLE'}
    ack_body = {'ack': True, 'confirmed': True, 'boot_id': boot,
                'session': session, 'heartbeat_timestamp_ms': timestamp,
                'channel': channel}
    req = {'mono_ns': request_mono, 'wall_ns': request_mono,
           'direction': 'outbound', 'source': {'ip': '10.80.0.11', 'port': 9001},
           'destination': {'ip': '10.80.0.1', 'port': 9001},
           'payload_sha256': 'req-%s-%s' % (boot, timestamp), 'kind': 'heartbeat',
           'body': req_body, 'identity': collector.make_identity(req_body, '10.80.0.11',
                                                                  '10.80.0.1', 9001, 9001,
                                                                  'heartbeat')}
    ack = {'mono_ns': ack_mono, 'wall_ns': ack_mono,
           'direction': 'inbound', 'source': {'ip': '10.80.0.1', 'port': 9001},
           'destination': {'ip': '10.80.0.11', 'port': 9001},
           'payload_sha256': 'ack-%s-%s' % (boot, timestamp), 'kind': 'heartbeat_ack',
           'body': ack_body, 'identity': collector.make_identity(ack_body, '10.80.0.1',
                                                                  '10.80.0.11', 9001, 9001,
                                                                  'heartbeat_ack')}
    return req, ack

events = []
for args in [('boot-single', 'session-single', 1, 157, 100, 200),
             ('boot-many', 'session-many', 2, 157, 300, 400),
             ('boot-many', 'session-many', 3, 157, 500, 600)]:
    events.extend(pair(*args))
result = collector.annotate(events, (1,), 1, 1_000)
assert result['max_wire_ack_receive_gap_ms'] is not None
assert result['max_timely_candidate_send_gap_ms'] is not None
assert result['identity_session_channel_summary']
print('none-gap-regression-ok')
