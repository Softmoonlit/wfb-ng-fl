# 分流与完成状态

本仓库采用本地 Markdown 工作流，只使用以下五个状态：

| 状态 | 含义 |
| --- | --- |
| `ready-for-agent` | 规格完整，可交给 AFK agent 执行 |
| `ready-for-human` | 需要人工实现、判断或外部操作 |
| `wontfix` | 不计划处理 |
| `need-for-review` | 实现已完成，等待 code review |
| `resolved` | 实现已完成，且 code review 已完成 |

## triage skill 边界

本仓库不使用 `triage` skill 的 `needs-triage` 和 `needs-info` 状态，也不创建这两个标签。

新任务应直接根据当前处理结果使用上述状态之一。实现完成后使用 `need-for-review`；code review 完成后使用 `resolved`。
