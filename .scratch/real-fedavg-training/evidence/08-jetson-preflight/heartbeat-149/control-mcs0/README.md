# 空口 READY/GRANT 固定 MCS0 的四端实测

## 实施与发布

提交 `bfb0eeca016c205d9517b2ef8d9616501feccac5` 固定 raw READY/GRANT 为 MCS0；控制帧带宽/GI与目标无线配置同步，数据MCS保持方向配置。TUN/IP心跳及ACK仍走数据面，上行MCS4、下行MCS3。没有更改5秒空闲心跳、230ms单次ACK等待、连续两次miss寻频、Server10秒OFFLINE或任何授权/FEC/调度期限。设计依据见 `docs/design/链路层空口调度与反压.md`。

TDD的RED/GREEN、最终构建日志在本目录。双轴审查发现的启动观测与重复发布native身份核验已修复，复核无阻断。repeat-helper的本地fake proc/文件系统测试也已保留；随后实际fl00只读接受旧607c064 wrapper。未混入未提交源码。

四端统一发布证据：上一级 `release-bfb0eeca016c-20261028T124504373133Z/`。四端运行同提交Python、同一构建native，依赖ABI一致，实际 `iw` 均为149/HT40+/-14.00 dBm。各端 `*-mcs-readback.txt` 含历史日志，**末次v6_config及后续frame段**确认 control=0、Server data=3、Client data=4；不要把历史启动段误作当前运行。

## 120秒结果

使用 `observe-control-mcs0.py --output .../control-mcs0/observation --seconds 120` 只读采样。120.45秒共121份：

| 节点 | 非READY样本 | 最大心跳年龄 | 行为 |
| --- | ---: | ---: | --- |
| fl01 | 0 | 5.19秒 | 全程READY |
| fl02 | 0 | 8.40秒 | 全程READY |
| fl04 | 8 | 12.64秒 | OFFLINE、HUNTING后恢复 |

fl04第52.20秒被Server标为OFFLINE（age10.63秒，仍申报IDLE）；第55.21秒观测到CONNECTING/HUNTING；第60.23秒恢复READY/IDLE/149。结果 `passed=false`。完整状态见 `observation/summary.json`、`samples.jsonl`，两端85秒被动抓包同时保留。

单独降低授权调度控制帧MCS没有消除现场失稳，也没有将心跳/ACK改为MCS0。本实验不能据此排除上行数据RF丢失，不能证明fl04网卡坏。它与先前单客户端、功率18档对照共同说明需要独立审视心跳监督的容错：空闲约5秒一次、服务端10秒静默即离线，一次心跳丢失或排队就可能跨过离线边界；客户端连续两次230ms miss即换频又会放大恢复。两个监督判据未统一。

未启动训练、HT20校准或四机冒烟。最后采样三个客户端均恢复READY，不将这一瞬时状态当作稳定门禁通过。
