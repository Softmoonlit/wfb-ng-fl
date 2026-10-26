# MNIST 真实训练与本机数据配置

本文说明 `wfb_ng.fl.mnist` 参考算法的部署条件、配置和验证边界。固定模型的生成、上传及初始化来源见 [MNIST 参考初始模型](MNIST参考初始模型.md)。本页不代替逐端环境核验、真实无线试跑或最终验收记录。

## 四板数据预置

当前集群为 fl00 服务端和 fl01、fl02、fl04 三个客户端，客户端节点 ID 分别为 1、2、4。部署前，在四块板各自的持久本地目录预置以下四个官方 MNIST 原始、未压缩 IDX 文件，保留官方来源、取得方式和摘要证据。目录中必须直接包含这些文件，不能只放压缩包或再套一层目录。

| 文件名 | 样本数 | 精确文件长度（字节） | 计算用途 |
| --- | ---: | ---: | --- |
| `train-images-idx3-ubyte` | 60,000 | 47,040,016 | 客户端训练图像 |
| `train-labels-idx1-ubyte` | 60,000 | 60,008 | 客户端训练标签 |
| `t10k-images-idx3-ubyte` | 10,000 | 7,840,016 | 服务端测试图像 |
| `t10k-labels-idx1-ubyte` | 10,000 | 10,008 | 服务端测试标签 |

例如，每端均可使用 `/srv/wfb-ng-fl/mnist`，也允许各端使用不同的绝对目录。目录与文件须对本端服务进程可读，作业期间不得修改、替换或删除。数据应在启动作业之前准备完毕；算法直接读取原目录，不在运行期下载、解压或复制入模型库、作业目录。

fl00 实际读取 `t10k` 两文件进行初始及逐轮评估；三个客户端各自读取 `train` 两文件，每端每轮都训练完整 60,000 样本一次。测试集不参加训练或聚合。预置四个文件是部署要求，准备检查只读取本角色使用的两份文件；单端准备成功不证明另外两份文件已经检查。

可在每端对本地文件执行只读摘要检查，并保存输出，例如：

```bash
sha256sum /srv/wfb-ng-fl/mnist/train-images-idx3-ubyte \
  /srv/wfb-ng-fl/mnist/train-labels-idx1-ubyte \
  /srv/wfb-ng-fl/mnist/t10k-images-idx3-ubyte \
  /srv/wfb-ng-fl/mnist/t10k-labels-idx1-ubyte
```

算法检查 IDX magic、维度、28×28 图像、精确长度、图像／标签计数，以及 0～9 标签；原始图像为 0～255 的 uint8。读取时检查文件是否变化，并把实际内存内容的 SHA-256 与源文件复核。准备报告保留本角色两份文件的完整小写摘要，各客户端对应训练文件的摘要必须一致。实现不内置官方摘要白名单；结构合法、摘要一致仍须结合部署来源证据，不能据此单独认定是官方数据。

## 本地目录与算法装配

`mnist_data_dir` 属于每端的本地部署配置。服务端在自身配置中设置该字段，客户端通过自身 Daemon 的 `--mnist-data-dir` 设置；宿主再放入本端 `AlgorithmContext.local_config`。服务端路径不下发给客户端，客户端路径不由作业消息指定，`algorithm_config` 中不允许夹带目录或其他本地配置。

在服务端现有部署配置中显式设置以下片段，并保留该环境原有的管理地址、无线和期限配置：

```json
{
  "mnist_data_dir": "/srv/wfb-ng-fl/mnist",
  "client_entry": "wfb_ng.fl.mnist:client_entry",
  "aggregate_entry": "wfb_ng.fl.mnist:aggregate_entry",
  "evaluate_entry": "wfb_ng.fl.mnist:evaluate_entry",
  "algorithm_config": {
    "data_seed": 2026
  }
}
```

这不是独立可启动的完整服务器配置。各客户端须在本端 Daemon 启动配置中提供本地参数，例如 `--mnist-data-dir /srv/wfb-ng-fl/mnist`；实际目录以该板的部署记录为准。本页不修改部署脚本或 systemd 配置。

`algorithm_config` 恰有一个字段 `data_seed`，必须是 `0 < data_seed < 2**63` 的整数，不接受布尔值、缺失字段或额外字段。该种子初始化客户端 CPU 数据排列 Generator，连续各轮使用其随机序列。它与初始模型的初始化种子分别记录，不承诺跨 GPU 或跨软件版本的位级一致。

三个入口的代码及匹配 PyTorch 依赖须预先安装到相应板端，执行作业的解释器必须能导入它们。不通过作业控制通路分发或安装代码。宿主显式调用：

```python
client_entry(runtime, config, context) -> None
aggregate_entry(current_model_path, updates_by_node, output_model_path, config, context) -> None
evaluate_entry(model_path, config, context) -> evaluation
```

