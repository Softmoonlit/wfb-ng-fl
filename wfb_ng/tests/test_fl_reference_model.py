"""只检查本票的模型制品边界，不进行训练或无线验收。"""

import json
import subprocess
import sys

import pytest

torch = pytest.importorskip('torch')

from wfb_ng.fl.errors import FLRuntimeError

from wfb_ng.fl.artifacts import file_sha256
from wfb_ng.fl.reference_model import initialize_model, load_model


def test_initial_model_is_loadable_reproducible_and_immutable(tmp_path):
    first, second = tmp_path / 'first.pt', tmp_path / 'second.pt'
    digest = initialize_model(first, seed=7)
    initialize_model(second, seed=7)
    artifact = load_model(first)
    repeated = load_model(second)
    assert artifact.sha256 == digest == file_sha256(first)
    assert artifact.initialization_seed == 7
    assert artifact.model(torch.zeros(2, 1, 28, 28)).shape == (2, 10)
    assert sum(parameter.numel() for parameter in artifact.model.parameters()) == 9098
    for name, tensor in artifact.model.state_dict().items():
        assert tensor.device.type == 'cpu'
        assert tensor.dtype == torch.float32
        assert torch.isfinite(tensor).all()
        torch.testing.assert_close(tensor, repeated.model.state_dict()[name], rtol=0, atol=0)
    with torch.no_grad():
        next(artifact.model.parameters()).zero_()
    assert file_sha256(first) == digest
    assert torch.count_nonzero(next(load_model(first).model.parameters())) > 0


@pytest.mark.parametrize('defect', [
    'format', 'extra_field', 'missing_parameter', 'extra_parameter', 'shape',
    'dtype', 'nan', 'inf', 'seed_type', 'seed_range', 'source', 'extra_metadata',
])
def test_invalid_model_is_rejected_explicitly_without_rewriting(tmp_path, defect):
    path = tmp_path / 'model.pt'
    initialize_model(path, seed=7)
    value = torch.load(path, weights_only=True)
    state = value['state_dict']
    if defect == 'format':
        value['model_format'] = 'opaque-file'
    elif defect == 'extra_field':
        value['unknown'] = 1
    elif defect == 'missing_parameter':
        del state['conv1.weight']
    elif defect == 'extra_parameter':
        state['unknown'] = torch.zeros(1)
    elif defect == 'shape':
        state['conv1.weight'] = torch.zeros(1)
    elif defect == 'dtype':
        state['conv1.weight'] = state['conv1.weight'].double()
    elif defect in ('nan', 'inf'):
        state['conv1.weight'].fill_(float(defect))
    elif defect == 'seed_type':
        value['initialization']['seed'] = True
    elif defect == 'seed_range':
        value['initialization']['seed'] = 2 ** 63
    elif defect == 'source':
        value['initialization']['source'] = 'unknown'
    elif defect == 'extra_metadata':
        value['initialization']['unknown'] = 'unsupported'
    torch.save(value, path)
    digest = file_sha256(path)
    with pytest.raises(FLRuntimeError) as error:
        load_model(path)
    assert error.value.error_code == 'invalid_reference_model'
    assert file_sha256(path) == digest


def test_cli_generates_identified_model_and_refuses_overwrite(tmp_path):
    path = tmp_path / 'initial.pt'
    command = [sys.executable, '-m', 'wfb_ng.fl.reference_model', str(path), '--seed', '7']
    result = subprocess.run(command, text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    identity = json.loads(result.stdout)
    assert identity['sha256'] == file_sha256(path)
    assert identity['initialization']['seed'] == 7
    digest = identity['sha256']
    repeated = subprocess.run(command, text=True, capture_output=True, timeout=15)
    assert repeated.returncode != 0
    assert '模型生成失败' in repeated.stderr
    assert file_sha256(path) == digest


@pytest.mark.parametrize('defect', ['opaque', 'empty', 'object', 'non_cpu', 'oversize'])
def test_unsupported_serialization_and_storage_are_rejected(tmp_path, defect):
    path = tmp_path / 'invalid.pt'
    if defect in ('opaque', 'empty', 'oversize'):
        path.write_bytes({'opaque': b'old opaque model', 'empty': b'',
                          'oversize': b'0' * (1024 * 1024 + 1)}[defect])
    elif defect == 'object':
        torch.save(torch.nn.Linear(1, 1), path)
    else:
        import io
        import zipfile
        initialize_model(path, seed=7)
        output = io.BytesIO()
        with zipfile.ZipFile(path) as source, zipfile.ZipFile(output, 'w') as destination:
            for entry in source.infolist():
                content = source.read(entry)
                if entry.filename.endswith('/data.pkl'):
                    assert b'X\x03\x00\x00\x00cpu' in content
                    content = content.replace(b'X\x03\x00\x00\x00cpu', b'X\x06\x00\x00\x00cuda:0')
                destination.writestr(entry, content)
        path.write_bytes(output.getvalue())
    digest = file_sha256(path)
    with pytest.raises(FLRuntimeError) as error:
        load_model(path)
    assert error.value.error_code == ('artifact_too_large' if defect == 'oversize'
                                      else 'invalid_reference_model')
    assert file_sha256(path) == digest


@pytest.mark.parametrize('seed', [True, -1, 2 ** 63, '7'])
def test_invalid_initialization_seed_does_not_create_artifact(tmp_path, seed):
    path = tmp_path / 'invalid-seed.pt'
    with pytest.raises(FLRuntimeError):
        initialize_model(path, seed=seed)
    assert not path.exists()


@pytest.mark.parametrize('cleanup_fails', [False, True])
def test_failed_publication_reports_cleanup_or_allows_retry(tmp_path, monkeypatch, cleanup_fails):
    import os
    import stat
    path = tmp_path / 'initial.pt'
    real_fsync = os.fsync
    def fail_directory_sync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError('目录同步失败')
        return real_fsync(fd)
    def fail_unlink(*args, **kwargs):
        raise OSError('目录无法删除文件')
    with monkeypatch.context() as patcher:
        patcher.setattr(os, 'fsync', fail_directory_sync)
        if cleanup_fails:
            patcher.setattr(os, 'unlink', fail_unlink)
        with pytest.raises(FLRuntimeError) as error:
            initialize_model(path, seed=7)
    if cleanup_fails:
        assert error.value.error_code == 'reference_model_cleanup_failed'
        assert '目标模型可能仍然存在' in str(error.value)
        assert path.exists()
    else:
        assert not path.exists()
        assert list(tmp_path.iterdir()) == []
        assert initialize_model(path, seed=7) == file_sha256(path)
