"""Web 射频准备：通过控制台领域入口与 UDP 协议边界验证。"""
from unittest.mock import patch

import pytest

from wfb_ng.fl.console import RadioPreparationError
from wfb_ng.fl.control import NodeHeartbeat
from wfb_ng.fl.server_daemon import ServerState
from wfb_ng.tests.test_fl_console import make_daemon, register_idle
from wfb_ng.tests import test_fl_radio_rest as radio_rest

CONFIG = dict(channel=149, radio_txpower_dbm=12, downlink_mcs=3,
              uplink_mcs=6, uftp_rate_kbps=15000)


def prepared(tmp_path):
    daemon = make_daemon(model_library_dir=str(tmp_path))
    daemon.start()
    register_idle(daemon)
    return daemon


def test_validate_then_apply_updates_authoritative_radio_and_event(tmp_path):
    daemon = prepared(tmp_path)
    try:
        validation = daemon.console.validate_radio_configuration({'config': CONFIG})
        assert validation['rate_bounds']['min_rate_kbps'] == 12000
        assert validation['warning'] is None
        peer = radio_rest.TestRadioReconfigureREST()
        peer.daemon = daemon
        with patch.object(daemon.control_plane, 'broadcast_downlink', side_effect=peer.protocol_peer):
            result = daemon.console.apply_radio_configuration({
                'confirmation_token': validation['confirmation']['token'], 'confirm_risk': False})
        assert result['status'] == 'finalized'
        assert result['applied_patch'] == {'channel':149}
        snapshot = daemon.console.snapshot()
        assert snapshot['server']['radio']['channel'] == 149
        assert snapshot['events'][-1]['type'] == 'RADIO_APPLY_RESULT'
    finally:
        daemon.stop()


def test_unchanged_configuration_does_not_create_radio_transaction(tmp_path):
    daemon = prepared(tmp_path)
    try:
        current = daemon.console.radio_configuration()['config']
        body = {key:current[key] for key in CONFIG}
        with pytest.raises(RadioPreparationError) as raised:
            daemon.console.validate_radio_configuration({'config':body})
        assert raised.value.code == 'RADIO_CONFIG_UNCHANGED'
        assert daemon.console.snapshot()['server']['state'] == 'idle'
    finally:
        daemon.stop()


@pytest.mark.parametrize('field,value', [('channel',161), ('channel',36), ('radio_txpower_dbm',9),
    ('downlink_mcs',7), ('uplink_mcs',True), ('uftp_rate_kbps',0), ('uftp_rate_kbps',None)])
def test_invalid_flat_parameters_never_get_confirmation(tmp_path, field, value):
    daemon = prepared(tmp_path)
    try:
        with pytest.raises(RadioPreparationError) as raised:
            daemon.console.validate_radio_configuration({'config': dict(CONFIG, **{field:value})})
        assert raised.value.code == 'INVALID_RADIO_CONFIG'
    finally:
        daemon.stop()


def test_out_of_range_requires_explicit_risk_acknowledgement_and_token_is_single_use(tmp_path):
    daemon = prepared(tmp_path)
    try:
        validation = daemon.console.validate_radio_configuration({'config':dict(CONFIG, uftp_rate_kbps=19000)})
        assert validation['warning']
        body = {'confirmation_token':validation['confirmation']['token'], 'confirm_risk':False}
        with pytest.raises(RadioPreparationError) as raised:
            daemon.console.apply_radio_configuration(body)
        assert raised.value.code == 'RADIO_RISK_CONFIRMATION_REQUIRED'
        assert daemon.console.snapshot()['server']['radio']['channel'] == 157
        peer = radio_rest.TestRadioReconfigureREST()
        peer.daemon = daemon
        with patch.object(daemon.control_plane, 'broadcast_downlink', side_effect=peer.protocol_peer):
            assert daemon.console.apply_radio_configuration(dict(body, confirm_risk=True))['status'] == 'finalized'
        with pytest.raises(RadioPreparationError) as raised:
            daemon.console.apply_radio_configuration(dict(body, confirm_risk=True))
        assert raised.value.code == 'RADIO_CONFIRMATION_EXPIRED'
    finally:
        daemon.stop()


