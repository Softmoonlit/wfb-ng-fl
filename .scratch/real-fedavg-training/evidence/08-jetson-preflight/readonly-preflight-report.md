# 08 票四板只读环境预检证据

本目录记录 2026-10-26 18:15～18:19 UTC 的提前准备结果。已先读取 08 票、真实训练规格以及《部署与运行基准》《ARMv8集群部署与演示操作手册》《MNIST真实训练与本机数据配置》《MNIST参考初始模型》。权威拓扑是仓库根目录 `cluster_nodes.conf`。本次只写本证据目录，不提交、不修改 08 票状态；未执行部署、依赖安装、数据下载或预置、训练、停服、重启服务、无线配置修改。GPU 检查仅分配小张量并执行加法，没有前向、反向或优化。05、06 完成后的主线正式部署和验收仍需另行执行。

## 结论与逐端实测

管理访问四端均成功，`sudo -n true` 四端均为退出码 0。远端顺序为 fl01、fl02、fl04，使用用户 `fl`，地址分别为 192.168.108.201、192.168.108.202、192.168.108.204；fl00 本机执行。主机名与预期一致。

| 项目 | fl00 | fl01 | fl02 | fl04 |
| --- | --- | --- | --- | --- |
| 运行角色 | 服务端 daemon active | 客户端 daemon active | 客户端 daemon active | 客户端 daemon active |
| 实际角色主进程 PID，前后保持一致 | 54188 | 17507 | 8576 | 17711 |
| 实际身份/TUN | 服务端 10.80.0.1/24 | 节点 1，10.80.0.11/24 | 节点 2，10.80.0.12/24 | 节点 4，10.80.0.14/24 |
| 系统 Python | 3.10.12 | 3.10.12 | 3.10.12 | 3.10.12 |
| 系统解释器 PyTorch | 2.5.0a0+872d972e41.nv24.08 | 未安装，导入失败 | 未安装，导入失败 | 未安装，导入失败 |
| 系统解释器 torchvision | 0.20.0a0+afc54f7 | 未安装 | 未安装 | 未安装 |
| NumPy | 1.23.5 | 1.21.5 | 1.21.5 | 1.21.5 |
| CUDA 工具包软件包 | 12.6.11-1 | 12.6.11-1 | 12.6.11-1 | 12.6.11-1 |
| 系统 cuDNN 软件包 | 9.3.0.75-1 | 9.3.0.75-1 | 9.3.0.75-1 | 9.3.0.75-1 |
| PyTorch CUDA / cuDNN 运行时 | 12.6 / 90300 | 未能取得，缺 torch | 未能取得，缺 torch | 未能取得，缺 torch |
| 实际 GPU 加法 | 重测通过，cuda:0 | 缺 torch，未执行成功 | 缺 torch，未执行成功 | 缺 torch，未执行成功 |
| 限定目录中的 IDX | 未发现 | 未发现 | 未发现 | 未发现 |

四板共同实测值：Ubuntu 22.04.5 LTS、aarch64、内核 5.15.148-tegra、L4T R36.4.3，`nvidia-l4t-core` 为 `36.4.3-20250107174145`。设备树型号为 `NVIDIA Jetson Orin Nano Engineering Reference Developer Kit Super`。`nvidia-smi` 四端均返回 `Orin (nvgpu), [N/A], 540.4.0`；其中显存字段为实际 `[N/A]`，不以此推断显存大小。`nvidia-jetpack` 元包四端均未安装，故没有可直接报告的 JetPack 元包版本；L4T/CUDA 的实际数值如上，不把推定映射当作安装版本。

fl00 GPU 加法重测取得设备名称 `Orin`、总显存 `7990046720` 字节、计算能力 `8.7`、多处理器数 `8`。实际 `cuda:0` 上 `[1,2,3] + [4,5,6]` 得到 `[5.0,7.0,9.0]`，同步和取回结果总耗时 `0.09171161600170308` 秒，退出码 0。这证明该解释器可执行 CUDA 加法，不证明训练卷积、反向、SGD 或 cuDNN 内核通过。cuDNN 仅查询实际版本。

服务端评估设备按部署文档规定应为 CPU；当前安装包没有真实 MNIST 评估模块，也没有启动评估，因此本次没有“服务器实际评估已执行”证据。三客户端 GPU 环境尚未满足训练前提，不能从 fl00 推断客户端通过。

