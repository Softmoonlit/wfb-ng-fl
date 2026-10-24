Status: ready-for-agent

# Stage 4 Web 控制台与同步文件仿真

## Problem Statement

当前项目已经具备 Stage 2 的常驻 Server/Client Daemon、带内控制面、作业级 `TASK_READY` 屏障、RoleService 生命周期、FL Runtime/Coordinator、UFTP 下行和 HTTP PUT 上行，也已经通过 Stage 3 的真实 VM/射频流程验证了作业运行时不依赖 SSH。操作者仍缺少一个面向桌面管理网的统一 Web 入口，无法从自己的电脑完成模型上传、作业启动、状态观察、射频准备和急停。

现有 REST IPC 面向本机调试和既有运行时调用，作业请求依赖 Server 本地模型路径，也没有模型库、内容寻址、可恢复控制台状态、统一的 HTTP/SSE 事件契约或静态操作台。浏览器若直接拼接事件或依赖管理网访问 Client、TUN、UFTP、HTTP PUT 或 SSH，会重新引入带外编排和状态不一致风险。

Stage 4 首版还需要一个可重复的文件仿真作业边界。平台必须能够通过管理网接收初始普通文件，经无线控制面协调 Server 与 Clients，再经无线数据面完成多轮模型下发和 update 上传，同时准确表达轮次、阶段、失败、急停和资源恢复。该闭环用于验证平台编排和文件交付，不应被误报为真实 FL 训练、聚合、FedAvg 或收敛效果。

## Solution

交付由同一个 `wfb-fl-server-daemon` 托管的零构建依赖原生 Web 控制台。Daemon 保留本机 `127.0.0.1:9090` IPC，同时在显式配置的管理网 IPv4 地址上提供固定 `8080` 端口的同源 Web listener。浏览器只访问 Server Daemon；Server 再通过现有无线控制面和 Client Daemon 完成节点管理、作业屏障、轮次推进、急停和资源恢复。

平台增加受控模型库，以完整小写 SHA-256 作为不可变模型制品的身份。浏览器上传文件时，Server 流式计算摘要并写入模型库；无线作业只引用摘要，不接受任意 Server 本地路径。首版模型库拒绝空文件，单文件上限为 1 GiB，总容量上限为 10 GiB，同一内容上传结果为去重成功。

首版作业固定为同步文件仿真作业。作业接受固定目标节点集合和固定完整同步轮次数量后，Server 等待所有目标 Client 的当前 `job_id` `TASK_READY`，经无线 UFTP 向全部目标节点下发模型；Client 跳过训练，以收到且校验通过的模型文件作为本轮 update，经无线 HTTP PUT 上传；Server 收齐全部有效 update 后跳过聚合，继续使用同一模型制品进入下一轮。任一目标节点失败、超时或摘要不匹配都会使整个作业失败，不跳过节点、不自动补轮、不续跑失败轮。

控制台从 Server 的权威状态快照恢复视图，通过 SSE 接收快照和关键事件。页面只展示有事实依据的当前轮次、已完成轮数、总轮数、Server 阶段、作业/轮次/阶段耗时、节点粗状态、错误、关键事件和资源恢复状态。页面提供模型库、运行监控、射频准备和急停操作；不展示虚假文件百分比、预计剩余时间、训练/聚合阶段或链路实时遥测。

验收采用四层矩阵：领域与模型库单元测试、Server HTTP/SSE 集成测试、浏览器工作流自动化测试，以及本机 Server、`vm1`、`vm2` 的三机 40 MiB 实体闭环。实体闭环必须同时通过无线控制面和无线数据面；Stage 2/3 已验证的底层射频证据在不改变运行时、协议或验收语义时复用。

## User Stories

