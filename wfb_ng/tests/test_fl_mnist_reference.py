"""参考计算的专项核验，不代替真实无线训练验收。"""
from types import SimpleNamespace

import pytest
import torch

from wfb_ng.fl.artifacts import file_sha256
from wfb_ng.fl.errors import FLRuntimeError
from wfb_ng.fl.mnist_reference import aggregate, initialize_model


def _inputs(tmp_path):
    model = tmp_path / 'initial.pt'
    output = tmp_path / 'aggregate.pt'
    initialize_model(model, seed=7)
    initial = torch.load(model, weights_only=True)
    updates = {}
    for node, samples, value in [(1, 1, 2.0), (2, 3, 6.0)]:
        path = tmp_path / f'update-{node}.pt'
        torch.save({
            'model_format': initial['model_format'],
            'state_dict': {key: torch.full_like(tensor, value)
                           for key, tensor in initial['state_dict'].items()},
            'base_model_sha256': file_sha256(model),
            'node_id': node, 'round_index': 1,
            'sample_count': samples, 'optimizer_steps': 1,
            'train_loss': 1.0, 'device': 'cuda:0',
            'training_seed': 0, 'software': {'torch': str(torch.__version__)},
        }, path)
        updates[node] = path
    context = SimpleNamespace(round_index=1, participant_node_ids=(1, 2),
                              sample_counts_by_node={1: 1, 2: 3},
                              check_stop=lambda: None)
    return model, output, initial, updates, context


def test_aggregate_uses_sample_weights(tmp_path):
    model, output, initial, updates, context = _inputs(tmp_path)
    assert aggregate(model, updates, output, {}, context) is None
    result = torch.load(output, weights_only=True)
    for tensor in result['state_dict'].values():
        torch.testing.assert_close(tensor, torch.full_like(tensor, 5.0), rtol=0, atol=0)
    assert [row['weight'] for row in result['provenance']['updates']] == [0.25, 0.75]
    assert result['provenance']['base_model_sha256'] == file_sha256(model)
    assert initial['round_index'] == 0


@pytest.mark.parametrize('field,value', [
    ('node_id', 2), ('node_id', True), ('round_index', 2),
    ('base_model_sha256', '0' * 64), ('sample_count', 0),
    ('sample_count', True), ('sample_count', 60001), ('optimizer_steps', 2),
    ('train_loss', float('nan')), ('device', 'cpu'), ('training_seed', 18),
    ('sample_count', 2),
])
def test_aggregate_rejects_invalid_update_without_publishing(tmp_path, field, value):
    model, output, _, updates, context = _inputs(tmp_path)
    update = torch.load(updates[1], weights_only=True)
    update[field] = value
    torch.save(update, updates[1])
    with pytest.raises(FLRuntimeError):
        aggregate(model, updates, output, {}, context)
    assert not output.exists()


@pytest.mark.parametrize('change', ['missing_node', 'nonfinite', 'shape', 'dtype', 'overwrite', 'stop', 'oversize', 'partial_reference'])
def test_aggregate_rejects_incomplete_or_unsafe_results(tmp_path, change):
    model, output, _, updates, context = _inputs(tmp_path)
    if change == 'missing_node':
        del updates[2]
    elif change == 'overwrite':
        output = model
    elif change == 'oversize':
        updates[1].write_bytes(b'\0' * (1024 * 1024 + 1))
    elif change == 'partial_reference':
        context.sample_counts_by_node = {1: 60000, 2: 60000}
    elif change == 'stop':
        def check_stop():
            raise FLRuntimeError('stopped', '专项停止请求')
        context.check_stop = check_stop
    else:
        update = torch.load(updates[1], weights_only=True)
        key = next(iter(update['state_dict']))
        if change == 'nonfinite':
            update['state_dict'][key].fill_(float('inf'))
        elif change == 'shape':
            update['state_dict'][key] = torch.zeros(1)
        elif change == 'dtype':
            update['state_dict'][key] = update['state_dict'][key].double()
        torch.save(update, updates[1])
    digest = file_sha256(model)
    with pytest.raises(FLRuntimeError):
        aggregate(model, updates, output, {}, context)
    assert file_sha256(model) == digest
    if change != 'overwrite':
        assert not output.exists()
