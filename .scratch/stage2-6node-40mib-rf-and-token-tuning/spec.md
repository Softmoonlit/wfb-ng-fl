# 6 节点 40 MiB 大载荷射频调优与上行调度科学固化规格说明书

Status: ready-for-agent

## Problem Statement

在真实硬件环境下（1 台 Server + 7 台 Client）推进 Stage 2 40 MiB 双向 FL 传输验收时，系统遇到了硬件物理层、网络协议栈与调度维度的综合性阻碍：
1. **近距离桌面射频过载与配置透明性缺失**：8 张无线网卡由于实验室测试条件限制，物理距离均在 1 米内的同一台面上且无法拉远。若使用默认 20 dBm（100 mW）发射功率，接收端天线信号强度过高（高于 $-15\,\text{dBm}$），直接打爆低噪声放大器（LNA）进入深度饱和，高阶调制（MCS 3 的 16-QAM、MCS 4 的 64-QAM）产生严重非线性失真，导致下行大载荷提速失败，被迫局限于 MCS 2（10 Mbps，单次 40 MiB 耗时高达 33.5 秒）。同时，系统必须遵循透明原则，不能私自黑盒调节发射功率，而需由用户根据物理距离自主配置；
2. **多播下行 NAK 风暴风险**：在 1 对多的大文件组播传输中，若 Server 盲目提高下行速率，一旦偶发丢包超出纠错能力，多个客户端将并发反向发射 NAK 组播包，打满反向空口并撑爆 Server 接收 TUN 队列，形成恶性重传雪崩；
3. **异构总线与硬件物理短板拖尾**：单台测试主机接口有限，部分网卡不得不插在 USB 2.0 扩展坞上。实测证明 Client 5 存在严重的射频遥测偏低与供电纹波波动，在 7 节点轮转时成为木桶最短板，导致 40 MiB 上行阶段在 120 秒边界被频繁截断；
4. **调度周期与 TCP 协议栈失步**：并发 HTTP PUT 上行依赖 TCP 协议。现有轮询 Grant 窗口较小（140 ms），且 7 节点轮转周期长达 1.05 秒，导致客户端经历接近 1 秒的静默冰冻期，限制了 TCP CWND 展开且极易触发 RTO 超时重传；
5. **已完成节点霸占调度席位（Early Release 滞后）**：客户端完成 40 MiB 上传后，底层 TCP 协议栈仍会回复少量挥手 ACK 或维护包，导致 Server 调度器的静默计数被清零，已完成传输的节点继续霸占调度席位，信道出现无谓空转。

## Solution

1. **用户可配置的距离导向射频功率策略**：
   - 系统提供标准“物理距离到推荐发射功率”映射规范，由用户在 CLI / Web 界面根据实际摆放距离自主配置；
   - 针对当前网卡间距小于 0.5 米的超近距密集台面环境，推荐配置 **10 ~ 13 dBm（当前现场基准锁定 12 dBm）**，将接收端 RSSI 控制在 $-22 \sim -28\,\text{dBm}$ 黄金线性解调区间，彻底消除 LNA 削顶失真，为下行切换至 MCS 3（15000 Kbps）扫清硬件障碍；
2. **三层防御屏障根治组播 NAK 风暴**：
   - **底层纠错**：严格固化 FEC 8/14（75% 冗余），在链路层直接自愈吸收 20% 以内的空口突发丢包，应用层 UFTP 感知不到缺块，从源头上杜绝 NAK 触发；
   - **速率区间契约**：遵守 ADR-0011 规范，将 MCS 3 下行速率安全锁定在推荐中值 15000 Kbps，杜绝超额注入导致内核 TUN 缓冲静默丢包；
   - **协议自适应退避**：调优 UFTP 原生 NAK 抑制与随机退避参数（`-r 0.1:0.01:2.0`），确保偶发缺块时客户端错峰上报，先发节点的 NAK 被其他客户端监听到后自动取消重复 NAK；