Coordinator 拥有服务端发布、完整收集和轮次推进，客户端入口拥有本地循环。Runtime 仍只交换模型与 update 文件，算法不传入 Runtime 轮号参数。宿主冻结本次入口、配置、初始制品、目标和轮数；操作者通过既有作业入口选择模型摘要、目标及轮数。

## 固定计算与 GPU 要求

模型共有 9,098 个 FP32 参数，两层卷积通道为 1→8→16，配合 ReLU、最大池化及十类线性分类层。训练和评估统一把图像转换为 FP32 后除以 255，使用普通交叉熵损失。每个客户端每轮从收到的全局模型重新加载参数，执行完整一个 epoch，batch size 为 64，保留尾部 32 个训练样本，实际共 938 次优化器更新。优化器固定为 SGD，学习率 0.01，无 momentum、无 weight decay；这些固定计算不开放为配置参数。

客户端必须具有可用 CUDA GPU。准备阶段实际执行固定网络的 GPU 前向、反向和 SGD 操作，必要依赖、GPU 或内核操作失败会明确失败，不降级为 CPU 训练。基础角色 ready 不代表训练准备通过。各端实际 JetPack、CUDA、Python、PyTorch、设备信息和安装身份须逐端核对，不能用 fl00 的结果推断其他板。

当前服务端评估明确使用 CPU，准备报告如实记录 `device.type = "cpu"`；聚合也在 CPU 上完成。CPU 服务端评估与客户端必须使用 GPU 的要求分别成立。服务器先校验初始模型及 10,000 个测试样本并完成初始评估，全体客户端准备通过后才能首次发布。

## 报告与计算制品

准备报告严格包含以下字段，作业、节点和稳定报告身份由宿主控制消息绑定：

```python
{
    "model_format": "mnist-two-conv-fp32-v1",
    "sample_count": 60000,
    "data_sha256": {
        "train-images-idx3-ubyte": "<64 位小写十六进制摘要>",
        "train-labels-idx1-ubyte": "<64 位小写十六进制摘要>",
    },
    "device": {
        "type": "cuda",
        "name": "<实际设备名>",
        "torch_version": "<实际 PyTorch 版本>",
        "cuda_version": "<实际 CUDA 版本>",
    },
    "data_seed": 2026,
}
```

服务端初始评估的准备报告使用 `sample_count = 10000`、两份 `t10k` 文件名及实际 CPU 描述。`device` 四个值均为长度 1～128 的字符串；没有 CUDA 版本时记录 `unavailable`，GPU 报告不接受该值。宿主可调用公开校验器：

```python
validate_preparation_report(report, expected_samples=60000,
                            require_gpu=True, expected_seed=2026)
validate_preparation_report(server_report, expected_samples=10000,
                            require_gpu=False, expected_seed=2026)
```

校验器拒绝缺失或额外字段、错误类型、样本数、种子、摘要格式或设备描述。宿主还须比较全体客户端的 `model_format`、`sample_count`、`data_sha256`、`data_seed` 一致性，设备名不要求相同。

模型和 update 均为单个 PyTorch 文件，使用 `weights_only=True` 读取，只保存基本类型元数据和完整 CPU 参数，不保存可执行模型对象。固定模型与 update 的算法读取上限均为 1 MiB。两类文件均严格核对参数键、形状、FP32、CPU、稠密布局和有限数值。

模型顶层字段恰为 `model_format`、`state_dict`、`initialization`。其中初始化元数据保留 `source = "xavier-uniform-weights-zero-bias-v1"` 及原始 `seed`，聚合后仍保持票 01 可直接加载的格式。初始及聚合模型的实际文件 SHA-256 由宿主记录模型链；保留初始化来源不表示参数未经训练。

update 顶层恰有九个字段：

| 字段 | 含义与校验 |
| --- | --- |
| `model_format` | 固定为 `mnist-two-conv-fp32-v1` |
| `state_dict` | 本轮本地训练后的完整 CPU／FP32 参数 |
| `base_model_sha256` | 本轮实际读取的全局模型文件摘要 |
| `node_id` | 与外部节点键一致的整数身份 |
| `round_index` | 从 1 开始的本地算法轮序 |
| `sample_count` | 实际完成的训练样本数，正式参考训练为 60,000 |
| `optimizer_steps` | 正整数，须等于 `ceil(sample_count / 64)`，正式训练为 938 |
| `train_loss` | 按实际样本数统计的有限、非负平均训练损失 |
| `device` | 与准备报告相同字段结构的实际 GPU 描述 |

聚合必须收到完整固定节点集合，逐一校验身份、轮序、基础摘要、参数和统计。宿主在服务端上下文的本地配置中提供 `client_preparation_reports`，以整数节点 ID 为键；聚合把每份 update 的样本数与该节点已通过准备的样本数绑定。正式准备了 60,000 样本后，声称只完成 1 或 3 样本的 update 会被拒绝。任一节点无效就失败，不跳过节点或复用旧 update。

