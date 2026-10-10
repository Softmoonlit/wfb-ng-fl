# MNIST 参考初始模型

本页对应[01：生成并入库参考初始模型](../../.scratch/real-fedavg-training/issues/01-reference-model-artifact.md)。本票交付固定模型、初始化命令及严格读取能力；模型库上传、SHA-256 引用和去重沿用现有入口。训练、评估、聚合和作业主链由 [04 票](../../.scratch/real-fedavg-training/issues/04-real-single-round-training.md)实现，不能用生成或加载成功证明真实训练已接入。

## 固定结构与初始化

实现位于 `wfb_ng.fl.reference_model`，唯一模型类为 `MNISTReferenceModel`，不接受运行时结构配置。输入形状为 `N×1×28×28`，FP32，取值范围为 `[0, 1]`；官方图像字节除以 255，不再减均值或除以标准差。数据读取和这一预处理的接入属于 04 票。

| 顺序 | 固定计算 |
| --- | --- |
| 第一层 | Conv2d，1→8 通道，3×3，stride 1，padding 1；ReLU；2×2 最大池化 |
| 第二层 | Conv2d，8→16 通道，3×3，stride 1，padding 1；ReLU；2×2 最大池化 |
| 分类层 | 展平 16×7×7，Linear 784→10，输出 logits，不在模型内使用 softmax |

模型共有 9,098 个 FP32 参数，无 BatchNorm、Dropout 或额外缓冲量。后续训练采用普通交叉熵损失。参数键及形状固定为：

| 参数键 | 形状 |
| --- | --- |
| `conv1.weight` / `conv1.bias` | `[8, 1, 3, 3]` / `[8]` |
| `conv2.weight` / `conv2.bias` | `[16, 8, 3, 3]` / `[16]` |
| `classifier.weight` / `classifier.bias` | `[10, 784]` / `[10]` |

初始化来源标识为 `xavier-uniform-weights-zero-bias-v1`：使用独立 CPU Generator 和操作者显式指定的种子，按 conv1、conv2、classifier 顺序对权重执行 Xavier uniform，gain 为 1；所有 bias 为零。种子是 `0～2**63-1` 的整数，不接受布尔值。初始化不访问数据、不执行优化，不初始化 CUDA，并保存和恢复调用者的 CPU 随机数状态。同一 PyTorch 环境和种子可复现参数，不承诺不同 PyTorch 版本的序列化字节或跨设备位级一致。

## 依赖与命令

基础通信服务不强制安装 PyTorch；`mnist` 安装 extra 声明参考模型所需的 `torch`。执行工具的解释器必须已具备支持 `weights_only=True` 的 PyTorch。Jetson 对应 JetPack／CUDA 的兼容包和各端依赖组合由 [08 票](../../.scratch/real-fedavg-training/issues/08-jetson-environment-and-40mhz-trial.md)核实，不用普通 CPU wheel 替代客户端 GPU 环境。

在仓库根目录执行：

```bash
python3 -m wfb_ng.fl.reference_model /path/to/mnist-initial-seed7.pt --seed 7
```

安装当前版本后也可使用：

```bash
wfb-fl-init-model /path/to/mnist-initial-seed7.pt --seed 7
```

工具以同目录临时文件完成序列化、fsync 和严格读取校验，再原子发布完整文件。目录同步失败会撤回本次新建的输出；如文件系统连清理也无法完成，返回专门的清理错误，不能报生成成功。输出必须是新路径；已有文件、符号链接或模型库制品不会被覆盖。成功后 stdout 输出 JSON，包含完整小写 SHA-256、模型格式及初始化来源和种子；失败返回非零退出码及明确原因。要重复使用同一初始制品，直接保存和上传同一个文件，无需重新初始化。

## 文件与严格读取

文件为单个 PyTorch 制品，只保存普通字典、基本类型元数据和 CPU 参数张量，顶层字段恰为：

```python
{
    "model_format": "mnist-two-conv-fp32-v1",
    "state_dict": {"conv1.weight": ..., "conv1.bias": ..., ...},
    "initialization": {
        "source": "xavier-uniform-weights-zero-bias-v1",
        "seed": 7,
    },
}
```

