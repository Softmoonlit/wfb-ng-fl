# 当前双新网卡条件下的单 fl01 对照

本次用于区分 fl01 本地链路与三客户端共享链路影响。

- fl01 网卡：`fc:22:1c:30:0e:99`。
- fl02：`fc:22:1c:50:0a:88`；fl04：`fc:22:1c:30:0f:04`。
- 射频：157 / HT40+ / 实际 `-14 dBm`；Server MCS 3，fl01 上行 MCS 4，FEC 8/14。
- 运行：[20261028T094406Z-single-fl01-current-nics](runs/20261028T094406Z-single-fl01-current-nics/)。

## 结果

- 仅 Server 与 fl01 参与 4 MiB 双向传输。
- 下行成功，SHA-256 一致；上行 HTTP 201，4 MiB 完整，SHA-256 一致。
- 上行耗时 52.157 秒，约 0.64 Mbps。
- Server 能持续给 fl01 发 grant，但日志仍记录 fl01 上行包丢失。
- 传输事实通过；runner 仅因恢复观察期限内未确认全员 READY 而标记 `transfer-passed-recovery-unconfirmed`。随后现场恢复过程出现 fl01/fl02/fl04 READY 轮换，未伪造全员 READY。

## 判断

fl01 不是完全失效的网卡：单节点仍能完成双向传输。但它的链路余量较低、吞吐和丢包不理想；三客户端并发时更容易在下行注册或 UFTP 重传阶段越过失败阈值。当前优先做 fl01 的位置/天线/USB/供电与节点组合对照，不直接把 fl01 网卡判定为损坏。
