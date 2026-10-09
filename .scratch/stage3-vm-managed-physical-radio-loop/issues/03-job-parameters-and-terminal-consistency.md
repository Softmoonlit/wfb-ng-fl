# 03: 作业 I/O/观测参数贯通与 Server 终态一致性

Type: task
Status: ready-for-agent
Blocked by: None

## What to build

补齐 daemon 作业路径中 `run_id`、`io_timeout_seconds` 与 `live_observation` 从本地 REST 到两端 Role 配置的严格贯通，并使人工 abort 与自然完成/失败使用一致的 Server 持久链路终态收口。该工单只修复现有作业契约缺口，不新增可推测配置。

## Acceptance criteria

- `POST /api/v1/jobs/start` 要求并严格校验路径安全且非空的 `run_id`、正数 `io_timeout_seconds` 与 boolean `live_observation`；缺失任一字段即拒绝，不保留旧请求默认兼容路径。
- 更新所有现有 daemon 作业调用、测试 fixture 和 Stage 2 验收请求，显式提供三个必填字段；不增加 fallback 或旧 payload 兼容层。
- `run_id` 写入 Server 作业状态和 `TASK_ANNOUNCE`，Client 严格解析并贯通到 `ClientJobConfig`；它不替代唯一 `job_id`。
- ServerRole 构造显式使用请求中的 I/O timeout，不再静默落到 10 秒默认值。
- `TASK_ANNOUNCE` 携带两个字段；Client 严格解析后写入 `ClientJobConfig` 和 RoleService 配置。
- Client observation 路径固定生成在当前作业沙箱，拒绝通过广播注入任意本地路径。
- Stage 3 配置下两端生成配置回读均为 `io_timeout_seconds=120`、`live_observation=true`。
- `round_timeout_seconds` 与 Transport I/O timeout 保持独立字段和独立校验。
- REST `jobs/abort` 关闭 Coordinator/ServerRole、重置 Server 持久链路、广播带正确 `job_id` 的 `JOB_ABORT` 并恢复 `IDLE`，与自然完成/失败共享唯一终态 helper。
- 测试覆盖三个字段缺失时严格拒绝、非法类型/范围、announce 严格解析、observation 固定路径、两端配置回读及 abort 链路重置失败。
- 相关 Coordinator、Server/Client Daemon、生命周期测试和 Mypy 通过。

## Comments
