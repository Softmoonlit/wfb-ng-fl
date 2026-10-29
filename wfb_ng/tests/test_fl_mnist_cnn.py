"""验证紧凑 MNIST CNN 使用独立算法入口完成 FedAvg 聚合。"""

from types import SimpleNamespace
import threading

import pytest

torch = pytest.importorskip('torch')

from wfb_ng.fl.mnist_cnn_32_64 import aggregate_entry
from wfb_ng.fl.mnist_cnn_model import initialize_model, load_model


def _preparation(samples):
    return {
        'model_format': 'mnist-cnn-32-64-fp32-v1',
        'sample_count': samples,
        'data_sha256': {
            'train-images-idx3-ubyte': '0' * 64,
            'train-labels-idx1-ubyte': '1' * 64,
        },
        'device': {
            'type': 'cuda', 'name': 'test GPU',
            'torch_version': 'test', 'cuda_version': 'test',
        },
        'data_seed': 1,
    }


def test_compact_algorithm_aggregates_sample_weighted_updates(tmp_path):
    initial = tmp_path / 'initial.pt'
    initialize_model(initial, seed=7)
    base = load_model(initial)
    updates = {}
    for node, samples, value in ((1, 1, 2), (2, 3, 6)):
        path = tmp_path / f'{node}.pt'
        torch.save({
            'model_format': 'mnist-cnn-32-64-fp32-v1',
            'state_dict': {key: torch.full_like(tensor, value)
                           for key, tensor in base.model.state_dict().items()},
            'base_model_sha256': base.sha256,
            'node_id': node,
            'round_index': 1,
            'sample_count': samples,
            'optimizer_steps': 1,
            'train_loss': 2.0,
            'device': {
                'type': 'cuda', 'name': 'test GPU',
                'torch_version': 'test', 'cuda_version': 'test',
            },
        }, path)
        updates[node] = path
    context = SimpleNamespace(
        target_nodes=(1, 2), round_index=1, rounds=1,
        local_config={'client_preparation_reports': {
            1: _preparation(1), 2: _preparation(3),
        }},
        check_stop=lambda: None,
    )
    output = tmp_path / 'result.pt'

    aggregate_entry(initial, updates, output, {'data_seed': 1}, context)

    result = load_model(output)
    for parameter in result.model.parameters():
        torch.testing.assert_close(parameter, torch.full_like(parameter, 5), rtol=0, atol=0)
