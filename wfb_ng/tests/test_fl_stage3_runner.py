"""Runner safety and REST orchestration tests; no SSH/systemd/radio required."""
import json
from pathlib import Path
import subprocess
import urllib.error

import pytest

from tests.fl_runtime import stage3_runner as module
from tests.fl_runtime.stage3_archive import init_envelope
from wfb_ng.fl.errors import FLRuntimeError


@pytest.fixture
def runner(tmp_path):
    archive=tmp_path/'archive'
    init_envelope(archive,run_id='stage3_test',job_id='stage3_test_sync',commit='a'*40)
    meta=json.loads((archive/'envelope.json').read_text())
    meta.update(created_at=1,completed_stages=[])
    (archive/'envelope.json').write_text(json.dumps(meta))
    calls=[]
    def backend(target,command,timeout):
        calls.append((target,command))
        return 0,'',''
    executor=module.AuditedExecutor(archive,backend)
    result=module.Stage3Runner(archive,tmp_path,executor)
    result.calls=calls
    result.job_root=tmp_path/'job'
    return result


@pytest.mark.parametrize('stage',('run-sync','validate'))
def test_forbidden_ssh_never_executes_or_opens_connection(runner,stage):
    runner.executor.stage=stage
    with pytest.raises(RuntimeError,match='SSH forbidden'):
        runner.executor.run('client1','echo forbidden')
    assert runner.calls==[]


def test_job_window_blocks_ssh_even_during_cleanup(runner):
    runner.executor.stage='stop-services'
    runner.executor.job_window=True
    with pytest.raises(RuntimeError,match='SSH forbidden'):
        runner.executor.run('client2','echo forbidden')
    assert runner.calls==[]


def test_audit_records_failed_command_exit_time_target_and_binding(runner):
    runner.executor.backend=lambda *args,**kwargs:(7,'out','err')
    rc,_,_=runner.executor.run('client1','echo test')
    assert rc==7
    record=json.loads((runner.archive/'orchestration.jsonl').read_text())
    assert record['target']=='vm1'
    assert record['returncode']==7
    assert record['run_id']==runner.run_id and record['job_id']==runner.job_id
    assert record['stage']=='preflight' and record['category']=='inspection'
    assert record['started_at']<=record['ended_at']


def test_foreground_ssh_explicitly_disables_connection_reuse(runner,monkeypatch):
    runner.executor.backend=None
    seen=[]
    class Process:
        returncode=0
        def __init__(self,argv,**kwargs):
            seen.append(argv)
            assert kwargs['start_new_session'] is True
        def communicate(self,*args,**kwargs):
            return '',''
    monkeypatch.setattr(module.subprocess,'Popen',Process)
    runner.executor.run('client2','true')
    assert seen[0][0]=='ssh'
    for option in ('ControlMaster=no','ControlPath=none','ControlPersist=no'):
        assert option in seen[0]


def test_radio_remote_commands_are_limited_to_fault_categories(runner):
    runner.executor.stage='run-radio-recovery'
    with pytest.raises(RuntimeError,match='fixed fault'):
        runner.executor.run('client2','systemctl restart bad')
    assert not runner.calls


def test_exact_fault_matchers_preserve_other_control_packets():
    command=module.fault_rules('install','stage3_test')
    assert '-i fl-c2 -p udp --dport 9000' in command
    assert '--string \'"NEW_CHANNEL_PING"\'' in command
    assert '--string \'"RADIO_SWITCH_FINALIZED"\'' in command
    assert 'PREPARE' not in command and 'COMMIT' not in command
    assert 'CONFIRMED' not in command and 'HEARTBEAT' not in command
    probe=module.fault_rules('probe','stage3_test')
    assert 'trap ' in probe and ' -N S3P' in probe
    assert ' -F S3P' in probe and ' -X S3P' in probe
    assert 'INPUT' not in probe
    remove=module.fault_rules('remove','stage3_test')
    assert ' -C INPUT' in remove and ' -D INPUT' in remove


