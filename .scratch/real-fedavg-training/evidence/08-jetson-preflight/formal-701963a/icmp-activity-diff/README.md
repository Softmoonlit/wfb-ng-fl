# 持续上行activity三阶段最小干预诊断

本轮只做用户授权的单次baseline→activity→post，每端每阶段捕获约45秒，没有重复矩阵、训练或标定。全程四端Python为701963ac254261c31c56a074d06966a9217346f6，运行native同为f7c73f41b730ddb3547e1730b5a5b7b40a55dd7a8e3e5f265dcc6563c6c432b1；daemon/native PID前后严格相同，未重启/改生产配置/RF/功率/MCS/timeout/物理位置。

结论：持续上行activity使fl02/fl04的READY refresh持续、静默移除显著减少；fl01已经失去有效授权，在activity期间未恢复，所有probe报文积压，停止probe后才成批到达。post fl01现场命中pending unauthorized FEC due/zero-timeout各4952次，但发生在积压恢复而非最初失联，不能据此定为所有故障唯一根因。本实验不是验收或修复。

## 运行与恢复边界

脚本 `run-three-phases.py` 只用标准库；节点脚本 `phase-node.py` 通过AF_PACKET被动复制TUN报文，不promiscuous、不绑业务端口。activity在各客户端执行：

```text
/usr/bin/ping -n -I fl-cN -i 0.25 -c 180 -w 45 10.80.0.1
```

没有设置-s，payload默认56B。-w45仅限制本次ping总运行时间，不修改生产timeout。每端capture有begin/end和owner-cleanup-complete，所有packet socket丢包为0。实际capture约45.04～45.10秒；外层每阶段约47～49秒还包括SSH启动、wait与状态读取。每次工具调用均少于60秒。

节点finally向自己Popen的ping PID发SIGINT、communicate/wait，异常时按PID和/proc starttime只回收本次ping/runner；不碰任何service。外层finally等待全部自有进程。记录的所有runner/ping PID+starttime在末次检查均不存在，见flXX-owned-pid-check.json。

## 三阶段单机指标

HB发送gap按client monotonic，接收gap按server monotonic；最大server静默列**含窗口边界**，避免零到达或首包晚到被“相邻gap小”掩盖。精确相邻报文gap也在comparison.json。HUNT是HB报文比例，不是状态持续时间比例。

| phase | node | max源HB gap s | max server观察静默 s | HUNT报文 | OFFLINE采样 | READY入队/refresh/移除 | accepted grant增量 | authorized/denied增量 |
| --- | --- | ---: | ---: | ---: | --- | --- | ---: | --- |
| baseline | fl01 | 5.232 | 29.364 | 94.9% | 4/10 | 3/0/4 | 38 | +3/+166 |
| baseline | fl02 | 5.232 | 5.339 | 0.0% | 0/10 | 9/0/8 | 139 | +9/+18 |
| baseline | fl04 | 5.211 | 5.212 | 0.0% | 0/10 | 9/0/9 | 158 | +8/+18 |
| activity | fl01 | 0.600 | 45.046 | 100.0% | 10/10 | 0/0/0 | 0 | +0/+533 |
| activity | fl02 | 5.119 | 5.124 | 0.0% | 0/10 | 0/94/0 | 171 | +184/+247 |
| activity | fl04 | 5.125 | 5.127 | 0.0% | 0/10 | 1/91/1 | 172 | +185/+171 |
| post | fl01 | 5.205 | 28.758 | 95.3% | 6/10 | 4/1/3 | 51 | +488/+5059 |
| post | fl02 | 5.231 | 5.211 | 0.0% | 0/10 | 8/0/9 | 169 | +8/+19 |
| post | fl04 | 5.209 | 5.209 | 0.0% | 0/10 | 9/0/9 | 106 | +9/+20 |

READY/silent_remove事件时间使用其前一个native stats约1秒anchor，因此阶段边界事件有约1秒误差。grant增量来自各阶段同机native GRANT_SEQ首末完整秒样本，覆盖约43秒；不与整45秒server发送事件数直接算丢包率。

activity fl01无一条HB到达server，相邻接收gap为null，server整个45秒观察窗无HB到达；其registry年龄最高77.39秒。post fl01首包约28.76秒才到，随后307条HB到达，不能以其相邻HB gap只有5.21秒声称没有失联。post registry最大年龄106.48秒，包含此前baseline/activity连续静默。

