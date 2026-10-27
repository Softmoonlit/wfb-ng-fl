# 08 票 Jetson 环境与官方 MNIST 准备记录

本次已完成授权范围内的三客户端 NVIDIA PyTorch 安装、四端官方 MNIST 预置，以及四端固定模型 GPU 前向、反向和优化器更新检查。未停止或重启角色服务、未修改无线、未部署当前未提交代码、未启动作业、未提交，也未修改 08 工单状态。08 的真实无线少轮训练、模型链、停止及恢复仍由主线后续正式验收。

初始只读预检报告完整保留在 [readonly-preflight-report.md](readonly-preflight-report.md)。其中“缺 torch、未发现数据”等结论对应准备前的历史时刻；以下为 2026-10-26 18:21～18:37 UTC 后续环境准备结果。所有失败与重测均保留。

## 当前结果

| 节点 | PyTorch / CUDA / cuDNN | NumPy | 固定模型 GPU 检查 | 数据 |
| --- | --- | --- | --- | --- |
| fl00 | 2.5.0a0+872d972e41.nv24.08 / 12.6 / 90300，复用原安装 | 1.23.5，未改 | 通过 | 四个官方 IDX 通过 |
| fl01 | 同上，本次离线安装 | 1.23.5，本次补齐 | 通过 | 同上 |
| fl02 | 同上，本次离线安装 | 1.23.5，本次补齐 | 通过 | 同上 |
| fl04 | 同上，本次离线安装 | 1.23.5，本次补齐 | 通过 | 同上 |

四端 torch 实际路径均为 `/usr/local/lib/python3.10/dist-packages/torch/__init__.py`，解释器为 `/usr/bin/python3` 3.10.12。所有检查从 `/tmp` 运行，禁用字节码写入，避免导入工作区代码。模型类直接使用四端原已安装的 `wfb_ng.fl.reference_model.MNISTReferenceModel`，源文件 SHA-256 均为 `86963f19190aa27d249e2b91fa07eddfbb4090fa8d4dafd9c58639311961be37`。本次没有部署 `mnist.py`、`mnist_data.py` 或任何未提交 05/06 代码。

## 兼容依据及依赖选择

先读取根目录 AGENTS、08 工单和部署文档，再检索 fl00 现有 wheel、pip/apt 缓存、各客户端现有依赖及缓存。fl00 原有 wheel 为 `/home/fl/torch-2.5.0a0+872d972e41.nv24.08-cp310-cp310-linux_aarch64.whl`，SHA-256 为 `6f75fd2d2ef840ede1a90dbcf40a5458214bee26cc803fa510cda2e8978d972a`，与其已安装 torch 的 `direct_url.json` 记录完全一致；wheel 内 `torch/version.py` 明确标注 CUDA 12.6，Python ABI 为 cp310、架构 aarch64。原有文件保留原位，不复制进仓库。

四端逐端确认 L4T `36.4.3-20250107174145`、CUDA 工具包 `12.6.11-1`、cuDNN `9.3.0.75-1`、内核 `5.15.148-tegra`。NVIDIA JetPack 6.2 官方发布说明明确列出 L4T 36.4.3、CUDA 12.6 和 cuDNN 9.3，与实际平台组件匹配。四端未安装 `nvidia-jetpack` 元包，故不能将文档映射写成元包安装实测版本。

已归档的 NVIDIA 官方说明：

- 安装文档：<https://docs.nvidia.com/deeplearning/frameworks/install-pytorch-jetson-platform/index.html>，支持使用本地 NVIDIA wheel，并要求 nv24.06 及之后版本先具备 cuSPARSELt。
- PyTorch 发布表：<https://docs.nvidia.com/deeplearning/frameworks/install-pytorch-jetson-platform-release-notes/pytorch-jetson-rel.html>。
- JetPack 6.2 发布说明：<https://docs.nvidia.com/jetson/jetpack/6.2/release-notes/index.html>。
- NVIDIA v60 wheel 索引：<https://developer.download.nvidia.com/compute/redist/jp/v60/pytorch/>。

