# 工单09：四次首点尝试诊断与清理回执

当前结论：**未取得任何完整三客户端成功会话，不交付HT20速率范围，不接表。已停止新增完整会话，不启动第五次matrix。** 当前四板仍使用已部署03版本99865ef；没有部署/重启，没有改产品代码、baseline、工单状态或commit。全部源bin和客户端临时/received制品位于各端 `/srv/wfb-ng-fl/calibration/09/<run>`；仓库仅保存小日志、脚本、hash/size/path及诊断。

## 四次尝试分层

全部针对第一个点：HT20、下行MCS3、上行MCS4、功率请求14dBm、候选2000 Kbps、8MiB、固定目标1/2/4。一次API拒绝，三次实际UFTP会话；没有跑完整matrix，更没有取得其余MCS/4000点的成功数据。

| run | API / receiver / 注册 | 文件与组会话 | 原始依据 |
| --- | --- | --- | --- |
| `20261026T184403Z` | API HTTP409 `radio_target_nodes_not_ready`，unready_nodes=[1]；未启动receiver、未发文件 | 无文件会话；不能归类UFTP速率失败 | [calibrate-response](runs/20261026T184403Z/01-mcs3-r2000-8mib/calibrate-response.json)、[result](runs/20261026T184403Z/01-mcs3-r2000-8mib/result.json) |
| `20261026T184920Z-retry` | API finalized、三receiver均启动；fl02注册未确认 | sender rc=0、wall58.6045s；fl01 Lost connection、fl02 Aborted、fl04 Completed；只有fl04最终size/hash匹配；组级COMPLETE只有fl04。MCS中途从3改为4，条件不稳定 | [uftp.log](runs/20261026T184920Z-retry/01-mcs3-r2000-8mib/uftp.log)、[result](runs/20261026T184920Z-retry/01-mcs3-r2000-8mib/result.json) |
| `20261026T190537Z-clean` | API finalized、三receiver均启动；三端REGISTER/FILEINFO_ACK均成立 | sender rc=0、wall59.6525s；fl01/fl02发NAK但sender未据此补齐，最终Lost connection且无最终文件；fl04 size/hash匹配。Server实际MCS3保持，但客户端曾寻频149，条件未保持 | [uftp.log](runs/20261026T190537Z-clean/01-mcs3-r2000-8mib/uftp.log)、[fl01接收日志](runs/20261026T190537Z-clean/01-mcs3-r2000-8mib/fl01-receiver-console.log)、[fl02接收日志](runs/20261026T190537Z-clean/01-mcs3-r2000-8mib/fl02-receiver-console.log)、[result](runs/20261026T190537Z-clean/01-mcs3-r2000-8mib/result.json) |
| `20261027T020324Z-transport-params` | API finalized、三receiver均启动，注册成立；09 sender补齐已部署Transport的 `-r 0.5:0.1:2.0 -s 20`、固定server UID255 | sender rc=0、wall64.6796s，三端文件级Completed；组级缺fl02，日志明确 `Couldn't get COMPLETE for group from 0x00000002`。fl02接收端多次发送组COMPLETE但未确认，最终received缺文件；fl01/fl04最终8388608字节且hash匹配。Server实际MCS再次3→4，不能采纳 | [uftp.log](runs/20261027T020324Z-transport-params/01-mcs3-r2000-8mib/uftp.log)、[fl02接收日志](runs/20261027T020324Z-transport-params/01-mcs3-r2000-8mib/fl02-receiver-console.log)、[result](runs/20261027T020324Z-transport-params/01-mcs3-r2000-8mib/result.json) |

日志中 `Group complete` 和sender退出0均不保证冻结三端全部完成。这里始终依据组级三UID COMPLETE和三端最终落盘hash判定，未把部分成功抬为整组成功。缺最终文件不叫hash不一致：具体是文件交付/组确认未收敛。

03说明中的裸UFTP命令适用于先前短探针，但此前09命令遗漏正式Transport反馈参数；已核对实际安装 `/usr/local/lib/python3.10/dist-packages/wfb_ng/fl/transport.py` 第247～250行，与当前主线相应参数一致。此次只修改09入口，并单点验证，没有通过改产品、提高功率、改变MCS/节点集合来伪造成功。补齐参数后仍不成功，因此不能宣称入口已稳定。

## 已确认干扰与尚未证明的根因

