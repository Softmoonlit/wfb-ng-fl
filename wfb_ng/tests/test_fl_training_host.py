"""工单04服务端宿主接线：public start_job + 真实控制报告门禁。

受控 Runtime 和显式部署的测试算法只验证宿主装配、顺序及完成边界；
不把它们当作 MNIST 计算、GPU 或无线验收证据。
"""
from copy import deepcopy
import json
import hashlib
from pathlib import Path
import sys
import threading
import time
import types

import pytest

from wfb_ng.fl.control import ClientNodeState, NodeHeartbeat
from wfb_ng.fl.errors import FLRuntimeError
from wfb_ng.fl.reference_model import MODEL_FORMAT, initialize_model
from wfb_ng.fl.server_daemon import ServerDaemon, ServerDaemonConfig
from wfb_ng.tests.test_fl_client_daemon import MockNetworkAdapter


def preparation_report(*, server=False):
    prefix = 't10k' if server else 'train'
    return {
        'model_format': MODEL_FORMAT,
        'sample_count': 10000 if server else 60000,
        'data_sha256': {f'{prefix}-images-idx3-ubyte': 'a' * 64,
                        f'{prefix}-labels-idx1-ubyte': 'b' * 64},
        'device': {'type': 'cpu' if server else 'cuda', 'name': 'host-fixture',
                   'torch_version': 'fixture-version',
                   'cuda_version': 'unavailable' if server else 'fixture-cuda'},
        'data_seed': 2026,
    }


class ControlledRuntime:
    """通过 runtime_factory 接缝观测发布，并控制收齐结果。"""

    def __init__(self, host):
        self.host = host
        self.participant_node_ids = (1, 2)
        self.ready = True
        self.published = threading.Event()
        self.collected = threading.Event()
        self.closed = threading.Event()
        self.partial_first_collection = False
        self.wait_calls = 0
        self.models = []
        self.model_contents = []

    def publish_model(self, path):
        self.host.trace.append('publish')
        self.models.append(path)
        self.model_contents.append(Path(path).read_bytes())
        self.published.set()

    def wait_for_updates(self):
        self.wait_calls += 1
        self.host.trace.append('collect')
        self.collected.set()
        if self.host.auto_round_ready:
            for node in (1, 2):
                self.host.round_ready(node)
        nodes = (1,) if self.partial_first_collection and self.wait_calls == 1 else (1, 2)
        return {node: str(self.host.updates[node]) for node in nodes}

    def abort(self, reason='aborted'):
        self.closed.set()

    def close(self):
        self.closed.set()


