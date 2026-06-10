# ADR-0007: v6 控制面采用统一链路层控制帧 envelope

Status: accepted

v6 控制面采用统一链路层控制帧 envelope，而不是沿用单一 `TokenFrame` 或为每种控制语义各自定义完全独立帧格式。公共外层只表达控制帧身份、控制类型、源节点、目标节点、按源节点单调递增的序号，以及显式 `version` 字段；`GRANT`、`READY` 等显式节点级控制语义由控制类型区分，反向反馈机会保留为调度行为并可由短 `GRANT` 序列实现，控制帧不进入普通数据面 FEC 组块，不写入 `TUN`，并由链路层控制面优先通路处理。

v6 继续使用 WFB `channel_id` 作为链路域过滤边界；旧实现里可继续采用 `channel_id = (link_id << 8) + radio_port` 这类映射，但该关系仅属于链路域过滤的实现映射，不升格为节点级寻址语义。空口帧物理上仍按广播承载，所谓控制帧单播是接收端基于 `target_node` / `NODE_ID` 的逻辑过滤，不表示 802.11 物理单播。`target_node=0` 保留为广播/ALL，但 `GRANT` 必须面向具体 client；广播只保留给全局阶段、静默或公告类控制语义，且不作为 v6 第一切片验收依赖。对 `READY` 而言，第一版单 server 链路域下保留统一 envelope 的 `target_node` 公共外层字段，但正式语义不依赖该字段做接收裁决；其正式最小语义只表达“发送者 `NODE_ID` 已具备上行发送条件”，并通过进入当前链路域 server ready 接收入口与静态已知 client 白名单完成接收裁决。READY 在实现里若暂时填写某个 `target_node` 值，也不自动升格为第一版正式契约。若后续出现多 server、server 选路或显式 READY target 需求，再作为后续版本单独决策。

v6 第一切片中，grant 控制帧在空口上只携带相对 `duration_ms`；接收端在本地接收时刻换算 `expires_at_ms`，并以该本地截止时间执行 Token gate 的硬截止判定。绝对截止时间不作为空口控制帧公共字段进入本切片。

第一版统一 control envelope 仅接受单一固定 `version`；`packet_type + magic + version` 共同构成最小解析边界。`version` 不匹配时直接丢弃并记录，不做隐式兼容；后续若要变更 envelope 结构，优先通过显式版本演进，而不是依赖字段长度或 `control_type` 分叉做猜测兼容。

第一版 `control_type` 采用严格有限枚举。第一版正式支持的控制类型至少包括 `GRANT` 与 `READY`；若未来需要引入全局阶段控制、静默或公告类控制，也必须先增补文档并显式扩充枚举。未知 `control_type` 一律直接丢弃并记录，不做隐式兼容、宽松透传或“先过解析再等待未来解释”。广播型控制若后续引入，也必须占用显式枚举值，而不是复用现有类型或靠 flags 偷表达。

第一版中，`GRANT` 必须占用一个独占专用 `control_type`。普通 `GRANT` 与反馈轮短 `GRANT` 不拆成不同类型，仍统一归为 `GRANT`；二者差异仅体现在 `duration_ms`、目标 client 与所处调度上下文。反向控制窗口 / 反馈轮属于调度行为上下文，而不是新的 `control_type`；第一版不引入 `SCHEDULER` 一类泛化父类型。

## Considered Options

- 统一链路层控制帧 envelope
- 每种控制语义定义独立帧结构
- 沿用旧 `TokenFrame` 后续按需扩字段

## Consequences

- RX 分流可以先基于统一外层快速区分控制面与数据面
- `Token Passing`、上行就绪声明和反馈机会保持不同领域语义，不被统一混称为 Token
- 控制帧序号、新鲜度判断、日志字段和验收观测可以统一
- 控制帧序号按 `source_node` 分域单调；接收端按源节点维护新鲜度，避免多个发送节点之间要求全局序号协调
- `channel_id` 只隔离链路域，不承担节点寻址；同一 FL 传输域内 server 与 clients 使用相同 `channel_id`
- 节点级控制目标由 `target_node` 和 **NODE_ID** 在接收端过滤；广播 grant 非法，避免多个 client 同时打开 Token gate
- grant 空口载荷只表达相对 `duration_ms`，不要求跨节点共享绝对截止时间或额外时钟同步

第一版不为“头部形式统一”而把控制面 envelope 倒灌进普通数据面。控制面使用统一 control envelope，至少承载控制类型、节点级控制语义、控制序号与控制专属字段；普通数据面继续使用独立的数据承载头模型，围绕 session / `channel_id` / `FEC` block / payload 组织。二者只在更低层共享 WFB 空口承载、`channel_id` / 方向链路域过滤与 packet type 分流；若后续要引入公共极薄外层，也只能承载 packet family / version / routing-class 一类无节点语义字段，不得破坏控制头与数据头的职责分界。
