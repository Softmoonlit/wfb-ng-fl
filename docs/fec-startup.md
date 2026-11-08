# RS8/12 启动候选与标定

生产默认 FEC 为 RS8/14。`fec_k` 固定为整数 8，`fec_n` 仅接受整数 12 或 14（布尔值、浮点数、字符串与 null 均拒绝）。服务端启动 JSON 使用 `"fec_n": 12`；客户端使用 `wfb-fl-client-daemon --fec-n 12`。客户端 `node.json` 仅管理身份，不存 FEC。客户端作业 sandbox 启动参数继承 resident 当前值。

切换时停止作业，在服务端和全部十个客户端配置相同 FEC，再统一重启这 11 个 native 链路进程。native `v6_uplink` 的 trusted plaintext 接收端按启动 K/N 初始化，不会从数据包头自动识别 K/N；不可混用 RS8/12 与 RS8/14。可通过 native `GET_FEC` 审计实际进程参数，并与 daemon metadata 比对。`SET_FEC` 尚未实现（ENOTSUP），因此普通在线 patch 和 control 事务拒绝 FEC 字段，即使字段值等于当前值也不作为 patch 接受；control 完整状态快照可以携带当前 FEC，用于事务确认与回滚核对。切 MCS、带宽或恢复控制面不会把 RS8/12 重置成 RS8/14。

READY/IDLE 心跳状态不校验 FEC；混合 FEC 的节点可能仍显示 READY，不能把该状态作为 FEC 一致性的证明。候选实验准入必须审计服务端及全部十个客户端：各端实际 native `GET_FEC`、进程 argv 中的 K/N 与 daemon metadata 均须等于预期值，任一缺失或不一致都必须拒绝实验。实验 runner 负责全 11 端审计，并在切换时统一停止、启动全部 11 个链路；本轮不扩展心跳协议。

专用 `/api/v1/radio/calibrate` 允许 20/40MHz，仍要求显式 `bandwidth_mhz`、`downlink_mcs`、`uftp_rate_kbps`、人工确认和目标节点检查。可使用 HT40/MCS4/12000Kbps 候选。标定期间继续阻止普通作业；普通 `/api/v1/radio/reconfigure` 仍拒绝该低速率组合。此入口不表示候选已实测，不修改普通 rate bounds 表。
