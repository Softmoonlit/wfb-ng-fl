# 6 节点 40 MiB 大载荷射频调优与上行调度科学固化规格说明书

Status: ready-for-agent

## Problem Statement

在真实硬件环境下（1 台 Server + 7 台 Client）推进 Stage 2 40 MiB 双向 FL 传输验收时，系统遇到了硬件物理层、网络协议栈与调度维度的综合性阻碍：
1. **近距离桌面射频过载与配置透明性缺失**：8 张无线网卡由于实验室测试条件限制，物理距离均在 1 米内的同一台面上且无法拉远。若使用默认 20 dBm（100 mW）发射功率，接收端天线信号强度过高（高于 $-15\,\text{dBm}$），直接打爆低噪声放大器（LNA）进入深度饱和，高阶调制产生严重非线性失真，导致大载荷提速受阻。同时，系统必须遵循透明原则，不能私自黑盒调节发射功率，而需由用户根据物理距离自主配置；
2. **多播下行 NAK 风暴风险**：在 1 对多的大文件组播传输中，若 Server 盲目提高下行速率，一旦偶发丢包超出纠错能力，多个客户端将并发反向发射 NAK 组播包，打满反向空口并撑爆 Server 接收 TUN 队列，形成恶性重传雪崩；
3. **扩展坞总线共享竞争与拓扑收敛**：单台测试主机物理接口有限，Client 5 与 Client 2 共接在同一个 USB 2.0 扩展坞上，两张高速无线网卡并发收发时争抢单条 USB 2.0 上行总线与带宽，引发总线拥塞与传输抖动，成为木桶瓶颈并导致 40 MiB 上行阶段在 120 秒边界出现排队超时风险；
4. **调度周期与 TCP 协议栈失步**：并发 HTTP PUT 上行依赖 TCP 协议。早期轮询 Grant 窗口较小或周转周期过长，导致客户端经历过长的静默冰冻期，限制了 TCP CWND 展开且极易触发 RTO 超时重传；
5. **已完成节点霸占调度席位（Early Release 滞后）**：服务端调度主循环此前未接入静默节点移出逻辑，导致率先完成 40 MiB 上传的客户端即使彻底静默，依然无休止占用 Token 席位，严重挤占后发节点的信道时间；
6. **动态时隙（AEW）的共振隐患**：早期草案曾设想根据单次发射耗时动态扩缩 Grant，但在真实 TCP 协议栈下，动态调窗会与 TCP 拥塞控制算法发生破坏性的“双重负反馈恶性共振”，导致信道吞吐雪崩。

## Solution

1. **用户可配置的距离导向射频功率策略**：
   - 系统提供标准“物理距离到推荐发射功率”映射规范，由用户在 CLI / Web 界面根据实际摆放距离自主配置；
   - 针对当前网卡间距小于 0.5 米的超近距密集台面环境，推荐配置 **10 ~ 13 dBm（当前现场基准锁定 12 dBm）**，将接收端 RSSI 控制在 $-22 \sim -28\,\text{dBm}$ 黄金线性解调区间，彻底消除 LNA 削顶失真，为全网提速扫清硬件障碍；
2. **三层防御屏障根治组播 NAK 风暴**：
   - **底层纠错**：严格固化 FEC 8/14（75% 冗余），在链路层直接自愈吸收 20% 以内的空口突发丢包，应用层 UFTP 感知不到缺块，从源头上杜绝 NAK 触发；
   - **速率区间契约**：遵守 ADR-0011 规范，将 MCS 3 下行速率安全锁定在推荐中值 15000 Kbps，杜绝超额注入导致内核 TUN 缓冲静默丢包；
   - **协议自适应退避**：调优 UFTP 原生 NAK 抑制与随机退避参数（`-r 0.1:0.01:2.0`），确保偶发缺块时客户端错峰上报，先发节点的 NAK 被其他客户端监听到后自动取消重复 NAK；