1. As a 现场操作者, I want to 从自己的电脑通过直连管理网打开 Web 控制台, so that 我无需登录 Server 终端即可操作当前集群。
2. As a 现场操作者, I want to 通过固定的同源页面、API 和 SSE 访问 Server, so that 浏览器不需要配置额外 API 主机、CORS 或客户端地址。
3. As a 部署维护人员, I want to 显式配置 Server 的管理网地址, so that daemon 不会绑定所有接口、TUN 地址或无线数据接口。
4. As a 服务维护人员, I want to 管理网地址暂不可用时核心 daemon 和无线控制面继续运行, so that 管理网暂时故障不会停止集群运行时。
5. As a 现场操作者, I want to 在页面看到 Server、Client 和管理 Web 的就绪状态, so that 我可以在启动作业前发现节点离线或资源恢复阻塞。
6. As a 现场操作者, I want to 在模型库上传一个普通文件, so that 我可以把初始模型交给平台而不暴露 Server 本地路径。
7. As a 现场操作者, I want to 看到上传文件的文件名、大小、SHA-256、入库时间和引用状态, so that 我可以确认选择的是正确模型制品。
8. As a 现场操作者, I want to 重复上传相同内容时得到明确的去重结果, so that 我不会产生多个相同的大文件副本。
9. As a 现场操作者, I want to 在上传空文件、超大文件或容量不足时得到稳定错误, so that 我能在作业开始前修正输入。
10. As a 现场操作者, I want to 删除未被作业引用的模型制品, so that 我可以维护模型库容量。
11. As a 现场操作者, I want to 在模型被作业引用时无法删除它, so that 正在运行的作业不会失去输入。
12. As a 现场操作者, I want to 使用模型的完整 SHA-256 创建作业, so that 文件身份不依赖容易冲突的文件名。
13. As a 现场操作者, I want to 固定目标 Client 集合和同步轮次数量, so that 作业的参与者和终止条件在运行期间不会漂移。
14. As a 现场操作者, I want to 启动作业前获得节点未就绪、射频未准备、模型不存在或资源未恢复的明确原因, so that 我不会提交一个必然失败的作业。
15. As a 运行时维护人员, I want to Server 只有在全部目标 Client 完成当前作业的 `TASK_READY` 后才发布模型, so that 进程已创建或普通心跳不会被误判为作业就绪。
16. As a 运行时维护人员, I want to 作业运行期间由无线控制面推进节点和轮次, so that 正常作业不依赖 SSH、管理网文件复制或人工补发消息。
17. As a 运行时维护人员, I want to Server 通过现有 UFTP 向所有目标 Client 下发同一模型, so that Stage 4 复用已验证的数据面。
18. As a 运行时维护人员, I want to Client 通过现有 HTTP PUT 上传本轮 update, so that 上行仍由无线数据面承载并保持既有传输边界。
19. As a 运行时维护人员, I want to Server 校验每个 update 的作业、轮次、节点、大小和 SHA-256, so that 重复、错轮或错节点文件不能冒充有效结果。
20. As a 运行时维护人员, I want to 只有全部目标 Client 的有效 update 收齐后才推进同步轮次, so that 固定轮次真正表示完整同步轮次。
21. As a 操作台用户, I want to 看到当前轮次、已完成轮数和总轮数, so that 我可以判断作业推进事实。
22. As a 操作台用户, I want to 看到 Server 当前处于准备、下发模型、等待 update 或恢复待命阶段, so that 我可以理解作业正在等待什么。
23. As a 操作台用户, I want to 看到作业、本轮和当前阶段的实际耗时, so that 我可以识别停滞而不依赖虚构的剩余时间。
24. As a 操作台用户, I want to 看到执行结果和资源恢复状态分开呈现, so that 作业失败/成功与链路是否已经恢复待命不会混淆。
25. As a 操作台用户, I want to 看到节点粗状态、在线情况和阻塞原因, so that 我可以知道哪个节点阻止作业启动或恢复。
26. As a 操作台用户, I want to 看到最近有限数量的关键作业事件, so that 我可以理解状态变化而不把周期心跳当成事件历史。
27. As a 操作台用户, I want to 刷新页面或重新建立 SSE 连接后恢复完整当前视图, so that 浏览器短暂断线不会导致状态丢失。
28. As a 操作台用户, I want to 在 SSE 断开时看到状态可能过时的提示并暂停本地耗时推算, so that 页面不会把旧画面当作实时事实。
29. As a 操作台用户, I want to 在 Server 重启后看到新的实例身份和前次作业不可用提示, so that 页面不会伪造旧作业已经成功或可以续跑。
30. As a 操作台用户, I want to 随时发起急停, so that 出现无线、节点或操作风险时可以停止当前作业。
31. As a 操作台用户, I want to 看到急停请求已受理与最终资源恢复两个不同结果, so that 请求发送成功不会被误报为清理已经完成。
32. As a 运行时维护人员, I want to 急停通过无线控制面传播到所有目标 Client, so that 作业停止不依赖管理网 SSH。
33. As a 运行时维护人员, I want to 急停取消传输、停止 RoleService、释放临时模型/update 副本并恢复待命链路, so that 下一作业不会继承前一作业的资源。
34. As a 操作台用户, I want to 在资源恢复完成前无法创建下一作业, so that 并发任务不会破坏 TUN、Transport 或角色服务所有权。
35. As a 现场操作者, I want to 在射频页面配置扁平的信道、发射功率、上下行 MCS, so that 我可以按当前台面环境作出明确的物理配置选择。
36. As a 现场操作者, I want to 射频校验拒绝 Channel 161 和其他非法组合, so that 已知驱动越界风险不会进入运行时。
37. As a 现场操作者, I want to 根据下行 MCS 看到 UFTP 安全速率范围和推荐值, so that 我不会通过过高注入速率击穿 TUN 队列。
38. As a 现场操作者, I want to 对 UFTP 安全范围外的配置看到阻断式告警并明确确认, so that 高风险配置不能被普通提交静默绕过。
39. As a 现场操作者, I want to 只能在空闲集群应用射频配置, so that 运行中的作业不会被隐式重配。
40. As a 运行时维护人员, I want to 射频应用继续使用现有双阶段最终屏障和 15 秒租约, so that 部分节点失败时可以安全回退或进入 fail-closed `RADIO_ERROR`。
41. As a 现场操作者, I want to 页面保持桌面操作台布局并在 1280x720 视口可用, so that 运行监控、模型、射频和急停信息可以同时被可靠操作。
42. As a 现场操作者, I want to 急停入口常驻且在运行状态下清晰可见, so that 紧急操作不需要先离开当前监控视图。
43. As a 现场操作者, I want to 上传、模型清单和状态查看在运行期间仍可用, so that 观察和准备下一项输入不会被不必要地锁死。
44. As a 现场操作者, I want to 与当前作业冲突的操作被禁用并显示原因, so that 页面行为与 Server 的资源门禁一致。
45. As a 验收人员, I want to 用 canonical fixture 生成确定性 40 MiB 模型和摘要, so that 每次实体闭环具有可比较的文件证据。
46. As a 验收人员, I want to 分别验证领域、HTTP/SSE、浏览器和三机闭环, so that Web 功能问题、运行时问题、无线控制面问题和数据面问题能归属到正确边界。
47. As a 验收人员, I want to 三机闭环同时验证无线控制面和无线数据面, so that 文件传输成功不能掩盖心跳、屏障、轮次推进或终态恢复缺陷。
48. As a 验收人员, I want to 归档管理 Web、控制面、数据面、生命周期和汇总证据, so that 验收结果可以机械复核并保留失败现场。
49. As a 验收人员, I want to 在无硬件测试中注入节点离线、屏障超时、摘要错误、SSE 断线和 Server 重启, so that 大多数失败分支可以在 CI 中重复验证。
50. As a 项目维护人员, I want to 在不改变运行时二进制、控制协议、数据面协议、射频逻辑或验收语义时复用既有 Stage 2/3 硬件归档, so that Web 层变化不会无意义地重复实体射频测试。

