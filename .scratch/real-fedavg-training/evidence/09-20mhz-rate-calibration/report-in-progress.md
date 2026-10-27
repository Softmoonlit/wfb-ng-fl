# 09实测现场记录（进行中）

正式授权后的固定矩阵为 MCS3/4/5/6 × 候选2000/4000 Kbps × 3重复 × 8MiB，共24次；保守推荐点每MCS另1次40MiB，共28次计划。没有改代码、组合表、工单状态或commit；没有同步主线。坐标、间距与天线朝向未测定，实验未移动设备。

## 已取得的现场事实

四端实际安装路径 `/usr/local/lib/python3.10/dist-packages/wfb_ng`，安装build_identity均为 `99865efe224a9ad35fefd13ae1340721f374b521`，底座SHA-256均为 `8ddb419be0c99e174eeb91239092f266398ea248e0c3c71eff643e28bbcc08e0`。root取证从/tmp导入安装包；最初非root fl00预检从工作区导入的结果不作安装代码身份依据，随后已纠正并独立保存 `*-root-preflight.json`。08安装/GPU检查的先前完成证据已读取；现场进程快照无pip/apt/MNIST安装、GPU检查或训练进程，但存在主线pytest软件检查及本机agent CPU负载，全部如实记录。

首次批次 `runs/20261026T184403Z` 的01尝试，在预检READY之后、API受理时fl01变成未就绪，HTTP409拒绝，无UFTP会话发送。失败保存。普通恢复返回finalized，指定40MHz配置成立；全员READY未成立。

第二批次 `runs/20261026T184920Z-retry` 的01尝试成功经专用API受理 HT20/MCS3/2000，四端iw读回20MHz，底座与TUN不变，随后实际发送8MiB：

- UFTP进程退出码0、wall 58.6045秒。
- fl04最终8388608字节，SHA-256为 `860f16f4148047b729e13768200743cd70c64abac8b615ea4bd4c6c8c68e2618`，匹配源；hash耗时约0.01183秒。
- fl01无最终文件，独立tmp留下8076900字节部分文件；fl02注册未确认、无最终模型文件。失败端原始日志和临时内容均保留。
- sender虽然打印Group complete，但只有fl04组级COMPLETE，整组目标1/2/4未完成，**全组失败**。
- 原始射频日志确认Server generation1=20MHz/MCS3，随后generation2变为20MHz/MCS4；客户端RX_ANT包含5785:3:20、5785:4:20，fl01/fl02还出现5745MHz（149信道）。目标条件未保持，不能据此判断2000候选的性能或安全边界。
- 首次恢复请求HTTP409：fl01未就绪；已立即反馈。随后仅通过原普通API恢复成功，事务 `switch_fb60ea21bdf84c5e98168bc424da59ac` finalized，calibration=false，157/40MHz/14dBm/MCS3/4/15000。未重启任何服务；真实全员READY仍待确认。

## 已确认的软件测试干扰

不通过发送测试包诊断；`observe_control.py` 仅用AF_PACKET被动监听本机lo与现有fl-s。原始 `runs/20261026T184920Z-retry/control-observed.jsonl` 显示：

- 真正fl01经fl-s从10.80.0.11向10.80.0.1:9001发心跳，IDLE、14dBm/MCS4。
- 本机lo还存在10.80.0.1到10.80.0.1:9001的NODE_ID1测试心跳，含PREPARING/RUNNING/IDLE、12dBm/MCS6和job1/job2 JOB_RECOVERY；Server向这些测试源端口回发CONFIG_RADIO_ALIGN。
- 最后一个截获的本机IDLE测试心跳timestamp_ms=26451499，临近真实fl01心跳为25841006，领先610.493秒。03安装版 `control.py` 的节点登记防重放会拒绝不大于已有时间戳的心跳，解释了真实节点管理在线却在Server中长时间OFFLINE。
- 同时观察到主线pytest进程，含test_fl_file_job、test_fl_client_service、test_fl_radio_runtime_application等；已报告主线软件检查接触真实控制端口的事实。仅凭文件名不认定每个测试都泄漏；实际泄漏边界须主线修复。本票不修改其测试或产品代码，也不停止其他agent进程。

本机测试消息污染生产控制面的事实已有直接报文证据；首次会话中MCS被改写的具体调用者尚未逐调用追踪，不把全部射频变化简单归因于某个测试文件。所有受干扰会话均不进入速率建议依据。

## 制品位置

依用户指示，大制品已迁至各端 `/srv/wfb-ng-fl/calibration/09/<run>`，repo内不保留bin。Server使用其 `source/` 子目录；客户端保留attempt下received、tmp及日志。源 `sources.json` 更新当前路径、original_path、bytes、sha256与迁移校验结果；`artifact-relocations.json` 和三个客户端迁移清单记录路径映射。原始sender-command/result中的历史路径保留，不篡改当次实际命令，须与迁移清单联合解释。所有原始日志、尝试和失败均保留。

当前暂停新校准，等待真实心跳被重新接受、全员READY且无控制面测试干扰后重测。不伪造心跳、不绕过准入、不部署、不通过重启清除注册表。没有取得可重复成功点之前，不提供HT20组合表数字；2000和4000仍是未标定候选。

## 已授权服务恢复完成（2026-10-27 02:26:21 UTC复核）

用户明确授权后，先核对PID135882已退出；保存Server状态、日志、PID与saved配置后正常重启Server Daemon。客户端仍HT20不能稳定对齐HT40，按授权逐端保存证据并正常重启fl01/fl02/fl04 Daemon。没有同步代码、改registry字段或清radioerror。最终Server IDLE、节点1/2/4全部READY；四端iw均157/40MHz，配置14dBm/MCS3/4/15000、GI short、FEC8/14。四端ps -C uftp,uftpd无进程，1144空闲；无第五次完整文件会话，小诊断也因恢复优先没有发送。等待主线server/control测试隔离完成通知后，才考虑单固定点复核。

恢复回执见 [authorized-recovery-result](runs/20261027T022037Z-fixed-small-diagnostic/authorized-recovery-result.json)、[实际读回](runs/20261027T022037Z-fixed-small-diagnostic/authorized-recovery-final-actual.json)、[全员READY复核](runs/20261027T022037Z-fixed-small-diagnostic/handoff-ready-status.json)。Server底座新PID148933，clients分别22800/14299/22306；这些PID变化属于本次明确授权恢复，不沿用此前测量PID不变结论。全量重启前日志保存在各端/srv/wfb-ng-fl/calibration/09/20261027T022037Z-fixed-small-diagnostic，repo记录路径/hash，原始小证据保留。