def test_strict_stage_order_and_no_duplicate_stage(runner,monkeypatch):
    with pytest.raises(RuntimeError,match='strict order'):
        runner.stage('install')
    seen=[]
    for stage in module.STAGES:
        monkeypatch.setattr(runner,stage.replace('-','_'),lambda stage=stage:seen.append(stage))
    monkeypatch.setattr(module.Stage3Runner,'validate',lambda self:seen.append('validate'))
    monkeypatch.setattr('tests.fl_runtime.stage3_archive.seal_archive',lambda _:None)
    for stage in module.STAGES:
        runner.stage(stage)
    assert seen==list(module.STAGES)
    assert runner.meta['completed_stages']==list(module.STAGES[:-1])
    with pytest.raises(RuntimeError,match='strict order'):
        runner.stage('preflight')


def ready_status():
    return dict(server_state='IDLE',nodes={str(n):dict(reported_state='IDLE',readiness='READY',current_channel=157) for n in (1,2)})


def test_ready_gate_retries_initial_rest_connection_refusal(runner,monkeypatch):
    answers=iter([urllib.error.URLError('refused'),ready_status()])
    def rest():
        result=next(answers)
        if isinstance(result,Exception): raise result
        return result
    monkeypatch.setattr(runner,'rest',rest)
    monkeypatch.setattr(module.time,'sleep',lambda _:None)
    assert runner.wait_ready(1)==ready_status()


def test_ready_gate_requires_both_nodes_and_correct_channel():
    status=ready_status()
    assert module.Stage3Runner.ready(status)
    status['nodes']['2']['current_channel']=149
    assert not module.Stage3Runner.ready(status)
    status['nodes']['2']['current_channel']=157
    status['nodes']['2']['readiness']='OFFLINE'
    assert not module.Stage3Runner.ready(status)


def test_sync_uses_local_rest_and_keeps_ssh_blocked_through_terminal(runner,monkeypatch):
    runner.executor.stage='run-sync'
    runner.job_root.mkdir()
    summary=dict(status='succeeded',rounds_completed=2)
    (runner.job_root/'coordinator_summary.json').write_text(json.dumps(summary))
    runner.save('services-ready.json',dict(started_at=1,ready_at=2))
    requests=[]
    def rest(path='status',payload=None,timeout=20):
        assert runner.executor.job_window
        with pytest.raises(RuntimeError,match='SSH forbidden'):
            runner.executor.run('client2','true')
        requests.append((path,payload))
        return dict(status='accepted') if path=='jobs/start' else ready_status()
    monkeypatch.setattr(runner,'rest',rest)
    monkeypatch.setattr(runner,'wait_ready',lambda _:ready_status())
    runner.run_sync()
    assert not runner.executor.job_window
    assert not runner.calls
    assert requests[0][0]=='jobs/start'
    payload=requests[0][1]
    assert payload['target_nodes']==[1,2] and payload['rounds']==2
    assert payload['model_size_bytes']==41943040
    assert payload['io_timeout_seconds']==120
    assert payload['live_observation'] is True
    assert not any(path=='jobs/abort' for path,_ in requests)
    assert json.loads((runner.archive/'job/coordinator.json').read_text())==summary
    recovery=json.loads((runner.archive/'job/recovery.json').read_text())
    assert recovery['status']=='passed' and recovery['timeout_seconds']==20
    assert recovery['idle_status']==ready_status()


def test_failed_sync_aborts_locally_before_unlocking_ssh(runner,monkeypatch):
    runner.executor.stage='run-sync'
    paths=[]
    def rest(path='status',payload=None,timeout=20):
        assert runner.executor.job_window
        paths.append(path)
        if path=='jobs/start': raise RuntimeError('failed acceptance')
        return ready_status()
    monkeypatch.setattr(runner,'rest',rest)
    with pytest.raises(RuntimeError,match='failed acceptance'):
        runner.run_sync()
    assert paths==['jobs/start','jobs/abort','status']
    assert not runner.executor.job_window
    assert not runner.calls
    assert json.loads((runner.archive/'job-window.json').read_text())['outcome']=='aborted'


def test_preflight_failure_preserves_existing_services_and_archive(runner,monkeypatch):
    runner.executor.stage='preflight'
    monkeypatch.setattr(runner,'resources',lambda *args,**kwargs:dict(clean=False,residuals=['old process']))
    monkeypatch.setattr(runner,'stop_services',lambda:pytest.fail('must not stop existing services'))
    runner.failure_cleanup(RuntimeError('dirty tree'))
    assert not any('systemctl stop' in cmd for _,cmd in runner.calls)
    failure=json.loads((runner.archive/'failure.json').read_text())
    assert failure['stage']=='preflight' and failure['category']=='environment'
    assert (runner.archive/'envelope.json').exists()
    for role in module.ROLES:
        assert (runner.archive/'nodes'/role/'failure-resources.json').exists()


