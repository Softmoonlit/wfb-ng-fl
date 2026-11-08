"""只读核验冻结工具和发布证据，生成本轮结论。"""
import ast
import hashlib
import json
from pathlib import Path

base = Path(__file__).resolve().parent
state = json.loads((base / 'state.json').read_text())
targets = ['fl00', 'fl01', 'fl02', 'fl04', 'fl05', 'fl06', 'fl07', 'fl08', 'fl09', 'fl10']
phases = ['preflight', 'stage', 'prepare', 'stop', 'install', 'start', 'audit']
assert state['targets'] == targets and state['completed'] == phases
assert hashlib.sha256((base / 'release-online-node.py').read_bytes()).hexdigest() == state['helperSHA']
assert hashlib.sha256((base / 'source.tar').read_bytes()).hexdigest() == state['archive_sha']
entry = ast.parse((base / 'release-online.py').read_text())
constants = {n.targets[0].id: ast.literal_eval(n.value) for n in entry.body
             if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
             and n.targets[0].id in {'TARGETS', 'PHASES'}}
assert constants['TARGETS'] == set(targets) and constants['PHASES'] == phases
rows = []
for host in targets:
    for phase in phases:
        assert json.loads((base / f'{phase}-{host}.json').read_text())['exit'] == 0
    def load(name):
        result = json.loads((base / f'{name}-{host}.json').read_text())
        assert result['exit'] == 0
        return json.loads(result['stdout'])
    pre, prepared, installed, audit, post = [load(n) for n in ['preflight', 'prepare', 'install', 'audit', 'postcheck']]
    assert pre['iface'] == prepared['iface']
    assert pre['nvpmodel'] == post['power']['stdout'] == 'NV Power Mode: 15W\n0\n'
    assert post['native_sha256'] == audit['native_sha'] == state['native_sha']
    assert audit['loaded']['commit'] == state['commit'] and audit['loaded']['pid'] == audit['pid']
    assert all(v['sha256'] == installed['files'][Path(v['path']).name] for v in audit['loaded']['modules'].values())
    assert set(prepared['runtime_files']) | {'build_identity.json'} == set(installed['files'])
    assert prepared['dependencies']['daemon_import_without_torch']
    assert audit['radio_runtime']['short_gi'] == 0 and audit['radio_runtime']['bandwidth'] == 40
    assert audit['radio_runtime']['mcs_index'] == (3 if host == 'fl00' else 4)
    assert audit['native_args'][audit['native_args'].index('--air-interface') + 1] == pre['iface']
    for sample in post['samples']:
        service = sample['service']['stdout'].splitlines()
        assert 'ActiveState=active' in service and 'SubState=running' in service
        assert f'MainPID={audit["pid"]}' in service
        radios = [r for r in sample['radios'] if r['interface'] == pre['iface']]
        assert len(radios) == 1 and radios[0]['exit'] == 0
        assert all(text in radios[0]['stdout'] for text in ['channel 149 ', 'width: 40 MHz', 'txpower -18.00 dBm'])
        if host == 'fl00':
            status = sample['status']
            assert status['active_job'] is None and status['server_state'] == 'IDLE'
            assert all(status['nodes'][h[2:].lstrip('0')]['readiness'] == 'READY' for h in targets[1:])
            assert 'diagnose-grant' not in sample['service']['stdout']
    rows.append(dict(host=host, interface=pre['iface'], pid=audit['pid'], native_pid=audit['native_pid'],
                     allRF=True, active=True, power='15W', dependencies=prepared['dependencies'],
                     ack_recovery_warnings=sum('累计缺失' in line and f'python3[{audit["pid"]}]' in line
                                               for line in post['journal']['stdout'].splitlines())))
server = json.loads(json.loads((base / 'audit-fl00.json').read_text())['stdout'])['native_args']
assert server[0] == '/usr/bin/wfb_v6_uplink'
assert server[server.index('--grant-duration-ms') + 1] == server[server.index('--guard-interval-ms') + 1] == '10'
summary = dict(commit=state['commit'], release_id=state['release_id'], targets=targets,
               batches=1, allRF=True, all_active=True, all_nine_ready_at_each_postcheck=True,
               observation_utc=[s['utc'] for s in json.loads(json.loads((base/'postcheck-fl00.json').read_text())['stdout'])['samples']],
               nodes=rows, limitation='fl04启动后曾缺5次ACK并寻频；后续三次快照READY，不能据此证明长期稳定；未执行模型或通信测试')
(base / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(summary, ensure_ascii=False, indent=2))
