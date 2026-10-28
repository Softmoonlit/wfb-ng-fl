# 新 fl04 网卡单节点基线

本次确认 fl04 新网卡、当前位置和当前 157/HT40+/14 档基线是否能独立建立上行控制链路。

## 身份与条件

- fl04 MAC：`fc:22:1c:30:0f:04`。
- USB 驱动：`rtl88xxau_wfb`，USB 3.x 协商 `5000M`。
- Python/native 与其他节点一致，native SHA-256：`f7c73f41b730ddb3547e1730b5a5b7b40a55dd7a8e3e5f265dcc6563c6c432b1`。
- 射频：157 / HT40+ / 实际 `-14.00 dBm`，Server MCS 3，fl04 上行 MCS 4，FEC 8/14。
- 运行：[20261028T101320Z-single-fl04-new-card-baseline](runs/20261028T101320Z-single-fl04-new-card-baseline/)。

## 结果

- fl04 能收到 Server 射频包；两条接收链日志约为 `-64~-70 dBm`。
- fl04 `TOKEN_AUTH=0`、`GRANT_SEQ=0`，没有获得有效 grant。
- Server native 没有收到 node 4 的有效上行声明；UFTP 只含 fl04 时 ANNOUNCE 超时，退出码 7。
- 因此 4 MiB 传输未开始，失败发生在控制/授权阶段，不是大文件吞吐阶段。
- fl04 内核日志未发现近期 USB reset、重新枚举、固件加载失败或驱动崩溃；无板端输入电压遥测。
- runner 清理后延迟 45 秒 API 仍为 Server `IDLE`、fl01/fl02/fl04 `READY`，说明本次恢复没有立即复现掉线，但不能抵消独立上行控制失败。

## 判断

新网卡已被系统正确识别，版本和 USB 速率正常；当前证据把故障定位到 fl04 发往 Server 的上行发射/天线/空间链路，而不是旧网卡、代码混装或三客户端调度竞争。优先逐项检查 fl04 的天线馈线/接头、USB 线与端口、19 V 发射负载压降和当前位置多径；每次只改变一个变量，再重复同一单 fl04 基线。未修改生产信道、功率、MCS，也未测试主信道 161。
