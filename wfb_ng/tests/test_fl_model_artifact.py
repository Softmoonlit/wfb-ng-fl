"""通过公共制品接口验证浮点权重及整数buffer的严格边界。"""

import pytest

torch = pytest.importorskip('torch')

from wfb_ng.fl.errors import FLRuntimeError
from wfb_ng.fl.model_artifact import ModelDefinition, initialize_model, load_model, write_model_atomic


DEFINITION = ModelDefinition(
    model_format='test-batchnorm-fp32-v1',
    initialization_source='batchnorm-default-v1',
    model_factory=lambda: torch.nn.BatchNorm2d(3, device='cpu', dtype=torch.float32),
    initialize_parameters=lambda model, generator: None,
    error_code='invalid_test_model',
    artifact_label='测试模型',
)


def test_integer_buffer_roundtrip_preserves_dtype_and_value(tmp_path):
    initial = tmp_path / 'initial.pt'
    digest = initialize_model(initial, 7, DEFINITION)
    artifact = load_model(initial, DEFINITION)
    assert artifact.sha256 == digest
    assert artifact.model.num_batches_tracked.dtype == torch.int64
    artifact.model(torch.ones(2, 3, 2, 2))
    updated = tmp_path / 'updated.pt'
    write_model_atomic(updated, dict(artifact.model.state_dict()), 7, DEFINITION)
    assert load_model(updated, DEFINITION).model.num_batches_tracked.item() == 1
    assert load_model(initial, DEFINITION).model.num_batches_tracked.item() == 0


@pytest.mark.parametrize('defect', ['integer_weight', 'float_counter', 'int32_counter', 'negative_counter'])
def test_wrong_parameter_or_counter_type_is_rejected_without_publication(tmp_path, defect):
    initial = tmp_path / 'initial.pt'
    initialize_model(initial, 7, DEFINITION)
    state = dict(load_model(initial, DEFINITION).model.state_dict())
    if defect == 'integer_weight':
        state['weight'] = torch.ones(3, dtype=torch.int64)
    elif defect == 'float_counter':
        state['num_batches_tracked'] = torch.tensor(1.0)
    elif defect == 'int32_counter':
        state['num_batches_tracked'] = torch.tensor(1, dtype=torch.int32)
    else:
        state['num_batches_tracked'] = torch.tensor(-1, dtype=torch.int64)
    output = tmp_path / 'invalid.pt'
    with pytest.raises(FLRuntimeError) as error:
        write_model_atomic(output, state, 7, DEFINITION)
    assert error.value.error_code == 'invalid_test_model'
    assert not output.exists()
