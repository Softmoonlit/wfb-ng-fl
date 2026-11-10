# 第二次八客户端模型闭环证据

## 批次范围

本 root 为 `second-eight-model-20261110T150044Z`，固定服务端 `fl00` 和客户端 `[fl01, fl02, fl03, fl05, fl06, fl07, fl08, fl09]`，排除 `fl04`、`fl10`。source 为 `66f15358d309d646d7dca887624a66c46099a54a`，release 为 `/srv/wfb-ng-fl/releases/heartbeat-66f15358d309-20261110T114225Z`，native SHA 为 `44d6bbf500f51df4d0b3ae8cd7a53c4a45cb55b09ca66cb5217e7ac75674feb2`。无线参数为 `157/HT40/Long GI/index18/DL3/UL4/RS(8,12)/30/5/12000`，板级限制为 15W。

本轮没有新增 smoke 文件；上一轮 smoke 已通过。本轮完整准入后只执行了一次唯一 model run `second-eight-model-20261110T150044Z-model`，payload 通过 WFB 下行和客户端原文件上传，不通过管理网传送模型。

## 准入与存储

准入前九板独立 manifest/source 40 文件、proc native、实际 MAC/driver/USB、GET_RADIO、GET_FEC、iw、15W/CPU/GPU/EMC、旧 unit/route、server registry 均通过；`check-all-ready.py` 返回 0，八端 READY/IDLE、149/HT40/Long GI、无 active job。实际 bundle SHA 为 `1ff80d4683137c191328ef524b910143a2dae462b9c6854e615412da61d46496`，模型为 44,770,302 字节、SHA `4b55cee2f0bdcfbbf610167a505efee0e2c50e3306fed02bda3613807c234e96`。

存储门槛为 `512MiB + 3*44,770,302 = 671,181,818` 字节。只清理已归档完整成功副本，并逐文件检查 size/SHA/stat 后 unlink：fl02 释放两个旧副本文件，fl08 删除 `g40-d10` 三文件，fl09 删除 `g30-d10` 三文件。清理后可用空间分别为 fl02 `680,824,832`、fl08 `685,203,456`、fl09 `707,059,712`，均越过门槛后立即停止。B2 原件、重启后第一轮成功模型、保留的成功模型、失败尝试、partial、日志、manifest、evidence 和安装文件均未删除。完整原始逐文件记录见 `storage-reclamation-final.raw` 和 `storage-reclamation.json`；两次未 unlink 的脚本前置错误也分别留在 `storage-reclamation-attempt-1.txt`、`storage-reclamation-attempt-2.txt`。

## Model 实际结果

model 的下行 dispatch 到八端最后接收验证为 `58.769s`，上行 submit dispatch 到服务端收齐八份为 `261.305s`。这两个数字均由本机 `progress.jsonl` 的编排机 monotonic 计算，没有比较不同节点的 monotonic。server `verify` 独立确认客户端 `[1,2,3,5,6,7,8,9]` 的八份文件均为 `44,770,302` 字节，SHA 均为 `4b55cee2f0bdcfbbf610167a505efee0e2c50e3306fed02bda3613807c234e96`。

runner 内的 role cleanup 九端均返回 `ok=true`，149 restore 返回 `ok=true`，九端 `audit_final` 均返回 `ok=true`。但独立 cleanup 阶段 fl03 的 SSH 在 18 秒回收预算内超时；随后 fl03 管理网返回 `No route to host`，无法确认其独立 unit、route 和 resident 身份。wrapper post raw inventory 因 `fl03: management/raw probe failed` 返回 1，model runner 返回 1，`cleanup_ok=false`、`restore_ok=true`。实际 post-model independent audit 返回 2，错误为 `fl03: raw probe failed` 和 `server registry final gate`；fl00 registry 当时显示 server `IDLE`、无 active job、fl03 `OFFLINE`，其余七个在线客户端 READY/IDLE。

因此本轮是一次 **WFB 双向 model 传输和八份 server SHA verify 通过，但完整闭环验收被 fl03 管理失联阻断** 的失败批次，不是第二次完整通过。没有 retry、改参、重启、重新删除端或把 `restore=false` 改为 true。没有证据把 fl03 失联归因于网卡、USB、温度、供电或永久硬件损坏。

## 连续验收边界

上一轮重启后完整成功仍为：下行 `78.192s`、上行 `289.975s`，八份 size/SHA、cleanup、149 restore 和独立审计均通过。本轮虽然传输时长和八份 SHA verify 已记录，但因 fl03 独立 cleanup/post audit 失败，不能与上一轮拼成“两次连续完整闭环”，也不称统计最优、硬件损坏或重启永久修复。问题 19 的上行长尾事实保留；本轮新增的是 fl03 管理面阻断边界，而非对根因的定论。

## 原始入口

- `model/runs/second-eight-model-20261110T150044Z-model/progress.jsonl`
- `raw-inventory-pre.json`、`raw-inventory-post.json`、`executor-result.json`
- `final-recovery-audit.json`、`post-model-final-recovery-audit.stdout`
- `batch-runner.log`
- `model-plan.jsonl`、`model/executor-command.txt`
- `validation-summary.json`

`file_transfer`：已通过 WFB 实际完成本轮 44,770,302 字节模型双向传输和 server 八份独立 SHA 校验；不是外部消息应用传文件，也不是 `file_transfer:none`。

## 父侧最终只读复核

直接复核原始progress的server verify：固定八份更新均为44,770,302字节，SHA均匹配模型。随后只读管理检查仍为fl03 SSH `No route to host`、两次ping均未响应、邻居项FAILED，registry为OFFLINE，其余七客户端READY/IDLE/149。见`fl03-final-management-recheck.json`。尚无现场供电确认，不能自行归因为断电。未自动重试或继续发送。需要现场检查fl03的供电与管理连接，恢复后重新完整准入；本轮结束时不能宣称连续两次完整验收通过。

## 后续现场事实分类（不改原验收结论）

用户随后确认：fl03 当时确因断电而关机，之后已充电并启动。该事实将旧批次的 `fl03 No route` 分类为**供电/环境中断**，不归因为无线、USB、温度或永久硬件故障。原始 `cleanup_ok=false`、独立 audit 失败、registry OFFLINE 和 runner 失败字段保持原值，不能改写为成功，也不能与新恢复后的九板审计拼接为连续验收。恢复后新 root `heartbeat-budget-eight-20261110T154206Z` 的九板独立审计通过证据另行保存。
