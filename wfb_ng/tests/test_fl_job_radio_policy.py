"""票10作业无线策略宿主公开接线；全部网络显式隔离到loopback临时端口。"""
from dataclasses import replace
import json
from pathlib import Path
import sys
from typing import Any
import threading
import time

import pytest

from wfb_ng.fl.control import ClientNodeState, NodeHeartbeat, RadioSwitchResult
from wfb_ng.fl.errors import FLRuntimeError
from wfb_ng.fl.server_daemon import ServerDaemonConfig, ServerState, load_server_config
from wfb_ng.tests.test_fl_training_host import (
    TrainingHost, initial_model, isolated_socket_boundary, isolated_control_address,
)


def test_radio_policy_entry_is_optional_deployment_config_and_json(tmp_path):
    assert ServerDaemonConfig().radio_policy_entry is None
    path = tmp_path / 'server.json'
    path.write_text(json.dumps({'radio_policy_entry': 'deployment:policy'}))
    assert load_server_config(str(path)).radio_policy_entry == 'deployment:policy'
    invalid: Any
    for invalid in ('', 'missing-colon', True, 12):
        with pytest.raises(FLRuntimeError):
            ServerDaemonConfig(radio_policy_entry=invalid)


@pytest.fixture
def policy_host(tmp_path, monkeypatch, initial_model, isolated_control_address):
    hosts = []

    def make(policy, *, rounds=2, auto_round_ready=True):
        host = TrainingHost(tmp_path, monkeypatch, initial_model,
                            control_address=isolated_control_address,
                            auto_round_ready=auto_round_ready, budget=2.5, round_budget=2.0)
        module = sys.modules['training_host_deployment_fixture']
        setattr(module, 'radio_policy', policy)
        host.daemon.config = replace(host.daemon.config,
                                    radio_policy_entry=f'{module.__name__}:radio_policy')
        host.payload['rounds'] = rounds
        hosts.append(host)
        return host

    yield make
    for host in reversed(hosts):
        host.close()


def test_policy_runs_after_initial_evaluation_and_only_between_completed_rounds(policy_host):
    calls = []

    def policy(config, context):
        calls.append(context.round_index)
        assert context.phase == 'radio_policy'
        assert context.job_id == host.payload['job_id']
        assert context.target_nodes == (1, 2)
        if context.round_index == 0:
            assert host.evaluation_exited.is_set()
            assert host.daemon.active_job is None
            assert not host.announced.is_set()
            assert host.daemon.server_state == ServerState.PREPARING
        else:
            assert host.runtime.wait_calls == 1
            assert len(host.runtime.models) == 1
            assert host.daemon.server_state == ServerState.RUNNING

    host = policy_host(policy)
    host.launch()
    recent = host.assert_succeeded()
    assert calls == [0, 1]
    assert recent['algorithm']['radio_policy_entry'].endswith(':radio_policy')


def test_request_cannot_select_deployment_radio_policy(policy_host):
    host = policy_host(lambda config, context: None)
    with pytest.raises(FLRuntimeError) as caught:
        host.daemon.start_job(dict(host.payload, radio_policy_entry='other:policy'))
    assert caught.value.error_code == 'invalid_job_config'


def add_online_node(host, node):
    for index, state in enumerate((ClientNodeState.HUNTING, ClientNodeState.IDLE)):
        hb = NodeHeartbeat(node_id=node, state=state.value, elapsed_ms=0,
                           current_channel=host.daemon.radio_config.channel,
                           txpower_dbm=host.daemon.radio_config.radio_txpower_dbm,
                           uplink_mcs=host.daemon.radio_config.uplink_mcs,
                           downlink_mcs=host.daemon.radio_config.downlink_mcs,
                           bandwidth_mhz=host.daemon.radio_config.bandwidth_mhz,
                           uftp_rate_kbps=host.daemon.radio_config.uftp_rate_kbps,
                           timestamp_ms=int(time.time() * 1000) + index)
        host.daemon.control_plane.handle_datagram(
            hb.to_bytes(), (f'127.0.0.{10 + node}', host.control_address[1]))