## 身份、安装产物与无线现场

现行服务为 `wfb-fl-server-daemon.service` / `wfb-fl-client-daemon.service`，入口首行为 `#!/usr/bin/env python3`。本次从 `/tmp` 执行 `python3 -B`，避免当前源码目录遮蔽安装包。systemd 单元、有效启动命令和环境均保存在逐端输出中；未发现另行配置的解释器环境。检查结果限于该系统解释器，不声称已枚举所有潜在虚拟环境。

四端实际导入 `wfb_ng` 的路径均为 `/usr/local/lib/python3.10/dist-packages/wfb_ng/__init__.py`。安装的 `fl/build_identity.json` 均声明提交 `99865efe224a9ad35fefd13ae1340721f374b521`，文件 SHA-256 为 `a27b0792cd8d7a885a591b90e0ce09f0bfe57e5476c8af17df587ae1b0ccd6f4`。四端已安装 `reference_model.py`、`client_daemon.py`、`server_daemon.py` 的摘要各自一致，原始输出保留完整摘要。四端安装包均缺 `mnist.py` 和 `mnist_data.py`，真实训练实现尚未部署。查询发行版名 `wfb-ng` 得到 PackageNotFoundError；补查发行版名称也未找到匹配项，但模块实际可导入，不能把元数据缺失等同于模块缺失。

四端 `/usr/bin/wfb_v6_uplink` 为 281992 字节，SHA-256 均为 `8ddb419be0c99e174eeb91239092f266398ea248e0c3c71eff643e28bbcc08e0`。这些是当前磁盘安装文件身份；未宣称运行进程已重新加载当前磁盘每个文件。

fl00 实际服务配置 `/etc/wfb-ng-fl/server.json` 的信道为 157、频宽 40、下行 MCS 3、上行 MCS 4、功率 14、UFTP 速率 15000 Kbps；`known_clients` 保留 1～10 的预置槽位，不等于本次正式目标集合。三客户端实际单元使用 `/etc/wfb-ng-fl/node.json`，节点身份为 1、2、4。各端 `/etc/wfb-ng/fl-*.json` 为另一套角色基础设施示例，仍可看到单客户端示例身份，不能将其代替现行 daemon 配置。fl00 上未运行的客户端身份示例同样不代表服务器身份。

`iw dev` 显示四端空口均为 monitor、信道 157（5785 MHz）、40 MHz、中心 5795 MHz，实际驱动报告发射功率为 `-14.00 dBm`。四端 RTL 网卡在 `lsusb -t` 中均为 `rtl88xxau_wfb`、5000M，控制器为 tegra-xusb，没有把管理无线接口当空口接口。模块安装名为 `88XXau_wfb`，路径 `/lib/modules/5.15.148-tegra/updates/dkms/88XXau_wfb.ko`，版本 `v5.2.20.2_28373.20190919`。驱动展示名与模块名不同，按展示名查询 modinfo 的失败已保留。

未取得板间物理距离、天线摆放、供电测量和现场照片，本记录不补填这些现场条件。

## 数据核验范围及结果

初次只读检索限定 `/var/lib/wfb-ng`、`/home/fl`、`/srv/wfb-ng-fl`；不跟随目录符号链接，排除 `.git`、`.cache`、`node_modules`、`__pycache__`，总检索限时 35 秒。随后补查实际配置涉及的 `/var/lib/wfb-ng-fl`，普通用户遇到权限拒绝后，以免密 sudo 对 `/var/lib/wfb-ng` 和 `/var/lib/wfb-ng-fl` 做只读补查，总限时 25 秒。所有补查退出码 0，均未发现名称含 idx1/idx3 的文件。`/srv/wfb-ng-fl` 四端不存在。

fl00 初次找到的 24 个含 mnist 名称的候选均是源码、文档、OpenCV 示例或示例图片等，没有规定的原始 IDX 文件；三客户端初次候选数均为 0。上述范围内没有可核验的官方数据，没有生成数据文件摘要。范围外、被排除目录、目录符号链接目标或采用其他命名的数据未覆盖，不能据此断言全盘不存在 MNIST。历史受控 IDX 的 GPU 训练记录不属于官方 MNIST 来源证明，本次没有将其作为官方数据。

## 官方来源、维护镜像与可达性

