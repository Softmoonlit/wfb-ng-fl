# 06: 三机 40 MiB sync 与射频租约回退正式硬件验收

Type: task
Status: resolved
Blocked by: 05, 07

## What to build

在 vm0、vm1、vm2 三台 Linux VM 和 USB 直通真实 `rtl88xxau_wfb` 网卡上执行 Stage 3 唯一正式入口，生成一组完整通过的正常作业与射频受控故障归档。三端已发现 `wlx*` 接口且均由 xHCI 纳管；操作者已确认网卡性能满足要求，无需交换 USB 直通。vm0 已清理可再生成缓存并达到磁盘预检门槛；正式运行发现终态恢复阻塞，待工单 07 修复后继续。

## Acceptance criteria

- 三端处于同一已审查提交且工作树干净，真实无线接口按 `wlx*` 动态发现，严禁硬编码接口名。
- 固定使用 Channel 157、12 dBm、下行 MCS 3、上行 MCS 6、15000 Kbps、HT40+、Short GI、FEC 8/14。
- 正式 daemon 服务启动后 120 秒内 node 1/2 达到 `IDLE/READY`。
- 两轮 40 MiB `sync` 作业在 400 秒内成功；Coordinator 单轮 timeout 与两端 Transport I/O timeout 均回读为 120 秒，`live_observation=true`；每轮 `committed_nodes == [1, 2]` 且无掉队节点。
- 模型与两份节点 update 的大小、manifest 和 SHA-256 全部匹配，第 2 轮输入摘要等于第 1 轮输出摘要。
- orchestration log 证明 Stage 3 执行器在正常作业观察窗口没有 vm1/vm2 SSH 操作。
- 两 Client evidence manifest 完整有效，包含成功场景必选的 observation 和两轮状态/manifest，且没有保存 40 MiB 文件本体。
- 受控故障场景形成 157→149 COMMIT、Server `rolled_back`、Client 租约超时回退和 20 秒内重新 `IDLE/READY`。
- 故障规则最终删除；作业终态只保留空闲资源，`stop-services` 后不遗留相关进程或 TUN。
- `validate` 输出 `passed`；失败尝试保留并使用新 run 重试。
- 最终归档记录实际提交、配置、耗时、结论边界和未验证能力，不声明真实训练、FedAvg、air-gapped、冷启动或多于两个 Client。

## Comments

### 2026-10-09 现场预检：网卡分配与磁盘阻塞

- 已确认工单 01～05 均为 `resolved`，开始执行正式入口 `preflight`，目标提交为 `4dad8eb6e4e5655870c4bcc23a5c5d46d16b2167`。
- 失败尝试保留于 `tests/logs/stage3_20261009_165809_63073c18509e/`；`failure.json` 分类为 `environment`，原因为 `server: physical adapter allocation mismatch`。没有进入安装、启动服务、作业或射频故障阶段。
- 动态发现结果：vm0 为 `fc:22:1c:50:0b:fe`（USB 5000 Mbps），vm1 为 `5c:ff:ff:af:6d:8c`（5000 Mbps），vm2 为 `fc:22:1c:10:01:57`（480 Mbps）；三端驱动均为 `rtl88xxau_wfb`、控制器均为 `xhci_hcd`。需在宿主机交换 vm0/vm1 USB 直通，使发射性能最优网卡归 Server；不放宽执行器门禁。
- vm0 `/var/tmp` 所在根分区仅余 778 MiB，低于预检要求的 1 GiB。既有硬件归档未删除；需扩容或释放空间，建议为独立 Debian 构建和 40 MiB 制品归档预留数 GiB。
- vm1/vm2 原提交均为 `59f5cc5`，工作树干净。远端 origin 尚无本机目标提交；通过本机目标提交的增量 git bundle，执行 `git fetch` 和 `git merge --ff-only FETCH_HEAD`，现三端均为 `4dad8eb6e4e5655870c4bcc23a5c5d46d16b2167`，两 Client 工作树干净；未使用 reset 或推送远端。
- 两 Client 身份文件分别为 node 1 / `10.80.0.11` 和 node 2 / `10.80.0.12`。准备诊断与同步命令追加在本次 orchestration log；该失败尝试不形成通过结论，环境修正后必须使用新 run/job ID 重跑。

### 2026-10-09 操作者确认与环境整理

- 操作者明确确认当前连接的所有网卡性能满足要求，无需换卡。已更新 canonical spec：移除按 MAC 指定 Server 或排除个体的规则，MAC 仅用于硬件身份记录；执行器与离线 validator 同步执行此口径，保留动态接口、驱动、xHCI 和 USB 速度门禁。
- 清理无运行中 pytest 占用的 `/tmp/pytest-of-virt`、npm 下载缓存、Mypy/pytest 缓存和 APT 缓存，vm0 根分区可用空间由 778 MiB 增至约 1.7 GiB。既有硬件归档、代码、已安装依赖均保留。

### 2026-10-09 实机执行结果：终态恢复仍阻塞