def test_job_callback_freezes_union_and_records_confirmed_config_without_idle_or_disk(
        policy_host, monkeypatch):
    from wfb_ng.fl.radio import validate_radio_patch
    calls = []
    reports = []

    def policy(config, context):
        reports.append(context.configure_radio({'radio_txpower_dbm': 13 + context.round_index}))

    host = policy_host(policy)
    add_online_node(host, 3)
    startup = host.daemon.config
    startup_path = Path(host.daemon.config.work_dir) / 'startup.json'
    startup_path.parent.mkdir(parents=True, exist_ok=True)
    startup_path.write_text('unchanged deployment')
    host.daemon.config = replace(startup, startup_config_path=str(startup_path))

    def transaction(patch, target_nodes, *, job_id, stop_event):
        assert job_id == host.payload['job_id']
        assert stop_event.is_set() is False
        before = host.daemon.control_plane.active_radio_config
        calls.append((list(target_nodes), host.daemon.server_state))
        assert list(target_nodes) == [1, 2, 3]
        add_online_node(host, 4)
        assert list(target_nodes) == [1, 2, 3], '已受理事务不加入后到节点'
        updated = validate_radio_patch(patch, before)
        host.daemon.control_plane.active_radio_config = updated
        return RadioSwitchResult(success=True, session_id=f'fixture-{len(calls)}',
            target_channel=updated.channel, applied_patch=dict(patch), status='finalized',
            effective_config=updated, before_config=before, target_nodes=list(target_nodes),
            confirmed_nodes=list(target_nodes), job_id=job_id)

    # 第二次事务的在线union应包含首次事务后上线的4号节点。
    def controlled_transaction(patch, target_nodes, **kwargs):
        if calls:
            assert list(target_nodes) == [1, 2, 3, 4]
            before = host.daemon.control_plane.active_radio_config
            updated = validate_radio_patch(patch, before)
            calls.append((list(target_nodes), host.daemon.server_state))
            host.daemon.control_plane.active_radio_config = updated
            return RadioSwitchResult(success=True, session_id='fixture-2',
                target_channel=updated.channel, applied_patch=dict(patch), status='finalized',
                effective_config=updated, before_config=before, target_nodes=list(target_nodes),
                confirmed_nodes=list(target_nodes), job_id=kwargs['job_id'])
        return transaction(patch, target_nodes, **kwargs)

    monkeypatch.setattr(host.daemon.control_plane, 'reconfigure_radio', controlled_transaction)
    host.launch()
    recent = host.assert_succeeded()
    assert calls == [([1, 2, 3], ServerState.PREPARING), ([1, 2, 3, 4], ServerState.RUNNING)]
    assert len(recent['radio_results']) == 2
    for index, fact in enumerate(recent['radio_results']):
        assert fact['job_id'] == host.payload['job_id']
        assert fact['round_index'] == index
        assert fact['request_id']
        assert fact['effective_config']['radio_txpower_dbm'] == 13 + index
        assert fact['unconfirmed_nodes'] == []
        assert fact['recovery_state'] == 'ready'
    assert host.daemon.radio_config.radio_txpower_dbm == 14
    assert host.daemon.config.radio_txpower_dbm == startup.radio_txpower_dbm
    assert startup_path.read_text() == 'unchanged deployment'
    assert reports[0]['target_nodes'] == (1, 2, 3)