作者官方历史来源为 <http://yann.lecun.com/exdb/mnist/>。本次同时尝试该站 HTTPS；HTTPS 失败，因此没有从作者站取得可核实的 SHA-256 发布表。PyTorch 维护镜像为 <https://ossci-datasets.s3.amazonaws.com/mnist/>，Google CVDF 维护镜像为 <https://storage.googleapis.com/cvdf-datasets/mnist/>；镜像与作者主源分别标明，不将镜像称作作者原站。

所有数据 URL 仅使用 `curl --head --location`，连接限时 4 秒、单请求限时 8 秒、外层进程限时 10 秒。数据对象代表探测为 `train-images-idx3-ubyte.gz`，未向任何节点下载数据文件。

| 节点 | 作者 HTTP 网页 HEAD | 作者 HTTPS 网页及训练图像 HEAD | OSSCI 训练图像 HEAD | CVDF 训练图像 HEAD |
| --- | --- | --- | --- | --- |
| fl00 | 外层 10 秒超时 | curl 28，SSL 连接超时 | 最终 HTTP 200 | 最终 HTTP 200 |
| fl01 | HTTP 200 | curl 7，连接拒绝 | HTTP 200 | HTTP 200 |
| fl02 | HTTP 200 | curl 7，连接拒绝 | HTTP 200 | HTTP 200 |
| fl04 | HTTP 200 | curl 7，连接拒绝 | HTTP 200 | HTTP 200 |

fl00 输出中的 `200 Connection established` 是代理隧道状态，不能将其当作者站成功。没有打印代理配置或凭据。HTTP 200 仅证明当时相应 HEAD 可达，未读取网页内容、验证文件正文或逐一探测其余三个数据对象。CVDF 报告训练图像压缩包长度 9912422 字节及 ETag `f68b3c2dcbeaaa9fbdd348bbdeb94873`；不依赖 ETag 单独证明内容身份。

本次仅通过 HTTPS 读取了 torchvision 的小型源码元数据：<https://raw.githubusercontent.com/pytorch/vision/main/torchvision/datasets/mnist.py>。完整响应保存在 `torchvision-mnist-source.txt`，该文件的哈希由本目录清单冻结；URL 的 main 分支可变化，复核应以归档内容为准。其 MNIST 段明确列出作者历史来源和 OSSCI 镜像，四个压缩包的维护方 MD5 如下。这些是维护方 MD5，不是作者发布的 SHA-256，也不是未压缩 IDX 的摘要。

| 压缩包 | torchvision 维护的压缩包 MD5 |
| --- | --- |
| train-images-idx3-ubyte.gz | f68b3c2dcbeaaa9fbdd348bbdeb94873 |
| train-labels-idx1-ubyte.gz | d53e105ee54ea40749a09fcbcd1e9432 |
| t10k-images-idx3-ubyte.gz | 9fb629c4189551a2d022fa330f9573f3 |
| t10k-labels-idx1-ubyte.gz | ec29112dd5afa0611ce80d1b7f02629c |

后续获准正式准备数据时，建议保存确切来源 URL、取得时间、HTTP 重定向链和原始响应元信息；优先使用可达的维护方 HTTPS 镜像。先核对四个压缩包的上述 MD5，并对实际收到的压缩包另外计算 SHA-256；经 gzip 完整性检查后解压，对原始 IDX 分别计算 SHA-256，保存为本次部署冻结清单。没有可信作者 SHA-256 发布表时，明确标为“从已记录来源取得后计算的 SHA-256”，不能写成“官方发布 SHA-256”。MD5 在此用于对照维护方公开历史文件身份，来源仍须 HTTPS 取得记录和归档元数据支撑。

未压缩文件进一步校验如下，不能只比样本数：

| 原始文件 | IDX magic | 样本数 | 字节数 |
| --- | ---: | ---: | ---: |
| train-images-idx3-ubyte | 2051 | 60000 | 47040016 |
| train-labels-idx1-ubyte | 2049 | 60000 | 60008 |
| t10k-images-idx3-ubyte | 2051 | 10000 | 7840016 |
| t10k-labels-idx1-ubyte | 2049 | 10000 | 10008 |

同时检查 28×28、uint8、图像和标签对应计数、精确文件长度及标签 0～9。各客户端训练两文件 SHA-256 必须逐一相同，服务器测试两文件与冻结清单一致；部署要求四端均准备四文件，但准备阶段按角色读取两文件。本地路径只在本端配置，不由服务器下发目录。以上均为后续验证方案，本次未下载、解压、预置或执行数据准备。

