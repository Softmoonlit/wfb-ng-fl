# 工单09：2026-10-28 实测与硬件恢复阻塞

2026-10-29 当前口径：用户已明确授权 fl00 + fl01/fl02，固定目标 `[1,2]`；fl04 隔离并单独归档。该集合适用于09标定、12故障与页面检查、13三次五轮，十五轮共30份 update，不再要求当前阶段回到三客户端。已保存的三次五轮全部为 HT40，及其独立数值复核，只证明当前两客户端 HT40 流程结果；09组合表、12完整故障／页面检查、13规定的双带宽与轮间切换验收仍未完成。

## 172704两客户端尝试勘误

`runs/20261028T172704Z-single8-two-client/`保留原始结果。该次以[1,2]启动两个接收器后，运行时runner仍用 `len(receivers)!=3` 硬编码检查，因而抛出 `receiver failed`，未启动sender，也未发生本次文件发送。原记录中的失败标记只表示attempt失败，不能据此认定HT20链路或两客户端速率失败。当前工作区runner已使用目标集合比较，但这不改变当时事实，也不构成修正后实测成功。后续必须在已授权[1,2]范围重新取得有效固定点与重复矩阵，09仍未完成。原始日志／JSON不改写。

以下为10月28日分批次历史记录；其中“当前结论”及恢复步骤描述当时网卡阻塞状态，后续已重启恢复并执行新的尝试，不代表10月29日当前状态。

## 10月28日中途结论（历史）

工单未完成，状态 `ready-for-human`：需要现场重新插拔 **fl00／Server 的 Realtek 8812 USB 测试网卡**（接口原名 `wlxfc221c100157`，USB路径 `2-1.1`，VID:PID `0bda:8812`）。本机软件USB复位失败，测试接口消失。四端常驻服务均已标准停止、无native残留，**尚未恢复射频和READY基线**。没有运行工单12/13，没有修改产品速率表。

## 已执行的测量

运行提交固定 `c62cde266ef509d605c8c923b0f4fa35e42fc4dd`，四端运行native SHA均为 `5248592c31820abdb15ce5f9aad2d539a5e7a4a4e179af52d3a6e2b48eed9f93`。起始149/HT40+/驱动14档/下行MCS3/上行MCS4/UFTP15000，READY/GRANT控制帧MCS0。TUN心跳与ACK仍使用方向数据MCS。所有候选经 `/radio/calibrate`，目标完整集合 `[1,2,4]`。

| 目录（位于runs/） | 条件与结果 | 恢复结果 |
| --- | --- | --- |
| `20261028T141119Z-preflight` | 版本与射频通过；长观察发现fl04间歇OFFLINE，连续120秒条件未取得。后续明确按既有IPC空闲全READY准入执行诊断固定点，未把长观察失败隐去 | 未变更射频 |
| `20261028T141859Z-single` | 149/HT20/MCS3/2000 Kbps/8MiB：HTTP标定通过，三UID COMPLETE、Group complete、三端最终大小和SHA-256一致。发送56.6925秒，存在NAK及重传；PID/TUN、实际射频和应用MCS核验通过 | 普通恢复请求成功，最终实际iw及全员READY通过 |
| `20261028T142137Z-matrix` | 正式40MiB矩阵在首项PREPARE失败，`unresponsive_nodes=[1]`；未发送文件。不是速率样本失败 | 自动回退，原40MHz配置、iw和全员READY通过 |
| `20261028T142414Z-matrix-retry` | 首项149/HT20/MCS3/2000 Kbps/40MiB：三端注册后传输中断，sender退出9，用时198.9716秒；三端最终文件均未提交，临时文件留在各端测量目录。收尾时三客户端已进入不同信道寻频；后续23项未发起 | **恢复请求HTTP409，目标非IDLE/READY；没有恢复** |
| `20261028T143247Z-passive40` | 仅被动观察上次失败遗留的HT20/HUNT状态；没有新文件传输。Server TUN未捕获心跳，客户端持续HUNT。不能据此推断负载期上/下行延迟原因 | 后续标准停服使TUN消失，捕获提前以Network is down退出；没有end/socket_stats，不能宣称零抓包丢失 |
| `20261028T143331Z-single40-trace` | 同参40MiB复现计划在initial-ready门禁失败，未发标定请求、未启动文件发送 | 未变更射频；归入环境准入失败 |
| `20261028T153723Z-single8-postreboot-startupbaseline` | 重启fl00并重建四端native后，8MiB固定点重新执行：标定进入149/HT20/MCS3/4/14档成功，但发送端180秒预算后退出124，三端最终文件均不存在；不是矩阵样本 | 恢复HTTP200 finalized，四端实际149/HT40+/14，全部READY，Server IDLE |
| `20261028T143717Z-standard-recovery` | 现场归档后四端stop；确认native全退出后start。fl00启动设置功率的iw卡在USB驱动；软件usbreset失败，接口消失 | 四端最终均inactive/dead、MainPID=0、无native；**未恢复READY** |

本次重启后固定点的UFTP证据进一步收窄了问题：fl01和fl02在多轮pass中分别报告COMPLETE，但fl04持续产生大批量NAK（2996、2247、1652、1051、617），最终发送端因预算结束退出124；Server最终中止会话，三端最终文件均未提交。该结果支持“fl04下行接收/反馈是整组会话瓶颈”的方向，但仍不能单凭UFTP日志区分空口接收、客户端反馈上行或守护状态切换。

