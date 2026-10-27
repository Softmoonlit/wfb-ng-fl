"""07 服务端宿主训练 metadata 快照接线的受控边界测试。"""
from types import SimpleNamespace
from typing import Any, Dict, cast
from unittest.mock import Mock

import pytest

from wfb_ng.fl.errors import FLRuntimeError
from wfb_ng.fl.server_daemon import ServerDaemon, ServerDaemonConfig


@pytest.fixture
def metadata_host(tmp_path):
    host = ServerDaemon.__new__(ServerDaemon)
    host.config = ServerDaemonConfig(
        enable_control_plane=False, enable_link_process=False,
        work_dir=str(tmp_path), model_library_dir=str(tmp_path / 'models'),
        aggregate_entry='wfb_ng.fl.mnist:aggregate_entry',
    )
    host._job_training_by_round = {}
    return host


def test_mnist_aggregate_wrapper_records_only_finite_metadata_after_success(monkeypatch, metadata_host):
    updates = {1: '/tmp/update-1.pt', 2: '/tmp/update-2.pt'}
    calls = []

    def aggregate(current, received, output, config, context):
        calls.append((current, received, output))
        return 'aggregate-result'

    def read(path, *, node_id, round_index, base_model_sha256, expected_sample_count):
        return {'node_id': node_id, 'round_index': round_index,
                'base_model_sha256': base_model_sha256,
                'sample_count': expected_sample_count, 'optimizer_steps': 938,
                'train_loss': 1.25, 'device': {'type': 'cuda'}}

    monkeypatch.setattr('wfb_ng.fl.mnist.read_update_metadata', read)
    monkeypatch.setattr('wfb_ng.fl.server_daemon.calculate_file_sha256', lambda path: 'a' * 64)
    wrapped = metadata_host._wrap_mnist_aggregate(
        aggregate, SimpleNamespace(target_nodes=(2, 1)),
        {1: {'sample_count': 60000}, 2: {'sample_count': 60000}},
    )
    context = SimpleNamespace(round_index=3)
    assert wrapped('base.pt', updates, 'out.pt', {}, context) == 'aggregate-result'
    assert calls == [('base.pt', updates, 'out.pt')]
    assert metadata_host._job_training_by_round[3][0]['node_id'] == 1
    assert metadata_host._job_training_by_round[3][1]['node_id'] == 2


def test_metadata_failure_keeps_aggregate_facts_missing_without_success(monkeypatch, metadata_host):
    aggregate = Mock(return_value=None)
    monkeypatch.setattr('wfb_ng.fl.server_daemon.calculate_file_sha256', lambda path: 'b' * 64)

    def read(path, *, node_id, **kwargs):
        if node_id == 2:
            raise FLRuntimeError('invalid_mnist_update', '节点 update metadata 无效')
        return {'node_id': node_id, 'round_index': 1, 'base_model_sha256': 'b' * 64,
                'sample_count': 60000}

    monkeypatch.setattr('wfb_ng.fl.mnist.read_update_metadata', read)
    wrapped = metadata_host._wrap_mnist_aggregate(
        aggregate, SimpleNamespace(target_nodes=(1, 2)),
        {1: {'sample_count': 60000}, 2: {'sample_count': 60000}},
    )
    with pytest.raises(FLRuntimeError):
        wrapped('base.pt', {1: 'u1', 2: 'u2'}, 'out.pt', {}, SimpleNamespace(round_index=1))
    assert aggregate.called
    assert metadata_host._job_training_by_round[1][0]['node_id'] == 1
    summary = {'status': 'failed', 'rounds': [{'round_index': 1, 'metrics': {'loss': 1.0}}]}
    copied = metadata_host._training_facts_for_summary(summary)
    assert copied['rounds'][0]['training'][0]['node_id'] == 1
    assert copied['rounds'][0].get('training') != [{'node_id': 2}]