def test_failure_cleanup_continues_stop_after_fault_cleanup_error(runner,monkeypatch):
    runner.save('services-managed.json',dict(run_id=runner.run_id))
    runner.executor.stage='run-radio-recovery'
    seen=[]
    def remove():
        seen.append('fault-remove')
        raise RuntimeError('fault rule removal failed')
    monkeypatch.setattr(runner,'remove_fault',remove)
    monkeypatch.setattr(runner,'stop_services',lambda:seen.append('stop'))
    monkeypatch.setattr(runner,'resources',lambda *args,**kwargs:dict(clean=True))
    runner.failure_cleanup(RuntimeError('radio failure'))
    assert seen==['fault-remove','stop']
    failure=json.loads((runner.archive/'failure.json').read_text())
    assert failure['category']=='radio_recovery'
    assert json.loads((runner.archive/'cleanup-result.json').read_text())['cleanup_errors']==['fault rule removal failed']


def test_stop_failure_still_stops_all_three_nodes(runner,monkeypatch):
    runner.executor.stage='stop-services'
    runner.executor.backend=lambda target,cmd,timeout:(1,'','failed') if target=='server' else (0,'','')
    monkeypatch.setattr(runner,'resources',lambda *args,**kwargs:dict(clean=True,processes=[],tuns=[]))
    with pytest.raises(RuntimeError,match='stop commands failed'):
        runner.stop_services()
    records=[json.loads(x) for x in (runner.archive/'orchestration.jsonl').read_text().splitlines()]
    assert [r['target'] for r in records]==['vm0','vm1','vm2']


def test_radio_failure_always_removes_partially_installed_fault(runner,monkeypatch):
    runner.executor.stage='run-radio-recovery'
    monkeypatch.setattr(runner,'wait_ready',lambda _:ready_status())
    calls=[]
    def checked(target,command,timeout=30,**kwargs):
        calls.append(command)
        if '-I INPUT' in command: raise RuntimeError('partial install')
        return ''
    monkeypatch.setattr(runner.executor,'checked',checked)
    with pytest.raises(RuntimeError,match='partial install'):
        runner.run_radio_recovery()
    assert any('-D INPUT' in c for c in calls)
    assert json.loads((runner.archive/'fault-state.json').read_text())['installed'] is False


def test_resource_counts_include_actual_role_service_module(runner):
    raw={role:dict(processes=[],tuns=[]) for role in module.ROLES}
    raw['client1']['processes']=[dict(comm='python3',args=['python3','-m','wfb_ng.fl.service','client'])]
    report=runner.resource_report(raw)
    assert report['nodes']['1']['processes']['role_service']==1


def test_initialization_rejects_reused_or_unsafe_run_id(tmp_path,monkeypatch):
    monkeypatch.setattr(module.subprocess,'check_output',lambda *args,**kwargs:'a'*40+'\n')
    archive=module.initialize(tmp_path,tmp_path,run_id='stage3_unique')
    assert archive.is_dir()
    with pytest.raises(FileExistsError):
        module.initialize(tmp_path,tmp_path,run_id='stage3_unique')
    for name in ('../bad','dot.name','/tmp/unsafe'):
        with pytest.raises(FLRuntimeError,match='run_id'):
            module.initialize(tmp_path,tmp_path,run_id=name)


def test_initialization_rejects_run_id_that_overflows_job_directory(tmp_path,monkeypatch):
    monkeypatch.setattr(module.subprocess,'check_output',lambda *args,**kwargs:'a'*40+'\n')
    with pytest.raises(ValueError,match='目录'):
        module.initialize(tmp_path,tmp_path,run_id='a'*251)
    assert not (tmp_path/('a'*251)).exists()


def test_initialization_accepts_identifier_length_boundary(tmp_path,monkeypatch):
    monkeypatch.setattr(module.subprocess,'check_output',lambda *args,**kwargs:'a'*40+'\n')
    archive=module.initialize(tmp_path,tmp_path,run_id='a'*111)
    assert json.loads((archive/'envelope.json').read_text())['job_id']=='a'*111+'_sync'
    with pytest.raises(ValueError,match='目录'):
        module.initialize(tmp_path,tmp_path,run_id='a'*112)


