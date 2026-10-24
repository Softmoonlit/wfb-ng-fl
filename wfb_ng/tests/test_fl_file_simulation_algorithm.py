import json
from pathlib import Path

import pytest

from wfb_ng.fl.artifacts import file_sha256
from wfb_ng.fl.errors import FLRuntimeError
from wfb_ng.fl.issue41_algorithm import client_main, copy_model, train


def test_file_simulation_client_completes_two_rounds_without_update_template(tmp_path):
    models = []
    for index in (1, 2):
        root = tmp_path / 'rounds' / str(index)
        root.mkdir(parents=True)
        model = root / 'model.bin'
        model.write_bytes(b'fixed-model')
        (root / 'model.manifest.json').write_text(json.dumps({'round_id': str(index)}))
        models.append(model)

    class Runtime:
        work_dir = str(tmp_path)
        node_id = 1

        def __init__(self):
            self.pending = iter(models)
            self.submitted = []

        def wait_for_model(self):
            return str(next(self.pending))

        def submit_update(self, path):
            self.submitted.append(Path(path).read_bytes())

    runtime = Runtime()
    client_main(runtime, {'file_simulation': True, 'rounds': 2})
    result = json.loads((tmp_path / 'issue41-client-result.json').read_text())
    assert result['conclusion'] == 'succeeded'
    assert result['update_template_path'] is None
    assert result['update_template_sha256'] is None
    assert runtime.submitted == [b'fixed-model', b'fixed-model']
    assert [row['round_id'] for row in result['rounds']] == ['1', '2']
    assert all(row['model_sha256'] == row['update_sha256'] == file_sha256(models[0])
               for row in result['rounds'])


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
