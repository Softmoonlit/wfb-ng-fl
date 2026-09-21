# 05: 三机真实硬件双 40 MiB 两轮并发正式验收与归档

**What to build:** 在三台真实物理机上执行升级后的端到端两轮双 40 MiB FL Runtime 闭环验收脚本，生成不可覆盖的正式运行归档，经归档验证器判定全 passed，通过服务生命周期审计，正式结算关联 GitHub 工单。

**Blocked by:** 04: 验收套件升级为场景驱动的两轮并发架构

**Status:** ready-for-agent

- [ ] 三台物理机运行相同 commit，工作区干净，网卡配置正常。
- [ ] 执行完整的端到端两轮双 40 MiB 闭环验收流程。
- [ ] 生成不可覆盖的独立运行归档，各分区（orchestration、pre_runtime_smoke、formal_runtime_loop、lifecycle、conclusion）全部 passed。
- [ ] 自动化归档校验器对归档执行全量校验，判定结果为 passed。
- [ ] 角色服务生命周期审计（stop、restart、TUN 回收、无残留孤儿进程）通过。
- [ ] 满足 GitHub Issue #54 验收条件，正式关闭 Issue #54 并关闭总 Epic Issue #50。
