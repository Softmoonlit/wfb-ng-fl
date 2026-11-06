"""固定模型制品的严格序列化与原子发布边界。"""

from dataclasses import dataclass
import hashlib
import io
import os
from pathlib import Path
import tempfile
from typing import Callable, Union

import torch
from torch import nn
from torch.types import Storage

from .artifacts import fsync_directory, preflight_artifact
from .errors import FLRuntimeError


ModelPath = Union[str, Path]


@dataclass(frozen=True)
class ModelDefinition:
    """一个显式模型格式的结构、初始化和错误边界。"""

    model_format: str
    initialization_source: str
    model_factory: Callable[[], nn.Module]
    initialize_parameters: Callable[[nn.Module, torch.Generator], None]
    error_code: str
    artifact_label: str
    max_model_bytes: int = 1024 * 1024
    publication_error_code: str = 'model_artifact_write_failed'
    cleanup_error_code: str = 'model_artifact_cleanup_failed'
    cleanup_error_message: str = '模型发布临时文件清理未完成，请检查输出路径'


@dataclass(frozen=True)
class ModelArtifact:
    model: nn.Module
    initialization_seed: int
    sha256: str


def _seed(seed: int, definition: ModelDefinition) -> int:
    if type(seed) is not int or not 0 <= seed < 2 ** 63:
        raise FLRuntimeError(definition.error_code, '初始化种子必须为 0～2**63-1 的整数')
    return seed


def validate_state_dict(state, definition: ModelDefinition) -> None:
    """严格检查CPU/FP32参数，并按模型结构保留非负int64计数buffer。"""
    with torch.random.fork_rng(devices=[]):
        model = definition.model_factory()
        expected = model.state_dict()
        integer_buffers = {name for name, value in model.named_buffers()
                           if value.dtype == torch.int64}
    if type(state) is not dict or set(state) != set(expected):
        raise FLRuntimeError(definition.error_code, f'{definition.artifact_label}参数键不匹配')
    for name, tensor in state.items():
        if (type(tensor) is not torch.Tensor or tensor.layout != torch.strided
                or tensor.device.type != 'cpu' or tensor.dtype != expected[name].dtype
                or not (tensor.dtype == torch.float32
                        or (name in integer_buffers and tensor.dtype == torch.int64
                            and (tensor >= 0).all().item()))
                or tensor.shape != expected[name].shape
                or not torch.isfinite(tensor).all().item()):
            raise FLRuntimeError(
                definition.error_code,
                f'{definition.artifact_label}参数 {name} 的形状、类型或数值无效',
            )


def _validate_payload(value, definition: ModelDefinition) -> int:
    if (type(value) is not dict
            or set(value) != {'model_format', 'state_dict', 'initialization'}
            or value['model_format'] != definition.model_format):
        raise FLRuntimeError(definition.error_code, f'{definition.artifact_label}格式或字段不匹配')
    initialization = value['initialization']
    if (type(initialization) is not dict
            or set(initialization) != {'source', 'seed'}
            or initialization['source'] != definition.initialization_source):
        raise FLRuntimeError(definition.error_code, f'{definition.artifact_label}初始化来源不匹配')
    seed = _seed(initialization['seed'], definition)
    validate_state_dict(value['state_dict'], definition)
    return seed


def write_model_atomic(output_path: ModelPath, state_dict: dict, initialization_seed: int,
                       definition: ModelDefinition) -> str:
    """严格校验 CPU 参数，以原子方式发布一个全新的模型文件。"""
    seed = _seed(initialization_seed, definition)
    validate_state_dict(state_dict, definition)
    value = {
        'model_format': definition.model_format,
        'state_dict': state_dict,
        'initialization': {'source': definition.initialization_source, 'seed': seed},
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
        digest = load_model(temporary, definition).sha256
        os.link(temporary, output)
        published = True
        fsync_directory(output.parent)
    except OSError as exc:
        if published:
            try:
                os.unlink(output)
            except OSError as cleanup_error:
                raise FLRuntimeError(
                    definition.cleanup_error_code,
                    '模型发布失败且输出清理失败，请检查输出路径',
                ) from cleanup_error
        raise FLRuntimeError(
            definition.publication_error_code,
            '模型生成失败，输出须为可写的新文件路径',
        ) from exc
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError as cleanup_error:
                raise FLRuntimeError(
                    definition.cleanup_error_code,
                    definition.cleanup_error_message,
                ) from cleanup_error
    return digest


def initialize_model(output_path: ModelPath, seed: int, definition: ModelDefinition) -> str:
    """生成固定格式的未训练 CPU/FP32 模型。"""
    seed = _seed(seed, definition)
    with torch.random.fork_rng(devices=[]):
        model = definition.model_factory()
        generator = torch.Generator(device='cpu').manual_seed(seed)
        definition.initialize_parameters(model, generator)
    return write_model_atomic(output_path, dict(model.state_dict()), seed, definition)


def load_model(model_path: ModelPath, definition: ModelDefinition) -> ModelArtifact:
    """从同一份字节快照严格读取模型，并返回独立的 CPU 模型。"""
    stream, _ = preflight_artifact(model_path, definition.max_model_bytes)
    try:
        data = stream.read(definition.max_model_bytes + 1)
    finally:
        stream.close()
    if len(data) > definition.max_model_bytes:
        raise FLRuntimeError(
            definition.error_code,
            f'{definition.artifact_label}文件超过 {definition.max_model_bytes} 字节',
        )
    def cpu_storage(storage: Storage, location: str) -> Storage:
        if location != 'cpu':
            raise FLRuntimeError(definition.error_code, '模型文件必须保存 CPU 张量')
        return storage

    try:
        value = torch.load(io.BytesIO(data), map_location=cpu_storage, weights_only=True)
        with torch.random.fork_rng(devices=[]):
            model = definition.model_factory()
        seed = _validate_payload(value, definition)
        model.load_state_dict(value['state_dict'], strict=True)
        return ModelArtifact(model, seed, hashlib.sha256(data).hexdigest())
    except FLRuntimeError:
        raise
    except Exception as exc:
        raise FLRuntimeError(
            definition.error_code,
            f'{definition.artifact_label}格式或参数无效',
        ) from exc