## Implementation Decisions

- Stage 4 只增加平台操作控制台和同步文件仿真作业能力，不建立新的 Server/Client Daemon、控制协议、TUN、UFTP、HTTP PUT 或 Runtime 实现。已有 Coordinator 的 `semi_async` 和 `async` 分支保留为既有实现背景，但不进入首版 Web 文件仿真契约。
- Web 是首版唯一用户入口；不交付 `wfb-fl-core` CLI、全屏 TUI、第二个 Web 后端或前端构建链。静态 HTML/CSS/JavaScript 由 `wfb-fl-server-daemon` 内置托管，页面不依赖在线资源。
- Server Daemon 保留 `127.0.0.1:9090` 本机 IPC，并新增显式 `web_host` 与固定 `web_port: 8080` 的同源 listener。禁止绑定 `0.0.0.0`、回环地址作为 Web 降级地址、TUN 地址或无线数据接口。管理网地址不可用时，核心 daemon、控制面、TUN 和 Client Daemon 继续运行；Web listener 进入不可用状态，并在地址就绪后重新尝试绑定。
- HTTP handler 只做 HTTP 方法、路径、JSON、multipart 和 SSE 编解码、基础字段校验及错误映射；模型库、作业生命周期、状态快照、事件去重、射频准备和资源门禁由领域服务拥有。handler 不直接调用 Client、SSH、WFB、TUN、UFTP 或 HTTP PUT。
- 稳定资源契约包括：完整状态快照、模型列表/上传/删除、同步作业创建/急停、关键事件查询/SSE、当前射频配置读取、射频配置校验和应用。页面使用相对路径，同源 API 不启用 CORS。
- 作业创建只接受完整小写 64 位 SHA-256、固定 `target_nodes` 和正整数 `rounds`；使用 `Idempotency-Key` 保证 Server 实例生命周期内的重复受理幂等。请求不接受 `model_path`、算法插件、训练/聚合参数、半异步/异步模式或未版本化路径。
- 旧的 Stage 2 写入形状不作为 Stage 4 兼容入口。已退出契约的旧写路由返回稳定的 `410 ROUTE_REPLACED`，不接受旧请求体，也不增加 fallback 或迁移垫片。
- 模型库按内容寻址管理不可变模型制品：空文件拒绝，单文件最多 1 GiB，模型库最多 10 GiB；上传边接收边计算 SHA-256，连接中断、写入失败、容量预留失败或摘要失败时删除临时文件并释放预留；同一摘要去重；被作业引用的制品禁止删除。
- 模型库返回摘要、原始展示文件名、字节大小、创建时间和是否被作业引用。模型制品保留在 Server 模型库；作业停止后清理 Client 临时模型/update 副本，不提供大文件结果下载和历史归档浏览。
- Server 维护唯一权威控制台状态快照。快照包含 `schema_version`、Server `instance_id`、单调 `state_version`、生成时间、Server 状态、管理 Web 状态、当前射频、启动阻塞原因、节点列表、当前/最近作业和最近关键事件。
- 作业快照区分执行结果与资源恢复状态。执行结果使用 `none`、`running`、`succeeded`、`failed`、`aborted`；恢复状态使用 `not_required`、`recovering`、`ready`、`blocked`。恢复未完成时始终拒绝下一作业。
- 控制台只展示事实性最小进度：当前轮次、已完成轮数、总轮数、Server 的准备/下发模型/等待 update/恢复待命阶段、作业/轮次/阶段耗时、节点粗状态、错误和最近关键事件。不展示总百分比、文件传输百分比、预计剩余时间、逐节点文件阶段或链路丢包/FEC/速率曲线。
- SSE 连接建立后先发送完整 `snapshot`，随后发送完整 `state_changed` 快照和独立的 `operator_event`。每帧拥有单调 ID；`Last-Event-ID` 用于发现丢失，不能要求 Server 回放全部中间事件。慢客户端使用有限队列，溢出后关闭连接，客户端重新连接取得完整快照。心跳使用 SSE 注释帧，不进入操作者事件。
- Server 仅在内存保留当前 daemon 实例最近 100 条关键事件。事件包括作业受理/开始/完成、作业级就绪屏障、轮次开始/完成、Server 阶段变化、节点离线、失败、急停、恢复开始/完成/阻塞和射频应用结果；同一领域事实只生成一条事件。
- Server 重启产生新 `instance_id`，不跨实例恢复旧事件或旧作业，不把旧作业伪造为成功，也不支持断点续跑。浏览器通过快照识别实例变化并显示前次作业状态不可用。
- 首版文件仿真作业复用现有 Server/Client Daemon、作业宣告、心跳、`TASK_READY`、RoleService、Runtime、Coordinator 和 Transport。每轮全部目标 Client 完成模型接收和摘要校验后作为 update 上传；Server 校验全部 update 后继续使用同一模型制品，不调用真实训练或聚合入口。
- 任一目标 Client 失联、等待超时、身份错误、重复提交或文件大小/SHA-256 错误都会使当前轮和整个作业失败；不缩减目标集合、不跳过节点、不自动重跑整轮、不续跑失败轮。正常传输协议内部重传不算平台重跑。
- 急停由 Web 提交给 Server application service，返回“已受理”不等于最终已停止。Server 通过无线控制面传播急停，取消传输、停止任务角色、清理临时制品并恢复待命链路；恢复事实进入快照和 SSE。失败和急停均保留小型日志、配置、状态、摘要和 manifest。
- 无线控制面继续使用现有对称 UDP 方向和端口边界：Server 下行受限广播 `255.255.255.255:9000`，Client 上行单播到 `10.80.0.1:9001`；控制信令与 UFTP UDP `1044` 及模型组播地址隔离。Web 管理网 HTTP 上传不得进入无线数据面。
- 三机作业窗口内，客户端唤醒、任务屏障、轮次推进、急停和终态恢复都必须经带内无线控制面与数据面完成。SSH 仅可用于部署、启动服务、受控故障注入和事后取证，不得在作业窗口内启动角色、搬运文件、补发消息或恢复资源。
- 射频页面采用 ADR-0014 的扁平参数模型：`channel`、`radio_txpower_dbm`、`downlink_mcs`、`uplink_mcs` 和由下行 MCS 决定的 `uftp_rate_kbps`。不恢复 robust/standard/performance 预设，不允许 Channel 161，不允许运行中未经操作者确认的跳频或频宽切换。
- 射频配置通过校验上下文和应用操作分两步完成。UFTP 速率越界在交互层阻断并显示强告警；应用只允许空闲集群，复用 Stage 3 双最终屏障、15 秒租约、commit 前回退 157 和 commit 后 `RADIO_ERROR` 语义。
- 页面采用桌面操作台信息架构：常驻急停与状态指挥条、运行监控主页、独立模型库页面、独立射频页面；最低基线视口为 1280x720。移动端不作为产品目标，只保证页面不溢出且急停可执行。
- Client 物理网卡、`node.json` 身份、TUN/IP、驱动、USB/xHCI、软件包、systemd 和私有数据集/凭据仍属于部署维护面。Web 只展示就绪状态与错误原因，不提供动态修改或 SSH 终端。
- Stage 4 正式验收入口按 `preflight → domain/model-library → daemon HTTP/SSE → browser workflow → three-node control-plane/data-plane closure → evidence collection → mechanical summary` 执行；每层失败保留已经生成的证据。
- 三机实体验收固定使用本机 Server、`vm1`（`node_id=1`）和 `vm2`（`node_id=2`）。确定性 40 MiB 模型和 SHA-256 必须由 canonical `wfb_ng.fl.issue41_fixtures` 生成；不使用临时 shell 生成器。
- 三机实体闭环必须同时通过：控制面中的发现、心跳、节点注册、`TASK_READY`、轮次屏障、急停传播和终态恢复；数据面中的 UFTP 模型下发、HTTP PUT update 上传、节点/轮次关联、大小和摘要完整性。任一门禁失败都使实体闭环失败。
- 实体证据至少分为管理 Web、无线控制面、无线数据面、生命周期和汇总分区，包含唯一 `run_id`、提交、拓扑、动态 `wlx*` 接口、节点身份、驱动/USB/xHCI、配置、控制面摘要、传输摘要和清理结果。
- 后续提交不改变运行时二进制、控制协议、数据面协议、射频逻辑或验收语义时，复用既有 Stage 2/3 硬件归档；改变任一边界时重新执行受影响的控制面、数据面或射频实体验收。

