from pathlib import Path

import pytest

from wfb_ng.fl.artifacts import file_sha256
from wfb_ng.fl.errors import FLRuntimeError
from wfb_ng.fl.issue41_algorithm import copy_model, train


def test_file_simulation_returns_model_bytes_and_preserves_digest(tmp_path):
    model = tmp_path / 'model.bin'
    update = tmp_path / 'update.bin'
    output = tmp_path / 'next.bin'
    model.write_bytes(b'fixed-model')
    train(str(model), str(update), {'file_simulation': True})
    copy_model(str(model), {1: str(update)}, str(output), {})
    assert update.read_bytes() == model.read_bytes()
    assert output.read_bytes() == model.read_bytes()
    assert file_sha256(output) == file_sha256(model)


def test_file_simulation_rejects_wrong_update_content(tmp_path):
    model = tmp_path / 'model.bin'
    update = tmp_path / 'update.bin'
    model.write_bytes(b'model')
    update.write_bytes(b'wrong')
    with pytest.raises(FLRuntimeError, match='完全一致'):
        copy_model(str(model), {1: str(update)}, str(tmp_path / 'out'), {})
