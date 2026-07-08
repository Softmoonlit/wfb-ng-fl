# ADR-0007: v6 控制面采用统一链路层控制帧 envelope

Status: accepted

v6 控制面采用统一链路层控制帧 envelope，而不是沿用单一 `TokenFrame` 或为每种控制语义各自定义完全独立帧格式。公共外层只表达控制帧身份、控制类型、源节点、目标节点、公共 `sequence` 观测字段，以及显式 `version` 字段；`GRANT`、`READY` 等显式节点级控制语义由控制类型区分，反向反馈机会保留为调度行为并可由短 `GRANT` 序列实现，控制帧不进入普通数据面 FEC 组块，不写入 `TUN`，并由链路层控制面优先通路处理。第一版中只有 `GRANT` 对该公共 `sequence` 字段施加正式协议语义：发送端按 `source_node` 维持单调递增，接收端也仅对 `GRANT` 执行基于该字段的新鲜度 / 去重 / 拒旧校验；`READY` 虽可保留该字段位置，但正式接收裁决不依赖它。

v6 继续使用 WFB `channel_id` 作为链路域过滤边界；旧实现里可继续采用 `channel_id = (link_id << 8) + radio_port` 这类映射，但该关系仅属于链路域过滤的实现映射，不升格为节点级寻址语义。空口帧物理上仍按广播承载，所谓控制帧单播是接收端基于 `target_node` / `NODE_ID` 的逻辑过滤，不表示 802.11 物理单播。`target_node=0` 保留为广播/ALL，但第一版 `GRANT` 必须显式指向某个具体 client `NODE_ID`，`target_node=0` 对 `GRANT` 一律非法；广播只保留给全局阶段、静默或公告类控制语义，且不作为 v6 第一切片验收依赖。对 `READY` 而言，第一版单 server 链路域下保留统一 envelope 的 `target_node` 公共外层字段，但正式语义与接收裁决都不依赖该字段；其正式最小语义只表达“发送者 `NODE_ID` 已具备上行发送条件”，并通过进入当前链路域 server ready 接收入口与静态已知 client 白名单完成接收裁决。READY 在实现里若暂时填写某个具体 `target_node` 值，也只属于实现细节：既不要求必须等于 server `NODE_ID`，也不自动升格为第一版正式契约。若后续出现多 server、server 选路或显式 READY target 需求，再作为后续版本单独决策。

v6 第一切片中，grant 控制帧在空口上只携带相对 `duration_ms`；接收端在本地接收时刻换算 `expires_at_ms`，并以该本地截止时间执行 Token gate 的硬截止判定。绝对截止时间不作为空口控制帧公共字段进入本切片。

第一版统一 control envelope 仅接受单一固定 `version`；`packet_type + magic + version` 共同构成最小解析边界。`version` 不匹配时直接丢弃并记录，不做隐式兼容；后续若要变更 envelope 结构，优先通过显式版本演进，而不是依赖字段长度或 `control_type` 分叉做猜测兼容。

第一版 `control_type` 采用严格有限枚举。第一版正式支持的控制类型至少包括 `GRANT` 与 `READY`；若未来需要引入全局阶段控制、静默或公告类控制，也必须先增补文档并显式扩充枚举。未知 `control_type` 一律直接丢弃并记录，不做隐式兼容、宽松透传或“先过解析再等待未来解释”。广播型控制若后续引入，也必须占用显式枚举值，而不是复用现有类型或靠 flags 偷表达。

第一版中，`GRANT` 必须占用一个独占专用 `control_type`。普通 `GRANT` 与反馈轮短 `GRANT` 不拆成不同类型，仍统一归为 `GRANT`；二者差异仅体现在 `duration_ms`、目标 client 与所处调度上下文。反向控制窗口 / 反馈轮属于调度行为上下文，而不是新的 `control_type`；第一版不引入 `SCHEDULER` 一类泛化父类型。

第一版中，`READY` 也必须占用一个独占专用 `control_type`。其第一版语义只表达“发送者 `NODE_ID` 已具备上行发送条件”，不引入 `CLIENT_SIGNAL` / `CLIENT_EVENT` 一类泛化父类型，也不允许通过追加 flags、复用 `READY` 或父类型子类来偷偷扩义；若未来需要新的 client->server 控制消息，必须单独新增显式 `control_type`。