## Testing Decisions

- 测试只断言操作者可观察的外部行为、稳定 API Schema、状态快照、控制面/数据面事实和归档判定，不绑定 Python 类的私有字段、线程实现、临时目录布局或浏览器 DOM 细节。
- 最高优先级接缝是 Server application service 与同源 HTTP/SSE listener；领域测试通过该接缝验证模型库、作业生命周期、快照和事件，浏览器测试通过真实 HTTP/SSE 验证最终操作台行为。只有无法通过该接缝表达的无线控制面故障和生命周期边界才使用 UDP/虚拟网络或现有 daemon seam。
- 领域与模型库测试覆盖 SHA-256 身份、去重、空文件、1 GiB 单文件限制、10 GiB 总容量、临时上传失败清理、引用保护、手动删除、作业幂等键和模型不存在错误。
- 作业状态测试覆盖同步固定轮次、目标集合冻结、`TASK_READY` 屏障、下发/等待 update/轮次推进、跨轮模型摘要连续性、节点失联、超时、错误摘要、重复 update、急停、恢复前拒绝新作业和恢复完成后重新启动。
- HTTP/SSE 集成测试覆盖静态资源同源服务、模型 multipart 上传、去重、模型列表/删除、作业创建/急停、快照 Schema、统一错误对象、旧路由 `410`、非法字段拒绝、SSE 首帧、状态变化、关键事件去重、`Last-Event-ID`、有限队列溢出、断线重连和 Server `instance_id` 变化。
- 射频 Web 测试覆盖扁平参数校验、Channel 161 拒绝、UFTP 速率范围动态提示、越界强告警、确认上下文失效、作业运行时拒绝应用和应用结果进入快照/SSE。
- 浏览器自动化测试覆盖在至少 1280x720 视口中的上传、去重、模型选择、固定轮数启动、状态观察、刷新、SSE 断线恢复、急停、恢复前禁启和射频告警确认。测试通过可访问的页面行为和可见文本/状态验证，不复制领域状态机到浏览器测试代码。
- 无硬件控制面测试复用现有虚拟网络/控制面 seam，覆盖 `NODE_HEARTBEAT`、`HEARTBEAT_ACK`、节点注册、控制面端口/方向、作业宣告、`TASK_READY`、重复/乱序处理和控制面失联；不把管理网 HTTP 成功当作无线控制面成功。
- 无硬件实体生命周期测试复用既有 Client Daemon、RoleService、Runtime、Transport 和 mock runtime seam，覆盖任务角色派生、正常终止、异常退出、急停、TUN 所有权恢复、临时文件清理和无残留进程。固定 sleep 不作为竞态修复或成功判据。
- 三机实体验收复用 Stage 3 运行时自治执行器、Stage 2 三机无 SSH 验收指南、canonical fixture、硬件预检和机械归档校验器。唯一实体拓扑为本机 Server、`vm1` 和 `vm2`；1~10 节点只做协议、配置和界面容量测试。
- 三机正常闭环使用确定性 40 MiB 文件和固定同步轮次，验证管理 Web 上传、无线控制面发现/心跳/`TASK_READY`/轮次屏障/终态恢复、无线 UFTP/HTTP PUT、每个 update 的节点/轮次/大小/SHA-256、跨轮摘要连续性和清理结果。
- 三机实体急停场景验证 Web 请求受理、控制面传播、传输取消、RoleService 停止、待命链路恢复、临时模型/update 清理以及恢复完成前拒绝下一作业。节点拔线、主动改频和底层射频故障实验继续由 Stage 2/3 既有范围承担，不作为 Stage 4 首版必跑场景。
- 验收归档必须记录 `management-web/`、`control-plane/`、`data-plane/`、`lifecycle/` 和 `summary/` 分区，并由机械校验器验证唯一运行身份、提交一致性、动态 `wlx*` 发现、控制面/数据面端口隔离、fixture 摘要、作业状态和资源恢复结果。
- 现有 prior art 包括 Stage 2 air-gapped mock E2E、FL Coordinator/Runtime/Control/Daemon 单元测试、Stage 3 runner 的作业窗口 SSH 禁止断言、Issue 41 fixture/manifest/摘要校验和 Stage 3 archive validator。新测试应扩展这些入口，不复制平行的验收脚本或第二套 fixture 生成器。

