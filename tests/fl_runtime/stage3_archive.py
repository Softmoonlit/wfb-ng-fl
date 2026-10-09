#!/usr/bin/env python3
"""Stage 3 evidence contract and fail-closed, offline archive validation.

Runtime files are preserved verbatim. JSON reports produced by the runner bind
run_id/job_id; journal-derived events retain their source file and exact line.
This proves the trusted runner's SSH boundary, not absence of external sessions.
SHA helpers and strict JSON parsing are shared with Issue41's canonical fixtures.
"""
import argparse
import json
import math
from pathlib import Path
import re
import stat
import tempfile
from functools import lru_cache
import uuid

from wfb_ng.fl.artifacts import file_sha256, read_json, write_json_atomic, validate_path_safe_identifier
from wfb_ng.fl.evidence import ROOT_FILES, ROUND_FILES, MAX_EVIDENCE_FILE_BYTES
from wfb_ng.fl.errors import FLRuntimeError
from wfb_ng.fl.runtime import validate_round_state
from wfb_ng.fl.issue41_fixtures import generate_model_fixture, generate_client_fixture

FIXED_CONFIG = dict(channel=157, radio_txpower_dbm=12, downlink_mcs=3,
    uplink_mcs=6, uftp_rate_kbps=15000, channel_width='HT40+', short_gi=True,
    fec_k=8, fec_n=14, grant_duration_ms=120, guard_interval_ms=10,
    artifact_size_bytes=40*1024*1024, mode='sync', rounds=2, target_nodes=[1,2],
    round_timeout_seconds=120, io_timeout_seconds=120, live_observation=True,
    job_timeout_seconds=400, startup_timeout_seconds=120,
    lease_seconds=15, recovery_timeout_seconds=20)
CONFIG_PATHS = ('server/server_role.json', 'server/job_config.json',
    'server/link.json', 'server/radio.json',
    'clients/1/client_role.json', 'clients/1/algorithm_config.json',
    'clients/2/client_role.json', 'clients/2/algorithm_config.json')
INSTALLED_FILES = ('/usr/bin/wfb-fl-server-daemon','/usr/bin/wfb-fl-client-daemon',
    '/lib/systemd/system/wfb-fl-server-daemon.service',
    '/lib/systemd/system/wfb-fl-client-daemon.service','/usr/bin/wfb_v6_uplink',
    'wfb_ng/fl/build_identity.json')
STAGES = ('preflight','install','start-services','run-sync','run-radio-recovery',
          'collect','stop-services','validate')


def _same(a, b):
    # Numeric 120.0 is a valid transport timeout; bool is never a number here.
    if isinstance(b, bool):
        return type(a) is bool and a == b
    if isinstance(b, (int,float)):
        return type(a) in (int,float) and a == b
    if isinstance(b,list):
        return isinstance(a,list) and len(a)==len(b) and all(type(x) is type(y) and _same(x,y) for x,y in zip(a,b))
    if isinstance(b,dict):
        return isinstance(a,dict) and set(a)==set(b) and all(_same(a[k],v) for k,v in b.items())
    return type(a) is type(b) and a == b


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _sha(value):
    return isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value) is not None


def init_envelope(archive_dir, *, run_id, job_id, commit):
    """Create a new archive only; never overwrite or resume an existing directory."""
    validate_path_safe_identifier(run_id, 'run_id')
    validate_path_safe_identifier(job_id, 'job_id')
    _require(isinstance(commit,str) and re.fullmatch('[0-9a-f]{40}',commit), 'invalid commit')
    root=Path(archive_dir)
    root.mkdir(parents=True, exist_ok=False)
    envelope=dict(schema_version=1,kind='stage3',run_id=run_id,job_id=job_id,
                  commit=commit,resolved_config=dict(FIXED_CONFIG),generated_config_sha256={},completed_stages=[])
    write_json_atomic(str(root/'envelope.json'),envelope)
    return envelope


def _file(root, relative):
    path=Path(relative)
    _require(not path.is_absolute() and path.parts and all(p not in ('.','..') for p in path.parts),
             f'unsafe evidence path: {relative}')
    _require(not Path(root).is_symlink(), 'archive root is a symlink')
    target=Path(root)
    for part in path.parts:
        target=target/part
        _require(not target.is_symlink(),f'symlink evidence: {relative}')
    _require(stat.S_ISREG(target.stat().st_mode),f'not a regular file: {relative}')
    return target


def _json(root, path):
    value=read_json(str(_file(root,path)))
    _require(isinstance(value,dict),f'{path}: expected object')
    return value