第一版规划文档先冻结 `control_type` 的语义集合，不先冻结具体数值编号。当前至少冻结 `GRANT` 与 `READY` 两个显式控制类型；具体枚举值留到实现阶段或协议定义文件中统一落定。无论最终编号如何，都必须保证每个 `control_type` 值唯一，且文档语义与实现严格一致；若后续外部工具链或抓包格式开始依赖编号表，再把数值表提升为显式文档契约。
第一版控制帧 payload 采用“统一 envelope + 按 `control_type` 分型负载”口径，而不是在 envelope 后强制所有控制类型共用同一固定控制体。`GRANT` 的第一版专属 payload 固定承载 `duration_ms`；`READY` 的第一版专属 payload 固定为空载荷，不再为它伪造 `duration_ms`、绝对截止时间或其他 grant 专属字段。接收端必须先完成 envelope 解析，再按 `control_type` 校验该类型允许的 payload 形态与精确总长度；`GRANT` 只能接受其固定长度，`READY` 只能接受仅含 envelope 的固定长度，多 1 字节或少 1 字节都一律直接丢弃并记录。第一版不引入 TLV、显式 `payload_len/total_len` 字段、可选字段协商或“同一固定控制体里部分字段按类型忽略”的宽松兼容策略；旧单一大结构若比该类型正式长度更长，也不得按“最小长度满足即可”放行。
第一版统一 control envelope 虽保留公共 `sequence` 字段，但该字段的正式协议语义只对 `GRANT` 生效，不对全部 `control_type` 一刀切展开。`GRANT` 必须按 `source_node` 维持单调递增，且在单 server 第一版下每个 `GRANT.source_node` 都必须显式等于当前 server 的 `NODE_ID`；接收端也仅对 `GRANT` 按源节点维护新鲜度状态并执行 duplicate / stale 拒绝。`READY` 不因缺少、重复、倒退或未消费该字段而改变第一版正式语义，相关行为不得偷偷升格为 READY 的隐式去重契约。该单调性只要求在单次 `GRANT` 发送端生命周期内成立，不要求跨进程重启永久单调；若发送端重启后从较小基线重新开始，接收端必须依赖独立重置边界清空该 `source_node` 的新鲜度状态后再接受新序号。第一版推荐直接复用既有 session/`epoch` 重置边界表达这一切换，而不是为 `GRANT` 再单独引入 reset flag、额外握手或持久化序号协议。
第一版控制帧进入类型语义裁决前，必须先完成 `packet_type + magic + version + control_type + 精确长度` 这些结构合法性检查。完成结构检查后，`GRANT` 的正式接收裁决顺序冻结为“来源节点合法性 → 目标节点过滤 → 过期判定 → `sequence` 新鲜度判定”：只有 `source_node` 确属当前 server `NODE_ID` 的 grant，才进入目标过滤；只有目标确属本节点的 grant，才进入本地时间有效性判定；只有未过期 grant，才允许继续参与 duplicate / stale 拒绝。这里的过期边界也一并冻结为“到点即过期”，即本地 `expires_at_ms <= now_ms` 时一律视为失效，而不是仅在 `< now_ms` 时才失效。这样可避免伪造来源、发给其他节点的 grant 或已过期垃圾帧污染本节点按源节点维护的新鲜度状态，也不给边界时刻额外放行一拍的灰区。第一版 `READY` 不进入这套 `GRANT` 序号裁决链路，而是按“来源节点合法性 → 当前链路域接收入口 → 静态已知 client 资格 → 活跃队列入队/刷新”处理；其中 `READY.source_node` 必须显式等于发送该声明的 client 自身 `NODE_ID`，server 也据此完成已知 client 资格判定与活跃队列入队/刷新。被拒收的 READY 不得静默吞掉，至少要按“非法来源”“错误接收入口 / 链路域”“未知 client”三类原因分别计数并打日志；这些原因只适用于已经通过结构合法性检查并被识别为 READY 的语义拒收，长度 / magic / version / control_type / payload 形态等结构解析失败不并入 READY 语义拒收分类。每条相关日志的最小排障字段集至少包括拒收原因、`source_node`、接收入口 / 链路域标识、当前 server `NODE_ID`，以及若能判定则带 `target_node` / `control_type`；其中 READY 的 `target_node` 仅作可选上下文字段，不因缺失、零值或实现未填单独触发异常日志。第一版 READY 指标默认只按拒收原因聚合，不按 `source_node` 拆维。
第一版控制面相关验收同样服从“外部语义通过层 + 观测证据完整层”的双层口径：例如上行场景下，既要证明活跃队列驱动的发送机会分配在双基线中仍能推进，也要能从日志 / 指标证明 `READY` 拒收原因分流、`GRANT` 目标过滤、过期拒绝与序号新鲜度判定等关键控制面裁决真实发生。第二层内部再分为 `2A 结构化必需证据` 与 `2B 补充日志证据`：前者要求形成稳定字段、固定计数或可归档摘要，后者要求留下足以人工核对的上下文日志。第一版控制面最小 `2A` 集合先冻结为 `grant_sent_total`、`ready_accepted_total`、`ready_rejected_total_by_reason`、`feedback_window_open_count/feedback_window_close_count`（仅在场景覆盖反馈轮时必需）以及“目标 client 在短 `GRANT` 时隙内收到反向上行数据”的结构化计数；其中 `ready_rejected_total_by_reason` 只统计已经通过结构合法性检查并进入 READY 语义裁决后的三类拒收原因：`invalid_source`、`wrong_ingress_or_link_domain`、`unknown_client`。反馈窗口相关 `2A` 只要求证明反馈轮开始/结束以及目标 client 在其短 `GRANT` 时隙内收到反向上行数据，不要求把反馈轮内逐个短 `GRANT` 的发放数升格为结构化必需证据；同样也不要求把 `GRANT` 目标过滤拒绝、过期拒绝或 stale / duplicate 拒绝中的任何一项升格为 `2A`。单条拒收上下文、`target_node` 可选上下文、`GRANT` 目标过滤 / 过期 / 序号拒绝明细（包括 stale / duplicate 拒绝）、活跃队列逐条入队 / 刷新过程、反馈轮内逐个短 `GRANT` 的发放过程，以及长度 / magic / version / control_type / payload 形态等结构解析失败明细，默认留在 `2B`。控制面日志与指标因此是正式验收证据层的一部分，而不是可有可无的调试副产物。

