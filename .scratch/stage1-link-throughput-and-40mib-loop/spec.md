Status: ready-for-agent

# Stage 1: 底层通信与传输极限攻坚（物理链路排查、拆分遥测与双 40 MiB 并发闭环）

## Problem Statement

此前系统的三机真实硬件验收仅验证了 4 MiB 单轮小规模负载在物理链路上的基本连通性。而在三个月前围绕 40 MiB 大文件尝试时，记录到无线空口 FEC 后残余丢包高达 15%~25%、有效吞吐跌至 0.3~1 MiB/s，导致严重的传输延迟甚至超时卡死。同时，服务端的无线链路层接收遥测将所有客户端的上行数据包混在一起输出单一聚合统计，无法区分丢包究竟来自哪一台客户端网卡与发射路径。

近期三台测试机的物理硬件与射频拓扑发生了重大变更：Client1 更换为全新无线网卡并连接至 USB 3.0（5000M）高速总线；Server 与 Client1 的网卡发生对调；Client2 保持在 USB 2.0（480M）总线。在 4 MiB 单轮门禁下，系统表现平稳且未见卡死，但该小负载表现无法代表双 40 MiB 大文件与两轮并发下的真实物理承载能力。

此外，现有的三机真实硬件编排套件、数据面门禁与归档校验器均深度绑定在单轮 4 MiB 的硬编码假设上，缺少场景驱动能力，无法验证两轮连续运行、双节点 40 MiB 并发 HTTP PUT 上传、自然重叠区间检测以及严格同步聚合屏障。团队必须在不放松 120 秒单次 I/O 停滞超时底线的前提下，摸清当前新硬件的传输极限，在底层彻底实现分源遥测归因，完成射频调优，并升级端到端验收体系以正式闭环结算既有工单。

## Solution

作为“基于 WFB-NG 的无网络联邦学习仿真平台”四阶段演进规划的第一个核心阶段，本阶段充分利用已建立的集中式 SSH 自动化测试网作为最高效的脚手架，垂直攻坚物理层与传输层的大载荷并发极限。

首先，在当前新硬件拓扑下，利用现有数据面门禁机制以参数化配置直接执行单周期 40 MiB 真实文件传输探路，采集第一手空口耗时与丢包数据，破除历史推测。

其次，在链路层底座的聚合重组器中，按来源节点的逻辑身份建立独立的包数、字节数、FEC 纠错恢复数、丢失数与交付数统计，在周期性统计导出中输出结构化的分源统计事件；同时升级遥测解析层，生成按节点拆分的结构化指标，达成精准上行质量归因，结算并关闭 GitHub Issue #60。

接着，根据拆分后的精准遥测数据，按照信道扫描对比、FEC 冗余比梯次调整、MCS 档位适配以及物理硬件交叉排查的策略，将双向传输性能调优至健康窗口，确保 40 MiB 数据在 120 秒停滞超时内平稳交付。

最后，将运行包络、双向数据面门禁、端到端运行时编排和不可覆盖归档验证器全面重构为场景驱动架构，支持可配置的载荷规模、轮次计数以及并发上传重叠区间观测；在三台真实物理机上执行端到端两轮双 40 MiB 并发闭环验收，产出全 `passed` 硬件归档，结算并关闭 GitHub Issue #53、Issue #54 以及总 Epic Issue #50。

## User Stories

