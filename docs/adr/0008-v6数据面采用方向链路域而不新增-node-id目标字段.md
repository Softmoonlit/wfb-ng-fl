# ADR-0008: v6 数据面采用方向链路域而不新增 NODE_ID 目标字段

Status: accepted

v6 数据面不在普通数据帧中新增 NODE_ID 目标字段，也不把 `source_node` / `target_node` 升格为普通数据面的正式协议字段，而是采用两个方向链路域：server 到 clients 的下行 stream，以及 clients 到 server 的上行 stream。空口物理承载仍是广播；BPF/channel_id 先按方向链路域过滤，避免客户端接收其他客户端的上行数据面流量；具体接收语义继续由 TUN/IP/TCP/UFTP 处理，节点级控制目标仍由链路层控制帧 envelope 的 `target_node`、`source_node` 和 NODE_ID 完成。控制面帧复用方向链路域：server 发出的控制帧走下行 stream，client 发出的控制帧走上行 stream；控制面优先级由链路层分流和控制面优先通路保证，不通过新增第三条 control stream 保证。

## Considered Options

- 数据面按方向链路域分离，不新增 NODE_ID 目标字段
- 数据面与控制面共用一个链路域，完全依赖 IP 层丢弃误收数据
- 每个数据帧都增加 NODE_ID source/target 并在写 TUN 前过滤

## Consequences

- client 不监听上行方向链路域，因此不会把其他 client 的上行数据面帧写入本机 TUN
- server 监听上行方向链路域，仍需通过 IP/TCP 会话或控制面观测区分具体 client
- server 下行方向仍是一发多收，非目标 IP 流量由客户端 IP/TCP/UFTP 栈处理
- 普通数据面帧既不携带正式的 `target_node` 字段，也不新增独立的正式 `source_node` 头字段；但在 shared uplink 多 sender 场景下，普通数据分片必须在既有 `data_nonce` 中提供 sender namespace，使 server 能在进入 unfinished-block 重组窗口前判定发送者，而不把该实现边界升格为新的独立数据面头模型
- 普通数据面不复用 control envelope，而是继续保持独立的数据承载头模型，围绕 session / `channel_id` / `FEC` block / payload 组织；控制面与数据面只在更低层共享空口承载、方向链路域过滤与 packet type 分流
- stream 数值使用配置项表达，例如 `downlink_stream` 与 `uplink_stream`；ADR 不锁死为 `0/1`
- server 发出的 `GRANT`、阶段控制等显式控制帧走下行方向链路域；反向控制窗口本身不要求单独控制帧类型，而是由 server 在该方向链路域内通过短 `GRANT` 调度实现
- client 发出的 `READY` 等显式控制帧走上行方向链路域
- v6 不新增独立 control stream；控制面优先级是同一方向链路域内的处理规则
