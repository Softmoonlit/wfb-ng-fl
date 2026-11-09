# fl08心跳修复后的两轮连续通信验收

状态：已完成。

本 continuation 是在 base root 的工具性 STOP 后建立的 fresh 子 root。base root 的原始 STOP 与 raw 证据保留不动，原 STOP 原因是缺少 canonical `check-all-ready.py`，模型执行计数为 0，不属于无线准入、参数或节点失败。

continuation 已补入并审阅 canonical `check-all-ready.py`，修正 `storage.py` 的索引路径，并实际 import 验证：`ROOT` 为本 continuation，`BATCHROOT` 固定为已授权的旧成功证据根，精确回收源文件存在，所有成功轮 reclaim bases 存在。未复制原 STOP，也未复用原运行状态。

## 验收结果

- 固定拓扑：`fl00`、`fl01`、`fl02`、`fl03`、`fl05`、`fl06`、`fl07`、`fl08`、`fl09`、`fl10`；`fl04` 排除。
- 发布：commit `cccac233291c5a79ca7a66bb2ebb210497ab4071`，native SHA `44d6bbf500f51df4d0b3ae8cd7a53c4a45cb55b09ca66cb5217e7ac75674feb2`。
- 通信 bundle：`728896c99b36cb873962dda74d61de729c223e82e5e06b58ac5b63f87441d958`。
- 配置：30/5、FEC 8/12、HT40/LGI、157 测量后恢复 149、MCS 3/4、驱动索引 18、12 Mbps。
- 模型：`44770302` bytes，SHA `4b55cee2f0bdcfbbf610167a505efee0e2c50e3306fed02bda3613807c234e96`。
- 第 1 轮：DL `41.825669667` 秒，UL `265.414043896` 秒，完整收发、清理、恢复和最终审计通过。
- 第 2 轮：DL `46.94585797199943` 秒，UL `255.10296096599996` 秒，完整收发、清理、恢复和最终审计通过。
- 两轮每轮 9 份 update 均为 `44770302` bytes / 目标模型 SHA；无模型重试，恰好两次模型执行。
- storage 硬门槛仍为 512 MiB；第二轮前仅按已授权成功重复件精确回收，保留 B2 成功样本，不碰失败件、partial 或日志。
- 最终十板实际 audit 和九 READY 通过；wrapper child exit 0、`hold_failure=null`，所有 executor 已退出；PackageKit 已恢复 `active/static`。

详细证据见 `final-closeout.json`、`trial1/`、`trial2/`、`worker-exit.json`、`wrapper-final.json` 和 base root 的 `execution-count-summary.json`。当前 wrapper 已结束，不再持有远端 owner。