- 已提交并同步三端：`3af888b` 移除 MAC 个体门禁；`dcf1b97` 修复 Makefile 独立工作树 ENV 与含空格 PATH；`5a39668` 修复 UFTP 注册重试窗口；`23c08af` 将控制面监听/广播源地址绑定 TUN，避免受限广播经管理网绕过射频。
- 补齐 vm0 Debian 构建依赖 `python3-all-dev`、`debhelper`、`dh-python` 以及三端运行依赖 `python3-serial`、`socat`。三端 `/usr/local/lib/python3.10/dist-packages/wfb_ng` 旧安装已备份至 `/var/tmp/wfb-stage3-legacy-20261009/`，避免遮蔽本次 Debian 包。
- `stage3_20261009_165809_63073c18509e`：旧 MAC 门禁失败；`stage3_20261009_170641_5c723fcbb8ef`：旧软件测试沙箱残留，已保留至该失败档案的 preparation-evidence；`stage3_20261009_170731_c16d6ebb25ad`：构建目录/PATH；`stage3_20261009_170958_deaf20bab28c` 与 `stage3_20261009_171145_47eabd412c3c`：缺少构建依赖；`stage3_20261009_171245_5a2e829e4290`：运行包依赖；`stage3_20261009_171408_7bef0ce56bf7`：旧 Python 安装遮蔽；以上失败均保留。
- `stage3_20261009_171554_8149f6eb3bc5`：第一轮 48.852 秒完成，第二轮 UFTP exit 7。真实双 namespace 实验复现快速节点压低 GRTT 后延迟 1.3 秒的节点无法注册；原参数失败、`0.5:0.1:2.0` 成功。诊断脚本保留在此档案 diagnostics，临时 namespace/veth/bridge 已删除。
- `stage3_20261009_172803_29b31eab95a7`：两轮成功（52.280 / 55.002 秒，无掉队），终态 20 秒内未恢复 READY。
- `stage3_20261009_173418_4d7f1cee9af0`：两轮及 IDLE/READY 恢复通过；射频请求却 finalized，精确规则未作用。诊断确定旧受限广播走管理网；该尝试不构成射频回退或完整通过证据。失败遗留的 149 缓存已备份至各 Client `/var/tmp/stage3-failed-149-channel-cache.json` 后恢复基线，非正式回退证据。
- 最新 `stage3_20261009_175625_b5de8bffbe5f`，提交 `23c08af6b59300b0b1a59e7566cc17642234b1ca`：三端 preflight、同包安装、正式服务就绪通过，Coordinator 两轮 succeeded，但仍因终态恢复超时失败，未进入射频场景。三端 failure-resources 均 clean，进程/TUN 与故障规则已清理。
- 软件验证：网卡/执行器定向 112 项通过；控制路由/daemon 定向 82 项通过；独立复审另跑 100 项通过，未发现已提交修复的阻塞问题。
- 后续阻塞详见工单 07；本工单保持未解决，尚无 validator `passed`，不得宣布 Stage 3 完整硬件通过。
## Answer

2026-10-09：工单 07 已修复终态启动会话、GRANT 授权、registry 身份与空闲链路所有权问题，并闭合收集权限与多值目标校验缺陷。本工单验收完成，设为 `resolved`。

正式归档：`tests/logs/stage3_20261009_203210_c0c3bd93b53c/`，提交 `ab66d45d8183bc72dca8a185a7b98bc972a18bd0`。三端提交一致、工作树干净、安装同一 Debian 包，动态无线发现、驱动、xHCI 与 USB 检查通过。唯一正式入口完成全部执行阶段，validator 为 `passed`、`errors=[]`。

- 双 Client 两轮 40 MiB sync：69.317 / 60.320 秒，每轮 committed 为 `[1, 2]`、无掉队；作业接受到终态约 129.835 秒，小于 400 秒。
- 模型与两份 update 的大小、manifest、SHA-256 和轮次连续性通过；单轮及 I/O timeout 保持 120 秒，live observation 开启。
- 正常终态 0.255 秒恢复两 Client `IDLE/READY`；正常作业窗口无执行器 Client SSH 操作。
- 157→149 COMMIT 受控丢包后 Server rolled_back；Client 2 的 LEASE_TIMEOUT 到全集群 READY 为 12.451 秒，小于 20 秒，最终均为 157。
- 两 Client succeeded evidence 各有 14 个白名单小型文件，没有模型/update 本体；配置固定为 12 dBm、下行 MCS 3、上行 MCS 6、15000 Kbps、HT40+、Short GI 和 FEC 8/14。
- 故障规则已删除，空闲只保留每端 daemon/持久底座；停服后三端相关进程、cgroup 和 TUN 均清空。
- 最终 Python 全量 395 项通过，C++ 会话/FEC/加密回归与全部生产构建通过，13 个相关模块及最终 validator/runner Mypy 通过；独立复审无阻塞。

旧失败归档保持失败结论，压缩副本经逐文件比较校验保留。通过边界仅为三机双 Client、确定性占位训练/聚合、作业运行时无 SSH 依赖；不声明真实训练/FedAvg、air-gapped、冷启动或更大硬件集群。