`state_dict` 是普通字典，不保存可执行模型对象。初始化元数据记录原始来源；后续训练／聚合产物可保留该来源，读取器不把“带有初始化来源”误当成“参数尚未训练”的证明。

```python
from wfb_ng.fl.reference_model import load_model

artifact = load_model("/path/to/model.pt")
model = artifact.model                 # 独立的 CPU／FP32 模型
seed = artifact.initialization_seed
sha256 = artifact.sha256               # 本次实际读取字节的完整摘要
```

读取器复用现有普通文件检查，在反序列化前限制为 1 MiB，并限制实际读取长度。加载与 SHA-256 基于同一份字节快照，使用 `weights_only=True`，不恢复任意类，不接受非 CPU 的序列化 storage。随后严格检查模型格式、顶层及元数据字段、参数键、形状、FP32、CPU、稠密布局及有限数值，检查通过才向模型复制参数。非法内容产生 `FLRuntimeError`，格式或参数错误码为 `invalid_reference_model`，文件检查继续使用既有错误码（例如 `artifact_too_large`）。不加载旧不透明模板，不转换错误 dtype，不使用兼容分支。

读取不会写入源文件，返回模型的内存修改也不会改写制品。1 MiB 是固定参考模型的算法读取上限；模型库仍对不透明文件执行原有 1 GiB 单文件上限，不解释模型格式或算法参数。

## 既有模型库操作

1. 打开服务器 `/models`，上传初始化工具输出的 `.pt` 文件；也可对既有 `POST /api/v1/models` 提交名为 `file` 的 multipart 文件。
2. 保留返回的完整 `model.sha256`，与工具输出核对。列表由 `GET /api/v1/models` 提供；再次上传同一字节内容返回去重结果和同一摘要。
3. 在现有作业页面的初始模型下拉框选择该制品，作业请求仍用 `model_sha256` 引用。可在不同作业重复选择同一摘要；库内文件由模型库管理，算法只读。

本票不改变作业启动语义，选择成功不代表后续真实训练入口已经实现。格式不合法的文件仍可作为不透明制品上传；算法使用前必须调用严格读取器，不能把“上传成功”当作格式检查。

## 软件检查与实机交接

本次生成与读取检查实际使用 fl00 的 Python 3.10.12、NVIDIA PyTorch `2.5.0a0+872d972e41.nv24.08`，全部模型计算均在 CPU，未检查或推断其他节点依赖。已核对该安装的 `torch.load`／`torch.save`、`nn.Conv2d`、初始化 Generator、Storage 类型与随机数状态能力，复用现有文件检查、fsync 和模型库入口，无新增运行时框架。

本票检查限定为参考模型模块、现有模型库及 HTTP 入口：初始化与 CLI、同种子参数复现、合法读取、严格拒绝错误内容、源制品不变、HTTP 上传、完整摘要和重复上传去重。新增模型文件测试在没有可选 PyTorch 的环境中明确跳过，不让基础服务测试隐式安装训练依赖；维护参考功能时应先准备 PyTorch 后执行。Python 类型检查使用临时安装的 mypy，对新增模块检查，不把类型工具加入产品依赖。

本次还在本机持久缓存生成一个可上传示例：`/home/fl/.cache/wfb-ng-fl/reference-model-artifact-f5udj0k6/mnist-initial-seed7.pt`，种子 7，实际大小 38,792 字节，SHA-256 为 `b1a1c1734bec69acd26df1ed6c6aaafecd0b7a6b5b3c2f9b594043d7fca453d1`。这是本票 CPU 生成示例，不是硬件验收备份，也未自动上传到正在运行的生产服务。

本票未执行 GPU 加载／前向／反向／优化、官方 IDX 准备、真实无线作业或全员资源恢复。相关代码由 04／05／06 票接入，逐端环境与 40 MHz 少轮实测归 08 票，最终完整验收归 11～13 票。现已有开发板，不能再将“无板”作为阻塞；这些实机事项仍须各票实际执行取证。
