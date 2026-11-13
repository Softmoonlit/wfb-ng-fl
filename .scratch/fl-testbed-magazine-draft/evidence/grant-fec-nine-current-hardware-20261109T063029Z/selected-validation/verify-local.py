"""仅本地语法、计划、归档与 SHA 核验；绝不导入执行编排模块。"""
import ast, hashlib, json, pathlib, tarfile, time
ROOT=pathlib.Path(__file__).resolve().parent
CWD=pathlib.Path('/home/fl/projects/wfb-ng-fl')

def sha(path):return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()
def value(tree,node):
    if isinstance(node,ast.Name):return constant(tree,node.id)
    if isinstance(node,ast.BinOp):
        a,b=value(tree,node.left),value(tree,node.right)
        if isinstance(node.op,ast.Add):return a+b
        if isinstance(node.op,ast.Mult):return a*b
        raise AssertionError('不支持的计划表达式')
    return ast.literal_eval(node)

def constant(tree,name):
    for node in tree.body:
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in node.targets):
            if isinstance(node.value,ast.Call) and isinstance(node.value.func,ast.Name) and node.value.func.id=='dict':
                assert not node.value.args
                return {k.arg:value(tree,k.value) for k in node.value.keywords}
            return value(tree,node.value)
    raise AssertionError(name)

