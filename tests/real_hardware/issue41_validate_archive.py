#!/usr/bin/env python3
"""Fail-closed offline validator for issue #41 three-host evidence."""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from wfb_ng.tests.v6_formal_summary import (  # noqa: E402
    SCENARIO_V6_REAL_HARDWARE_DOWNLINK_SHARED,
    SCENARIO_V6_REAL_HARDWARE_UPLINK,
    SummaryValidationError,
    load_summary,
)

ROLES = ('server', 'client1', 'client2')
NODES = (1, 2)


class Section:
    def __init__(self, name):
        self.name = name
        self.checks = []

    def require(self, condition, check, detail=''):
        self.checks.append({'check': check, 'status': 'PASS' if condition else 'FAIL',
                            'detail': str(detail)})
        return condition

    def value(self):
        return {'schema_version': 1, 'section': self.name,
                'status': 'PASS' if all(item['status'] == 'PASS' for item in self.checks) else 'FAIL',
                'checks': self.checks}


def read_json(path):
    try:
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def read_text(path):
    try:
        with open(path, encoding='utf-8', errors='replace') as fh:
            return fh.read()
    except OSError:
        return ''


def digest(path):
    value = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def artifact(section, path, manifest, artifact_type, round_id, node_id=None):
    valid = isinstance(manifest, dict)
    valid = valid and manifest.get('schema_version') == 1
    valid = valid and manifest.get('artifact_type') == artifact_type
    valid = valid and manifest.get('round_id') == round_id
    if node_id is not None:
        valid = valid and manifest.get('node_id') == node_id
    file_ok = os.path.isfile(path)
    sha_ok = False
    if valid and file_ok:
        sha_ok = manifest.get('size_bytes') == os.path.getsize(path) and manifest.get('sha256') == digest(path)
    section.require(valid, '%s manifest contract' % artifact_type, manifest)
    section.require(file_ok, '%s file exists' % artifact_type, path)
    section.require(sha_ok, '%s manifest SHA/size' % artifact_type, path)


def validate_checksums(root, role, section):
    directory = os.path.join(root, role)
    checksum = os.path.join(directory, 'SHA256SUMS')
    if not section.require(os.path.isfile(checksum), '%s SHA256SUMS exists' % role, checksum):
        return
    result = subprocess.run(['sha256sum', '-c', 'SHA256SUMS'], cwd=directory,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, check=False)
    section.require(result.returncode == 0, '%s SHA256SUMS verifies' % role, result.stdout.strip())
    listed = set()
    for line in read_text(checksum).splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            listed.add(parts[1].lstrip('*'))
    actual = set()
    for current, _, files in os.walk(directory):
        for name in files:
            relative = os.path.relpath(os.path.join(current, name), directory)
            if relative != 'SHA256SUMS':
                actual.add(relative)
    section.require(listed == actual, '%s SHA256SUMS complete manifest' % role,
                    {'missing': sorted(actual - listed), 'extra': sorted(listed - actual)})


def parse_status(path):
    connects, results = [], []
    for line in read_text(path).splitlines():
        fields = line.split(';')
        try:
            if fields[0] == 'CONNECT' and len(fields) == 3:
                connects.append((fields[1], int(fields[2], 16)))
            elif fields[0] == 'RESULT' and len(fields) == 6:
                results.append((int(fields[1], 16), fields[2], fields[4]))
        except ValueError:
            return [], []
    return connects, results


def find_one(directory, pattern):
    try:
        matches = [os.path.join(directory, name) for name in os.listdir(directory)
                   if re.fullmatch(pattern, name)]
    except OSError:
        return None
    return matches[0] if len(matches) == 1 else None