1. As a 链路维护者, I want to execute a single-cycle 40 MiB exploratory probe on the current hardware topology, so that I can obtain first-hand baseline throughput and loss data without guessing based on outdated hardware evidence.
2. As a 链路维护者, I want the exploratory probe to be executed via parameterized configuration of existing tools, so that no temporary redundant throwaway test scripts are introduced into the codebase.
3. As a 系统诊断者, I want the link-layer receiver aggregator to maintain independent packet counters per source node, so that telemetry can clearly distinguish traffic originating from different clients.
4. As a 系统诊断者, I want the link-layer periodic stats dump to output structured per-source metrics including raw packets, raw bytes, FEC recovered packets, lost packets, and delivered outgoing packets/bytes, so that uplink packet loss can be attributed to specific client transmit paths.
5. As a 监控解析者, I want the telemetry parser to extract structured per-node loss and recovery metrics from role service logs, so that verification tools and humans do not need to parse unstructured raw text.
6. As a 测试工程师, I want unit tests to cover the C++ per-source aggregator logic, so that regression in source-node identification and counter accumulation is prevented before deploying to physical nodes.
7. As a 测试工程师, I want unit tests to cover the Python telemetry parser against per-source log formats, so that malformed or missing fields are detected immediately.
8. As a 现场操作者, I want to view separate FEC recovery and loss percentages for Client1 and Client2, so that I can determine whether the USB 3.0 adapter outperforms the USB 2.0 adapter.
9. As a 链路维护者, I want to scan and compare alternative 5 GHz channels when packet loss is elevated, so that external radio frequency interference can be isolated and avoided.
10. As a 链路维护者, I want to tune the FEC redundancy ratio (such as 8/12 vs 8/14 vs 8/10) based on observed loss rates, so that optimal effective throughput is achieved under the current link capability.
11. As a 链路维护者, I want to evaluate MCS rates 0 through 3 on the physical link, so that modulation fits the signal-to-noise ratio of all participating test nodes.
12. As a 架构维护者, I want the single I/O progress stall timeout to remain strictly 120 seconds, so that link stalls and deadlocks are never masked by inflating timeouts.
13. As a 架构维护者, I want the overall runtime phase timeout to be strictly bounded, so that test runs fail closed if cumulative throughput is insufficient.
14. As a 验收套件维护者, I want the execution envelope to accept configurable scenario parameters for payload size and round counts, so that a single unified envelope supports both regression small-payload gates and formal large-payload acceptance.
15. As a 验收套件维护者, I want test payloads and digests to be deterministically generated before the live run, so that live execution does not hide file generation overhead inside transmission measurements.
16. As a FL 算法开发者, I want the server to publish an operator-provided or deterministically prepared 40 MiB model, so that downlink distribution represents full-scale model artifact delivery.
17. As a FL 算法开发者, I want Client1 and Client2 to use distinct 40 MiB synthetic update templates, so that the server can verify that submissions from the two clients have unique content and distinct SHA-256 digests.
18. As a FL 算法开发者, I want Client1 and Client2 to reuse their respective update templates across both rounds, so that template preparation is deterministic while round identity remains distinct.
19. As a FL 算法开发者, I want both clients to operate with zero artificial training delay, so that updates are submitted immediately following model reception and validation.
20. As a Transport Backend 维护者, I want the server to admit concurrent HTTP PUT uploads from different participating nodes without rejecting either with upload-in-progress conflicts, so that multi-client concurrent transport is exercised over the radio link.
21. As a Transport Backend 维护者, I want the execution evidence to record the start and completion timestamps of each client's HTTP PUT, so that whether the uploads naturally overlapped in time is transparently documented.
22. As a Transport Backend 维护者, I want the acceptance criteria to accept naturally occurring upload overlap without inserting artificial sleeps or delays to force artificial concurrency, so that real-world network behavior is respected.
23. As a FL Runtime 维护者, I want the server to wait for the complete set of participating updates before advancing to placeholder aggregation, so that strict synchronous round progression is preserved.
24. As a FL Runtime 维护者, I want the server to advance across two full rounds with distinct round identifiers, so that cross-round state cleanup and round re-initialization are proven on real hardware.
25. As a 运行维护者, I want the scenario to run through installed systemd role services on all three physical machines, so that production-grade service lifecycles and cgroup isolation are exercised.
26. As a 运行维护者, I want service stop, restart, and orphan process checks to run after the two-round execution, so that no leaked processes or lingering TUN interfaces remain on any test node.
27. As a 归档审计者, I want the archive validator to verify evidence across all partitions including orchestration, pre-runtime smoke, formal two-round runtime loop, and lifecycle teardown, so that no stage can be silently skipped.
28. As a 归档审计者, I want the validator to assert exact 40 MiB sizes and matching SHA-256 digests for all published models and committed updates across both rounds, so that end-to-end data integrity is proven beyond doubt.
29. As a 归档审计者, I want the validator to assert that no upload-in-progress or HTTP timeout occurred during either round, so that transmission was completely clean.
30. As a 项目维护者, I want every failed execution to generate an immutable failed archive preserving the first failing stage and raw radio logs, so that failures are debuggable without wiping historical evidence.
31. As a 项目维护者, I want the successful execution of this stage to formally resolve and close GitHub Issue #60, Issue #53, Issue #54, and Epic Issue #50, so that the GitHub tracker state accurately reflects repository delivery.

