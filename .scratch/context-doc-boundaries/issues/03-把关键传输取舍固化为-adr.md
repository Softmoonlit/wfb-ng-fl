Title: 把关键传输取舍固化为 ADR
Status: done

## What to build

把当前联邦学习传输架构中那些难逆转、缺少上下文会让后续读者困惑、并且确实经过权衡的关键取舍正式固化为 ADR。完成后，维护者应能清楚知道为什么当前系统选择了这些方向，以及哪些替代方案被放弃或暂缓。

## Acceptance criteria

- [ ] 至少覆盖当前最关键且难逆转的传输架构取舍，例如下行方式、上行方式、`TUN/IP` 边界、集成式 `wfb` 链路层守护进程等
- [ ] 每条 ADR 都说明背景、决策、权衡和影响，而不是只罗列结论
- [ ] glossary、设计文档和 ADR 之间职责清晰：术语、方案、决策分别落在正确位置

## Blocked by

- `.scratch/context-doc-boundaries/issues/02-补充联邦学习传输总设计文档.md`
