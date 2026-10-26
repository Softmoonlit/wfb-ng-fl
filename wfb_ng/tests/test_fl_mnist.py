"""真实 MNIST 计算入口的公开文件与 Runtime 边界。"""
from types import SimpleNamespace
from types import MappingProxyType
import os
import threading

import pytest

torch = pytest.importorskip('torch')

from wfb_ng.fl.errors import FLRuntimeError
from wfb_ng.fl.reference_model import initialize_model, load_model


def preparation(samples=60000, device='cuda'):
    prefix = 't10k' if samples == 10000 and device == 'cpu' else 'train'
    return dict(model_format='mnist-two-conv-fp32-v1', sample_count=samples,
                data_sha256={f'{prefix}-images-idx3-ubyte': '0' * 64,
                             f'{prefix}-labels-idx1-ubyte': '1' * 64},
                device=dict(type=device, name='test device', torch_version='test', cuda_version='test'),
                data_seed=1)


def context(tmp_path, **values):
    defaults = dict(job_id='job', node_id=1, target_nodes=(1, 2),
                    rounds=1, round_index=1, phase='aggregate', artifact_dir=tmp_path,
                    local_config={'client_preparation_reports': {1: preparation(1), 2: preparation(1)}},
                    initial_metrics=None, check_stop=lambda: None)
    defaults.update(values)
    result = SimpleNamespace(**defaults)
    result.for_phase = lambda phase, **kwargs: context(tmp_path, **{**defaults, 'phase': phase, **kwargs})
    return result


def update(tmp_path, base, node, samples, number):
    value = dict(model_format='mnist-two-conv-fp32-v1',
                 state_dict={k: torch.full_like(v, number) for k, v in base.model.state_dict().items()},
                 base_model_sha256=base.sha256, node_id=node, round_index=1,
                 sample_count=samples, optimizer_steps=1, train_loss=2.0,
                 device=dict(type='cuda', name='test GPU', torch_version='test', cuda_version='test'))
    path = tmp_path / (str(node) + '.pt')
    torch.save(value, path)
    return path


@pytest.mark.parametrize('updates_type', [dict, MappingProxyType])
@pytest.mark.parametrize('frozen_config', [False, True])
def test_fedavg_weights_actual_samples_and_preserves_loadable_model(tmp_path, updates_type, frozen_config):
    from wfb_ng.fl.algorithm import AlgorithmContext, freeze_algorithm_config
    from wfb_ng.fl.mnist import aggregate_entry
    initial = tmp_path / 'initial.pt'
    initialize_model(initial, 7)
    base = load_model(initial)
    updates = {1: update(tmp_path, base, 1, 1, 2),
               2: update(tmp_path, base, 2, 3, 6)}
    output = tmp_path / 'result.pt'
    # 使用正式宿主冻结全部嵌套准备事实，并覆盖 Runtime 的只读映射返回类型。
    ctx = AlgorithmContext(job_id='job', node_id=255, target_nodes=(1, 2), rounds=1,
                           artifact_dir=str(tmp_path), stop_event=threading.Event(),
                           local_config={'client_preparation_reports':
                                         {1: preparation(1), 2: preparation(3)}}).for_phase(
                                             'aggregate', round_index=1)
    assert isinstance(ctx.local_config['client_preparation_reports'], MappingProxyType)
    assert isinstance(ctx.local_config['client_preparation_reports'][1], MappingProxyType)
    assert isinstance(ctx.local_config['client_preparation_reports'][1]['data_sha256'], MappingProxyType)
    assert isinstance(ctx.local_config['client_preparation_reports'][1]['device'], MappingProxyType)
    config = freeze_algorithm_config({'data_seed': 1}) if frozen_config else {'data_seed': 1}
    assert aggregate_entry(initial, updates_type(updates), output, config, ctx) is None
    result = load_model(output)
    assert result.initialization_seed == 7
    for parameter in result.model.parameters():
        torch.testing.assert_close(parameter, torch.full_like(parameter, 5), rtol=0, atol=0)
    assert load_model(initial).sha256 == base.sha256