## Implementation Decisions

- **分层与演进定位**：本规格属于联邦学习仿真平台演进路径的第一阶段（Stage 1）。本阶段专注攻坚底层物理链路通信与 40 MiB 两轮并发吞吐，继续使用集中式 SSH 编排网作为测试与验证脚手架，不在本阶段引入无 SSH 仿真控制协议或常驻客户端守护进程。
- **架构最简与复用原则**：全面扩展和参数化现有的三机硬件验收工具套件，坚决不为 40 MiB 场景复制出平行的脚本集合。数据面门禁、运行包络、编排入口和归档验证器统一升级为场景驱动，原生支持传入载荷大小（如 4 MiB vs 40 MiB）、轮次数（如 1 vs 2）以及门禁判定规则。
- **单次 I/O 超时硬约束**：单次网络 I/O（包含 TUN 读写、TCP 连接、HTTP 握手及数据块传输）的停滞超时坚决锁定为 120 秒，绝不通过增大该超时阈值掩盖无线链路物理死锁或调度停滞。整轮端到端运行设定有界最大容忍时限（400 秒），超时直接触发 fail-closed 退出。
- **链路底座分源遥测协议**：
  - 链路层底座的接收聚合器内部，利用 WFB 数据帧头部内嵌的 `source_node` 字段对接收上下文进行分流；
  - 为每个已知客户端节点独立维护原始收包计数、原始收包字节、FEC 纠错重组包计数、残余丢包计数以及成功向 TUN 交付的包数与字节数；
  - 周期性状态导出时，保留原有面向全链路吞吐观测的聚合事件输出，同时新增按节点细化的分源统计事件输出；
  - 分源统计输出采用紧凑机器可解析的格式，标明时间戳、源节点编号以及五项关键计数。
- **遥测解析与归档契约**：
  - 遥测解析模块升级为同时解析全局链路指标与按节点拆分的指标字典；
  - 归档摘要结构中，在保留原有全链路丢包统计的同时，增设按客户端源节点索引的丢包率、FEC 恢复率与吞吐指标；
  - 归档验证器在解析到分源统计时，严格校验每个参与客户端的上行数据完整性与丢包指标，不允许任何一端的上行数据被伪造或缺失。
- **射频参数调优策略**：
  - 调优遵循梯次推进原则：第一优先级为信道扫描，评估 5 GHz 频段（如 157、149、161）以避开共存干扰；第二优先级为 FEC 冗余比例调整（在 8/12 默认基础上，对比 8/14 增强纠错与 8/10 提升净荷率）；第三优先级为 MCS 速率遍历（MCS 0 至 MCS 3）；
  - 任何调优后的配置必须以确定性参数写入正式运行配置，禁止在验收执行期间动态漂移。
- **自然并发与无休眠保证**：
  - 两台客户端在接收并校验模型完成后，以零人为训练延迟立即发起 HTTP PUT 上传；
  - 验收工具记录两个客户端上传请求的起止时间戳与重叠状态；
  - 若空口调度与处理时序自然产生时间重叠，验证服务端并发保持能力；若因某节点传输极快而未产生时间重叠，如实记录实际区间，严禁通过强制插入人为休眠（sleep）伪造重叠。
- **两轮严格同步与占位语义**：
  - 正式验收场景固定为两个连续轮次，每轮下发 40 MiB 模型并收齐两个节点各 40 MiB 的 update；
  - 严格保持占位训练与占位聚合语义，明确声明不执行真实梯度计算或 FedAvg 算法，客户端通过安全复制预置模板生成待提交 update，服务端通过复制当前模型推进下一轮；
  - 服务端在每轮中必须且只能在完整收齐当前轮次冻结参与集合 `[1, 2]` 的全部 update 后方可完成收齐，任一节点的单一提交不得触发过早推进。
