# 工单09暂停交接

2026-10-29 当前口径：用户已明确授权 fl00 + fl01/fl02，固定目标 `[1,2]`；fl04 隔离并单独归档。该集合适用于09标定、12故障与页面检查、13三次五轮，十五轮共30份 update，不再要求当前阶段回到三客户端。已保存的三次五轮全部为 HT40，及其独立数值复核，只证明当前两客户端 HT40 流程结果；09组合表、12完整故障／页面检查、13规定的双带宽与轮间切换验收仍未完成。

本交接的USB阻塞、待插拔及四端停止状态为10月28日中途快照，后续已恢复并继续尝试。172704批次属于runner接收器数量硬编码失败、sender未启动，不能认定两客户端HT20失败，见[执行勘误](20261028-execution.md)。下列原始交接和脚本参数只保留历史意义，不作为当前执行步骤。

## 2026-10-28中途状态（历史）

08已resolved，用户已授权并实际执行09。当前阻塞为fl00测试USB网卡驱动/Hub超时，软件复位失败、接口消失，需要人工重新插拔；四端服务已标准停止，尚未恢复READY。已有一次HT20/MCS3/2000/8MiB全组成功，正式40MiB首项失败，不能填速率表。完整新批次、恢复失败和后续顺序见[本轮执行记录](20261028-execution.md)。下文保留2026-10-27历史交接，不得将其157/READY/等待08授权状态作为当前现场。

## 原始交接（2026-10-27：等待新build与08 passed）

按用户最新安排，09停止基于旧03构建99865ef进行测量。等待06/07正式提交、08在同一正式新build上完成部署及少轮试跑，并收到用户明确的08 passed通知后，才重开单固定点及matrix。当前不执行校准、不发送小文件、不启动训练、不部署、不commit、不改issue09状态；不将本票标resolved。

## 恢复事实

2026-10-27 02:26:21 UTC复核：Server IDLE，fl01/fl02/fl04全部READY；四端实际iw均信道157/40MHz，配置14dBm、下行MCS3/上行MCS4、UFTP15000 Kbps、GI short、FEC8/14。此前用户明确授权服务恢复后，先保存日志/PID/config，再正常重启Server及逐端客户端Daemon，未修改registry或清radioerror。恢复记录见 [回执](runs/20261027T022037Z-fixed-small-diagnostic/authorized-recovery-result.json)、[实际读回](runs/20261027T022037Z-fixed-small-diagnostic/authorized-recovery-final-actual.json)、[全员READY](runs/20261027T022037Z-fixed-small-diagnostic/handoff-ready-status.json)。

旧PID135882已在检查前退出。暂停收尾重新扫描09所属实际Python脚本进程，未发现run_measurements、restore_baseline、wait_then_restore_once及观测器存活，见 [进程清理记录](pause-process-cleanup.json)。没有后台进程等待READY后自动发射频请求。四端无uftp/uftpd，1144空闲，未执行第五次完整会话；最后固定小诊断只启动被动观测，未发文件。

## 脚本修正与执行限制

- `run_measurements.py` 的sender已与安装版03 Transport对齐：`-U 0x000000ff -r 0.5:0.1:2.0 -s 20`；仍要求全部1/2/4组级COMPLETE、Group complete和三端最终落盘size/hash，不接受退出0或文件级Completed代替全组。
- 源文件和客户端received/tmp位于 `/srv/wfb-ng-fl/calibration/09/<run>`，repo仅记路径、大小、摘要和日志。没有通过移动设备、修改功率/MCS基线或缩减参与节点掩盖失败。
- 增加实际Server发射MCS变化检查、并行pytest准入检查；后者只是本机附加观察，**不能代替主线对server/control真实UDP边界已隔离的确认**。下一次必须同步保存TUN反馈、TOKEN队列、9200请求及9000/9001控制消息，与UFTP组确认闭合。
- 为避免误启动旧构建，入口现在必须显式 `--execute-new-build <40位commit>`，拒绝旧03 commit，并逐端从安装包读取build_identity核对同一正式commit。这个参数不构成用户授权；收到08 passed通知前禁止调用。
- 新一轮须新建唯一run目录和当前run引用，冻结新build身份及现场配置，不能复用旧active-run、旧PID或旧源文件路径冒充新结果。先按用户重新确认的固定点/载荷单次验证；成功才重开24次8MiB矩阵与各MCS推荐点40MiB长流。新build上的参数、状态与API结构需执行前核对，不能假设和03完全一致。
- `restore_baseline.py`、`wait_then_restore_once.py` 是保留历史恢复工具，当前不运行、不自动启动。新阶段正常恢复仍需根据新build契约操作。

以上只做语法核对和文档/参数修正，没有运行新的硬件测量，也没有宣称脚本已在新build通过。

## 四次失败及污染证据

[四次尝试分层诊断](diagnosis-four-attempts.md)保留全部事实：一次API准入拒绝、三次实际UFTP首点会话失败，未形成任何可重复成功速率。重点包括fl02反复发送组COMPLETE但sender未确认、最终目录缺文件，以及fl00本机更新MCS3→4的污染。

最后MCS变更的request ID为1751617984（0x686791c0）、cmd2/rc0，时间约2026-10-27 02:04:00.213～02:04:01.213 UTC；当时的被动抓包尚未启动，不能将该request ID直接归属某个单测广播。另有直接抓包证据表明本机测试向真实9001发送NODE_ID1心跳并污染时间戳，造成真实节点被防重放忽略。不能把受污染会话当注入速率失败边界，不能据此给出min/max/recommended。

四个失败批次全部保留：`20261026T184403Z`、`20261026T184920Z-retry`、`20261026T190537Z-clean`、`20261027T020324Z-transport-params`。未删失败、未覆盖日志，也没有新增正常HT20表。

## 仓库外数据清单

[四端逐文件清单](external-artifact-inventory.json)包括每个文件的绝对path、bytes、SHA-256和本次hash耗时。包含源模型、完整/部分接收文件、UFTP重启临时内容、被动报文及服务重启前完整底座日志，不只列成功产物。

| 主机 | 文件数 | 总字节数 | 根目录 |
| --- | ---: | ---: | --- |
| fl00 | 11 | 267909757 | `/srv/wfb-ng-fl/calibration/09` |
| fl01 | 16 | 49418957 | `/srv/wfb-ng-fl/calibration/09` |
| fl02 | 17 | 39255036 | `/srv/wfb-ng-fl/calibration/09` |
| fl04 | 15 | 49580054 | `/srv/wfb-ng-fl/calibration/09` |

历史bin迁移关联由 [Server迁移表](artifact-relocations.json) 和 `fl01/fl02/fl04-artifact-relocations.json` 记录；当次原始sender命令及result中的旧路径不改写，以保持证据真实性。当前仓库09目录无bin。原数据不自动清理，后续由用户决定保留和归档。
