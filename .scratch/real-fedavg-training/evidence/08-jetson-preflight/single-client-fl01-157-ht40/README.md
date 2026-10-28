# 单客户端 fl01 / 157 / HT40+ 诊断

本次仅保留 fl01（node 1）作为 active target，Server native 的 `known-clients` 和 `client-target`、UFTP、HTTP 收尾均只针对 node 1；fl02/fl04 停服后恢复，不参与传输。

- 四板预检提交：`701963a`
- native SHA-256：`f7c73f41b730ddb3547e1730b5a5b7b40a55dd7a8e3e5f265dcc6563c6c432b1`
- 射频：157 / HT40+ / 实际 `-14 dBm`
- MCS：Server 3，fl01 上行 4
- 负载：4 MiB
- 运行记录：[20261028T042122Z-single-fl01-157-ht40](runs/20261028T042122Z-single-fl01-157-ht40/)

## 结果

- 下行完整接收，大小 4 MiB，SHA-256 与源文件一致。
- 上行 HTTP 201，服务端回传文件大小和 SHA-256 一致。
- fl01 上行耗时 3.682 秒，约 9.11 Mbps。
- Server 持续收到 `PKT_SRC node1`，接受 READY 并发出连续 grant；单节点 active queue 为 `[1]`。
- 传输及 runner 清理无错误。实验结束后另行按标准流程重启四端并确认全员 READY。

该结果证明在相同 Server/native 制品和 157/HT40+ 参数下，单客户端控制、授权和 4 MiB 双向传输可以正常闭环。
