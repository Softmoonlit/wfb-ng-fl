"""分阶段发布入口；显式冻结 commit 和目标，所有目标必须通过每一阶段。"""
import argparse
import concurrent.futures
import datetime
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import tarfile

TARGETS = {f'fl{i:02d}' for i in range(11)}
NATIVE_SHA = 'ad3340e51eecc6e5691a6edb84f26f3a262b518c3555497df62ea6f7865e1e56'
BASE = pathlib.Path(__file__).resolve().parent
PHASES = ['preflight', 'stage', 'prepare', 'stop', 'install', 'start', 'audit']


def checked(command, **kwargs):
    return subprocess.run(command, check=True, timeout=45, **kwargs)


def valid_commit(value):
    return len(value) == 40 and all(c in '0123456789abcdef' for c in value)


if sys.argv[1:] == ['known-stages']:
    print(' '.join(PHASES))
    sys.exit()

if len(sys.argv) >= 2 and sys.argv[1] == 'init':
    parser = argparse.ArgumentParser(description='仅本地冻结源码与显式验收预期；不执行远端阶段')
    parser.add_argument('commit')
    parser.add_argument('targets', nargs='+')
    parser.add_argument('--expected-grant-ms', type=int, choices=range(1, 121), default=10)
    parser.add_argument('--expected-guard-ms', type=int, choices=range(11), default=10)
    parser.add_argument('--expected-fec-n', type=int, choices=(12, 14), default=14)
    options = parser.parse_args(sys.argv[2:])
    commit, targets = options.commit, options.targets
    if not valid_commit(commit):
        raise SystemExit('commit 必须是 40 位小写十六进制值')
    if not targets or len(set(targets)) != len(targets) or targets[0] != 'fl00':
        raise SystemExit('targets 必须去重且必须包含首位 fl00')
    if set(targets) != TARGETS:
        raise SystemExit('本轮 targets 必须精确为 fl00 fl01 fl02 fl03 fl04 fl05 fl06 fl07 fl08 fl09 fl10')
    checked(['git', 'diff', '--exit-code', '--', 'src'])
    checked(['git', 'diff', '--cached', '--exit-code', '--', 'src'])
    checked(['git', 'diff', '--exit-code', 'd898d21', commit, '--', 'src', 'fec.c', 'fec.h'])
    release_id = 'heartbeat-' + commit[:12] + '-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    evidence = BASE / release_id
    evidence.mkdir()
    archive = evidence / 'source.tar'
    with archive.open('wb') as target:
        checked(['git', 'archive', '--format=tar', commit, 'wfb_ng', 'scripts', 'Makefile',
                 'setup.py', 'version.py', 'docs/README.md'], stdout=target)
    archive_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        assert members and all(not pathlib.Path(m.name).is_absolute() and '..' not in pathlib.Path(m.name).parts
                               for m in members)
        assert all(m.isfile() or m.isdir() for m in members)
    helper_sha = hashlib.sha256((BASE / 'release-online-node.py').read_bytes()).hexdigest()
    state = {'commit': commit, 'release_id': release_id, 'targets': targets,
             'archive_sha': archive_sha, 'native_sha': NATIVE_SHA,
             'helperSHA': helper_sha, 'expected': {'grant_duration_ms': options.expected_grant_ms,
             'guard_interval_ms': options.expected_guard_ms, 'fec_n': options.expected_fec_n},
             'completed': []}
    (evidence / 'state.json').write_text(json.dumps(state, indent=2) + '\n')
    print(evidence)
    sys.exit()

if len(sys.argv) != 3:
    raise SystemExit('用法: python release-online.py <phase> <evidence>')
phase, evidence_arg = sys.argv[1:]
evidence = pathlib.Path(evidence_arg).resolve()
state = json.loads((evidence / 'state.json').read_text())
targets = state['targets']
assert targets and targets[0] == 'fl00' and len(set(targets)) == len(targets)
assert set(targets) == TARGETS and len(targets) == len(TARGETS)
helper = (BASE / 'release-online-node.py').read_text()
assert hashlib.sha256(helper.encode()).hexdigest() == state['helperSHA']
assert phase in PHASES and len(state['completed']) < len(PHASES) and phase == PHASES[len(state['completed'])], state['completed']
expected = state['expected']
assert type(expected['grant_duration_ms']) is int and 1 <= expected['grant_duration_ms'] <= 120
assert type(expected['guard_interval_ms']) is int and 0 <= expected['guard_interval_ms'] <= 10
assert type(expected['fec_n']) is int and expected['fec_n'] in (12, 14)


def on_host(host):
    try:
        if phase == 'stage':
            remote_path = '/tmp/' + state['release_id']
            if host == 'fl00':
                pathlib.Path(remote_path).mkdir()
                shutil.copy2(evidence / 'source.tar', pathlib.Path(remote_path) / 'source.tar')
            else:
                checked(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=6', host, 'mkdir ' + remote_path], capture_output=True)
                checked(['scp', '-q', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=6', str(evidence / 'source.tar'), host + ':' + remote_path + '/source.tar'], capture_output=True)
            result = {'host': host, 'exit': 0}
        else:
            args = [phase, state['commit'], state['release_id'], state['archive_sha'], state['native_sha'],
                    str(expected['grant_duration_ms']), str(expected['guard_interval_ms']), str(expected['fec_n'])]
            command = (['sudo', '-n', '/usr/bin/python3', '-', *args] if host == 'fl00' else
                       ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=6', host,
                        'sudo -n /usr/bin/python3 - ' + ' '.join(args)])
            proc = subprocess.run(command, input=helper, text=True, capture_output=True, timeout=45)
            result = {'host': host, 'exit': proc.returncode, 'stdout': proc.stdout, 'stderr': proc.stderr}
    except Exception as exc:
        result = {'host': host, 'exit': -1, 'error': repr(exc)}
    (evidence / (phase + '-' + host + '.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return result


with concurrent.futures.ThreadPoolExecutor(max_workers=len(targets)) as pool:
    results = list(pool.map(on_host, targets))
for result in results:
    print(result['host'], phase, result['exit'], result.get('stderr', '')[-1200:], result.get('error', ''))
if any(result['exit'] != 0 for result in results):
    raise SystemExit('阶段失败：已停止推进，不绕过目标')
state['completed'].append(phase)
(evidence / 'state.json').write_text(json.dumps(state, indent=2) + '\n')
