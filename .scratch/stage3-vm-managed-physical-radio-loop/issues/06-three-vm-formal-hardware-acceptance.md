# 06: 三机 40 MiB sync 与射频租约回退正式硬件验收

Type: task
Status: ready-for-human
Blocked by: 05

## What to build

在 vm0、vm1、vm2 三台 Linux VM 和 USB 直通真实 `rtl88xxau_wfb` 网卡上执行 Stage 3 唯一正式入口，生成一组完整通过的正常作业与射频受控故障归档。当前三端均未发现 `wlx*` 接口；操作者完成每台 VM 的 USB xHCI 直通后，本工单可转为 `ready-for-agent` 执行。

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