def test_generic_aggregate_is_opaque_and_round_event_summary_is_copied(metadata_host):
    metadata_host.config = ServerDaemonConfig(
        enable_control_plane=False, enable_link_process=False,
        work_dir='/tmp/07-generic-host', model_library_dir='/tmp/07-models',
        aggregate_entry='fixture:aggregate',
    )
    aggregate = Mock(return_value='opaque')
    wrapped = metadata_host._wrap_mnist_aggregate(
        aggregate, SimpleNamespace(target_nodes=(1,)), {1: {'sample_count': 60000}},
    )
    assert wrapped is aggregate
    original = {'type': 'ROUND_COMPLETED', 'round_index': 1,
                'summary': {'round_index': 1, 'metrics': {'loss': 1.0}}}
    metadata_host._job_training_by_round[1] = [{'node_id': 1, 'sample_count': 60000}]
    copied = metadata_host._on_training_round_event(original)
    assert copied['summary']['training'][0]['node_id'] == 1
    assert 'training' not in cast(Dict[str, Any], original['summary'])


def test_summary_copy_preserves_first_terminal_and_merges_available_training(metadata_host):
    metadata_host._job_training_by_round[1] = [{'node_id': 1, 'sample_count': 60000}]
    summary = {'status': 'failed', 'execution_result': 'failed',
               'rounds': [{'round_index': 1, 'metrics': {'loss': 2.0}}]}
    copied = metadata_host._training_facts_for_summary(summary)
    assert copied['status'] == 'failed'
    assert copied['execution_result'] == 'failed'
    assert copied['rounds'][0]['training'][0]['node_id'] == 1
    assert 'training' not in summary['rounds'][0]


def test_publish_event_keeps_shared_diagnostic_original_and_enriches_console(tmp_path, monkeypatch):
    from copy import deepcopy
    import socket
    from wfb_ng.fl.client_daemon import NetworkAdapter

    def reject_network(*args, **kwargs):
        raise AssertionError('07 宿主事件检查禁止网络出站')

    monkeypatch.setattr(socket.socket, 'sendto', reject_network)
    monkeypatch.setattr(socket.socket, 'connect', reject_network)
    monkeypatch.setattr(socket.socket, 'connect_ex', reject_network)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sink:
        sink.bind(('127.0.0.1', 0))
        port = sink.getsockname()[1]
        adapter = Mock(spec=NetworkAdapter)
        adapter.is_tun_active.return_value = False
        host = ServerDaemon(ServerDaemonConfig(
            enable_control_plane=False, enable_link_process=False,
            ipc_host='127.0.0.1', ipc_port=0,
            control_bind_host='127.0.0.1', control_bind_port=0,
            control_broadcast_addr='127.0.0.1', control_broadcast_port=port,
            radio_control_port=port, work_dir=str(tmp_path),
            model_library_dir=str(tmp_path / 'models'),
        ), network_adapter=adapter)
        host.active_job = {'job_id': 'snapshot-job', 'target_nodes': [1], 'rounds': 1}
        training = {'node_id': 1, 'round_index': 1, 'sample_count': 60000,
                    'optimizer_steps': 938, 'train_loss': 1.25}
        host._job_training_by_round[1] = [training]
        original = {'type': 'ROUND_COMPLETED', 'job_id': 'snapshot-job', 'round_index': 1,
                    'summary': {'round_index': 1, 'metrics': {'loss': 1.0}}}
        before = deepcopy(original)
        subscriber = host.event_bus.subscribe()
        # 观察 Console 公开入口，并继续执行真实 Console 投影。
        recorded = Mock(wraps=host.console.record_event)
        monkeypatch.setattr(host.console, 'record_event', recorded)
        host.publish_event(original)
        diagnostic = subscriber.get_nowait()
        history = recorded.call_args.args[0]
        assert history['summary']['training'] == [training]
        assert host.console.snapshot()['current_job']['evaluation']['rounds'][0]['training'] == [training]
        assert diagnostic == before
        assert diagnostic is original
        assert original == before
        assert 'training' not in cast(Dict[str, Any], original['summary'])
        host.event_bus.unsubscribe(subscriber)
