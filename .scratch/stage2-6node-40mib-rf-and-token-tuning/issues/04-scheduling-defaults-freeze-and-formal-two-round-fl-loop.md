# 04: 调度参数封板固化与 6 节点 40 MiB 两轮 FL 闭环正式验收

**What to build:**
依据对照评测数据最终封板调度参数并达成 Stage 2 前置验收。将数据表现最优的 Grant/Guard 比例永久固化为系统默认配置与 ADR-0013 封板结论；在 6 节点集群上执行 40 MiB 两轮正式联邦学习运行时闭环（`issue41_fl_runtime_loop.sh run-all`）；由 `issue41_validate_archive.py` 严格校验归档中的全部 5 个分区，确保单次 I/O 不超过 120 秒、整轮运行不超过 400 秒、生命周期无泄漏，达成 Stage 2 的前置验收闭环目标。

**Blocked by:** 03: 6 客户端上行异构 MCS 覆盖与调度参数三组正交对照实测

**Status:** ready-for-agent

- [ ] 根据评测优胜结果，更新代码与脚本中的默认 Grant Duration 与 Guard Interval 参数
- [ ] 将优胜参数写入 `docs/adr/0013-近距台面射频衰减保护与六客户端上行调度基准.md` 并标记为 accepted
- [ ] 执行完整的两轮 40 MiB 正式 FL Runtime 闭环（`run-all`）
- [ ] 验证 6 客户端各轮次 HTTP PUT 区间自然并发重叠且全部成功提交
- [ ] 运行 `issue41_validate_archive.py` 进行机械化校验，确认 5 个分区（orchestration, pre_runtime_smoke, formal_runtime_loop, lifecycle, conclusion）全部 passed
- [ ] 验证整轮耗时远低于 400 秒，单次 I/O 远低于 120 秒，完成可审计的 formal 归档交付
