# 08 纯空闲心跳被动观察：发送、grant、端到端交付与注册表

本轮未发任何训练、abort、射频变更、重启或探活报文，未部署/修改production。仅复用systemd、ss、journal、iw、native日志及AF_PACKET被动复制TUN既有数据包，四板独占观察110秒。抓包不设置promiscuous、不抢业务端口、不停止native，不向socket发送任何数据。进程退出后关闭观察socket，原daemon保持运行。AF_PACKET应用于TUN，不是无线设备；`socket_stats`四端丢包均为0。

主要发现：**本机HB持续生成，但fl01/02上行既有报文在client TUN至server TUN之间出现数十秒积压和批量交付；对应native授权发送停滞。**本窗口支持优先定位原生授权/调度/寻频反馈路径，不支持把纯空闲offline全部归因于torch导入、心跳发送线程完全停止或未来timestamp造成的统一replay拒绝。尚未区分实际积压发生在native队列、设备发送、射频到达或重组的哪一内部位置。

## 方法和时间

每端一个root观察进程，以AF_PACKET/SOCK_DGRAM/ETH_P_ALL绑定本端TUN，仅解析IPv4 UDP9000/9001，记录：

- 观察端本机monotonic、wall_ns、TUN packet_type、IP源/目标/ID、UDP源/目标端口、payload SHA-256及JSON正文。
- 每5秒读取既有native日志尾部的PKT、PKT_SRC、GRANT_FILTER、TOKEN_AUTH，读取TUN计数、daemon线程wchan、iw读回。server同频率GET IPC状态。
- 结束时读取AF_PACKET内核接收/丢包计数、ss和有效服务命令；只读补取server相同native单调时钟段的grant序号/目标。

服务端捕获窗口：2026-10-27 07:16:25.866～07:18:15.920 UTC，单机时长110.054秒；客户端各自单机时长110.04～110.09秒。wall clocks仍有数秒偏移，不用于直接算单向时延。逐端开始/结束及monotonic保存在 `native-thread-summary.json`。

心跳没有独立seq字段，使用精确payload哈希与`(node_id,timestamp_ms)`匹配；ACK正文只有`{"ack":true}`，没有心跳身份，因此用ACK本身的`(src,dst,sport,dport,IP ID)`匹配两端同一个ACK包，**不宣称某个ACK确认了某个HB**。client TUN能看到其他客户端的下行ACK副本，分析已按自己的目标IP过滤，不能把这些副本计作自己的确认。

源码 `_next_timestamp_ms()` 实际使用 `monotonic_ns//1000000`，不是Unix毫秒。API的 `last_heartbeat_timestamp_ms` 则来自server接收wall time，二者不能直接比较成未来时间。首次分析器曾尝试对两者减值，已纠正；保留的最终分析字段明确标注client monotonic，不把无意义差值作为结论。

## 可复核数量

| 节点 | client TUN观察到HB | server TUN观察到HB | 可与本窗口client发送匹配 | server ACK / 自己client观察到ACK | API OFFLINE样本/23 | 最大HB年龄 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| fl01 | 215 | 156 | 146 | 156 / 141 | 14/23 | 49.22s |
| fl02 | 175 | 100 | 92 | 100 / 84 | 11/23 | 63.33s |
| fl04 | 22 | 21 | 21 | 21 / 20 | 0/23 | 8.11s |

server端有一些HB来自观察开始前的client积压，因此server观察数可超过本窗口匹配数；client结束前发出的包可能仍在队列中。未匹配数不是永久丢包率，不将窗口边界或尚未交付的包记成“确定丢失”。

四端AF_PACKET的 `(received,dropped)` 为server(554,0)、fl01(450,0)、fl02(377,0)、fl04(269,0)。均成功读到双向数据，观察程序未崩溃或丢包。

## 单调时钟证明的积压与burst

不需要跨板时钟同步即可比较同一发送端timestamp跨度与同一接收端arrival跨度：

