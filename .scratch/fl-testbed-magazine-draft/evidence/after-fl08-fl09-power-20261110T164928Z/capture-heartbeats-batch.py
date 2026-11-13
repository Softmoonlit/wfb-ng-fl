#!/usr/bin/env python3
"""Passive AF_PACKET heartbeat capture on the existing TUN; never sends."""
import argparse
import hashlib
import json
import select
import signal
import socket
import struct
import sys
import time

PORTS = (9001,)
CLIENT_IPS = {n: '10.80.0.%d' % (n + 10) for n in (1, 2, 3, 5, 6, 7, 8, 9)}
NODE_BY_IP = {ip: n for n, ip in CLIENT_IPS.items()}
PACKET_STATISTICS = 6
STOP_REQUESTED = False


def pstats(sock):
    try:
        raw = sock.getsockopt(getattr(socket, 'SOL_PACKET', 263), PACKET_STATISTICS, 8)
        packets, drops = struct.unpack('=II', raw)
        return {'packets': packets, 'drops': drops}
    except (OSError, struct.error) as exc:
        return {'error': repr(exc)}


def request_stop(signum, frame):
    global STOP_REQUESTED
    STOP_REQUESTED = True


def clients(value):
    if value == 'all':
        return tuple(sorted(CLIENT_IPS))
    try:
        result = tuple(sorted({int(x) for x in value.split(',') if x}))
    except ValueError as exc:
        raise argparse.ArgumentTypeError('clients must be all or comma-separated node ids') from exc
    if not result or any(x not in CLIENT_IPS for x in result):
        raise argparse.ArgumentTypeError('clients must be from 1,2,3,5,6,7,8,9')
    return result


def ports(value):
    try:
        result = tuple(sorted({int(x) for x in value.split(',') if x}))
    except ValueError as exc:
        raise argparse.ArgumentTypeError('ports must be comma-separated integers') from exc
    if not result or any(x < 1 or x > 65535 for x in result):
        raise argparse.ArgumentTypeError('ports must be valid UDP ports')
    return result


def ip_payload(packet):
    if packet and packet[0] >> 4 == 4:
        return packet
    return packet[14:] if len(packet) >= 14 and packet[14] >> 4 == 4 else None


def endpoint(ip, port):
    return {'ip': ip, 'port': port}


def make_identity(body, src, dst, sport, dport, kind):
    if kind == 'heartbeat':
        node, timestamp, channel = body.get('node_id'), body.get('timestamp_ms'), body.get('current_channel')
    else:
        node = NODE_BY_IP.get(dst) or NODE_BY_IP.get(src)
        timestamp, channel = body.get('heartbeat_timestamp_ms'), body.get('channel')
    return {'node_id': node, 'boot_id': body.get('boot_id'), 'session': body.get('session'),
            'request_timestamp_ms': timestamp, 'channel': channel,
            'source': endpoint(src, sport), 'destination': endpoint(dst, dport)}


def request_complete(item):
    ident = item['identity']
    return (isinstance(ident.get('node_id'), int) and ident['node_id'] in CLIENT_IPS
            and isinstance(ident.get('boot_id'), str) and bool(ident['boot_id'])
            and (ident.get('session') is None or isinstance(ident.get('session'), str))
            and isinstance(ident.get('request_timestamp_ms'), int)
            and isinstance(ident.get('channel'), int))


def ack_complete(body):
    return (body.get('ack') is True and body.get('confirmed') is True
            and isinstance(body.get('boot_id'), str) and bool(body.get('boot_id'))
            and isinstance(body.get('session'), str) and bool(body.get('session'))
            and isinstance(body.get('heartbeat_timestamp_ms'), int)
            and isinstance(body.get('channel'), int))


def key(ident):
    return (ident.get('node_id'), ident.get('boot_id'), ident.get('session'),
            ident.get('request_timestamp_ms'), ident.get('channel'))