def test_shell_has_fixed_phases_and_failure_trap():
    shell=Path(module.__file__).with_name('stage3_vm_physical_loop.sh')
    subprocess.run(['bash','-n',str(shell)],check=True)
    source=shell.read_text()
    assert 'trap cleanup EXIT' in source
    assert 'trap' in source and 'INT' in source and 'TERM' in source
    assert 'archive_manifest.json' in source and 'stage3_runner cleanup' in source
    assert 'systemd-run' not in source and 'scp ' not in source


def test_isolated_build_installs_identical_package_and_removes_worktree(runner,monkeypatch):
    import shlex
    import shutil
    calls=[]
    received={}
    def checked(target,command,timeout=30,input_data=None):
        calls.append((target,command))
        words=shlex.split(command)
        if 'worktree' in words and 'add' in words:
            Path(words[words.index('--detach')+1]).mkdir()
        if words and words[0]=='make':
            source=Path(words[words.index('-C')+1])
            (source/'deb_dist').mkdir()
            (source/'deb_dist/only.deb').write_bytes(b'one reproducible package')
        if 'worktree' in words and 'remove' in words:
            shutil.rmtree(words[-1])
        if 'cat /etc/wfb-ng-fl/node.json' in command:
            return json.dumps(dict(node_id=int(target[-1])))
        if input_data:
            received[target]=input_data
        if words and words[0]=='sha256sum':
            return module.file_sha256(str(runner.archive/'package/only.deb'))+'  package\n'
        return ''
    runner.save('topology.json',{r:dict(identity=dict(node_id=int(r[-1]))) for r in ('client1','client2')})
    monkeypatch.setattr(runner.executor,'checked',checked)
    monkeypatch.setattr(runner,'verify_package',lambda *args:dict(version='1.0',files={'entrypoint':dict(sha256='b'*64)}))
    monkeypatch.setattr(runner,'audit_package_scripts',lambda *args:None)
    monkeypatch.setattr(runner,'resources',lambda *args,**kwargs:dict(clean=True))
    monkeypatch.setattr(runner,'generate_fixtures',lambda:None)
    runner.install()
    assert received=={'client1':b'one reproducible package','client2':b'one reproducible package'}
    builds=[c for t,c in calls if c.startswith('make ')]
    assert len(builds)==1 and builds[0].endswith(' deb')
    assert str(runner.repo) not in builds[0]
    assert len([c for _,c in calls if 'dpkg --force-confold --force-confdef -i ' in c])==3
    assert any('worktree remove --force' in c for _,c in calls)
    report=json.loads((runner.archive/'installation.json').read_text())
    assert report['package']['build_isolated'] is True
    assert all(n['package_sha256']==report['package']['sha256'] for n in report['nodes'].values())


def test_ambiguous_build_fails_before_install_and_still_removes_worktree(runner,monkeypatch):
    import shlex
    import shutil
    commands=[]
    def checked(target,command,timeout=30,**kwargs):
        commands.append(command)
        words=shlex.split(command)
        if 'worktree' in words and 'add' in words:
            Path(words[words.index('--detach')+1]).mkdir()
        if words[0]=='make':
            source=Path(words[words.index('-C')+1])
            (source/'a.deb').write_bytes(b'a')
            (source/'b.deb').write_bytes(b'b')
        if 'worktree' in words and 'remove' in words:
            shutil.rmtree(words[-1])
        return ''
    monkeypatch.setattr(runner.executor,'checked',checked)
    with pytest.raises(RuntimeError,match='exactly one'):
        runner.install()
    assert not any('dpkg -i' in c for c in commands)
    assert any('worktree remove' in c for c in commands)


def test_preflight_commit_mismatch_fails_before_any_destructive_action(runner,monkeypatch):
    def checked(target,command,timeout=30,**kwargs):
        runner.calls.append((target,command))
        return 'b'*40 if 'rev-parse' in command else ''
    monkeypatch.setattr(runner.executor,'checked',checked)
    with pytest.raises(RuntimeError,match='commit parity'):
        runner.preflight()
    assert len(runner.calls)==2
    assert all('git -C' in c for _,c in runner.calls)