## ping loss和RTT

| node | 实际发送/收到 | loss | RTT min/avg/max/mdev ms |
| --- | --- | ---: | --- |
| fl01 | 175/0 | 100.0% | 无回复，无RTT |
| fl02 | 180/178 | 1.11111% | 1.797/48.862/326.946/64.232 |
| fl04 | 180/176 | 2.22222% | 1.559/42.759/378.788/56.443 |

fl01命令请求180包，但45秒截止和实际调度下只发出175包；没有补发或重跑。三个ping返回码均为1（截止时仍有未收到的包），节点采证runner成功退出并保存摘要，不能因ping有loss把它误记为脚本没执行。原文为fl01/fl02/fl04-ping-output.txt，完整命令和PID在activity-flXX.jsonl。

activity期间TUN request在client分别175/180/180包，server活动窗口内分别0/176/165；client echo分别0/178/176。server/client捕获启动/结束错开约1～2秒，不能把各窗口计数差当作精确空口丢包率，ping自身摘要是本次进程层结果。

## 迟到probe和FEC seam现场信号

post没有任何新的ping进程，但server看到161条来自fl01 activity的旧ICMP request；通过ICMP identifier+sequence逐一匹配同一批旧probe，161条在server monotonic108907.039036224～108907.133626528内到达（约94.6ms）。fl01 TUN还收到131条迟到echo，ping已退出，故这些回复不计入ping已保存的0 received。匹配原文见fl01-post-late-probe-requests.json。

post fl01同机native 108297439→108298439ms（约本阶段27.92～28.92秒）出现：

```text
CLIENT_LOOP pending_unauthorized_fec_due: 0 -> 4952
CLIENT_LOOP zero_timeout_polls:          0 -> 4952
TOKEN_AUTH 6103:0:1000:7358
GRANT_SEQ  6103:16028
RX_BATCH   235:597:12404:399:12
```

该秒原始字段保存在fl01-post-loop-positive.json。计数证明实际进入过“queue有pending、无授权、FEC idle deadline已到”分支且反复zero-timeout poll；没有精确queued_packets观测，不补报深度。此信号发生在fl01恢复grant并释放此前积压的附近，尚不足以证明它导致了最初activity完全收不到grant。baseline/activity时fl01 CLIENT_LOOP两计数均不增长，RX_BATCH最大不足1ms，故应区分最初授权失败与后续FEC seam放大效应。

其余CLIENT_LOOP全阶段均为0。RX_BATCH最大us：baseline fl01/fl02/fl04=644/1010/67；activity=627/471/76；post=612/458/128。它们只描述已完成batch，本次没有秒级batch长停证据。

## 对照解释与下一步

fl02/fl04 baseline已经相对稳定，activity保持无HUNTING并增多READY refresh（分别94/91），减少silent_remove（0/1）；这支持持续上行活性能维持调度活动身份，但不是“使所有节点恢复”的充分条件。fl01 activity既有TUN发包、却accepted/authorized均0，没有READY入队或refresh，wrong_target继续增长；需要继续分析READY空口到达与频道/有效grant链路，不能靠继续增加流量或功率恢复。

post中probe停止但旧包仍在链路排队，所以不是已经排空的稳态对照。禁止把结束时全员READY当作修复通过。下一步最小动作是**离线回放本轮fl01 activity到post约29秒的同seq grant、READY/silent_remove、ICMP和CLIENT_LOOP转变**，定位先后；本轮已有现场FEC seam阳性，无需立刻再加probe。任何行为修复须先主线提交、冻结统一发布，再另行验证，不能现场私改flush/授权/超时。

## 结束状态与证据

最后只读API：server IDLE、无active job、1/2/4均READY；对应final-status.json时间点。四端daemon/native PID、Python701、nativeSHA前后不变，身份和实际加载路径复核保存在flXX-identity-before/after.json及identity-gate.json。没有训练、标定或其他服务动作，所有自有诊断进程已回收。

每阶段原始控制与ICMP：baseline/activity/post-flXX.jsonl；native原始窗口：flXX-native-full-window.json（各板/srv/wfb-ng-fl/{server,client}/wfb_uplink.log只读截取）；机器可读指标comparison.json。脚本与异常恢复逻辑原样保存，SHA256SUMS冻结所有小型证据。