def annotate(events, selected_nodes, local_node, capture_end_mono_ns=None):
    requests, acks, seen = {}, [], {}
    for index, event in enumerate(events):
        packet_key = (event['direction'], event['source']['ip'], event['destination']['ip'],
                      event['source']['port'], event['destination']['port'], event['payload_sha256'])
        event['duplicate_packet'] = packet_key in seen
        if event['duplicate_packet']:
            event['duplicate_of_event_index'] = seen[packet_key]
        else:
            seen[packet_key] = index
        if event['kind'] not in ('heartbeat', 'heartbeat_ack'):
            continue
        ident = event['identity']
        if local_node is not None and ident.get('node_id') != local_node:
            event['ignored_nonlocal_node'] = True
            continue
        if event['kind'] == 'heartbeat':
            event['identity_complete'] = request_complete(event)
            if event['identity_complete'] and not event['duplicate_packet']:
                requests.setdefault(key(ident), []).append(event)
        else:
            event['identity_complete'] = ack_complete(event['body'])
            acks.append(event)

    samples = []
    for ack in acks:
        if ack['duplicate_packet']:
            ack['valid_for_count'], ack['invalid_reason'] = False, 'duplicate_packet'
            continue
        if not ack['identity_complete']:
            ack['valid_for_count'], ack['invalid_reason'] = False, 'invalid_ack_fields_or_not_confirmed'
            continue
        candidates = [r for r in requests.get(key(ack['identity']), []) if r['mono_ns'] <= ack['mono_ns']]
        candidates = [r for r in candidates if r['source'] == ack['destination'] and r['destination'] == ack['source']]
        if not candidates:
            ack['valid_for_count'], ack['invalid_reason'] = False, 'unmatched_ack_not_observed_in_window'
            ack['unmatched_ack'] = True
            continue
        request = candidates[-1]
        if 'matched_ack_event_index' in request:
            ack['valid_for_count'], ack['invalid_reason'] = False, 'request_already_matched'
            continue
        request['matched_ack_event_index'] = events.index(ack)
        ident, latency = ack['identity'], ack['mono_ns'] - request['mono_ns']
        sample = {
            'sample_id': '%s/%s/%s/%s/%s' % key(ident), 'node_id': ident['node_id'],
            'boot_id': ident['boot_id'], 'session': ident['session'],
            'request_timestamp_ms': ident['request_timestamp_ms'], 'channel': ident['channel'],
            'request_event_index': events.index(request), 'ack_event_index': events.index(ack),
            'request_mono_ns': request['mono_ns'], 'ack_mono_ns': ack['mono_ns'],
            'ack_rtt_ns': latency, 'ack_rtt_ms': latency / 1_000_000.0,
            'within_230ms_candidate': 0 <= latency < 230_000_000,
            'confirmed': True, 'acceptance_observed': False,
        }
        ack['valid_for_count'], ack['sample_id'] = True, sample['sample_id']
        request['sample_id'] = sample['sample_id']
        samples.append(sample)

    def group_of(item):
        return (item['node_id'], item['boot_id'], item['session'], item['channel'])

    def group_name(group):
        return '%s/%s/%s/%s' % group

    all_requests = [request for rows in requests.values() for request in rows]
    request_groups = {}
    for request in all_requests:
        request_groups.setdefault(group_of(request['identity']), []).append(request)
    sample_groups = {}
    for sample in samples:
        sample_groups.setdefault(group_of(sample), []).append(sample)
    request_count = len(all_requests)
    right_censored = 0
    unmatched_requests = 0
    for request in all_requests:
        if 'matched_ack_event_index' in request:
            continue
        if (capture_end_mono_ns is not None
                and capture_end_mono_ns - request['mono_ns'] < 230_000_000):
            right_censored += 1
        else:
            unmatched_requests += 1

    identity_summary = {}
    identity_wire_gap = {}
    identity_timely_send_gap = {}
    for group in sorted(set(request_groups) | set(sample_groups), key=group_name):
        group_requests = request_groups.get(group, [])
        group_samples = sample_groups.get(group, [])
        timely = [sample for sample in group_samples if sample['within_230ms_candidate']]
        late = [sample for sample in group_samples if not sample['within_230ms_candidate']]
        request_times = [sample['ack_mono_ns'] for sample in sorted(group_samples, key=lambda x: x['ack_mono_ns'])]
        timely_send_times = [sample['request_mono_ns'] for sample in sorted(timely, key=lambda x: x['request_mono_ns'])]
        wire_gaps = [right - left for left, right in zip(request_times, request_times[1:])]
        send_gaps = [right - left for left, right in zip(timely_send_times, timely_send_times[1:])]
        group_unmatched = sum('matched_ack_event_index' not in request for request in group_requests)
        group_right = sum(
            'matched_ack_event_index' not in request and capture_end_mono_ns is not None
            and capture_end_mono_ns - request['mono_ns'] < 230_000_000
            for request in group_requests)
        challenge = sum(
            item.get('identity', {}).get('node_id') == group[0]
            and item.get('identity', {}).get('boot_id') == group[1]
            and item.get('identity', {}).get('session') == group[2]
            and item.get('identity', {}).get('channel') == group[3]
            and item.get('body', {}).get('confirmed') is False for item in acks)
        identity_summary[group_name(group)] = {
            'request_count': len(group_requests), 'paired_confirmed_count': len(group_samples),
            'timely_count': len(timely), 'late_count': len(late),
            'unmatched_count': max(0, group_unmatched - group_right),
            'right_censored_count': group_right, 'challenge_count': challenge,
            'candidate_ratio_paired_confirmed': (len(timely) / len(group_samples)) if group_samples else None,
            'candidate_ratio_window_requests': (
                len(timely) / (len(group_requests) - group_right)
                if len(group_requests) - group_right else None),
        }
        identity_wire_gap[group_name(group)] = max(wire_gaps) / 1_000_000.0 if wire_gaps else None
        identity_timely_send_gap[group_name(group)] = max(send_gaps) / 1_000_000.0 if send_gaps else None

    samples.sort(key=lambda x: x['ack_mono_ns'])
    by_node = {node: [x for x in samples if x['node_id'] == node] for node in selected_nodes}
    requests_by_node = {node: [x for x in all_requests if x['identity']['node_id'] == node] for node in selected_nodes}
    candidate_by_node = {node: sum(x['within_230ms_candidate'] for x in rows) for node, rows in by_node.items()}
    late_by_node = {node: len(rows) - candidate_by_node[node] for node, rows in by_node.items()}
    paired_by_node = {node: len(rows) for node, rows in by_node.items()}
    request_by_node = {node: len(rows) for node, rows in requests_by_node.items()}
    right_by_node = {node: sum(
        'matched_ack_event_index' not in request and capture_end_mono_ns is not None
        and capture_end_mono_ns - request['mono_ns'] < 230_000_000 for request in rows)
        for node, rows in requests_by_node.items()}
    unmatched_by_node = {node: max(0, request_by_node[node] - paired_by_node[node] - right_by_node[node])
                         for node in selected_nodes}
    local_gap = max((gap for group, gap in identity_wire_gap.items()
                     if group.startswith(str(local_node) + '/')), default=None)
    local_send_gap = max((gap for group, gap in identity_timely_send_gap.items()
                          if group.startswith(str(local_node) + '/')), default=None)
    paired = len(samples)
    timely = sum(candidate_by_node.values())
    late = sum(late_by_node.values())
    window_requests = request_count - right_censored
    challenge_count = sum(item.get('body', {}).get('confirmed') is False for item in acks)
    return {
        'samples': samples, 'request_count': request_count, 'paired_confirmed_count': paired,
        'timely_count': timely, 'late_count': late, 'unmatched_count': unmatched_requests,
        'right_censored_count': right_censored, 'challenge_count': challenge_count,
        'unmatched_ack_count': sum(item.get('unmatched_ack', False) for item in acks),
        'request_count_by_node': request_by_node, 'paired_confirmed_count_by_node': paired_by_node,
        'timely_count_by_node': candidate_by_node, 'late_count_by_node': late_by_node,
        'unmatched_count_by_node': unmatched_by_node,
        'candidate_ratio_paired_confirmed': timely / paired if paired else None,
        'candidate_ratio_window_requests': timely / window_requests if window_requests else None,
        'candidate_ratio_window_denominator': 'request_count-right_censored_count',
        'candidate_ratio_paired_denominator': 'paired_confirmed_count',
        'identity_session_channel_summary': identity_summary,
        'max_wire_ack_receive_gap_ms_by_identity': identity_wire_gap,
        'max_timely_candidate_send_gap_ms_by_identity': identity_timely_send_gap,
        'max_wire_ack_receive_gap_ms': local_gap,
        'max_timely_candidate_send_gap_ms': local_send_gap,
        'request_count_by_node': request_by_node, 'right_censored_count_by_node': right_by_node,
        'rtt_semantics': 'client_send_to_client_ack_receive',
        'liveness_gap_status': 'timely_candidate_send_gap_only; non_app_anchor',
        'application_acceptance_observed': False, 'unmatched_is_not_physical_loss': True,
        'selected_nodes': list(selected_nodes), 'local_node': local_node, 'host_role': 'client',
        'duplicate_packet_count': sum(item.get('duplicate_packet', False) for item in events),
        'invalid_ack_count': sum(1 for item in acks if not item.get('valid_for_count')),
    }


