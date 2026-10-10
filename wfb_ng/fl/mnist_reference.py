"""固定 MNIST／FP32 参考计算；轮次推进与文件传输由宿主负责。"""

import argparse
import hashlib
import io
import math
import os
from pathlib import Path
import struct
import tempfile

import torch
from torch import nn
from torch.nn import functional as F
from torch.utils.data import DataLoader, TensorDataset

from .artifacts import file_sha256, fsync_directory, preflight_artifact
from .errors import FLRuntimeError


MODEL_FORMAT = 'mnist-two-conv-fp32-v1'
BATCH_SIZE = 64
REFERENCE_ROUNDS = 5
MAX_ALGORITHM_FILE_BYTES = 1024 * 1024
MODEL_FIELDS = {'model_format', 'state_dict', 'initialization_seed',
                'round_index', 'provenance'}
UPDATE_FIELDS = {'model_format', 'state_dict', 'base_model_sha256', 'node_id',
                 'round_index', 'sample_count', 'optimizer_steps', 'train_loss',
                 'device', 'training_seed', 'software'}


class MNISTModel(nn.Module):
    """28×28 → Conv(8) → 池化 → Conv(16) → 池化 → 10 类 logits。"""

    def __init__(self):
        super().__init__()
        self.conv1 = nn.Conv2d(1, 8, 3, padding=1)
        self.conv2 = nn.Conv2d(8, 16, 3, padding=1)
        self.classifier = nn.Linear(16 * 7 * 7, 10)

    def forward(self, images):
        images = F.max_pool2d(F.relu(self.conv1(images)), 2)
        images = F.max_pool2d(F.relu(self.conv2(images)), 2)
        return self.classifier(images.flatten(1))


def _fail(message):
    raise FLRuntimeError('algorithm_failed', message)


def _integer(value, name, minimum=1):
    if type(value) is not int or value < minimum:
        _fail(f'{name} 必须是大于等于 {minimum} 的整数')
    return value


def _config(config):
    if type(config) is not dict or set(config) - {'training_seed', 'evaluation_device'}:
        _fail('参考配置仅允许 training_seed 和 evaluation_device')
    seed = _integer(config.get('training_seed', 0), 'training_seed', 0)
    if seed >= 2 ** 63:
        _fail('training_seed 必须小于 2**63')
    device = config.get('evaluation_device', 'cpu')
    if device not in ('cpu', 'cuda:0'):
        _fail('evaluation_device 必须为 cpu 或 cuda:0')
    return seed, device


