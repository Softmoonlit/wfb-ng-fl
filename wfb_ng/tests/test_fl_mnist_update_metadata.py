"""通过公开读取接口检查真实 torch update 的有限元数据，不执行训练。"""
import json

import pytest

torch = pytest.importorskip('torch')

from wfb_ng.fl.artifacts import file_sha256
from wfb_ng.fl.errors import FLRuntimeError
from wfb_ng.fl.reference_model import MODEL_FORMAT, initialize_model, load_model


@pytest.fixture
def reference_update(tmp_path):
    initial = tmp_path / 'initial.pt'
    initialize_model(initial, seed=7)
    base = load_model(initial)
    value = {
        'model_format': MODEL_FORMAT,
        'state_dict': dict(base.model.state_dict()),
        'base_model_sha256': base.sha256,
        'node_id': 2,
        'round_index': 3,
        'sample_count': 60000,
        'optimizer_steps': 938,
        'train_loss': 1.25,
        'device': {'type': 'cuda', 'name': 'fixture GPU',
                   'torch_version': 'fixture', 'cuda_version': 'fixture'},
    }
    path = tmp_path / 'update.pt'
    torch.save(value, path)
    expected = dict(node_id=2, round_index=3, base_model_sha256=base.sha256,
                    expected_sample_count=60000)
    return path, value, expected


def test_read_update_metadata_returns_bounded_facts_without_parameters(reference_update):
    from wfb_ng.fl.mnist import read_update_metadata
    path, value, expected = reference_update
    digest = file_sha256(path)
    result = read_update_metadata(path, **expected)
    assert result == {
        'model_format': MODEL_FORMAT,
        'base_model_sha256': expected['base_model_sha256'],
        'node_id': 2, 'round_index': 3, 'sample_count': 60000,
        'optimizer_steps': 938, 'train_loss': 1.25,
        'device': {'type': 'cuda', 'name': 'fixture GPU',
                   'torch_version': 'fixture', 'cuda_version': 'fixture'},
    }
    assert len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8')) <= 3072
    assert file_sha256(path) == digest
    result['device']['name'] = 'changed'
    assert read_update_metadata(path, **expected)['device']['name'] == 'fixture GPU'


@pytest.mark.parametrize('defect', [
    'node', 'round', 'base', 'samples', 'steps', 'loss_nan', 'loss_inf', 'loss_negative',
    'tensor_nan', 'tensor_inf', 'tensor_shape', 'tensor_dtype', 'tensor_missing',
    'tensor_extra', 'format', 'extra_field', 'cpu_device', 'device_long', 'loss_huge_integer',
])
def test_metadata_rejects_invalid_update_contents(reference_update, defect):
    from wfb_ng.fl.mnist import read_update_metadata
    path, value, expected = reference_update
    if defect == 'node':
        value['node_id'] = 1
    elif defect == 'round':
        value['round_index'] = 2
    elif defect == 'base':
        value['base_model_sha256'] = '0' * 64
    elif defect == 'samples':
        value['sample_count'] = 59999
    elif defect == 'steps':
        value['optimizer_steps'] = 937
    elif defect.startswith('loss_'):
        value['train_loss'] = {'loss_nan': float('nan'), 'loss_inf': float('inf'),
                               'loss_negative': -0.1, 'loss_huge_integer': 10**400}[defect]
    elif defect in ('tensor_nan', 'tensor_inf'):
        value['state_dict']['conv1.weight'].fill_(float(defect.removeprefix('tensor_')))
    elif defect == 'tensor_shape':
        value['state_dict']['conv1.weight'] = torch.zeros(1)
    elif defect == 'tensor_dtype':
        value['state_dict']['conv1.weight'] = value['state_dict']['conv1.weight'].double()
    elif defect == 'tensor_missing':
        del value['state_dict']['conv1.weight']
    elif defect == 'tensor_extra':
        value['state_dict']['unexpected'] = torch.zeros(1)
    elif defect == 'format':
        value['model_format'] = 'unknown-algorithm'
    elif defect == 'extra_field':
        value['other'] = 1
    elif defect == 'cpu_device':
        value['device']['type'] = 'cpu'
    elif defect == 'device_long':
        value['device']['name'] = 'x' * 129
    torch.save(value, path)
    digest = file_sha256(path)
    with pytest.raises(FLRuntimeError):
        read_update_metadata(path, **expected)
    assert file_sha256(path) == digest


@pytest.mark.parametrize('field,bad_value,payload_value', [
    ('node_id', True, 1), ('node_id', 2.0, 2), ('node_id', 0, 0), ('node_id', 11, 11),
    ('round_index', True, 1), ('round_index', 3.0, 3), ('round_index', 0, 0),
    ('round_index', 2**63, 2**63),
    ('base_model_sha256', 'not-a-sha', 'not-a-sha'),
    ('base_model_sha256', 'A' * 64, 'A' * 64),
    ('expected_sample_count', 60000.0, 60000), ('expected_sample_count', True, 1),
])
def test_metadata_rejects_invalid_expected_identity_and_sample_constraints(
        reference_update, field, bad_value, payload_value):
    from wfb_ng.fl.mnist import read_update_metadata
    path, value, expected = reference_update
    expected[field] = bad_value
    payload_field = 'sample_count' if field == 'expected_sample_count' else field
    value[payload_field] = payload_value
    if field == 'expected_sample_count':
        value['optimizer_steps'] = (payload_value + 63) // 64
    torch.save(value, path)
    with pytest.raises(FLRuntimeError):
        read_update_metadata(path, **expected)


def test_metadata_rejects_corrupt_torch_file(reference_update):
    from wfb_ng.fl.mnist import read_update_metadata
    path, _, expected = reference_update
    path.write_bytes(b'not a torch update')
    with pytest.raises(FLRuntimeError):
        read_update_metadata(path, **expected)
