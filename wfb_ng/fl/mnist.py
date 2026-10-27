"""固定 MNIST 参考计算；作业和传输推进由宿主及 Runtime 拥有。"""
from pathlib import Path
import io
import math
import os
import re
import tempfile
from collections.abc import Mapping

import torch

from .artifacts import fsync_directory, preflight_artifact
from .errors import FLRuntimeError
from .mnist_data import load_mnist
from .reference_model import (MODEL_FORMAT, MAX_MODEL_BYTES, load_model,
                              MNISTReferenceModel, ModelPath, validate_state_dict, write_model_atomic)


UPDATE_FIELDS = {'model_format', 'state_dict', 'base_model_sha256', 'node_id',
                 'round_index', 'sample_count', 'optimizer_steps', 'train_loss', 'device'}
BATCH_SIZE = 64


def validate_config(config):
    """仅接受显式数据加载种子；路径属于各端本地配置。"""
    if (not isinstance(config, Mapping) or set(config) != {'data_seed'}
            or type(config['data_seed']) is not int or not 0 < config['data_seed'] < 2**63):
        raise FLRuntimeError('invalid_algorithm_config', 'algorithm_config 仅允许正整数 data_seed（小于 2**63）')
    return config['data_seed']


def validate_preparation_report(report, *, expected_samples=60000, require_gpu=True, expected_seed=None):
    """宿主复核有限准备事实；不修改报告，也不接受缺失或额外字段。"""
    if (type(expected_samples) is not int or not 0 < expected_samples <= 60000
            or type(require_gpu) is not bool
            or (expected_seed is not None and
                (type(expected_seed) is not int or not 0 < expected_seed < 2**63))):
        raise FLRuntimeError('invalid_mnist_preparation', '准备报告检查条件无效')
    fields = {'model_format', 'sample_count', 'data_sha256', 'device', 'data_seed'}
    if (not isinstance(report, Mapping) or set(report) != fields
            or report['model_format'] != MODEL_FORMAT
            or type(report['sample_count']) is not int or report['sample_count'] != expected_samples
            or type(report['data_seed']) is not int or not 0 < report['data_seed'] < 2**63
            or (expected_seed is not None and report['data_seed'] != expected_seed)):
        raise FLRuntimeError('invalid_mnist_preparation', '准备报告格式、样本数或 data_seed 不匹配')
    prefix = 't10k' if expected_samples == 10000 and not require_gpu else 'train'
    digests = report['data_sha256']
    if (not isinstance(digests, Mapping)
            or set(digests) != {f'{prefix}-images-idx3-ubyte', f'{prefix}-labels-idx1-ubyte'}
            or any(type(value) is not str or re.fullmatch(r'[0-9a-f]{64}', value) is None
                   for value in digests.values())):
        raise FLRuntimeError('invalid_mnist_preparation', '准备报告 IDX SHA-256 字段无效')
    device = report['device']
    if (not isinstance(device, Mapping)
            or set(device) != {'type', 'name', 'torch_version', 'cuda_version'}
            or any(type(value) is not str or not 0 < len(value) <= 128 for value in device.values())
            or device['type'] not in ('cuda', 'cpu')
            or (require_gpu and device['type'] != 'cuda')
            or (device['type'] == 'cuda' and device['cuda_version'] == 'unavailable')):
        raise FLRuntimeError('invalid_mnist_preparation', '准备报告实际设备或依赖描述无效')


def _cpu_storage(storage, location):
    if location != 'cpu':
        raise FLRuntimeError('invalid_mnist_update', 'update 必须保存 CPU 参数')
    return storage


def _load_update(path):
    stream, _ = preflight_artifact(path, MAX_MODEL_BYTES)
    try:
        data = stream.read(MAX_MODEL_BYTES + 1)
    finally:
        stream.close()
    try:
        if len(data) > MAX_MODEL_BYTES:
            raise ValueError('update 超过大小限制')
        return torch.load(io.BytesIO(data), weights_only=True, map_location=_cpu_storage)
    except FLRuntimeError:
        raise
    except Exception as exc:
        raise FLRuntimeError('invalid_mnist_update', 'update 文件格式无效') from exc


