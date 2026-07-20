#!/usr/bin/env python3
"""Generate issue #41 three-host role configs without touching host state."""
import argparse
import json
import os


def write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(value, fh, indent=2, sort_keys=True)
        fh.write('\n')


def link_args(tun, address, interface, queue_summary, args, server=False):
    values = [
        '--tun-name', tun, '--tun-addr', address, '--link-id', str(args.link_id),
        '--uplink-stream', str(args.uplink_stream), '--downlink-stream', str(args.downlink_stream),
        '--air-interface', interface, '--queue-summary-file', queue_summary,
    ]
    if server:
        values += [
            '--known-clients', '1,2',
            '--client-target', '1:%s:127.0.0.1:1' % args.client1_ip,
            '--client-target', '2:%s:127.0.0.1:1' % args.client2_ip,
            '--feedback-window-period-ms', str(args.feedback_window_period_ms),
            '--feedback-window-duration-ms', str(args.feedback_window_duration_ms),
        ]
    return values


def main():
    parser = argparse.ArgumentParser(description='生成 issue #41 三机配置快照')
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--server-iface', required=True)
    parser.add_argument('--client1-iface', required=True)
    parser.add_argument('--client2-iface', required=True)
    parser.add_argument('--server-ip', default='10.80.0.1')
    parser.add_argument('--client1-ip', default='10.80.0.11')
    parser.add_argument('--client2-ip', default='10.80.0.12')
    parser.add_argument('--tun-name', default='wfb-fl0')
    parser.add_argument('--link-id', type=int, default=406)
    parser.add_argument('--uplink-stream', type=int, default=32)
    parser.add_argument('--downlink-stream', type=int, default=33)
    parser.add_argument('--uftp-port', type=int, default=1044)
    parser.add_argument('--http-port', type=int, default=8080)
    parser.add_argument('--multicast', default='230.4.4.1')
    parser.add_argument('--client1-delay-seconds', type=float, default=0)
    parser.add_argument('--client2-delay-seconds', type=float, default=2)
    parser.add_argument('--feedback-window-period-ms', type=int, default=100)
    parser.add_argument('--feedback-window-duration-ms', type=int, default=20)
    args = parser.parse_args()
    common = {'schema_version': 1, 'uftp_port': args.uftp_port,
              'max_update_size_bytes': 1073741824}
    server = dict(common, role='server', work_dir='/var/lib/wfb-ng/fl-server', node_id=255,
                  participant_node_ids=[1, 2], participant_uftp_uids=[1, 2],
                  server_uftp_uid=255, uftp_interface_address=args.server_ip,
                  uftp_multicast_address=args.multicast, http_host=args.server_ip,
                  http_port=args.http_port,
                  link_args=link_args(args.tun_name, args.server_ip + '/24', args.server_iface,
                                      '/var/lib/wfb-ng/fl-server/queue-summary.json', args, True))
    clients = {}
    for name, node_id, address, interface in (
            ('client1', 1, args.client1_ip, args.client1_iface),
            ('client2', 2, args.client2_ip, args.client2_iface)):
        clients[name] = dict(common, role='client', work_dir='/var/lib/wfb-ng/fl-client',
                             node_id=node_id, uftp_uid=node_id, uftp_bind_address=address,
                             server_uftp_multicast_address=args.multicast,
                             server_http_host=args.server_ip, server_http_port=args.http_port,
                             link_args=link_args(args.tun_name, address + '/24', interface,
                                                 '/var/lib/wfb-ng/fl-client/queue-summary.json', args))
    write_json(os.path.join(args.output_dir, 'server', 'fl-server.json'), server)
    write_json(os.path.join(args.output_dir, 'server', 'fl-server-algorithm.json'), {
        'model_path': '/usr/share/wfb-ng/fl-fixture-model.bin',
        'participant_node_ids': [1, 2],
        'start_gate_path': '/run/wfb-ng/issue41-start.gate',
        'start_gate_timeout_seconds': 300,
        'result_path': '/var/lib/wfb-ng/fl-server/algorithm-result.json',
    })
    delays = {'client1': args.client1_delay_seconds,
              'client2': args.client2_delay_seconds}
    for name, value in clients.items():
        write_json(os.path.join(args.output_dir, name, 'fl-client.json'), value)
        write_json(os.path.join(args.output_dir, name, 'fl-client-algorithm.json'), {
            'node_id': value['node_id'], 'participant_node_ids': [1, 2],
            'delay_seconds': delays[name],
            'result_path': '/var/lib/wfb-ng/fl-client/algorithm-result.json',
        })
    environment = {
        'server': ('wfb_ng.fl.acceptance_fixture:server',
                   '/etc/wfb-ng/fl-server-algorithm.json'),
        'client1': ('wfb_ng.fl.acceptance_fixture:client',
                    '/etc/wfb-ng/fl-client-algorithm.json'),
        'client2': ('wfb_ng.fl.acceptance_fixture:client',
                    '/etc/wfb-ng/fl-client-algorithm.json'),
    }
    for role, (algorithm, config) in environment.items():
        filename = 'wfb-fl-' + ('server' if role == 'server' else 'client')
        path = os.path.join(args.output_dir, role, filename)
        with open(path, 'w', encoding='ascii') as fh:
            fh.write('ALGORITHM=%s\nALGORITHM_CONFIG=%s\n' %
                     (algorithm, config))
    write_json(os.path.join(args.output_dir, 'site-parameters.json'), {
        'schema_version': 1, 'issue': 41, 'channel': 157, 'channel_width': 'HT40+',
        'server_iface': args.server_iface, 'client1_iface': args.client1_iface,
        'client2_iface': args.client2_iface, 'server_ip': args.server_ip,
        'client1_ip': args.client1_ip, 'client2_ip': args.client2_ip,
        'tun_name': args.tun_name, 'link_id': args.link_id,
        'uplink_stream': args.uplink_stream, 'downlink_stream': args.downlink_stream,
        'uftp_port': args.uftp_port, 'http_port': args.http_port,
        'multicast': args.multicast,
        'client1_delay_seconds': args.client1_delay_seconds,
        'client2_delay_seconds': args.client2_delay_seconds,
        'feedback_window_period_ms': args.feedback_window_period_ms,
        'feedback_window_duration_ms': args.feedback_window_duration_ms,
    })
    print(os.path.abspath(args.output_dir))


if __name__ == '__main__':
    main()