def main():
    parser = argparse.ArgumentParser(description='passive AF_PACKET heartbeat capture')
    parser.add_argument('--duration', type=float, required=True)
    parser.add_argument('--clients', type=clients, default=tuple(sorted(CLIENT_IPS)))
    parser.add_argument('--ports', type=ports, default=PORTS)
    parser.add_argument('--interface')
    args = parser.parse_args()
    if not 0 < args.duration <= 1500:
        parser.error('duration must be >0 and <=1500 seconds')
    host = socket.gethostname()
    interface = args.interface or ('fl-s' if host == 'fl00' else 'fl-c' + str(int(host[2:])))
    loopback_test = interface == 'lo'
    if host == 'fl00' and not loopback_test:
        parser.error('client-only capture: fl00 is not a measurement client')
    local_node = None if loopback_test else int(host[2:])
    selected_ips = {CLIENT_IPS[node] for node in args.clients}
    clock = time.get_clock_info('monotonic')
    result = {
        'schema_version': 3, 'capture_role': 'passive_heartbeat_wire_observation',
        'host': host, 'interface': interface, 'selected_nodes': list(args.clients),
        'selected_ports': list(args.ports),
        'clock': {'rtt_clock': 'time.monotonic_ns', 'wall_clock': 'time.time_ns',
                  'monotonic_implementation': clock.implementation,
                  'monotonic_resolution_seconds': clock.resolution,
                  'wall_time_is_alignment_metadata_only': True,
                  'cross_host_monotonic_comparison': False},
        'identity_contract': {
            'request': ['node_id', 'boot_id', 'session', 'timestamp_ms', 'current_channel', 'source'],
            'ack': ['ack', 'confirmed', 'boot_id', 'session', 'heartbeat_timestamp_ms', 'channel', 'source'],
            'valid_ack_requires': 'confirmed=true, exact identity, and inverse source/destination endpoint',
            'duplicate_policy': 'exact direction/endpoint/payload duplicates are retained and excluded',
            'application_acceptance': 'not observable from passive capture'},
    }
    global STOP_REQUESTED
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    sock, events, errors = None, [], []
    start_mono = start_wall = None
    try:
        sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
        sock.bind((interface, 0)); sock.setblocking(False)
        start_mono, start_wall = time.monotonic_ns(), time.time_ns()
        before, deadline = pstats(sock), start_mono + int(args.duration * 1_000_000_000)
        while not STOP_REQUESTED and time.monotonic_ns() < deadline:
            wait = min(.2, max(0, (deadline - time.monotonic_ns()) / 1_000_000_000))
            if not select.select([sock], [], [], wait)[0]:
                continue
            packet, address = sock.recvfrom(65535)
            mono = time.monotonic_ns(); ip = ip_payload(packet)
            if ip is None or len(ip) < 28 or ip[9] != 17:
                continue
            offset = (ip[0] & 15) * 4
            if offset < 20 or len(ip) < offset + 8:
                continue
            total = struct.unpack_from('!H', ip, 2)[0]
            end = min(len(ip), total) if total >= offset + 8 else len(ip)
            sport, dport = struct.unpack_from('!HH', ip, offset)
            src, dst = socket.inet_ntoa(ip[12:16]), socket.inet_ntoa(ip[16:20])
            if (sport not in args.ports and dport not in args.ports) or (src not in selected_ips and dst not in selected_ips):
                continue
            payload = ip[offset + 8:end]; digest = hashlib.sha256(payload).hexdigest()
            try:
                body, parse_error = json.loads(payload.decode()), None
            except (UnicodeError, ValueError) as exc:
                body, parse_error = None, repr(exc)
            if isinstance(body, dict) and body.get('type') == 'NODE_HEARTBEAT':
                kind = 'heartbeat'
            elif isinstance(body, dict) and ('heartbeat_timestamp_ms' in body or 'ack' in body):
                kind = 'heartbeat_ack'
            else:
                kind = 'other_control'
            event = {'event_index': len(events), 'mono_ns': mono, 'wall_ns': time.time_ns(),
                     'direction': 'outbound' if len(address) > 2 and address[2] == socket.PACKET_OUTGOING else 'inbound',
                     'source': endpoint(src, sport), 'destination': endpoint(dst, dport),
                     'payload_sha256': digest, 'payload_bytes': len(payload), 'kind': kind, 'body': body}
            if parse_error:
                event['parse_error'] = parse_error
            if kind in ('heartbeat', 'heartbeat_ack') and isinstance(body, dict):
                event['identity'] = make_identity(body, src, dst, sport, dport, kind)
            events.append(event)
        end_mono, end_wall, after = time.monotonic_ns(), time.time_ns(), pstats(sock)
    except Exception as exc:
        errors.append(repr(exc)); end_mono, end_wall = time.monotonic_ns(), time.time_ns()
        before, after = locals().get('before'), pstats(sock) if sock else None
    finally:
        if sock:
            sock.close()
    analysis = annotate(events, args.clients, local_node, end_mono) if not errors else {
        'samples': [], 'request_count': 0, 'paired_confirmed_count': 0, 'timely_count': 0,
        'late_count': 0, 'unmatched_count': 0, 'right_censored_count': 0, 'challenge_count': 0,
        'candidate_ratio_paired_confirmed': None, 'candidate_ratio_window_requests': None,
        'candidate_ratio_window_denominator': 'request_count-right_censored_count',
        'candidate_ratio_paired_denominator': 'paired_confirmed_count',
        'invalid_ack_count': 0, 'duplicate_packet_count': 0,
        'unmatched_is_not_physical_loss': True,
        'liveness_gap_status': 'capture_error', 'application_acceptance_observed': False,
        'rtt_semantics': 'client_send_to_client_ack_receive', 'selected_nodes': list(args.clients),
    }
    before_packets = before.get('packets', 0) if isinstance(before, dict) else 0
    after_packets = after.get('packets', 0) if isinstance(after, dict) else 0
    before_drops = before.get('drops', 0) if isinstance(before, dict) else 0
    after_drops = after.get('drops', 0) if isinstance(after, dict) else 0
    analysis['packet_statistics_delta'] = {'packets': after_packets - before_packets,
                                           'drops': after_drops - before_drops}
    analysis['capture_completeness'] = ('complete_no_socket_drops'
                                        if analysis['packet_statistics_delta']['drops'] == 0
                                        else 'incomplete_socket_drops_observed')
    analysis['loopback_test'] = loopback_test
    result.update({'stop_requested': STOP_REQUESTED,
        'capture_window': {
        'requested_duration_seconds': args.duration, 'start_mono_ns': start_mono,
        'end_mono_ns': end_mono, 'start_wall_ns': start_wall, 'end_wall_ns': end_wall,
        'elapsed_mono_seconds': ((end_mono - start_mono) / 1_000_000_000.0) if start_mono else None,
        'wall_alignment_error_note': 'wall is per-host metadata; phase alignment needs external runner markers'},
        'socket_statistics_before': before, 'socket_statistics_after': after,
        'socket_statistics_delta_note': 'PACKET_STATISTICS kernel accounting; capture never injects',
        'events': events, 'analysis': analysis, 'errors': errors})
    print(json.dumps(result, ensure_ascii=False, separators=(',', ':')))
    return bool(errors)


if __name__ == '__main__':
    raise SystemExit(main())
