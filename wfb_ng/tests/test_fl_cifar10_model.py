"""验证 CIFAR10 ResNet18 初始模型制品的公开边界。"""

import subprocess
import sys

import pytest

torch = pytest.importorskip('torch')
torch.set_num_threads(1)

from wfb_ng.fl.artifacts import file_sha256
from wfb_ng.fl.cifar10_model import (
    MODEL_FORMAT,
    initialize_model,
    load_model,
    write_model_atomic,
)
from wfb_ng.fl.errors import FLRuntimeError


def test_cifar10_resnet18_roundtrip_and_structure(tmp_path):
    first, second = tmp_path / 'first.pt', tmp_path / 'second.pt'
    digest = initialize_model(first, seed=7)
    initialize_model(second, seed=7)
    artifact = load_model(first)
    repeated = load_model(second)

    assert artifact.sha256 == digest == file_sha256(first)
    assert artifact.initialization_seed == 7
    artifact.model.eval()
    assert artifact.model(torch.zeros(2, 3, 32, 32)).shape == (2, 10)
    assert sum(parameter.numel() for parameter in artifact.model.parameters()) == 11173962
    assert 40 * 1024 * 1024 < first.stat().st_size < 45 * 1024 * 1024
    assert artifact.model.conv1.kernel_size == (3, 3)
    assert artifact.model.conv1.stride == (1, 1)
    assert artifact.model.conv1.padding == (1, 1)
    assert artifact.model.conv1.bias is None
    assert isinstance(artifact.model.maxpool, torch.nn.Identity)
    for name, tensor in artifact.model.state_dict().items():
        assert tensor.device.type == 'cpu'
        if name.endswith('num_batches_tracked'):
            assert tensor.dtype == torch.int64
            assert torch.all(tensor >= 0)
        else:
            assert tensor.dtype == torch.float32
            assert torch.isfinite(tensor).all()
        torch.testing.assert_close(tensor, repeated.model.state_dict()[name], rtol=0, atol=0)


def test_cifar10_resnet18_seed_changes_initialized_weights(tmp_path):
    first, second = tmp_path / 'first.pt', tmp_path / 'second.pt'
    initialize_model(first, seed=7)
    initialize_model(second, seed=8)

    first_state = load_model(first).model.state_dict()
    second_state = load_model(second).model.state_dict()
    assert not torch.equal(first_state['conv1.weight'], second_state['conv1.weight'])


def test_cifar10_resnet18_rejects_bad_public_state_dicts(tmp_path):
    source = tmp_path / 'source.pt'
    initialize_model(source, seed=7)
    state = load_model(source).model.state_dict()

    bad_dtype = dict(state)
    bad_dtype['conv1.weight'] = state['conv1.weight'].to(torch.float64)
    bad_shape = dict(state)
    bad_shape['conv1.weight'] = state['conv1.weight'].flatten()
    bad_nan = dict(state)
    bad_nan['conv1.weight'] = state['conv1.weight'].clone()
    bad_nan['conv1.weight'].view(-1)[0] = float('nan')

    for bad_state in (bad_dtype, bad_shape, bad_nan):
        with pytest.raises(FLRuntimeError) as error:
            write_model_atomic(tmp_path / 'rejected.pt', bad_state, initialization_seed=7)
        assert error.value.error_code == 'invalid_cifar10_resnet18_model'


def test_cifar10_resnet18_cli_generates_identified_model(tmp_path):
    path = tmp_path / 'initial.pt'
    command = [sys.executable, '-m', 'wfb_ng.fl.cifar10_model', str(path), '--seed', '7']
    result = subprocess.run(command, text=True, capture_output=True, timeout=30)

    assert result.returncode == 0, result.stderr
    assert MODEL_FORMAT in result.stdout
    assert 'size_bytes' in result.stdout
    assert file_sha256(path)