控制面相关正式验收的唯一执行细则以 `docs/v6第一版正式验收标准细则.md` 为准；本 ADR 在此只保留与控制帧边界直接相关的验收约束摘要。

## Known Divergence

截至 2026-07-08，当前实现与本 ADR 以及 `CONTEXT.md` 中关于 `GRANT.source_node` 的来源身份语义存在已知冲突：实现保持现状，在接收 `GRANT` 时未严格校验 `source_node == server NODE_ID`，而是依赖当前单 server、受信任实验环境、`target_node` 过滤、过期判定与 `sequence` 新鲜度判定维持 v6/v7 前置场景可运行。

该冲突当前不作为 v6 进入 v7 的阻塞项；也不在本次直接修改协议实现。后续若要重新收敛该语义，必须单独决策：要么恢复并实现本 ADR 当前定义的来源身份硬约束，要么修订本 ADR、`CONTEXT.md` 与验收口径，将 `GRANT.source_node` 降级为保留字段 / 日志上下文 / 序号命名空间字段之一。未完成该后续决策前，不应把当前实现误读为已经满足“`GRANT.source_node` 必须等于 server `NODE_ID`”的协议要求。

## Considered Options

- 统一链路层控制帧 envelope
- 每种控制语义定义独立帧结构
- 沿用旧 `TokenFrame` 后续按需扩字段

## Consequences

- RX 分流可以先基于统一外层快速区分控制面与数据面
- `Token Passing`、上行就绪声明和反馈机会保持不同领域语义，不被统一混称为 Token
- 公共 `sequence` 字段、日志字段和验收观测仍可统一，但第一版正式新鲜度判断只对 `GRANT` 生效
- `GRANT` 序号按 `source_node` 分域单调；单 server 第一版中该 `source_node` 必须就是当前 server `NODE_ID`，因此接收端的新鲜度状态也只围绕该正式来源建立
- `GRANT` 序号不是跨重启永久单调计数器；较小序号重新生效必须绑定显式重置边界，避免接收端靠超时猜测放行
- `channel_id` 只隔离链路域，不承担节点寻址；同一 FL 传输域内 server 与 clients 使用相同 `channel_id`
- 节点级控制目标由 `target_node` 和 **NODE_ID** 在接收端过滤；第一版 `GRANT` 只能是显式逻辑单播目标，广播 grant 非法，而 `READY` 不因 `target_node` 取值形成正式 target 契约
- grant 空口载荷只表达相对 `duration_ms`，不要求跨节点共享绝对截止时间或额外时钟同步
- `READY` 不再背负 `duration_ms` 等 `GRANT` 专属字段，避免把 grant 语义误升格为控制面公共语义
- `GRANT` 接收裁决顺序固定为“目标节点过滤 → 过期判定 → 新鲜度判定”，便于实现、计数器与抓包分析统一对齐
- `GRANT` 来源身份不是自由字段：单 server 第一版中，`source_node != server NODE_ID` 的 grant 一律视为非法来源
- `READY` 来源身份同样不是自由字段：第一版中，`source_node != 发送该声明的 client NODE_ID` 的 READY 一律视为非法来源
- 非法 READY 不允许只有一个模糊总数；第一版至少区分“非法来源”“错误接收入口 / 链路域”“未知 client”三类拒收原因做独立观测
- 非法 READY 的默认观测面采用最小排障字段集，而不是一上来绑定 namespace/socket/raw-bytes 等全量调试噪声
- READY 的 `target_node` 只作可选日志上下文，不能因为观测面存在就反向升格成正式接收条件
- 非法 READY 的指标面默认保持低基数：只按拒收原因聚合；`source_node` 细节留给日志而不是默认指标标签
- `GRANT` 过期边界采用硬截止口径：本地 `expires_at_ms <= now_ms` 即失效，不保留“等于边界点还能再发一次”的半开灰区
- 第一版按 `control_type` 冻结精确总长度，抓包、测试与实现校验边界都可以直接对齐，不给尾随脏字节或偷渡扩展留灰区

第一版不为“头部形式统一”而把控制面 envelope 倒灌进普通数据面。控制面使用统一 control envelope，至少承载控制类型、节点级控制语义、控制序号与控制专属字段；普通数据面继续使用独立的数据承载头模型，围绕 session / `channel_id` / `FEC` block / payload 组织。二者只在更低层共享 WFB 空口承载、`channel_id` / 方向链路域过滤与 packet type 分流；若后续要引入公共极薄外层，也只能承载 packet family / version / routing-class 一类无节点语义字段，不得破坏控制头与数据头的职责分界。