@pytest.mark.parametrize('phase', [0, 1])
@pytest.mark.parametrize('unknown', [False, True])
def test_transaction_failure_is_fatal_even_if_policy_catches_and_keeps_radio_facts(
        policy_host, monkeypatch, phase, unknown):
    from wfb_ng.fl.radio import validate_radio_patch
    caught = []

    def policy(config, context):
        if context.round_index == phase:
            try:
                context.configure_radio({'radio_txpower_dbm': 13})
            except FLRuntimeError as exc:
                caught.append(exc.error_code)

    host = policy_host(policy)

    def failed_transaction(patch, target_nodes, *, job_id, stop_event):
        before = host.daemon.control_plane.active_radio_config
        target = validate_radio_patch(patch, before)
        if unknown:
            host.daemon.control_plane.active_radio_config = target
            host.daemon.control_plane.radio_error = True
        return RadioSwitchResult(success=False, session_id='fixture-failed',
            target_channel=target.channel, applied_patch=dict(patch),
            status='radio_error' if unknown else 'rolled_back',
            effective_config=None if unknown else before, before_config=before,
            error_message='fixture confirm missing', failed_phase='CONFIRMED' if unknown else 'COMMIT',
            unresponsive_nodes=[2], target_nodes=list(target_nodes), confirmed_nodes=[1], job_id=job_id)

    monkeypatch.setattr(host.daemon.control_plane, 'reconfigure_radio', failed_transaction)
    host.launch()
    assert host.start_done.wait(3)
    if phase == 1:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            recent = host.daemon.get_status_report()['recent_job']
            if recent and recent['recovery_state'] != 'recovering':
                break
            threading.Event().wait(0.005)
    else:
        assert host.errors and host.errors[0].error_code == 'radio_transaction_failed'
        recent = host.daemon.get_status_report()['recent_job']
    assert caught == ['radio_transaction_failed']
    assert recent and recent['execution_result'] == 'failed'
    assert len(host.runtime.models) == phase
    fact = recent['radio_results'][0]
    assert fact['round_index'] == phase
    assert fact['before_config']['radio_txpower_dbm'] != 13
    assert fact['confirmed_nodes'] == [1]
    assert fact['unconfirmed_nodes'] == [2]
    if unknown:
        assert fact['effective_config'] is None
        assert fact['recovery_state'] == 'blocked'
        assert host.daemon.get_status_report()['radio'] is None
        assert host.daemon.server_state == ServerState.RADIO_ERROR
        assert recent['recovery_state'] == 'blocked'
    else:
        assert fact['effective_config'] == fact['before_config']
        assert host.daemon.radio_config.radio_txpower_dbm == fact['before_config']['radio_txpower_dbm']
    if unknown:
        with pytest.raises(FLRuntimeError):
            host.daemon.start_job(dict(host.payload, job_id='conflicting-after-failure'))


def test_preflight_patch_rejections_are_handleable_without_control_transaction(policy_host, monkeypatch):
    rejected = []

    def policy(config, context):
        for patch in ({'channel': 149}, {'target_nodes': [1]}, {'radio_txpower_dbm': True},
                      {'bandwidth_mhz': 20}, {'uftp_rate_kbps': -1}):
            try:
                context.configure_radio(patch)
            except FLRuntimeError as exc:
                rejected.append(exc.error_code)

    host = policy_host(policy, rounds=1)

    def no_transaction(*args, **kwargs):
        raise AssertionError('参数拒绝不能发起跨节点事务')

    monkeypatch.setattr(host.daemon.control_plane, 'reconfigure_radio', no_transaction)
    host.launch()
    recent = host.assert_succeeded()
    assert len(rejected) == 5
    assert recent['radio_results'] == []


@pytest.mark.parametrize('phase', [0, 1])
def test_stop_cancels_job_bound_transaction_and_waits_for_actual_policy_exit(
        policy_host, monkeypatch, phase):
    entered = threading.Event()
    cancelled = threading.Event()
    release = threading.Event()
    exited = threading.Event()
    stop_done = threading.Event()

    def policy(config, context):
        if context.round_index == phase:
            context.configure_radio({'radio_txpower_dbm': 13})

    host = policy_host(policy)

    def blocked_transaction(patch, target_nodes, *, job_id, stop_event):
        entered.set()
        assert stop_event.wait(2), '宿主必须将同一停止事件传入事务'
        cancelled.set()
        release.wait()
        before = host.daemon.control_plane.active_radio_config
        exited.set()
        return RadioSwitchResult(success=False, session_id='fixture-cancelled',
            target_channel=before.channel, applied_patch=dict(patch), status='rolled_back',
            effective_config=before, before_config=before, target_nodes=list(target_nodes),
            confirmed_nodes=list(target_nodes), failed_phase='PREPARE',
            error_message='fixture cancelled and rolled back', job_id=job_id)

    monkeypatch.setattr(host.daemon.control_plane, 'reconfigure_radio', blocked_transaction)
    host.launch()
    assert entered.wait(2)
    result = []

    def stop():
        result.append(host.daemon.abort_job('operator_stop'))
        stop_done.set()

    stopper = threading.Thread(target=stop)
    stopper.start()
    try:
        assert cancelled.wait(2)
        assert not exited.is_set()
        assert not stop_done.is_set(), '不能用已发停止请求冒充策略／事务已退出'
        if phase == 0:
            assert not host.announced.is_set()
        else:
            recent = host.daemon.get_status_report()['recent_job']
            assert recent['execution_result'] == 'aborted'
            assert recent['recovery_state'] == 'recovering'
    finally:
        release.set()
        stopper.join(3)
    assert stop_done.is_set()
    assert exited.is_set()
    assert host.start_done.wait(2)
    assert len(host.runtime.models) == phase
    assert result[0]['status'] == 'aborted'
    recent = host.daemon.get_status_report()['recent_job']
    assert recent['radio_results'][0]['transaction_state'] == 'rolled_back'
    assert recent['radio_results'][0]['effective_config'] is not None


