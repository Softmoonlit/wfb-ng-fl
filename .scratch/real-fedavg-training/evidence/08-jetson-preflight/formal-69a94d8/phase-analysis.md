# 69 数量差分：单机 monotonic 指标

四阶段各约80秒，原始矩阵已经完成，本次仅离线分析已有文件，没有重跑。server始终未重启；阶段结束finally恢复所有客户端，结束快照全员READY。此前四端Python为69，但native尚未统一（fl00 8ddb、客户端b4f）；因此该矩阵是历史对照，不能当作统一native后的复验。随后统一native的来源和SHA见 [native-unification/README.md](native-unification/README.md)。

| phase | node | max HB发送间隔 s | max server HB接收间隔 s | 最近ACK median/max ms | offline采样 | hunt报文比例 | accepted grant增量 |
| --- | --- | ---: | ---: | ---: | --- | ---: | ---: |
| all3 | 1 | 5.248 | 9.242 | 185.47 / 447.52 | 0/17 | 46.4% | 133 |
| all3 | 2 | 5.232 | 9.367 | 178.79 / 4346.18 | 2/17 | 61.9% | 211 |
| all3 | 4 | 5.231 | 9.500 | 184.76 / 4355.39 | 0/17 | 44.8% | 219 |
| only-fl02 | 2 | 5.232 | 5.234 | 7.17 / 76.69 | 0/17 | 0.0% | 515 |
| fl01-fl02 | 1 | 5.249 | 18.887 | 159.59 / 4355.43 | 2/17 | 71.7% | 252 |
| fl01-fl02 | 2 | 5.232 | 10.413 | 161.93 / 256.62 | 0/17 | 46.7% | 305 |
| restore-all | 1 | 5.232 | 10.411 | 183.73 / 4345.20 | 0/17 | 0.0% | 175 |
| restore-all | 2 | 5.232 | 10.415 | 185.23 / 313.85 | 0/17 | 0.0% | 253 |
| restore-all | 4 | 5.234 | 9.350 | 186.68 / 4359.62 | 1/17 | 44.4% | 142 |

## 统计口径与限制

- HB发送间隔在client本机monotonic计算，HB接收间隔在server本机monotonic计算，不直接相减不同机器wall clock。最大间隔只覆盖两条已观察报文之间，不含捕获前后边界。
- ACK正文没有HB序号/时间戳，表中延迟是本机“发送HB后、下次HB前首个自己目的端口ACK”的观察关联，可能是旧ACK，不是严格RTT/真正确认。4.3秒值也可能是迟到ACK，不能据此证明控制recvfrom等待了4.3秒。
- hunt比例为HB报文数比例，不是HUNTING停留时间比例（HUNTING发包远密于IDLE）。
- grant增量来自捕获开始之后、native统计时间戳去重排序后的累计GRANT_FILTER accepted差值，约覆盖75秒有效统计窗口。排除停止/启动后日志尾部混入的旧native计数，避免负增量。不能与整80秒精确grant发送总数直接相除。
- API每5秒采样，共17个点。重新启动fl01/fl04时server保留先前停止阶段的陈旧HB，最初OFFLINE不是新阶段运行故障。fl01-fl02阶段node1两个OFFLINE样本中只有1个在本阶段首个HB更新后；restore-all的node4一个OFFLINE样本全部在首个本阶段HB之前。all3 node2两个OFFLINE也在该阶段首个新HB更新之前。
- 最大registry年龄：all3为node1 8.70/node2 15.81/node4 8.64秒；only-fl02为4.99秒；fl01-fl02 node1 93.55秒含此前停服，node2 8.76秒；restore-all node1 8.71/node2 6.71/node4 172.98秒（node4含此前停服），不可把93/172秒标成阶段内新故障。

## 对照说明

only-fl02窗口内无HUNTING，HB接收节奏约5.23秒，ACK最近关联median7.17ms/max76.69ms，accepted增量515，明显好于该矩阵all3。加回fl01后两者都出现HUNTING，node1有本阶段首个HB之后的OFFLINE样本和18.89秒接收间隔。恢复三端后node1/2在该短窗口没有HUNTING，说明“客户端数量”不是决定性充分条件，存在初始队列/授权/频道状态的影响。不能据单次矩阵断言多节点必然失败，也不能以最终READY掩盖中间延迟。

全部精确值、离线区间monotonic、ACK关联计数、grant/filter/TOKEN_AUTH增量在 `phase-monotonic-metrics.json`，脚本为 `analyze-phase-monotonic.py`。原始capture、phase-before/after、finally日志不修改。