def test_fixture_generation_calls_only_installed_canonical_generator(runner,monkeypatch):
    calls=[]
    def checked(target,command,timeout=30,**kwargs):
        calls.append((target,command))
        n=0 if target=='server' else int(target[-1])
        return json.dumps(dict(path='fixture',size_bytes=41943040,sha256=str(n)*64))
    monkeypatch.setattr(runner.executor,'checked',checked)
    runner.generate_fixtures()
    assert len(calls)==3
    for target,command in calls:
        assert 'wfb_ng.fl.issue41_fixtures' in command
        assert 'size_bytes=41943040' in command
        assert 'cd /' in command
        assert 'generate_model_fixture' in command if target=='server' else 'generate_client_fixture' in command
    digests=json.loads((runner.archive/'fixtures.json').read_text())
    assert digests['client1']['sha256']!=digests['client2']['sha256']


def test_validation_does_not_run_commands_or_modify_original_evidence(runner,monkeypatch):
    runner.save('archive_manifest.json',dict(files={}))
    original=(runner.archive/'envelope.json').read_bytes()
    monkeypatch.setattr('tests.fl_runtime.stage3_archive.validate_archive',lambda _:dict(status='failed',errors=[dict(category='evidence')]))
    with pytest.raises(RuntimeError,match='archive validation'):
        runner.validate()
    assert not runner.calls
    assert (runner.archive/'envelope.json').read_bytes()==original
    assert json.loads((runner.archive/'validation.json').read_text())['status']=='failed'

# Reuse the archive agent's complete deterministic, real-file-shape fixture.
# Both the seal and validation execute their actual implementations here.
from wfb_ng.tests.test_fl_stage3_archive import archive as complete_archive, payloads


def test_actual_sealed_archive_validates_via_runner_without_resealing(complete_archive,tmp_path):
    from tests.fl_runtime.stage3_archive import validate_archive
    root=complete_archive
    sealed=(root/'archive_manifest.json').read_bytes()
    envelope=(root/'envelope.json').read_bytes()
    runner=module.Stage3Runner(root,tmp_path)
    runner.stage('validate')
    assert json.loads((root/'validation.json').read_text())['status']=='passed'
    assert (root/'archive_manifest.json').read_bytes()==sealed
    assert (root/'envelope.json').read_bytes()==envelope
    assert validate_archive(root)['status']=='passed'


def test_interrupted_ssh_process_group_is_terminated_and_reaped_before_return(runner,monkeypatch):
    runner.executor.backend=None
    events=[]
    class Process:
        pid=12345
        returncode=-15
        count=0
        def __init__(self,*args,**kwargs):
            assert kwargs['start_new_session'] is True
        def communicate(self,*args,**kwargs):
            self.count+=1
            events.append('communicate')
            if self.count==1: raise InterruptedError('signal')
            return '',''
    monkeypatch.setattr(module.subprocess,'Popen',Process)
    monkeypatch.setattr(module.os,'killpg',lambda pid,sig:events.append(('kill',pid,sig)))
    with pytest.raises(InterruptedError):
        runner.executor.run('client1','true')
    assert events==['communicate',('kill',12345,module.signal.SIGTERM),'communicate']
    assert json.loads((runner.archive/'orchestration.jsonl').read_text())['returncode']==125


