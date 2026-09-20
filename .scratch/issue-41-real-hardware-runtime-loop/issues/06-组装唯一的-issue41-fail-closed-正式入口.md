# 06: 组装唯一的 Issue 41 fail-closed 正式入口

**What to build:** 把已经独立验证的三周期数据面 gate、正式一轮 Runtime 和 lifecycle 组装成 Issue #41 唯一正式运行行为。入口复用运行包络已经建立的 run identity、preflight、失败归档和分类机制，负责固定阶段顺序、比较 smoke 与 Runtime 的解析后配置、汇总已有分区，并让最终验证器只对同一次完整 run 给出 passed 或 failed 结论。

**Blocked by:** 03 实现可审计的连续三周期双向数据面 gate; 05 验证三角色服务 restart 与资源生命周期

**Status:** ready-for-agent

- [ ] 正式入口严格按 preflight、数据面 gate、配置等价比较、Runtime、lifecycle、收集和最终验证的顺序组合已有行为。
- [ ] gate 未通过时 Runtime 不启动；Runtime 未通过时不得报告 lifecycle 或总体验收成功。
- [ ] smoke 与 Runtime 的信道、带宽、MCS、FEC、方向链路域、TUN、队列阈值和 feedback 配置逐项相同。
- [ ] 成功运行只产生一个包含 orchestration、gate、Runtime、lifecycle 和 conclusion 分区且通过验证器的正式归档。
- [ ] 任一阶段失败都复用同一 run 的受控停止、收集和分类机制生成 failed 归档，不丢失已完成阶段的证据。
- [ ] 最终验证器拒绝不同 run ID 的证据、被覆盖的旧归档、缺失分区、运行中修改配置、diagnostic 模式和候选 feedback 行为。
- [ ] 每个 smoke 周期和正式轮次的固定整体 deadline 均进入解析配置与归档，且运行中不可修改。
- [ ] 最终差异不包含 Issue #53 场景、旧 fixture profile、终端演示、立即 feedback window 候选或任何兼容/fallback 路径。
- [ ] 现场手册与唯一正式入口、执行前置条件、阶段顺序、失败分流和通过条件一致。
- [ ] 完整合成运行、各阶段失败、配置不等价、混合 run、缺失分区和 diagnostic 模式的自动化测试通过。
