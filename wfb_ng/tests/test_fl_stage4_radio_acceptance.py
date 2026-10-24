"""Fail-closed gates for the supplementary real-radio executor (no hardware)."""
from copy import deepcopy
import json

import pytest

from tests.fl_runtime.stage4_radio_acceptance import (
    CHANGED, Executor, RadioRunner, ready, validate_observation, validate_rebuild,
)


def observation(node=0, channel=157):
    ip, port = ('0.0.0.0', 9000) if node else ('10.80.0.1', 9001)
    width, center = (20, 5825) if channel == 165 else (40, 5795)
    return dict(daemon_pid=10, processes=[dict(pid=20, start_ticks=30, argv=[
        'wfb_v6_uplink', '--radio-mcs-index', str(5 if node else 4),
        '--radio-bandwidth', str(width)])], tun=[dict(ifindex=7)],
        iw=f'channel {channel} (x), width: {width} MHz, center1: {center} MHz\n txpower -12.00 dBm',
        sockets=f'UNCONN 0 0 {ip}:{port} 0.0.0.0:*')


@pytest.mark.parametrize('node', [0, 1, 2, 7])
@pytest.mark.parametrize('channel', [157, 165])
def test_real_radio_readback(node, channel):
    validate_observation(observation(node, channel), {**CHANGED, 'channel': channel}, node)


@pytest.mark.parametrize('field,value', [
    ('iw', 'channel 157 (x), width: 20 MHz, center1: 5785 MHz\n txpower -12.00 dBm'),
    ('iw', 'channel 157 (x), width: 40 MHz, center1: 5795 MHz\n txpower 20.00 dBm'),
    ('sockets', 'UNCONN 0 0 0.0.0.0:9001 0.0.0.0:*'),
    ('processes', []), ('daemon_pid', 0), ('tun', []),
])
def test_cached_web_config_cannot_replace_physical_evidence(field, value):
    row = observation()
    row[field] = value
    with pytest.raises(ValueError):
        validate_observation(row, CHANGED, 0)


def test_rebuild_requires_both_new_process_and_new_tun():
    before = {'server': observation()}
    after = deepcopy(before)
    with pytest.raises(ValueError, match='process did not rebuild'):
        validate_rebuild(before, after)
    after['server']['processes'][0]['start_ticks'] += 1
    with pytest.raises(ValueError, match='TUN did not rebuild'):
        validate_rebuild(before, after)
    after['server']['tun'][0]['ifindex'] += 1
    validate_rebuild(before, after)


def test_executor_forbids_ssh_even_for_extra_clients(tmp_path):
    executor = Executor(tmp_path, {'client7': 'vm7'})
    executor.job_window = True
    with pytest.raises(RuntimeError, match='SSH forbidden'):
        executor.run('client7', 'true')


def test_web_confirmation_uses_actual_token_endpoint(tmp_path, monkeypatch):
    runner = RadioRunner(tmp_path, 'http://192.168.1.1:8080', {1: 'vm1', 2: 'vm2'})
    calls = []
    def web(path, body):
        calls.append((path, body))
        return ({'confirmation': {'token': 'bound-token'}, 'warning': None}
                if path.endswith('validate') else {'status': 'finalized'})
    monkeypatch.setattr(runner, 'web', web)
    runner.apply(CHANGED, 'mcs-only')
    assert calls == [('radio/config/validate', {'config': CHANGED}),
                     ('radio/config/apply', {'confirmation_token': 'bound-token', 'confirm_risk': False})]
    assert json.loads((tmp_path / 'control-plane/mcs-only-result.json').read_text())['status'] == 'finalized'


def test_missing_execute_cannot_send_request(monkeypatch):
    from tests.fl_runtime.stage4_radio_acceptance import main
    monkeypatch.setattr('tests.fl_runtime.stage4_radio_acceptance._request',
                        lambda *args, **kwargs: pytest.fail('request without --execute'))
    with pytest.raises(SystemExit):
        main(['--web-url', 'http://192.168.1.1:8080', '--client', '1=vm1'])