def test_report_barrier_precedes_policy_and_next_publish_waits_transaction(policy_host, monkeypatch):
    from wfb_ng.fl.radio import validate_radio_patch
    entered = threading.Event()
    release = threading.Event()

    def policy(config, context):
        if context.round_index:
            context.configure_radio({'radio_txpower_dbm': 13})

    host = policy_host(policy, auto_round_ready=False)

    def transaction(patch, target_nodes, *, job_id, stop_event):
        entered.set()
        release.wait()
        before = host.daemon.control_plane.active_radio_config
        updated = validate_radio_patch(patch, before)
        host.daemon.control_plane.active_radio_config = updated
        return RadioSwitchResult(success=True, session_id='fixture-report-boundary',
            target_channel=before.channel, applied_patch=dict(patch), status='finalized',
            effective_config=updated, before_config=before, target_nodes=list(target_nodes),
            confirmed_nodes=list(target_nodes), job_id=job_id)

    monkeypatch.setattr(host.daemon.control_plane, 'reconfigure_radio', transaction)
    host.launch()
    assert host.runtime.collected.wait(2)
    assert not entered.is_set()
    assert host.round_ready(1)['ok'] is True
    assert not entered.is_set()
    host.auto_round_ready = True
    assert host.round_ready(2)['ok'] is True
    try:
        assert entered.wait(2)
        assert len(host.runtime.models) == 1
        assert host.daemon.server_state == ServerState.RUNNING
    finally:
        release.set()
    host.assert_succeeded()


def test_next_downlink_uses_confirmed_rate_without_recreating_role_or_runtime(policy_host, monkeypatch):
    from types import SimpleNamespace
    from wfb_ng.fl.radio import validate_radio_patch
    rates = []
    role_creations = []
    role_closed = threading.Event()

    def policy(config, context):
        context.configure_radio({'uftp_rate_kbps': 12000 if context.round_index == 0 else 18000})

    host = policy_host(policy)

    class Role:
        def __init__(self, **kwargs):
            role_creations.append(kwargs)
            self.transport = SimpleNamespace(uftp_rate_kbps=kwargs['uftp_rate_kbps'])
            self.runtime = host.runtime

        def start(self):
            pass

        def close(self):
            role_closed.set()

        def poll_failure(self):
            return None

    def transaction(patch, target_nodes, *, job_id, stop_event):
        before = host.daemon.control_plane.active_radio_config
        updated = validate_radio_patch(patch, before)
        host.daemon.control_plane.active_radio_config = updated
        return RadioSwitchResult(success=True, session_id=f'rate-{updated.uftp_rate_kbps}',
            target_channel=updated.channel, applied_patch=dict(patch), status='finalized',
            effective_config=updated, before_config=before, target_nodes=list(target_nodes),
            confirmed_nodes=list(target_nodes), job_id=job_id)

    publish = host.runtime.publish_model

    def observe_publish(path):
        rates.append(host.daemon.server_role.transport.uftp_rate_kbps)
        publish(path)

    monkeypatch.setattr('wfb_ng.fl.role.ServerRole', Role)
    monkeypatch.setattr(host.runtime, 'publish_model', observe_publish)
    monkeypatch.setattr(host.daemon.control_plane, 'reconfigure_radio', transaction)
    host.daemon.runtime_factory = None
    host.daemon.config = replace(host.daemon.config, enable_link_process=True)
    host.launch()
    recent = host.assert_succeeded()
    assert len(role_creations) == 1
    assert role_creations[0]['uftp_rate_kbps'] == 12000
    assert rates == [12000, 18000]
    assert role_closed.is_set()
    assert recent['execution_result'] == 'succeeded'