@pytest.mark.parametrize('change', ['job', 'node', 'event', 'restart', 'expiry', 'recovery', 'link'])
def test_confirmation_invalidated_by_changed_cluster_or_instance(tmp_path, change):
    daemon = prepared(tmp_path)
    try:
        validation = daemon.console.validate_radio_configuration({'config':CONFIG})
        body = {'confirmation_token':validation['confirmation']['token'], 'confirm_risk':False}
        if change == 'job':
            daemon.server_state = ServerState.RUNNING
            daemon.active_job = {'job_id':'busy'}
        elif change == 'node':
            daemon.control_plane.handle_datagram(NodeHeartbeat(1, 'HUNTING', 0, 157, 12, 6, timestamp_ms=2000).to_bytes(), ('127.0.0.1', 10001))
        elif change == 'event':
            daemon.publish_event({'type':'JOB_STARTED', 'job_id':'finished-before-apply'})
        elif change == 'restart':
            other = make_daemon(model_library_dir=str(tmp_path))
            with pytest.raises(RadioPreparationError) as raised:
                other.console.apply_radio_configuration(body)
            assert raised.value.code == 'RADIO_CONFIRMATION_EXPIRED'
            return
        elif change == 'recovery':
            daemon._recent_job = {'recovery_state':'blocked', 'execution_result':'failed'}
        elif change == 'link':
            from dataclasses import replace
            daemon.config = replace(daemon.config, enable_link_process=True)
        if change == 'expiry':
            with patch('wfb_ng.fl.console.time.monotonic', return_value=10**12):
                with pytest.raises(RadioPreparationError) as raised:
                    daemon.console.apply_radio_configuration(body)
        else:
            with pytest.raises(RadioPreparationError) as raised:
                daemon.console.apply_radio_configuration(body)
        assert raised.value.code == 'RADIO_CONFIRMATION_EXPIRED'
        assert daemon.console.snapshot()['server']['radio']['channel'] == 157
    finally:
        daemon.stop()


@pytest.mark.parametrize('failed_kind,status,phase,channel', [
    ('CONFIG_RADIO_PREPARE', 'rolled_back', 'PREPARE', 157),
    ('RADIO_SWITCH_FINALIZED', 'rolled_back', 'FINALIZED', 157),
    ('RADIO_SWITCH_CONFIRMED', 'radio_error', 'CONFIRMED', 149),
])
def test_web_application_preserves_protocol_failure_and_recovery(tmp_path, failed_kind, status, phase, channel):
    daemon = prepared(tmp_path)
    try:
        peer = radio_rest.TestRadioReconfigureREST()
        peer.daemon = daemon
        checked = daemon.console.validate_radio_configuration({'config':CONFIG})
        def respond(message):
            if message['type'] == failed_kind:
                raise OSError('广播失败')
            peer.protocol_peer(message)
        with patch.object(daemon.control_plane, 'broadcast_downlink', side_effect=respond):
            result = daemon.console.apply_radio_configuration({
                'confirmation_token':checked['confirmation']['token'], 'confirm_risk':False})
        assert result['status'] == status
        assert result['failed_phase'] == phase
        state = daemon.console.snapshot()
        assert state['server']['radio']['channel'] == channel
        assert state['events'][-1]['message'] == status
        if status == 'rolled_back':
            assert state['server']['state'] == 'idle'
            assert daemon.console.validate_radio_configuration({'config':CONFIG})['confirmation']['token']
        else:
            assert state['server']['state'] == 'radio_error'
            with pytest.raises(RadioPreparationError) as raised:
                daemon.console.validate_radio_configuration({'config':CONFIG})
            assert raised.value.code == 'RADIO_NOT_READY'
    finally:
        daemon.stop()


def test_concurrent_apply_keeps_state_readable_and_rejects_reuse(tmp_path):
    import threading
    daemon = prepared(tmp_path)
    entered, release = threading.Event(), threading.Event()
    results = []
    try:
        checked = daemon.console.validate_radio_configuration({'config':CONFIG})
        body = {'confirmation_token':checked['confirmation']['token'], 'confirm_risk':False}
        peer = radio_rest.TestRadioReconfigureREST()
        peer.daemon = daemon
        def respond(message):
            if message['type'] == 'CONFIG_RADIO_PREPARE':
                entered.set()
                if not release.wait(4):
                    raise RuntimeError('未释放协议屏障')
            peer.protocol_peer(message)
        with patch.object(daemon.control_plane, 'broadcast_downlink', side_effect=respond):
            worker = threading.Thread(target=lambda: results.append(daemon.console.apply_radio_configuration(body)))
            worker.start()
            try:
                assert entered.wait(2)
                assert daemon.console.snapshot()['server']['state'] == 'preparing'
                with pytest.raises(RadioPreparationError) as raised:
                    daemon.console.apply_radio_configuration(body)
                assert raised.value.code == 'RADIO_CONFIRMATION_EXPIRED'
                with pytest.raises(RadioPreparationError):
                    daemon.console.validate_radio_configuration({'config':CONFIG})
            finally:
                release.set()
                worker.join(4)
        assert not worker.is_alive()
        assert results[0]['status'] == 'finalized'
    finally:
        release.set()
        daemon.stop()