3. **收敛至 6 客户端拓扑**：拔除与 Client 2 共用同一个 USB 2.0 扩展坞的 Client 5 网卡，消除单控制器多网卡总线竞争，并利用测试框架原生的 `ISSUE41_CLIENT_ROLES` 环境变量，将拓扑收敛为 **1 Server + 6 Clients**（总上行 240 MiB）；
4. **角色分离异构 MCS 升级与全节点高阶调制跃升**：
   - 基于 IEEE 802.11 物理层由 Preamble 与 HT-SIG 引导的逐包自适应解调机制，全网节点独立运行不同 MCS；
   - Server 下行组播升级至 **MCS 3 (15000 Kbps)**，单次 40 MiB 下行耗时稳定在 28~30 秒，6 节点 SHA-256 校验 100% 一致；
   - Client 上行全面升级至 **MCS 6 (64-QAM 3/4，135 Mbps)**，净吞吐带宽跃升至约 42 Mbps，物理丢包率维持在 1.1%~2.5% 极低区间，TCP 重传率为 0；
5. **正交调度对照实验与终极基准固化**：
   - 经三组正交基准矩阵（200/15、220/12、180/15）与高阶细分探针（150/10、120/10、118/12、120/12）实测筛选，敲定双轨调度基准：
     - **主线默认方案（方案 1 优先）**：**Grant 120 ms / Guard 10 ms**。240 MiB 上行并发总耗时仅 **84.18 秒**，最慢节点仅 **82.48 秒**（安全裕量高达 37.5 秒），长尾极差压缩至 **14.56 秒**，物理丢包率仅 **1.17%**，Guard 开销严格控制在 **7.69%** 黄金比率；
     - **高抗抖备选方案（方案 2 备选）**：**Grant 118 ms / Guard 12 ms**。长尾极差进一步降至 **12.44 秒** 历史最低，显式拥有 12 ms 物理强静默保护，总耗时 87.31 秒稳居极速档；
6. **服务端主循环静默席位释放激活**：
   - 在服务端主调度循环中启用 `collect_silent_node_removals`，客户端完成 40 MiB 上传后在连续 2 次无数据 Grant 后自动移出活跃队列，释放空口时间给剩余落后节点，彻底消除后发节点超时风险；
7. **坚决否定动态时隙，确立“固定时隙 + 静默移出 + 自然错峰”并发模型**：
   - 彻底废弃早期草案中的三态状态机动态时隙（AEW），杜绝底层与 TCP 拥塞控制产生恶性负反馈共振；
   - 依靠联邦学习天然错峰特性与 120 ms 固定时隙提供确定性信道，经理论与实测证明完全能平滑支持未来 10 节点规模（周转周期仅 1.30 秒，空等 1.17 秒，远离 TCP RTO 警戒线）。

## User Stories

1. As an operator, I want to configure wireless transmit power via CLI/Web based on a physical distance guide, so that I can prevent receiver LNA saturation in desktop testing without hidden system alterations.
2. As an operator, I want the system to control 6 clients (`client1`, `client2`, `client3`, `client4`, `client6`, `client7`) via `ISSUE41_CLIENT_ROLES`, so that I can eliminate defective hardware nodes cleanly.
3. As an engineer, I want all active nodes to execute with identical binaries and configurations verified by the envelope hash, so that test validity is guaranteed.
4. As an FL algorithm engineer, I want server multicast downlink time for 40 MiB models to drop to under 30 seconds under MCS 3 (15000 Kbps), so that airtime is preserved for uplink transfers.
5. As a network engineer, I want multicast NAK storms to be suppressed by FEC 8/14 recovery and UFTP exponential backoff, so that server receive buffers never overflow during downlink.
6. As a wireless transport engineer, I want client uplinks to operate at MCS 6 (64-QAM 3/4), so that net physical goodput reaches ~42 Mbps and 240 MiB transfers complete in ~84 seconds.
7. As a transport architect, I want the system to default to Grant 120 ms / Guard 10 ms, so that 6-client round-robin latency is 780 ms and 10-client round-robin latency is strictly capped at 1.30 seconds.
8. As a transport architect, I want an optional high-stability profile of Grant 118 ms / Guard 12 ms, so that jitter-sensitive environments achieve a 12.44-second tail-latency spread.
9. As a transport architect, I want the system to strictly reject dynamic elastic window (AEW) algorithms, so that TCP congestion window growth is never disrupted by oscillating slot sizes.
10. As a distributed system engineer, I want completed clients to be automatically pruned from the active scheduling ring after 2 consecutive silent grants, so that tail clients receive 100% of remaining airtime.
11. As a QA engineer, I want zero air-collision overlaps and zero TCP retransmissions across all formal FL runtime rounds, so that collision-free token passing is verified.
12. As a QA engineer, I want the archive validator to audit all 5 partitions (orchestration, pre_runtime_smoke, formal_runtime_loop, lifecycle, conclusion) across 2 full 40 MiB rounds, so that formal acceptance succeeds.
13. As a system architect, I want the winning Grant/Guard parameters (120 ms / 10 ms) permanently frozen in codebase defaults and ADR-0013 accepted, so that future development has an authoritative baseline.
14. As a developer, I want all clients to complete their 40 MiB HTTP PUT within the 120-second single I/O timeout boundary, with total round duration far below 400 seconds, so that formal acceptance invariants are strictly upheld.
15. As an operator, I want test cleanup to reliably shut down background processes and return all wireless interfaces to `managed/DOWN`, so that subsequent test runs are clean and leak-free.

