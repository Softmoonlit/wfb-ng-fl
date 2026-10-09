# 05: Stage 3 软件回归与规格/标准双轴审查

Type: task
Status: ready-for-agent
Blocked by: 01, 02, 03, 04

## What to build

在真实硬件运行前完成全量软件回归，并按仓库 code-review 流程分别审查实现是否符合工程标准、是否完整满足 Stage 3 规格。审查发现必须在进入硬件阶段前闭合。

## Acceptance criteria

- evidence 归档、射频 REST、作业参数/终态一致性、执行器、envelope 和 validator 的定向测试全部通过。
- `pytest -q` 全量通过。
- 对 `control.py`、`client_daemon.py`、`server_daemon.py`、`runtime.py` 及新增 helper 执行仓库要求的 Mypy 校验并通过。
- shell 入口通过 `bash -n` 和仓库已有 shell lint/测试能力。
- Standards review 与 Spec review 均由独立审查代理完成，发现按严重度记录并修复。
- 复审无阻塞性发现后把本工单设为 `resolved`，才允许正式硬件运行。
- 本工单不以软件 mock 冒充 40 MiB UFTP/HTTP 或真实射频证据。

## Comments
