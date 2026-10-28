# fl04 移动到新较好位置后的单客户端验证

本次用于确认新物理位置是否足以支持继续软件工单，不是三客户端或 FedAvg 验收。

- 四板 Python/native：提交 `701963a`，native SHA-256 `f7c73f41b730ddb3547e1730b5a5b7b40a55dd7a8e3e5f265dcc6563c6c432b1`。
- fl04 当前仍使用原 fl02 网卡 `fc:22:1c:30:0c:bb`；现场条件为 19 V。
- 射频：157 / HT40+ / 实际 `-14 dBm`。
- Server 下行 MCS 3，fl04 上行 MCS 4，FEC 8/14。
- 仅保留 fl04 为 active target，负载 4 MiB。
- 运行记录：[20261028T085527Z-single-fl04-new-good-position-r2](runs/20261028T085527Z-single-fl04-new-good-position-r2/)。

## 结果

- 下行 4 MiB 成功，SHA-256 与源文件一致。
- 上行 HTTP 201，4 MiB 文件完整，SHA-256 与源文件一致。
- 上行耗时 26.283 秒，约 1.28 Mbps。
- runner 传输和清理无错误；随后通过连续 6 个 API 采样确认 Server `IDLE`、fl01/fl02/fl04 `READY`。

## 判断

新位置可以完成单 fl04 双向传输，可作为后续多客户端测试的候选位置；但当前使用的原 fl02 网卡吞吐仍明显低于原 fl04 网卡，不能作为三个客户端稳定 FedAvg 的最终硬件底座。应先更换原 fl02 网卡，再进行三客户端 smoke test 和正式训练。
