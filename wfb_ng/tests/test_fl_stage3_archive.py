"""Hardware-free tests of the Stage 3 archive contract (real Runtime file shapes)."""
import json
import os
import shutil
import uuid

import pytest

from tests.fl_runtime.stage3_archive import (
    FIXED_CONFIG, init_envelope, validate_archive, validate_client_evidence,
)
from wfb_ng.fl.artifacts import file_sha256
from wfb_ng.fl.errors import FLRuntimeError
from wfb_ng.fl.issue41_fixtures import generate_model_fixture, generate_client_fixture

COMMIT = 'a' * 40


def put(root, path, value):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value) + '\n')


def bind(**fields):
    return dict(run_id='run_test', job_id='job_test', **fields)


@pytest.fixture(scope='module')
def payloads(tmp_path_factory):
    root = tmp_path_factory.mktemp('stage3-payloads')
    generate_model_fixture(str(root / 'model'), size_bytes=40 * 1024 * 1024)
    for n in (1, 2):
        generate_client_fixture(str(root / str(n)), n, size_bytes=40 * 1024 * 1024)
    return root


def role(n):
    return dict(schema_version=1, role='client' if n else 'server', node_id=n,
                channel=157, channel_width='HT40+', radio_txpower_dbm=12,
                io_timeout_seconds=120, live_observation=True,
                uftp_rate_kbps=15000, max_update_size_bytes=40 * 1024 * 1024,
                link_args=['--radio-mcs-index', str(6 if n else 3),
                           '--fec-k', '8', '--fec-n', '14', '--radio-bandwidth', '40',
                           '--radio-short-gi'])


def evidence_manifest(root, n):
    base = root / 'clients' / str(n)
    files = {}
    for p in base.rglob('*'):
        if p.is_file() and p.name != 'evidence_manifest.json':
            files[str(p.relative_to(base))] = dict(size_bytes=p.stat().st_size, sha256=file_sha256(p))
    put(base, 'evidence_manifest.json', bind(schema_version=1, node_id=n,
        runtime_commit=COMMIT, build_identity_sha256=files['build_identity.json']['sha256'],
        lifecycle_outcome='succeeded', returncode=0, files=files))