def test_ready_rejects_unexpected_registered_nodes():
    node = dict(node_id=1, reported_state='IDLE', state='idle', readiness='READY', last_heartbeat_ago_seconds=1)
    state = dict(current_job=None, server={'start_blockers': []}, nodes=[node])
    assert ready(state, [1])
    assert not ready(state, [1, 2])
    state['nodes'].append({**node, 'node_id': 7})
    assert not ready(state, [1])



def test_actual_uftp_sample_rejects_other_job_and_accepts_real_rate(tmp_path):
    import uuid
    from tests.fl_runtime.stage4_hardware_archive import validate_transfer_start
    from tests.fl_runtime.stage4_radio_acceptance import option
    rid = str(uuid.uuid4())
    root = f'/tmp/wfb-ng-fl/server/job_web-one_role/rounds/{rid}'
    sample = dict(job_id='web-one', pid=100, start_ticks=200, observed_at=1,
                  cwd=root, argv=['uftp', '-D', rid, '-L', root + '/uftp-real.log',
                  '-S', root + '/status', '-p', '1044', '-I', '10.80.0.1',
                  '-M', '239.80.41.1', '-P', '239.80.41.2', '-R', '18000',
                  'model.bin', 'model.manifest.json'])
    validate_transfer_start(sample, 'web-one')
    assert option(sample['argv'], '-R') == '18000'
    with pytest.raises(ValueError):
        validate_transfer_start(sample, 'web-two')


def test_cleanup_resolves_original_key_and_aborts_then_restores(tmp_path, monkeypatch):
    runner = RadioRunner(tmp_path, 'http://192.168.1.1:8080', {1: 'vm1'})
    body = dict(model_sha256='a' * 64, target_nodes=[1], rounds=2)
    runner.pending_create = body
    runner.executor.job_window = True
    runner.baseline_verified = True
    node = dict(node_id=1, reported_state='IDLE', state='idle', readiness='READY', last_heartbeat_ago_seconds=1)
    calls = []
    def web(path, payload=None, **kwargs):
        calls.append((path, payload, kwargs))
        if path == 'jobs':
            assert payload == body and kwargs['headers']['Idempotency-Key'] == runner.run_id
            return dict(status='accepted', job_id='web-owned', idempotency_key=runner.run_id)
        if path.endswith('/abort'):
            return {'status': 'accepted'}
        if len([c for c in calls if c[0] == 'state']) == 1:
            return {'current_job': {'job_id': 'web-owned'}}
        return dict(current_job=None, recent_job=dict(job_id='web-owned', recovery_state='ready'),
                    server=dict(start_blockers=[], radio=CHANGED), nodes=[node])
    restored = []
    monkeypatch.setattr(runner, 'web', web)
    monkeypatch.setattr(runner, 'apply', lambda config, name: restored.append((config, name)))
    monkeypatch.setattr(runner, 'wait_ready', lambda *args: None)
    monkeypatch.setattr(runner, 'capture', lambda *args: None)
    runner.cleanup()
    assert any(c[0] == 'jobs/web-owned/abort' for c in calls)
    assert runner.pending_create is None and runner.active_job_id is None
    assert not runner.executor.job_window
    assert restored[0][1] == 'failure-restore-baseline'


def test_failure_evidence_never_uses_ssh_with_active_job(tmp_path, monkeypatch):
    runner = RadioRunner(tmp_path, 'http://192.168.1.1:8080', {1: 'vm1', 2: 'vm2'})
    runner.executor.job_window = True
    monkeypatch.setattr(runner, 'web', lambda path: {'current_job': {'job_id': 'web-active'}})
    monkeypatch.setattr(runner.executor, 'checked', lambda *args, **kwargs: pytest.fail('SSH during job'))
    report = runner.failure_evidence('failure')
    assert report['ssh_skipped']
    assert json.loads((tmp_path / 'control-plane/failure-state.json').read_text())['current_job']['job_id'] == 'web-active'


