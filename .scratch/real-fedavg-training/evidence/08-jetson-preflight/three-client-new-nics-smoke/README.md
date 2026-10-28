# 三客户端双新网卡 smoke test

本次验证 fl02、fl04 均更换网卡后，是否可以进入完整 FedAvg 前的三客户端通信基线。仍不是训练验收。

## 当前硬件身份

- fl02：`fc:22:1c:50:0a:88`。
- fl04：`fc:22:1c:30:0f:04`。
- fl01：`fc:22:1c:30:0e:99`。
- 四板 Python/native：提交 `701963a`，native SHA-256 `f7c73f41b730ddb3547e1730b5a5b7b40a55dd7a8e3e5f265dcc6563c6c432b1`。
- 射频：157 / HT40+ / 实际 `-14 dBm`；Server MCS 3，客户端 MCS 4，FEC 8/14。

## 结果

### r2：三端完成传输，但 runner 收尾失败

运行：[20261028T092827Z-three-client-new-nics-smoke-r2](runs/20261028T092827Z-three-client-new-nics-smoke-r2/)

三端下行和上行均完成。fl01、fl02、fl04 的 HTTP 均返回 201，4 MiB 大小和 SHA-256 全部一致；上行耗时分别为 97.669 秒（0.34 Mbps）、83.216 秒（0.40 Mbps）和 50.470 秒（0.66 Mbps）。runner 后续服务端 helper 收尾超时，未形成完整自动 `result.json` 成功状态，但三端 HTTP 结果已独立记录。

### r3：下行注册阶段 UFTP 退出码 7

运行：[20261028T093110Z-three-client-new-nics-smoke-r3](runs/20261028T093110Z-three-client-new-nics-smoke-r3/)

UFTP 在下行阶段因客户端掉出退出码 7，未进入上行。fl01 出现大量接收丢包，RSSI 一条链最低约 `-76 dBm`，并出现 `GRANT_SEQ=0`；fl02、fl04仍能完成本次下行。该轮说明双新网卡不能保证三客户端注册稳定。

### r4：fl01 下行重传超时

运行：[20261028T093144Z-three-client-new-nics-smoke-r4](runs/20261028T093144Z-three-client-new-nics-smoke-r4/)

fl02、fl04 下行完成，fl01 在多轮 NAK 后仍未完成，UFTP 因 node 1 超时以退出码 9 中止整组。fl01 的 Server 接收日志继续出现连续 `PACKET_LOSS`；恢复观察第一次未达到全员 READY，随后通过 fl01、fl04 顺序重启恢复三端 READY。

## 判断

- 新 fl02、fl04 网卡已经排除“原 fl02 网卡自身劣化”这一硬件变量；r2 中两端都完成了 4 MiB 上行。
- 三客户端整体仍未稳定，当前主要失败点转移到 fl01 的下行接收/注册链路，不能进入完整 FedAvg。
- r2 的一次完整传输不能抵消 r3/r4 的连续下行失败；必须先处理 fl01 的位置、天线、USB/供电或链路环境，再重新做三客户端稳定性验证。
- r4 结束后通过顺序重启恢复 Server `IDLE`、fl01/fl02/fl04 `READY`；未修改生产代码、协议、MCS、FEC 或超时。
