# 06: 三机 40 MiB sync 与射频租约回退正式硬件验收

Type: task
Status: ready-for-agent
Blocked by: 05

## What to build

在 vm0、vm1、vm2 三台 Linux VM 和 USB 直通真实 `rtl88xxau_wfb` 网卡上执行 Stage 3 唯一正式入口，生成一组完整通过的正常作业与射频受控故障归档。三端已发现 `wlx*` 接口且均由 xHCI 纳管；操作者已确认网卡性能满足要求，无需交换 USB 直通。vm0 已清理可再生成缓存并达到磁盘预检门槛，继续执行正式验收。

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