def _validate_update(value, node, round_index, base_sha):
    if (type(value) is not dict or set(value) != UPDATE_FIELDS
            or value['model_format'] != MODEL_FORMAT
            or type(value['node_id']) is not int or value['node_id'] != node
            or type(value['round_index']) is not int or value['round_index'] != round_index
            or value['base_model_sha256'] != base_sha):
        raise FLRuntimeError('invalid_mnist_update', 'update 字段、节点、轮序或基础模型摘要不匹配')
    samples, steps, loss = value['sample_count'], value['optimizer_steps'], value['train_loss']
    if (type(samples) is not int or not 0 < samples <= 60000
            or type(steps) is not int or steps != (samples + BATCH_SIZE - 1) // BATCH_SIZE
            or type(loss) not in (int, float) or not math.isfinite(loss) or loss < 0):
        raise FLRuntimeError('invalid_mnist_update', 'update 样本数、优化器步数或训练损失无效')
    device = value['device']
    if (type(device) is not dict or set(device) != {'type', 'name', 'torch_version', 'cuda_version'}
            or device['type'] != 'cuda'
            or any(type(item) is not str or not 0 < len(item) <= 128 for item in device.values())):
        raise FLRuntimeError('invalid_mnist_update', 'update 训练设备必须为有限 GPU 描述')
    validate_state_dict(value['state_dict'])


def read_update_metadata(path: ModelPath, *, node_id: int, round_index: int,
                         base_model_sha256: str, expected_sample_count: int) -> dict:
    """严格读取参考 update 的有限事实，不返回参数，也不推断训练或作业成功。"""
    if (type(node_id) is not int or not 1 <= node_id <= 10
            or type(round_index) is not int or not 0 < round_index < 2**63
            or type(base_model_sha256) is not str
            or re.fullmatch(r'[0-9a-f]{64}', base_model_sha256) is None
            or type(expected_sample_count) is not int or not 0 < expected_sample_count <= 60000):
        raise FLRuntimeError('invalid_mnist_update', 'update 元数据预期身份、摘要或样本约束无效')
    value = _load_update(path)
    try:
        _validate_update(value, node_id, round_index, base_model_sha256)
    except (TypeError, ValueError, OverflowError, RuntimeError) as exc:
        raise FLRuntimeError('invalid_mnist_update', 'update 元数据或参数格式无效') from exc
    if value['sample_count'] != expected_sample_count:
        raise FLRuntimeError('invalid_mnist_update', 'update 实际样本数与预期样本数不一致')
    return {'model_format': value['model_format'],
            'base_model_sha256': value['base_model_sha256'],
            'node_id': value['node_id'], 'round_index': value['round_index'],
            'sample_count': value['sample_count'], 'optimizer_steps': value['optimizer_steps'],
            'train_loss': float(value['train_loss']), 'device': dict(value['device'])}