| 节点 | server第一包monotonic | burst包数 | server接收跨度 | client源timestamp跨度 |
| --- | ---: | ---: | ---: | ---: |
| fl01，第1次 | 70924.289947488 | 54 | 0.171541312s | 43.874s |
| fl01，第2次 | 70991.320217408 | 99 | 0.396077280s | 46.509s |
| fl02 | 70945.117481696 | 91 | 0.085386432s | 58.076s |

fl02第一组server接收出现在server观察开始约56秒后；期间client TUN确实持续观察到HUNTING HB。服务端不是在当时才收到新生成的单个HB，而是收到跨数十秒生成的旧HB批次。

按双向可匹配包估计monotonic时钟差，用户态接收采样/调度/时钟速率误差导致fl01原始非负时延约束有0.634ms不一致。`delay-analysis.py`保留此误差，使用双侧20ms测量余量提供**估计**，不是内核timestamp严格界：fl01最慢已匹配上行约46.36～46.40秒，fl02约54.94～54.98秒，fl04约5.25～5.29秒。结论主要依赖上述burst的同机跨度压缩，不能把20ms余量包装成严格时钟精度承诺。

已匹配ACK下行在该估计中通常为几十ms以内；未匹配ACK仍可能丢失或越过窗口。服务端收到HB后为同客户端目的端口发ACK的最近时间差全部小于8ms（fl01最大6.04ms，fl02 7.63ms，fl04 5.96ms）。burst中ACK没有HB序号，该nearest匹配只说明服务端持续处理回复，不证明每条业务消费完成。

## 原生grant与RX对应事实

TOKEN_AUTH字段顺序由e53de00 `v6_uplink.cpp`确认：accepted_events、rejected_events、authorized_sends、denied_sends。GRANT_FILTER为received、accepted、invalid_source、wrong_target、duplicate、stale、expired、invalid_session、retired_session。原始计数与PKT数组全部保留，不随意给未知字段起名。

fl01：

- relative 0～30秒，accepted_events固定36776、authorized_sends固定5994，denied_sends由3718517升至3718681；同时GRANT received由89181升至89242，wrong_target由52405升至52466，accepted不增加。证明收到别人的grant，但没有获得自己的有效授权，且有待发送被拒绝。
- relative约35秒，accepted_events变36780、authorized_sends变6082，随后server第一次burst到达。
- relative约55～100秒，authorized_sends固定6085，denied持续增长；relative约105秒authorized_sends跳到6209，与第二次burst对应。

fl02：

- relative 0～55秒，accepted_events固定30827、authorized_sends固定7396；denied从20943612增加到20943865，GRANT收到和wrong_target持续增长。
- relative约60秒，accepted_events升到30839、authorized_sends升到7570，与约56秒开始server burst相符（native按5秒快照，时间分辨率有限）。

server native窗口读到normal grant计数：node1=106、node2=186、node4=561。头约30秒以node4为主，node1首次grant anchor70922541ms（约relative33s），node2首次anchor70934542ms（约relative45s）。grant行本身没有timestamp，使用此前1秒native统计作为anchor，误差约1秒，详见 `server-native-window.json`。node2开始被server授权之后仍到约56秒才成批上行，说明“服务器发了grant”不等于客户端当时接收/接受/实际使用了该grant。

被动iw读回还看到fl01/02在157/149/153间变化，与HUNTING心跳一致；fl04窗口内维持157/IDLE。这是现有daemon自行寻频的行为，**观察程序没有调用set channel或其他射频写命令**。因此可能存在“丢ACK→HUNTING离开157→错过grant→积压→晚到ACK”的放大回路，但目前不能确定其最初触发是调度、方向性无线损失还是其他因素。

## replay、未来timestamp及线程阻塞的核查

server观察到各node HB的timestamp严格递增，无重复或乱序。client payload timestamp相对自己的观察monotonic均在过去，最近的也至少早0.29～0.46ms，没有本轮新产生的未来timestamp。它们与Unix wall time的巨大差异是时钟定义不同，不是pytest污染证据。

