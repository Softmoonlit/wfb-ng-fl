# 05: Stage 3 软件回归与规格/标准双轴审查

Type: task
Status: resolved
Blocked by: 01, 02, 03, 04

## What to build

在真实硬件运行前完成全量软件回归，并按仓库 code-review 流程分别审查实现是否符合工程标准、是否完整满足 Stage 3 规格。审查发现必须在进入硬件阶段前闭合。

## Acceptance criteria

- evidence 归档、射频 REST、作业参数/终态一致性、执行器、envelope 和 validator 的定向测试全部通过。
- `pytest -q` 全量通过。
- 对 `control.py`、`client_daemon.py`、`server_daemon.py`、`runtime.py` 及新增 helper 执行仓库要求的 Mypy 校验并通过。
- shell 入口通过 `bash -n` 和仓库已有 shell lint/测试能力。
- Standards review 与 Spec review 均由独立审查代理完成，发现按严重度记录并修复。
- 审查必须确认 Stage 3 继续使用 Stage 2 自治运行时，且已优先复用或提取 Issue #41 通用验收能力；出现平行 daemon/Runtime/Transport、SSH 作业编排回流或无理由复制 helper 时视为阻塞性发现。
- 审查应把 Stage 2 三机无 SSH 指南作为已有生命周期与现场诊断经验的参考，但不得用其 4 MiB 人工验收口径替代 Stage 3 规格或验收标准。
- 复审无阻塞性发现后把本工单设为 `resolved`，才允许正式硬件运行。
- 本工单不以软件 mock 冒充 40 MiB UFTP/HTTP 或真实射频证据。

## Comments

### 审查范围与结果

审查基线为 `8651bc61b0b340f3fa2a1acae1f4010b01db2405`，覆盖 01～04 的实现提交 `71ae7d5`、`3c6e520`、`9f048ad`、`7181d94`，以及本工单修复后的工作树。Standards 与 Spec 由两个独立代理审查并复审；两轴均确认继续直接复用 Stage 2 双 daemon、Coordinator、RoleService、Runtime、Transport 与 `TASK_READY`，以及 Issue #41 canonical fixture、SHA-256、硬件发现和生命周期审计。未发现平行运行时、SSH 作业编排回流或以 Stage 2 的 4 MiB 口径替代 Stage 3。

| 审查轴 | 严重度 | 发现与闭合 |
| --- | --- | --- |
| Standards | 阻塞 / 高 | archive 重复 identifier helper：删除 `_identifier`，统一复用 `artifacts.validate_path_safe_identifier`。runner 独立检查既有 111 字符长度上限，为派生角色与 evidence 临时目录留足空间；最大允许值及下一字符均有回归。 |
| Standards | 中，判断项 | producer 重复固定参数簇：fixture 大小与 `jobs/start` 参数统一从 `FIXED_CONFIG` 派生；validator 保留独立规格硬门槛，复审认可其防止共同漂移的作用。 |
| Spec | 阻塞 / 高 | validator 未裁决 postflight/unit：现强制读取三端运行后 commit、clean 和活动 unit，并与启动记录、安装包摘要、unit 文件摘要、FragmentPath、ExecStart、KillMode、drop-in 交叉校验。 |
| Spec | 高 | 终态广播失败仍可能 IDLE，且缺少终态语义检查：Server 在广播失败或控制面缺失时进入 STOPPED 并抛出 `terminal_broadcast_failed`；validator 要求两个 REST 快照中 `active_job=null`、持久链路与节点 IDLE/READY，并核对作业身份绑定的 Server 广播/收口及 Client 清理日志。 |
| Spec | 阻塞 / 高 | preflight 只信汇总：现要求原始 `topology.json`，核对角色映射、提交、工作树、无线接口/驱动、xHCI、USB speed、MAC、Client node identity、TUN IP 和停服资源事实。 |
| Spec 复审 | 阻塞 / 高 | 成功 Client 可能因完成信令终止角色而得到 `-15/-9`：遵循 ADR-0015，以 succeeded lifecycle 和完整成功证据矩阵裁决，同时要求 journal 退出码与 evidence manifest 等值；一致负退出码允许，不一致严格拒绝。 |

上述缺失/错误证据可在重新封口后仍被拒绝，已通过公开 `validate_archive()` 的 red→green 回归验证。Standards 与 Spec 最终复审均无阻塞发现。

### 软件回归修复

全量回归暴露 E2E 测试自身的并发寻频：`start_control_plane()` 后台已拥有寻频与 socket，测试主线程又调用 `hunt_once()`，导致 READY 之后仍收到 HUNTING 心跳；捕获实际 400 为 `preflight_target_nodes_not_ready`。删除主线程寻频，改用已有状态轮询等待 Client 锁定 157、IDLE 与 Server READY/IDLE。没有新增固定 sleep、作业启动重试或运行时旁路。原定向循环第 8 轮复现，线程归属探针修复前失败、修复后通过，修复后三项 E2E 连续 17 轮共 51 次通过；两轴也独立复审了该测试改动。

旧链路重置测试补齐广播边界并断言关闭角色、重置链路、终态广播的顺序；失败 evidence 测试使用稳定 `runtime_evidence` 分类断言，避免绑定错误文案。

### 验证环境与结论边界

默认 `/tmp` 所在根分区在生成 40 MiB fixture 时触发 ENOSPC。软件测试改用 `TMPDIR=/dev/shm` 与独立 `--basetemp`；保持 canonical fixture、制品大小及验收门槛不变。该环境失败不能算作通过证据。

本工单未连接 vm1/vm2，未执行实体射频、Debian 三端安装或 40 MiB UFTP/HTTP 真实空口验收。软件 mock 与本地归档测试只用于软件契约回归，正式三机证据由工单 06 交付。

## Answer

01～04 的全量软件回归、Standards/Spec 独立审查、发现修复及复审已完成。本工单设为 `resolved`，解除工单 06 的软件前置阻塞；真实硬件准备与正式运行仍按工单 06 执行。

最终工作树验证：

- 仓库根目录全量：`TMPDIR=/dev/shm python3 -m pytest -q --basetemp=/dev/shm/stage3-issue05-release`，**355 passed in 125.23s**。
- evidence/build identity、射频 REST/事务/租约、作业契约、runner、envelope/validator、hardware helper 定向回归：**204 passed**。
- `mypy --follow-imports=silent` 检查以下 13 个文件，**Success: no issues found in 13 source files**：
  `wfb_ng/fl/control.py`、`client_daemon.py`、`server_daemon.py`、`runtime.py`、`evidence.py`、`artifacts.py`、`coordinator.py`、`role.py`、`transport.py`；`tests/fl_runtime/hardware.py`、`issue41_envelope.py`、`stage3_runner.py`、`stage3_archive.py`。
- `bash -n tests/fl_runtime/stage3_vm_physical_loop.sh`、相关 Python 编译、`git diff --check` 均通过。环境未安装 shellcheck；仓库已有 shell 阶段/失败 trap/信号回收测试随定向和全量回归通过。
- Standards 最终发现数 **0**，Spec 最终发现数 **0**；两轴原始严重发现及追加边界均已闭合。