1. **生产控制面被本机软件测试触及：已证实。** 第一阶段被动抓包：真实fl01从fl-s/10.80.0.11发IDLE/14dBm/MCS4心跳，而本机lo/10.80.0.1还向真实10.80.0.1:9001发NODE_ID1的PREPARING/RUNNING/IDLE、12dBm/MCS6及job1/job2恢复消息，Server回复CONFIG_RADIO_ALIGN。见 [第一阶段被动报文](runs/20261026T184920Z-retry/control-observed.jsonl)。第二阶段再次抓到83条本机NODE_ID1心跳，包括ABORTING；见 [最新被动报文](runs/20261027T020324Z-transport-params/control-observed.jsonl)。同时实际存在主线pytest，已在进度消息中报告，不停止其他agent、不修改主线测试。
2. **节点活性被未来时间戳压住：已证实。** 本机测试心跳使用fl00 monotonic时间，真实fl01的monotonic约小610秒。03 `control.py` 节点登记在incoming timestamp_ms不大于已有值时返回ACK但不更新登记；因此真实管理在线、心跳到达仍可能在Server中OFFLINE。最新抓到本机52360069，临近真实51772590，仍领先约587秒。不会清注册表、伪造心跳或重启以跳过这一事实。
3. **实际发射参数被额外改写：已证实；具体调用归属尚未证明。** 第二和第四次中Server日志均有请求MCS3后另一次generation更新MCS4，数据与控制帧一起变更；API缓存仍是目标MCS3，说明仅看API不足。不能仅凭并行pytest的文件名断言哪个测试执行了这次UDP更新，需主线隔离真实9000/9001/9200/TUN后复核。
4. **反馈/心跳与寻频交织：已证实，底层最终根因未闭合。** 第三次三端注册成功，fl01/fl02收到DONE并多次发NAK，sender只处理fl04反馈；接收侧有REASSEMBLY_OVERFLOW和149频点观测。fl02 journal在03:08:46记录连续两次ACK缺失而寻频，发送刚开始约20秒。第四次fl02在10:03:43、10:04:04/20/35多次记录同类寻频，组COMPLETE反复发送却未收敛。可能涉及持续下行时反馈/心跳可达性，不能把它直接判为候选速率高低、UFTP自身bug或某个测试的单一因果。03软件/底座若需正式修复，由主线统一修改并审查后另行授权部署。

## 清理与恢复回执

2026-10-27 02:08:58 UTC只读复查：四端无uftp/uftpd，UDP1144均空闲；Daemon及底座保留，未停止非必需服务。四端清理读回文件位于最新run的 `fl00/fl01/fl02/fl04-cleanup-readback.txt`。

恢复历史必须分开理解：

- 继续任务时首先恢复基线成功：`switch_00d177f53eea413ea2517b5d8fb81478` finalized、calibration=false、157/40MHz/14dBm/MCS3/4/15000，四端iw实际40MHz，三客户端全READY；见 [恢复响应](runs/20261026T190537Z-clean/resume-restore-response.json)、[状态](runs/20261026T190537Z-clean/resume-restored-status.json)、[实际读回](runs/20261026T190537Z-clean/resume-restored-actual.json)。
- 最新第四次会话后恢复请求 **HTTP409拒绝**，`unready_nodes=[1,2]`；见 [恢复错误](runs/20261027T020324Z-transport-params/restore-response.json)。这不是RADIO_ERROR，未改任何标志。随后状态为Server IDLE、HT20标定配置，fl02/fl04 READY、fl01 OFFLINE；**此时不能声称末尾已恢复40MHz或全员READY**。
- 当前只读等待全部READY，满足准入后最多发出一次普通恢复请求，不反复发送校准/恢复。回执位置为最新run的 `recovery-wait-status.json`、`recovery-single-response.json`、`recovery-actual.json`、`recovery-final-status.json`。等待及请求结果均写日志；失败不自动重试，不启动第五次完整会话。

## 后续建议

暂不运行matrix，不给min/max/recommended。先由主线保证软件检查不访问真实生产控制端口/TUN并消除未来时间戳污染；再联合现有日志复核下行期间心跳寻频与UFTP反馈的冲突。09入口需保留与安装Transport一致参数、每次完整组证据、实际射频稳定性、源文件仓库外路径与全量失败。当前低速首点尚未完整成功，不能用换4MCS/4000或删除失败来宣称表有依据。用户已要求停止完整测量，现阶段只完成清理、恢复及诊断交接。

## 最新交接快照

2026-10-27T02:12:59Z：Server IDLE，实际有效配置仍为HT20/下行MCS3/2000标定；节点状态为[('1', 'OFFLINE'), ('2', 'READY'), ('4', 'READY')]。没有新增会话。四端无UFTP进程、1144空闲的清理证据已保存；只读等待恢复进程PID 135882，只在全部READY时发一次普通恢复请求，失败不重试。该后台等待不代表恢复成功，恢复事实以recovery-single-response.json和recovery-final-status.json为准。

## 已授权服务恢复完成（2026-10-27 02:26:21 UTC复核）

用户明确授权后，先核对PID135882已退出；保存Server状态、日志、PID与saved配置后正常重启Server Daemon。客户端仍HT20不能稳定对齐HT40，按授权逐端保存证据并正常重启fl01/fl02/fl04 Daemon。没有同步代码、改registry字段或清radioerror。最终Server IDLE、节点1/2/4全部READY；四端iw均157/40MHz，配置14dBm/MCS3/4/15000、GI short、FEC8/14。四端ps -C uftp,uftpd无进程，1144空闲；无第五次完整文件会话，小诊断也因恢复优先没有发送。等待主线server/control测试隔离完成通知后，才考虑单固定点复核。

恢复回执见 [authorized-recovery-result](runs/20261027T022037Z-fixed-small-diagnostic/authorized-recovery-result.json)、[实际读回](runs/20261027T022037Z-fixed-small-diagnostic/authorized-recovery-final-actual.json)、[全员READY复核](runs/20261027T022037Z-fixed-small-diagnostic/handoff-ready-status.json)。Server底座新PID148933，clients分别22800/14299/22306；这些PID变化属于本次明确授权恢复，不沿用此前测量PID不变结论。全量重启前日志保存在各端/srv/wfb-ng-fl/calibration/09/20261027T022037Z-fixed-small-diagnostic，repo记录路径/hash，原始小证据保留。