@pytest.mark.parametrize('defect', ['missing_node', 'extra_node', 'node_id', 'round_index',
    'base_model_sha256', 'sample_count', 'optimizer_steps', 'train_loss', 'device',
    'state_dict', 'extra_field', 'model_format', 'bool_samples', 'cpu_device', 'step_count', 'bool_node'])
def test_bad_update_rejects_whole_round_without_output(tmp_path, defect):
    from wfb_ng.fl.mnist import aggregate_entry
    initial = tmp_path / 'initial.pt'
    initialize_model(initial, 7)
    base = load_model(initial)
    paths = {node: update(tmp_path, base, node, 1, 2) for node in (1, 2)}
    value = torch.load(paths[1], weights_only=True)
    if defect == 'missing_node':
        del paths[2]
    elif defect == 'extra_node':
        paths[3] = paths[1]
    elif defect == 'extra_field':
        value['unknown'] = 1
    elif defect == 'state_dict':
        value['state_dict']['conv1.weight'].fill_(float('nan'))
    elif defect == 'bool_node':
        value['node_id'] = True
    elif defect == 'bool_samples':
        value['sample_count'] = True
    elif defect == 'cpu_device':
        value['device']['type'] = 'cpu'
    elif defect == 'step_count':
        value['optimizer_steps'] = 2
    else:
        value[defect] = dict(node_id=3, round_index=2, base_model_sha256='0' * 64,
                            sample_count=0, optimizer_steps=0, train_loss=float('inf'),
                            device={}, model_format='bad')[defect]
    torch.save(value, paths[1])
    output = tmp_path / 'result.pt'
    with pytest.raises(FLRuntimeError):
        aggregate_entry(initial, paths, output, {'data_seed': 1}, context(tmp_path))
    assert not output.exists()


@pytest.mark.parametrize('config_type', [dict, MappingProxyType])
@pytest.mark.parametrize('config', [{}, {'data_seed': True}, {'data_seed': 0},
    {'data_seed': 2**63}, {'data_seed': 1, 'mnist_data_dir': '/tmp'}])
def test_configuration_has_only_explicit_positive_data_seed(tmp_path, config, config_type):
    from wfb_ng.fl.mnist import aggregate_entry
    with pytest.raises(FLRuntimeError, match='data_seed'):
        aggregate_entry('absent', {}, tmp_path / 'out.pt', config_type(config), context(tmp_path))


def test_evaluate_returns_initial_preparation_and_final_improvement_verdict(tmp_path):
    from wfb_ng.fl.mnist import evaluate_entry
    from wfb_ng.fl.reference_model import write_model_atomic
    from wfb_ng.tests.test_fl_mnist_data import write_test_idx
    write_test_idx(tmp_path)
    initial = tmp_path / 'initial.pt'
    initialize_model(initial, 7)
    state = {name: torch.zeros_like(value) for name, value in load_model(initial).model.state_dict().items()}
    model_path = tmp_path / 'zero.pt'
    write_model_atomic(model_path, state, 7)
    ctx = context(tmp_path, phase='initial', round_index=0,
                  local_config={'mnist_data_dir': str(tmp_path)})
    result = evaluate_entry(model_path, {'data_seed': 1}, ctx)
    assert result['metrics']['loss'] == pytest.approx(2.302585093, abs=1e-6)
    assert result['metrics']['accuracy'] == 1.0
    assert result['preparation']['sample_count'] == 10000
    assert result['preparation']['device']['type'] in ('cpu', 'cuda')
    assert result['verdict'] is result['reason'] is None
    ctx.round_index, ctx.phase = 1, 'evaluate'
    ctx.initial_metrics = MappingProxyType({'loss': 3.0, 'accuracy': 0.1})
    final = evaluate_entry(model_path, {'data_seed': 1}, ctx)
    assert final['preparation'] is None
    assert final['verdict'] is True
    ctx.initial_metrics = result['metrics']
    assert evaluate_entry(model_path, {'data_seed': 1}, ctx)['verdict'] is False


def test_client_without_gpu_fails_before_ready_or_runtime_wait(tmp_path, monkeypatch):
    from wfb_ng.fl.mnist import client_entry
    monkeypatch.setattr(torch.cuda, 'is_available', lambda: False)
    events = []
    ctx = context(tmp_path, report_prepared=lambda value: events.append('ready'))
    runtime = SimpleNamespace(wait_for_model=lambda: events.append('wait'))
    with pytest.raises(FLRuntimeError, match='GPU'):
        client_entry(runtime, {'data_seed': 1}, ctx)
    assert events == []


