Status: resolved

# Issue 41：真实硬件 FL Runtime 严格同步闭环

## 问题声明

项目已经具备 FL Runtime / SDK、Transport Backend、集成式链路层底座和 systemd 角色服务，但尚无可审计证据证明这些能力能够在三台独立机器的真实无线数据面上联合完成一次多客户端严格同步联邦学习轮次。

此前围绕原 GitHub Issue #41 的实现把独立 Issue #53 的双 40 MiB、两轮场景，以及尚未独立评审的立即 feedback window 候选行为混入了同一验收入口。仓库没有保留任何 Issue #41 现场归档，无法证明最后一次执行的实际结果，也无法区分当时失败发生在无线设备、方向链路域、TUN、UFTP、HTTP、Runtime、角色服务生命周期还是验收工具。

用户需要恢复原始 Issue #41 的边界，先证明真实无线数据面能够稳定双向传输，再通过安装后的角色服务完成一轮完整 FL Runtime 严格同步闭环，并让每个通过条件都能追溯到同一次运行中的直接证据。

## 解决方案

建立一条唯一、fail-closed 的三机真实硬件验收路径。当前机器固定承担 server、SSH orchestrator 和归档汇总机职责，另外两台机器分别承担 client1 和 client2。执行开始前，三台机器必须已经上线，server 能通过管理网 SSH 控制两个 client；管理网只用于编排和证据回收，不计入 FL 数据面成功事实。

验收首先在同一组链路进程和冻结配置下连续完成三个双向数据面周期。每个周期由一次 server 到两个 client 的 shared UFTP 下行，以及两个 client 分别到 server 的 HTTP PUT over TCP 上行组成。全部传输使用确定性 4 MiB 文件，并校验状态矩阵、大小和 SHA-256。任一周期失败都禁止进入正式 Runtime。

数据面门槛通过后，安装后的一个 server 角色服务和两个 client 角色服务运行一轮正式闭环。server 发布一个确定性 4 MiB 模型；client1 和 client2 分别等待并校验模型，执行占位训练，再提交内容不同的 4 MiB update。client1 立即提交，client2 固定延迟三秒，以直接证明 server 在只收到一个有效 update 时不会返回部分结果，只有完整 `[1, 2]` 到达后才完成 `wait_for_updates()`。

验收结束后验证角色服务 stop/restart、cgroup、TUN 和全部子进程生命周期。每次运行绑定唯一 commit、拓扑、解析后配置和 run ID；失败运行也必须生成完整失败归档，不得覆盖旧结果或拼接不同运行的证据。

## 用户故事