需明确保留的来源限制：当前 NVIDIA 发布表将提交 `872d972e41` 的 nv24.08 条目标为 JetPack 6.0，而本机缓存 wheel 内实际标注 CUDA 12.6；当前 v60 索引没有该同名 wheel。未从该索引重新获得独立官方 wheel 摘要，不能声称公开发布表已明确认证本精确 wheel 与 JetPack 6.2 的组合。选择复用的依据是本机原安装记录摘要完全一致、相同平台组件、实际 CUDA 12.6 构建信息及四端固定模型运算实测通过；该限制不隐藏，也未以普通 CPU wheel 替代。

客户端已有 OpenBLAS/cuDNN，予以复用。仅补充 torch 声明缺失或版本不足的依赖：`filelock 3.20.0`、`typing_extensions 4.15.0`、`networkx 3.4.2`、`fsspec 2025.12.0`、`sympy 1.13.1`、`mpmath 1.3.0`。已有 Jinja2 3.0.3 / MarkupSafe 2.0.1 满足要求，未升级；不安装 torchvision，也不升级 pip。NumPy 初次保留系统 1.21.5 后发现实际 C API 不兼容，因此补充与 fl00 相同的 1.23.5；系统 apt 文件未卸载，由 `/usr/local` 的新安装供系统解释器使用。

cuSPARSELt 复用 fl00 版本 `libcusparselt0 0.7.1.0-1`。客户端未配置相应 apt 候选，故仅下载并安装该单个 NVIDIA deb，不变更仓库配置。URL 为 <https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/arm64/libcusparselt0_0.7.1.0-1_arm64.deb>，SHA-256 `43b6c9fcccf6e0d2f86b0981d591d49f4c724c10b6b63dceee6bda8299bc8965` 与 fl00 apt 仓库元数据一致。torch 通过 `pip install --no-cache-dir --no-index --find-links` 离线安装，没有运行通用 `pip install torch`。各制品名称、长度、摘要见 `external-cache-manifest.json`、`install-artifact-manifest.json`；依赖下载输出、每端安装命令和结果均保留。

## 固定模型真实 GPU 检查

`gpu-fixed-model-check.py` 直接导入已安装固定网络，不复制或改写算法实现。输入为已校验官方训练集的前 64 个样本，FP32 除以 255，标签转 int64；模型为两卷积加分类器，9098 参数，Xavier 权重、零偏置，初始化种子 2026。实际在 `cuda:0` 上执行一次 batch size 64 的交叉熵前向、反向和 SGD，学习率 0.01、无 momentum、无 weight decay。

检查输出、梯度、参数均在 GPU 且数值有限；逐参数保存运算前副本并核对 `after ≈ before - 0.01 × gradient`，六组参数全部发生更新。全程只在内存执行，不写模型或 update，不启动任何作业；不将单批次检查计为正式训练轮次或完整 epoch。

| 节点 | 损失 | 单批次同步运算耗时（秒） | 改变的参数张量数 | 最大 SGD 数值误差 |
| --- | ---: | ---: | ---: | ---: |
| fl00 | 2.292933702468872 | 0.3987317119972431 | 6 | 7.450580596923828e-09 |
| fl01 | 2.292933702468872 | 3.1473656759990263 | 6 | 7.450580596923828e-09 |
| fl02 | 2.292933702468872 | 11.784192445000372 | 6 | 7.450580596923828e-09 |
| fl04 | 2.292933702468872 | 9.322973338999873 | 6 | 7.450580596923828e-09 |

上述为单次环境运算耗时，可能包含首次内核准备，不作为训练性能基准。cuDNN 实际查询均为 90300、可用且启用；没有单独声称通过 profiler 证明每个算子走某一 cuDNN 算法。GPU 均为 Orin、计算能力 8.7、8 个 SM；显存字节数逐端保存在原始日志，不跨端套用。

## 官方 MNIST 来源及跨板一致性