def main():
    parser = argparse.ArgumentParser(description='校验 issue #41 三机现场归档')
    parser.add_argument('archive_root')
    parser.add_argument('--output-dir', default='')
    args = parser.parse_args()
    root = os.path.abspath(args.archive_root)
    output = os.path.abspath(args.output_dir or os.path.join(root, 'summary'))
    os.makedirs(output, exist_ok=True)
    baseline = Section('baseline_uplink')
    downlink = Section('downlink_feedback')
    runtime = Section('runtime_loop')

    versions = {}
    installed_hashes = {}
    hostnames = {}
    client_algorithms = {}
    for role in ROLES:
        validate_checksums(root, role, runtime)
        config = read_json(os.path.join(root, role, 'role-config.json'))
        config_ok = isinstance(config, dict) and isinstance(config.get('link_args'), list)
        runtime.require(config_ok, '%s role config snapshot' % role, config)
        if config_ok:
            args_list = config['link_args']
            try:
                tun = args_list[args_list.index('--tun-name') + 1]
                address = args_list[args_list.index('--tun-addr') + 1]
            except (ValueError, IndexError):
                tun, address = '', ''
            addresses = read_text(os.path.join(root, role, 'addresses-running.txt'))
            sockets = read_text(os.path.join(root, role, 'sockets-running.txt'))
            routes = read_text(os.path.join(root, role, 'routes-running.txt'))
            wireless = read_text(os.path.join(root, role, 'wireless-running.txt'))
            version = read_text(os.path.join(root, role, 'version.txt'))
            version_match = re.fullmatch(
                r'commit=([0-9a-f]{40})\ndescribe=([^\n]+)\n?', version)
            version_ok = (version_match is not None and
                          not version_match.group(2).endswith('-dirty'))
            runtime.require(
                version_ok, '%s clean commit/version snapshot' % role,
                version.strip())
            if version_ok:
                versions[role] = version_match.group(1)
            installed = read_text(
                os.path.join(root, role, 'installed-sha256.txt'))
            parsed_hashes = {}
            for line in installed.splitlines():
                fields = line.split()
                if len(fields) == 2 and re.fullmatch(r'[0-9a-f]{64}', fields[0]):
                    parsed_hashes[os.path.basename(fields[1])] = fields[0]
            runtime.require(
                set(parsed_hashes) == {
                    'wfb_v6_uplink', 'wfb-fl-server', 'wfb-fl-client'},
                '%s installed binary SHA snapshot' % role, parsed_hashes)
            installed_hashes[role] = parsed_hashes
            hostname = read_text(
                os.path.join(root, role, 'hostname.txt')).strip()
            runtime.require(
                bool(hostname) and '\n' not in hostname,
                '%s hostname snapshot' % role, hostname)
            hostnames[role] = hostname
            multicast = config.get('uftp_multicast_address', config.get('server_uftp_multicast_address', ''))
            runtime.require('type monitor' in wireless and 'channel 157' in wireless and 'HT40+' in wireless,
                            '%s monitor/channel snapshot' % role, wireless.strip())
            runtime.require(bool(tun) and tun in addresses and address in addresses,
                            '%s actual TUN/IP snapshot' % role, {'tun': tun, 'address': address})
            runtime.require(':%d' % config.get('uftp_port', -1) in sockets,
                            '%s actual UFTP port snapshot' % role, config.get('uftp_port'))
            if role == 'server':
                runtime.require(':%d' % config.get('http_port', -1) in sockets,
                                'server actual HTTP port snapshot', config.get('http_port'))
            runtime.require(bool(multicast) and multicast in routes and ('dev ' + tun) in routes,
                            '%s actual multicast route snapshot' % role,
                            {'multicast': multicast, 'tun': tun})
        lifecycle = read_text(os.path.join(root, role, 'stopped-lifecycle.txt'))
        runtime.require('result=PASS' in lifecycle and 'main_pid=0' in lifecycle and
                        'orphan_process_count=0' in lifecycle,
                        '%s stopped cgroup/no orphans' % role, lifecycle.strip())
        restart = read_text(os.path.join(root, role, 'restart-lifecycle.txt'))
        runtime.require('result=PASS' in restart and 'restart_active_state=active' in restart and
                        'stopped_active_state=inactive' in restart and 'stopped_main_pid=0' in restart and
                        'orphan_process_count=0' in restart,
                        '%s restart/cgroup/stop lifecycle' % role, restart.strip())
        unit = read_text(os.path.join(root, role, 'unit-show.txt'))
        runtime.require('ActiveState=active' in unit and 'ControlGroup=' in unit,
                        '%s systemd/cgroup running record' % role, unit.strip())

    runtime.require(
        len(versions) == 3 and len(set(versions.values())) == 1,
        'three hosts use the same clean commit', versions)
    runtime.require(
        len(installed_hashes) == 3 and
        all(installed_hashes[role] == installed_hashes['server']
            for role in ROLES),
        'three hosts use identical installed binaries', installed_hashes)
    runtime.require(
        len(hostnames) == 3 and len(set(hostnames.values())) == 3,
        'server and clients are three distinct hosts', hostnames)

    round_ids = [read_text(os.path.join(root, role, 'ROUND_ID')).strip() for role in ROLES]
    round_id = round_ids[0] if round_ids else ''
    runtime.require(bool(round_id) and len(set(round_ids)) == 1, 'same non-empty round id', round_ids)

    baseline_path = os.path.join(
        root, 'baseline_uplink', 'formal_2a_summary.json')
    try:
        baseline_summary = load_summary(baseline_path)
    except (OSError, UnicodeError, json.JSONDecodeError,
            SummaryValidationError) as exc:
        baseline_summary = None
        baseline_detail = str(exc)
    else:
        baseline_detail = baseline_summary
    baseline.require(
        isinstance(baseline_summary, dict) and
        baseline_summary.get('scenario_id') ==
        SCENARIO_V6_REAL_HARDWARE_UPLINK,
        'existing formal real-hardware uplink baseline', baseline_detail)
    baseline_result = read_text(
        os.path.join(root, 'baseline_uplink', 'result.md'))
    baseline.require(
        bool(re.search(r'^- 总结论: \*\*PASS\*\*$', baseline_result, re.M)) or
        bool(re.search(r'^\[通过\].*结论', baseline_result, re.M)),
        'uplink baseline result explicitly passed', baseline_result.strip())
    baseline_required = ('SHA256SUMS',)
    baseline.require(
        all(os.path.isfile(os.path.join(root, 'baseline_uplink', name))
            for name in baseline_required) and
        any(name.endswith('.log')
            for _, _, files in os.walk(os.path.join(root, 'baseline_uplink'))
            for name in files) and
        any('queue' in name and name.endswith('.json')
            for _, _, files in os.walk(os.path.join(root, 'baseline_uplink'))
            for name in files),
        'uplink baseline keeps checksums, raw logs and queue evidence')
    baseline_checksums = subprocess.run(
        ['sha256sum', '-c', 'SHA256SUMS'],
        cwd=os.path.join(root, 'baseline_uplink'),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, check=False)
    baseline.require(
        baseline_checksums.returncode == 0,
        'uplink baseline SHA256SUMS verifies',
        baseline_checksums.stdout.strip())

    server = os.path.join(root, 'server')
    server_log = read_text(os.path.join(server, 'journal.log'))
    downlink_2a_path = os.path.join(
        server, 'downlink-formal-2a-summary.json')
    try:
        downlink_2a = load_summary(downlink_2a_path)
    except (OSError, UnicodeError, json.JSONDecodeError,
            SummaryValidationError) as exc:
        downlink_2a = None
        downlink_2a_detail = str(exc)
    else:
        downlink_2a_detail = downlink_2a
    downlink.require(
        isinstance(downlink_2a, dict) and
        downlink_2a.get('scenario_id') ==
        SCENARIO_V6_REAL_HARDWARE_DOWNLINK_SHARED and
        downlink_2a.get('feedback_window_open_count', 0) > 0 and
        downlink_2a.get('feedback_window_open_count') ==
        downlink_2a.get('feedback_window_close_count') and
        all(downlink_2a.get(
            'feedback_uplink_hit_total_by_node', {}).get(str(node), 0) > 0
            for node in NODES),
        'formal shared downlink/feedback 2A summary', downlink_2a_detail)

    status_path = find_one(server, r'uftp-[0-9a-f-]+\.status')
    downlink.require(status_path is not None, 'exactly one shared UFTP status', status_path)
    connects, results = parse_status(status_path) if status_path else ([], [])
    expected_connects = sorted(('success', node) for node in NODES)
    expected_results = sorted((node, '%s/%s' % (round_id, name), 'copy')
                              for node in NODES for name in ('model.bin', 'model.manifest.json'))
    downlink.require(sorted(connects) == expected_connects, 'two-client CONNECT matrix', connects)
    downlink.require(sorted(results) == expected_results, 'two clients x two files UFTP matrix', results)
    opens = len(re.findall(r'^feedback_window_open ', server_log, re.M))
    closes = len(re.findall(r'^feedback_window_close ', server_log, re.M))
    hits = {str(node): len(re.findall(r'^feedback_uplink_hit node_id=%d ' % node, server_log, re.M))
            for node in NODES}
    downlink.require(opens > 0 and opens == closes, 'feedback open/close balanced', {'open': opens, 'close': closes})
    downlink.require(all(hits[str(node)] > 0 for node in NODES), 'feedback hit each client', hits)

    server_round = os.path.join(server, 'round')
    server_manifest = read_json(os.path.join(server_round, 'model.manifest.json'))
    participant_ok = isinstance(server_manifest, dict) and server_manifest.get('participant_node_ids') == [1, 2]
    runtime.require(participant_ok, 'model participant set [1,2]', server_manifest)
    artifact(runtime, os.path.join(server_round, 'model.bin'), server_manifest, 'model', round_id)
    server_model_sha = server_manifest.get('sha256') if isinstance(server_manifest, dict) else None
    for role, node_id in (('client1', 1), ('client2', 2)):
        client_round = os.path.join(root, role, 'round')
        model_manifest = read_json(os.path.join(client_round, 'model.manifest.json'))
        artifact(runtime, os.path.join(client_round, 'model.bin'), model_manifest, 'model', round_id)
        runtime.require(isinstance(model_manifest, dict) and model_manifest.get('sha256') == server_model_sha,
                        '%s model end-to-end SHA' % role, model_manifest)
        client_update = read_json(os.path.join(client_round, 'update.manifest.json'))
        artifact(runtime, os.path.join(client_round, 'update.bin'), client_update, 'update', round_id, node_id)
        server_update_dir = os.path.join(server_round, 'updates', str(node_id))
        server_update = read_json(os.path.join(server_update_dir, 'update.manifest.json'))
        artifact(runtime, os.path.join(server_update_dir, 'update.bin'), server_update, 'update', round_id, node_id)
        runtime.require(isinstance(client_update, dict) and client_update == server_update,
                        'node %d update end-to-end manifest/SHA' % node_id)
        http_result = read_json(os.path.join(
            client_round, 'http-put-result.json'))
        runtime.require(
            isinstance(http_result, dict) and set(http_result) == {
                'schema_version', 'round_id', 'node_id',
                'connection_count', 'put_request_count', 'continue_status',
                'final_status', 'final_content_length'} and
            http_result.get('schema_version') == 1 and
            http_result.get('round_id') == round_id and
            http_result.get('node_id') == node_id and
            http_result.get('connection_count') == 1 and
            http_result.get('put_request_count') == 1 and
            http_result.get('continue_status') == 100 and
            http_result.get('final_status') == 201 and
            http_result.get('final_content_length') == 0,
            '%s single connection/PUT and verified HTTP 201' % role,
            http_result)
        algorithm = read_json(os.path.join(root, role, 'algorithm-result.json'))
        timings = (algorithm.get('interface_timings', {})
                   if isinstance(algorithm, dict) else {})
        runtime.require(
            isinstance(algorithm, dict) and
            algorithm.get('status') == 'succeeded' and
            algorithm.get('node_id') == node_id and
            set(timings) == {'wait_for_model', 'submit_update'} and
            algorithm.get('input_sha256') == server_model_sha and
            isinstance(client_update, dict) and
            algorithm.get('output_sha256') == client_update.get('sha256'),
            '%s formal fixture result and Runtime interfaces' % role,
            algorithm)
        if isinstance(algorithm, dict):
            client_algorithms[role] = algorithm
        state = read_json(os.path.join(client_round, 'round-state.json'))
        runtime.require(isinstance(state, dict) and state.get('state') == 'succeeded' and state.get('round_id') == round_id,
                        '%s round-state succeeded' % role, state)
    algorithm = read_json(os.path.join(server, 'algorithm-result.json'))
    node_order = ([item.get('node_id')
                   for item in algorithm.get('update_mapping', [])]
                  if isinstance(algorithm, dict) else [])
    timings = (algorithm.get('interface_timings', {})
               if isinstance(algorithm, dict) else {})
    runtime.require(
        isinstance(algorithm, dict) and
        algorithm.get('status') == 'succeeded' and
        set(timings) == {'publish_model', 'wait_for_updates'} and
        node_order == [1, 2],
        'server algorithm JSON/full sorted mapping', algorithm)
    try:
        client1_delay = client_algorithms['client1'][
            'configured_delay_seconds']
        client2_delay = client_algorithms['client2'][
            'configured_delay_seconds']
        client1_submit = client_algorithms['client1'][
            'interface_timings']['submit_update']['completed_at']
        client2_submit = client_algorithms['client2'][
            'interface_timings']['submit_update']['completed_at']
        server_complete = timings['wait_for_updates']['completed_at']
        timing_ok = (
            isinstance(client1_delay, (int, float)) and
            isinstance(client2_delay, (int, float)) and
            not isinstance(client1_delay, bool) and
            not isinstance(client2_delay, bool) and
            client1_delay < client2_delay and
            client1_submit < client2_submit <= server_complete)
    except (KeyError, TypeError):
        timing_ok = False
        client1_submit = client2_submit = server_complete = None
    runtime.require(
        timing_ok,
        'faster client submits first and strict server boundary follows both', {
            'client1_submit_completed_at': client1_submit,
            'client2_submit_completed_at': client2_submit,
            'server_wait_completed_at': server_complete,
        })
    state = read_json(os.path.join(server_round, 'round-state.json'))
    runtime.require(isinstance(state, dict) and state.get('state') == 'succeeded' and
                    state.get('participant_node_ids') == [1, 2] and
                    state.get('committed_update_node_ids') == [1, 2],
                    'server strict round-state boundary', state)

    sections = [baseline.value(), downlink.value(), runtime.value()]
    for value in sections:
        with open(os.path.join(output, value['section'] + '.json'), 'w', encoding='utf-8') as fh:
            json.dump(value, fh, indent=2, sort_keys=True); fh.write('\n')
    overall = 'PASS' if all(value['status'] == 'PASS' for value in sections) else 'FAIL'
    result = {'schema_version': 1, 'issue': 41, 'status': overall,
              'sections': {value['section']: value['status'] for value in sections}}
    with open(os.path.join(output, 'formal_summary.json'), 'w', encoding='utf-8') as fh:
        json.dump(result, fh, indent=2, sort_keys=True); fh.write('\n')
    with open(os.path.join(output, 'result.md'), 'w', encoding='utf-8') as fh:
        fh.write('# Issue #41 三机真实硬件验收结果\n\n')
        for value in sections:
            fh.write('- %s: **%s**\n' % (value['section'], value['status']))
        fh.write('\n- 总结论: **%s**\n' % overall)
    print(overall)
    return 0 if overall == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