## 所有失败与处理边界

1. 四端 `nvidia-jetpack` 元包查询失败，联合 dpkg-query 返回 1；同次已成功返回的 L4T/CUDA/cuDNN 数值照实记录，没有安装元包。
2. 三客户端系统 Python 导入 torch 均抛 `ModuleNotFoundError`，实际 GPU 加法未成功；torch/torchvision 发行版查询也失败。没有安装 wheel、切 CPU 或跳过节点报成功。
3. fl00 首次打印整个 `torch.cuda.get_device_properties()` 对象时出现 `UnicodeDecodeError`，加法尚未执行。保留原始失败，补充脚本逐字段读取并独立执行 GPU 加法，后者退出 0；对象整体字符串化问题未修改依赖修复。
4. 四端 `mnist.py`、`mnist_data.py` 安装文件不存在。保持原安装状态，后续正式部署解决。
5. 四端查询发行版名 `wfb-ng` 失败，补查名称未匹配；实际包路径、build identity 和逐文件摘要可用，不能伪造安装版本号。
6. 四端 `modinfo rtl88xxau_wfb` 失败；实际 `modinfo 88XXau_wfb` 成功，前者为设备树展示的驱动名，失败不代表工作驱动缺失。
7. `command -v nvcc` 在三客户端返回 1；绝对路径 `/usr/local/cuda/bin/nvcc --version` 成功，CUDA 工具实际存在但未加入其登录命令 PATH。未修改 PATH。
8. `/var/lib/wfb-ng-fl` 初次普通用户读取遇到权限拒绝，原始错误保留；之后 sudo 只读补查完成，四端 IDX 候选均为 0。
9. 作者站 HTTPS 拒绝/超时及 fl00 HTTP 超时见逐端网络原始日志，未替换为成功值。只探测网页 HEAD 不能证明作者站数据文件内容有效。
10. `/srv/wfb-ng-fl` 四端不存在；限定范围内无官方 IDX，来源与数据摘要验收仍待后续准备。

## 证据文件与复现方式

- `probe.py`：完整只读命令、逐命令超时和 GPU 加法脚本；`fl00.txt`、`fl01.txt`、`fl02.txt`、`fl04.txt` 为逐端原始输出，包含每个命令退出码。
- `gpu-addition-retry.py` 与 `fl00-gpu-addition-retry.txt`：fl00 属性打印失败后真实加法重测，保留两次尝试。
- `supplement.py` 与 `flXX-supplement.txt`：入口摘要、设备型号、服务前后 PID 与 nvidia-smi 字段。
- `data-root-readonly.py` 与 `flXX-data-root-readonly.txt`：sudo 下限定数据根补查。
- `network-probe.py` 与 `flXX-source-reachability.txt`：各端只读来源 HEAD 检查。
- `torchvision-mnist-source.txt`：通过 HTTPS 取得的维护方来源和 MD5 元数据，不含数据集正文。
- `cluster_nodes.conf.snapshot`：本次权威拓扑原文快照。
- `SHA256SUMS`：本目录证据文件 SHA-256，可在本目录执行 `sha256sum -c SHA256SUMS` 检查。

复现时从仓库外目录运行并禁用 Python 字节码写入，例如本机使用 `cd /tmp` 后 `PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 timeout 110 python3 -B - < probe.py的绝对路径`。远端通过标准输入发送同一脚本，不在远端落脚本文件：`ssh -o BatchMode=yes -o ConnectTimeout=7 -o ConnectionAttempts=1 -o StrictHostKeyChecking=yes -o ServerAliveInterval=10 -o ServerAliveCountMax=2 fl@192.168.108.201 'cd /tmp && PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 timeout 100 python3 -B -' < probe.py的绝对路径`，其外层使用 110 秒 timeout。另两端依权威配置换地址。脚本不启用 `set -e`，保留失败后继续采集，因此脚本或 SSH 退出 0 不等于所有检查成功。

本证据有效范围是上述采集时刻、节点与系统解释器。后续安装依赖、变更代码、数据或环境后应重新取证；本次不勾选 08 的部署、官方数据、准备屏障、真实训练、模型链、停止或恢复验收项。
