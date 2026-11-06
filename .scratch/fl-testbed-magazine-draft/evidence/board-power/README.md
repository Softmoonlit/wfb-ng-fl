# fl01 心跳迟到定位

## 已确认的症状

fl01 标准重启服务后短暂 READY，随后再次 HUNTING。当前使用旧运行版本、Short GI、功率索引 14；尚未统一部署工作区 Long GI 修改。板级功耗仍为 40W，未执行 nvpmodel 切换。

## 同步被动抓包

执行：将 `heartbeat-capture.py` 分别通过本机 sudo 和 SSH sudo 在 fl00/fl01 并行运行 40 秒。仅被动读取 AF_PACKET，不向无线发送探测报文。各设备用自己的单调时间，按 heartbeat timestamp 标识配对，未作跨设备墙钟相减。

原始记录：`fl00-heartbeat.json`、`fl01-heartbeat.json`；统计：`capture-analysis.json`。

- 客户端发出 91 份请求，85 份 HUNTING、6 份 IDLE。
- 客户端共观察到 166 份 ACK，其中 63 份可配对到本窗口请求，其余可能对应窗口前请求。
- 63 份配对 ACK 中仅 7 份在 230ms 内到达；RTT 中位约 1309.9ms、最大约 7229.0ms。
- 两侧共同捕获到 59 份请求，服务端全部 ACK。样本处理耗时通常 3~33ms。
- 请求 8037301、8037531、8038827 等在客户端不同时刻发出，但在服务端 elapsed 9.702~9.703 秒批量到达。因此至少存在上行进入服务端 TUN 前的批量积压，不能归因于服务端没有 ACK。
- 部分请求声明的探测信道为 153/157，但随后收到 ACK 的 server channel 为 149。这些 ACK 不应被用来接受错误频道；寻频期间旧排队数据仍然抵达，是必须保留的事实。

## 当前故障边界

上行 TUN 发出至服务端 TUN 接收之间包括客户端 native 排队、授权发送、无线传输及服务端 FEC/顺序重组。尚未证明网卡硬件故障，也没有根据迟到结果放宽 ACK 截止。

源码复核发现确定缺陷：`src/v6_uplink.cpp` 客户端循环仅在 pending 队列为空时执行 `flush_data_block_if_due`；pending 非空且未授权时绕过到期封口并反复使用已过期 deadline 执行零超时 poll，导致忙循环。pending 非空且已授权时也不优先封口。primary fragment 本身会立即发出，不能误称“攒满8个才首次发送”；如果缺失片段未能及时通过 parity 修复，接收顺序重组可能进一步阻塞。该缺陷与抓包的积压现象相容，但尚未用受控复现及修复前后实验证明它解释全部现场延迟。修复必须同时处理封口与授权边界，不能简单让未授权客户端随时发送 parity。

## 物理18dBm核实结果

驱动正 `1800` 路径设置软件 `CurrentTxPwrIdx=18` 并取消 override，RTL8812A 实际HAL计算不将其作为物理18dBm目标；负 `-1800` 是强制TxAGC索引18。`get_txpower` 读软件状态，不能证实实际物理功率。因此这两条命令均不能构成“实测18dBm已达成”的证据。依据为 `/home/fl/projects/rtl8812au/os_dep/linux/ioctl_cfg80211.c`、`hal/rtl8812a/rtl8812a_phycfg.c` 与README固定索引说明。

fl01 管理接口当前关联手机热点，信号 -37dBm、协商速率 780/866.7Mbps；这只说明即时关联状态，不证明没有管理网间歇丢包。内存 7619MiB，可用约 6083MiB，swap 未使用；不能仅凭这个数值保证 ResNet-18 训练显存足够。

## 功耗与射频语义修正

用户明确 25W/40W 为 Jetson 板级模式；18dBm 为期望无线发射功率。驱动索引不直接等于物理 dBm。当前未改变板级模式，也未声称物理18dBm已达成。