def main():
    plan=json.loads((ROOT/'plan.json').read_text());profile=pathlib.Path(plan['profile'])
    state=json.loads((profile/'state.json').read_text())
    compiled=[]
    for p in sorted(ROOT.glob('*.py')):
        compile(p.read_text(),str(p),'exec');compiled.append(p.name)
    driver=ast.parse((ROOT/'driver-base.py').read_text());helper=ast.parse((ROOT/'helpers.py').read_text())
    for tree,name in [(driver,'AUDIT_CODE'),(helper,'POLL_CODE')]:compile(constant(tree,name),name,'exec')
    extraction=ROOT.parent/'fec14-remaining/storage-cleanup-continuation-20261109T065239Z/worker.py'
    source=ast.parse(extraction.read_text())
    for name in ['HASH','ARCHIVE','DELETE']:compile(constant(source,name),name,'exec')
    assert state['completed']==[] and state['targets']==plan['hosts']
    assert state['expected']==plan['expected_cold']==dict(grant_duration_ms=10,guard_interval_ms=10,fec_n=12)
    assert state['commit']==plan['source_commit']==constant(driver,'COMMIT')
    assert state['native_sha']==state['native_before_sha']==plan['native_sha256']==constant(driver,'SHA')
    assert sha(profile/'source.tar')==state['archive_sha']
    assert sha(profile/'wfb_v6_uplink')==sha(state['native_artifact_input'])==state['native_sha']
    assert sha(ROOT/'release-online-node.py')==state['helperSHA']
    with tarfile.open(profile/'source.tar') as archive:
        assert hashlib.sha256(archive.extractfile('wfb_v6_uplink').read()).hexdigest()==state['native_sha']
        assert all(not pathlib.PurePosixPath(m.name).is_absolute() and '..' not in pathlib.PurePosixPath(m.name).parts for m in archive.getmembers())
    assert sha(ROOT/'hardware-from-actual.json')==sha(ROOT.parent/'fec12/hardware-from-actual.json')
    hardware=json.loads((ROOT/'hardware-from-actual.json').read_text())
    assert sha(hardware['source'])==hardware['source_sha256'] and hardware['source_audit_ok'] is True
    payload=CWD/'.scratch/fl-testbed-magazine-draft/models/cifar10-resnet18-seed7.pt'
    assert payload.stat().st_size==plan['model_bytes']==44770302 and sha(payload)==plan['model_sha256']==constant(driver,'PAYLOAD_SHA')
    tool=CWD/'.scratch/fl-testbed-magazine-draft/communication'
    bundle=hashlib.sha256()
    for name in ['runner.py','node_agent.py','recover.py']:bundle.update(name.encode()+b'\0'+(tool/name).read_bytes())
    assert bundle.hexdigest()==plan['bundle_sha256']==constant(driver,'BUNDLE_SHA')
    timing=constant(ast.parse((tool/'runner.py').read_text()),'TIMING')
    assert timing['io_timeout']==plan['direction_seconds']==600 and timing['orchestration_timeout']==plan['overall_seconds']==1290
    assert plan['hosts']==['fl00','fl01','fl02','fl03','fl05','fl06','fl07','fl08','fl09','fl10']
    assert constant(driver,'SCHEDULE')==[(30,5)] and plan['trials']==[1,2] and plan['max_executions']==2 and plan['small_executions']==0 and plan['stage_budget_seconds']==4500
    assert plan['final_schedule']=='30/5' and plan['baseline_channel']==149 and plan['test_channel']==157 and plan['uftp_kbps']==12000
    worker=(ROOT/'worker.py').read_text();storage=(ROOT/'storage.py').read_text();dtext=(ROOT/'driver-base.py').read_text()
    assert worker.count('d.configure(')==1 and "d.configure(30,5,setuproot)" in worker
    assert "tag+'-trial'+str(c['trial'])" in worker and "d.run_transfer(root,30,5,'model')" in worker
    assert "prior=ROOT.parent/'fec14-matched-completion/after-cold-audit-recovery'" in worker and "'ownerfinished'" in worker
    assert "'prior_remote_owners_finished'" in worker and 'all_actors_exited' not in worker
    # 仅提取纯守门函数，以本地样例核验合法跳过与拒绝路径；不执行 worker 主入口。
    guard_node=next(n for n in ast.parse(worker).body if isinstance(n,ast.FunctionDef) and n.name=='validate_prior_screening')
    namespace={}
    exec(compile(ast.Module(body=[guard_node],type_ignores=[]),'本地筛选守门核验','exec'),namespace)
    guard=namespace['validate_prior_screening']
    sample=dict(status='complete',candidates=[])
    for grant,interval in [(10,10),(20,10),(30,10),(20,5)]:
        for mode in ['smoke','model']:
            sample['candidates'].append(dict(grant_duration_ms=grant,guard_interval_ms=interval,mode=mode,execute_count=1,stage='candidate-finished',transfer_verified=True,independent_exact_cleanup_ok=True,independent_restore_ok=True,independent_ready_ok=True))
    guard(sample)
    skipped=json.loads(json.dumps(sample));skipped['candidates'][0]['transfer_verified']=False
    skipped['candidates'][1].update(execute_count=0,stage='skipped');guard(skipped)
    bad_samples=[]
    for index,change in [(0,dict(execute_count=0)),(0,dict(execute_count=2)),(1,dict(execute_count=0,stage='pending')),(1,dict(execute_count=0,stage='skipped')),(1,dict(execute_count=2))]:
        bad=json.loads(json.dumps(sample));bad['candidates'][index].update(change);bad_samples.append(bad)
    bad=json.loads(json.dumps(sample));bad['status']='running';bad_samples.append(bad)
    bad=json.loads(json.dumps(sample));bad['candidates'].pop();bad_samples.append(bad)
    for bad in bad_samples:
        try:guard(bad)
        except AssertionError:pass
        else:raise AssertionError('筛选守门错误接受未完成或非法执行路径')
    assert "if phase=='audit':cold_ready()" in worker and 'seconds=30' in worker and 'end=time.monotonic()+60' in worker
    assert "BATCHROOT = ROOT.parent" in storage and "base.rglob('*exact-uftp-kernel-files.json')" in storage
    assert all(x in storage for x in ["BATCHROOT/'fec14-remaining'","BATCHROOT/'fec12'","BATCHROOT/'fec14-matched-completion'",'if not exists[\'exists\']:continue'])
    assert 'rm -rf' not in storage and 'shutil.rmtree' not in storage and "'client10 USB speed changed'" in dtext
    assert not any((ROOT/p).exists() for p in ['GO.json','worker-process.json','matrix.json','checkpoint.json'])
    files=list(ROOT.glob('*.py'))+[ROOT/'README.md',ROOT/'plan.json',ROOT/'hardware-from-actual.json']+list(profile.iterdir())
    files += [extraction,pathlib.Path(hardware['source']),payload,CWD/'.scratch/fl-testbed-magazine-draft/evidence/model-sized-four-20261106T173222Z/model-manifest.json']
    files += [tool/n for n in ['runner.py','node_agent.py','recover.py']]
    manifest=dict(说明='仅本地准备的审阅输入；运行前完整核验，未授权任何现场动作',files={str(p.resolve()):sha(p) for p in sorted(set(files)) if p.is_file()})
    (ROOT/'review-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    result=dict(ok=True,说明='本地编译语法对象、嵌入代码语法、计划及SHA核验通过，未执行编排',compiled=compiled,profile_completed=state['completed'],bundle_sha256=bundle.hexdigest(),native_sha256=state['native_sha'],model_bytes=payload.stat().st_size,model_sha256=sha(payload),review_manifest_sha256=sha(ROOT/'review-manifest.json'),reviewed_files=len(manifest['files']),prior_screening_guard_checks=dict(accepted=2,rejected=len(bad_samples)),remote_actions_this_turn=0,worker_started=False,wall=time.time())
    (ROOT/'local-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
