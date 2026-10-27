# 同一native下fl02 OFFLINE窗口85秒被动捕获

四板运行69a94d84提交且运行中native同为SHA `8ddb419be0c99e174eeb91239092f266398ea248e0c3c71eff643e28bbcc08e0` 后，仅在server fl-s与fl02 fl-c2复制现有控制报文，并只读提取相同单机monotonic范围native日志。没有训练、stop/start、改RF/timeout或主动发探活，没有重复数量矩阵。

实际server捕获时长85.047秒，server/client packet socket丢包均0，结束标记均存在，观察进程正常退出。native单机时间锚点来自每秒stats，精度约1秒；grant_accept有自身expires_at_ms，减120ms为client接受时刻估计。不能将server/client不同monotonic直接相减。

## 结果

| 指标 | fl02 / server读回 |
| --- | ---: |
| client HB / server HB | 63 / 69 |
| 与本client窗口payload匹配的HB | 52 |
| client最大HB发送间隔 | 5.231384秒 |
| server最大HB接收间隔 | 14.852231秒 |
| server离线采样 | 4/18 |
| server最大最后HB年龄 | 21.08秒 |
| HUNTING报文比例 | 48/63 = 76.19% |
| HB频道157/149/153 | 33 / 14 / 16 |
| 最近ACK观察延迟median/max | 185.972ms / 4346.683ms |
| server node2 grant / client grant_accept | 122 / 97 |
| 同grant序号匹配 | 97 |
| server node2 ready_accept / silent_remove | 13 / 13 |

ACK不含HB序号，因此最近ACK关联不是准确RTT，4.3秒可能对应更早报文，不能当作一次控制socket等待时长。server收到69个HB其中包含capture开始前积压，client发送未匹配可能仍在队列或越过窗口，不能直接计算丢包率。

server第一次burst在monotonic90467.809832224开始，0.338426秒收43个HB，源timestamp跨度32.175秒；第二次在90514.273308704开始，0.009972秒收16个，源跨度4.603秒。说明同native统一后仍存在既有HB积压后成批交付，不是安装SHA差异充分解释了症状。

## 单grantseq链路

完整122条server grant及其client匹配在 `analysis.json:grant_sequences`。例如：

- seq8565～8574期间server日志向node2发grant；本窗口client未见对应accept，不能仅由日志缺失断言帧未到达或原因。
- seq8575：server日志anchor90467221ms，`active_queue=[2]`；client `grant_accept expires_at_ms=83089226`，接受时刻约83089106ms。两者通过同一seq建立身份匹配，不用wall time猜配。
- seq8577/8578亦匹配，client接受时刻约83089366/83089496ms。
- client初段TOKEN_AUTH accepted保持1706，authorized_sends保持1686，denied增加；约client83093秒快照accepted升1712、authorized_sends升1727，与第一burst对应。

server对node2的silent_remove包括：554ms/4 silent grants、647ms/2、517ms/3、779ms/5、3121ms/23、3298ms/24、364ms/2等（不是server Python10秒阈值）。在node只有静态低频HB、短时没有新数据时，active membership可被移出，之后ready_accept再入队；这可以是调度设计正常反应，也可能与HUNTING/READY报文丢失形成反馈，不能仅凭移除次数判bug。

client GRANT_FILTER的stale/expired/session拒绝没有显示为本次主增量；收到对其他node的grant（wrong_target）持续增长。node2不是整个RX完全停了，而是其有效授权与上行交付间歇不足。

RX_ANT原文保留在两端native-window。样例server接收5785MHz/MCS4/40MHz，天线1 RSSI约-48/-47/-46；client接收5785MHz/MCS3/40MHz，天线1约-62/-61/-60；这只是对应统计桶样例，不是每个grant的RSSI或整个窗口射频质量结论。RF波动/瞬时丢失未被排除。

## 与本地纯loop结合后的判断

既有native-loop已验证 pending_packet非空且unauthorized分支跳过到期FEC flush，且到期deadline可以产生zero-timeout poll忙循环；这是独立代码路径缺陷候选。可是TX每个primary fragment立即inject，RX可连续交付已到primary，并非必须凑满8个primary才交付。不能把“FEC未close”直接等同于所有HB必然被缓存。

本窗口缺少pending队列长度、FEC fragment_idx/deadline_due/flush调用次数、每次pcap batch处理时长和grant收到但未接受的逐帧原因，故不足以将现场积压归为FEC/授权自锁。现有证据更具体指向：持续生成HB、部分grant无法匹配接受、READY/静默移除反复、HUNTING改变频道、取得有效授权后burst；这仍是反馈链，不是唯一根因证明。

## 下一最小动作建议（未执行）

优先由主线为native提交**只观测、限频日志**，不先改timeout/调度/FEC行为。需要的最小差分：

1. client每秒记录pending_packet/queued_packets、FEC fragment_idx、flush_deadline_due、due但unauthorized跳过flush次数、zero-timeout poll次数；不要逐包日志造成扰动。
2. client每批pcap记录处理帧数和elapsed_us/max（counter聚合），grant seq记录accept/reject及原由；server READY/grant/silent_remove附本机monotonic而非只有上一个stats anchor。
3. 使用已存在授权的同版规则冻结提交、同一native构建后，仅对fl02做一次短纯idle观测即可区分：due+pending+noauth持久成立→支持flush seam；pcap batch耗时异常而队列无该重叠→支持接收饥饿；缺READY/缺grant接受且批次短→优先声明/授权/寻频反馈。

不要为观察调用FEC flush或修改授权规则；若观测需要改变代码，先给主线提交和审查，不私改安装源。备选更轻的主动probe是一个独立临时端口的极小带seq UDP，经同一TUN测排队；但它无法区分FEC seam与pcap batch，故观测差分更直接。本轮未启动probe或训练，不要求用户实体操作。

原始文件：`fl00/fl02-capture.jsonl`、`fl00/fl02-native-window.json`。分析：`analysis.json`，含单grant序号对照、API及counter时间线；脚本为 `capture-tun.py`、`extract-native.py`、`analyze.py`。本目录独立SHA256SUMS冻结小型证据。
