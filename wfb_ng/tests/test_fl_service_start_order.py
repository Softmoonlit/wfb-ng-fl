"""公共 main 入口：恢复任务链路后再导入算法，停止后不继续启动。"""
import json
import signal
import sys
import threading

import pytest

from wfb_ng.fl import algorithm, service
from wfb_ng.fl.errors import FLRuntimeError
from wfb_ng.tests.test_fl_service_deadlines import role_config_path


pytestmark = pytest.mark.parametrize('role_config_path', ['client'], indirect=True)


@pytest.fixture
def entry(role_config_path, tmp_path, monkeypatch):
    events = []
    handlers = {}
    called = threading.Event()

    class FakeRoleService:
        def start(self):
            events.append('start')
            return object()

        def wait(self, interval):
            raise AssertionError('测试必须在进入运行等待前停止')

        def close(self):
            events.append('close')

    instance = FakeRoleService()

    def load_service(*args, **kwargs):
        events.append('load_service')
        return instance

    def install_signal(signum, handler):
        handlers[signum] = handler

    def run(runtime, config, context):
        called.set()
        context.stop_event.set()

    algorithm_config = tmp_path / 'algorithm.json'
    algorithm_config.write_text('{}')
    context = tmp_path / 'context.json'
    context.write_text(json.dumps(dict(
        job_id='job1', node_id=1, target_nodes=[1], rounds=1,
        artifact_dir=str(tmp_path / 'artifacts'), local_config={},
        preparation_timeout_seconds=10)))
    monkeypatch.setattr(sys, 'argv', ['service', '--config', str(role_config_path),
        '--algorithm', 'controlled:run', '--algorithm-config', str(algorithm_config),
        '--job-context', str(context)])
    monkeypatch.setattr(service, 'load_role_service', load_service)
    monkeypatch.setattr(service.signal, 'signal', install_signal)
    return events, handlers, called, run


def test_link_starts_before_algorithm_import_and_import_stop_closes_service(entry, monkeypatch):
    events, handlers, called, run = entry
    importing = threading.Event()
    release_import = threading.Event()
    errors = []

    def loader(spec, *, arity):
        events.append('import')
        assert arity == 3
        importing.set()
        assert release_import.wait(3)
        return run

    def stop_during_import():
        try:
            assert importing.wait(3)
            handlers[signal.SIGTERM](signal.SIGTERM, None)
        except Exception as error:
            errors.append(error)
        finally:
            release_import.set()

    monkeypatch.setattr(algorithm, 'load_algorithm', loader)
    stopper = threading.Thread(target=stop_during_import)
    stopper.start()
    try:
        assert service.main('client') == 0
    finally:
        release_import.set()
        stopper.join(3)
    assert not stopper.is_alive()
    assert errors == []
    assert events == ['load_service', 'start', 'import', 'close']
    assert not called.is_set()


@pytest.mark.parametrize('stop_at', ['config', 'load_service', 'start'])
def test_stop_checkpoint_prevents_following_start_or_import(entry, monkeypatch, stop_at):
    events, handlers, called, run = entry
    original_read = service._read_config
    original_load = service.load_role_service

    def request_stop():
        handlers[signal.SIGTERM](signal.SIGTERM, None)

    if stop_at == 'config':
        def read(path):
            config = original_read(path)
            request_stop()
            return config
        monkeypatch.setattr(service, '_read_config', read)
    else:
        def load(*args, **kwargs):
            instance = original_load(*args, **kwargs)
            if stop_at == 'load_service':
                request_stop()
            else:
                original_start = instance.start

                def start():
                    runtime = original_start()
                    request_stop()
                    return runtime

                instance.start = start
            return instance
        monkeypatch.setattr(service, 'load_role_service', load)

    def unexpected_import(*args, **kwargs):
        raise AssertionError('停止后不能导入算法')

    monkeypatch.setattr(algorithm, 'load_algorithm', unexpected_import)
    assert service.main('client') == 0
    expected = {'config': [], 'load_service': ['load_service', 'close'],
                'start': ['load_service', 'start', 'close']}
    assert events == expected[stop_at]
    assert not called.is_set()


def test_import_failure_reports_prepare_error_and_closes_started_service(entry, monkeypatch):
    events, handlers, called, run = entry
    reports = []

    def failed_import(spec, *, arity):
        events.append('import')
        raise FLRuntimeError('invalid_configuration', '算法导入失败')

    def report(self, event, **fields):
        reports.append((event, fields))
        return True

    monkeypatch.setattr(algorithm, 'load_algorithm', failed_import)
    monkeypatch.setattr(service.AlgorithmNotifications, 'send', report)
    assert service.main('client') == 1
    assert events == ['load_service', 'start', 'import', 'close']
    assert len(reports) == 1
    assert reports[0][0] == 'error'
    assert reports[0][1]['phase'] == 'prepare'
    assert reports[0][1]['code'] == 'invalid_configuration'
    assert not called.is_set()


def test_invalid_algorithm_json_is_rejected_before_link_start(entry, monkeypatch):
    events, handlers, called, run = entry
    config_path = sys.argv[sys.argv.index('--algorithm-config') + 1]
    from pathlib import Path
    Path(config_path).write_text('[]')
    monkeypatch.setattr(service.AlgorithmNotifications, 'send', lambda *args, **kwargs: True)
    assert service.main('client') == 1
    assert events == []
    assert not called.is_set()


def test_import_stop_runs_real_role_service_cleanup(entry, monkeypatch):
    events, handlers, called, run = entry

    class Link:
        def start(self):
            events.append('link_start')

        def close(self):
            events.append('link_close')

    class Role:
        def start(self):
            events.append('transport_start')
            return object()

        def close_transport(self):
            events.append('transport_close')

        def close_runtime(self):
            events.append('runtime_close')

    instance = service.RoleService(Role(), Link())

    def loader(spec, *, arity):
        assert instance.ready
        events.append('import')
        handlers[signal.SIGTERM](signal.SIGTERM, None)
        return run

    monkeypatch.setattr(service, 'load_role_service', lambda *args, **kwargs: instance)
    monkeypatch.setattr(algorithm, 'load_algorithm', loader)
    assert service.main('client') == 0
    assert events == ['link_start', 'transport_start', 'import',
                      'transport_close', 'link_close', 'runtime_close']
    assert not instance.ready
    assert not called.is_set()


def test_legacy_arity_still_reports_invalid_configuration_after_link_start(entry, monkeypatch):
    import types
    events, handlers, called, run = entry
    reports = []
    module = types.ModuleType('controlled')
    setattr(module, 'run', lambda runtime, config: None)
    monkeypatch.setitem(sys.modules, 'controlled', module)

    def report(self, event, **fields):
        reports.append((event, fields))
        return True

    monkeypatch.setattr(service.AlgorithmNotifications, 'send', report)
    assert service.main('client') == 1
    assert events == ['load_service', 'start', 'close']
    assert reports[0][0] == 'error'
    assert reports[0][1]['code'] == 'invalid_configuration'
    assert not called.is_set()