def _lines(root,path,prefix=''):
    result=[]
    for line in _file(root,path).read_text().splitlines():
        _require(bool(line.strip()),f'{path}: empty record')
        _require(not prefix or line.startswith(prefix),f'{path}: invalid observation prefix')
        def pairs(items):
            obj={}
            for key,val in items:
                _require(key not in obj,f'{path}: duplicate key {key}')
                obj[key]=val
            return obj
        value=json.loads(line[len(prefix):],object_pairs_hook=pairs,
                         parse_constant=lambda val: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
        _require(isinstance(value,dict),f'{path}: expected object record')
        result.append(value)
    return result


def _binding(value,env):
    for key in ('run_id','job_id'):
        _require(value.get(key)==env[key],f'{key} identity mismatch')


def _equal_fields(value,expected):
    for key,val in expected.items():
        if key in ('schema_version','node_id','round_index','rounds','rounds_total','rounds_completed','size_bytes'):
            _require(type(value.get(key)) is int,f'{key}: integer required')
        _require(_same(value.get(key),val),f'{key}: {value.get(key)!r} != {val!r}')


def _number(value):
    _require(type(value) in (int,float) and math.isfinite(value),'invalid timestamp/number')
    return value


def _duration(start,end,limit):
    delta=_number(end)-_number(start)
    _require(0<=delta<=limit,f'time gate exceeded: {delta} / {limit}')


def _options(args):
    _require(isinstance(args,list) and all(isinstance(x,str) for x in args),'invalid link_args')
    values={}
    target_nodes=set()
    for i,arg in enumerate(args):
        if not arg.startswith('--'):
            continue
        value=True if i+1==len(args) or args[i+1].startswith('--') else args[i+1]
        if arg=='--client-target':
            fields=value.split(':') if isinstance(value,str) else []
            _require(len(fields)==4 and all(fields) and re.fullmatch(r'[0-9]+',fields[0]),
                     'invalid client-target')
            node_id=int(fields[0])
            _require(1<=node_id<=255,'invalid client-target')
            _require(node_id not in target_nodes,'duplicate client-target node_id')
            target_nodes.add(node_id)
            values.setdefault(arg,[]).append(value)
        else:
            _require(arg not in values,f'duplicate link option {arg}')
            values[arg]=value
    return values


def _role(value,node):
    _equal_fields(value,dict(role='client' if node else 'server',
        io_timeout_seconds=120,live_observation=True))
    if 'max_update_size_bytes' in value:
        _require(type(value['max_update_size_bytes']) is int and value['max_update_size_bytes']>=40*1024*1024,'invalid update size cap')
    if node:
        _equal_fields(value,dict(node_id=node,channel=157,channel_width='HT40+',radio_txpower_dbm=12))
        _link(value.get('link_args'),node)
    else:
        _equal_fields(value,dict(uftp_rate_kbps=15000))


def _link(args,node):
    options=_options(args)
    if node:
        _require('--client-target' not in options,'client link forbids --client-target')
    else:
        _require(options.get('--known-clients')=='1,2,3,4,5,6,7,8,9,10',
                 'server known-clients mismatch')
        targets={f'{n}:10.80.0.{10+n}:127.0.0.1:1' for n in range(1,11)}
        _require(set(options.get('--client-target',[]))==targets,
                 'server client-target topology mismatch')
    for option, expected in {'--radio-mcs-index':str(6 if node else 3),
        '--fec-k':'8','--fec-n':'14','--radio-bandwidth':'40',
        '--radio-short-gi':True}.items():
        _require(options.get(option)==expected,f'link option {option} mismatch')
    for option,default in (('--grant-duration-ms','120'),('--guard-interval-ms','10')):
        _require(options.get(option,default)==default,f'link scheduling {option} mismatch')
    _require('--feedback-window-start-immediately' not in options,'unaccepted feedback mode')


def _whitelisted(path):
    if path in ROOT_FILES or path=='build_identity.json':
        return True
    parts=Path(path).parts
    if len(parts)!=3 or parts[0]!='rounds' or parts[2] not in ROUND_FILES:
        return False
    try:
        return str(uuid.UUID(parts[1]))==parts[1]
    except ValueError:
        return False


def validate_client_evidence(evidence_dir, *, run_id, job_id, node_id, commit):
    """Integrity plus ADR0015 lifecycle matrix; failures are returned, not raised."""
    errors=[]
    try:
        root=Path(evidence_dir)
        manifest=_json(root,'evidence_manifest.json')
        _equal_fields(manifest,dict(schema_version=1,run_id=run_id,job_id=job_id,
                                   node_id=node_id,runtime_commit=commit))
        outcome=manifest.get('lifecycle_outcome')
        _require(outcome in ('succeeded','start_failed','aborted','failed'),'invalid lifecycle outcome')
        _require(type(manifest.get('returncode')) is int,'invalid evidence returncode')
        files=manifest.get('files')
        _require(isinstance(files,dict),'missing evidence file table')
        required={'build_identity.json'}
        if outcome=='succeeded':
            required.update(ROOT_FILES)
        elif outcome in ('aborted','failed'):
            required.update(('client_role.json','role_service.log'))
        _require(required<=files.keys(),f'missing {outcome} lifecycle evidence: {required-files.keys()}')
        actual=set()
        for p in root.rglob('*'):
            _require(not p.is_symlink(),'symlink in evidence tree')
            if p.is_file() and p.relative_to(root).as_posix()!='evidence_manifest.json':
                actual.add(p.relative_to(root).as_posix())
        _require(actual==set(files),'unlisted or missing evidence files')
        for path,info in files.items():
            _require(_whitelisted(path),f'file outside evidence whitelist: {path}')
            p=_file(root,path)
            _require(type(info.get('size_bytes')) is int and 0<=info['size_bytes']<=MAX_EVIDENCE_FILE_BYTES,'evidence size invalid')
            _require(p.stat().st_size==info['size_bytes'] and _sha(info.get('sha256'))
                     and file_sha256(p)==info['sha256'],f'evidence integrity: {path}')
        identity=_json(root,'build_identity.json')
        _equal_fields(identity,dict(schema_version=1,commit=commit))
        _require(manifest.get('build_identity_sha256')==files['build_identity.json']['sha256'],'build identity digest mismatch')
        if outcome=='succeeded':
            result=_json(root,'issue41-client-result.json')
            _equal_fields(result,dict(schema_version=1,role='client',node_id=node_id,conclusion='succeeded'))
            rounds=result.get('rounds')
            _require(isinstance(rounds,list) and len(rounds)==2,'missing two client rounds')
            ids=[]
            for index,r in enumerate(rounds,1):
                rid=r.get('round_id')
                _require(isinstance(rid,str) and str(uuid.UUID(rid))==rid,'invalid round UUID')
                _equal_fields(r,dict(round_index=index,node_id=node_id))
                ids.append(rid)
                for name in ROUND_FILES:
                    _require(f'rounds/{rid}/{name}' in files,f'missing round evidence {rid}/{name}')
            _require(len(set(ids))==2,'duplicate client rounds')
            expected_round_files={f'rounds/{rid}/{name}' for rid in ids for name in ROUND_FILES}
            _require({p for p in files if p.startswith('rounds/')}==expected_round_files,'mixed client rounds')
    except (ValueError,KeyError,TypeError,OSError,FLRuntimeError,AttributeError,IndexError) as exc:
        errors.append(str(exc))
    return errors


def _preflight(root,env):
    report=_json(root,'preflight.json');_binding(report,env)
    _require(set(report['nodes'])=={'0','1','2'},'preflight topology mismatch')
    topology=_json(root,'topology.json')
    _require(set(topology)=={'server','client1','client2'},'raw preflight topology mismatch')
    for n,node in report['nodes'].items():
        _equal_fields(node,dict(commit=env['commit'],clean=True,
            usb_driver='xhci_hcd',identity_valid=True,ports_clear=True,
            resources_clear=True,disk_ok=True,payload_matcher=True))
        _require(node.get('driver') in ('rtl88xxau_wfb','88XXau_wfb'),'incorrect wireless driver')
        _require(isinstance(node.get('interface'),str) and node['interface'].startswith('wlx'),'interface must be dynamically discovered wlx*')
        role='server' if n=='0' else f'client{n}'
        raw=topology[role]
        _equal_fields(raw,dict(commit=env['commit'],workspace_clean=True))
        wireless=raw['wireless']
        _equal_fields(wireless,dict(interface=node['interface'],driver=node['driver'],usb_controller=node['usb_driver']))
        _require(_number(float(wireless['usb_speed']))>=480,'preflight USB speed below 480 Mbps')
        mac=wireless.get('mac')
        _require(isinstance(mac,str) and re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}',mac),'invalid adapter MAC')
        identity=raw.get('identity')
        if n=='0':
            _require(identity is None,'unexpected server node identity')
        else:
            _require(isinstance(identity,dict),'missing client node identity')
            _equal_fields(identity,dict(node_id=int(n)))
            _require(identity.get('tun_ip') in (f'10.80.0.{10+int(n)}',f'10.80.0.{10+int(n)}/24'),'client TUN identity mismatch')
        resources=raw['resources']
        _stopped_resources(resources)
        _require(_process_counts(resources.get('processes'))==dict(daemon=0,role_service=0,wfb_v6_uplink=0,uftp=0)
                 and resources.get('tuns')==[],'preflight residual process/TUN')


def _installation(root,env):
    report=_json(root,'installation.json');_binding(report,env)
    package=report['package']
    _equal_fields(package,dict(commit=env['commit'],build_clean=True,build_isolated=True,package_count=1))
    _require(_sha(package.get('sha256')) and file_sha256(_file(root,package['path']))==package['sha256'],'package digest mismatch')
    _require(set(report['nodes'])=={'0','1','2'},'installed topology mismatch')
    versions=[]
    for node in report['nodes'].values():
        _equal_fields(node,dict(commit=env['commit'],package_sha256=package['sha256']))
        _require(isinstance(node.get('version'),str) and node['version'],'missing installed version')
        versions.append(node['version'])
        files=node['files']
        for name in INSTALLED_FILES:
            matches=[path for path in files if path.lstrip('/').endswith(name.lstrip('/'))]
            _require(len(matches)==1,f'missing/ambiguous installed package ownership: {name}')
            info=files[matches[0]]
            _require(info.get('package_sha256')==package['sha256'] and _sha(info.get('installed_sha256'))
                     and info['installed_sha256']==info.get('expected_sha256'),f'installed artifact mismatch: {name}')
    _require(len(set(versions))==1,'package version mismatch')
    postflight=_json(root,'postflight.json');_binding(postflight,env)
    _require(set(postflight['nodes'])=={'0','1','2'},'postflight topology mismatch')
    for n,node in postflight['nodes'].items():
        _equal_fields(node,dict(commit=env['commit'],clean=True))
        role='server' if n=='0' else f'client{n}'
        initial=_json(root,f'nodes/{role}/unit.json')
        _require(node.get('unit')==initial,'active unit changed during run')
        unit=initial
        name='wfb-fl-'+('server' if n=='0' else 'client')+'-daemon.service'
        files=report['nodes'][n]['files']
        paths=[p for p in files if p.endswith('/'+name)]
        _require(len(paths)==1,'missing/ambiguous unit package provenance')
        path=paths[0]
        _equal_fields(unit,dict(package_path=path,package_sha256=package['sha256'],
                               sha256=files[path]['installed_sha256']))
        props=unit.get('properties')
        _require(isinstance(props,dict),'missing active unit properties')
        _equal_fields(props,dict(KillMode='control-group',DropInPaths=''))
        fragment=props.get('FragmentPath')
        _require(fragment in (path,'/usr'+path if path.startswith('/lib/') else path.removeprefix('/usr')),
                 'active unit fragment outside package')
        executable=re.search(r'path=([^ ;]+)',props.get('ExecStart',''))
        entry='/usr/bin/wfb-fl-'+('server' if n=='0' else 'client')+'-daemon'
        _require(executable is not None and executable[1]==entry,'active unit executable mismatch')


def _startup_config(root,path,mcs):
    configs=[]
    for line in _file(root,path).read_text().splitlines():
        if 'v6_config ' in line:
            configs.append(dict(re.findall(r'(\w+)=(\d+)',line)))
    _require(configs,f'missing actual v6_config startup log: {path}')
    expected=dict(radio_bandwidth='40',radio_mcs_index=str(mcs),radio_short_gi='1',
        fec_k='8',fec_n='14',grant_duration_ms='120',guard_interval_ms='10')
    for config in configs:
        _equal_fields(config,expected)


def _physical_radio(root,path):
    radio=_json(root,path)
    _equal_fields(radio,dict(channel=157,channel_width='HT40+',radio_txpower_dbm=12))
    iw=_file(root,radio['source']).read_text()
    _require(re.search(r'channel 157 .*width: 40 MHz.*center1: 5795 MHz',iw),'actual channel/HT40+ mismatch')
    power=re.search(r'txpower (-?[0-9.]+) dBm',iw)
    _require(power is not None and abs(float(power.group(1)))==12,'actual txpower mismatch')


def _configurations(root,env):
    digests=env.get('generated_config_sha256')
    _require(isinstance(digests,dict) and set(digests)==set(CONFIG_PATHS),'missing generated config SHA binding')
    for path in CONFIG_PATHS:
        _require(_sha(digests[path]) and file_sha256(_file(root,path))==digests[path],f'generated config SHA mismatch: {path}')
    link=_json(root,'server/link.json')
    _link(link.get('argv'),0)
    _startup_config(root,'server/link.log',3)
    _physical_radio(root,'server/radio.json')
    job=_json(root,'server/job_config.json');_binding(job,env)
    _equal_fields(job,{k:FIXED_CONFIG[k] for k in ('mode','rounds','target_nodes','io_timeout_seconds','round_timeout_seconds','live_observation')})
    _equal_fields(job,dict(model_size_bytes=40*1024*1024))
    for n in (0,1,2):
        base='server' if n==0 else f'clients/{n}'
        _role(_json(root,base+('/server_role.json' if n==0 else '/client_role.json')),n)
        if n:
            _startup_config(root,f'nodes/client{n}/idle-link.log',6)
            _startup_config(root,f'clients/{n}/role_service.log',6)
            _physical_radio(root,f'nodes/client{n}/radio.json')
            alg=_json(root,base+'/algorithm_config.json')
            # client_main defaults to one round, so two must be explicit.
            _equal_fields(alg,dict(rounds=2))
            if 'required_artifact_size_bytes' in alg:
                _equal_fields(alg,dict(required_artifact_size_bytes=40*1024*1024))


def _window(root,env):
    request=_json(root,'job/request.json');_binding(request,env)
    _equal_fields(request,{k:FIXED_CONFIG[k] for k in ('mode','rounds','target_nodes',
        'io_timeout_seconds','round_timeout_seconds','live_observation')})
    window=_json(root,'job/window.json');_binding(window,env)
    _equal_fields(window,dict(ready_nodes=[1,2]))
    _duration(window['services_started_at'],window['nodes_ready_at'],120)
    _require(window['nodes_ready_at']<=window['accepted_at'],'job before ready gate')
    _duration(window['accepted_at'],window['terminal_at'],400)
    return window


def _ssh(root,env):
    window=_window(root,env)
    entries=_lines(root,'orchestration.jsonl')
    _require(entries,'empty orchestration audit')
    required={'preflight':(r'git\b.*rev-parse HEAD',r'git\b.*status --porcelain'),
        'install':(r'\bdpkg\s+--force-confold\s+--force-confdef\s+-i\s+\S+',),
        'start-services':(r'systemctl start wfb-fl-(?:server|client)-daemon\.service',r'systemctl is-active wfb-fl-(?:server|client)-daemon\.service'),
        'collect':(r'journalctl -u wfb-fl-(?:server|client)-daemon\.service',r'ip -j link show'),
        'stop-services':(r'systemctl stop wfb-fl-(?:server|client)-daemon\.service',r'systemctl show',r'ip -j link show')}
    for stage,patterns in required.items():
        for target in ('vm0','vm1','vm2'):
            successful=[e.get('command','') for e in entries if e.get('stage')==stage and e.get('target')==target and type(e.get('returncode')) is int and e['returncode']==0]
            for pattern in patterns:
                _require(any(re.search(pattern,c) for c in successful),f'missing successful {stage}/{target} command audit: {pattern}')
    for entry in entries:
        _binding(entry,env)
        _require(entry.get('stage') in STAGES,'unknown SSH phase')
        _require(entry.get('target') in ('vm0','vm1','vm2'),'unknown SSH target')
        _require(isinstance(entry.get('command'),str) and entry['command'],'missing SSH command')
        _require(isinstance(entry.get('category'),str) and entry['category'],'missing SSH category')
        _require(type(entry.get('returncode')) is int,'missing SSH exit code')
        start,end=_number(entry['started_at']),_number(entry['ended_at'])
        _require(start<=end,'invalid SSH interval')
        stage_record=_json(root,f"stages/{entry['stage']}.json")
        _require(stage_record['started_at']<=start<=end<=stage_record['ended_at'],'command timestamp outside its declared phase')
        transport=entry.get('transport','local' if entry['target']=='vm0' else 'ssh')
        _require(transport==('local' if entry['target']=='vm0' else 'ssh'),'orchestration transport/target mismatch')
        if transport=='local':
            continue
        categories={'preflight':{'inspection','preflight'},'install':{'inspection','install'},
            'start-services':{'inspection','start-services'},'collect':{'inspection','collect'},
            'stop-services':{'inspection','stop-services','fault-remove'},
            'run-radio-recovery':{'fault-install','fault-observe','fault-remove'}}
        _require(entry['stage'] in categories and entry['category'] in categories[entry['stage']],'SSH category outside stage matrix')
        _require(end<window['accepted_at'] or start>window['terminal_at'],'SSH overlaps normal job window')
        if entry['stage']=='run-radio-recovery' or entry['category']=='fault-remove':
            _require(entry['target']=='vm2','fault command must target client2')
            _require(start>=window['terminal_at'],'radio fault before job terminal')
            # Share the exact runner command contract instead of a keyword denylist.
            from tests.fl_runtime.stage3_runner import fault_rules
            fixed={fault_rules('install',env['run_id'])} if entry['category']=='fault-install' else {fault_rules('remove',env['run_id']),fault_rules('observe',env['run_id'])} if entry['category']=='fault-remove' else {fault_rules('observe',env['run_id'])}
            journal=r'sudo -n journalctl -u wfb-fl-client-daemon\.service --since @\d+(?:\.\d+)? --no-pager -o short-unix'
            _require(entry['command'] in fixed or (entry['category']=='fault-observe' and re.fullmatch(journal,entry['command'])), 'SSH command outside fixed radio fault contract')


def _manifest(value,rid,kind,sha,node=None):
    expected=dict(schema_version=1,artifact_type=kind,round_id=rid,size_bytes=40*1024*1024,sha256=sha)
    if node is None:
        expected['participant_node_ids']=[1,2]
    else:
        expected['node_id']=node
    _require(set(value)==set(expected),'manifest field mismatch')
    _equal_fields(value,expected)
    _require(_sha(sha),'invalid artifact digest')


def _observation(root,path,expected,node):
    records=_lines(root,path,'WFB_FL_EVENT ')
    _require(records,'missing observation records')
    for rid,n,sha in expected:
        matches=[r for r in records if r.get('round')==rid and r.get('node_id')==n
                 and r.get('sha256')==sha and _same(r.get('size_bytes'),40*1024*1024)
                 and r.get('role')==('client' if node else 'server')
                 and r.get('transport_outcome')==('created' if node else 'committed')
                 and r.get('event')==('upload_phase' if node else 'upload_committed')
                 and (not node or r.get('phase')=='final_response')]
        _require(matches,f'observation missing verified transfer {rid}/{n}')
    known={(rid,n) for rid,n,_ in expected}
    for record in records:
        if record.get('event') in ('upload_committed','upload_phase','active_uploads','upload_started','upload_rejected') and 'round' in record:
            _require((record.get('round'),record.get('node_id')) in known,'mixed observation identity')


def _job_terminal(root,env):
    for path in ('job-idle.json','collected-status.json'):
        status=_json(root,path)
        _require('active_job' in status,'terminal active_job fact missing')
        _equal_fields(status,dict(server_state='IDLE',active_job=None))
        _require(status.get('link_process',{}).get('running') is True,'terminal persistent server link missing')
        _equal_fields(status.get('radio',{}),{k:FIXED_CONFIG[k] for k in ('channel','radio_txpower_dbm','downlink_mcs','uplink_mcs','uftp_rate_kbps')})
        for n in (1,2):
            _equal_fields(status.get('nodes',{}).get(str(n),{}),dict(reported_state='IDLE',readiness='READY',current_channel=157))
    window=_window(root,env)
    collected=_json(root,'stages/collect.json')['ended_at']
    job=re.escape(env['job_id'])
    patterns={'server':(rf'作业终态广播成功 type=JOB_COMPLETED job_id={job}(?:\s|$)',
                        rf'作业 {job} 终态收口完成 \(outcome=completed,'),
              'client1':(rf'回收作业 {job} 资源 \(exit_code=(-?\d+)\)',),
              'client2':(rf'回收作业 {job} 资源 \(exit_code=(-?\d+)\)',)}
    for role,required in patterns.items():
        lines=_file(root,f'nodes/{role}/daemon.log').read_text().splitlines()
        times=[]
        for pattern in required:
            matching=[line for line in lines if re.search(pattern,line)]
            _require(len(matching)==1,f'missing/ambiguous job terminal journal: {role}/{pattern}')
            if role!='server':
                code=re.search(pattern,matching[0])
                manifest=_json(root,f'clients/{role[-1]}/evidence_manifest.json')
                _require(code is not None and type(manifest.get('returncode')) is int
                         and int(code[1])==manifest['returncode'],'client cleanup/evidence returncode mismatch')
            timestamp=re.match(r'^(\d+(?:\.\d+)?)\s',matching[0])
            _require(timestamp is not None,'terminal journal timestamp missing')
            observed=_number(float(timestamp[1]))
            _require(window['accepted_at']<=observed<=collected,'terminal journal outside job/collection window')
            times.append(observed)
        _require(times==sorted(times),'terminal broadcast/finalization order mismatch')


def _runtime(root,env):
    _job_terminal(root,env)
    coordinator=_json(root,'job/coordinator.json');_binding(coordinator,env)
    _equal_fields(coordinator,dict(schema_version=1,status='succeeded',mode='sync',
        rounds_total=2,rounds_completed=2,target_nodes=[1,2],final_model_size_bytes=40*1024*1024))
    rounds=coordinator['rounds'];_require(isinstance(rounds,list) and len(rounds)==2,'missing coordinator rounds')
    canonical=_fixture_digests()
    _require(coordinator.get('final_model_sha256')==canonical[0],'model is not canonical Issue41 fixture')
    results={}
    for n in (1,2):
        base=Path(root)/f'clients/{n}'
        errors=validate_client_evidence(base,run_id=env['run_id'],job_id=env['job_id'],node_id=n,commit=env['commit'])
        _require(not errors,f'client {n} evidence: {errors}')
        manifest=_json(base,'evidence_manifest.json')
        installed=_json(root,'installation.json')['nodes'][str(n)]['files']
        identity_files=[v for k,v in installed.items() if k.endswith('wfb_ng/fl/build_identity.json')]
        _require(len(identity_files)==1 and identity_files[0].get('installed_sha256')==manifest['build_identity_sha256'],'evidence build identity differs from installed package')
        _require(manifest['lifecycle_outcome']=='succeeded',f'client {n} lifecycle not succeeded')
        results[n]=_json(base,'issue41-client-result.json')
    ids=[];expected={0:[],1:[],2:[]};previous=None
    for index,summary in enumerate(rounds,1):
        _equal_fields(summary,dict(round_index=index,committed_nodes=[1,2],dropped_out_nodes=[]))
        _require(0<=_number(summary.get('duration_seconds'))<=120,'round timeout exceeded')
        rid=results[1]['rounds'][index-1]['round_id'];ids.append(rid)
        model_sha=summary['input_model_sha256'];output_sha=summary['output_model_sha256']
        _require(model_sha==output_sha and (previous is None or previous==model_sha),'placeholder aggregation/cross-round continuity mismatch')
        previous=output_sha
        base=f'server/rounds/{rid}'
        _manifest(_json(root,base+'/model.manifest.json'),rid,'model',model_sha)
        model=_file(root,base+'/model.bin')
        _require(model.stat().st_size==40*1024*1024 and file_sha256(model)==model_sha,'actual model SHA/size mismatch')
        output=_file(root,f'server/models/{index}.bin')
        _require(output.stat().st_size==40*1024*1024 and file_sha256(output)==output_sha,'actual aggregate SHA/size mismatch')
        state=_json(root,base+'/round-state.json');validate_round_state(state,rid,'server',())
        _equal_fields(state,dict(state='succeeded',participant_node_ids=[1,2],committed_update_node_ids=[1,2]))
        update_shas=[]
        for n in (1,2):
            r=results[n]['rounds'][index-1]
            _equal_fields(r,dict(round_id=rid,round_index=index,node_id=n,
                model_size_bytes=40*1024*1024,model_sha256=model_sha,update_size_bytes=40*1024*1024))
            sha=r['update_sha256'];update_shas.append(sha)
            _require(sha==canonical[n],'update is not canonical Issue41 fixture')
            _require(results[n].get('update_template_sha256')==sha,'update fixture differs across rounds')
            _manifest(_json(root,f'{base}/updates/{n}/update.manifest.json'),rid,'update',sha,n)
            binary=_file(root,f'{base}/updates/{n}/update.bin')
            _require(binary.stat().st_size==40*1024*1024 and file_sha256(binary)==sha,'actual update SHA/size mismatch')
            cbase=f'clients/{n}/rounds/{rid}'
            _manifest(_json(root,cbase+'/model.manifest.json'),rid,'model',model_sha)
            _manifest(_json(root,cbase+'/update.manifest.json'),rid,'update',sha,n)
            cstate=_json(root,cbase+'/round-state.json');validate_round_state(cstate,rid,'client',())
            _equal_fields(cstate,dict(state='succeeded'))
            expected[0].append((rid,n,sha));expected[n].append((rid,n,sha))
        _require(len(set(update_shas))==2,'client updates must be distinct')
    _require(len(set(ids))==2,'duplicate server rounds')
    _require({p.name for p in (Path(root)/'server/rounds').iterdir()}==set(ids),'mixed server rounds')
    _require(coordinator.get('final_model_sha256')==previous,'final model digest mismatch')
    for n in (0,1,2):
        base='server' if n==0 else f'clients/{n}'
        _equal_fields(_json(root,base+'/current-round.json'),dict(schema_version=1,
            role='server' if n==0 else 'client',round_id=ids[-1]))
        _observation(root,base+'/observation.jsonl',expected[n],n)


def _radio(root,env):
    result=_json(root,'radio/result.json')
    _equal_fields(result,dict(status='rolled_back',target_channel=149,unresponsive_nodes=[2]))
    _require(result.get('failed_phase') in ('commit','COMMIT'),'unexpected radio failure phase')
    _require(result.get('effective_config',{}).get('channel')==157,'server did not rollback')
    sid=validate_path_safe_identifier(result.get('session_id'), 'session_id')
    fault=_json(root,'radio/fault.json');_binding(fault,env)
    _equal_fields(fault,dict(node_id=2,drop_types=['NEW_CHANNEL_PING','RADIO_SWITCH_FINALIZED'],present=False))
    installed,removed=_number(fault['installed_at']),_number(fault['removed_at'])
    rule_state=_json(root,'fault-state.json')
    _equal_fields(rule_state,dict(installed=False,run_id=env['run_id']))
    _require(_same(rule_state.get('checked_at'),removed),'fault rule cleanup timestamp mismatch')
    _require(isinstance(rule_state.get('rules'),str) and env['run_id'] not in rule_state['rules'],'raw fault rules still present')
    window=_window(root,env)
    _require(window['terminal_at']<=installed<removed,'fault chronology invalid')
    events=_lines(root,'radio/events.jsonl');_require(events,'missing radio journal events')
    selected={}
    required={('PREPARE',0):157,('LEASE_ARM',2):157,('COMMIT',2):149,('SERVER_ROLLBACK',0):157,
              ('LEASE_TIMEOUT',2):157,('IDLE_READY',1):157,('IDLE_READY',2):157}
    for e in events:
        _require(e.get('session_id')==sid,'mixed radio session')
        _require(type(e.get('node_id')) is int and e['node_id'] in (0,1,2),'invalid journal node')
        _number(e.get('timestamp'))
        source=_file(root,e['source']);line=e.get('line')
        _require(isinstance(line,str) and line and line in source.read_text().splitlines(),'radio event lacks raw journal source')
        if e.get('event')=='IDLE_READY':
            raw=json.loads(line)
            _require(_same(raw.get('observed_at'),e['timestamp']),'topology observation timestamp mismatch')
            status=raw.get('status',raw)
            node=status.get('nodes',{}).get(str(e['node_id']),{})
            _equal_fields(node,dict(reported_state='IDLE',readiness='READY',current_channel=157))
            _require(status.get('server_state')=='IDLE','server not idle after recovery')
        else:
            timestamp=re.match(r'^(\d+(?:\.\d+)?)\s',line)
            _require(timestamp is not None and abs(float(timestamp.group(1))-e['timestamp'])<=0.001,'journal timestamp mismatch')
        key=(e.get('event'),e['node_id'])
        if key in required:
            _require(key not in selected,'duplicate radio causal event')
            _require(e.get('channel')==required[key],'radio event channel mismatch')
            _require(re.search(r'session='+re.escape(sid)+r'(?:\b|[,，])',line) or e['event']=='IDLE_READY','journal session absent')
            if e['event'] in ('COMMIT','LEASE_ARM','LEASE_TIMEOUT'):
                _require(re.search(r'节点\s+'+str(e['node_id'])+r'\b',line),'journal node identity mismatch')
            if e['event']=='COMMIT':
                _require('channel=149' in line,'journal does not prove physical COMMIT to 149')
            if e['event'] in ('SERVER_ROLLBACK','LEASE_TIMEOUT'):
                _require('Channel 157' in line,'journal does not prove fallback to 157')
            tokens={'PREPARE':('PREPARE',),'LEASE_ARM':('LEASE_ARM',),'COMMIT':('COMMIT',),
                'SERVER_ROLLBACK':('SERVER_ROLLBACK','回退至 Channel'),
                'LEASE_TIMEOUT':('LEASE_TIMEOUT','射频租约耗尽'),
                'IDLE_READY':('IDLE_READY','IDLE')}
            if e['event']!='IDLE_READY':
                _require(any(t in line for t in tokens[e['event']]),'journal does not prove event')
            selected[key]=e
    _require(set(selected)==set(required),'radio causal chain incomplete')
    prepare=selected['PREPARE',0]['timestamp'];commit=selected['COMMIT',2]['timestamp']
    rollback=selected['SERVER_ROLLBACK',0]['timestamp'];lease=selected['LEASE_TIMEOUT',2]
    armed=selected['LEASE_ARM',2]
    _require(installed<=prepare<=armed['timestamp']<=commit<=rollback<=lease['timestamp']<=removed,'radio causal/time order mismatch')
    _require(_same(armed.get('lease_seconds'),15),'lease must be 15 seconds')
    raw_seconds=re.search(r'lease_seconds=(\d+(?:\.\d+)?)',armed['line'])
    _require(raw_seconds is not None and float(raw_seconds.group(1))==15,'raw journal lease must be 15 seconds')
    _require(14.5<=lease['timestamp']-armed['timestamp']<=17,'lease expiry inconsistent with 15 seconds')
    for n in (1,2):
        t=selected['IDLE_READY',n]['timestamp']
        _duration(lease['timestamp'],t,20)
        _require(t<=removed,'fault removed before recovery observation')


def _stages(root,env):
    _require(env.get('completed_stages')==list(STAGES[:-1]),'incomplete or reordered formal stages')
    last=None
    records={}
    for stage in STAGES[:-1]:
        record=_json(root,f'stages/{stage}.json')
        _require(record.get('run_id')==env['run_id'],'mixed stage run')
        _require(record.get('status')=='passed','stage not successful')
        start,end=_number(record['started_at']),_number(record['ended_at'])
        _require(start<=end and (last is None or last<=start),'stage timestamp order mismatch')
        records[stage]=(start,end);last=end
    window=_window(root,env)
    _require(records['run-sync'][0]<=window['accepted_at']<=window['terminal_at']<=records['run-sync'][1], 'job outside run-sync stage')
    _require(records['start-services'][0]<=window['services_started_at']<=window['nodes_ready_at']<=records['start-services'][1], 'ready window outside start-services')


def _process_counts(processes):
    _require(isinstance(processes,list),'raw process table missing')
    counts=dict(daemon=0,role_service=0,wfb_v6_uplink=0,uftp=0)
    for process in processes:
        args=process.get('args')
        _require(isinstance(args,list) and all(isinstance(a,str) for a in args),'invalid raw process argv')
        _require(type(process.get('pid')) is int and process['pid']>0,'invalid raw process pid')
        counts['daemon']+=int(any(a in args for a in ('wfb_ng.fl.server_daemon','wfb_ng.fl.client_daemon','/usr/bin/wfb-fl-server-daemon','/usr/bin/wfb-fl-client-daemon')))
        counts['role_service']+=int(any(a in args for a in ('wfb_ng.fl.role_service','wfb_ng.fl.service')))
        counts['wfb_v6_uplink']+=int(process.get('comm')=='wfb_v6_uplink')
        counts['uftp']+=int(process.get('comm') in ('uftp','uftpd'))
    return counts


def _stopped_resources(raw):
    _equal_fields(raw,dict(clean=True,is_inactive=True,unit_status='inactive',
        cgroup_clean=True,tun_exists=False,cgroup_procs=[],orphan_processes=[]))
    _require(raw.get('tasks_current') in (0,None),'stopped cgroup task leak')
    evidence=raw.get('raw_evidence',{})
    _require(evidence.get('is_active')=='inactive','unit raw active state mismatch')
    props=evidence.get('show_props',{})
    _require(props.get('ActiveState')=='inactive' and props.get('MainPID')=='0','unit show evidence mismatch')
    _require(props.get('TasksCurrent') in ('0','[not set]'),'unit raw task count mismatch')
    _require('mtu' not in evidence.get('tun_out','').lower(),'raw TUN still present')


def _resources(root,env):
    for mode in ('idle','stopped'):
        report=_json(root,f'resources/{mode}.json');_binding(report,env)
        _require(set(report['nodes'])=={'0','1','2'},'resource topology mismatch')
        for n,node in report['nodes'].items():
            raw=node.get('raw');_require(isinstance(raw,dict),'missing raw resource audit')
            _require(_process_counts(raw.get('processes'))==node['processes'],'raw process counts mismatch')
            _require(raw.get('tuns')==node.get('tuns'),'raw TUN table mismatch')
            if mode=='stopped':
                _stopped_resources(raw)
            else:
                _require(raw.get('unit_status')=='active' and not raw.get('is_inactive'),'idle daemon unit not active')
                _require(raw.get('tun_exists') is True,'idle raw TUN absent')
            _equal_fields(node['processes'],dict(daemon=1 if mode=='idle' else 0,
                role_service=0,wfb_v6_uplink=1 if mode=='idle' else 0,uftp=0))
            tuns=node.get('tuns');_require(isinstance(tuns,list) and all(isinstance(t,str) for t in tuns),'invalid TUN evidence')
            if mode=='idle':
                _equal_fields(node,dict(state='IDLE',phase='READY',channel=157))
                expected='fl-s' if n=='0' else f'fl-c{n}'
                _require(tuns==[expected],'idle TUN ownership mismatch')
            else:
                _require(not any(re.fullmatch(r'fl-(s|c\d+)',t) for t in tuns),'stopped TUN leak')


def _fixture_digests():
    with tempfile.TemporaryDirectory(prefix='stage3-fixtures-') as directory:
        base=Path(directory)
        digests={0:generate_model_fixture(str(base/'model.bin'),size_bytes=40*1024*1024)['sha256']}
        for n in (1,2):
            digests[n]=generate_client_fixture(str(base/f'update-{n}.bin'),n,size_bytes=40*1024*1024)['sha256']
        return digests


_fixture_digests=lru_cache(maxsize=1)(_fixture_digests)


def _inventory(root):
    files={}
    for p in Path(root).rglob('*'):
        _require(not p.is_symlink(),'symlink in archive')
        if p.is_file():
            relative=p.relative_to(root).as_posix()
            if relative in ('archive_manifest.json','validation.json'):
                continue
            _file(root,relative)
            files[relative]=dict(size_bytes=p.stat().st_size,sha256=file_sha256(p))
    return files


def seal_archive(archive_dir):
    """Bind generated configs and seal all original evidence; never re-seal."""
    root=Path(archive_dir)
    _require(not (root/'archive_manifest.json').exists(),'archive already sealed')
    env=_json(root,'envelope.json')
    env['generated_config_sha256']={path:file_sha256(_file(root,path)) for path in CONFIG_PATHS}
    write_json_atomic(str(root/'envelope.json'),env)
    manifest=dict(schema_version=1,run_id=env['run_id'],job_id=env['job_id'],files=_inventory(root))
    write_json_atomic(str(root/'archive_manifest.json'),manifest)
    return manifest


def _seal(root,env):
    manifest=_json(root,'archive_manifest.json');_binding(manifest,env)
    _equal_fields(manifest,dict(schema_version=1))
    _require(manifest.get('files')==_inventory(root),'archive changed after sealing')


def validate_archive(archive_dir):
    """Read a sealed archive; every missing/malformed fact fails its audit category."""
    root=Path(archive_dir);errors=[]
    try:
        env=_json(root,'envelope.json')
        _equal_fields(env,dict(schema_version=1,kind='stage3'))
        validate_path_safe_identifier(env.get('run_id'), 'run_id')
        validate_path_safe_identifier(env.get('job_id'), 'job_id')
        _require(isinstance(env.get('commit'),str) and re.fullmatch('[0-9a-f]{40}',env['commit']),'invalid envelope commit')
        config=env.get('resolved_config');_require(isinstance(config,dict) and set(config)==set(FIXED_CONFIG),'fixed config fields mismatch')
        _equal_fields(config,FIXED_CONFIG)
    except (ValueError,KeyError,TypeError,OSError,FLRuntimeError) as exc:
        return dict(status='failed',errors=[dict(category='envelope',message=str(exc))])
    for category,check in (('archive_integrity',_seal),('stages',_stages),('preflight',_preflight),('installation',_installation),
        ('configuration',_configurations),('ssh_boundary',_ssh),('runtime_evidence',_runtime),
        ('radio_recovery',_radio),('resource_cleanup',_resources)):
        try:
            check(root,env)
        except (ValueError,KeyError,TypeError,OSError,FLRuntimeError,AttributeError,IndexError) as exc:
            errors.append(dict(category=category,message=str(exc)))
    return dict(status='failed' if errors else 'passed',run_id=env['run_id'],job_id=env['job_id'],errors=errors)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    init=sub.add_parser('init');init.add_argument('archive_dir')
    for key in ('run-id','job-id','commit'):
        init.add_argument('--'+key,required=True)
    seal=sub.add_parser('seal');seal.add_argument('archive_dir')
    validate=sub.add_parser('validate');validate.add_argument('archive_dir')
    args=parser.parse_args(argv)
    if args.command=='init':
        print(json.dumps(init_envelope(args.archive_dir,run_id=args.run_id,job_id=args.job_id,commit=args.commit)))
        return 0
    if args.command=='seal':
        print(json.dumps(seal_archive(args.archive_dir)))
        return 0
    result=validate_archive(args.archive_dir);print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['status']=='passed' else 1


if __name__=='__main__':
    raise SystemExit(main())
