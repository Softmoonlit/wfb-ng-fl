# ADR-0006: v6 集成式链路层底座采用 clean cutover

Status: accepted

v6 是链路层底座替换阶段，采用 clean cutover：新集成式链路层底座必须直接承接 `TUN` 读写、空口 `TX/RX`、Token gate、固定容量用户态队列、动态逻辑水位、反向控制窗口与对 `TUN` 的反压。旧 split-process 数据路径只保留为 v5 迁移基线、人工对照和回滚参考，不能作为 v6 主线运行时 fallback；如果新底座未通过冻结双基线与内部可观测性验收，结论是 v6 未完成，而不是退回旧路径后通过。

## Considered Options

- v6 clean cutover，只允许旧 split-process 作为基线和对照
- v6 运行时允许自动退回旧 split-process 数据路径

## Consequences

- v6 PASS 必须来自新集成式链路层底座本身
- 队列受限、控制窗口推进和 `TUN` 可写信用变化不能由外围 split-process 兜底伪装
- 旧 split-process 仍可用于迁移前后对照排查，但不进入 v6 主线验收路径
