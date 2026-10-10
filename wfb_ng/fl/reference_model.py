"""固定 MNIST 模型的初始化与制品读取，不承担训练或作业推进。"""

import argparse
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Union

import torch
from torch import nn
from torch.nn import functional as F
from torch.types import Storage

from .artifacts import fsync_directory, preflight_artifact
from .errors import FLRuntimeError


MODEL_FORMAT = 'mnist-two-conv-fp32-v1'
INITIALIZATION_SOURCE = 'xavier-uniform-weights-zero-bias-v1'
MAX_MODEL_BYTES = 1024 * 1024
ModelPath = Union[str, Path]


class MNISTReferenceModel(nn.Module):
    """输入为 [0, 1] 的 FP32 灰度图，输出十类 logits。"""

    def __init__(self) -> None:
        super().__init__()
        self.conv1 = nn.Conv2d(1, 8, 3, padding=1, device='cpu', dtype=torch.float32)
        self.conv2 = nn.Conv2d(8, 16, 3, padding=1, device='cpu', dtype=torch.float32)
        self.classifier = nn.Linear(16 * 7 * 7, 10, device='cpu', dtype=torch.float32)

    def forward(self, images: torch.Tensor) -> torch.Tensor:
        images = F.max_pool2d(F.relu(self.conv1(images)), 2)
        images = F.max_pool2d(F.relu(self.conv2(images)), 2)
        return self.classifier(images.flatten(1))


@dataclass(frozen=True)
class ModelArtifact:
    model: MNISTReferenceModel
    initialization_seed: int
    sha256: str


def _seed(seed: int) -> int:
    if type(seed) is not int or not 0 <= seed < 2 ** 63:
        raise FLRuntimeError('invalid_reference_model', '初始化种子必须为 0～2**63-1 的整数')
    return seed


def initialize_model(output_path: ModelPath, seed: int) -> str:
    """生成未训练的 CPU／FP32 模型；拒绝覆盖现有文件，返回完整摘要。"""
    _seed(seed)
    # 只保存并恢复 CPU 随机数状态，不访问或初始化 CUDA。
    with torch.random.fork_rng(devices=[]):
        model = MNISTReferenceModel()
        generator = torch.Generator(device='cpu').manual_seed(seed)
        for layer in (model.conv1, model.conv2, model.classifier):
            nn.init.xavier_uniform_(layer.weight, generator=generator)
            assert layer.bias is not None
            nn.init.zeros_(layer.bias)
    value = {
        'model_format': MODEL_FORMAT,
        'state_dict': dict(model.state_dict()),
        'initialization': {'source': INITIALIZATION_SOURCE, 'seed': seed},
    }
    output = Path(output_path).absolute()
    temporary = None
    published = False
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix=f'.{output.name}.',
                                         delete=False) as stream:
            temporary = stream.name
            torch.save(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        digest = load_model(temporary).sha256
        # 同目录硬链接原子发布且不覆盖已存在路径，保护已有模型制品。
        os.link(temporary, output)
        published = True
        fsync_directory(output.parent)
    except OSError as exc:
        if published:
            try:
                os.unlink(output)
            except OSError as cleanup_error:
                raise FLRuntimeError('reference_model_cleanup_failed',
                                     '模型发布失败且输出清理失败，请检查输出路径') from cleanup_error
        raise FLRuntimeError('reference_model_write_failed', '模型生成失败，输出须为可写的新文件路径') from exc
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError as cleanup_error:
                raise FLRuntimeError('reference_model_cleanup_failed',
                                     '模型文件清理未完成，目标模型可能仍然存在；请检查输出路径和临时文件') from cleanup_error
    return digest


def _cpu_storage(storage: Storage, location: str) -> Storage:
    if location != 'cpu':
        raise FLRuntimeError('invalid_reference_model', '模型文件必须保存 CPU 张量')
    return storage


def _validate_payload(value: Any, model: MNISTReferenceModel) -> int:
    if (type(value) is not dict
            or set(value) != {'model_format', 'state_dict', 'initialization'}
            or value['model_format'] != MODEL_FORMAT):
        raise FLRuntimeError('invalid_reference_model', '参考模型格式或字段不匹配')
    initialization = value['initialization']
    if (type(initialization) is not dict or set(initialization) != {'source', 'seed'}
            or initialization['source'] != INITIALIZATION_SOURCE):
        raise FLRuntimeError('invalid_reference_model', '参考模型初始化来源不匹配')
    seed = _seed(initialization['seed'])
    state = value['state_dict']
    expected = model.state_dict()
    if type(state) is not dict or set(state) != set(expected):
        raise FLRuntimeError('invalid_reference_model', '参考模型参数键不匹配')
    for name, tensor in state.items():
        if (type(tensor) is not torch.Tensor or tensor.layout != torch.strided
                or tensor.device.type != 'cpu' or tensor.dtype != torch.float32
                or tensor.shape != expected[name].shape
                or not torch.isfinite(tensor).all().item()):
            raise FLRuntimeError('invalid_reference_model', f'参数 {name} 的形状、类型或数值无效')
    return seed


def load_model(model_path: ModelPath) -> ModelArtifact:
    """返回可独立修改的 CPU 模型及实际读取内容的 SHA-256，不改写源文件。"""
    stream, _ = preflight_artifact(model_path, MAX_MODEL_BYTES)
    try:
        data = stream.read(MAX_MODEL_BYTES + 1)
    finally:
        stream.close()
    if len(data) > MAX_MODEL_BYTES:
        raise FLRuntimeError('invalid_reference_model', '参考模型文件超过 1 MiB')
    try:
        value = torch.load(io.BytesIO(data), map_location=_cpu_storage, weights_only=True)
        with torch.random.fork_rng(devices=[]):
            model = MNISTReferenceModel()
        seed = _validate_payload(value, model)
        model.load_state_dict(value['state_dict'], strict=True)
        return ModelArtifact(model, seed, hashlib.sha256(data).hexdigest())
    except FLRuntimeError:
        raise
    except Exception as exc:
        raise FLRuntimeError('invalid_reference_model', '参考模型格式或参数无效') from exc


def main() -> None:
    parser = argparse.ArgumentParser(description='生成固定 MNIST 参考任务的未训练初始模型')
    parser.add_argument('output_path', help='新模型文件路径；不覆盖已有文件')
    parser.add_argument('--seed', type=int, required=True, help='初始化种子，范围 0～2**63-1')
    args = parser.parse_args()
    try:
        digest = initialize_model(args.output_path, args.seed)
    except FLRuntimeError as exc:
        parser.error(str(exc))
    print(json.dumps({'sha256': digest, 'model_format': MODEL_FORMAT,
                      'initialization': {'source': INITIALIZATION_SOURCE, 'seed': args.seed}},
                     ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