def aggregate_entry(current_model_path, updates_by_node, output_model_path, config, context):
    """按实际样本数合并完整参数，保留初始模型来源。"""
    seed = validate_config(config)
    context.check_stop()
    targets = tuple(context.target_nodes)
    if (not targets or any(type(node) is not int or not 1 <= node <= 10 for node in targets)
            or len(set(targets)) != len(targets)
            or not isinstance(updates_by_node, Mapping)
            or any(type(node) is not int for node in updates_by_node)
            or set(updates_by_node) != set(targets)):
        raise FLRuntimeError('invalid_mnist_update', 'update 必须包含完整固定节点集合')
    if type(context.round_index) is not int or not 1 <= context.round_index <= context.rounds:
        raise FLRuntimeError('invalid_mnist_update', '聚合轮序无效')
    if Path(output_model_path).resolve() in {Path(p).resolve() for p in
                                           [current_model_path, *updates_by_node.values()]}:
        raise FLRuntimeError('invalid_mnist_update', '输出模型不得覆盖输入制品')
    preparations = context.local_config.get('client_preparation_reports')
    if (not isinstance(preparations, Mapping) or set(preparations) != set(targets)
            or any(type(node) is not int for node in preparations)):
        raise FLRuntimeError('invalid_mnist_preparation', '聚合缺少完整的客户端准备报告')
    base = load_model(current_model_path)
    updates = []
    for node in targets:
        context.check_stop()
        report = preparations[node]
        accepted_samples = report.get('sample_count') if isinstance(report, Mapping) else None
        validate_preparation_report(report, expected_samples=accepted_samples, expected_seed=seed)
        value = _load_update(updates_by_node[node])
        _validate_update(value, node, context.round_index, base.sha256)
        if value['sample_count'] != accepted_samples:
            raise FLRuntimeError('invalid_mnist_update', 'update 实际样本数与已通过准备的样本数不一致')
        updates.append(value)
    total = sum(value['sample_count'] for value in updates)
    state = {name: torch.zeros_like(tensor) for name, tensor in base.model.state_dict().items()}
    for value in updates:
        context.check_stop()
        for name in state:
            state[name].add_(value['state_dict'][name], alpha=value['sample_count'] / total)
    context.check_stop()
    write_model_atomic(output_model_path, state, base.initialization_seed)


def _device_report(device):
    return {'type': device.type,
            'name': torch.cuda.get_device_name(device) if device.type == 'cuda' else 'CPU',
            'torch_version': str(torch.__version__),
            'cuda_version': str(torch.version.cuda) if torch.version.cuda else 'unavailable'}


def _preparation(dataset, device, data_seed):
    report = {'model_format': MODEL_FORMAT, 'sample_count': len(dataset.labels),
              'data_sha256': dict(dataset.sha256), 'device': _device_report(device),
              'data_seed': data_seed}
    validate_preparation_report(report, expected_samples=len(dataset.labels),
                                require_gpu=device.type == 'cuda', expected_seed=data_seed)
    return report


def _batches(dataset, device, order):
    for start in range(0, len(order), BATCH_SIZE):
        indices = order[start:start + BATCH_SIZE]
        images = dataset.images[indices].to(device=device, dtype=torch.float32).div_(255.0)
        labels = dataset.labels[indices].to(device=device)
        yield images, labels


def evaluate_entry(model_path, config, context):
    """服务端固定测试集评估；使用 CPU 并如实记录，末轮判定相对初始改善。"""
    seed = validate_config(config)
    context.check_stop()
    artifact = load_model(model_path)
    dataset = load_mnist(context.local_config.get('mnist_data_dir'), 'test', context.check_stop)
    device = torch.device('cpu')
    model = artifact.model.to(device).eval()
    loss_sum, correct, count = 0.0, 0, 0
    with torch.inference_mode():
        for images, labels in _batches(dataset, device, torch.arange(len(dataset.labels))):
            context.check_stop()
            logits = model(images)
            loss = torch.nn.functional.cross_entropy(logits, labels, reduction='sum').item()
            if not math.isfinite(loss):
                raise FLRuntimeError('invalid_mnist_metrics', '测试损失不是有限数值')
            loss_sum += loss
            correct += (logits.argmax(dim=1) == labels).sum().item()
            count += len(labels)
    metrics = {'loss': loss_sum / count, 'accuracy': correct / count}
    verdict, reason = None, None
    if context.round_index == context.rounds:
        baseline = context.initial_metrics
        if (not isinstance(baseline, Mapping) or set(baseline) != {'loss', 'accuracy'}
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in baseline.values())
                or baseline['loss'] < 0 or not 0 <= baseline['accuracy'] <= 1):
            raise FLRuntimeError('invalid_mnist_metrics', '末轮评估缺少有效初始指标')
        verdict = metrics['accuracy'] > baseline['accuracy'] and metrics['loss'] < baseline['loss']
        reason = '末轮准确率高于初始且损失低于初始' if verdict else '末轮准确率和损失未同时优于初始'
    context.check_stop()
    return {'metrics': metrics,
            'preparation': _preparation(dataset, device, seed) if context.round_index == 0 else None,
            'verdict': verdict, 'reason': reason}