四个 gz 从 torchvision MNIST 段列明的 HTTPS 维护镜像 `https://ossci-datasets.s3.amazonaws.com/mnist/` 取得，时间为 2026-10-26 18:24:56～18:25:03 UTC。来源元数据 `torchvision-mnist-source.txt` 已在初次预检归档；每个 HTTP 响应头保存在 `*.headers.txt`，来源时间、gz MD5、gz SHA-256、raw SHA-256、样本数和长度保存在 `mnist-source-manifest.json`。四个 gz MD5 全部匹配 torchvision 维护值；没有使用受控 IDX、示例图片或自制样本替代。

| 原始文件 | 样本数 | 字节数 | 原始文件 SHA-256，四板一致 |
| --- | ---: | ---: | --- |
| train-images-idx3-ubyte | 60000 | 47040016 | ba891046e6505d7aadcbbe25680a0738ad16aec93bde7f9b65e87a2fc25776db |
| train-labels-idx1-ubyte | 60000 | 60008 | 65a50cbbf4e906d70832878ad85ccda5333a97f0f4c3dd2ef09a8a9eef7101c5 |
| t10k-images-idx3-ubyte | 10000 | 7840016 | 0fa7898d509279e482958e8ce81c8e77db3f2f8254e26661ceb7762c4d494ce7 |
| t10k-labels-idx1-ubyte | 10000 | 10008 | ff7bcfd416de33731a308c3f266cc351222c34898ecbeaf847f06e48f7ec33f2 |

先校验全部 gz 的 MD5、SHA-256、gzip 完整性、IDX magic、28×28、精确长度、图像/标签计数与 0～9 标签，再以独占创建模式 `xb` 写入四端 `/srv/wfb-ng-fl/mnist`。创建前检查四个目标均不存在，拒绝覆盖已有文件。四文件为 root:root、0644，目录可供非 root 读取。每端另存 `SOURCE-MANIFEST.json`，四端该文件 SHA-256 均为 `1f3fe3cb6a3cc8aff941dec86286d1710eb832f034ef3dbca339ee68f5404b7a`。最终从普通用户读取实际目标文件重新计算并交叉比较全部摘要，见 `cross-node-verification.json`。

这些 SHA-256 是从有来源记录、MD5 匹配的实际对象计算的部署冻结值，不伪称作者发布的 SHA-256 白名单。数据下载在作业之外完成，运行时不会下载。当前只预置目录，尚未修改服务的 MNIST 本地路径或入口配置；主线正式部署时各端分别配置该目录，不下发服务器路径。

## 缓存位置与磁盘

按用户最新要求，仓库证据目录仅保留小型日志、脚本、网页元数据和 manifest；没有 wheel、deb、gz 或 IDX。下载缓存保留供复用，使用 `mv` 移出仓库，未删除：

- fl00 依赖 wheel/deb：`/srv/wfb-ng-fl/install-cache`；原有 770 MB torch wheel 保持 `/home/fl/` 原位。
- 三客户端 torch wheel、依赖 wheel/deb：各端 `/srv/wfb-ng-fl/install-cache`。
- 四端 MNIST gz：各端 `/srv/wfb-ng-fl/mnist-downloads`；实际 IDX：各端 `/srv/wfb-ng-fl/mnist`。

早期日志中的仓库 `install-cache` / `mnist-downloads` 和远端 `/home/fl/wfb-ng-08-*-cache-20261026` 是迁移前的历史路径；当前缓存清单和脚本已指向仓库外位置。`prepare-clients.py` 后续分发临时目录为 `/tmp/wfb08-install-cache`，`install-client.py` 接受显式缓存目录参数，默认读取 `/srv/wfb-ng-fl/install-cache`；不应在已经准备好的节点无意义重装。`download-mnist.py` 对已有下载文件拒绝覆盖，`provision-mnist.py` 对已有 IDX 拒绝覆盖。

安装前客户端空闲约 6.62～6.99 GB；移动缓存后的实际可用字节数为 fl01 `4154753024`、fl02 `4393852928`、fl04 `4024586240`，仍保留可复用安装包。wheel 解压内容约 1.49 GB，逐端安装前执行空闲空间检查；未删除任何已有缓存来腾空间。

## 失败、重测与未决事项