全部277条server HB到达之后的下一次API采样都已观察到该node registry最后HB推进到该到达或更晚，且每条对应server侧ACK。5秒采样不能逐条证明中间registry写入，但结合严格递增和已提交replay逻辑，**本窗口没有支持“持续到达但统一被旧未来watermark拒绝”**。未取得私有registry payload watermark快照，不能断言历史上从未有单条拒绝。

client发送的最大相邻间隔均约5.232秒（HUNTING期通常更密），没有在这些offline窗口出现“控制线程完全停止发送超过10秒”。server能在burst到达后毫秒级回复，也不支持此时其接收线程长时间卡死。线程wchan中的poll/futex只是瞬时等待位置，不能单靠它判死锁；现有数据排除的是本窗口的整体长停，不是所有可能的短暂锁竞争。

此外ACK无报文身份，迟到的`{"ack":true}`可能被另一个等待阶段消费，这一风险需要另行可复现检查；现有被动TUN只能证明包到了内核，不能证明哪一次recvfrom消费了它。

## 三个可证伪假说与下一个最小active probe

| 假说 | 本窗口证据与判断 | 可证伪条件 |
| --- | --- | --- |
| H1：上行授权/声明/调度或寻频反馈造成心跳积压 | 最支持。client持续有HB、无authorized_sends增量，随后grant授权和server burst同时出现；内部积压位置未定 | 若在offline窗口client可持续有效授权发送且server TUN低延迟收到同一HB，积压解释不成立，应转查接收/registry |
| H2：server未来timestamp watermark导致replay拒绝、HB不刷新 | 本窗口未支持：输入严格递增、client timestamp非未来、server接收后registry推进并回ACK | 若捕获到server已收到HB并回ACK但registry持续不更新，且payload timestamp≤实际watermark，则支持；若registry每次推进则否定该窗口的该原因 |
| H3：client/server控制线程阻塞或没发送 | 本窗口对长停不支持：client最大发送间隔约5.23s，server收到后<8ms回复 | 若client在native正常、有授权时超过10s完全无TUN发送且同进程线程卡在固定等待点，或serverTUN已有HB但其socket积压不消费，则支持 |

下一最小active probe建议（**未执行，不是继续发训练**）：为node2发3个带独立诊断序号和本机monotonic的极小UDP包，1Hz，从既有client TUN经现有native通路到server一个专用临时诊断端口；可回显同一payload，双端继续被动捕获。不要伪造HB、不要碰9000/9001、不要修改registry/timeout/无线，也不停止其他daemon。记录同期间READY/declaration、grant sequence与accepted/authorized_sends；若诊断包与HB同样在无授权期积压后成批交付，指向共用native授权/队列；若诊断包及时而HB不及时，转查控制socket/路由/消费路径。实验需由主线在确认临时端口后明确执行，当前只给方案。

若仍需更精确地区分native TUN读队列和radio发送，应先利用现有queue summary/日志能力核对可用性，再决定是否由主线增加最小提交级观测；不在现场私改安装源。

## 当前状态与边界

结束后server IDLE、无active job。最终一次API时node1/2 READY、node4 OFFLINE，说明波动并未因为观察而修复，不能报告全员持续READY。四端daemon PID仍168778/24102/15784/23591，NRestarts仍0；有效MNIST/workdir配置保持原值。没有要求用户实体操作的证据，本轮可继续软件与链路方向隔离。

原始证据：`flXX-capture.jsonl`、`capture-processes.json`、`flXX-post-observation.txt`、`status-final.json`。分析证据：`aligned-analysis.json`、`delay-bounds.json`、`native-thread-summary.json`、`server-native-window.json`。捕获与分析脚本均为小型标准库脚本，只有被动复制，不部署长期平台。所有历史故障保留，独立SHA256SUMS冻结本轮文件。
