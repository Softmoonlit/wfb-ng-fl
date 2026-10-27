# 701同native的85秒idle故障观测

仅在server fl-s及fl02 fl-c2被动复制既有UDP9000/9001报文，记录本机monotonic、payload哈希、HB timestamp及ACK IP ID；同步读取native日志、iw、thread/TUN计数及server IPC。没有生成控制数据，没有训练或标定，没有stop/start、改RF或timeout。两端capture有end记录，实际85.051秒，packet socket丢包均0。

采证前四端Python提交均为701963ac254261c31c56a074d06966a9217346f6，四端运行native同为f7c73f41…，不存在Python69/native701混装。身份来源见上级README。

## 核心指标（同机统计）

| 指标 | fl02 | server |
| --- | ---: | ---: |
| 本机最大HB发送/接收间隔 | 5.238046732s发送 | 13.438430208s接收 |
| 观察HB数量 | 41 | node2 34 |
| payload匹配数量 | 34 | 34 |
| RX_BATCH覆盖统计秒数 | 85 | 85 |
| RX_BATCH总批次/包数 | 637 / 1003 | 175 / 504 |
| RX_BATCH总处理us | 38225 | 7419 |
| RX_BATCH最大单批us | **523** | **161** |
| RX_BATCH最大单批包数 | 13 | 9 |
| CLIENT_LOOP pending_unauthorized_fec_due | 0→0，**增量0** | 无该client字段 |
| CLIENT_LOOP zero_timeout_polls | 0→0，**增量0** | 无该client字段 |
| GRANT_SEQ accepted count | 163→319，**增量156** | — |
| GRANT_SEQ最后接受seq | 1212→1917 | — |
| TOKEN_AUTH accepted/rejected增量 | +156 / +0 | — |
| TOKEN_AUTH authorized/denied增量 | **+42 / +156** | — |

RX_BATCH是每秒清零的完成批次统计，本表对每个native时间戳去重后汇总；CLIENT_LOOP/TOKEN_AUTH/GRANT_SEQ是进程生命周期累计计数，以本窗口第一/最后样本相减。不能把accepted事件数当作data发送包数，一次grant可允许多包或没有待发包。

API共18个样本，node2 OFFLINE为1个，最大HB年龄12.06秒。HUNTING心跳25/41（60.98%），IDLE16；频道字段157/149/153分别25/8/8。自动HUNTING切频道来自原daemon，本轮观察程序未写射频。

最近ACK关联median185.752ms/max4354.374ms。ACK正文只有ack=true，没有HB序号，因此这个关联不是严格RTT，也不是证明客户端单次recvfrom等待4.35秒。详细原始对照见 `analysis.json`。

一次server burst从monotonic107007.17256272开始，21.383ms内交付22个心跳，源timestamp跨度8.125秒。表明同native后仍有上行积压交付，不是停止发送或更换native就自动解决。

## queue非空对应窗口

`TOKEN_AUTH.denied_sends`仅在native源码 `pending_packet!=NULL && !authorization_state.is_authorized(now_ms)`分支增加。因此本窗口有18个相邻约1秒区间满足authorized不变、denied增长，可确认该秒内出现过queue非空且无有效授权，但**没有逐包队列深度**，不能推断整个秒始终有pending，不能补报queued_packets具体值。

例如client native 99619860～99626863ms之间连续7个区间，authorized增量0、denied依次+6/+4/+6/+5/+6/+6/+7。CLIENT_LOOP的due及zero-timeout计数此段仍为0。全部18个区间在 `token-auth-deltas.json`。

这在本次故障窗口反驳“queue非空无grant时必须触发FEC到期跳过/忙循环”的现场预测，但不撤销既有本地red loop揭示的独立代码路径风险：本次可能根本没有已开FEC block等待flush。同样RX_BATCH每秒完成最大523us，且native每秒都继续输出统计，不支持本窗口由秒级pcap batch长阻塞触发。

## GRANT_SEQ与READY/silent_remove

从两端既有原生日志提取捕获窗口±2秒的grant/declare等事件（普通事件使用此前stats timestamp作约1秒anchor）：server向node2有195条grant日志、client有168条accept，168个seq都可匹配server发出日志。这个扩展窗口事件数与严格样本段GRANT_SEQ增量156覆盖范围不同，不混用为丢包率。

完整单seq对照在 `analysis.json:grant_sequences`；所有未匹配仅表示本扩展日志范围内缺accept，不能直接称空口丢包，仍需看拒绝原因/频道/接收时机。server ready、silent_remove及RX_ANT原文在 `fl00-native-window.json`，client accept/declare/GRANT_FILTER和RX_ANT在 `fl02-native-window.json`。

现有证据把候选收窄为READY输入/有效grant交付/静默移除与HUNTING反馈，而非把2.2秒导入、FEC seam或pcap batch当作所有idle失联的唯一根因。native序号与HB payload身份可直接供diagnose08-startup离线分析。

## 下一最小动作建议（未执行）

先离线对齐已保存的单grantseq和READY/silent_remove时间线、230ms等待与现有调度pureloop，检查移除前是否已有足够的client数据/READY到达，而不是再跑数量矩阵或训练。本窗口计数已能否定两条候选，暂不需要再加FEC计数。

若必须增加现场观测，最小差分应精确记录server READY/data refresh的monotonic和node、client每个未接受grant的source/target/session/sequence拒绝原因，以及grant到达时本机频道/状态；保持行为不变、限频聚合，由主线先提交再统一发布。不能通过延长timeout、降低MCS或改硬件制造通过。

## 结束现场

capture均正常结束，原native/daemon PID未因本轮观察改变。最后API为IDLE、无active job、1/2/4均READY；四端服务active、NRestarts0，无部署/采证后台残留。对应 `final-status.json` 与 `flXX-final-processes.txt`，只说明该时刻恢复，不声称持续稳定。

## 原始日志位置

现场持续日志（会继续追加）：

- fl00：`/srv/wfb-ng-fl/server/wfb_uplink.log`
- fl02：`/srv/wfb-ng-fl/client/wfb_uplink.log`

冻结窗口（仓库小型证据）：本目录 `fl00-native-window.json`、`fl02-native-window.json`；包含原始native日志行。双端HB/ACK为 `fl00-capture.jsonl`、`fl02-capture.jsonl`。新增字段汇总为 `native-observation-summary.json`，授权与queue分支区间为 `token-auth-deltas.json`，整体对齐为 `analysis.json`。不需要等待/转发即可由共享工作区子代理直接读取。