@pytest.fixture
def archive(tmp_path, payloads):
    root = tmp_path / 'archive'
    init_envelope(root, run_id='run_test', job_id='job_test', commit=COMMIT)
    nodes = {str(n): dict(commit=COMMIT, clean=True, interface='wlxabc',
        driver='rtl88xxau_wfb', usb_driver='xhci_hcd', identity_valid=True,
        ports_clear=True, resources_clear=True, disk_ok=True, payload_matcher=True) for n in (0, 1, 2)}
    put(root, 'preflight.json', bind(nodes=nodes))
    package = root / 'package.deb'
    package.write_bytes(b'package fixture')
    digest = file_sha256(package)
    names = ['/usr/bin/wfb-fl-server-daemon', '/usr/bin/wfb-fl-client-daemon',
             '/lib/systemd/system/wfb-fl-server-daemon.service',
             '/lib/systemd/system/wfb-fl-client-daemon.service', '/usr/bin/wfb_v6_uplink',
             'wfb_ng/fl/build_identity.json']
    files = {p: dict(package_sha256=digest, installed_sha256=digest, expected_sha256=digest) for p in names}
    put(root, 'installation.json', bind(package=dict(path='package.deb', sha256=digest,
        commit=COMMIT, build_clean=True, build_isolated=True, package_count=1),
        nodes={str(n): dict(package_sha256=digest, commit=COMMIT, version='1.0', files=files) for n in (0, 1, 2)}))
    request=bind(**{k: FIXED_CONFIG[k] for k in (
        'mode','rounds','target_nodes','io_timeout_seconds','round_timeout_seconds','live_observation')})
    put(root, 'job/request.json', request)
    put(root, 'server/job_config.json', dict(request,model_size_bytes=40*1024*1024))
    put(root, 'job/window.json', bind(services_started_at=1, nodes_ready_at=2,
        accepted_at=3, terminal_at=100, ready_nodes=[1, 2]))
    put(root, 'server/server_role.json', {k:v for k,v in role(0).items() if k not in ('channel','channel_width','radio_txpower_dbm','link_args')})
    put(root, 'server/link.json', dict(argv=role(0)['link_args']))
    put(root, 'server/radio.json', dict(channel=157,channel_width='HT40+',radio_txpower_dbm=12,source='server/iw.txt'))
    (root/'server/iw.txt').write_text('channel 157 (5785 MHz), width: 40 MHz, center1: 5795 MHz\n txpower -12.00 dBm\n')
    (root/'server/link.log').write_text('v6_config radio_bandwidth=40 radio_mcs_index=3 radio_short_gi=1 fec_k=8 fec_n=14 grant_duration_ms=120 guard_interval_ms=10\n')
    rounds = []
    obs = {0: [], 1: [], 2: []}
    client_rounds = {1: [], 2: []}
    model_sha = file_sha256(payloads / 'model')
    for index in (1, 2):
        rid = str(uuid.uuid4())
        rounds.append(dict(round_index=index, input_model_sha256=model_sha,
            output_model_sha256=model_sha, committed_nodes=[1, 2], dropped_out_nodes=[], duration_seconds=40))
        model = dict(schema_version=1, artifact_type='model', round_id=rid,
            size_bytes=40*1024*1024, sha256=model_sha, participant_node_ids=[1, 2])
        put(root, f'server/rounds/{rid}/model.manifest.json', model)
        target = root / f'server/rounds/{rid}/model.bin'
        os.link(payloads / 'model', target)
        output = root / f'server/models/{index}.bin'
        output.parent.mkdir(exist_ok=True)
        os.link(payloads / 'model', output)
        put(root, f'server/rounds/{rid}/round-state.json', dict(schema_version=1,
            role='server', round_id=rid, state='succeeded', participant_node_ids=[1, 2], committed_update_node_ids=[1, 2]))
        for n in (1, 2):
            sha = file_sha256(payloads / str(n))
            update = dict(schema_version=1, artifact_type='update', round_id=rid,
                          node_id=n, size_bytes=40*1024*1024, sha256=sha)
            put(root, f'server/rounds/{rid}/updates/{n}/update.manifest.json', update)
            os.link(payloads / str(n), root / f'server/rounds/{rid}/updates/{n}/update.bin')
            put(root, f'clients/{n}/rounds/{rid}/model.manifest.json', model)
            put(root, f'clients/{n}/rounds/{rid}/update.manifest.json', update)
            put(root, f'clients/{n}/rounds/{rid}/round-state.json', dict(schema_version=1, role='client', round_id=rid, state='succeeded'))
            client_rounds[n].append(dict(round_index=index, round_id=rid, node_id=n,
                model_size_bytes=40*1024*1024, model_sha256=model_sha,
                update_size_bytes=40*1024*1024, update_sha256=sha))
            obs[0].append(dict(event='upload_committed', role='server', node_id=n,
                round=rid, sha256=sha, size_bytes=40*1024*1024, transport_outcome='committed'))
            obs[n].append(dict(event='upload_phase', role='client', node_id=n,
                round=rid, sha256=sha, size_bytes=40*1024*1024, transport_outcome='created', phase='final_response'))
    for n in (0, 1, 2):
        base = 'server' if n == 0 else f'clients/{n}'
        put(root, f'{base}/current-round.json', dict(schema_version=1, role='server' if n == 0 else 'client', round_id=rid))
        (root / base / 'observation.jsonl').write_text(''.join('WFB_FL_EVENT '+json.dumps(e)+'\n' for e in obs[n]))
        if n:
            put(root, f'{base}/client_role.json', role(n))
            put(root, f'{base}/algorithm_config.json', dict(rounds=2, node_id=n, required_artifact_size_bytes=40*1024*1024))
            put(root, f'{base}/build_identity.json', dict(schema_version=1, commit=COMMIT))
            put(root, f'{base}/issue41-client-result.json', dict(schema_version=1,
                role='client', node_id=n, conclusion='succeeded', rounds=client_rounds[n],
                update_template_sha256=client_rounds[n][0]['update_sha256']))
            for log in ('role_service.log', 'uftpd.log'):
                (root / base / log).write_text('v6_config radio_bandwidth=40 radio_mcs_index=6 radio_short_gi=1 fec_k=8 fec_n=14 grant_duration_ms=120 guard_interval_ms=10\n' if log=='role_service.log' else 'successful runtime\n')
            node=root/f'nodes/client{n}';node.mkdir(parents=True)
            (node/'idle-link.log').write_text((root/base/'role_service.log').read_text())
            (node/'iw.txt').write_text((root/'server/iw.txt').read_text())
            put(root,f'nodes/client{n}/radio.json',dict(channel=157,channel_width='HT40+',radio_txpower_dbm=12,source=f'nodes/client{n}/iw.txt'))
            evidence_manifest(root, n)
    put(root, 'job/coordinator.json', bind(schema_version=1, mode='sync', status='succeeded',
        rounds_total=2, rounds_completed=2, target_nodes=[1, 2], rounds=rounds,
        final_model_sha256=model_sha, final_model_size_bytes=40*1024*1024))
    put(root, 'radio/result.json', dict(session_id='radio_test', status='rolled_back',
        target_channel=149, failed_phase='commit', unresponsive_nodes=[2], effective_config={'channel':157}))
    put(root, 'radio/fault.json', bind(node_id=2, drop_types=['NEW_CHANNEL_PING','RADIO_SWITCH_FINALIZED'], installed_at=101, removed_at=140, present=False))
    put(root, 'fault-state.json', dict(installed=False,run_id='run_test',checked_at=140,rules='-P INPUT ACCEPT\n'))
    events = []
    for event, n, t, ch in [('PREPARE',0,102,157),('LEASE_ARM',2,103,157),('COMMIT',2,105,149),('SERVER_ROLLBACK',0,112,157),('LEASE_TIMEOUT',2,118,157),('IDLE_READY',1,120,157),('IDLE_READY',2,120,157)]:
        if event=='IDLE_READY':
            line=json.dumps(dict(observed_at=t,status=dict(server_state='IDLE',nodes={str(n):dict(reported_state='IDLE',readiness='READY',current_channel=157)})))
        else:
            message={'PREPARE':'射频重配 PREPARE', 'LEASE_ARM':'节点 2 LEASE_ARM',
                'COMMIT':'节点 2 COMMIT', 'SERVER_ROLLBACK':'射频重配回退至 Channel 157',
                'LEASE_TIMEOUT':'节点 2: 射频租约耗尽 回退至 Channel 157'}[event]
            line = f'{t:.6f} vm0 daemon: {message} session=radio_test channel={ch}' + (' lease_seconds=15.000' if event=='LEASE_ARM' else '')
        events.append(dict(event=event,node_id=n,timestamp=t,channel=ch,session_id='radio_test',source='radio/journal.log',line=line,lease_seconds=15))
    (root / 'radio/journal.log').write_text('\n'.join(e['line'] for e in events))
    (root / 'radio/events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
    for mode in ('idle','stopped'):
        nodes={}
        for n in (0,1,2):
            idle=mode=='idle'
            processes=[dict(pid=10,comm='python3',args=['python3','-m','wfb_ng.fl.server_daemon' if n==0 else 'wfb_ng.fl.client_daemon']),dict(pid=11,comm='wfb_v6_uplink',args=['/usr/bin/wfb_v6_uplink'])] if idle else []
            tuns=[f'fl-c{n}' if n else 'fl-s'] if idle else []
            nodes[str(n)]=dict(processes=dict(daemon=int(idle),role_service=0,wfb_v6_uplink=int(idle),uftp=0),
                tuns=tuns,state='IDLE',phase='READY',channel=157,
                raw=dict(processes=processes,tuns=tuns,clean=not idle,is_inactive=not idle,
                    unit_status='active' if idle else 'inactive',cgroup_clean=True,
                    tun_exists=idle,cgroup_procs=[],orphan_processes=[],tasks_current=0,
                    raw_evidence=dict(is_active='active' if idle else 'inactive',
                        show_props=dict(ActiveState='active' if idle else 'inactive',MainPID='10' if idle else '0',TasksCurrent='2' if idle else '0'),tun_out='mtu 1500' if idle else 'Device does not exist')))
        put(root,f'resources/{mode}.json',bind(nodes=nodes))
    topology={}
    stopped=json.loads((root/'resources/stopped.json').read_text())['nodes']
    for n in (0,1,2):
        role_name='server' if n==0 else f'client{n}'
        topology[role_name]=dict(commit=COMMIT,workspace_clean=True,
            wireless=dict(interface='wlxabc',driver='rtl88xxau_wfb',usb_controller='xhci_hcd',
                usb_speed='480',mac='5c:ff:ff:af:6d:8c' if n==0 else f'00:11:22:33:44:0{n}'),
            identity=None if n==0 else dict(node_id=n,tun_ip=f'10.80.0.{10+n}'),
            resources=stopped[str(n)]['raw'])
    put(root,'topology.json',topology)
    from tests.fl_runtime.stage3_archive import STAGES
    times=[(0,.8),(.8,.9),(1,2),(3,100),(101,140),(141,142),(143,144),(145,146)]
    for stage,(start,end) in zip(STAGES[:-1],times):
        put(root,f'stages/{stage}.json',dict(run_id='run_test',status='passed',started_at=start,ended_at=end))
    env=json.loads((root/'envelope.json').read_text());env['completed_stages']=list(STAGES[:-1]);put(root,'envelope.json',env)
    commands=[]
    for n in (0,1,2):
        unit='wfb-fl-server-daemon.service' if n==0 else 'wfb-fl-client-daemon.service'
        stages={'preflight':['git rev-parse HEAD','git status --porcelain'],
            'install':['sudo -n env DEBIAN_FRONTEND=noninteractive dpkg --force-confold --force-confdef -i package.deb'],
            'start-services':['sudo -n systemctl start '+unit,'sudo -n systemctl is-active '+unit],
            'collect':['sudo -n journalctl -u '+unit,'ip -j link show'],
            'stop-services':['sudo -n systemctl stop '+unit,'sudo systemctl show '+unit,'ip -j link show']}
        for stage,cmds in stages.items():
            start,end=times[list(STAGES).index(stage)]
            for command in cmds:
                commands.append(bind(stage=stage,target='vm'+str(n),transport='local' if n==0 else 'ssh',command=command,category='inspection',started_at=start,ended_at=end,returncode=0))
    (root/'orchestration.jsonl').write_text(''.join(json.dumps(c)+'\n' for c in commands))
    from tests.fl_runtime.stage3_archive import seal_archive
    install=json.loads((root/'installation.json').read_text())
    identity=file_sha256(root/'clients/1/build_identity.json')
    for node in install['nodes'].values():
        node['files']['wfb_ng/fl/build_identity.json'].update(installed_sha256=identity,expected_sha256=identity)
    put(root,'installation.json',install)
    postflight={}
    for n in (0,1,2):
        role_name='server' if n==0 else f'client{n}'
        daemon='server' if n==0 else 'client'
        path=f'/lib/systemd/system/wfb-fl-{daemon}-daemon.service'
        unit=dict(package_path=path,package_sha256=digest,sha256=digest,
            properties=dict(FragmentPath=path,KillMode='control-group',DropInPaths='',
                ExecStart='{ path=/usr/bin/wfb-fl-'+daemon+'-daemon ; argv[]=/usr/bin/wfb-fl-'+daemon+'-daemon ; }'))
        put(root,f'nodes/{role_name}/unit.json',unit)
        postflight[str(n)]=dict(commit=COMMIT,clean=True,unit=unit)
    put(root,'postflight.json',bind(nodes=postflight))
    status=dict(server_state='IDLE',active_job=None,link_process=dict(running=True),
        radio={k:FIXED_CONFIG[k] for k in ('channel','radio_txpower_dbm','downlink_mcs','uplink_mcs','uftp_rate_kbps')},
        nodes={str(n):dict(reported_state='IDLE',readiness='READY',current_channel=157) for n in (1,2)})
    put(root,'job-idle.json',status)
    put(root,'collected-status.json',status)
    (root/'nodes/server/daemon.log').write_text('99.000 vm0 daemon: 作业终态广播成功 type=JOB_COMPLETED job_id=job_test\n99.100 vm0 daemon: 作业 job_test 终态收口完成 (outcome=completed, reason=None)\n')
    for n in (1,2):
        (root/f'nodes/client{n}/daemon.log').write_text(f'99.200 vm{n} daemon: 回收作业 job_test 资源 (exit_code=0)\n')
    seal_archive(root)
    return root


def test_complete_archive(archive):
    assert validate_archive(archive)['status'] == 'passed'


def test_postflight_is_required_even_with_valid_seal(archive):
    (archive / 'postflight.json').unlink(missing_ok=True)
    reseal(archive)
    result = validate_archive(archive)
    assert result['status'] == 'failed'
    assert any(e['category'] == 'installation' for e in result['errors'])


def test_raw_preflight_topology_is_required(archive):
    (archive / 'topology.json').unlink(missing_ok=True)
    reseal(archive)
    result = validate_archive(archive)
    assert result['status'] == 'failed'
    assert any(e['category'] == 'preflight' for e in result['errors'])


def test_job_terminal_evidence_is_required(archive):
    (archive / 'job-idle.json').unlink(missing_ok=True)
    reseal(archive)
    result = validate_archive(archive)
    assert result['status'] == 'failed'
    assert any(e['category'] == 'runtime_evidence' for e in result['errors'])


@pytest.mark.parametrize('kind', [
    'postflight_commit','postflight_dirty','unit_digest','unit_package','unit_exec',
    'unit_fragment','unit_dropin','unit_killmode','unit_changed',
    'topology_missing_node','topology_identity','topology_usb','topology_mac','topology_resources',
    'terminal_active_job','terminal_active_job_missing','terminal_client_not_ready',
    'terminal_wrong_job','terminal_missing_broadcast','terminal_exit_code_mismatch',
])
def test_resealed_invalid_lifecycle_evidence_is_rejected(archive,kind):
    if kind.startswith('postflight') or kind.startswith('unit'):
        path='postflight.json'
        report=json.loads((archive/path).read_text())
        if kind=='postflight_commit': report['nodes']['1']['commit']='b'*40
        elif kind=='postflight_dirty': report['nodes']['2']['clean']=False
        elif kind=='unit_changed': report['nodes']['0']['unit']['sha256']='b'*64
        else:
            unit=report['nodes']['0']['unit']
            if kind=='unit_digest': unit['sha256']='b'*64
            elif kind=='unit_package': unit['package_sha256']='b'*64
            elif kind=='unit_exec': unit['properties']['ExecStart']='{ path=/usr/bin/other ; }'
            elif kind=='unit_fragment': unit['properties']['FragmentPath']='/tmp/foreign.service'
            elif kind=='unit_dropin': unit['properties']['DropInPaths']='/etc/systemd/system/override.conf'
            elif kind=='unit_killmode': unit['properties']['KillMode']='process'
            put(archive,'nodes/server/unit.json',unit)
        put(archive,path,report)
        category='installation'
    elif kind.startswith('topology'):
        report=json.loads((archive/'topology.json').read_text())
        if kind=='topology_missing_node': del report['client1']
        elif kind=='topology_identity': report['client2']['identity']['node_id']=1
        elif kind=='topology_usb': report['client2']['wireless']['usb_speed']='12'
        elif kind=='topology_mac': report['server']['wireless']['mac']='00:11:22:33:44:55'
        elif kind=='topology_resources': report['client1']['resources']['tun_exists']=True
        put(archive,'topology.json',report)
        category='preflight'
    else:
        if kind in ('terminal_active_job','terminal_active_job_missing','terminal_client_not_ready'):
            status=json.loads((archive/'job-idle.json').read_text())
            if kind=='terminal_active_job': status['active_job']={'job_id':'job_test'}
            elif kind=='terminal_active_job_missing': del status['active_job']
            else: status['nodes']['2']['readiness']='NOT_READY'
            put(archive,'job-idle.json',status)
        elif kind=='terminal_exit_code_mismatch':
            path=archive/'nodes/client2/daemon.log'
            path.write_text(path.read_text().replace('exit_code=0','exit_code=-15'))
        else:
            path=archive/'nodes/server/daemon.log'
            text=path.read_text()
            path.write_text(text.replace('job_id=job_test','job_id=other') if kind=='terminal_wrong_job'
                            else '\n'.join(line for line in text.splitlines() if '广播成功' not in line)+'\n')
        category='runtime_evidence'
    reseal(archive)
    result=validate_archive(archive)
    assert result['status']=='failed'
    assert any(e['category']==category for e in result['errors'])


@pytest.mark.parametrize('returncode', [-15,-9])
def test_completed_job_can_have_terminated_role_exit_code(archive,returncode):
    path=archive/'clients/2/evidence_manifest.json'
    manifest=json.loads(path.read_text());manifest['returncode']=returncode
    put(archive,'clients/2/evidence_manifest.json',manifest)
    log=archive/'nodes/client2/daemon.log'
    log.write_text(log.read_text().replace('exit_code=0',f'exit_code={returncode}'))
    reseal(archive)
    assert validate_archive(archive)['status']=='passed'


def test_init_never_overwrites(archive):
    with pytest.raises(FileExistsError):
        init_envelope(archive, run_id='run_test', job_id='job_test', commit=COMMIT)


@pytest.mark.parametrize('field,value', [('run_id','../escape'),('job_id','bad/path'),('commit','abc')])
def test_invalid_identity(tmp_path, field, value):
    args = dict(run_id='run',job_id='job',commit=COMMIT)
    args[field] = value
    with pytest.raises(FLRuntimeError if field in ('run_id','job_id') else ValueError):
        init_envelope(tmp_path/'new', **args)


@pytest.mark.parametrize('path', ['clients/1/observation.jsonl','clients/2/role_service.log',
    'clients/1/rounds','server/observation.jsonl','resources/stopped.json','radio/events.jsonl','installation.json'])
def test_missing_evidence_fails(archive, path):
    p = archive/path
    shutil.rmtree(p) if p.is_dir() else p.unlink()
    assert validate_archive(archive)['status']=='failed'


@pytest.mark.parametrize('path,field,value', [
    ('server/server_role.json','io_timeout_seconds',10),
    ('server/server_role.json','live_observation',False),
    ('job/request.json','round_timeout_seconds',10),
    ('job/coordinator.json','run_id','other'),
    ('job/window.json','terminal_at',500),
    ('radio/result.json','status','finalized'),
    ('radio/fault.json','present',True),
    ('envelope.json','commit','b'*40),
])
def test_tampered_evidence_fails(archive,path,field,value):
    obj=json.loads((archive/path).read_text());obj[field]=value;put(archive,path,obj)
    assert validate_archive(archive)['status']=='failed'


def test_regenerated_manifest_cannot_hide_wrong_generated_config(archive):
    config=role(1);config['io_timeout_seconds']=10
    put(archive,'clients/1/client_role.json',config)
    evidence_manifest(archive,1)
    assert validate_archive(archive)['status']=='failed'


@pytest.mark.parametrize('start,end,stage,category', [(2,4,'collect','collect'),(4,5,'run-sync','observe'),(101,102,'run-radio-recovery','restore-channel')])
def test_ssh_boundary(archive,start,end,stage,category):
    with (archive/'orchestration.jsonl').open('a') as f:
        f.write(json.dumps(bind(stage=stage,target='vm2',command='true',category=category,
            started_at=start,ended_at=end,returncode=0))+'\n')
    assert validate_archive(archive)['status']=='failed'


def test_symlink_rejected(archive,tmp_path):
    p=archive/'clients/1/role_service.log';p.unlink()
    external=tmp_path/'external';external.write_text('successful runtime\n');p.symlink_to(external)
    assert validate_archive(archive)['status']=='failed'


def test_failure_outcome_matrix(archive):
    base=archive/'clients/1'
    for p in list(base.iterdir()):
        if p.name not in ('evidence_manifest.json','build_identity.json'):
            shutil.rmtree(p) if p.is_dir() else p.unlink()
    evidence_manifest(archive,1)
    manifest=json.loads((base/'evidence_manifest.json').read_text())
    manifest['lifecycle_outcome']='start_failed';manifest['returncode']=-1
    put(base,'evidence_manifest.json',manifest)
    assert validate_client_evidence(base,run_id='run_test',job_id='job_test',node_id=1,commit=COMMIT)==[]
    result=validate_archive(archive)
    assert result['status']=='failed'
    assert any(e['category']=='runtime_evidence' for e in result['errors'])


def test_duplicate_json_key_rejected(archive):
    (archive/'job/window.json').write_text('{"run_id":"run_test","run_id":"other"}')
    assert validate_archive(archive)['status']=='failed'


def reseal(root):
    """Model a bad fact collected before sealing, so semantic checks must reject it."""
    from tests.fl_runtime.stage3_archive import seal_archive
    (root/'archive_manifest.json').unlink()
    seal_archive(root)


def change(root,path,modify):
    obj=json.loads((root/path).read_text());modify(obj);put(root,path,obj)


def test_local_command_during_sync_allowed(archive):
    with (archive/'orchestration.jsonl').open('a') as f:
        f.write(json.dumps(bind(stage='run-sync',target='vm0',transport='local',command='cat local-summary.json',category='inspection',started_at=4,ended_at=5,returncode=0))+'\n')
    reseal(archive)
    assert validate_archive(archive)['status']=='passed'


@pytest.mark.parametrize('kind', ['stage-order','stage-missing','unit-active','cgroup-process','unit-raw-active','raw-process-leak','raw-tun-leak','link-default','radio-readback','wrong-node-list','package-identity','radio-timestamp','radio-node','radio-causal','radio-topology','fault-raw','client-config','manifest-size'])
def test_semantic_rejection_before_sealing(archive,kind):
    if kind=='stage-order':
        change(archive,'stages/collect.json',lambda o:o.update(started_at=1))
    elif kind=='stage-missing':
        change(archive,'envelope.json',lambda o:o.update(completed_stages=o['completed_stages'][:-1]))
    elif kind in ('unit-active','cgroup-process','unit-raw-active','raw-process-leak','raw-tun-leak'):
        def corrupt(o):
            n=o['nodes']['1'];raw=n['raw']
            if kind=='unit-active':raw['unit_status']='active'
            elif kind=='cgroup-process':raw['cgroup_procs']=[99]
            elif kind=='unit-raw-active':raw['raw_evidence']['show_props']['ActiveState']='active'
            elif kind=='raw-process-leak':raw['processes']=[dict(pid=99,comm='uftp',args=['uftp'])]
            else:raw['tuns']=['fl-c1']
        change(archive,'resources/stopped.json',corrupt)
    elif kind=='link-default':
        p=archive/'server/link.log';p.write_text(p.read_text().replace('guard_interval_ms=10','guard_interval_ms=9'))
    elif kind=='radio-readback':
        p=archive/'server/iw.txt';p.write_text(p.read_text().replace('channel 157','channel 149'))
    elif kind=='wrong-node-list':
        change(archive,'job/request.json',lambda o:o.update(target_nodes=[True,2]))
    elif kind=='package-identity':
        change(archive,'installation.json',lambda o:o['nodes']['1']['files']['wfb_ng/fl/build_identity.json'].update(installed_sha256='b'*64,expected_sha256='b'*64))
    elif kind.startswith('radio-'):
        p=archive/'radio/events.jsonl';events=[json.loads(x) for x in p.read_text().splitlines()]
        if kind=='radio-timestamp':events[2]['timestamp']+=1
        elif kind=='radio-node':events[2]['node_id']=1
        elif kind=='radio-causal':events[0]['timestamp']=130
        else:
            e=events[-1];line=json.loads(e['line']);line['status']['nodes']['2']['readiness']='OFFLINE'
            old=e['line'];e['line']=json.dumps(line)
            journal=archive/'radio/journal.log';journal.write_text(journal.read_text().replace(old,e['line']))
        p.write_text(''.join(json.dumps(e)+'\n' for e in events))
    elif kind=='fault-raw':
        change(archive,'fault-state.json',lambda o:o.update(rules='-A INPUT -m comment --comment run_test -j DROP'))
    elif kind=='client-config':
        change(archive,'clients/1/client_role.json',lambda o:o.update(io_timeout_seconds=10))
        evidence_manifest(archive,1)
    else:
        result=json.loads((archive/'clients/1/issue41-client-result.json').read_text())
        rid=result['rounds'][0]['round_id']
        change(archive,f'clients/1/rounds/{rid}/model.manifest.json',lambda o:o.update(size_bytes=1))
        evidence_manifest(archive,1)
    reseal(archive)
    result=validate_archive(archive)
    assert result['status']=='failed'
    assert all(e['category']!='archive_integrity' for e in result['errors'])


def test_radio_command_must_equal_fixed_contract(archive):
    with (archive/'orchestration.jsonl').open('a') as f:
        f.write(json.dumps(bind(stage='run-radio-recovery',target='vm2',command='touch /tmp/pretend-observation',category='fault-observe',started_at=110,ended_at=111,returncode=0))+'\n')
    reseal(archive)
    assert any(e['category']=='ssh_boundary' for e in validate_archive(archive)['errors'])


def test_archive_seal_detects_modification(archive):
    (archive/'server/link.log').write_text('changed after sealing')
    assert any(e['category']=='archive_integrity' for e in validate_archive(archive)['errors'])


def test_reseal_rejected(archive):
    from tests.fl_runtime.stage3_archive import seal_archive
    with pytest.raises(ValueError,match='already sealed'):
        seal_archive(archive)


def test_unknown_observation_event_does_not_reject_legal_server_identity(archive):
    with (archive/'server/observation.jsonl').open('a') as f:
        f.write('WFB_FL_EVENT '+json.dumps(dict(event='model_progress',node_id=255,round='diagnostic'))+'\n')
    reseal(archive)
    assert validate_archive(archive)['status']=='passed'


@pytest.mark.parametrize('which', ['empty','missing-client-install','failed-start','missing-confold'])
def test_orchestration_requires_successful_three_node_stage_audit(archive,which):
    path=archive/'orchestration.jsonl'
    entries=[json.loads(line) for line in path.read_text().splitlines()]
    if which=='empty':entries=[]
    elif which=='missing-client-install':
        entries=[e for e in entries if not (e['stage']=='install' and e['target']=='vm2')]
    elif which=='missing-confold':
        for e in entries:
            if e['stage']=='install':
                e['command']=e['command'].replace('--force-confold ','')
    else:
        for e in entries:
            if e['stage']=='start-services' and e['target']=='vm1':e['returncode']=1
    path.write_text(''.join(json.dumps(e)+'\n' for e in entries))
    reseal(archive)
    assert any(e['category']=='ssh_boundary' for e in validate_archive(archive)['errors'])


def test_validation_result_is_outside_original_evidence_seal(archive):
    put(archive,'validation.json',dict(status='failed',errors=['previous report']))
    assert validate_archive(archive)['status']=='passed'