def test_saved_context_cannot_reconfigure_after_policy_window(policy_host):
    captured = []

    def policy(config, context):
        captured.append(context)

    host = policy_host(policy, auto_round_ready=False)
    host.launch()
    assert host.runtime.collected.wait(2)
    assert captured[0].round_index == 0
    with pytest.raises(FLRuntimeError) as caught:
        captured[0].configure_radio({'radio_txpower_dbm': 13})
    assert caught.value.error_code == 'radio_policy_phase_required'
    host.daemon.abort_job('finish-fixture')


def test_initial_policy_prep_budget_cancels_and_does_not_announce(policy_host):
    entered = threading.Event()
    exited = threading.Event()

    def policy(config, context):
        entered.set()
        context.stop_event.wait()
        exited.set()
        context.check_stop()

    host = policy_host(policy)
    host.daemon.config = replace(host.daemon.config, preparation_timeout_seconds=1.0)
    host.launch()
    assert host.start_done.wait(3)
    assert entered.is_set() and exited.is_set()
    assert host.errors[0].error_code == 'preflight_preparation_timeout'
    assert not host.announced.is_set()
    assert not host.runtime.published.is_set()


def test_public_start_uses_actual_control_job_wire_and_keeps_initial_unwatched_safe(
        policy_host, monkeypatch):
    from functools import partial
    rounds = []
    sessions = []

    def policy(config, context):
        rounds.append(context.round_index)
        context.configure_radio({'radio_txpower_dbm': 13 + context.round_index})

    host = policy_host(policy)
    receive = host.receive_downlink
    control = host.daemon.control_plane
    # 仅缩短公开事务参数；控制面状态机、job绑定和逐阶段报告处理保持真实。
    monkeypatch.setattr(control, 'reconfigure_radio', partial(
        control.reconfigure_radio, prepare_timeout_seconds=0.05, commit_delay_seconds=0,
        commit_timeout_seconds=0.1, lease_timeout_seconds=0.3, ping_interval_seconds=0.005))

    def wire(message):
        receive(message)
        reply_type = {
            'CONFIG_RADIO_PREPARE': 'PREPARE_ACK',
            'NEW_CHANNEL_PING': 'COMMIT_SUCCESS',
            'RADIO_SWITCH_FINALIZED': 'RADIO_SWITCH_FINALIZED_ACK',
            'RADIO_SWITCH_CONFIRMED': 'RADIO_SWITCH_CONFIRMED_ACK',
            'CONFIG_RADIO_ABORT': 'RADIO_SWITCH_ROLLBACK_ACK',
        }.get(message['type'])
        if reply_type is None:
            return
        assert message['job_id'] == host.payload['job_id']
        if message['type'] == 'CONFIG_RADIO_PREPARE':
            sessions.append(message['session_id'])
        for node in message['target_nodes']:
            reply = dict(type=reply_type, node_id=node, job_id=message['job_id'],
                         session_id=message['session_id'], ok=True,
                         channel=message['config']['channel'],
                         current_channel=message['config']['channel'],
                         config=message['config'], calibration=message['calibration'])
            control.handle_datagram(json.dumps(reply).encode(),
                                    (f'127.0.0.{10 + node}', host.control_address[1]))

    monkeypatch.setattr(control, 'broadcast_downlink', wire)
    host.launch()
    recent = host.assert_succeeded()
    assert rounds == [0, 1]
    assert len(set(sessions)) == 2
    assert [entry['session_id'] for entry in recent['radio_results']] == sessions
    assert all(entry['transaction_state'] == 'finalized' for entry in recent['radio_results'])
    assert host.daemon.radio_config.radio_txpower_dbm == 14
    assert host.daemon.adapter.wireless_states['wlan0']['txpower_dbm'] == 14