3. **收敛至 6 客户端拓扑**：利用测试框架原生的 `ISSUE41_CLIENT_ROLES` 环境变量，排除存在硬件/供电物理短板的 Client 5，将拓扑收敛为 **1 Server + 6 Clients**（总上行 240 MiB），释放系统容量裕量至 30% 以上；
4. **角色分离异构 MCS 覆盖与逐包自适应**：
   - 基于 IEEE 802.11 物理层由 Preamble 与 HT-SIG 引导的逐包自适应解调机制，全网节点独立运行不同 MCS；
   - Server 下行升级至 **MCS 3 (15000 Kbps)**，单次下行耗时由 33.5 秒缩减至 22 秒；
   - 默认客户端配置 **MCS 4** 保障全局吞吐，针对 USB 2.0 弱供电的 Client 1 和 Client 2 覆盖配置为 **MCS 3**，压制供电纹波引起的高阶误码；
5. **两组科学对照实验确立最终固化调度参数**：
   - 以 **基准候选 (Grant 200 ms / Guard 15 ms)** 为锚点（单轮周转 1290 ms，空口占空比 93.0%）；
   - **对照组 1 (吞吐优先组 / Throughput-Optimized)**：`Grant 220 ms / Guard 12 ms`（周转时延 1392 ms，空口占空比 94.8%），检验单次超大突发对拉满 TCP 吞吐的收益；
   - **对照组 2 (稳健防抖组 / Latency-Optimized)**：`Grant 180 ms / Guard 15 ms`（周转时延 1170 ms，静默期低于 1 秒消除 TCP RTO 抖动，空口占空比 92.3%），检验低周转时延对消除长尾的收益；
   - 实测对比空口碰撞率、TCP 重传率、长尾方差与总耗时，数据优胜者作为最终固化比例封板写入代码与 ADR-0013；
6. **已完成节点活跃队列修剪（Early Release 机制）的阶段决策**：
   - 遵循“满足当前需求的最简实现”原则，鉴于 6 节点（240 MiB）拓扑下系统容量裕量充裕，现阶段**暂不修改 C++ 协议帧格式**，优先通过调度参数对照与 MCS 覆盖直接通关；
   - 明确演进触发条件：若对照组实测表明率先完成的节点因 TCP ACK 维持导致调度器空转、后发节点仍无法在 120 秒内收敛，则触发最小改造（优化 `kSilentGrantRemovalThreshold` 或由应用层 HTTP 201 触发撤销就绪）。

## User Stories

1. As an operator, I want to configure wireless transmit power via CLI/Web based on a physical distance guide, so that I can prevent receiver LNA saturation in desktop testing without hidden system alterations.
2. As an operator, I want the system to control 6 clients (`client1`, `client2`, `client3`, `client4`, `client6`, `client7`) via `ISSUE41_CLIENT_ROLES`, so that I can eliminate defective hardware nodes cleanly.
3. As an engineer, I want all active nodes to be verified against commit `90be631` with identical `wfb_v6_uplink` binaries, so that test validity is guaranteed.
4. As an FL algorithm engineer, I want server multicast downlink time for 40 MiB models to drop from 33.5 seconds to approximately 22 seconds under MCS 3 (15000 Kbps), so that airtime is preserved for uplink transfers.
5. As a network engineer, I want multicast NAK storms to be suppressed by FEC 8/14 recovery and UFTP exponential backoff, so that server receive buffers never overflow during downlink.
6. As a wireless transport engineer, I want Client 1 and Client 2 to be assigned MCS 3 while other clients use MCS 4, so that USB 2.0 power voltage drops do not trigger high-order modulation decoding failures.
7. As a transport architect, I want to evaluate Candidate Baseline (Grant 200 ms / Guard 15 ms) on 6 clients uploading 240 MiB, so that I have a reliable reference point for round-robin latency.
8. As a transport architect, I want to evaluate Control Group 1 (Grant 220 ms / Guard 12 ms) on 6 clients, so that I can test whether maximizing single-grant TCP burst size improves goodput.
9. As a transport architect, I want to evaluate Control Group 2 (Grant 180 ms / Guard 15 ms) on 6 clients, so that I can test whether sub-second client wait intervals eliminate TCP RTO drops and tail latency.
10. As a QA engineer, I want zero air-collision overlaps between adjacent client grants across all experimental groups, so that collision-free token passing is verified.
11. As a QA engineer, I want the archive validator to audit per-client loss, FEC recovery, and committed artifacts across the 6 nodes without requiring Client 5 data, so that formal archive generation succeeds.
12. As a system architect, I want the winning Grant/Guard parameter pair to be permanently frozen in codebase defaults and ADR-0013, so that future developers have a validated, permanent scheduling baseline.
13. As a developer, I want all clients to complete their 40 MiB HTTP PUT within the 120-second single I/O timeout boundary, so that formal acceptance invariants are strictly upheld.
14. As an operator, I want test cleanup to reliably shut down background processes and return all `wlx*` wireless interfaces to `managed/DOWN`, so that subsequent test runs are clean.