## Out of Scope

- CLI、`wfb-fl-core` 命令行产品、全屏 TUI 和实验编排工具。
- React、Vue、Node.js 构建链、在线 CDN、在线字体或其他外部前端依赖。
- 真实算法插件、插件注册、训练参数 Schema、聚合参数、代码/依赖部署和全节点插件一致性门禁。
- 真实训练、真实聚合、FedAvg、loss、accuracy、收敛分析和训练效果比较。
- Web 首版的 `semi_async`、`async` 文件仿真、跨轮迟到 update 和节点级训练参数覆盖。
- 总完成百分比、文件传输百分比、预计剩余时间、客户端细粒度文件阶段和链路实时丢包/FEC/队列/TCP 曲线。
- 作业历史列表、历史归档浏览、大文件结果下载和归档包下载。
- Web 直接访问 Client、SSH、WFB 底座、TUN、UFTP 或 HTTP PUT 数据面。
- Web 修改 Client 物理网卡、`node.json`、TUN/IP、驱动、USB/xHCI、systemd、私有数据集或凭据。
- 应用用户体系、多租户、HTTPS、跨域远程访问、面向非受信任网络开放和额外应用鉴权。
- 运行时自主跳频、自动频宽切换、未经操作者确认的射频重配以及重复 Stage 2/3 底层射频故障验收。
- 将 1~10 节点协议/配置容量测试写成已经完成的十节点实体验收；Stage 4 实体基准始终是本机 Server、`vm1` 和 `vm2`。

## Further Notes

- 本规格承接 `stage4-simulation-cli-and-web-dashboard` Wayfinder 地图及其已解决的运行契约、模型库、状态语义、射频准备、管理网、Web API、界面原型和验收矩阵决策；地图继续作为决策索引，本规格作为实现入口。
- “无线控制面”和“无线数据面”是三机验收中的两个独立门禁。控制面使用既有带内控制信令和控制面优先通路，数据面使用 TUN 上的 UFTP/HTTP PUT；两者共用底层无线承载但不能相互替代。
- 作业执行结果与资源恢复状态必须保持独立。失败或急停的作业可以仍处于 `recovering` 或 `blocked`，此时页面必须阻止下一作业。
- 管理网 Web 上传与无线模型下发是两个不同阶段：前者把制品写入 Server 模型库，后者由现有无线数据面将引用制品发布给目标 Client。
- 40 MiB 是实体验收输入，不是模型库任意用户文件的固定大小要求。任意合法普通文件遵循模型库容量契约；实体归档固定使用 canonical 40 MiB fixture。
- 本规格完成后可拆分为实现任务，但实现必须先保持上述状态、协议、证据和边界；不得以旧 API、旧本地路径或 SSH 编排增加兼容层。
