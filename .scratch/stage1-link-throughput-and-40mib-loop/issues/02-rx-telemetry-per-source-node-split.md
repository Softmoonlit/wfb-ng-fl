# 02: C++ 链路底座分源遥测与 Python 解析器端到端贯通

**What to build:** 改造 C++ 链路底座（`src/rx.cpp`），使接收聚合器按数据包内嵌的 `source_node` 独立维护每个客户端的收包、FEC 恢复、丢包与交付计数，并在周期导出时输出结构化的 `\tPKT_SRC\t` 行；升级 Python 遥测解析器提取分源指标，在真实硬件上部署验证丢包精准归因能力。

**Blocked by:** 01: 真实硬件新拓扑 40 MiB 数据面基准摸底探路

**Status:** ready-for-agent

- [ ] C++ 链路底座保留全局 `PKT` 行的同时，新增结构化 `PKT_SRC` 统计行（覆盖 source_node、raw、bytes、fec_recovered、lost、out_packets、out_bytes）。
- [ ] 编写 Catch2 单元测试验证分源聚合器在不同源节点交替到达和丢包时的计数准确性与输出格式。
- [ ] `issue41_gate.py` 中的 `parse_telemetry` 解析 `PKT_SRC`，输出结构化 `loss_and_fec_by_node` 指标字典。
- [ ] 编写 Python 单元测试验证分源指标解析及异常格式处理。
- [ ] 在真实物理硬件上部署并验证分源遥测，能够清晰展示 Client1 与 Client2 各自的丢包率与 FEC 恢复率。
- [ ] 满足 GitHub Issue #60 验收条件并完成对应结算。
