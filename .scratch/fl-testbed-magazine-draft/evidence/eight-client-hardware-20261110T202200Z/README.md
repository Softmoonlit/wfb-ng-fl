# 八客户端物理更换批次结果

本 root 对应用户完成物理更换后的独立连接批次，固定 server `fl00` + clients `fl01, fl02, fl03, fl05, fl06, fl07, fl08, fl09`，排除 `fl04`、`fl10`。平台 slot1..10 保留为 native 槽位池，不等于业务参与集合。

## 实际硬件

fl00 实卡为 RTL8812AU，专用接口 `wlxfc221c100119`，MAC `fc:22:1c:10:01:19`，driver `rtl88xxau_wfb`，USB `2-1.1:1.0`，5000M。八个 client 的接口/MAC/driver/USB 路径/速率见 `hardware-from-actual.json`。传输前后由 `batchhardwareprobe.py` 保存 raw `/proc` native argv/SHA、GET_RADIO、GET_FEC、iw、服务和存储；两次 inventory 一致，未发现换卡/路径/速率变化。旧 release `actualargs.json` 的 stale fl00 接口已原子修订，旧/新 hash 见 `release-actualargs-rebind.json`。

## 准入与 Smoke

九板标准启动准入通过：server-first 后八 client，八端 `READY/IDLE`，server `IDLE`、无 active job，149/HT40/Long GI/index18、RS(8,12)、30/5、15W，旧 comm/组播 route 清理通过。fl04/fl10 保持 OFFLINE。fl08 在模型前按成功归档 size/SHA 清单精确删除三份旧 duplicate，释放 `134,324,224` bytes；B2 原件、failed partial、evidence、install 未删除，见 `fl08-storage-reclamation.json`。

唯一 smoke run `eight-20261110T132200Z-smoke` 成功：1MiB SHA `30e14955...09fcb58`，149 -> 157 -> 149；下行 7.381s，上行 8.570s；八端独立大小/SHA 全部通过，cleanup、149 恢复、九端 final audit 全部通过。smoke 的 raw inventory 原文件后来被后续 model executor 覆盖，未能独立保存，不能把当前 `raw-inventory-pre/post.json` 追溯称为 smoke raw；smoke progress、executor 成果和覆盖事实均保留。

## Model 结果

唯一 model run `eight-20261110T132500Z-model` 按授权一次执行、每方向 600s 硬截止、不自动 retry。下行 44,770,302 bytes、SHA `4b55cee2...c234e96`，八端接收完整；上行 fl01/fl03/fl05/fl06/fl07/fl08/fl09 为客户端 `update_sent`，不能称 server 独立 SHA 通过，因为 runner 未进入 `phase=verify`。fl02 在截止时仅有 15,466,496 bytes partial，server 报 `FLRuntimeError: 轮次等待 update 超时，未达到最小配额: 期望 8`。server 七个实际 `updates/<node>/update.bin` 的 exists/symlink/size/SHA 只读结果见 `fl02-model-server-update-audit.json`。本次不计为模型闭环成功，不声称最终双闭环验收或调度最优。失败 progress、upload partial 采样、runner log、manifest 和 model raw post-inventory 全部保留；cleanup 完成，RF restore response 成功但原 runner 独立最终审计旗标仍为 false。

最终服务状态：fl00 server 与八 client WFB service active，native 均 resident；server radio 已恢复 `149/HT40/LGI/index18/FEC8/12`。独立最终审计首次窗口见 `final-recovery-audit.json`，它保留当时 fl02 `HUNTING/OFFLINE` 的真实读回；不改写原 runner `restore_ok=false`。随后只读复核见 `final-recovery-audit-recheck.json`，九板 source/native/15W、debug EMC lock、radio/getfec、routes、active job 和八端 READY/IDLE 全部通过。fl02 bounded 只读窗口见 `fl02-bounded-diagnosis.json`：boot ID、kernel/USB/power journal、service heartbeat/寻频日志、SSH 管理日志和 120 条 partial progress 样本均保留；窗口内无 kernel USB reset/disconnect/power/thermal 条目，不能据此推断 power/USB 根因。

## 用户授权的fl02整机重启

模型失败与窄诊断留档后，用户要求重启fl02。确认无活动临时通信任务后执行一次标准整机重启，旧boot_id为`8c60fa57-ef3e-47a7-a433-f91fb4d8917b`，新boot_id为`2d5d89c7-d14b-4ab0-8842-a7026a2ef6bf`。服务自动恢复，40个源码文件、运行native SHA、15W实际限制、149/HT40/Long GI、FEC8/12、MCS4与八客户端READY/IDLE均核验通过，见`fl02-reboot-return.json`及`fl02-post-reboot-audit.json`。这只证明重启后当前状态恢复，不能证明长流尾部缺陷已解决；未自动重跑模型。