1. 原只读预检全部失败完整保留，见历史报告和原始日志，包括缺 torch、fl00 整体属性字符串化 UTF-8 错误、元包/发行版查询失败、驱动展示名与模块名不一致、权限拒绝和作者站网络失败。
2. 新检查脚本曾在远端使用 `rg` 过滤 ldconfig 输出，远端无 rg，返回 127；随后使用现有 grep 和实际 torch 运行补证，未为此安装 rg。
3. apt 下载 NVIDIA cuSPARSELt 反复重试，结束的仅是本次下载进程，改用同仓库 HTTPS URL 后成功，并对照 apt 元数据校验 SHA-256；没有停止服务。
4. 初次 torch 导入提示 NumPy 1.21.5 C API 不兼容。fl01 固定模型检查明确失败 `RuntimeError: Numpy is not available`，原始失败保存为 `fl01-gpu-fixed-model-before-numpy-fix.txt`；三客户端安装 NumPy 1.23.5 后各自固定模型运算成功。未绕过 NumPy 使用要求，也未回退 CPU。
5. 用户要求缓存移出仓库时，结束外层串行分发循环，已启动的 fl02 SSH 安装子进程继续完成；其日志含 `CLIENT_INSTALL_PASS`，之后 NumPy 修复与 GPU 检查成功，fl04 使用新路径继续分发。缓存和下载数据全部保留。
6. `pip check` 四端返回 1：四端均报告 `pynacl 1.5.0 requires cffi`；fl00 另报告既有 OpenCV 4.12 对 NumPy>=2 的要求及 PyNaCl 平台告警。fl00 未修改其 Python 依赖；本次只补齐 torch 必需依赖，没有为消除全局告警升级 NumPy 2 或扩展安装无关包。因此本记录只确认参考模型实际 GPU 路径通过，不能称系统所有 Python 包无冲突。后续若相关组件实际需要这些包，应另行针对组件修复验证。
7. wheel 原始下载来源仅能由 fl00 已安装记录和本地同摘要缓存追溯，未取得独立官方发布摘要；NVIDIA 发布表的版本映射差异如上保留，不能包装成官方逐组合认证。

## 服务与部署身份边界

最终四端角色主进程 PID 与初次预检一致：fl00 54188，fl01 17507，fl02 8576，fl04 17711，角色服务均 active。`iw dev` 仍为信道 157、40 MHz、空口驱动报告功率 -14.00 dBm，USB 网卡仍为 rtl88xxau_wfb、5000M。

最终安装 build identity 仍是 `99865efe224a9ad35fefd13ae1340721f374b521`；build identity、reference_model、client_daemon、server_daemon 文件摘要均与准备前一致，`mnist.py`/`mnist_data.py` 仍未部署。本次 GPU 检查是在新 Python 子进程执行，不宣称长期运行的 daemon 已加载新依赖或已完成训练准备屏障。服务器正式评估按文档使用 CPU，本次未发评估作业。

## 证据索引与校验

- `flXX-prepare-before.txt`、NVIDIA 官方页面归档、`cached-wheel-metadata.txt`：安装前检查与选择依据。
- `flXX-installation.txt`、`flXX-numpy-fix.txt`：逐端实际安装命令、错误和结果。
- `flXX-gpu-fixed-model.txt`、`gpu-fixed-model-results.json`：实际前向/反向/SGD检查、参数更新数值。
- `mnist-source-manifest.json`、`*.headers.txt`、`mnist-download.log`、`flXX-mnist-provision.txt`：官方来源、压缩包与 IDX 校验、禁止覆盖预置。
- `flXX-final-environment.txt`、`cross-node-verification.json`：最终逐端数据摘要、服务 PID、无线及安装代码身份。
- `flXX-cache-relocation.txt`、`external-cache-manifest.json`：缓存位置、大小和摘要，所有实际包在仓库外。
- 初次只读原始输出、脚本和报告均保留，便于对照前后状态。

`SHA256SUMS` 覆盖本目录全部小型证据文件，可在本目录运行 `sha256sum -c SHA256SUMS`。缓存和实际数据另按 manifest 校验，不存入 git。本记录不等于 08 完成或正式无线训练验收通过。
