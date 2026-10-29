"""验证紧凑 MNIST CNN 模型制品的公开边界。"""

import subprocess
import sys

import pytest

torch = pytest.importorskip('torch')

from wfb_ng.fl.errors import FLRuntimeError
from wfb_ng.fl.artifacts import file_sha256
from wfb_ng.fl.mnist_cnn_model import MODEL_FORMAT, initialize_model, load_model
from wfb_ng.fl.reference_model import initialize_model as initialize_reference


def test_compact_model_is_reproducible_and_has_expected_size(tmp_path):
    first, second = tmp_path / 'first.pt', tmp_path / 'second.pt'
    digest = initialize_model(first, seed=7)
    initialize_model(second, seed=7)
    artifact = load_model(first)
    repeated = load_model(second)

    assert artifact.sha256 == digest == file_sha256(first)
    assert artifact.initialization_seed == 7
    assert artifact.model(torch.zeros(2, 1, 28, 28)).shape == (2, 10)
    assert sum(parameter.numel() for parameter in artifact.model.parameters()) == 50186
    assert 200_000 < first.stat().st_size < 210_000
    for name, tensor in artifact.model.state_dict().items():
        assert tensor.device.type == 'cpu'
        assert tensor.dtype == torch.float32
        assert torch.isfinite(tensor).all()
        torch.testing.assert_close(tensor, repeated.model.state_dict()[name], rtol=0, atol=0)


def test_compact_reader_rejects_the_minimal_reference_format(tmp_path):
    path = tmp_path / 'reference.pt'
    initialize_reference(path, seed=7)

    with pytest.raises(FLRuntimeError) as error:
        load_model(path)

    assert error.value.error_code == 'invalid_mnist_cnn_model'


def test_compact_model_cli_generates_identified_model(tmp_path):
    path = tmp_path / 'initial.pt'
    command = [sys.executable, '-m', 'wfb_ng.fl.mnist_cnn_model', str(path), '--seed', '7']
    result = subprocess.run(command, text=True, capture_output=True, timeout=15)

    assert result.returncode == 0, result.stderr
    assert MODEL_FORMAT in result.stdout
    assert file_sha256(path)
