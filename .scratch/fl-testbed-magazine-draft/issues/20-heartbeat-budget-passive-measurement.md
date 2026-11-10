# 心跳 230ms/10s 实测与环境中断边界

Status: ready-for-review
Type: research

## 范围

固定八客户端 `[1,2,3,5,6,7,8,9]`、fl00 server、149/HT40/Long GI/index18、DL3/UL4、FEC8/12、30/5；保留单 ACK `230ms` 与失活 `10s`，不改 retry、协议或门限。

## 已完成证据

- diagnosing-bugs skill、契约 review、capture schema review 已完成。
- client-only AF_PACKET/TUN 工具通过 sudo loopback 动态测试和合成 regression：confirmed true、完整 node/boot/session/request timestamp/channel/反向 endpoint、confirmed false challenge、duplicate、严格 `<230ms` 边界均通过。
- schema3 输出 `request_count`、`paired_confirmed_count`、`timely_count`、`late_count`、`unmatched_count`、right-censor、challenge 及两个显式 candidate 分母；wire ACK receive gap 与 timely request send gap 分开，均不宣称 app accepted。
- fl03 断电恢复后九板独立审计通过；旧第二模型批次 `cleanup_ok=false` 和独立 audit 失败保持原值，事实分类追加为供电/环境中断。
- fl02 新 boot `8ec76341-ea5e-455e-a007-a2c182a60469`，15W/mode0、release/native、149/40/LGI、FEC8/12、source/iw 全部只读核验；全八 READY 后新 idle 120s 完成，证据在 `evidence/heartbeat-budget-eight-20261110T154206Z/power02-recovery-20261110T161403Z/idle-20261110T1615Z/`。

## 新 idle 摘要

fl02 恢复后窗口为 58 request、58 paired confirmed、58 timely、0 late、0 unmatched；drops=0，RTT p50 约 50.45ms，application acceptance 未观测。fl01 末端 230ms right-censor 1 个，fl03 有 1 个 paired late；这些不被解释为物理 loss 或 app anchor gap。bounded daemon journal 无条目，hunt 计数保持 null。

## Model 边界

`model-20261110T1606Z` 仅完成 preflight，fl02 `No route` 阻断，0-byte、未发送、未改频；capture partial 独立保留，不自动重跑。fl02 恢复后 model 尚未开始：fl09 在空间维护前变为 `No route`，未 unlink，故当前仍禁止 model。fl02、fl08 已按本地完整成功证据精确回收 3 个 duplicate 各自达到空间门槛；fl09 需恢复管理面后重新只读空间/preflight，不能降门槛。

## Comments

- 2026-11-10：唯一远端 owner 记录环境中断、保留原始失败与新恢复 root；父侧继续决定 model 是否在 fl09 恢复后执行。

## 当前停止边界

fl02恢复后的120秒空闲：464请求、463配对、462及时、1晚到、1右删失、0完整窗口未配对，socket drops0；fl03晚到约1068.70ms。模型在发送前因环境中断未开始。父侧最终只读复核fl08/fl09均SSH No route to host、registry OFFLINE，供电原因尚未经确认；待恢复完整八端后继续模型负载测量。230ms/10秒未改，不能下最终门限结论。证据见本批README。
