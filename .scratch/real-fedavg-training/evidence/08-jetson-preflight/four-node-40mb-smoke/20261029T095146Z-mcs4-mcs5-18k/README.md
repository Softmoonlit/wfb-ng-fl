# 四机 40 MiB 双向冒烟：fl04 新网卡与提高 MCS

时间：2026-10-29 09:51 UTC 开始，运行标签 `20261029T095146Z-mcs4-mcs5-18k`。

## 测试条件

- 拓扑：fl00 Server，fl01、fl02、fl04 Client。
- 四端运行提交：`202f532632aca15871bf4805b13832593cc8635b`。
- fl04 新网卡：接口 `wlxfc221c300cbb`，MAC `fc:22:1c:30:0c:bb`，USB `0bda:8812 RTL8812AU`，路径 `platform-3610000.usb-usb-0:1.3:1.0`。
- 射频：信道 149、HT40+、驱动读回 `-14.00 dBm`。
- 本次仅临时配置：下行 MCS 4、上行 MCS 5、UFTP 18000 Kbps。
- 原常驻配置未改动；测试结束后 API 读回仍为下行 MCS3、上行 MCS4、UFTP 15000 Kbps。
- 每个方向的单份载荷：`41943040` 字节（40 MiB）；源文件 SHA-256：`9fa46b7088bfb012fc433384c202058250927ce808c6b54e8af6b851bc3b0354`。

## 结果

### 下行：通过

- 三个客户端均收到完整文件，SHA-256 全部一致。
- UFTP 耗时 `35.734s`，空口平均吞吐 `9.39 Mbps`。

### 上行：通过

| 客户端 | HTTP | 大小 | 耗时 | 吞吐 | SHA |
| --- | ---: | ---: | ---: | ---: | --- |
| fl01 / node 1 | 201 | 41943040 | 195.475 秒 | 1.72 Mbps | 一致 |
| fl02 / node 2 | 201 | 41943040 | 82.182 秒 | 4.08 Mbps | 一致 |
| fl04 / node 4 | 201 | 41943040 | 88.989 秒 | 3.77 Mbps | 一致 |

三客户端总回传耗时 `196.126s`，聚合有效吞吐 `5.13 Mbps`。TCP 重传 0 次，TUN 反压 PAUSE/RESUME 均为 0。

## 遥测

- 物理收包：`88878`，物理丢包：`4878`，丢包率 `5.20%`。
- FEC 恢复：`12000`，恢复率 `71.10%`。
- node 1：`out_packets=10787`，`out_bytes=42823878`。
- node 2：`out_packets=10738`，`out_bytes=42615981`。
- node 4：`out_packets=10732`，`out_bytes=42617687`。

上一次旧网卡、MCS3/MCS4、UFTP 15000 的四机测试中，fl04 只有 77 个交付包、约 0.21 MB 并最终超时；本次 fl04 已达到与其它客户端相同量级并成功完成，说明新网卡在本次联合参数条件下消除了之前的严重上行失败现象。

## 参数解释

下行从 MCS3/15000 Kbps 提高到 MCS4/18000 Kbps 后，40 MiB 下行由 `61.585s` 降至 `35.734s`。上行从 MCS4 提高到 MCS5 后虽然三端均成功，但丢包率上升至 `5.20%`，fl01 上行耗时 `195.475s`，明显慢于之前 MCS4 条件下的 `75.639s`；因此不能把更高 MCS 视为上行性能提升。

本次同时更换了 fl04 网卡并调整了 MCS/速率，结论是联合结果，不足以单独量化网卡和 MCS 各自的因果收益。后续对照应保留新网卡，仅单独回到上行 MCS4 测量。

## 恢复与证据

测试前暂停了四端常驻 daemon，测试结束后恢复标准拓扑：fl00 仅运行 `wfb-fl-server-daemon.service`，fl01/fl02/fl04 仅运行 `wfb-fl-client-daemon.service`。四端身份、单 native 进程和 `iw` 读回均通过，Server 与三个客户端恢复 `READY/IDLE`。

- `runner.log`：完整冒烟输出。
- `run-artifacts/uplink_summary.json`：三端 PUT 结果和 SHA。
- `run-artifacts/telemetry.json`：Server 遥测摘要。
- `preflight.txt`：新网卡和本次参数的前置现场。
- `postflight.txt`：恢复后的服务、身份和射频读回。
- `cluster_nodes.mcs4-mcs5-18000.conf`：本次临时配置副本。