def _prepare_gpu():
    if not torch.cuda.is_available():
        raise FLRuntimeError('mnist_gpu_unavailable', 'MNIST 训练要求 GPU，CUDA 不可用')
    device = torch.device('cuda', 0)
    try:
        # 准备屏障前实际执行固定网络的前反向与 SGD，及时暴露依赖或内核错误。
        with torch.random.fork_rng(devices=[]):
            probe = MNISTReferenceModel().to(device)
        loss = torch.nn.functional.cross_entropy(
            probe(torch.zeros(2, 1, 28, 28, dtype=torch.float32, device=device)),
            torch.zeros(2, dtype=torch.long, device=device))
        loss.backward()
        torch.optim.SGD(probe.parameters(), lr=0.01, momentum=0, weight_decay=0).step()
        torch.cuda.synchronize(device)
        if not math.isfinite(loss.item()):
            raise ValueError('GPU 准备损失无效')
    except Exception as exc:
        raise FLRuntimeError('mnist_gpu_preparation_failed', 'GPU 前反向或优化器准备失败') from exc
    return device


def _write_update_atomic(path, value, context):
    temporary, published = None, False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f'.{path.name}.', delete=False) as stream:
            temporary = stream.name
            torch.save(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        restored = _load_update(temporary)
        _validate_update(restored, value['node_id'], value['round_index'], value['base_model_sha256'])
        context.check_stop()
        os.link(temporary, path)
        published = True
        fsync_directory(path.parent)
    except OSError as exc:
        if published:
            path.unlink()
        raise FLRuntimeError('mnist_update_write_failed', 'update 原子发布失败，输出须为新文件') from exc
    finally:
        if temporary is not None:
            os.unlink(temporary)


def client_entry(runtime, config, context):
    """GPU 准备通过后拥有本地完整轮次循环，提交确认后才报告完成。"""
    seed = validate_config(config)
    context.check_stop()
    device = _prepare_gpu()
    context.check_stop()
    dataset = load_mnist(context.local_config.get('mnist_data_dir'), 'train', context.check_stop)
    context.report_prepared(_preparation(dataset, device, seed))
    generator = torch.Generator(device='cpu').manual_seed(seed)
    for index in range(1, context.rounds + 1):
        current = context.for_phase('training', round_index=index)
        current.check_stop()
        artifact = load_model(runtime.wait_for_model())
        current.check_stop()
        model = artifact.model.to(device).train()
        optimizer = torch.optim.SGD(model.parameters(), lr=0.01, momentum=0, weight_decay=0)
        samples, steps, loss_sum = 0, 0, 0.0
        order = torch.randperm(len(dataset.labels), generator=generator)
        for images, labels in _batches(dataset, device, order):
            current.check_stop()
            optimizer.zero_grad(set_to_none=True)
            loss = torch.nn.functional.cross_entropy(model(images), labels)
            if not math.isfinite(loss.item()):
                raise FLRuntimeError('invalid_mnist_metrics', '训练损失不是有限数值')
            loss.backward()
            optimizer.step()
            samples += len(labels)
            steps += 1
            loss_sum += loss.item() * len(labels)
        if samples != 60000 or steps != 938:
            raise FLRuntimeError('invalid_mnist_training', '本轮必须完整执行 60000 样本及尾批次')
        value = {'model_format': MODEL_FORMAT,
                 'state_dict': {name: tensor.detach().cpu().clone() for name, tensor in model.state_dict().items()},
                 'base_model_sha256': artifact.sha256, 'node_id': current.node_id,
                 'round_index': index, 'sample_count': samples, 'optimizer_steps': steps,
                 'train_loss': loss_sum / samples, 'device': _device_report(device)}
        current.check_stop()
        output = Path(current.artifact_dir) / f'update-{index:04d}.pt'
        _write_update_atomic(output, value, current)
        current.check_stop()
        runtime.submit_update(str(output))
        current.report_round_complete(index)