## Implementation Decisions

- **Topological Invariant**:
  - The cluster topology is dynamically defined via environment variable `ISSUE41_CLIENT_ROLES="client1 client2 client3 client4 client6 client7"`.
- **User-Configured RF Power Invariant**:
  - Transmit power is not modified autonomously by the system; it is set by the operator via `ISSUE41_RADIO_TXPOWER_DBM=12` (or CLI/Web equivalent) matching the desktop near-field recommendation of 12 dBm for ultra-close (< 0.5m) deployments.
- **Role-Separated MCS & NAK Defense**:
  - Server downlink configured to MCS 3 with UFTP rate 15000 Kbps.
  - Client default MCS is MCS 4; Client 1 and Client 2 override to MCS 3.
  - Multicast NAK protection is anchored by mandatory FEC 8/14 redundancy and UFTP backoff `-r 0.1:0.01:2.0`.
- **Scheduling Comparative Matrix**:
  - Three distinct pairs are evaluated under identical payload (240 MiB total, 40 MiB/client):
    1. Baseline: `grant_duration_ms=200`, `guard_interval_ms=15`
    2. Group 1 (Throughput): `grant_duration_ms=220`, `guard_interval_ms=12`
    3. Group 2 (Latency/Anti-Jitter): `grant_duration_ms=180`, `guard_interval_ms=15`
  - Scoring criteria: Collision count = 0, TCP retransmission < 2%, minimum total uplink duration, minimum gap between fastest and slowest client.
- **Early Release Stage Decision**:
  - No C++ protocol packet changes in this phase. If post-completion TCP ACKs keep completed clients active and delay remaining nodes beyond 120 seconds, minimal socket-level removal logic will be triggered as a follow-up.
- **Strict Single-IO Boundary**:
  - `io_timeout_seconds <= 120` and `runtime_timeout_seconds <= 400` remain strictly enforced.

## Testing Decisions

- **High-Level Seam Selection**:
  - All tests execute through the existing canonical entry point: `tests/real_hardware/issue41_fl_runtime_loop.sh` using subcommands `preflight`, `verify-config-equivalence`, `smoke-gate`, and `run-all`.
  - Archive outputs are verified against `tests/real_hardware/issue41_validate_archive.py`.
- **External Behavior Verification**:
  - Success is evaluated exclusively via external observables: HTTP 201 responses from the server, SHA-256 digest match between templates and committed server artifacts, telemetry loss and recovery metrics in `wfb.log`, and client PUT interval overlap duration.
- **Unit and Script Regression**:
  - All existing unit tests in `tests/real_hardware/test_*.py` must pass with 100% pass rate.

## Out of Scope

- Modifying the C++ token control protocol envelope or creating new control packet types unless comparative tuning fails to clear the 120-second threshold.
- Refactoring the Python FL Core Engine or Client Daemon architecture (Stage 2 Ticket 01+ scope).
- Upgrading physical USB 2.0 hubs on the desktop test machine to USB 3.0 (deferred to distributed board deployment).
- Dynamic runtime rate adaptation or in-flight channel switching.

## Further Notes

- Once the comparative matrix identifies the winning scheduling configuration, the defaults in `tests/real_hardware/issue41_fl_runtime_loop.sh`, `issue41_gate.py`, and `src/token_scheduler.hpp` will be permanently updated.
- Results and telemetry from each control group run will be archived into distinct run directories under `tests/logs/`.
