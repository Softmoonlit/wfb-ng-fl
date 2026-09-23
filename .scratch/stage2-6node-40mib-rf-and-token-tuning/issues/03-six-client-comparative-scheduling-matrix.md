# 03: 6 客户端上行异构 MCS 覆盖与调度参数三组正交对照实测

**What to build:**
实施 6 客户端并发上行大载荷压力测试并开展调度参数科学对照。在 6 客户端全集群环境（排除 Client 5）就绪的前提下，配置 Client 1 与 Client 2 覆盖为 MCS 3（压制 USB 2.0 供电纹波误码），其余强节点保持默认 MCS 4；严格保持总载荷 240 MiB（每客户端 40 MiB），顺序执行三组正交对照 Gate 实验并为每组保存完整、独立的运行归档：
1. 基准候选组 (Baseline): Grant 200 ms / Guard 15 ms
2. 对照组 1 (吞吐优先组): Grant 220 ms / Guard 12 ms
3. 对照组 2 (防抖优先组): Grant 180 ms / Guard 15 ms
抓取并对比各组的空口碰撞计数、TCP 重传率、各节点 HTTP PUT 完成耗时分布与长尾差距，输出详细的数据对比报告。

**Blocked by:** 02: 6 客户端拓扑收敛与单机 MCS 3 / 15000 Kbps 下行提速探路

**Status:** ready-for-agent

- [ ] 全集群 6 客户端配置角色异构 MCS 覆盖（Client 1、2 为 MCS 3，Client 3、4、6、7 为 MCS 4）
- [ ] 执行基准候选组测试（Grant 200 ms / Guard 15 ms），归档完整遥测日志与 PUT 事件
- [ ] 执行对照组 1 测试（Grant 220 ms / Guard 12 ms），归档完整遥测日志与 PUT 事件
- [ ] 执行对照组 2 测试（Grant 180 ms / Guard 15 ms），归档完整遥测日志与 PUT 事件
- [ ] 验证三组实验中所有 6 个客户端均在单次 I/O 120 秒限时内成功提交 40 MiB 并获得 HTTP 201
- [ ] 统计并输出三组对照组的核心对比报告（空口重叠碰撞数、TCP 重传表现、总上行耗时、USB 2.0 与 3.0 节点完成时间方差）