class TrainingHost:
    def __init__(self, tmp_path, monkeypatch, model_path, *, budget=1.5,
                 auto_ready=True, auto_round_ready=True, runtime_missing=False,
                 invalid_entry=None, reference_client=False, algorithm_config=None):
        self.trace = []
        self.announced = threading.Event()
        self.terminal = threading.Event()
        self.evaluation_started = threading.Event()
        self.evaluation_exited = threading.Event()
        self.final_evaluated = threading.Event()
        self.start_done = threading.Event()
        self.auto_ready = auto_ready
        self.auto_round_ready = auto_round_ready
        self.evaluation_mode = 'normal'
        self.client_report = preparation_report() if reference_client else {'ready': True}
        self.initial_preparation = {'model_format': 'host-fixture', 'ready': True}
        self.messages = []
        self.acks = []
        self.errors = []
        self.result = None
        self.aggregate_contexts = []
        self.evaluate_contexts = []
        self.updates = {node: tmp_path / f'update-{node}.bin' for node in (1, 2)}
        for node, path in self.updates.items():
            path.write_bytes(f'opaque-update-{node}'.encode())
        self.runtime = ControlledRuntime(self)

        # 每个测试独占自己的部署模块，夹具状态不跨测试共享。
        module = types.ModuleType('training_host_deployment_fixture')

        def client_entry(runtime, config, context):
            raise AssertionError('server must load client callable without executing it')

        def aggregate_entry(current, updates, output, config, context):
            self.trace.append('aggregate')
            self.aggregate_contexts.append((dict(config), context, dict(updates)))
            Path(output).parent.mkdir(parents=True, exist_ok=True)
            Path(output).write_bytes(b'explicit-test-aggregate-output')

        def evaluate_entry(model_path, config, context):
            self.evaluate_contexts.append((dict(config), context))
            context.check_stop()
            if context.round_index == 0:
                self.trace.append('initial_evaluate')
                self.evaluation_started.set()
                try:
                    if self.evaluation_mode == 'fail':
                        raise FLRuntimeError('fixture_initial_evaluation_failed', '本机准备失败')
                    if self.evaluation_mode == 'wait_for_cancel':
                        if not context.stop_event.wait(2):
                            raise AssertionError('initial evaluation was not cancelled by preparation budget')
                        context.check_stop()
                finally:
                    self.evaluation_exited.set()
                return {'metrics': {'loss': 2.0, 'accuracy': 0.1},
                        'preparation': dict(self.initial_preparation),
                        'verdict': None, 'reason': None}
            self.trace.append('final_evaluate')
            self.final_evaluated.set()
            return {'metrics': {'loss': 1.0, 'accuracy': 0.2},
                    'preparation': None, 'verdict': True, 'reason': 'fixture improved'}

        module.client_entry = client_entry
        module.aggregate_entry = aggregate_entry
        module.evaluate_entry = evaluate_entry
        if invalid_entry is not None:
            # 分别覆盖三个必需部署入口的 callable 校验。
            setattr(module, invalid_entry, object())
        monkeypatch.setitem(sys.modules, module.__name__, module)
        entries = {name: f'{module.__name__}:{name}' for name in
                   ('client_entry', 'aggregate_entry', 'evaluate_entry')}
        if reference_client:
            entries['client_entry'] = 'wfb_ng.fl.mnist:client_entry'
        config = ServerDaemonConfig(
            work_dir=str(tmp_path / 'server'), model_library_dir=str(tmp_path / 'models'),
            ipc_host='127.0.0.1', ipc_port=0, control_bind_host='127.0.0.1',
            control_bind_port=0, enable_control_plane=False, enable_link_process=False,
            preparation_timeout_seconds=budget, mnist_data_dir=str(tmp_path / 'local-test-data'),
            algorithm_config={'data_seed': 2026} if algorithm_config is None else algorithm_config, **entries)

        def runtime_factory(job, payload):
            self.trace.append('runtime')
            return None if runtime_missing else self.runtime

        self.daemon = ServerDaemon(config, network_adapter=MockNetworkAdapter(['wlan0']),
                                   runtime_factory=runtime_factory)
        # 仅替换出站网络边界，保留真实注册表、准备校验和报告确认处理。
        monkeypatch.setattr(self.daemon.control_plane, 'broadcast_downlink', self.receive_downlink)
        self.daemon.start()
        for node in (1, 2):
            for state in (ClientNodeState.HUNTING, ClientNodeState.IDLE):
                heartbeat = NodeHeartbeat(node_id=node, state=state.value, elapsed_ms=0,
                    current_channel=config.channel, txpower_dbm=config.radio_txpower_dbm,
                    uplink_mcs=config.uplink_mcs, downlink_mcs=config.downlink_mcs,
                    uftp_rate_kbps=self.daemon.radio_config.uftp_rate_kbps,
                    bandwidth_mhz=config.bandwidth_mhz, timestamp_ms=int(time.time() * 1000))
                self.daemon.control_plane.handle_datagram(heartbeat.to_bytes(), (f'10.80.0.{10 + node}', 9001))
        self.payload = dict(job_id='training-host-job', run_id='training-host-run',
            model_path=str(model_path), model_size_bytes=model_path.stat().st_size,
            target_nodes=[1, 2], rounds=1, mode='sync', round_timeout_seconds=1.5,
            io_timeout_seconds=2, live_observation=False)
        self.worker = None
        self.entries = entries

    def receive_downlink(self, message):
        self.messages.append(deepcopy(message))
        if message['type'] == 'TASK_ANNOUNCE':
            self.trace.append('announce')
            self.announced.set()
            if self.auto_ready:
                for node in (1, 2):
                    self.task_ready(node)
        elif message['type'] in ('JOB_ABORT', 'JOB_COMPLETED'):
            self.terminal.set()

    def send(self, payload):
        replies = self.daemon.control_plane.handle_datagram(
            json.dumps(payload).encode(), (f"10.80.0.{10 + payload['node_id']}", 9001))
        ack = json.loads(replies[0]) if replies else None
        self.acks.append(ack)
        return ack

    def task_ready(self, node, report=None, *, bare=False):
        payload = dict(type='TASK_READY', job_id=self.payload['job_id'], node_id=node,
                       report_id=f'prepared-{node}')
        if not bare:
            payload['report'] = deepcopy(self.client_report) if report is None else report
        return self.send(payload)

    def round_ready(self, node):
        return self.send(dict(type='ROUND_READY', job_id=self.payload['job_id'], node_id=node,
                              report_id=f'round-1-{node}', round_index=1))

    def launch(self):
        def start():
            try:
                self.result = self.daemon.start_job(dict(self.payload))
            except Exception as exc:
                self.errors.append(exc)
            finally:
                self.start_done.set()
        self.worker = threading.Thread(target=start, name='training-host-public-start', daemon=True)
        self.worker.start()

    def await_announce(self):
        assert self.announced.wait(1), f'no TASK_ANNOUNCE; start errors={self.errors}; trace={self.trace}'

    def assert_succeeded(self):
        assert self.start_done.wait(2), 'start_job must finish within its preparation budget'
        assert not self.errors, f'start_job failed: {self.errors}'
        assert self.terminal.wait(2), f'no terminal after complete reports; trace={self.trace}'
        recent = self.daemon.get_status_report()['recent_job']
        assert recent and recent['execution_result'] == 'succeeded', recent
        return recent

    def close(self):
        self.daemon.stop()
        if self.worker is not None:
            self.worker.join(3)
            assert not self.worker.is_alive(), 'start_job worker must exit on daemon stop'