def test_failure_collection_continues_after_one_bad_journal(tmp_path, monkeypatch):
    runner = RadioRunner(tmp_path, 'http://192.168.1.1:8080', {1: 'vm1', 2: 'vm2'})
    monkeypatch.setattr(runner, 'web', lambda path: {'current_job': None})
    visited = []
    def checked(role, command, **kwargs):
        visited.append(role)
        if role == 'client1':
            raise RuntimeError('unreachable')
        return 'original journal'
    monkeypatch.setattr(runner.executor, 'checked', checked)
    monkeypatch.setattr(runner, 'production_identity', lambda role: runner.save(f'summary/{role}-production.json', {'version': 'test'}))
    report = runner.failure_evidence('failure')
    assert visited == ['server', 'client1', 'client2']
    assert report['errors']['client1_journal'] == 'unreachable'
    assert (tmp_path / 'control-plane/failure-client2-journal.txt').read_text() == 'original journal'

@pytest.fixture
def saved_radio_archive(tmp_path):
    import hashlib
    import uuid
    from tests.fl_runtime.stage4_radio_acceptance import BASELINE
    from wfb_ng.fl.artifacts import write_json_atomic
    def save(name, value):
        write_json_atomic(tmp_path / name, value)
    run_id = 'stage4-radio-test'
    commit = 'a' * 40
    digest = 'b' * 64
    save('summary/request.json', dict(run_id=run_id, clients={'1': 'vm1'},
                                    include_rollback=False, model_sha256=digest))
    save('summary/run-identity.json', dict(run_id=run_id, commit=commit, source_sha256={name: 'c' * 64 for name in ('server_daemon', 'client_daemon', 'console', 'console_http', 'control', 'radio', 'service', 'transport')}))
    production = dict(build_identity=dict(schema_version=1, commit=commit), config_commit=commit,
                      version='test-version', daemon_pid=10, daemon_argv=['installed-daemon'],
                      files={name: dict(path='/usr/lib/' + name, sha256='c' * 64)
                             for name in ('server_daemon', 'client_daemon', 'console', 'console_http',
                                          'control', 'radio', 'service', 'transport', 'wfb_v6_uplink')})
    for role in ('server', 'client1'):
        save(f'summary/{role}-checkout.json', dict(commit=commit, dirty=''))
        save(f'summary/{role}-production.json', production)
    save('summary/client1-identity.json', {'node_id': 1})
    for index, (name, config) in enumerate([
        ('baseline', BASELINE), ('mcs-only', CHANGED),
        ('power-only-13', {**CHANGED, 'radio_txpower_dbm': 13}), ('power-restore-12', CHANGED),
        ('165-HT20', {**CHANGED, 'channel': 165}), ('157-HT40', CHANGED),
        ('post-job', CHANGED), ('restored-baseline', BASELINE)]):
        node = dict(node_id=1, reported_state='IDLE', state='idle', readiness='READY', last_heartbeat_ago_seconds=1,
                    current_channel=config['channel'], txpower_dbm=config['radio_txpower_dbm'], uplink_mcs=config['uplink_mcs'])
        save(f'control-plane/{name}-ready.json', dict(current_job=None, server=dict(start_blockers=[], radio=config), nodes=[node]))
        for role, n in [('server', 0), ('client1', 1)]:
            row = observation(n, config['channel'])
            row['iw'] = row['iw'].replace('-12.00', '-' + str(config['radio_txpower_dbm']) + '.00')
            row['processes'][0]['argv'][2] = str(config['uplink_mcs'] if n else config['downlink_mcs'])
            # Power-only transactions may keep the same process and TUN.
            generation = 1 if name in ('power-only-13', 'power-restore-12') else index
            row['processes'][0]['pid'] += generation
            row['tun'][0]['ifindex'] += generation
            save(f'lifecycle/{name}-{role}.json', row)
        result_name = 'restore-baseline' if name == 'restored-baseline' else name
        save(f'control-plane/{result_name}-result.json', dict(status='finalized', effective_config=config))
    job_id = 'web-owned'
    body = dict(model_sha256=digest, target_nodes=[1], rounds=2)
    save('data-plane/job-request.json', dict(request=body, idempotency_key=run_id))
    save('data-plane/job-accepted.json', dict(job_id=job_id, status='accepted', idempotency_key=run_id))
    save('data-plane/job-terminal.json', dict(current_job=None, server={'state': 'idle'},
        recent_job=dict(job_id=job_id, recovery_state='ready', execution_result='succeeded', rounds_completed=2, **body),
        nodes=[dict(node_id=1, state='idle', readiness='READY')]))
    rid = str(uuid.uuid4())
    root = f'/tmp/wfb-ng-fl/server/job_{job_id}_role/rounds/{rid}'
    save('data-plane/uftp-argv.json', dict(job_id=job_id, pid=100, start_ticks=200, observed_at=1,
        cwd=root, argv=['uftp', '-D', rid, '-L', root + '/uftp-real.log', '-S', root + '/status',
                       '-p', '1044', '-I', '10.80.0.1', '-M', '239.80.41.1', '-P', '239.80.41.2',
                       '-R', '18000', 'model.bin', 'model.manifest.json']))
    log = tmp_path / f'data-plane/server-role/rounds/{rid}/uftp-real.log'
    log.parent.mkdir(parents=True)
    log.write_text('original UFTP log')
    save('summary/verdict.json', {'status': 'passed'})
    def seal():
        inventory = {str(p.relative_to(tmp_path)): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in tmp_path.rglob('*') if p.is_file() and p != tmp_path / 'summary/seal.json'}
        save('summary/seal.json', dict(run_id=run_id, files=inventory))
    seal()
    return tmp_path, save, seal


