"""仅本地核验语法、冻结字节身份及七档计划，不导入运行编排模块。"""
import ast
import hashlib
import json
from pathlib import Path
import tarfile
import tokenize
import io

ROOT=Path(__file__).resolve().parent
CWD=Path('/home/fl/projects/wfb-ng-fl')
PROFILE=CWD/'.scratch/heartbeat-restart/heartbeat-1cd6264d5d6b-20261108T124420Z'
TOOL=CWD/'.scratch/fl-testbed-magazine-draft/communication'
SCHEDULE=[(10,10),(20,10),(30,10),(40,10),(60,10),(20,5),(30,5)]
HOSTS=['fl00','fl01','fl02','fl03','fl05','fl06','fl07','fl08','fl09','fl10']

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,value):Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def literals(tree):
    result={}
    for node in tree.body:
        if isinstance(node,ast.Assign):
            for name in node.targets:
                if isinstance(name,ast.Name):
                    try:result[name.id]=ast.literal_eval(node.value)
                    except (ValueError,TypeError):pass
    return result

def main():
    trees={};syntax=[]
    for path in sorted(ROOT.glob('*.py')):
        text=path.read_text();tree=ast.parse(text,filename=str(path));compile(tree,str(path),'exec');trees[path.name]=tree;syntax.append(path.name)
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type==tokenize.COMMENT:
                assert any('\u4e00'<=c<='\u9fff' for c in token.string), (path,token.start,'新注释须用中文')
        doc=ast.get_docstring(tree)
        if doc:assert any('\u4e00'<=c<='\u9fff' for c in doc),path
    for name in ['release-online.py','release-online-node.py']:
        ast.parse((PROFILE/name).read_text(),filename=name)
    driver=(ROOT/'driver-base.py').read_text();worker=(ROOT/'worker.py').read_text();storage=(ROOT/'storage.py').read_text()
    constants=literals(trees['driver-base.py'])
    assert constants['SCHEDULE']==SCHEDULE and constants['NODES']==[1,2,3,5,6,7,8,9,10]
    assert constants['COMMIT']=='1cd6264d5d6be660998ce60f3a349063009ecfa7'
    assert constants['SHA']=='44d6bbf500f51df4d0b3ae8cd7a53c4a45cb55b09ca66cb5217e7ac75674feb2'
    assert constants['RELEASE']=='/srv/wfb-ng-fl/releases/'+PROFILE.name
    assert "for mode in ['smoke', 'model']" in driver
    assert "'--fec-n','12'" in driver and "a[a.index('--fec-n')+1]=='12'" in driver
    assert '0,8,expected_fec' in driver and 'expected_fec=12' in driver
    assert "radio!=[0x434f4d4d,0,0,0,0,40,3 if row['host']=='fl00' else 4,0,1]" in driver
    assert "txpower -18.00 dBm" in driver and "'rtl88xxau_wfb'" in driver
    assert "native['interface']!=HARDWARE[row['host']]['interface']" in driver
    assert 'original_runner_final_flags' in driver
    for field in ['immediate_independent_restore_ok','immediate_independent_ready_ok','independent_exact_cleanup_ok','independent_restore_ok','independent_ready_ok']:
        assert field in worker
    assert '150*60' in worker and 'boundary(1420)' in worker
    assert 'MINIMUM = 512 * 1024 * 1024' in storage and "{'fl01','fl02'}" in storage
    assert 'storage.gate(d,root)' in worker and "if mode=='model' and not smoke_ok" in worker
    assert "if not clean:stop('exact cleanup failure/residual state')" in worker
    assert 'review-manifest.json' in worker and 'GO.json' in worker
    assert not (ROOT/'GO.json').exists() and not (ROOT/'worker-process.json').exists()
    assert not (ROOT/'matrix.json').exists(), '本回合不可启动编排'
    for node in ast.walk(trees['driver-base.py']):
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and isinstance(node.func.value,ast.Name) and node.func.value.id=='time' and node.func.attr=='sleep':
            if node.args and isinstance(node.args[0],ast.Constant):assert node.args[0].value<=30
    compile(constants['AUDIT_CODE'],'远端审计代码字符串','exec')
    cleanup=ROOT.parent/'fec14-remaining/storage-cleanup-continuation-20261109T065239Z/worker.py'
    cleanup_constants=literals(ast.parse(cleanup.read_text()))
    for name in ['HASH','ARCHIVE','DELETE']:compile(cleanup_constants[name],name,'exec')
    assert 'p.unlink()' in cleanup_constants['DELETE'] and 's2.st_mtime_ns' in cleanup_constants['DELETE']
    state=json.loads((PROFILE/'state.json').read_text())
    assert state['completed']==[] and state['targets']==HOSTS
    assert state['expected']==dict(grant_duration_ms=10,guard_interval_ms=10,fec_n=12)
    assert state['commit']==constants['COMMIT'] and state['native_sha']==state['native_before_sha']==constants['SHA']
    assert sha(PROFILE/'wfb_v6_uplink')==state['native_sha']
    assert sha(PROFILE/'source.tar')==state['archive_sha']
    assert sha(PROFILE/'release-online-node.py')==state['helperSHA']
    with tarfile.open(PROFILE/'source.tar') as archive:
        members=archive.getmembers();names={m.name for m in members}
        assert all(not Path(m.name).is_absolute() and '..' not in Path(m.name).parts and (m.isfile() or m.isdir()) for m in members)
        assert 'wfb_ng/fl/server_daemon.py' in names and 'wfb_ng/fl/client_daemon.py' in names
        assert hashlib.sha256(archive.extractfile('wfb_v6_uplink').read()).hexdigest()==state['native_sha']
        install=archive.extractfile('scripts/install/install-v8.sh').read().decode()
        assert '\nmake build_identity PYTHON="$python"' in install and '\nmake build_v6' not in install
    hardware=json.loads((ROOT/'hardware-from-actual.json').read_text())
    assert sha(hardware['source'])==hardware['source_sha256'] and hardware['source_audit_ok']
    actual=json.loads(Path(hardware['source']).read_text());assert actual['ok']
    assert set(hardware['hosts'])==set(HOSTS)
    for row in actual['rows']:
        assert row['returncode']==0
        native=row['result']['native'][0];argv=native['argv'];interface=argv[argv.index('--air-interface')+1]
        assert hardware['hosts'][row['host']]==dict(mac=native['mac'],interface=interface)
    assert hardware['hosts']['fl00']['mac']=='fc:a3:86:b3:86:72'
    assert hardware['hosts']['fl10']['mac']=='fc:22:1c:10:01:19'
    assert hardware['hosts']['fl05']['mac']=='fc:22:1c:10:01:57'
    model=CWD/'.scratch/fl-testbed-magazine-draft/models/cifar10-resnet18-seed7.pt'
    model_manifest=CWD/'.scratch/fl-testbed-magazine-draft/evidence/model-sized-four-20261106T173222Z/model-manifest.json'
    assert model.stat().st_size==44770302 and sha(model)==constants['PAYLOAD_SHA']
    manifest=json.loads(model_manifest.read_text());assert manifest['size_bytes']==44770302 and manifest['sha256']==constants['PAYLOAD_SHA']
    digest=hashlib.sha256()
    for name in ['runner.py','node_agent.py','recover.py']:digest.update(name.encode()+b'\0'+(TOOL/name).read_bytes())
    assert digest.hexdigest()==constants['BUNDLE_SHA']
    plan=dict(status='pending_parent_GO',scope='仅本地准备与语法/计划核验',hosts=HOSTS,excluded=['fl04'],
              schedule=[dict(grant_ms=g,guard_ms=u,modes=['smoke','model'],smoke_bytes=1048576,model_requires_smoke_pass=True) for g,u in SCHEDULE],
              max_executions=14,model_bytes=44770302,model_sha256=constants['PAYLOAD_SHA'],fec_k=8,fec_n=12,
              release_root=constants['RELEASE'],source_commit=constants['COMMIT'],native_sha256=constants['SHA'],
              profile=str(PROFILE),profile_completed=[],cold_phases=['preflight','stage','prepare','stop','install','start','audit'],
              minimum_model_available_bytes=512*1024*1024,agent_cleanup_budget_target_bytes=1024*1024*1024,
              direction_seconds=600,overall_seconds=1290,stage_budget_seconds=9000,uftp_kbps=12000,
              test_channel=157,baseline_channel=149,bandwidth_mhz=40,gi='LGI',index=18,downlink_mcs=3,uplink_mcs=4,power='15W',
              transient_and_final_evidence_separate=True,failed_transfer_can_continue_after_exact_cleanup_full10_restore_nine_READY=True,
              remote_actions_this_turn=0,worker_started=False)
    write(ROOT/'plan.json',plan)
    verified=list(ROOT.glob('*.py'))+[ROOT/'README.md',ROOT/'plan.json',ROOT/'hardware-from-actual.json',cleanup,model_manifest]
    verified += [PROFILE/name for name in ['state.json','source.tar','wfb_v6_uplink','release-online.py','release-online-node.py']]
    verified += [TOOL/name for name in ['runner.py','node_agent.py','recover.py']]
    verified += [Path(hardware['source']), model]
    write(ROOT/'review-manifest.json',dict(files={str(p):sha(p) for p in verified}))
    result=dict(ok=True,syntax_files=syntax,embedded_code_syntax='通过',profile_completed=state['completed'],
                profile_bytes_and_native='摘要一致',hardware_source_audit_ok=True,hardware_hosts=len(hardware['hosts']),
                schedule_count=7,smoke_count_maximum=7,model_count_maximum=7,max_executions=14,
                raw_runner_flags_separate_from_independent_restore=True,minimum_model_available_bytes=512*1024*1024,
                frozen_bundle_sha=digest.hexdigest(),new_comments_and_module_docstrings='中文',
                worker_imported_or_started=False,profile_commands_executed=False,remote_or_live_HTTP_actions=0,
                limitations='本地静态核验未声称远端预飞、运行 smoke、模型传输或恢复已通过')
    write(ROOT/'local-verification.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