def _save(path, value, context=None):
    path = Path(path).absolute()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f'.{path.name}.',
                                         delete=False) as stream:
            temporary = stream.name
            torch.save(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        if context is not None:
            context.check_stop()
        os.replace(temporary, path)
        temporary = None
        fsync_directory(str(path.parent))
    finally:
        if temporary is not None:
            os.unlink(temporary)


def _snapshot(path, size_limit):
    source, _ = preflight_artifact(path, size_limit)
    try:
        data = source.read(size_limit + 1)
    finally:
        source.close()
    if len(data) > size_limit:
        _fail('输入文件超过参考计算大小限制')
    return data


def _load(path, fields):
    data = _snapshot(path, MAX_ALGORITHM_FILE_BYTES)
    # 同一字节快照同时用于加载和摘要，避免重新打开路径产生身份竞态。
    try:
        value = torch.load(io.BytesIO(data), map_location='cpu', weights_only=True)
    except Exception as exc:
        raise FLRuntimeError('algorithm_failed', '无法加载参考算法文件') from exc
    if type(value) is not dict or set(value) != fields:
        _fail('参考算法文件字段不匹配')
    if value['model_format'] != MODEL_FORMAT:
        _fail('模型格式不匹配')
    _validate_state(value['state_dict'])
    return value, hashlib.sha256(data).hexdigest()


def _validate_state(state):
    # 结构来自固定模型，构造时不改变宿主的 CPU 随机数状态。
    with torch.random.fork_rng(devices=[]):
        expected = MNISTModel().state_dict()
    if type(state) is not dict or set(state) != set(expected):
        _fail('参数键不匹配')
    for key, tensor in state.items():
        if (type(tensor) is not torch.Tensor or tensor.layout != torch.strided
                or tensor.device.type != 'cpu' or tensor.dtype != torch.float32
                or tensor.shape != expected[key].shape
                or not torch.isfinite(tensor).all().item()):
            _fail(f'参数 {key} 的形状、类型或数值无效')


def _model(path):
    value, digest = _load(path, MODEL_FIELDS)
    _integer(value['initialization_seed'], 'initialization_seed', 0)
    _integer(value['round_index'], 'round_index', 0)
    if type(value['provenance']) is not dict:
        _fail('模型来源必须为 object')
    return value, digest


def initialize_model(output_model_path, seed=0):
    """生成固定结构的未训练模型，所有层 Xavier uniform、偏置为零。"""
    _integer(seed, 'initialization_seed', 0)
    if seed >= 2 ** 63:
        _fail('initialization_seed 必须小于 2**63')
    with torch.random.fork_rng(devices=[]):
        torch.random.default_generator.manual_seed(seed)
        model = MNISTModel()
        for layer in (model.conv1, model.conv2, model.classifier):
            nn.init.xavier_uniform_(layer.weight)
            nn.init.zeros_(layer.bias)
    _save(output_model_path, {
        'model_format': MODEL_FORMAT, 'state_dict': dict(model.state_dict()),
        'initialization_seed': seed, 'round_index': 0,
        'provenance': {'initialization': 'xavier_uniform_weights_zero_bias'},
    })


def load_mnist(data_dir, split, context):
    """直接读取解压 IDX，统一为 uint8 张量；归一化留在计算批次执行。"""
    context.check_stop()
    if split not in ('train', 'test'):
        _fail('MNIST split 必须为 train 或 test')
    prefix, count = ('train', 60000) if split == 'train' else ('t10k', 10000)
    images_path = Path(data_dir) / f'{prefix}-images-idx3-ubyte'
    labels_path = Path(data_dir) / f'{prefix}-labels-idx1-ubyte'
    images = bytearray(_snapshot(images_path, 16 + count * 784))
    labels = bytearray(_snapshot(labels_path, 8 + count))
    if (len(images) != 16 + count * 784 or len(labels) != 8 + count
            or struct.unpack('>IIII', images[:16]) != (2051, count, 28, 28)
            or struct.unpack('>II', labels[:8]) != (2049, count)):
        _fail('MNIST IDX 类型、维度、计数或长度错误')
    x = torch.frombuffer(images, dtype=torch.uint8, offset=16).reshape(count, 1, 28, 28)
    y = torch.frombuffer(labels, dtype=torch.uint8, offset=8)
    if y.max().item() > 9:
        _fail('MNIST 标签必须在 0～9 范围内')
    context.check_stop()
    return TensorDataset(x, y), {
        'sample_count': count,
        'images_sha256': hashlib.sha256(images).hexdigest(),
        'labels_sha256': hashlib.sha256(labels).hexdigest(),
    }


def _software(device):
    return {'torch': str(torch.__version__), 'cuda': torch.version.cuda,
            'cudnn': torch.backends.cudnn.version(), 'device': str(device),
            'device_name': torch.cuda.get_device_name(device) if device.type == 'cuda' else 'cpu'}


def _gpu():
    if not torch.cuda.is_available():
        _fail('参考训练要求 CUDA GPU，不允许切换 CPU')
    return torch.device('cuda:0')


def _network(value, device):
    with torch.random.fork_rng(devices=[]):
        model = MNISTModel()
    model.load_state_dict(value['state_dict'], strict=True)
    return model.to(device)


def train(model_path, output_update_path, config, context):
    """从本轮全局模型完整训练一次官方训练集，原子产生完整参数 update。"""
    context.check_stop()
    seed, _ = _config(config)
    node_id = _integer(context.node_id, 'node_id')
    round_index = _integer(context.round_index, 'round_index')
    _distinct_output(output_update_path, [
        model_path, Path(context.mnist_data_dir) / 'train-images-idx3-ubyte',
        Path(context.mnist_data_dir) / 'train-labels-idx1-ubyte'])
    device = _gpu()
    value, base_digest = _model(model_path)
    if value['round_index'] != round_index - 1:
        _fail('训练基准模型轮序不匹配')
    dataset, _ = load_mnist(context.mnist_data_dir, 'train', context)
    model = _network(value, device)
    model.train()
    optimizer = torch.optim.SGD(model.parameters(), lr=0.01, momentum=0, weight_decay=0)
    generator = torch.Generator().manual_seed(seed)
    loader = DataLoader(dataset, batch_size=BATCH_SIZE, shuffle=True,
                        drop_last=False, num_workers=0, generator=generator)
    samples, steps, loss_sum = 0, 0, 0.0
    for images, labels in loader:
        context.check_stop()
        images = images.to(device=device, dtype=torch.float32).div_(255)
        labels = labels.to(device=device, dtype=torch.long)
        optimizer.zero_grad(set_to_none=True)
        loss = F.cross_entropy(model(images), labels)
        if not torch.isfinite(loss).item():
            _fail('训练损失非有限值')
        loss.backward()
        optimizer.step()
        count = labels.numel()
        loss_sum += loss.item() * count
        samples += count
        steps += 1
    state = {key: tensor.detach().cpu() for key, tensor in model.state_dict().items()}
    _validate_state(state)
    update = {
        'model_format': MODEL_FORMAT, 'state_dict': state,
        'base_model_sha256': base_digest, 'node_id': node_id,
        'round_index': round_index, 'sample_count': samples,
        'optimizer_steps': steps, 'train_loss': loss_sum / samples,
        'device': str(device), 'training_seed': seed, 'software': _software(device),
    }
    _save(output_update_path, update, context)


def _distinct_output(output, inputs):
    output = Path(output)
    for source in inputs:
        if output.resolve() == Path(source).resolve() or (
                output.exists() and os.path.samefile(output, source)):
            _fail('输出不得覆盖输入模型或 update')


def aggregate(current_model_path, updates_by_node, output_model_path, config, context):
    """校验完整固定集合并按实际样本数聚合，不复制旧模型或跳过坏节点。"""
    context.check_stop()
    seed, _ = _config(config)
    expected = tuple(context.participant_node_ids)
    if (not expected or len(set(expected)) != len(expected)
            or any(type(node) is not int or node <= 0 for node in expected)
            or any(type(node) is not int for node in updates_by_node)
            or set(updates_by_node) != set(expected)):
        _fail('聚合参与节点集合不匹配')
    sample_counts = context.sample_counts_by_node
    if (type(sample_counts) is not dict or set(sample_counts) != set(expected)
            or any(type(node) is not int for node in sample_counts)
            or any(type(count) is not int or not 1 <= count <= 60000
                   for count in sample_counts.values())):
        _fail('缺少准备阶段确认的逐节点样本数')
    round_index = _integer(context.round_index, 'round_index')
    _distinct_output(output_model_path, [current_model_path, *updates_by_node.values()])
    current, base_digest = _model(current_model_path)
    if current['round_index'] != round_index - 1:
        _fail('聚合基准模型轮序不匹配')
    updates = []
    for node in sorted(expected):
        context.check_stop()
        path = updates_by_node[node]
        update, update_digest = _load(path, UPDATE_FIELDS)
        if (_integer(update['node_id'], 'node_id') != node
                or _integer(update['round_index'], 'round_index') != round_index
                or update['base_model_sha256'] != base_digest):
            _fail('update 节点、轮序或基准模型摘要不匹配')
        samples = _integer(update['sample_count'], 'sample_count')
        steps = _integer(update['optimizer_steps'], 'optimizer_steps')
        if samples != sample_counts[node] or steps != math.ceil(samples / BATCH_SIZE):
            _fail('update 样本数或优化器步数不匹配')
        loss = update['train_loss']
        if type(loss) not in (int, float) or not math.isfinite(loss) or loss < 0:
            _fail('update 训练损失无效')
        if update['device'] != 'cuda:0':
            _fail('update 必须来自 CUDA GPU 训练')
        if _integer(update['training_seed'], 'training_seed', 0) != seed:
            _fail('update 训练种子与冻结配置不匹配')
        if type(update['software']) is not dict or not update['software']:
            _fail('update 缺少软件版本')
        updates.append((node, update_digest, update))
    total = sum(update['sample_count'] for _, _, update in updates)
    state = {}
    # FP64 累加，最终统一转回 FP32，避免客户端遍历顺序放大舍入误差。
    for key, tensor in current['state_dict'].items():
        context.check_stop()
        summed = torch.zeros_like(tensor, dtype=torch.float64)
        for _, _, update in updates:
            summed.add_(update['state_dict'][key].double(), alpha=update['sample_count'] / total)
        state[key] = summed.float()
    _validate_state(state)
    _save(output_model_path, {
        'model_format': MODEL_FORMAT, 'state_dict': state,
        'initialization_seed': current['initialization_seed'], 'round_index': round_index,
        'provenance': {'base_model_sha256': base_digest, 'updates': [
            {'node_id': node, 'sha256': digest,
             'sample_count': update['sample_count'], 'weight': update['sample_count'] / total}
            for node, digest, update in updates]},
    }, context)


def evaluate(model_path, config, context):
    """在官方测试集计算样本平均交叉熵、准确率，并在末轮判定改善。"""
    context.check_stop()
    _, evaluation_device = _config(config)
    device = _gpu() if evaluation_device == 'cuda:0' else torch.device('cpu')
    value, _ = _model(model_path)
    if value['round_index'] != context.round_index:
        _fail('评估模型轮序不匹配')
    dataset, preparation = load_mnist(context.mnist_data_dir, 'test', context)
    model = _network(value, device)
    model.eval()
    samples, correct, loss_sum = 0, 0, 0.0
    with torch.no_grad():
        for images, labels in DataLoader(dataset, batch_size=BATCH_SIZE, shuffle=False):
            context.check_stop()
            images = images.to(device=device, dtype=torch.float32).div_(255)
            labels = labels.to(device=device, dtype=torch.long)
            logits = model(images)
            loss_sum += F.cross_entropy(logits, labels, reduction='sum').item()
            correct += (logits.argmax(dim=1) == labels).sum().item()
            samples += labels.numel()
    metrics = {'loss': loss_sum / samples, 'accuracy': correct / samples}
    if not all(math.isfinite(metric) for metric in metrics.values()):
        _fail('评估指标非有限值')
    verdict, reason = None, None
    if context.round_index > 0 and context.round_index == context.rounds and context.rounds != REFERENCE_ROUNDS:
        reason = '本次轮数不是五轮，仅记录试跑指标，不判参考效果达标'
    if context.round_index == REFERENCE_ROUNDS and context.rounds == REFERENCE_ROUNDS:
        baseline = context.initial_metrics
        if (type(baseline) is not dict or set(baseline) != {'loss', 'accuracy'}
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in baseline.values())
                or baseline['loss'] < 0 or not 0 <= baseline['accuracy'] <= 1):
            _fail('末轮评估缺少有效初始基线')
        verdict = metrics['accuracy'] > baseline['accuracy'] and metrics['loss'] < baseline['loss']
        reason = '准确率提高且平均损失降低' if verdict else '未同时满足准确率提高和平均损失降低'
    preparation.update({'model_format': MODEL_FORMAT, 'software': _software(device)})
    return {'metrics': metrics, 'preparation': preparation if context.round_index == 0 else None,
            'verdict': verdict, 'reason': reason}


def main():
    parser = argparse.ArgumentParser(description='生成 MNIST 参考任务的未训练模型')
    parser.add_argument('output_model_path')
    parser.add_argument('--seed', type=int, default=0)
    args = parser.parse_args()
    initialize_model(args.output_model_path, args.seed)
    print(file_sha256(args.output_model_path))


if __name__ == '__main__':
    main()
