"""验证两个 MNIST 算法 bundle 的入口身份不会互相回退。"""

from wfb_ng.fl.console import _project_algorithm
from wfb_ng.fl.mnist_deployment import get_mnist_deployment
from wfb_ng.fl.errors import FLRuntimeError
from wfb_ng.fl.server_daemon import ServerDaemonConfig, _mnist_module_for_entries


OLD = (
    'wfb_ng.fl.mnist:client_entry',
    'wfb_ng.fl.mnist:aggregate_entry',
    'wfb_ng.fl.mnist:evaluate_entry',
)
COMPACT = (
    'wfb_ng.fl.mnist_cnn_32_64:client_entry',
    'wfb_ng.fl.mnist_cnn_32_64:aggregate_entry',
    'wfb_ng.fl.mnist_cnn_32_64:evaluate_entry',
)


def test_bundles_have_explicit_identities_and_no_partial_match():
    old = get_mnist_deployment(*OLD)
    compact = get_mnist_deployment(*COMPACT)
    assert old is not None and old.algorithm_id == 'mnist-two-conv-fp32-v1'
    assert compact is not None and compact.model_format == 'mnist-cnn-32-64-fp32-v1'
    assert get_mnist_deployment(COMPACT[0], OLD[1], OLD[2]) is None


def test_server_rejects_partial_known_bundle():
    import pytest

    with pytest.raises(FLRuntimeError, match='完整匹配'):
        ServerDaemonConfig(
            client_entry=COMPACT[0], aggregate_entry=OLD[1], evaluate_entry=COMPACT[2])


def test_server_and_console_select_compact_bundle():
    module = _mnist_module_for_entries(*COMPACT)
    assert module.MODEL_FORMAT == 'mnist-cnn-32-64-fp32-v1'
    projected = _project_algorithm({
        'client_entry': COMPACT[0], 'aggregate_entry': COMPACT[1],
        'evaluate_entry': COMPACT[2], 'config': {'data_seed': 2026},
    })
    assert projected['client_entry'] == COMPACT[0]
    assert projected['config'] == {'data_seed': 2026}
