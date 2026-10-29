# 四机 40 MiB 双向通信冒烟

时间：2026-10-29 09:39 UTC 开始，运行标签 `20261029T093923Z`。

## 测试边界

- 拓扑：fl00 Server，fl01、fl02、fl04 Client。
- 发布提交：`202f532632aca15871bf4805b13832593cc8635b`。
- 射频：信道 149、HT40+、驱动读回 `-14.00 dBm`。
- 下行 MCS 3、上行 MCS 4、FEC 8/14、下行注入速率 15000 Kbps。
- 每个方向的单份载荷：`41943040` 字节（40 MiB）；源文件 SHA-256：`9fa46b7088bfb012fc433384c202058250927ce808c6b54e8af6b851bc3b0354`。
- 入口：`tests/demo/run_fl_demo.sh all`。测试前按约束暂停四端常驻 daemon，测试后恢复标准 daemon 拓扑。

## 结果

### 下行：通过

UFTP 组播耗时 61.585 秒。fl01、fl02、fl04 均收到 `41943040` 字节，三端 SHA-256 与源文件完全一致。

### 上行：失败

| 客户端 | HTTP | 大小 | 耗时 | 吞吐 | 结果 |
| --- | ---: | ---: | ---: | ---: | --- |
| fl01 / node 1 | 201 | 41943040 | 75.639 秒 | 4.44 Mbps | 通过 |
| fl02 / node 2 | 201 | 41943040 | 37.362 秒 | 8.98 Mbps | 通过 |
| fl04 / node 4 | 无 | 未提交 | 260 秒超时 | - | 失败 |

fl04 的 `http-sender` 在 `sendall()` 阶段抛出 `TimeoutError`，服务端没有收到完整的 node 4 文件，也没有生成成功提交结果。

独立遥测摘要：

- node 1：`out_packets=10723`，`out_bytes=42588574`。
- node 2：`out_packets=10707`，`out_bytes=42524862`。
- node 4：`out_packets=77`，`out_bytes=209870`。
- Server 日志持续记录 `PACKET_LOSS source_node=4`。

## 判断

更换 fl04 位置后，本次四机并发 40 MiB 冒烟仍然复现 fl04 上行失败。下行接收正常，fl01/fl02 上行正常，故障边界已经收窄到 fl04 上行数据交付或其与三机调度的交互；当前证据还不能单独区分射频、天线、USB/驱动和并发调度原因。

这次结果不是 FedAvg 训练验收，也不把前两端成功当作四机通过。

## 恢复

测试外层恢复钩子第一次错误地同时启动了互斥的 role/daemon units，现场证据保留在 `postflight.txt`。随后已停止全部相关 units 和残留 native 进程，只启动标准拓扑：fl00 的 `wfb-fl-server-daemon.service`，以及 fl01/fl02/fl04 的 `wfb-fl-client-daemon.service`。

纠正后的现场核验见 `corrective-restoration.txt`：四端每个 daemon 拓扑正确、每端只有一个 native 进程，Python 提交和 native SHA 一致，`iw` 均读回 149/HT40+/-14 dBm，Server 与三个客户端均恢复 `READY/IDLE`。

## 证据

- `runner.log`：完整冒烟入口输出。
- `run-artifacts/downlink_summary.json`：下行三客户端摘要。
- `telemetry.json`：Server 与三客户端 native 遥测解析。
- `client-artifacts/`：三客户端 PUT 结果、队列摘要和 native 日志。
- `preflight.txt`、`after-stop.txt`、`corrective-restoration.txt`：测试前、停服后和纠正恢复后的现场核验。
