"""工单04服务端宿主接线：public start_job + 真实控制报告门禁。

受控 Runtime 和显式部署的测试算法只验证宿主装配、顺序及完成边界；
不把它们当作 MNIST 计算、GPU 或无线验收证据。
"""
from copy import deepcopy
import json
import hashlib
import ipaddress
import socket
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



@pytest.fixture(autouse=True)
def isolated_socket_boundary(monkeypatch):
    """只在真实 socket 出站边界拦截：本文件绝不访问板卡或共享控制端口。"""
    sendto = socket.socket.sendto
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex

    def require_isolated(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            host, port = address[:2]
            assert ipaddress.ip_address(host).is_loopback, f'禁止测试访问非 loopback 地址: {address}'
            assert port not in (9000, 9001, 9002, 9200), f'禁止测试访问共享控制端口: {address}'

    def isolated_sendto(sock, data, *args):
        require_isolated(sock, args[-1])
        return sendto(sock, data, *args)

    def isolated_connect(sock, address):
        require_isolated(sock, address)
        return connect(sock, address)

    def isolated_connect_ex(sock, address):
        require_isolated(sock, address)
        return connect_ex(sock, address)

    monkeypatch.setattr(socket.socket, 'sendto', isolated_sendto)
    monkeypatch.setattr(socket.socket, 'connect', isolated_connect)
    monkeypatch.setattr(socket.socket, 'connect_ex', isolated_connect_ex)


@pytest.fixture
def isolated_control_address(isolated_socket_boundary):
    """持有 OS 分配的 loopback UDP 接收端，不复用实机默认端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sink:
        sink.bind(('127.0.0.1', 0))
        yield sink.getsockname()

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
        if self.host.auto_round_ready and not (
                self.host.hold_final_reports and self.wait_calls == self.host.payload['rounds']):
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
                 control_address, auto_ready=True, auto_round_ready=True, runtime_missing=False,
                 invalid_entry=None, reference_client=False, algorithm_config=None, round_budget=1.5):
        self.control_address = control_address
        self.trace = []
        self.announced = threading.Event()
        self.terminal = threading.Event()
        self.evaluation_started = threading.Event()
        self.evaluation_exited = threading.Event()
        self.final_evaluated = threading.Event()
        self.release_final_evaluation = threading.Event()
        self.final_evaluation_exited = threading.Event()
        self.start_done = threading.Event()
        self.auto_recovery = True
        self.auto_ready = auto_ready
        self.auto_round_ready = auto_round_ready
        self.hold_final_reports = False
        self.evaluation_mode = 'normal'
        self.client_report = preparation_report() if reference_client else {'ready': True}
        self.initial_preparation = {'model_format': 'host-fixture', 'ready': True}
        self.messages = []
        self.acks = []
        self.errors = []
        self.result = None
        self.aggregate_contexts = []
        self.hold_aggregate = False
        self.aggregate_started = threading.Event()
        self.release_aggregate = threading.Event()
        self.aggregate_exited = threading.Event()
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
            if self.hold_aggregate:
                self.aggregate_started.set()
                self.release_aggregate.wait()
                self.aggregate_exited.set()
                context.check_stop()
            self.aggregate_contexts.append((dict(config), context, dict(updates)))
            Path(output).parent.mkdir(parents=True, exist_ok=True)
            Path(output).write_bytes(f'explicit-test-aggregate-output-{context.round_index}'.encode())

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
            if self.evaluation_mode == 'hold_final':
                self.final_evaluated.set()
                self.release_final_evaluation.wait()
                self.final_evaluation_exited.set()
                context.check_stop()
            if context.round_index == context.rounds:
                self.final_evaluated.set()
            final = context.round_index == context.rounds
            verdict = self.evaluation_mode != 'effect_failed'
            return {'metrics': {'loss': 1.0, 'accuracy': 0.2},
                    'preparation': None, 'verdict': verdict if final else None,
                    'reason': ('fixture improved' if verdict else 'fixture not improved') if final else None}

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
            control_bind_port=0, control_broadcast_addr=control_address[0],
            control_broadcast_port=control_address[1], radio_control_port=control_address[1],
            enable_control_plane=False, enable_link_process=False,
            preparation_timeout_seconds=budget, round_timeout_seconds=round_budget, io_timeout_seconds=0.2, mnist_data_dir=str(tmp_path / 'local-test-data'),
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
                self.daemon.control_plane.handle_datagram(heartbeat.to_bytes(), (f'127.0.0.{10 + node}', self.control_address[1]))
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
            if self.auto_recovery:
                for node in (1, 2):
                    self.recovery(node)
            self.terminal.set()

    def send(self, payload):
        replies = self.daemon.control_plane.handle_datagram(
            json.dumps(payload).encode(), (f"127.0.0.{10 + payload['node_id']}", self.control_address[1]))
        ack = json.loads(replies[0]) if replies else None
        self.acks.append(ack)
        return ack

    def task_ready(self, node, report=None, *, bare=False):
        payload = dict(type='TASK_READY', job_id=self.payload['job_id'], node_id=node,
                       report_id=f'prepared-{node}')
        if not bare:
            payload['report'] = deepcopy(self.client_report) if report is None else report
        return self.send(payload)

    def recovery(self, node, *, state='ready', epoch=1):
        report = dict(recovery_epoch=epoch, recovery_state=state)
        if state == 'blocked':
            report.update(error_code='FIXTURE_CLEANUP_BLOCKED', reason='任务资源仍存在')
        return self.send(dict(type='JOB_RECOVERY', job_id=self.payload['job_id'], node_id=node,
                              report_id=f'recovery-{node}-{epoch}', report=report))

    def round_ready(self, node):
        index = self.runtime.wait_calls or 1
        return self.send(dict(type='ROUND_READY', job_id=self.payload['job_id'], node_id=node,
                              report_id=f'round-{index}-{node}', round_index=index))

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
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            recent = self.daemon.get_status_report()['recent_job']
            if recent and recent['recovery_state'] != 'recovering':
                break
            time.sleep(0.005)
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
def host_factory(tmp_path, monkeypatch, initial_model, isolated_control_address):
    hosts = []
    def make(**kwargs):
        model_path = kwargs.pop('model_path', initial_model)
        host = TrainingHost(tmp_path, monkeypatch, model_path,
                            control_address=isolated_control_address, **kwargs)
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


def test_effect_failure_preserves_successful_execution_and_independent_recovery(host_factory):
    host = host_factory()
    host.evaluation_mode = 'effect_failed'
    host.launch()
    recent = host.assert_succeeded()
    assert recent['effect_result']['verdict'] is False
    assert recent['summary']['status'] == 'failed'
    assert recent['summary']['error_code'] == 'effect_criterion_not_met'
    assert recent['summary']['rounds_completed'] == 1
    assert recent['recovery_state'] == 'ready'
    assert host.messages[-1]['type'] == 'JOB_ABORT', '效果失败仍应如实结束全局作业'


def test_five_round_host_reuses_runtime_and_waits_for_all_final_submission_returns(host_factory):
    host = host_factory()
    host.payload['rounds'] = 5
    host.hold_final_reports = True
    host.launch()
    assert host.final_evaluated.wait(2)
    assert not host.terminal.is_set()
    assert not host.runtime.closed.is_set(), '末轮上传最终响应未返回前不能回收任务资源'
    assert host.round_ready(1)['ok'] is True
    assert host.round_ready(1)['ok'] is True
    assert not host.terminal.is_set(), '单节点及其重复报告不能代替全员屏障'
    assert host.round_ready(2)['ok'] is True
    recent = host.assert_succeeded()
    assert recent['summary']['rounds_completed'] == 5
    assert host.trace.count('runtime') == 1
    assert host.trace.count('announce') == 1
    assert len(host.runtime.models) == 5
    assert [context.round_index for _, context in host.evaluate_contexts] == [0, 1, 2, 3, 4, 5]
    rows = recent['summary']['rounds']
    assert host.runtime.models == [rows[0]['input_model_path']] + [row['output_model_path'] for row in rows[:4]]
    assert recent['recovery_state'] == 'ready'


def test_request_cannot_override_deployment_deadlines(host_factory):
    host = host_factory()
    host.payload.update(preparation_timeout_seconds=999, round_timeout_seconds=999,
                        io_timeout_seconds=999)
    host.launch()
    recent = host.assert_succeeded()
    announce = next(message for message in host.messages if message['type'] == 'TASK_ANNOUNCE')
    for name in ('preparation_timeout_seconds', 'round_timeout_seconds', 'io_timeout_seconds'):
        assert recent[name] == getattr(host.daemon.config, name)
        assert announce[name] == getattr(host.daemon.config, name)


def test_terminal_requires_recovery_from_all_targets_and_late_ready_only_updates_recovery(host_factory):
    host = host_factory()
    host.auto_recovery = False
    host.launch()
    recent = host.assert_succeeded()
    assert recent['recovery_state'] == 'unconfirmed'
    assert recent['recovery_missing_nodes'] == [1, 2]
    frozen_summary = deepcopy(recent['summary'])
    with pytest.raises(FLRuntimeError) as caught:
        host.daemon.start_job(dict(host.payload, job_id='conflicting-job'))
    assert caught.value.error_code == 'preflight_engine_conflict'
    assert host.recovery(1)['ok'] is True
    recent = host.daemon.get_status_report()['recent_job']
    assert recent['recovery_state'] == 'unconfirmed'
    assert recent['recovery_missing_nodes'] == [2]
    assert host.recovery(2, state='blocked')['ok'] is True
    assert host.daemon.get_status_report()['recent_job']['recovery_state'] == 'blocked'
    assert host.recovery(2, epoch=2)['ok'] is True
    recent = host.daemon.get_status_report()['recent_job']
    assert recent['recovery_state'] == 'ready'
    assert recent['summary'] == frozen_summary
    assert recent['execution_result'] == 'succeeded'
    assert host.daemon.abort_job()['status'] == 'completed'
    assert len([m for m in host.messages if m['type'] in ('JOB_ABORT', 'JOB_COMPLETED')]) == 1


def test_stop_preserves_recovering_until_algorithm_actually_exits(host_factory):
    host = host_factory()
    host.evaluation_mode = 'hold_final'
    host.launch()
    assert host.final_evaluated.wait(2)
    result = []
    stopper = threading.Thread(target=lambda: result.append(host.daemon.abort_job('operator_stop')))
    stopper.start()
    try:
        assert host.runtime.closed.wait(1), '停止必须显式唤醒 Runtime 等待'
        recent = host.daemon.get_status_report()['recent_job']
        assert recent['execution_result'] == 'aborted'
        assert recent['recovery_state'] == 'recovering'
        assert host.terminal.wait(1), '服务端计算未退出也必须先向客户端请求停止'
        assert recent['server_recovery_complete'] is False
        assert not host.final_evaluation_exited.is_set()
        recent = host.daemon.get_status_report()['recent_job']
        assert recent['recovery_missing_nodes'] == [], '客户端已确认恢复也不能替代服务端退出证明'
        assert recent['recovery_state'] == 'recovering'
        with pytest.raises(FLRuntimeError) as caught:
            host.daemon.start_job(dict(host.payload, job_id='while-server-compute-held'))
        assert caught.value.error_code == 'preflight_engine_conflict'
        assert stopper.is_alive()
    finally:
        host.release_final_evaluation.set()
        stopper.join(3)
    assert not stopper.is_alive()
    assert host.final_evaluation_exited.is_set()
    assert result[0]['status'] == 'aborted'
    assert host.daemon.get_status_report()['recent_job']['recovery_state'] == 'ready'
    assert host.daemon.abort_job('late_stop') == result[0]


def test_role_failure_is_supervised_without_status_polling(host_factory, monkeypatch):
    from dataclasses import replace
    host = host_factory(auto_round_ready=False)
    failed = threading.Event()
    closed = threading.Event()

    class Role:
        runtime = host.runtime

        def start(self):
            pass

        def poll_failure(self):
            return FLRuntimeError('fixture_role_exit', '任务 HTTP 接收器异常退出') if failed.is_set() else None

        def close(self):
            closed.set()

    monkeypatch.setattr('wfb_ng.fl.role.ServerRole', lambda **kwargs: Role())
    host.daemon.runtime_factory = None
    host.daemon.config = replace(host.daemon.config, enable_link_process=True)
    host.launch()
    assert host.final_evaluated.wait(2)
    failed.set()
    assert host.terminal.wait(2), '角色失败必须主动触发终态，不能依赖 get_status_report'
    assert closed.wait(1)
    recent = host.daemon.get_status_report()['recent_job']
    assert recent['execution_result'] == 'failed'
    assert 'fixture_role_exit' in recent['reason'] or 'HTTP' in recent['error']
    assert host.runtime.closed.wait(1)


def test_frozen_participant_offline_fails_job_without_status_poll(host_factory):
    host = host_factory(auto_round_ready=False)
    host.daemon.control_plane.offline_threshold_seconds = 0.12
    host.daemon.control_plane.registry.offline_threshold_seconds = 0.12
    host.launch()
    assert host.final_evaluated.wait(2)
    assert host.terminal.wait(2), '心跳失联由活动作业监管主动收尾'
    recent = host.daemon.get_status_report()['recent_job']
    assert recent['execution_result'] == 'failed'
    assert recent['reason'] == 'participant_offline'
    assert recent['target_nodes'] == [1, 2]
    assert recent['liveness_events'][0]['state'] == 'offline'
    assert host.runtime.closed.wait(1)


def test_client_business_error_is_acknowledged_and_wakes_round_barrier(host_factory):
    host = host_factory(auto_round_ready=False)
    host.launch()
    assert host.final_evaluated.wait(2)
    error = dict(type='TASK_ERROR', job_id=host.payload['job_id'], node_id=1,
                 report_id='error-1', phase='round', round_index=1,
                 error_code='GPU_TRAINING_FAILED', reason='设备训练失败')
    ack = host.send(error)
    assert ack['ok'] is True
    assert host.send(error)['ok'] is True
    assert host.terminal.wait(2)
    recent = host.daemon.get_status_report()['recent_job']
    assert recent['execution_result'] == 'failed'
    assert recent['client_error']['error_code'] == 'GPU_TRAINING_FAILED'
    assert host.runtime.closed.wait(1)


def test_round_deadline_freezes_failure_before_compute_exit_and_cannot_be_overwritten_by_stop(host_factory):
    host = host_factory(round_budget=0.08)
    host.evaluation_mode = 'hold_final'
    host.launch()
    try:
        assert host.final_evaluated.wait(2)
        assert host.runtime.closed.wait(1), '完整轮次期限必须主动请求 Runtime 取消'
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            recent = host.daemon.get_status_report()['recent_job']
            if recent:
                break
            time.sleep(0.005)
        assert recent and recent['execution_result'] == 'failed'
        assert recent['reason'] == 'round_timeout'
        assert recent['recovery_state'] == 'recovering'
        assert host.terminal.wait(1)
        assert recent['server_recovery_complete'] is False
        assert host.daemon.abort_job('late_operator_stop')['status'] == 'failed'
    finally:
        host.release_final_evaluation.set()
    assert host.terminal.wait(2)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        recent = host.daemon.get_status_report()['recent_job']
        if recent.get('summary'):
            break
        time.sleep(0.005)
    assert recent['execution_result'] == 'failed'
    assert recent['summary']['error_code'] == 'round_timeout'


def test_operator_stop_cancels_initial_preparation_before_announcement(host_factory):
    host = host_factory()
    host.evaluation_mode = 'wait_for_cancel'
    host.launch()
    assert host.evaluation_started.wait(1)
    result = host.daemon.abort_job('operator_stop')
    assert result['status'] == 'aborted'
    assert host.evaluation_exited.is_set()
    assert host.start_done.wait(2)
    assert host.errors[0].error_code == 'job_preparation_cancelled'
    assert not host.announced.is_set()
    assert not host.runtime.published.is_set()


def test_terminal_retry_keeps_identity_and_waits_for_real_recovery(host_factory, monkeypatch):
    host = host_factory()
    terminal_attempts = []
    receive = host.receive_downlink

    def lose_first_terminal(message):
        if message['type'] in ('JOB_ABORT', 'JOB_COMPLETED'):
            terminal_attempts.append(deepcopy(message))
            if len(terminal_attempts) == 1:
                return
        receive(message)

    monkeypatch.setattr(host.daemon.control_plane, 'broadcast_downlink', lose_first_terminal)
    from dataclasses import replace
    host.daemon.config = replace(host.daemon.config, io_timeout_seconds=0.6)
    host.launch()
    recent = host.assert_succeeded()
    assert recent['recovery_state'] == 'ready'
    assert len(terminal_attempts) == 2
    assert terminal_attempts[0] == terminal_attempts[1]


def test_runtime_close_failure_blocks_recovery_and_next_job(host_factory, monkeypatch):
    host = host_factory()

    def fail_close():
        raise FLRuntimeError('fixture_close_failed', '任务资源尚未释放')

    monkeypatch.setattr(host.runtime, 'close', fail_close)
    host.launch()
    assert host.start_done.wait(2)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        recent = host.daemon.get_status_report()['recent_job']
        if recent and recent['recovery_state'] == 'blocked':
            break
        time.sleep(0.005)
    assert recent['execution_result'] == 'succeeded'
    assert recent['recovery_error'] == 'fixture_close_failed'
    assert recent['server_recovery_complete'] is False
    assert recent['recovery_missing_nodes'] == []
    assert host.terminal.wait(1), '服务端清理失败也必须向客户端发送停止／完成'
    with pytest.raises(FLRuntimeError) as caught:
        host.daemon.start_job(dict(host.payload, job_id='blocked-next-job'))
    assert caught.value.error_code == 'preflight_engine_conflict'


def test_daemon_stop_during_terminal_cleanup_waits_same_compute_and_closes_role_once(host_factory, monkeypatch):
    from dataclasses import replace
    host = host_factory()
    host.evaluation_mode = 'hold_final'
    close_count = []

    class Role:
        runtime = host.runtime

        def start(self):
            pass

        def poll_failure(self):
            return None

        def close(self):
            close_count.append('closed')

    monkeypatch.setattr('wfb_ng.fl.role.ServerRole', lambda **kwargs: Role())
    host.daemon.runtime_factory = None
    host.daemon.config = replace(host.daemon.config, enable_link_process=True)
    host.launch()
    assert host.final_evaluated.wait(2)
    aborter = threading.Thread(target=lambda: host.daemon.abort_job('operator_stop'))
    stopper = threading.Thread(target=host.daemon.stop)
    aborter.start()
    try:
        assert host.runtime.closed.wait(1)
        stopper.start()
        assert not host.final_evaluation_exited.is_set()
        assert not close_count, '计算未退出，任务角色不能关闭'
        assert host.terminal.wait(1)
    finally:
        host.release_final_evaluation.set()
        aborter.join(3)
        if stopper.ident is not None:
            stopper.join(3)
    assert not aborter.is_alive()
    assert not stopper.is_alive()
    assert host.final_evaluation_exited.is_set()
    assert close_count == ['closed']
    recent = host.daemon.get_status_report()['recent_job']
    assert recent['execution_result'] == 'aborted'
    assert recent['reason'] == 'operator_stop'
    assert recent['recovery_state'] == 'blocked'
    assert recent['recovery_error'] == 'DAEMON_STOPPED'


def test_blocked_aggregate_does_not_delay_client_abort_or_terminal_retry(host_factory, monkeypatch):
    from dataclasses import replace
    host = host_factory()
    host.hold_aggregate = True
    host.daemon.config = replace(host.daemon.config, io_timeout_seconds=0.8)
    receive = host.receive_downlink
    attempts = []

    def drop_first_abort(message):
        if message['type'] == 'JOB_ABORT':
            attempts.append(deepcopy(message))
            if len(attempts) == 1:
                return
        receive(message)

    monkeypatch.setattr(host.daemon.control_plane, 'broadcast_downlink', drop_first_abort)
    host.launch()
    assert host.aggregate_started.wait(2)
    stopper = threading.Thread(target=lambda: host.daemon.abort_job('operator_stop'))
    stopper.start()
    try:
        assert host.terminal.wait(1), '阻塞聚合不能阻挡向客户端发送并重试停止'
        assert len(attempts) == 2
        assert attempts[0] == attempts[1]
        assert not host.aggregate_exited.is_set()
        recent = host.daemon.get_status_report()['recent_job']
        assert recent['execution_result'] == 'aborted'
        assert recent['recovery_state'] == 'recovering'
        assert recent['server_recovery_complete'] is False
        assert recent['recovery_missing_nodes'] == []
        assert set(recent['recovery_reports']) == {1, 2}
        with pytest.raises(FLRuntimeError) as caught:
            host.daemon.start_job(dict(host.payload, job_id='conflicts-with-blocked-aggregate'))
        assert caught.value.error_code == 'preflight_engine_conflict'
        assert stopper.is_alive()
    finally:
        host.release_aggregate.set()
        stopper.join(3)
    assert not stopper.is_alive()
    recent = host.daemon.get_status_report()['recent_job']
    assert recent['execution_result'] == 'aborted'
    assert recent['server_recovery_complete'] is True
    assert recent['recovery_state'] == 'ready'
    assert recent['summary']['status'] == 'aborted'


def test_first_terminal_send_exception_is_retried_within_frozen_recovery_budget(host_factory, monkeypatch):
    from dataclasses import replace
    host = host_factory()
    host.daemon.config = replace(host.daemon.config, io_timeout_seconds=0.7)
    receive = host.receive_downlink
    attempts = []

    def transient_send_failure(message):
        if message['type'] in ('JOB_ABORT', 'JOB_COMPLETED'):
            attempts.append(deepcopy(message))
            if len(attempts) == 1:
                raise OSError('fixture transient UDP send failure')
        receive(message)

    monkeypatch.setattr(host.daemon.control_plane, 'broadcast_downlink', transient_send_failure)
    host.launch()
    assert host.terminal.wait(2), '首个 send 异常后仍须在同一恢复预算内重试终态'
    recent = host.assert_succeeded()
    assert recent['recovery_state'] == 'ready'
    assert recent['recovery_missing_nodes'] == []
    assert len(attempts) == 2
    assert attempts[0] == attempts[1]
    assert recent['terminal_delivery_errors'][0]['reason'] == 'fixture transient UDP send failure'


def test_blocked_recovery_does_not_stop_terminal_retries_to_other_missing_target(host_factory, monkeypatch):
    from dataclasses import replace
    host = host_factory(auto_round_ready=False)
    host.auto_recovery = False
    host.daemon.config = replace(host.daemon.config, io_timeout_seconds=0.7)
    receive = host.receive_downlink
    attempts = []
    second_received_stop = threading.Event()

    def one_blocked_one_missing(message):
        receive(message)
        if message['type'] == 'JOB_ABORT':
            attempts.append(deepcopy(message))
            if len(attempts) == 1:
                assert host.recovery(1, state='blocked')['ok'] is True
            else:
                assert host.recovery(2)['ok'] is True
                second_received_stop.set()

    monkeypatch.setattr(host.daemon.control_plane, 'broadcast_downlink', one_blocked_one_missing)
    host.launch()
    assert host.final_evaluated.wait(2)
    result = host.daemon.abort_job('operator_stop')
    assert result['status'] == 'aborted'
    assert second_received_stop.is_set(), '一节点blocked不能让另一丢包节点收不到stop'
    assert len(attempts) >= 2
    assert all(message == attempts[0] for message in attempts)
    recent = host.daemon.get_status_report()['recent_job']
    assert recent['execution_result'] == 'aborted'
    assert recent['recovery_state'] == 'blocked'
    assert recent['recovery_missing_nodes'] == [1]
    assert recent['recovery_reports'][1]['report']['error_code'] == 'FIXTURE_CLEANUP_BLOCKED'
    with pytest.raises(FLRuntimeError):
        host.daemon.start_job(dict(host.payload, job_id='must-remain-blocked'))
    assert host.recovery(1, epoch=2)['ok'] is True
    progressed = host.daemon.get_status_report()['recent_job']
    assert progressed['recovery_state'] == 'ready'
    assert progressed['execution_result'] == 'aborted'
    assert progressed['recovery_report_history'][1][0]['report']['error_code'] == 'FIXTURE_CLEANUP_BLOCKED'
    assert progressed['recovery_report_history'][1][1]['report']['recovery_state'] == 'ready'
    assert host.daemon.abort_job('late-stop') == result