@pytest.mark.skipif(os.environ.get('WFB_RUN_MNIST_GPU_TEST') != '1',
                    reason='显式运行真实 GPU 完整 60000 样本计算检查')
@pytest.mark.parametrize('rounds', [1, 2])
def test_gpu_client_trains_full_epoch_and_reports_only_after_submit(tmp_path, rounds):
    import struct
    from wfb_ng.fl.algorithm import AlgorithmContext
    from wfb_ng.fl.mnist import client_entry
    from wfb_ng.fl.reference_model import write_model_atomic
    (tmp_path / 'train-images-idx3-ubyte').write_bytes(
        struct.pack('>IIII', 2051, 60000, 28, 28) + bytes(60000 * 784))
    (tmp_path / 'train-labels-idx1-ubyte').write_bytes(
        struct.pack('>II', 2049, 60000) + bytes(60000))
    initial = tmp_path / 'initial.pt'
    initialize_model(initial, 7)
    events, submitted, global_models = [], [], [initial]
    def submit(path):
        value = torch.load(path, weights_only=True)
        submitted.append(value)
        events.append('submit')
        next_model = tmp_path / f'global-{len(submitted)}.pt'
        write_model_atomic(next_model, value['state_dict'], 7)
        global_models.append(next_model)
    runtime = SimpleNamespace(wait_for_model=lambda: global_models[-1], submit_update=submit)
    ctx = AlgorithmContext(job_id='job', node_id=1, target_nodes=(1,), rounds=rounds,
                           artifact_dir=str(tmp_path),
                           local_config={'mnist_data_dir': str(tmp_path)},
                           stop_event=threading.Event(),
                           prepared_callback=lambda report: events.append(report),
                           round_callback=lambda index: events.append(index))
    client_entry(runtime, {'data_seed': 1}, ctx)
    assert events[0]['sample_count'] == 60000
    assert events[0]['device']['type'] == 'cuda'
    assert events[1:] == {1: ['submit', 1], 2: ['submit', 1, 'submit', 2]}[rounds]
    assert ctx.round_index == 0
    assert len(submitted) == rounds
    value = submitted[-1]
    assert value['sample_count'] == 60000
    assert value['optimizer_steps'] == 938
    assert value['node_id'] == 1 and value['round_index'] == rounds
    assert value['base_model_sha256'] == load_model(global_models[-2]).sha256
    assert 0 <= value['train_loss'] < 1
    assert all(t.device.type == 'cpu' and t.dtype == torch.float32 for t in value['state_dict'].values())
    assert any(not torch.equal(v, load_model(initial).model.state_dict()[k]) for k, v in value['state_dict'].items())


def test_stop_during_aggregation_preserves_inputs_and_publishes_nothing(tmp_path):
    from wfb_ng.fl.mnist import aggregate_entry
    initial = tmp_path / 'initial.pt'
    initialize_model(initial, 7)
    base = load_model(initial)
    paths = {node: update(tmp_path, base, node, 1, 2) for node in (1, 2)}
    boundaries = []
    def check_stop():
        boundaries.append(None)
        if len(boundaries) == 5:
            raise FLRuntimeError('job_aborted', '测试停止')
    output = tmp_path / 'result.pt'
    with pytest.raises(FLRuntimeError) as caught:
        aggregate_entry(initial, paths, output, {'data_seed': 1}, context(tmp_path, check_stop=check_stop))
    assert caught.value.error_code == 'job_aborted'
    assert not output.exists()
    assert load_model(initial).sha256 == base.sha256


def test_existing_output_is_not_overwritten(tmp_path):
    from wfb_ng.fl.mnist import aggregate_entry
    initial = tmp_path / 'initial.pt'
    initialize_model(initial, 7)
    base = load_model(initial)
    paths = {node: update(tmp_path, base, node, 1, 2) for node in (1, 2)}
    output = tmp_path / 'result.pt'
    output.write_bytes(b'existing output')
    with pytest.raises(FLRuntimeError):
        aggregate_entry(initial, paths, output, {'data_seed': 1}, context(tmp_path))
    assert output.read_bytes() == b'existing output'
    assert not tuple(tmp_path.glob('.result.pt.*'))


