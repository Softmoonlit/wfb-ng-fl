#!/usr/bin/env python3
"""四板显式发布：仅在 fl00 使用明确已提交的完整 SHA 执行。

用法：python3 release-four.py FULL_COMMIT。
所有联网和部署动作仅在 main() 中发生；失败停止推进，不自动回滚。
"""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = Path(__file__).resolve().parent
NODES = {'fl00': None, 'fl01': '192.168.108.201',
         'fl02': '192.168.108.202', 'fl04': '192.168.108.204'}
WHITELIST_IDS = tuple(range(1, 11))
SSH = ['-o', 'BatchMode=yes', '-o', 'ConnectTimeout=7']


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    if len(sys.argv) != 2 or not re.fullmatch('[0-9a-f]{40}', sys.argv[1]):
        raise SystemExit('usage: python3 release-four.py FULL_40_HEX_COMMITTED_SHA')
    commit = sys.argv[1]
    if os.uname().nodename != 'fl00':
        raise SystemExit('must run locally on fl00')
    repo = Path(subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], text=True).strip())
    resolved = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', '--verify', commit + '^{commit}'], text=True).strip()
    if resolved != commit:
        raise SystemExit('not an explicit committed object')
    # 管理脚本与拓扑也必须已经进入指定提交，禁止工作区内容混入发布。
    frozen = {}
    for path in (Path(__file__).resolve(), HERE / 'release-node.py', repo / 'cluster_nodes.conf'):
        relative = str(path.relative_to(repo))
        content = subprocess.check_output(['git', '-C', str(repo), 'show', commit + ':' + relative], timeout=30)
        if path.read_bytes() != content:
            raise SystemExit('working file differs from explicit commit: ' + relative)
        frozen[relative] = content
    helper_relative = str((HERE / 'release-node.py').relative_to(repo))
    # 只按数据解析拓扑，绝不执行其中的 shell 语句；集合必须严格匹配。
    topology = frozen['cluster_nodes.conf'].decode()
    blocks = re.findall(r'^CLIENTS=\(\s*\n(.*?)^\)', topology, re.M | re.S)
    if len(blocks) != 1:
        raise SystemExit('need exactly one CLIENTS topology block')
    rows = shlex.split(blocks[0], comments=True)
    expected = ['client1 192.168.108.201 fl 1 10.80.0.11',
                'client2 192.168.108.202 fl 2 10.80.0.12',
                'client4 192.168.108.204 fl 4 10.80.0.14']
    if sorted(' '.join(r.split()) for r in rows) != expected:
        raise SystemExit('cluster_nodes.conf topology mismatch')
    participant_ids = tuple(sorted(int(row.split()[3]) for row in rows))
    if participant_ids != (1, 2, 4):
        raise SystemExit('participant topology must be exactly nodes 1/2/4')
    for key, value in {'SERVER_HOST': 'fl00', 'SERVER_USER': 'fl', 'SERVER_NODE_ID': '255',
                       'SERVER_TUN_IP': '10.80.0.1/24', 'WIRELESS_CHANNEL': '149',
                       'WIRELESS_CHANNEL_WIDTH': 'HT40+', 'WIRELESS_TXPOWER_DBM': '14',
                       'DOWNLINK_MCS': '3', 'UPLINK_MCS': '4', 'UFTP_RATE_KBPS': '15000'}.items():
        if re.findall(r'^' + key + r'=([^\n#]+)', topology, re.M) != [value]:
            raise SystemExit('topology/radio mismatch: ' + key)
    run_id = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    evidence = HERE / ('release-' + commit[:12] + '-' + run_id)
    evidence.mkdir(mode=0o700)
    manifest = {'commit': commit, 'run_id': run_id,
                'topology_sha256': hashlib.sha256(frozen['cluster_nodes.conf']).hexdigest(),
                'helper_sha256': hashlib.sha256(frozen[helper_relative]).hexdigest(),
                'frozen_management_sha256': {name: hashlib.sha256(content).hexdigest()
                                             for name, content in frozen.items()},
                'whitelist_ids': list(WHITELIST_IDS), 'participant_ids': list(participant_ids),
                'node_identity_ids': None, 'active_job_targets': None,
                'events': [], 'state': 'preflight'}

    def save():
        temp = evidence / 'manifest.tmp'
        temp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
        os.replace(temp, evidence / 'manifest.json')

    def command(node, args, label, timeout=120):
        argv = args if node == 'fl00' else ['ssh', *SSH, 'fl@' + NODES[node], shlex.join(args)]
        event = {'node': node, 'phase': label, 'argv': argv, 'state': 'running',
                 'utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}
        manifest['events'].append(event)
        save()
        print(node, label, flush=True)
        with (evidence / (node + '-' + label + '.stdout')).open('wb') as out, (evidence / (node + '-' + label + '.stderr')).open('wb') as err:
            try:
                result = subprocess.run(argv, stdout=out, stderr=err, timeout=timeout)
                event['rc'] = result.returncode
                result.check_returncode()
            except Exception as exc:
                event.update(state='failed', error=repr(exc))
                save()
                raise
        event['state'] = 'passed'
        save()
        return (evidence / (node + '-' + label + '.stdout')).read_text()

    def status(label):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open('http://127.0.0.1:9090/api/v1/status', timeout=8) as response:
            state = json.load(response)
        (evidence / (label + '.json')).write_text(json.dumps(state, indent=2) + '\n')
        if 'active_job' not in state:
            raise RuntimeError('status missing active_job')
        active_job = state['active_job']
        active_targets = None
        if active_job is not None:
            if not isinstance(active_job, dict) or not isinstance(active_job.get('target_nodes'), list):
                raise RuntimeError('active job must expose target_nodes')
            active_targets = active_job['target_nodes']
            manifest['active_job_targets'] = active_targets
            save()
            if (any(type(node_id) is not int for node_id in active_targets)
                    or len(set(active_targets)) != len(active_targets)
                    or sorted(active_targets) != list(participant_ids)):
                raise RuntimeError('active job targets differ from frozen participants')
        else:
            manifest['active_job_targets'] = None
            save()
        if state.get('server_state') != 'IDLE' or active_job is not None:
            raise RuntimeError('server must be IDLE with active_job=null')
        return state

    inbox = '/tmp/wfb-release-' + commit + '-' + run_id
    root = '/srv/wfb-ng-fl/release-' + commit + '-' + run_id

    def phase(node, name, timeout=120):
        return json.loads(command(node, ['sudo', '-n', '/usr/bin/python3', '-B',
                                         inbox + '/release-node.py', name, commit, node,
                                         root, inbox, manifest.get('archive_sha256', '-'),
                                         manifest.get('native_sha256', '-')], name, timeout))

    try:
        save()
        status('server-before')  # 不以 fl04 当前就绪状态作为发布前置条件。
        with tempfile.TemporaryDirectory(prefix='wfb-release-') as temp:
            archive = Path(temp) / 'source.tar'
            subprocess.run(['git', '-C', str(repo), 'archive', '--format=tar',
                            '--output=' + str(archive), commit], check=True, timeout=60)
            helper = Path(temp) / 'release-node.py'
            helper.write_bytes(frozen[helper_relative])
            manifest['archive_sha256'] = sha(archive)
            for node in NODES:
                command(node, ['mkdir', '-m', '700', inbox], 'inbox')
                if node == 'fl00':
                    command(node, ['cp', '--', str(archive), str(helper), inbox + '/'], 'transfer')
                else:
                    command('fl00', ['scp', *SSH, '--', str(archive), str(helper),
                                      'fl@' + NODES[node] + ':' + inbox + '/'], 'transfer-' + node)
            audits = {node: phase(node, 'preflight') for node in NODES}
            identity_ids = sorted(value['node_identity']['node_id'] for node, value in audits.items()
                                  if node != 'fl00')
            if identity_ids != list(participant_ids):
                raise RuntimeError('node identities differ from frozen participants; no service stopped')
            manifest['node_identity_ids'] = identity_ids
            manifest['abi'] = {node: value['abi'] for node, value in audits.items()}
            if len({json.dumps(v['abi']['fingerprint'], sort_keys=True) for v in audits.values()}) != 1:
                raise RuntimeError('cross-board ABI mismatch; no service stopped')
            built = phase('fl00', 'build', 600)
            manifest['native_sha256'] = built['native_sha256']
            save()
            # root 私有的 source 保持 0700；只把已核验产物降权复制到本次 inbox 后分发。
            canonical_binary = inbox + '/canonical-wfb_v6_uplink'
            command('fl00', ['sudo', '-n', 'install', '-m', '0644',
                             root + '/source/wfb_v6_uplink', canonical_binary], 'canonical-binary')
            canonical_sha = command('fl00', ['sha256sum', '--', canonical_binary],
                                    'canonical-binary-sha').split()[0]
            if canonical_sha != manifest['native_sha256']:
                raise RuntimeError('canonical binary hash mismatch; no service stopped')
            # 仅分发 fl00 冻结源码构建的同一二进制，其他板禁止重新编译。
            for node in NODES:
                if node == 'fl00':
                    continue
                command('fl00', ['scp', *SSH, '--', canonical_binary,
                                  'fl@' + NODES[node] + ':' + inbox + '/wfb_v6_uplink'], 'binary-' + node)
            staged = {node: phase(node, 'stage') for node in NODES}
            dependencies = {node: result['dependency_sha256'] for node, result in staged.items()}
            manifest['staged_dependency_sha256'] = dependencies
            save()
            if len({json.dumps(value, sort_keys=True) for value in dependencies.values()}) != 1:
                raise RuntimeError('new binary cross-board dependency ABI mismatch; no service stopped')
            status('server-before-stop')
            # 四端先全部停止；确认全部收尾后才允许任一端安装。
            for name in ['stop', 'stopped', 'install', 'verify-install', 'start', 'verify-runtime']:
                manifest['state'] = name
                save()
                errors = []
                for node in NODES:
                    try:
                        phase(node, name, 180 if name == 'install' else 120)
                    except Exception as exc:
                        errors.append(node + ': ' + repr(exc))
                        # 停服、收尾和只读审计失败时，仍完成其余板的同阶段取证。
                        if name not in ('stop', 'stopped', 'verify-install', 'verify-runtime'):
                            break
                if errors:
                    raise RuntimeError('; '.join(errors))
            # 只观测真实新鲜心跳，不生成 READY，也不修改注册表。
            for attempt in range(20):
                after = status('server-after-' + str(attempt + 1))
                nodes = after.get('nodes', {})
                if set(nodes) != {str(n) for n in WHITELIST_IDS}:
                    raise RuntimeError('runtime status must expose whitelist slots 1..10')
                ready = all(nodes[str(n)].get('readiness') == 'READY'
                            and nodes[str(n)].get('current_channel') == 149
                            and isinstance(nodes[str(n)].get('last_heartbeat_ago_seconds'), (float, int))
                            and nodes[str(n)]['last_heartbeat_ago_seconds'] <= 6
                            for n in participant_ids)
                if ready:
                    break
                if attempt == 19:
                    raise RuntimeError('fresh READY heartbeats on channel 149 not observed for nodes 1/2/4')
                time.sleep(2)
            manifest['state'] = 'verified'
            save()
    except Exception as exc:
        manifest.update(state='failed', error=repr(exc),
                        failure_policy='no rollback, no fallback, no registry changes; inspect logs before manual recovery')
        save()
        print('FAILED; evidence: ' + str(evidence), file=sys.stderr)
        raise
    print('VERIFIED; evidence: ' + str(evidence))


if __name__ == '__main__':
    main()
