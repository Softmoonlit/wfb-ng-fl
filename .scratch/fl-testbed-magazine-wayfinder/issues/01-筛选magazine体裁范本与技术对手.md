# 筛选 Magazine 体裁范本与技术对手

Type: research
Status: resolved
Blocked by:

## Question

哪些真实发表于 IEEE Magazine 刊物的文章最适合作为本项目的体裁与叙事范本，哪些 journal、conference 或其他出版物中的平台/testbed 工作最适合作为技术对手？两组文献分别采用了怎样的章节结构、图表组织、贡献表达和验证方式？

## Answer

体裁范本首选 Liu 等发表于 **IEEE Network** 的 *Federated Learning on 5G Edge for Industrial Internet of Things*：其“挑战/使能技术 → 应用算法 → 真实 5G/IoT testbed → analytics → open challenges”结构与本项目最接近。**IEEE Communications Magazine** 中，Shen 等的 *Resource Rationing for Wireless Federated Learning* 最适合学习“一个中心概念统领全文、每幅图完成一个论证步骤”；Farajzadeh 等的 *Federated Learning in NTN* 适合学习复杂分层架构、角色表、流程图和数值验证的短篇组织。**IEEE Internet of Things Magazine** 的 IoUT 文章只作为场景导入/挑战收束的次级范本，不是 testbed 范本。

技术对手应组合对照：FedScale（现实 workload、统一 benchmark 与规模）、Flower（通用框架接口、异构执行和仿真到设备）、FedComm（通信协议与网络损伤的 lab testbed）、Tedeschini 等的跨国 MQTT 实机 FL（真实端到端同步/异步、中心/去中心验证）。本项目的可辩护差异不是“另一个 FL 框架”，而是可复现的真实广播无线空口、跨层轮次/故障语义，以及链路—系统—学习三层联合证据。

IEEE Xplore 的 **“Journals & Magazine”仅是页面分类**；只有 `Published in` 明确为 *IEEE Communications Magazine*、*IEEE Network* 或 *IEEE Internet of Things Magazine* 的文章进入体裁组。*IEEE Access*、*IEEE Internet of Things Journal* 和会议论文均只进入技术组。完整元数据、可核验章节、叙事弧、图表/实验角色及建议图表与实验门槛见 [`research/01-体裁范本与技术对手.md`](../research/01-体裁范本与技术对手.md)。

## Comments
