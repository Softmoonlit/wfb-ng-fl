# 08: 完成 Jetson 部署检查与 40 MHz 少轮试跑

**What to build:** 在现有 fl00、fl01、fl02、fl04 四板上部署已实现的参考任务，核实环境与 GPU，完成 40 MHz 少轮真实无线训练、模型关联、正常停止与待命恢复，形成后续实机联调的可信起点。

**Blocked by:** 05（[贯通五轮模型链与末轮安全收尾](05-multiround-model-chain.md)）、06（[落实有界等待、失联失败与停止作业](06-job-deadlines-stop-and-failure.md)）。

**Status:** ready-for-agent

**测试底座：** [fl00 + fl01、fl02、fl04 四板与已有通信基线](../spec.md#当前四板测试底座与实施进度)。本次已验证文件交换，不代表本票 GPU／数据／训练检查通过。

**规格依据：** [参考环境与部署验证边界](../spec.md#testing-decisions)，用户故事 8～11、18、20、23、35、36。

**执行条件：** 真实 Jetson、无线设备及管理访问已具备，不等待新增设备。`Blocked by` 的 05、06 均为 `resolved` 后执行训练试跑；逐端依赖、GPU 和数据核验可提前准备，尚未取得的版本、耗时或 GPU 成功不得填写。本票不阻塞其他无需其测量结果的软件票；最终故障与正式验收必须等待本票通过。实际执行由 agent 通过现有管理访问完成，需要实体操作时再协调人工。

- [x] 核对并记录当前 4 块 Jetson：fl00 为服务端，fl01、fl02、fl04 为三个客户端，保持节点 ID 1、2、4 与既有 TUN 地址；记录网卡／驱动和现场条件，预部署匹配代码，逐端核对实际安装产物与构建身份。
- [x] 逐端记录实际 JetPack、CUDA、Python、PyTorch 及 GPU 信息，确认兼容安装组合；客户端实际 GPU 操作通过，不自动降级 CPU，记录服务器实际评估设备。
- [x] 预置官方 MNIST 并保存来源，检查客户端 60,000 与服务器 10,000 样本及固定文件摘要，各客户端对应内容一致；记录本地配置而不下发服务器目录。
- [ ] 选择同一明确初始制品，冻结算法入口、配置、实际期限与目标，采用合法固定 40 MHz 信道与已有适用速率；不为本票启用缺表的 20 MHz。
- [ ] 经现有作业入口完成至少跨越两个轮次的少轮真实训练，取得全员准备、GPU 优化、上传、聚合与评估事实，并核对后一轮加载前一轮真实聚合模型。
- [ ] 核对实际样本数、步数、训练损失、设备、初始／逐轮指标和产物摘要，不用参数文件变化或日志自报单独证明训练发生。
- [ ] 检查正常收尾、客户端末轮提交确认和待命恢复；另以短作业检查正常停止，清楚区分受理、计算退出及资源恢复。
- [ ] 作业运行阶段的唤醒、文件交换、推进与复位不依赖 SSH；部署和事后取证可以使用必要管理访问，不混淆两种边界。
- [x] 保存所有尝试和问题；依赖、数据或设备失败如实修复再测，不通过换 CPU、复制模板或跳过节点使试跑成功。
- [ ] 形成环境与试跑记录，交接有效期限、部署条件和未决故障；不把少轮试跑记入三次五轮验收或提前认定参考效果达标。

## 执行记录（尚未完成）

四板依赖、官方 MNIST 与固定模型 GPU 单批次检查已取证；同版 `e53de00` 的两次真实尝试均失败且保留全部日志。首次运行时 systemd drop-in 覆盖数据参数，已通过有效 ExecStart 和第二次实际上下文确认修正；第二次服务器观察到 node 2 心跳静默 10 秒，在训练前失败。未取得两个完整训练轮次，不标记本票完成。

[纯空闲被动观测](../evidence/08-jetson-preflight/diagnosis/passive-idle/README.md)确认客户端持续生成心跳，fl01/fl02 上行出现数十秒积压与成批交付，和原生有效授权发送停滞对应；仍需定位授权／寻频／射频路径。跨板时钟不同步，不按未经校正的墙钟推算事件先后。当前无证据要求人工换卡或拔插。

另已通过公开 `service.main()` 受控测试确证并修复启动顺序缺陷：任务链路先启动，再动态导入算法，导入前后检查停止，避免已停止后继续启动算法；不声称能中断同步 Python import。三个完整 service 检查文件 **66 项通过，无排除**。该修复已由 `69a94d8` 提交并在四板部署；不代表已解决全部空闲波动。详见[启动顺序修复证据](../evidence/08-jetson-preflight/service-start-order/README.md)。

[环境与所有实机证据](../evidence/08-jetson-preflight/README.md)。

### 同版 native 诊断结果

[69 版本数量差分](../evidence/08-jetson-preflight/formal-69a94d8/phase-analysis.md)显示单独 fl02 时未见寻频，多客户端阶段复现寻频和心跳延迟；这不是训练验收，也不能仅以结束时 READY 认定稳定。

四板随后统一到 `701963a`，实际运行 native 均核对为 `f7c73f41…`，并确认从安装目录加载。[85 秒被动观测](../evidence/08-jetson-preflight/formal-701963a/native-unification/passive85s/README.md)仍复现故障：fl02 最大心跳生成间隔 5.238 秒，服务端接收间隔 13.438 秒；`pending_unauthorized_fec_due` 与 `zero_timeout_polls` 增量均为零，最长 RX batch 为 523 微秒。因此本窗口不支持 FEC 到期忙等或长 RX batch 是直接触发点，继续按 READY、grant、静默移除与寻频序列定位，不扩大超时、不凭瞬时 READY 重跑训练。采证结束无残留进程；四板版本、制品与实际加载身份均有记录。

[后续三阶段 ICMP 活动对照](../evidence/08-jetson-preflight/formal-701963a/icmp-activity-diff/README.md)仅改变是否持续输入上行小包，服务、PID、构建及射频参数保持不变。fl02／fl04 在活动阶段维持授权和 READY refresh，ICMP 丢包约 1%～2%；fl01 活动阶段没有 grant、没有有效上行，175 个 ICMP 全丢，服务端整阶段未收到其心跳。停止探针后 fl01 恢复发送，约 94.6 毫秒内交付 161 个旧 ICMP。其恢复阶段首次出现 `pending_unauthorized_fec_due` 与 `zero_timeout_polls` 各增加 4952；该阳性发生在积压之后，不解释最初失联。不能将之前 85 秒窗口的零计数外推成此分支永不发生。

[迟到 ACK 跨频道边界诊断](../evidence/08-jetson-preflight/diagnosis/ack-channel-identity/README.md)在真实 loopback UDP 中三次均未误锁频道：现有发送前 drain 清除了指定窗口的旧 ACK。该候选边界已被证伪，不据此修改 ACK 协议或扩大寻频期限。

[固定信道 157 standalone 64 KiB 对照](../evidence/08-jetson-preflight/standalone157-runner/README.md)已完成：四端在 `701963a` 与统一 native SHA `f7c73f41…` 下，停止完整 daemon 后用固定 157／HT40+ native 路径完成 fl01、fl02、fl04 三端 64 KiB 下行和回传，六份文件摘要全部一致，服务恢复无错误。fl02 回传仅约 0.14 Mbps，fl01 约 2.65 Mbps，fl04 约 17.06 Mbps，表明固定信道路径可用但链路质量差异明显。该结果支持“动态 HUNTING／控制宿主路径是进一步排查重点”，不能替代完整 daemon 心跳、真实训练或最终验收，也不证明已经修复 08。完整事实见该证据目录的 run 记录。

随后执行了 fl02 的较大文件参数对照，所有组均保持信道 157／HT40+、FEC 8/14、Server 下行 MCS 3、四板代码与 native 制品一致，并在每组结束恢复 daemon：上行 MCS 4、3、2 均无法在 120 秒内完成 4 MiB 回传；实际功率从 -14 dBm 提高到 -10 dBm 后仍无法完成。fl01、fl04 同期均可完成回传，已完成文件 SHA-256 一致。结果不支持仅通过降低 MCS 或提高 fl02 发射功率修复问题，说明还需检查控制帧路径、网卡/驱动、天线和 USB 环境。详情见 [MCS 与功率对照](../evidence/08-jetson-preflight/mcs-power-matrix/README.md)。

随后对 `-18 dBm / MCS 4` 做了三次重复：一次 fl01 下行校验失败，一次 fl02 超过 120 秒且 fl01/fl04 完成，一次 UFTP 退出码 7。fl02 是最严重节点，但 fl01、fl04 也存在间歇性失败，不能把问题简化为单一 fl02 功率设置。完整记录见同一 MCS/功率证据目录。

[信道 149 / HT20 对照](../evidence/08-jetson-preflight/channel149-ht20/README.md)在四端同版、`-14 dBm`、Server MCS 3、客户端 MCS 4、FEC 8/14 下执行了一次 4 MiB standalone 对照。四端实际读回 149 / 20 MHz，native argv 也为带宽 20；但 UFTP 下行以退出码 7 中止，fl02/fl04 完成注册而 fl01 未注册，未进入上行。恢复无错误，最终回到 157 / HT40+、`-14 dBm`、Server `IDLE`、三客户端 `READY`。该结果不支持切换到 149 / HT20，也不能作为 20 MHz 速率标定证据。

[单客户端 fl02 / 157 / HT40+ 诊断](../evidence/08-jetson-preflight/single-client-fl02-157-ht40/README.md)仅保留 node 2，服务端 target/known-clients、UFTP 和 HTTP 收尾均只针对 fl02；fl01/fl04 只停服并恢复。fl02 能收到服务端包并生成声明，但 `GRANT_SEQ=0`、授权发送为 0，服务端 `PKT_SRC node2` 全程为 0，UFTP REGISTER 最终以退出码 6 结束，未进入文件传输。该结果排除了“只有三客户端同时在线才会失败”的充分解释，但尚不能单独区分 fl02 本地射频/驱动链路与 native 控制帧协议问题。恢复后四端回到 157 / HT40+、`-14 dBm`、Server `IDLE`、三客户端 `READY`。

随后使用同一单客户端 runner 完成 fl04 和 fl01 原始硬件对照，均为 157 / HT40+、`-14 dBm`、Server MCS 3、客户端 MCS 4、4 MiB：原 fl04 网卡在 fl04 下行和上行均成功，服务端持续收到 node4 并发 grant，上行约 9.17 Mbps；fl01 下行和上行均成功，服务端持续收到 node1 并发 grant，上行约 9.11 Mbps，文件 SHA-256 均一致。单节点对照显示共同 Server/native 基础路径可用。三次单节点记录分别见 `single-client-fl01-157-ht40`、`single-client-fl02-157-ht40` 和 `single-client-fl04-157-ht40` 证据目录。runner 恢复采样曾出现短暂 READY/OFFLINE 时间窗，随后通过标准四端重启和 `iw` 读回恢复。

[fl02/fl04 网卡互换与 19 V 供电交叉诊断](../evidence/08-jetson-preflight/swap-cross-matrix/README.md)完成了交叉变量隔离：fl02 使用原 fl04 网卡并改为 19 V 后，成功获得 grant、完成 4 MiB 下行，但上行仍在 120 秒超时；fl04 使用原 fl02 网卡后，4 MiB 双向文件均完整且 SHA-256 一致，但上行耗时 86.385 秒、约 0.39 Mbps，远低于交换前原 fl04 网卡约 9.17 Mbps。该矩阵支持“原 fl02 网卡自身劣化”与“fl02 主机/位置/天线/USB/供电链路独立异常”叠加，不能归因于单纯 MCS、功率或 Server 调度代码。操作者报告 fl02 已换为 19 V，但板端无输入电压遥测。

[fl02/fl04 物理位置互换对照](../evidence/08-jetson-preflight/position-cross-matrix/README.md)在网卡已互换、两台现场均使用 19 V 的条件下进一步只互换物理位置：fl02 主机搭配原 fl04 网卡移动到 fl04 原位置后，4 MiB 双向成功，上行约 4.61 Mbps；同一网卡在 fl02 原位置时上行超时。fl04 主机搭配原 fl02 网卡移动到 fl02 原位置后，下行成功但上行超时；同一网卡在较好位置时可完成但仅约 0.39 Mbps。该对照直接复现位置/天线环境影响，并与原 fl02 网卡自身劣化叠加，继续排除单纯 MCS、功率或公共 Server/native 代码作为首要根因。两轮结束后均通过连续 6 个 API 采样确认 Server `IDLE`、三客户端 `READY`。

### 暂缓的硬件与射频诊断

[fl04 新较好位置单客户端验证](../evidence/08-jetson-preflight/new-position-fl04/README.md)在 fl04 移动到新的较好位置后完成：当时仍使用原 fl02 网卡 `fc:22:1c:30:0c:bb`、现场 19 V、157 / HT40+、MCS 4，4 MiB 下行和上行均成功且 SHA-256 一致，上行约 1.28 Mbps。该位置可以支持单节点闭环，但原 fl02 网卡吞吐明显劣化。

[三客户端新 fl02 网卡 smoke test](../evidence/08-jetson-preflight/three-client-new-fl02-nic-smoke/README.md)完成了仅替换 fl02 网卡后的通信验证：三客户端下行均成功；fl02 新网卡 `fc:22:1c:50:0a:88` 上行 4 MiB 成功，约 1.76 Mbps；fl04 上行超过 120 秒超时，实际 MAC 仍为原 fl02 网卡 `fc:22:1c:30:0c:bb`。该结果确认新 fl02 网卡已改善 node 2，当前三客户端失败点转移到 fl04；需先核对/更换 fl04 网卡，再进行三客户端 smoke test，不进入完整 FedAvg。

后续天线、馈线、USB、供电负载和逐链 RSSI 检查统一记录在[工单 08 后续硬件与射频诊断计划](../evidence/08-jetson-preflight/pending-hardware-diagnostics.md)。这些检查暂不阻塞其余软件工单，但在进入三客户端完整 FedAvg 实训前仍必须完成；当前 08 不能标记为通过。

[三客户端双新网卡 smoke test](../evidence/08-jetson-preflight/three-client-new-nics-smoke/README.md)确认 fl02 `fc:22:1c:50:0a:88` 与 fl04 `fc:22:1c:30:0f:04` 均已更换。r2 中三端 HTTP 201、4 MiB 和 SHA-256 全部成功，但 r3 在下行注册阶段因 fl01 掉出退出码 7，r4 又因 fl01 多轮 NAK 后超时退出码 9；fl01 日志出现约 `-76 dBm` 接收链和大量 `PACKET_LOSS`。因此双新网卡已改善 fl02/fl04 的已知硬件变量，但三客户端整体仍未稳定，当前必须处理 fl01 的位置/天线/USB/供电或链路环境，不进入完整 FedAvg。r4 后通过 fl01、fl04 顺序重启恢复 Server `IDLE`、三客户端 `READY`。

[当前双新网卡条件下的单 fl01 对照](../evidence/08-jetson-preflight/single-client-fl01-current/README.md)仅保留 fl01 后完成 4 MiB 下行和上行，HTTP 201、SHA-256 一致，上行约 0.64 Mbps；Server 能持续给 fl01 发 grant，但仍记录上行丢包。该结果说明 fl01 网卡并非完全失效，而是链路余量低，三客户端并发时容易越过失败阈值。单 fl01 传输通过，但恢复观察未确认全员 READY，随后 fl01/fl02/fl04 状态轮换，未伪造稳定恢复。