def test_shell_signal_waits_for_child_exit_before_fallback_cleanup(tmp_path):
    import os
    import signal
    import time
    archive=tmp_path/'archive'
    archive.mkdir()
    (archive/'envelope.json').write_text('{}')
    events=tmp_path/'events'
    bin_dir=tmp_path/'bin'
    bin_dir.mkdir()
    fake=bin_dir/'python3'
    fake.write_text('''#!/usr/bin/env bash
if [[ $3 == cleanup ]]; then
    printf 'fallback-cleanup\\n' >> "$TEST_EVENTS"
    exit 1
fi
trap 'printf "child-exit\\n" >> "$TEST_EVENTS"; exit 143' TERM
printf 'child-running\\n' >> "$TEST_EVENTS"
while :; do sleep 0.05; done
''')
    fake.chmod(0o755)
    shell=Path(module.__file__).with_name('stage3_vm_physical_loop.sh')
    env=dict(os.environ,PATH=str(bin_dir)+os.pathsep+os.environ['PATH'],TEST_EVENTS=str(events))
    process=subprocess.Popen(['bash',str(shell),'run-all','--archive',str(archive)],env=env,
                             stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
    try:
        deadline=time.monotonic()+3
        while not events.exists() and time.monotonic()<deadline: time.sleep(.02)
        assert events.exists(),'fake Python child failed to launch'
        os.kill(process.pid,signal.SIGTERM)
        process.communicate(timeout=5)
        assert process.returncode==143
        assert events.read_text().splitlines()==['child-running','child-exit','fallback-cleanup']
    finally:
        if process.poll() is None:
            os.killpg(process.pid,signal.SIGKILL)
            process.communicate()


def test_cleanup_retry_keeps_original_failure_and_raw_snapshots(runner,monkeypatch):
    runner.executor.stage='run-radio-recovery'
    monkeypatch.setattr(runner,'resources',lambda *args,**kwargs:dict(clean=True))
    runner.failure_cleanup(RuntimeError('original failure'))
    original=(runner.archive/'failure.json').read_bytes()
    runner.failure_cleanup(RuntimeError('cleanup retry'))
    assert (runner.archive/'failure.json').read_bytes()==original
    retries=list((runner.archive/'cleanup-attempts').glob('*.json'))
    assert len(retries)==1
    assert json.loads(retries[0].read_text())['reason']=='cleanup retry'
    assert (runner.archive/'nodes/server/failure-daemon.log').exists()
    assert len(list((runner.archive/'nodes/server').glob('failure-daemon-*.log')))==1


def test_ready_timeout_retains_every_sample_last_status_and_rest_errors(runner,monkeypatch):
    runner.executor.stage='run-sync'
    now=[0.0]
    monkeypatch.setattr(module.time,'monotonic',lambda:now[0])
    monkeypatch.setattr(module.time,'sleep',lambda seconds:now.__setitem__(0,now[0]+seconds))
    offline=ready_status()
    offline['nodes']['2'].update(readiness='OFFLINE',last_seen=123,reported_state='IDLE')
    answers=iter([offline,urllib.error.URLError('refused'),offline,offline])
    def rest():
        value=next(answers)
        if isinstance(value,Exception): raise value
        return value
    monkeypatch.setattr(runner,'rest',rest)
    with pytest.raises(TimeoutError,match='IDLE/READY'):
        runner.wait_ready(1)
    root=runner.archive/'readiness/run-sync'
    assert json.loads((root/'last-status.json').read_text())==offline
    samples=[json.loads(line) for line in (root/'timeline.jsonl').read_text().splitlines()]
    assert len(samples)==4
    assert [sample['elapsed_seconds'] for sample in samples]==[0,.25,.5,.75]
    assert samples[0]['status']==offline and samples[-1]['status']==offline
    assert samples[1]['error_type']=='URLError' and 'refused' in samples[1]['error']
    assert all(sample['run_id']==runner.run_id and sample['job_id']==runner.job_id for sample in samples)
    result=json.loads((root/'result.json').read_text())
    assert result['status']=='failed' and result['timeout_seconds']==1
    assert result['last_status']==offline and result['samples']==4


def test_ready_success_retains_initial_errors_and_ready_sample(runner,monkeypatch):
    runner.executor.stage='start-services'
    answers=iter([urllib.error.URLError('refused'),ready_status()])
    def rest():
        value=next(answers)
        if isinstance(value,Exception): raise value
        return value
    monkeypatch.setattr(runner,'rest',rest)
    monkeypatch.setattr(module.time,'sleep',lambda _:None)
    assert runner.wait_ready(1)==ready_status()
    root=runner.archive/'readiness/start-services'
    samples=[json.loads(line) for line in (root/'timeline.jsonl').read_text().splitlines()]
    assert len(samples)==2 and samples[-1]['status']==ready_status()
    assert json.loads((root/'result.json').read_text())['status']=='passed'


def test_sync_recovery_failure_keeps_coordinator_success_and_independent_recovery(runner,monkeypatch):
    runner.executor.stage='run-sync'
    runner.job_root.mkdir()
    summary=dict(status='succeeded',rounds_completed=2)
    (runner.job_root/'coordinator_summary.json').write_text(json.dumps(summary))
    runner.save('services-ready.json',dict(started_at=1,ready_at=2))
    monkeypatch.setattr(runner,'rest',lambda path='status',*args,**kwargs:
                        dict(status='accepted') if path=='jobs/start' else ready_status())
    def fail_ready(timeout):
        assert timeout==20 and not runner.executor.job_window
        raise TimeoutError('cluster did not restore IDLE/READY')
    monkeypatch.setattr(runner,'wait_ready',fail_ready)
    with pytest.raises(TimeoutError,match='IDLE/READY'):
        runner.run_sync()
    assert json.loads((runner.archive/'job/coordinator.json').read_text())==summary
    assert json.loads((runner.archive/'job/window.json').read_text())['terminal_at']>0
    recovery=json.loads((runner.archive/'job/recovery.json').read_text())
    assert recovery['status']=='failed' and recovery['timeout_seconds']==20
    assert recovery['started_at']<=recovery['ended_at']
    assert 'IDLE/READY' in recovery['error']
    assert not (runner.archive/'job-idle.json').exists()
    assert not runner.calls


def test_failure_cleanup_collects_all_idle_logs_even_when_journal_fails(runner,monkeypatch):
    runner.executor.stage='run-sync'
    runner.save('services-managed.json',dict(run_id=runner.run_id))
    seen=[]
    monkeypatch.setattr(runner,'stop_services',lambda:seen.append('stop'))
    monkeypatch.setattr(runner,'resources',lambda *args,**kwargs:dict(clean=True))
    def checked(role,command,**kwargs):
        if 'journalctl' in command: raise RuntimeError('journal unavailable')
        assert 'wfb_uplink.log' in command
        seen.append(role)
        return role+' idle DATA/GRANT diagnostics\n'
    monkeypatch.setattr(runner.executor,'checked',checked)
    runner.failure_cleanup(RuntimeError('not ready'))
    assert seen==['stop',*module.ROLES]
    for role in module.ROLES:
        path=runner.archive/'nodes'/role/'failure-idle-link.log'
        assert path.read_text()==role+' idle DATA/GRANT diagnostics\n'
    originals={role:(runner.archive/'nodes'/role/'failure-idle-link.log').read_bytes() for role in module.ROLES}
    runner.failure_cleanup(RuntimeError('retry cleanup'))
    for role in module.ROLES:
        node=runner.archive/'nodes'/role
        assert (node/'failure-idle-link.log').read_bytes()==originals[role]
        assert len(list(node.glob('failure-idle-link-*.log')))==1


def test_sync_real_twenty_second_recovery_failure_preserves_rest_timeline(runner,monkeypatch):
    runner.executor.stage='run-sync'
    runner.job_root.mkdir()
    summary=dict(status='succeeded',rounds_completed=2)
    (runner.job_root/'coordinator_summary.json').write_text(json.dumps(summary))
    runner.save('services-ready.json',dict(started_at=1,ready_at=2))
    offline=ready_status()
    offline['nodes']['2']['readiness']='OFFLINE'
    now=[0.0]
    monkeypatch.setattr(module.time,'monotonic',lambda:now[0])
    sleeps=[]
    def sleep(seconds):
        sleeps.append(seconds)
        now[0]+=seconds
    monkeypatch.setattr(module.time,'sleep',sleep)
    def rest(path='status',*args,**kwargs):
        assert not runner.calls
        return dict(status='accepted') if path=='jobs/start' else offline
    monkeypatch.setattr(runner,'rest',rest)
    with pytest.raises(TimeoutError,match='IDLE/READY'):
        runner.run_sync()
    root=runner.archive/'readiness/run-sync'
    samples=[json.loads(line) for line in (root/'timeline.jsonl').read_text().splitlines()]
    assert len(samples)==80 and samples[-1]['elapsed_seconds']==19.75
    assert all(sample['status']==offline for sample in samples)
    assert sleeps==[.25]*80
    assert json.loads((root/'last-status.json').read_text())==offline
    result=json.loads((root/'result.json').read_text())
    assert result['timeout_seconds']==20 and result['elapsed_seconds']==20
    assert result['status']=='failed'
    assert json.loads((runner.archive/'job/coordinator.json').read_text())==summary
    assert json.loads((runner.archive/'job/recovery.json').read_text())['status']=='failed'
    assert not runner.executor.job_window


def test_server_collect_tree_keeps_archive_writable_and_root_source_unchanged(runner,tmp_path):
    import os
    source=tmp_path/'root-owned-source'
    source.mkdir()
    (source/'nested').mkdir()
    (source/'nested/config.json').write_bytes(b'{"fixture":true}\n')
    (source/'model.bin').write_bytes(b'copied server artifact')
    subprocess.run(['sudo','-n','chown','-R','0:0',str(source)],check=True)
    destination=runner.archive/'server'
    sibling=runner.archive/'untouched-root-sibling'
    sibling.mkdir()
    subprocess.run(['sudo','-n','chown','0:0',str(sibling)],check=True)
    def snapshot(root):
        return {str(path.relative_to(root)):(path.stat().st_uid,path.stat().st_gid,
                    path.stat().st_mode,path.read_bytes() if path.is_file() else None)
                for path in [root,*sorted(root.rglob('*'))]}
    original=snapshot(source)
    runner.executor.backend=None
    try:
        module.collect_tree(runner.executor, 'server',str(source),destination)
        assert snapshot(source)==original
        assert sibling.stat().st_uid==0 and sibling.stat().st_gid==0
        for path in [destination,*destination.rglob('*')]:
            assert (path.stat().st_uid,path.stat().st_gid)==(os.getuid(),os.getgid())
        (destination/'models').mkdir()
        (destination/'models/1.bin').write_bytes(b'follow-up collection')
        (destination/'nested/config.json').write_bytes(b'caller can update copied evidence')
        assert (destination/'model.bin').read_bytes()==b'copied server artifact'
    finally:
        subprocess.run(['sudo','-n','chown','-R',f'{os.getuid()}:{os.getgid()}',
                        str(source),str(destination),str(sibling)],check=True)


def test_server_single_files_after_tree_copy_are_readable_without_changing_root600_sources(runner,tmp_path):
    import hashlib
    import os
    import stat
    source=tmp_path/'root source'
    source.mkdir()
    files={'job_config.json':b'{"io_timeout_seconds":120}\n',
           'output model.bin':b'root-owned model fixture'}
    for name,content in files.items():
        (source/name).write_bytes(content)
        (source/name).chmod(0o600)
    subprocess.run(['sudo','-n','chown','-R','0:0',str(source)],check=True)
    before={name:(source/name).stat() for name in files}
    destination=runner.archive/'server'
    runner.executor.backend=None
    try:
        module.collect_tree(runner.executor, 'server',str(source),destination)
        for name,relative in [('job_config.json','job_config.json'),('output model.bin','models/1.bin')]:
            target=destination/relative
            module.collect_server_file(runner.executor, str(source/name),target)
            assert module.file_sha256(str(target))==hashlib.sha256(files[name]).hexdigest()
            copied=target.stat()
            assert (copied.st_uid,copied.st_gid,stat.S_IMODE(copied.st_mode))==(os.getuid(),os.getgid(),0o600)
            original=(source/name).stat()
            assert (original.st_uid,original.st_gid,original.st_mode,original.st_mtime_ns)==(
                before[name].st_uid,before[name].st_gid,before[name].st_mode,before[name].st_mtime_ns)
            assert subprocess.check_output(['sudo','-n','cat','--',str(source/name)])==files[name]
    finally:
        subprocess.run(['sudo','-n','chown','-R',f'{os.getuid()}:{os.getgid()}',str(source),str(destination)],check=True)


def test_collect_routes_server_config_and_models_through_owned_file_copy(runner,monkeypatch):
    monkeypatch.setattr(runner,'rest',ready_status)
    monkeypatch.setattr(runner,'resources',lambda *args,**kwargs:dict(processes=[],tuns=[]))
    monkeypatch.setattr(runner,'collect_wireless',lambda role:None)
    monkeypatch.setattr(module,'collect_tree',lambda executor,role,source,dest:dest.mkdir(parents=True,exist_ok=True))
    runner.save('job/coordinator.json',dict(rounds=[dict(output_model_path='/root/output-model.bin')]))
    copies=[]
    def copy(executor,source,dest):
        copies.append((str(source),dest.relative_to(runner.archive).as_posix()))
        if len(copies)==2: raise RuntimeError('copy boundary reached')
    monkeypatch.setattr(module,'collect_server_file',copy)
    with pytest.raises(RuntimeError,match='copy boundary reached'):
        runner.collect()
    assert copies==[(str(runner.job_root/'job_config.json'),'server/job_config.json'),
                    ('/root/output-model.bin','server/models/1.bin')]
    assert not any('sudo -n cp ' in command for _,command in runner.calls)