1. 作为系统维护者，我希望 Issue #41 恢复为原始的一轮真实硬件 Runtime 闭环，以便它能够独立收口，而不再被 Issue #53 的大文件多轮场景阻塞。
2. 作为系统维护者，我希望三台角色机在运行前使用同一分支、同一 commit 且工作区干净，以便所有现场证据都能归因到唯一代码版本。
3. 作为现场操作者，我希望当前机器固定作为 server、orchestrator 和归档汇总机，以便现场职责明确且无需第四台管理机。
4. 作为现场操作者，我希望 client 的管理地址、仓库位置和无线接口在每次运行时重新发现并归档，以便旧拓扑信息不会污染新的验收。
5. 作为现场操作者，我希望管理网只用于 SSH 编排和回收证据，以便任何模型或 update 成功都能证明经过真实无线数据面。
6. 作为链路维护者，我希望在运行 Runtime 前先验证 shared UFTP 下行和 HTTP PUT 上行，以便接收端无法收到数据时能够先定位数据面，而不是误改 Runtime。
7. 作为链路维护者，我希望同一配置和同一组进程连续完成三个双向数据面周期，以便排除依赖重启或偶然成功的脆弱链路。
8. 作为 Transport Backend 维护者，我希望一次 shared UFTP operation 同时向两个参与 client 交付模型和 manifest，以便证明下行语义与正式 Runtime 一致。
9. 作为 Transport Backend 维护者，我希望两个 client 分别以单连接、单 PUT 提交 update，以便证明 TCP per-client 上行能够通过真实 TUN 和无线链路完成。
10. 作为算法开发者，我希望模型和 update 都是确定性生成的 4 MiB 普通文件，以便验证真实文件交换而不混入模型框架或外部输入准备问题。
11. 作为算法开发者，我希望两个 client 的 update 内容和 SHA-256 不同，以便 server 能够证明它收到了两个独立节点的提交物。
12. 作为算法开发者，我希望 client1 立即提交而 client2 固定延迟三秒，以便验收能够稳定观察严格同步的中间状态。
13. 作为算法开发者，我希望 server 在只收到 client1 时继续等待，以便 `wait_for_updates()` 不会返回部分结果。
14. 作为算法开发者，我希望 server 只在 `[1, 2]` 的有效 update 全部到达后返回按 NODE_ID 数值升序排列的完整映射，以便严格同步契约得到证明。
15. 作为运行维护者，我希望正式闭环通过安装后的 systemd 角色服务启动，以便验收覆盖真实部署入口、Runtime、Transport 和链路子进程的联合行为。
16. 作为运行维护者，我希望一轮完成后验证 stop/restart、cgroup、TUN 和全部子进程，以便服务停止后没有孤儿进程或残留网络资源。
17. 作为测试工程师，我希望每个确定性通过条件都由归档验证器自动判定，以便不能用人工解释替代缺失证据。
18. 作为测试工程师，我希望无线原始日志、FEC、重传、队列峰值和阶段耗时被完整归档，以便失败时能够识别最后成功层和第一失败层。
19. 作为测试工程师，我希望任何失败都产生明确的 failed 结论和独立归档，以便后续修复不会抹去失败现场。
20. 作为链路架构维护者，我希望未接受的立即 feedback window 行为不参与正式通过，以便验收票不会静默拥有链路协议变更。
21. 作为链路架构维护者，我希望正式失败后可以运行隔离的候选行为 A/B 诊断，以便识别能力缺口，同时不把诊断结果冒充正式通过。
22. 作为项目维护者，我希望最终差异不包含 Issue #53、终端演示或兼容 profile，以便仓库只保留当前需求所需的最简路径。
23. 作为项目维护者，我希望环境错误、验收工具错误、现有产品实现错误和链路能力缺口被分别处理，以便修复发生在正确的责任边界。
24. 作为项目维护者，我希望链路能力缺口形成独立 blocker，完成后再返回 Issue #41，以便 Issue #41 的范围和通过结论保持清晰。

## 实现决策

- 原 GitHub Issue #41 的验收条件是规范来源；现场手册负责操作化，归档验证器负责机器可判定事实。
- 采用单一 clean-cut 验收路径，不保留 Issue #53 profile、旧 fixture profile 或正式结果 fallback。
- 当前机器固定承担 server、SSH orchestrator 和归档汇总机职责；另外两台机器固定承担 client1 和 client2。
- 三台机器上线且 server 可以通过 SSH 控制两个 client，是开始实现执行阶段之前的外部前置条件，不是编写本规格时已经成立的事实。
- 正式场景固定为一个联邦学习轮次、一个 4 MiB 模型和两个内容不同的 4 MiB update。
- 验收 fixture 确定性生成模型和 update，不依赖操作者准备输入文件；占位训练与占位聚合不得声称执行真实训练或 FedAvg。
- client1 的占位训练延迟为零，client2 的固定延迟为三秒；该延迟只服务于严格同步验收，不属于产品算法默认行为。
- 正式端到端边界是安装后的一个 server systemd 角色服务和两个 client systemd 角色服务。Runtime 四接口是业务驱动入口，Transport 和集成式链路层底座通过同一次运行的子进程与归档证据被覆盖。
- Runtime 前设置双向数据面硬门槛。连续三个周期复用同一组链路、UFTP receiver 和 HTTP receiver 进程；周期之间只清理本周期交付物和状态。
- smoke 与正式 Runtime 的信道、带宽、MCS、FEC、方向链路域、TUN 地址、队列阈值和 feedback 配置必须一致；从临时进程切换到 systemd 角色服务前后自动比较解析后的配置。
- 允许可靠传输从无线丢包中恢复，但单次 I/O 连续 120 秒无进展必须失败。每个 smoke 周期和正式轮次另有固定整体阶段上限；一次运行中不得临时放大上限。
- `feedback-window-start-immediately` 及等价候选行为不得出现在正式配置或正式通过归档中。
- 正式失败归档完成后，可以使用独立 diagnostic run 对候选链路行为做 A/B 比较；diagnostic run 必须具有独立标识，且永远不能形成 Issue #41 passed 结论。
- 每次运行使用新的 run ID，绑定 commit、三机身份、实际无线接口、完整解析配置和运行模式。旧归档不得覆盖，不同运行的证据不得拼接。
- 任一阶段失败后先停止、收集、校验并形成 failed 归档，再进行修复。不得保持现场进程运行并原地修改参数后继续生成同一结论。
- 失败按环境错误、验收工具错误、现有产品实现错误和链路能力缺口分流。链路能力缺口必须形成独立 blocker spec 和 tickets，完成后再恢复 Issue #41。
- 最终实现必须清理 Issue #53 专属两轮 40 MiB 门槛、终端演示及兼容路径，同时保留确属原始 Issue #41 的 Runtime、Transport、生命周期和验收能力。