@pytest.fixture(scope='module')
def initial_model(tmp_path_factory):
    path = tmp_path_factory.mktemp('training-host-model') / 'initial.pt'
    initialize_model(str(path), seed=17)
    return path


@pytest.fixture
def host_factory(tmp_path, monkeypatch, initial_model):
    hosts = []
    def make(**kwargs):
        model_path = kwargs.pop('model_path', initial_model)
        host = TrainingHost(tmp_path, monkeypatch, model_path, **kwargs)
        hosts.append(host)
        return host
    yield make
    for host in reversed(hosts):
        host.close()


def test_initial_evaluation_precedes_runtime_and_announce_and_uses_explicit_algorithms(host_factory):
    host = host_factory()
    host.launch()
    host.assert_succeeded()
    assert host.trace.index('initial_evaluate') < host.trace.index('runtime') < host.trace.index('announce')
    assert host.trace.index('announce') < host.trace.index('publish') < host.trace.index('aggregate')
    announce = next(message for message in host.messages if message['type'] == 'TASK_ANNOUNCE')
    assert announce['algorithm'] == host.entries['client_entry']
    assert announce['algorithm_config'] == {'data_seed': 2026}
    assert 'mnist_data_dir' not in announce
    assert [context.round_index for _, context in host.evaluate_contexts] == [0, 1]
    config, context, updates = host.aggregate_contexts[0]
    assert config == {'data_seed': 2026}
    assert set(updates) == {1, 2}
    assert dict(context.local_config['client_preparation_reports'][1]) == {'ready': True}


def test_initial_evaluation_failure_never_starts_runtime_or_publishes(host_factory):
    host = host_factory()
    host.evaluation_mode = 'fail'
    host.launch()
    assert host.start_done.wait(2)
    assert host.evaluation_started.is_set(), f'evaluate callable was never invoked: {host.errors}'
    assert 'runtime' not in host.trace
    assert not host.announced.is_set()
    assert not host.runtime.published.is_set()
    if host.errors:
        assert isinstance(host.errors[0], FLRuntimeError)
    else:
        assert host.daemon.get_status_report()['recent_job']['execution_result'] == 'failed'


def test_initial_evaluation_consumes_preparation_budget_and_is_joined_before_return(host_factory):
    host = host_factory(budget=0.15)
    host.evaluation_mode = 'wait_for_cancel'
    started = time.monotonic()
    host.launch()
    assert host.start_done.wait(1.5), 'initial evaluation must be cancelled by the preparation budget'
    assert host.evaluation_started.is_set(), f'evaluate callable was never invoked: {host.errors}'
    assert host.evaluation_exited.is_set(), 'preparation cannot return while evaluation still owns resources'
    assert time.monotonic() - started < 1.5
    assert not host.announced.is_set()
    assert not host.runtime.published.is_set()
    assert host.errors and isinstance(host.errors[0], FLRuntimeError)


def test_runtime_factory_none_cannot_accept_a_training_job(host_factory):
    host = host_factory(runtime_missing=True)
    host.launch()
    assert host.start_done.wait(2)
    assert not host.runtime.published.is_set()
    assert not host.announced.is_set()
    assert host.errors and isinstance(host.errors[0], FLRuntimeError), host.errors
    assert host.errors[0].error_code == 'runtime_unavailable'


@pytest.mark.parametrize('entry', ['client_entry', 'aggregate_entry', 'evaluate_entry'])
def test_all_three_deployment_entries_must_resolve_to_callables(host_factory, entry):
    host = host_factory(invalid_entry=entry)
    host.launch()
    assert host.start_done.wait(2)
    assert host.errors and isinstance(host.errors[0], FLRuntimeError), host.errors
    assert not host.announced.is_set()
    assert not host.runtime.published.is_set()


def test_reports_from_every_target_gate_first_publication_even_without_link_process(host_factory):
    host = host_factory(auto_ready=False)
    host.launch()
    host.await_announce()
    assert host.task_ready(1)['ok'] is True
    reports = host.daemon.control_plane.get_task_preparation_reports(host.payload['job_id'])
    assert reports[1]['report'] == {'ready': True}
    assert not host.runtime.published.wait(0.05), 'one target report cannot release a two-target barrier'
    assert host.task_ready(2)['ok'] is True
    host.assert_succeeded()