- **工单结算映射**：
  - 探路实测与 C++ 分源统计交付并通过后，对应结算 GitHub Issue #60；
  - 场景驱动套件重构交付后，对应结算 GitHub Issue #53；
  - 端到端真实硬件验收通过并产出验证合规归档后，对应结算 GitHub Issue #54，并正式关闭总 Epic GitHub Issue #50。

## Testing Decisions

- **测试质量原则**：所有测试均严格测试外部行为、外部接口状态与协议交付物，禁止针对私有内部变量或私有锁结构施加脆弱断言。
- **测试缝隙（Testing Seams）设计**：
  1. **单元与解析缝隙（Unit Seam）**：
     - C++ 链路层：通过 Catch2 单元测试框架，构造不同 `source_node` 的数据包序列与丢包场景，断言聚合器正确统计每个源节点的丢包与恢复数，并验证格式化输出的一致性；
     - Python 遥测与校验：针对分源统计日志样例进行单元测试，验证解析器正确提取指标字典，并验证归档校验器在字段缺失或失真时坚决 fail-closed。
  2. **双向数据面门禁缝隙（Data Plane Gate Seam）**：
     - 在拉起正式 FL Runtime 之前，使用真实网卡在物理空口上连续执行参数化的数据面双向传输门禁，验证 shared UFTP 下行组播与双路 HTTP PUT 并发上行在 40 MiB 载荷下的连通性与吞吐底线。
  3. **端到端角色服务闭环缝隙（E2E Runtime Seam）**：
     - 通过集中式编排脚本在三台物理机上通过 systemd 启动正式角色服务，运行两轮双 40 MiB 完整业务闭环；
     - 运行结束后执行服务生命周期审计（stop、restart、无孤儿进程、TUN 资源回收）；
     - 由不可覆盖归档验证器对归档目录内的五个完整分区执行机械化全量校验，判定最终 passed 结论。
- **参考既有测试（Prior Art）**：
  - C++ 源码中的 `src/control_envelope_test.cpp` 与 `src/rx_token_listener_test.cpp`；
  - 运行包络与门禁测试 `tests/real_hardware/test_issue41_gate.py`、`test_issue41_build_summary.py` 和 `test_issue41_validate_archive.py`；
  - Issue #41 成功通过的历史真实硬件归档 `tests/logs/v8_issue41_20260921_145721/`。

## Out of Scope

- 不在本阶段实现面向无网络环境的 Client 自启常驻守护进程（Daemon Agent）与失联防锁死信道回退（归属 Stage 2）。
- 不在本阶段实现脱离 SSH 的中心协同引擎与基于 TUN 的应用层仿真控制信令（归属 Stage 2）。
- 不在本阶段执行拔除管理以太网网线的纯无网黑盒验收（归属 Stage 3）。
- 不在本阶段开发 CLI 命令行工具或 Web 浏览器控制面板（归属 Stage 4）。
- 不执行真实深度学习模型训练或真实的 FedAvg 参数聚合计算。
- 不引入自动 HTTP 重试、断点续传或模糊提交补偿逻辑。
- 不修改底层 `GRANT` / `READY` 链路层时隙仲裁与 Token Passing 核心算法。

## Further Notes

- 当前测试集群物理网卡信息绑定：Server（`vm0`，`wlxfc221c300cbc`，USB 480M）、Client1（`vm1`，`wlxfc221c300cbb`，USB 5000M）、Client2（`vm2`，`wlxfc221c500a88`，USB 480M）。
- 每次真实硬件验收运行必须分配唯一的 `run_id`，归档保留在 `tests/logs/<run_id>/` 目录下，禁止任何形式的原地覆盖或跨次拼接。
- 验收套件在遭遇任何未预期异常时，必须优先保护现场证据并生成状态为 `failed` 的归档，确保现场问题可追溯。