## 测试决策

- 好测试验证公开契约和可观察结果，不依赖内部调用顺序；唯一例外是规范明确要求归档的链路证据，它们用于证明真实数据路径而非锁定实现细节。
- 最高测试边界是安装后的三个 systemd 角色服务。直接运行 Python fixture、namespace、same-host 或 split-process 结果只能作为本地回归，不能形成正式通过。
- 较低层测试覆盖确定性 fixture、严格同步状态、shared UFTP 完成矩阵、HTTP PUT 提交、角色服务清理、summary 构建、归档验证和 fail-closed 行为。
- 真实硬件执行前，所有本地单元测试、集成测试、脚本测试和合成归档测试必须通过。
- preflight 自动验证三机版本、工作区、依赖、sudo、无线设备、monitor/UP 状态、信道、USB 信息、端口、TUN、残留进程和运行目录边界。
- 双向数据面 gate 在同一配置和同一组进程下连续运行三个周期。每周期自动验证两个 client 的 UFTP CONNECT 与文件状态、model/manifest 大小和 SHA-256、两个 HTTP PUT 的客户端 201 与 server committed 事实，以及活动上传状态最终收敛。
- 数据面 gate 任一周期失败时，正式 Runtime 不得启动。
- 正式 Runtime 自动验证 `publish_model()`、两个 `wait_for_model()`、两个 `submit_update()` 和 `wait_for_updates()` 均由正式角色服务完成。
- 正式 Runtime 自动验证 client1 成功提交后 server 尚未返回结果，client2 提交后 server 返回完整且有序的 `[1, 2]`，并明确记录未返回 partial result。
- 正式 UFTP 验证每个参与 client、每个文件的完成矩阵内容，而不只是检查 status 文件存在。
- 生命周期验证拥有独立结果，不得从 Runtime 状态推导。自动检查 stop/restart、unit 状态、cgroup、TUN 和角色进程、链路进程、UFTP 进程是否全部清理。
- 归档验证器自动判定版本与身份、真实无线/TUN 路径、解析配置一致性、smoke 矩阵、Runtime 事件、文件大小和 SHA-256、HTTP 结果、严格同步中间状态、生命周期及必需文件完整性。
- READY/GRANT、authorized sends、server 接收、queue/backpressure、reassembly、sender isolation、feedback、丢包、FEC 恢复、TCP 重传、队列峰值和阶段耗时进入归档。若自然发生 queue pause，则必须有对应 resume 且最终状态恢复；不通过人为降低阈值强制制造事件。
- 人工审查只解释无线质量和失败根因，不能替代缺失的确定性证据。
- 最终对相对主线的完整差异进行代码审查，确认实现符合本规格和仓库规则，并确认无 Issue #53、演示或兼容 profile 残留。

## 范围外

- Issue #53 的双 40 MiB、两轮正式场景。
- 性能优化、吞吐基准、长时间压力、十节点规模、半同步和节点级容错。
- 真实模型训练、模型精度验证或 FedAvg 聚合。
- 修改 Token Passing、READY/GRANT、feedback window、队列协议或无线链路协议。
- 动态 feedback lease、可靠的上行阶段 release 或 `submit_update()` 阶段门禁。
- TLS、认证、链路加密或生产安全强化。
- namespace、same-host、split-process 或管理网数据传输替代三机真实无线验收。
- 把现场 IP、SSH 地址、仓库路径、网卡名或验收 fixture 参数推广为产品默认值。
- 为旧验收场景保留兼容层、fallback 或迁移垫片。

## 进一步说明

规格和 tickets 完成并提交后，由用户启动另外两台机器并确认当前 server 能通过 SSH 控制它们。任何代码收敛、本地验证或真实硬件执行都在该确认之后开始。

如果双向数据面 gate 失败，必须按无线设备与参数、方向链路域与 raw-air 计数、FEC/reassembly、TUN 与路由、UFTP feedback/status、TCP/READY/GRANT/队列、Runtime 的顺序定位，记录最后成功层和第一失败层。只有直接证据证明既有链路契约不足时，才进入独立 blocker 的设计和实现。
