# 04: Stage 3 单一执行器、run envelope 与严格 validator

Type: task
Status: ready-for-agent
Blocked by: 01, 02, 03

## What to build

新增唯一正式入口 `tests/fl_runtime/stage3_vm_physical_loop.sh` 及必要的结构化 Python helper，严格串联 `preflight -> install -> start-services -> run-sync -> run-radio-recovery -> collect -> stop-services -> validate`。执行器直接驱动 Stage 2 已实现的双 daemon 自治运行时，不新增平行的 RoleService、Runtime 或数据面实现。它使用独立 Stage 3 envelope，不继承 Issue #41 的 SSH RoleService 编排、五分区目录或旧 validator 结论语义；实现前先审计 `issue41_fl_runtime_loop.sh` 及其 helper，直接调用或小范围提取其中与编排语义解耦的硬件预检、独立构建安装、canonical fixture、SHA-256、资源核查和失败归档能力，禁止无理由复制。

## Acceptance criteria

- 提交中明确列出复用、提取和 Stage 3 专用实现的边界；不得复制已有通用 helper 后仅改名，也不得为 Stage 3 新建第二套 daemon、RoleService、Coordinator、Runtime、UFTP 或 HTTP Transport。
- 实现和现场诊断须参考 [Stage 2 三机无 SSH 运行时自治验收指南](../../../docs/acceptance/stage2三机无SSH运行时自治验收指南.md)中仍适用的生命周期、`TASK_READY`、SSH 边界、诊断顺序和资源清理经验；不得把该 Stage 2 指南当作 Stage 3 基线或通过依据，冲突时以 Stage 3 规格和本工单为准。
- `run-sync` 只通过 Server Daemon 本地 REST 发起作业，由现有带内 `TASK_ANNOUNCE`、Client Daemon 自主派生和 `TASK_READY` 屏障推进；禁止退回 Issue #41 的 SSH 作业启动路径。
- `preflight` 在 destructive action 前验证三端提交一致、工作树干净、身份配置、`wlx*`/驱动/xHCI、payload matcher、端口、磁盘和无旧资源。
- `install` 在 vm0 的临时独立 clean build worktree 中对目标提交执行 `make deb`，要求唯一 Debian 包并记录 SHA-256；同一包复制到三端后用系统包管理器安装，再核对 `dpkg-query`、daemon entry point、unit 文件和 `wfb_v6_uplink` 均来自该制品。禁止污染/借用运行工作树、使用不安装 daemon unit 的 `make install_v8`，或复用无法绑定本次包摘要的遗留二进制。
- `start-services` 受审计地启动正式 daemon unit，不使用 `systemd-run`。
- 所有 SSH 命令记录时间、目标、命令类别、退出码和允许阶段。
- `run-sync` 从 REST 接受到 Coordinator 终态期间，Stage 3 执行器不发起或维持 vm1/vm2 SSH 连接；validator 只对执行器记录作机械证明，不声称排除受信任环境外的其他管理员行为。
- `run-sync` 固定提交双 Client、两轮、40 MiB、`sync`、`live_observation=true` 的作业；fixture 只通过 `wfb_ng.fl.issue41_fixtures` 生成，两 Client update 摘要不同。
- validator 逐字段校验 Channel 157、12 dBm、下行 MCS 3、上行 MCS 6、15000 Kbps、HT40+、Short GI、FEC 8/14、`120 ms / 10 ms` 调度配置，以及服务启动后 120 秒内 node 1/2 `IDLE/READY`、作业 400 秒总门限、Coordinator 单轮和两端 Transport I/O 各 120 秒、租约恰为 15 秒、回退后 20 秒恢复；所有配置必须回读实际生成值，不能只读取请求。
- 射频场景在 Client 2 预置只匹配 `NEW_CHANNEL_PING`/`RADIO_SWITCH_FINALIZED` 的规则，通过 REST 发起 157→149，并在观察到租约自动回退后撤销规则。
- shell trap 在失败或中断时清理故障规则并停止受管服务，但保留原始证据。
- `collect` 保存作业终态和空闲资源状态；`stop-services` 把停服后进程/TUN 核查追加到同一归档；最终 `validate` 才裁决完整归档。失败 trap 同样执行故障规则清理、停服、失败现场追加和机器分类。
- validator 按 lifecycle outcome 的必选文件矩阵严格拒绝摘要错误、混合提交/作业、执行器 SSH 越界操作、evidence 缺失、observation 缺失、配置回读不符、摘要不符、跨轮不连续、资源残留或故障规则残留。
- 失败尝试形成机器可读分类并永久保留；重跑必须使用新 `run_id`/`job_id`。
- 为 envelope、SSH 阶段审计、evidence 校验和失败分类提供无硬件自动测试。

## Comments
