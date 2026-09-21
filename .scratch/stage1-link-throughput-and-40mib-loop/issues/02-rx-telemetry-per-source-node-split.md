# 02: C++ 链路底座分源遥测与 Python 解析器端到端贯通

**What to build:** 改造 C++ 链路底座（`src/rx.cpp`），使接收聚合器按数据包内嵌的 `source_node` 独立维护每个客户端的收包、FEC 恢复、丢包与交付计数，并在周期导出时输出结构化的 `\tPKT_SRC\t` 行；升级 Python 遥测解析器提取分源指标，在真实硬件上部署验证丢包精准归因能力。

**Blocked by:** 01: 真实硬件新拓扑 40 MiB 数据面基准摸底探路

**Status:** resolved

- [x] C++ 链路底座保留全局 `PKT` 行的同时，新增结构化 `PKT_SRC` 统计行（覆盖 source_node、raw、bytes、fec_recovered、lost、out_packets、out_bytes）。
- [x] 编写 Catch2 单元测试验证分源聚合器在不同源节点交替到达和丢包时的计数准确性与输出格式。
- [x] `issue41_gate.py` 中的 `parse_telemetry` 解析 `PKT_SRC`，输出结构化 `loss_and_fec_by_node` 指标字典。
- [x] 编写 Python 单元测试验证分源指标解析及异常格式处理。
- [x] 在真实物理硬件上部署并验证分源遥测，能够清晰展示 Client1 与 Client2 各自的丢包率与 FEC 恢复率。
- [x] 满足 GitHub Issue #60 遥测拆分验收条件（实现上行丢包与 FEC 按发射节点精准归因）。

## Comments

### 2026-09-21 C++ 链路底座分源遥测与三机真实硬件验证报告

#### 1. 架构与实现概要

1. **C++ 链路底座 (`src/rx.hpp`, `src/rx.cpp`)**：
   - 在 `rx_source_state_t` 中新增 `RxSourceStats` 独立统计结构，分别跟踪每个源节点的收包数 (`count_p_raw`)、收字节数 (`count_b_raw`)、FEC 恢复包数 (`count_p_fec_recovered`)、丢包数 (`count_p_lost`)、交付包数 (`count_p_outgoing`) 和交付字节数 (`count_b_outgoing`)。
   - 在重组环形缓冲队列处理、单包 override 以及 FEC 解码重构交付点严格同步更新对应源节点的统计，保证分源统计与全局 `PKT` 指标严格自洽。
   - 在 `dump_stats()` 周期导出时，保持原有 `\tPKT\t` 全局行输出不变，并针对所有处于激活状态且属于合法源节点（1..255）的源输出结构化的 `\tPKT_SRC\t` 行：
     `%lld\tPKT_SRC\t%u:%u:%u:%u:%u:%u:%u\n`（格式：`timestamp_ms \t PKT_SRC \t node_id:raw_pkts:raw_bytes:fec_recovered:lost:out_pkts:out_bytes`）。
   - 保留全局 `PKT` 保证向后兼容性；不添加任何兼容回退或多余适配层。

2. **C++ 单元测试 (`src/rx_source_telemetry_test.cpp`, `Makefile`)**：
   - 编写 Catch2 单元测试套件 `src/rx_source_telemetry_test.cpp`，接入 Makefile 编译目标 `rx_source_telemetry_test`，并在 `V6_DEFAULT_TESTS` 中纳入构建。
   - 验证了多源节点交替到达、丢包推进、FEC 恢复以及状态生命周期管理下分源计数的绝对准确性。

3. **Python 遥测解析与门禁契约 (`tests/real_hardware/issue41_gate.py`, `tests/real_hardware/test_issue41_gate.py`)**：
   - 实现了 `parse_pkt_src_line` 与 `format_node_telemetry_lines`，具备严格的行首/行尾正则锚定与节点 ID 范围校验（1..255）。
   - `parse_telemetry` 在输出中提取 `loss_and_fec_by_node`，计算各客户端各自的丢包率与 FEC 恢复率。
   - 在 `validate_cycle_evidence` 中强制校验：必须同时包含 Client1 与 Client2 的有效分源采样，且交付包数 `out_packets` 必须大于 0，防止空样本或未归因传输混过门禁。
   - 新增针对合法格式、越界 ID、多节点汇总与异常格式的 Python 单元测试，全量 253 个测试全部通过。

4. **正式端到端串联脚本 (`tests/real_hardware/issue41_fl_runtime_loop.sh`)**：
   - 支持实时打印和汇总各周期的分源丢包与 FEC 恢复遥测。
   - 日志行数记录与切片均采用 fail-closed 设计，无隐式容错回退。

#### 2. 三机真实硬件验证实测结果

在真实物理集群（Server: vm0 USB 2.0, Client1: vm1 USB 3.0, Client2: vm2 USB 2.0）上执行完整端到端闭环 `run-all`：
- **正式归档 Run ID**：`v8_issue41_20260921_231116`
- **归档路径**：`tests/logs/v8_issue41_20260921_231116/`
- **归档校验**：`issue41_validate_archive.py` 严格校验 100% 通过（5个分区全部 passed）。

**数据面 Gate 连续 3 周期实测分源遥测数据**：
- **Cycle 1 (13.99s)**:
  - Client 1: raw=1910 pkts, lost=57 (4.93%), fec_recovered=227 (19.64%), out=1099 pkts
  - Client 2: raw=2104 pkts, lost=51 (4.45%), fec_recovered=248 (21.64%), out=1095 pkts
- **Cycle 2 (14.10s)**:
  - Client 1: raw=2101 pkts, lost=56 (4.89%), fec_recovered=236 (20.61%), out=1089 pkts
  - Client 2: raw=1750 pkts, lost=43 (3.80%), fec_recovered=210 (18.55%), out=1089 pkts
- **Cycle 3 (12.56s)**:
  - Client 1: raw=1806 pkts, lost=34 (3.03%), fec_recovered=232 (20.66%), out=1089 pkts
  - Client 2: raw=1659 pkts, lost=37 (3.28%), fec_recovered=232 (20.59%), out=1090 pkts

**实测结论**：
首次成功将真实物理集群的上行链路丢包和 FEC 恢复率在空口按 Client1 与 Client2 进行精确归因。双方交付包数均精准达到约 1090 包（对应 4 MiB 数据载荷），FEC 成功恢复了约 17%~21% 的受损包，残留丢包率稳定在 3%~5% 区间，满足 GitHub Issue #60 遥测拆分验收标准，为后续 Ticket 03 的射频调优提供了不可或缺的可观测性底座。