FedAvg 对完整参数使用 `sample_count / sum(sample_count)` 加权，不按优化器步数计权。模型和 update 都先在同目录临时文件中写完、同步并校验，再原子发布到新路径，不覆盖输入制品或现有输出。客户端仅在 `submit_update` 成功返回后报告本地轮次完成；这不等于服务器聚合或全局作业成功。

评估返回恰为 `metrics`、`preparation`、`verdict`、`reason`。`metrics` 包含按全部测试样本统计的有限平均 `loss` 和 0～1 的 `accuracy`，保留评估尾批次；只有初始评估包含准备报告。初始与中间轮的 `verdict`、`reason` 为 `None`。末轮与初始指标比较，准确率更高且损失更低才判效果通过，不要求逐轮单调或预设高准确率。正式五轮验收使用 M0 与 M5；少轮结果单独记录。

## 已执行验证及边界

本次计算检查在 fl00 的实际 NVIDIA Orin 上使用 PyTorch `2.5.0a0+872d972e41.nv24.08`、CUDA `12.6` 执行过真实 GPU 单轮和两轮计算。输入为测试生成、结构合法的受控 IDX，每轮完整 60,000 样本、938 次 SGD 更新。检查覆盖 CPU 完整参数输出、实际基础摘要关联、重新加载每轮全局模型、提交后完成报告，以及冻结上下文不被修改。

CPU 评估检查覆盖完整 10,000 样本、非零像素除以 255、尾批次按样本加权、初始准备及末轮效果判定。受控 FedAvg 检查使用样本数 1:3、参数值 2 和 6，预期聚合值为 5；两个 update 分别配有样本数为 1 和 3 的准备 fixture。这只证明样本加权算术与准备绑定，不提供正式训练的小样本配置。

最终启用实际 GPU 检查后，计算、IDX 与票 01 模型检查为 **82 passed**：

```bash
OMP_NUM_THREADS=4 WFB_RUN_MNIST_GPU_TEST=1 python -m pytest -q \
  wfb_ng/tests/test_fl_mnist.py wfb_ng/tests/test_fl_mnist_data.py \
  wfb_ng/tests/test_fl_reference_model.py
```

这些受控文件没有官方数据来源证据，不能证明官方 MNIST 效果，也不证明客户端三板环境、真实无线传输、全员准备屏障或全员恢复通过。官方数据与实机责任仍须按下列工单执行。

对三个计算实现的类型检查已执行通过：

```bash
python -m mypy --follow-imports=silent --check-untyped-defs \
  wfb_ng/fl/mnist.py wfb_ng/fl/mnist_data.py wfb_ng/fl/reference_model.py
```

结果为 `Success: no issues found in 3 source files`。此次补充部署说明未重复 GPU 训练，未修改部署脚本。

## 退役的文件仿真入口

正式包已删除 `wfb_ng.fl.issue41_algorithm`、复制训练／聚合、模板 update 和 Runtime 的仿真专用匹配开关，不保留转发或兼容入口。历史 `tests/fl_runtime` 文件闭环脚本仅用于理解旧证据；不能再以其旧算法路径启动当前角色服务，也不能替代本文的真实训练配置。确定性字节数据和历史归档工具仍与真实训练相互独立。

## 实机交接责任

| 工单 | 必须实际取得的证据 |
| --- | --- |
| [08：Jetson 环境与 40 MHz 少轮试跑](../../.scratch/real-fedavg-training/issues/08-jetson-environment-and-40mhz-trial.md) | 在 05、06 完成后执行四板训练试跑；逐端环境、代码身份、官方数据来源与摘要、实际 GPU 操作、服务器评估设备和适用期限；至少跨两个轮次的 40 MHz 真实无线训练、模型关联、正常停止与待命恢复。环境及数据核验可提前准备，少轮试跑不计正式验收。 |
| [12：故障与控制台实机检查](../../.scratch/real-fedavg-training/issues/12-fault-and-console-hardware-acceptance.md) | 满足该票全部前置条件，包括 08 试跑及 09 速率表后，实际检查非法模型、数据缺失／损坏／不一致、GPU 不可用、错误 update、训练异常、失联、期限、停止、无线事务及页面状态。缺失确认和恢复未完成不得显示成功。 |
| [13：连续三次五轮最终验收](../../.scratch/real-fedavg-training/issues/13-three-run-final-acceptance.md) | 11、12 完成后，以同一四板、初始模型、官方数据和种子记录，连续完成规定的 40 MHz、20 MHz 及轮次间切换三次五轮任务；保存 M0～M5、十五轮共 45 份 update，独立 FP64 复算全部实际聚合并按固定容差比较，分别确认效果、执行、恢复和持久备份。 |

现有四板具备管理访问与通信底座，各票仍须按实际依赖和执行结果推进。受控计算、文件交换或少轮记录均不能提前填作最终验收通过。
