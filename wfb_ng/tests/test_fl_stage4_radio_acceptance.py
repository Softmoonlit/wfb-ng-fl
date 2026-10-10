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
        iw=f'channel {channel} (x), width: {width} MHz, center1: {center} MHz\n txpower -11.00 dBm',
        sockets=f'UNCONN 0 0 {ip}:{port} 0.0.0.0:*')


@pytest.mark.parametrize('node', [0, 1, 2, 7])
@pytest.mark.parametrize('channel', [157, 165])
def test_real_radio_readback(node, channel):
    validate_observation(observation(node, channel), {**CHANGED, 'channel': channel}, node)


@pytest.mark.parametrize('field,value', [
    ('iw', 'channel 157 (x), width: 20 MHz, center1: 5785 MHz\n txpower -11.00 dBm'),
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
    runner.apply(CHANGED, 'mcs-power')
    assert calls == [('radio/config/validate', {'config': CHANGED}),
                     ('radio/config/apply', {'confirmation_token': 'bound-token', 'confirm_risk': False})]
    assert json.loads((tmp_path / 'control-plane/mcs-power-result.json').read_text())['status'] == 'finalized'


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


def test_offline_recheck_rejects_changed_saved_radio(tmp_path):
    from tests.fl_runtime.stage4_radio_acceptance import BASELINE, recheck
    from wfb_ng.fl.artifacts import write_json_atomic
    write_json_atomic(tmp_path / 'summary/request.json', dict(clients={'1': 'vm1'}, include_rollback=False))
    node = dict(node_id=1, reported_state='IDLE', state='idle', readiness='READY',
                last_heartbeat_ago_seconds=1, current_channel=157, txpower_dbm=12, uplink_mcs=6)
    state = dict(current_job=None, server=dict(start_blockers=[], radio=BASELINE), nodes=[node])
    write_json_atomic(tmp_path / 'control-plane/baseline-ready.json', state)
    for role, number in [('server', 0), ('client1', 1)]:
        row = observation(number)
        row['iw'] = row['iw'].replace('-11.00', '-12.00')
        row['processes'][0]['argv'][2] = str(6 if number else 3)
        if role == 'client1':
            row['sockets'] = 'UNCONN 0 0 10.1.1.1:9000 0.0.0.0:*'
        write_json_atomic(tmp_path / f'lifecycle/baseline-{role}.json', row)
    with pytest.raises(ValueError, match='socket bind'):
        recheck(tmp_path)


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