def test_recheck_requires_independent_saved_facts_even_after_resealing(saved_radio_archive):
    from tests.fl_runtime.stage4_radio_acceptance import recheck
    root, save, seal = saved_radio_archive
    assert recheck(root)['status'] == 'passed'
    value = json.loads((root / 'data-plane/job-terminal.json').read_text())
    value['recent_job']['job_id'] = 'web-other'
    save('data-plane/job-terminal.json', value)
    seal()
    assert recheck(root)['status'] == 'failed'


def test_recheck_checks_power_readback_and_installed_identity(saved_radio_archive):
    from tests.fl_runtime.stage4_radio_acceptance import recheck
    root, save, seal = saved_radio_archive
    path = 'lifecycle/power-only-13-client1.json'
    value = json.loads((root / path).read_text())
    value['iw'] = value['iw'].replace('-13.00', '-12.00')
    save(path, value)
    seal()
    assert 'power readback' in ';'.join(recheck(root)['errors'])


def test_recheck_rejects_unsealed_or_recorded_failed_run(saved_radio_archive):
    from tests.fl_runtime.stage4_radio_acceptance import recheck
    root, save, seal = saved_radio_archive
    save('summary/verdict.json', dict(status='failed', error='COMMIT rollback'))
    seal()
    assert recheck(root)['status'] == 'failed'
    (root / 'summary/seal.json').unlink()
    assert recheck(root)['status'] == 'failed'


def test_recheck_rejects_installed_version_identity_mismatch(saved_radio_archive):
    from tests.fl_runtime.stage4_radio_acceptance import recheck
    root, save, seal = saved_radio_archive
    path = 'summary/client1-production.json'
    value = json.loads((root / path).read_text())
    value['config_commit'] = 'f' * 40
    save(path, value)
    seal()
    assert 'production commit mismatch' in ';'.join(recheck(root)['errors'])


def test_production_probe_is_valid_python_without_running_hardware():
    from tests.fl_runtime.stage4_radio_acceptance import PRODUCTION_PROBE
    compile(PRODUCTION_PROBE.replace('UNIT', repr('wfb-fl-server-daemon.service')), '<production-probe>', 'exec')