def test_evaluation_normalizes_pixels_and_weights_tail_samples(tmp_path):
    import struct
    from wfb_ng.fl.mnist import evaluate_entry
    from wfb_ng.fl.reference_model import write_model_atomic
    (tmp_path / 't10k-images-idx3-ubyte').write_bytes(
        struct.pack('>IIII', 2051, 10000, 28, 28) + bytes([255]) * (10000 * 784))
    (tmp_path / 't10k-labels-idx1-ubyte').write_bytes(
        struct.pack('>II', 2049, 10000) + bytes(9984) + bytes([1]) * 16)
    initial = tmp_path / 'initial.pt'
    initialize_model(initial, 7)
    state = {name: torch.zeros_like(value) for name, value in load_model(initial).model.state_dict().items()}
    # 全白图正确归一化后，只有类别 0 的 logit 为 1，其余为 0。
    state['conv1.weight'][0, 0, 1, 1] = 1
    state['conv2.weight'][0, 0, 1, 1] = 1
    state['classifier.weight'][0, 0] = 1
    model_path = tmp_path / 'known-logits.pt'
    write_model_atomic(model_path, state, 7)
    result = evaluate_entry(model_path, {'data_seed': 1},
        context(tmp_path, round_index=0, local_config={'mnist_data_dir': str(tmp_path)}))
    assert result['metrics']['accuracy'] == 0.9984
    assert result['metrics']['loss'] == pytest.approx(1.4627501717, abs=1e-6)


def test_preparation_validator_accepts_gpu_and_server_cpu_reports():
    from wfb_ng.fl.mnist import validate_preparation_report
    assert validate_preparation_report(preparation(), expected_seed=1) is None
    assert validate_preparation_report(preparation(10000, 'cpu'), expected_samples=10000,
                                       require_gpu=False, expected_seed=1) is None


@pytest.mark.parametrize('defect', ['extra', 'sample_bool', 'samples', 'sha_upper', 'sha_missing',
    'device_extra', 'device_cpu', 'device_long', 'seed_bool', 'seed_mismatch', 'format', 'sha_type'])
def test_invalid_preparation_report_is_rejected(defect):
    from wfb_ng.fl.mnist import validate_preparation_report
    value = preparation()
    if defect == 'extra':
        value['extra'] = 1
    elif defect == 'sample_bool':
        value['sample_count'] = True
    elif defect == 'samples':
        value['sample_count'] = 1
    elif defect == 'sha_upper':
        value['data_sha256']['train-images-idx3-ubyte'] = 'A' * 64
    elif defect == 'sha_missing':
        del value['data_sha256']['train-labels-idx1-ubyte']
    elif defect == 'sha_type':
        value['data_sha256']['train-images-idx3-ubyte'] = 0
    elif defect == 'device_extra':
        value['device']['extra'] = 1
    elif defect == 'device_cpu':
        value['device']['type'] = 'cpu'
    elif defect == 'device_long':
        value['device']['name'] = 'x' * 129
    elif defect == 'seed_bool':
        value['data_seed'] = True
    elif defect == 'seed_mismatch':
        value['data_seed'] = 2
    elif defect == 'format':
        value['model_format'] = 'other'
    with pytest.raises(FLRuntimeError):
        validate_preparation_report(value, expected_seed=1)


def test_update_sample_count_must_match_accepted_preparation(tmp_path):
    from wfb_ng.fl.mnist import aggregate_entry
    initial = tmp_path / 'initial.pt'
    initialize_model(initial, 7)
    base = load_model(initial)
    paths = {node: update(tmp_path, base, node, 1, 2) for node in (1, 2)}
    output = tmp_path / 'result.pt'
    ctx = context(tmp_path, local_config={'client_preparation_reports':
                  {1: preparation(), 2: preparation()}})
    with pytest.raises(FLRuntimeError):
        aggregate_entry(initial, paths, output, {'data_seed': 1}, ctx)
    assert not output.exists()