## Implementation Decisions

- **Topological Invariant**:
  - The cluster topology is dynamically defined via environment variable `ISSUE41_CLIENT_ROLES="client1 client2 client3 client4 client6 client7"`.
- **User-Configured RF Power Invariant**:
  - Transmit power is set by the operator via `ISSUE41_RADIO_TXPOWER_DBM=12` matching the desktop near-field recommendation of 12 dBm for ultra-close (< 0.5m) deployments.
- **RF Modulation Defaults**:
  - Server downlink broadcast: MCS 3 (16-QAM) matching UFTP rate 15000 Kbps.
  - Client uplink transmission: MCS 6 (64-QAM 3/4) with Short GI enabled, yielding 135 Mbps PHY rate.
  - Multicast NAK protection is anchored by mandatory FEC 8/14 redundancy and UFTP backoff `-r 0.1:0.01:2.0`.
- **Scheduling Defaults & Invariants**:
  - Primary default configuration: `GRANT_DURATION_MS = 120`, `GUARD_INTERVAL_MS = 10`.
  - Reserve profile: `GRANT_DURATION_MS = 118`, `GUARD_INTERVAL_MS = 12`.
  - Rejection of dynamic timeslot adaptation (AEW) to prevent TCP CWND phase oscillation.
- **Silent Node Removal (Early Release)**:
  - Enabled via `collect_silent_node_removals` in the server's main scheduling loop (`src/v6_uplink.cpp`), pruning nodes after 2 consecutive silent grants with zero protocol packet overhead.
- **Strict Single-IO and Round Boundaries**:
  - Single client HTTP PUT I/O timeout strictly enforced at `<= 120` seconds.
  - Formal 2-round FL runtime loop total deadline strictly enforced at `<= 400` seconds.

## Testing Decisions

- **High-Level Seam Selection**:
  - All tests execute through the canonical entry point: `tests/real_hardware/issue41_fl_runtime_loop.sh` using subcommands `preflight`, `verify-config-equivalence`, `smoke-gate`, and `run-all`.
  - Archive outputs are verified against `tests/real_hardware/issue41_validate_archive.py`.
- **External Behavior Verification**:
  - Success is evaluated exclusively via external observables: HTTP 201 responses from the server, SHA-256 digest match between templates and committed server artifacts, telemetry loss and recovery metrics in `wfb.log`, and client PUT interval overlap duration.
- **Formal 2-Round 40 MiB Acceptance**:
  - `run-all` executes 2 consecutive rounds of 40 MiB FL cycles on the 6-client cluster.
  - Verification requires all 5 partitions (`orchestration`, `pre_runtime_smoke`, `formal_runtime_loop`, `lifecycle`, `conclusion`) to be strictly marked `passed`.
- **Regression Suite**:
  - All existing unit tests in `tests/real_hardware/test_*.py` must pass with 100% pass rate.

## Out of Scope

- Modifying the C++ token control protocol envelope or creating new control packet types.
- Refactoring the Python FL Core Engine or Client Daemon architecture (Stage 2 Ticket 01+ scope).
- Upgrading physical USB 2.0 hubs on the desktop test machine to USB 3.0.
- In-flight dynamic rate adaptation or multi-channel frequency hopping during active rounds.

## Further Notes

- Once formal 2-round 40 MiB acceptance passes under `run-all`, Ticket 04 will be completed and Stage 2 front-end RF and scheduling tuning will be formally sealed.
- All benchmark metrics from the three orthogonal matrix runs and the four probe runs are permanently recorded in ADR-0013.
