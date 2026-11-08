# 最终恢复：f376a1f 全11节点 RS8/14

已按本轮授权结束有界探索，恢复全11节点（含fl03）RS8/14。没有执行新的射频事务、cutover、实验文件传输、标定、单台重启或换卡。

## 精确发布身份

```text
--release-root /srv/wfb-ng-fl/releases/heartbeat-f376a1f1ab48-20261108T110513Z
--release-commit f376a1f1ab4896a00163938784fe36d664058550
```

标准manifest位于每节点该root/manifest.json，冻结source为root/source/wfb_ng/fl。标准Python模块入口和硬件接口映射保持不变。

## 阶段与恢复审计

全11无active/activating/deactivating的comm unit。全部preflight/stage/prepare通过后，全11stop屏障通过才进入install，全11install通过才进入start。七阶段每节点exit=0，state.json记录全部完成。

全11native argv与实际GET_FEC均8/14。server source JSON和实际调度18/2，只有fec_n由12原子恢复14，其余JSON内容不变；client node.json字节不变，启动参数只将--fec-n由12恢复14。identity-fl*.json存before/after/expected、manifest与loaded。audit-fl*.json及final-recovery.json包含source/installed SHA、实际nativeSHA、UFTP摘要、15W、LGI/149/HT40/idx18、server MCS3/client MCS4。硬件映射比较preflight/prepare/Python/native接口一致。

Python来源证据是实际proc入口、隔离解释器模块路径、源码与manifest摘要，不声称活进程sys.modules。

## fl04延迟观测：不宣称整体稳定

ready10-before-observation.json首先确认server IDLE、无active_job、10个client READY。之后每次先等10秒，再获取fl04实际iw、native GET_RADIO与server registry READY（原始记录fl04-observation-1.json与2.json）。

- 第1次：2026-11-08T11:08:37.952649+00:00（本地19:08:37），fl04 READY；actualiface wlxfc221c300f04，iw149/40MHz/txpower -18，GET_RADIO rc0/LGI/40MHz/MCS4。
- 第2次：2026-11-08T11:08:48.535038+00:00（本地19:08:48），同样fl04 READY、实际149/40MHz/-18、GET_RADIO rc0/LGI/40MHz/MCS4；全10client仍READY。

两次采样附带本次服务激活以来的原始journal，采样时未匹配到hunting/寻频/自愈/缺失行。这不覆盖恢复前日志，也不证明持续稳定。用户/父agent补充：fl04在本地19:03–19:05 journal中持续HUNT，fl03/fl08近期未见同量事件；该历史异常不能被本次两个READY采样否定。没有通过重复重启或调参掩盖异常。

## native stdout路径与RX_ANT对照（只读）

fl04和fl03当前native的fd1/fd2实际均指向各自主机的：
`/srv/wfb-ng-fl/client/wfb_uplink.log`

fl04-native-log-paths.json与fl03-native-log-paths.json记录读取时UTC、native PID/argv、fd目标、文件末尾64KiB与RX_ANT行，日志是append文件，历史行不得冒充当前进程的整段历史。末尾最近每天线RX_ANT原样保留：

- fl04 antenna1：65:-54:-52:-50:8:17:30；antenna0：65:-58:-54:-52:8:17:27。
- fl03 antenna1：69:-58:-55:-54:10:15:21；antenna0：69:-50:-47:-46:10:15:21。

按src/rx.cpp格式，该字段为count:rssi_min:rssi_avg:rssi_max:snr_min:snr_avg:snr_max；对应最近平均RSSI为fl04 -52/-54 dBm、fl03 -55/-47 dBm。只是各主机最近日志样本，不是同步控制实验，不能单凭此断定必须换卡。父agent负责向用户提出可审阅的受控换卡A/B方案，本任务未执行换卡或其它后续动作。

final-status.json为第二次延迟采样的server状态。只提交本轮final-recovery证据，不混入父agent其它工作。
