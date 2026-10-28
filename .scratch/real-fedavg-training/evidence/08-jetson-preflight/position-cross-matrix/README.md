# fl02/fl04 物理位置互换对照

本次在网卡已互换、两台均按现场条件使用 19 V 的基础上，只互换 fl02 与 fl04 的物理位置，验证位置/天线环境影响。

## 固定条件

- 四板 Python/native：提交 `701963a`，native SHA-256 `f7c73f41b730ddb3547e1730b5a5b7b40a55dd7a8e3e5f265dcc6563c6c432b1`。
- 信道 157、HT40+、实际 `iw` 功率 `-14.00 dBm`。
- Server 下行 MCS 3，单客户端上行 MCS 4，FEC 8/14。
- 每次只保留一个 active target，4 MiB 文件，下行和上行均核对 SHA-256。

## 结果

### A：fl02 主机 + 原 fl04 网卡，移动到 fl04 原位置

运行：[20261028T083034Z-single-fl02-swapped-position](runs/20261028T083034Z-single-fl02-swapped-position/)

- 下行 4 MiB 成功，SHA-256 一致。
- 上行 HTTP 201，4 MiB 文件完整，SHA-256 一致。
- 上行耗时 7.284 秒，约 4.61 Mbps。
- Server 持续收到 node 2 上行包并发 grant。
- 同一主机和同一网卡在 fl02 原位置时曾在 120 秒内无法完成；移动位置后明显改善。

### B：fl04 主机 + 原 fl02 网卡，移动到 fl02 原位置

运行：[20261028T083256Z-single-fl04-swapped-position](runs/20261028T083256Z-single-fl04-swapped-position/)

- 下行 4 MiB 成功，SHA-256 一致。
- 上行 HTTP sender 超时，未形成完整上行文件摘要；Server 侧没有形成持续的 node 4 有效上行数据。
- runner 因上行收尾失败标记失败，恢复无错误。
- 同一原 fl02 网卡在 fl04 原位置时曾可以完成上行但仅约 0.39 Mbps；移动到 fl02 原位置后进一步恶化为超时。

## 判断

- 位置因素已被直接复现：原 fl04 网卡从 fl02 原位置移到 fl04 原位置后，fl02 上行从超时改善为约 4.61 Mbps。
- 原 fl02 网卡也有独立劣化：它在较好位置仍只有约 0.39 Mbps，在较差位置完全无法完成上行。
- 当前最符合证据的模型是：原 fl02 网卡劣化叠加 fl02 原位置的天线/遮挡/多径/USB 或供电环境问题。
- 共同 Server/native 代码路径不是这次 fl02 特有故障的首要嫌疑；同一 bundle 在较好位置可以完成单客户端闭环。

测试结束后通过连续 6 个 API 采样确认 Server `IDLE`、fl01/fl02/fl04 `READY`，四端恢复 157 / HT40+、`-14 dBm`。