def test_initial_confirmed_change_is_retained_when_role_preparation_fails(policy_host, monkeypatch):
    from wfb_ng.fl.radio import validate_radio_patch

    def policy(config, context):
        context.configure_radio({'radio_txpower_dbm': 13})

    host = policy_host(policy, rounds=1)
    host.daemon.runtime_factory = lambda job, payload: None

    def transaction(patch, target_nodes, *, job_id, stop_event):
        before = host.daemon.control_plane.active_radio_config
        updated = validate_radio_patch(patch, before)
        host.daemon.control_plane.active_radio_config = updated
        return RadioSwitchResult(success=True, session_id='before-role-failure',
            target_channel=updated.channel, applied_patch=dict(patch), status='finalized',
            effective_config=updated, before_config=before, target_nodes=list(target_nodes),
            confirmed_nodes=list(target_nodes), job_id=job_id)

    monkeypatch.setattr(host.daemon.control_plane, 'reconfigure_radio', transaction)
    host.launch()
    assert host.start_done.wait(2)
    assert host.errors[0].error_code == 'runtime_unavailable'
    assert not host.announced.is_set()
    recent = host.daemon.get_status_report()['recent_job']
    assert recent['execution_result'] == 'failed'
    assert recent['radio_results'][0]['effective_config']['radio_txpower_dbm'] == 13
    assert host.daemon.radio_config.radio_txpower_dbm == 13


def test_unexpected_control_error_is_unknown_and_cannot_be_swallowed(policy_host, monkeypatch):
    caught = []

    def policy(config, context):
        try:
            context.configure_radio({'radio_txpower_dbm': 13})
        except FLRuntimeError as exc:
            caught.append(exc.error_code)

    host = policy_host(policy, rounds=1)

    def transaction(*args, **kwargs):
        raise OSError('fixture lost control outcome')

    monkeypatch.setattr(host.daemon.control_plane, 'reconfigure_radio', transaction)
    host.launch()
    assert host.start_done.wait(2)
    assert caught == ['radio_transaction_failed']
    recent = host.daemon.get_status_report()['recent_job']
    assert recent['execution_result'] == 'failed'
    assert recent['radio_results'][0]['effective_config'] is None
    assert recent['radio_results'][0]['phase'] == 'unknown'
    assert recent['radio_results'][0]['before_config']
    assert recent['radio_results'][0]['target_config']
    assert host.daemon.server_state == ServerState.RADIO_ERROR
    assert not host.announced.is_set()


def test_initial_target_snapshot_keeps_fixed_nodes_even_when_they_become_offline(
        policy_host, monkeypatch):
    seen = []

    def policy(config, context):
        # 通过既有注册表超时判定变为OFFLINE；不修改目标集合或伪造未来心跳。
        host.daemon.control_plane.registry.offline_threshold_seconds = -1
        try:
            context.configure_radio({'radio_txpower_dbm': 13})
        except FLRuntimeError:
            pass

    host = policy_host(policy, rounds=1)

    def transaction(patch, target_nodes, *, job_id, stop_event):
        seen.append(list(target_nodes))
        before = host.daemon.control_plane.active_radio_config
        return RadioSwitchResult(success=False, session_id='offline-fixed',
            target_channel=before.channel, applied_patch=dict(patch), status='rolled_back',
            effective_config=before, before_config=before, target_nodes=list(target_nodes),
            confirmed_nodes=[], unresponsive_nodes=list(target_nodes), failed_phase='PREPARE',
            error_message='fixed participants offline', job_id=job_id)

    monkeypatch.setattr(host.daemon.control_plane, 'reconfigure_radio', transaction)
    host.launch()
    assert host.start_done.wait(2)
    assert seen == [[1, 2]]
    assert host.errors[0].error_code == 'radio_transaction_failed'
    assert host.daemon.get_status_report()['recent_job']['radio_results'][0]['target_nodes'] == [1, 2]