仅有一次8MiB整组成功，不能声明3/3重复成功、40MiB稳定性、任何min/max/default，也不能启用正常20MHz配置。2000/4000仍只是候选。现有40MHz表未改；本次也未形成与40MHz表值的数值冲突证明。
仓库外源文件、已完成8MiB文件、40MiB失败临时文件及接收日志的逐端路径、实际字节、SHA-256和哈希耗时见[独立制品清单](20261028-external-artifacts.json)。原文件留在各端 `/srv/wfb-ng-fl/calibration/09/`，未删除、未移动临时文件伪造最终交付；二进制载荷不提交到仓库。

## 关键诊断证据与边界

40MiB失败的各端 `*-receiver.json` 中，真实接收日志在 `receiver-console.log` 字段（UFTP前台模式）；`uftpd.log` 字段为空，不应当作日志缺失。接收端约在本机日志22:27:08～09报告Transfer timed out。fl01 journal在发送开始约18秒后已记录“十秒内未确认有效心跳”并寻频；这只是同端事件次序，未用跨板墙钟计算链路时延。

`matrix-retry/diagnosis/`保留四端journal及本机单调事件字段。PREPARE失败只证明deadline前没有ok=true ACK，不能证明具体PREPARE或ACK丢包；源码发送PREPARE一次、无定期重发是独立可靠性发现，未在本次改动。

`standard-recovery/fl00-iw-blocked.json`保留D状态堆栈：

```text
iw dev wlxfc221c100157 set txpower fixed -1400
usb_kill_urb -> usb_control_msg -> usbctrl_vendorreq [88XXau_wfb]
-> PHY_SetTxPowerIndex_8812A -> cfg80211_rtw_set_txpower
```

`usbreset 002/003`仅作用于已核验测试网卡，返回 `failed [No such device]`。`after-usbreset-kernel.txt`记录USB2-1.1的LPM设置失败及Hub超时 `-110`。这确定解释恢复启动阻塞，但**不能反推为40MiB传输失败的根因**，也不等于网卡本体损坏。

标准恢复脚本先调用了只允许恰有一个native的测量SNAP，在native全停止后触发审计异常。随后通过独立 `/proc` 零native检查确认四端全部停止，再统一启动；没有跳过零native屏障。启动未成功，最后执行并记录四端stop，见 `final-stopped.json`。`recovery-console.log`保留原脚本错误，不能将systemctl start受理当作恢复完成。

中途进度曾把前一个PREPARE失败后的恢复成功误用于后一个40MiB失败；发现后已明确更正。本文件以逐批次HTTP、实际iw、终态为准，40MiB失败后不标恢复成功。

## 已交付的执行准备

- `run_measurements.py`：显式运行commit/native SHA、单固定点与24项40MiB矩阵、运行Python manifest/native/proc tick、实际air-interface、逐端iw/runtime MCS、三UID组级完成与最终hash、PID/TUN及清理和恢复校验。恢复失败单独落盘，禁止伪装成功。
- `preflight-current.py`、`recheck-ready.py`：保留本次额外观察及其失败；不当作09新增强制准入。
- `recover-baseline.py`：本次恢复尝试原样保留，包含已披露的停服后SNAP接缝错误，**不应直接复用**；后续严格采用修正后的零native检查及运行身份复核。
- `summarize-measurements.py`：本次摘要辅助；产品 `wfb_ng/fl/radio.py` 未修改。

## 当时拟定的恢复后继续顺序（历史，当前集合以[1,2]为准）

1. 现场重新插拔fl00测试网卡；若经USB Hub连接且仍不识别，再核验Hub链路。不要先重启fl00整机，以免中断当前管理会话。
2. 重新确认USB 5000M、测试接口、驱动可响应；由agent标准启动四端，核验c62cde2/Python加载路径/native SHA、实际149/HT40+/14档，以及三个客户端真实READY。不改注册表。
3. 新建唯一批次，运行同参40MiB单次复现，同时预先开启覆盖完整负载期的被动HB/ACK取证。当前失败后的capture不能替代负载期证据。
4. 按heartbeat timestamp和每板本机mono分开核验上行交付、server处理、下行ACK及native队列/授权/RX批次；不得先假定RF、FIFO或FEC就是主因。
5. 原因修复并重新取得有效重复样本后继续剩余矩阵，TDD接入真实表，再进行09审查与正式发布。09未resolved前，不推进依赖09的后续实机验收。

## 本次归档的独立汇总

`measured-summary.json`、`summary-in-progress.json` 保留为当时生成的历史快照，不代表最终分母。新汇总见 [archive-corrected-summary.json](archive-corrected-summary.json)，输入批次及排除的旧格式目录见 [archive-summary-inputs.json](archive-summary-inputs.json)。汇总工具只接受该清单所用的新schema，旧schema保留独立原始记录，不做兼容转换。

按实际sender启动统计，三客户端HT20/MCS3/2000的8MiB为1/2成功，40MiB为0/1成功；另有2次准入或接收器准备失败，未发sender，不计候选传输分母。两客户端批次是准备失败，没有两客户端无线传输结果；没有任何候选达到3/3可重复成功。
