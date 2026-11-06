"""固定六在线目标的分阶段发布入口；每个阶段全员通过才允许下一阶段。"""
import concurrent.futures
import datetime
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys

HOSTS = ['fl00', 'fl01', 'fl02', 'fl04', 'fl05', 'fl06']
NATIVE_SHA = 'ad3340e51eecc6e5691a6edb84f26f3a262b518c3555497df62ea6f7865e1e56'
BASE = pathlib.Path(__file__).resolve().parent


def checked(command, **kwargs):
    return subprocess.run(command, check=True, timeout=45, **kwargs)


if sys.argv[1] == 'init':
    commit = subprocess.check_output(['git', 'rev-parse', 'c7d99b6'], text=True).strip()
    assert not subprocess.check_output(['git', 'diff', 'd898d21', commit, '--', 'src', 'fec.c', 'fec.h'])
    release_id = 'heartbeat-' + commit[:12] + '-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    evidence = BASE / release_id
    evidence.mkdir()
    archive = evidence / 'source.tar'
    with archive.open('wb') as target:
        checked(['git', 'archive', '--format=tar', commit, 'wfb_ng', 'scripts', 'Makefile',
                 'setup.py', 'version.py', 'docs/README.md'], stdout=target)
    state = {'commit': commit, 'release_id': release_id, 'hosts': HOSTS,
             'archive_sha': hashlib.sha256(archive.read_bytes()).hexdigest(),
             'native_sha': NATIVE_SHA,
             'helper_sha': hashlib.sha256((BASE / 'release-online-node.py').read_bytes()).hexdigest(),
             'completed': []}
    (evidence / 'state.json').write_text(json.dumps(state, indent=2) + '\n')
    print(evidence)
    sys.exit()

phase, evidence_arg = sys.argv[1:]
evidence = pathlib.Path(evidence_arg).resolve()
state = json.loads((evidence / 'state.json').read_text())
assert state['hosts'] == HOSTS
helper = (BASE / 'release-online-node.py').read_text()
assert hashlib.sha256(helper.encode()).hexdigest() == state['helper_sha']
phases = ['preflight', 'stage', 'prepare', 'stop', 'install', 'start', 'audit']
assert phase == phases[len(state['completed'])], state['completed']


def on_host(host):
    try:
        if phase == 'stage':
            remote_path = '/tmp/' + state['release_id']
            if host == 'fl00':
                pathlib.Path(remote_path).mkdir()
                shutil.copy2(evidence / 'source.tar', pathlib.Path(remote_path) / 'source.tar')
            else:
                checked(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=6', host,
                         'mkdir ' + remote_path], capture_output=True)
                checked(['scp', '-q', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=6',
                         str(evidence / 'source.tar'), host + ':' + remote_path + '/source.tar'], capture_output=True)
            result = {'host': host, 'exit': 0}
        else:
            args = [phase, state['commit'], state['release_id'], state['archive_sha'], state['native_sha']]
            command = (['sudo', '-n', '/usr/bin/python3', '-', *args] if host == 'fl00' else
                       ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=6', host,
                        'sudo -n /usr/bin/python3 - ' + ' '.join(args)])
            proc = subprocess.run(command, input=helper, text=True, capture_output=True, timeout=65)
            result = {'host': host, 'exit': proc.returncode, 'stdout': proc.stdout, 'stderr': proc.stderr}
    except Exception as exc:
        result = {'host': host, 'exit': -1, 'error': repr(exc)}
    (evidence / (phase + '-' + host + '.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return result


with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
    results = list(pool.map(on_host, HOSTS))
for result in results:
    print(result['host'], phase, result['exit'], result.get('stderr', '')[-1200:], result.get('error', ''))
if any(result['exit'] != 0 for result in results):
    raise SystemExit('阶段失败：已停止推进，不绕过目标')
state['completed'].append(phase)
(evidence / 'state.json').write_text(json.dumps(state, indent=2) + '\n')