def test_bare_task_ready_cannot_release_preparation(host_factory):
    host = host_factory(auto_ready=False)
    host.launch()
    host.await_announce()
    assert host.task_ready(1, bare=True)['ok'] is False
    assert host.task_ready(2)['ok'] is True
    assert not host.runtime.published.wait(0.05)
    assert host.task_ready(1)['ok'] is True
    host.assert_succeeded()


@pytest.mark.parametrize('invalid', ['samples', 'gpu', 'digest_schema', 'different_data', 'seed'])
def test_invalid_or_inconsistent_mnist_reports_are_rejected_before_publication(host_factory, invalid):
    host = host_factory(auto_ready=False, reference_client=True)
    host.launch()
    host.await_announce()
    assert host.task_ready(1)['ok'] is True
    report = preparation_report()
    if invalid == 'samples':
        report['sample_count'] = 59999
    elif invalid == 'gpu':
        report['device']['type'] = 'cpu'
    elif invalid == 'digest_schema':
        report['data_sha256']['train-images-idx3-ubyte'] = 'not-a-sha256'
    elif invalid == 'different_data':
        report['data_sha256']['train-images-idx3-ubyte'] = 'c' * 64
    else:
        report['data_seed'] = 17
    ack = host.task_ready(2, report)
    assert ack and ack['ok'] is False, ack
    assert not host.runtime.published.wait(0.05)


def test_global_terminal_waits_for_every_final_round_ready(host_factory):
    host = host_factory(auto_round_ready=False)
    host.launch()
    assert host.start_done.wait(2)
    assert not host.errors, host.errors
    assert host.final_evaluated.wait(1), f'final evaluation not reached: {host.trace}'
    assert not host.terminal.wait(0.05), 'collect and evaluation do not prove clients received submit success'
    assert host.round_ready(1)['ok'] is True
    assert not host.terminal.wait(0.05), 'one final report cannot release the terminal barrier'
    assert host.round_ready(2)['ok'] is True
    host.assert_succeeded()


def test_first_incomplete_collection_fails_without_retry_or_success(host_factory):
    host = host_factory()
    host.runtime.partial_first_collection = True
    host.launch()
    assert host.start_done.wait(2)
    assert not host.errors, host.errors
    assert host.terminal.wait(2), 'incomplete strict-sync collection must produce a terminal failure'
    recent = host.daemon.get_status_report()['recent_job']
    assert recent and recent['execution_result'] == 'failed', recent
    assert host.runtime.wait_calls == 1
    assert not host.aggregate_contexts, 'partial updates cannot reach the deployed aggregate callable'
    assert all(message['type'] != 'JOB_COMPLETED' for message in host.messages)


def test_generic_algorithm_accepts_its_own_config_model_and_preparation(host_factory, tmp_path):
    model_path = tmp_path / 'custom-model.bin'
    model_path.write_bytes(b'custom-model-format-without-mnist-tensors')
    config = {'step_size': 0.25, 'strategy': 'custom'}
    host = host_factory(model_path=model_path, algorithm_config=config)
    host.launch()
    recent = host.assert_succeeded()
    assert host.runtime.model_contents == [b'custom-model-format-without-mnist-tensors']
    assert all(actual_config == config for actual_config, _ in host.evaluate_contexts)
    aggregate_config, context, _ = host.aggregate_contexts[0]
    assert aggregate_config == config
    assert dict(context.local_config['client_preparation_reports'][1]) == {'ready': True}
    announce = next(message for message in host.messages if message['type'] == 'TASK_ANNOUNCE')
    assert announce['algorithm_config'] == config
    assert recent['server_preparation'] == {'model_format': 'host-fixture', 'ready': True}


def test_publication_uses_frozen_initial_model_after_original_is_changed(host_factory, tmp_path, initial_model):
    original = tmp_path / 'operator-initial-model.pt'
    original_bytes = initial_model.read_bytes()
    original.write_bytes(original_bytes)
    original_sha = hashlib.sha256(original_bytes).hexdigest()
    host = host_factory(model_path=original, auto_ready=False)
    host.payload['model_sha256'] = original_sha
    host.launch()
    host.await_announce()
    assert host.evaluation_exited.is_set()
    assert not host.runtime.published.is_set()
    # 本机准备已通过；客户端报告屏障使首次发布仍处于等待状态。
    original.write_bytes(b'operator changed the source after preparation')
    for node in (1, 2):
        assert host.task_ready(node)['ok'] is True
    host.assert_succeeded()
    assert Path(host.runtime.models[0]).resolve() != original.resolve()
    assert host.runtime.model_contents == [original_bytes]
    assert hashlib.sha256(host.runtime.model_contents[0]).hexdigest() == original_sha
    announce = next(message for message in host.messages if message['type'] == 'TASK_ANNOUNCE')
    assert announce['model_sha256'] == original_sha
