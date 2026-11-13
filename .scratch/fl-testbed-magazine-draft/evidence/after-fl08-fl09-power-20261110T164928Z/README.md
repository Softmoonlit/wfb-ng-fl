# fl08/fl09 供电恢复后的八端 model 基线

## 结果

固定 `fl00 + [fl01, fl02, fl03, fl05, fl06, fl07, fl08, fl09]`，排除 fl04/fl10。用户确认 fl08/fl09 供电恢复后，本轮新证据根为 `after-fl08-fl09-power-20261110T164928Z/`，冻结 bundle SHA 仍为 `1ff80d4683137c191328ef524b910143a2dae462b9c6854e615412da61d46496`。

本批先后执行两个实际模型run，均为独立记录：首次 `after-fl08-fl09-power-20261110T171130Z-model` 完成通信但采集结果丢失；修复远端落盘后，另一次 `after-fl08-fl09-power-20261110T180211Z-model` 完成通信并取得fl02/fl08的部分有效心跳样本，六端因统计异常丢失原始JSON。后一次独立恢复复核又遇到fl07管理离线。因此本批未完成八端心跳预算验收，230 ms与10秒保持不变，暂停继续发送。

以下至“180211 新测量修正”前的通信耗时及收尾清单仅描述首次run `after-fl08-fl09-power-20261110T171130Z-model`，载荷 `44,770,302` bytes，SHA `4b55cee2f0bdcfbbf610167a505efee0e2c50e3306fed02bda3613807c234e96`。

- pre-transfer hardware gate 无错误，九板实际 source/native、15W、radio/FEC、容量和 READY/IDLE 均通过。
- 下行 server send-complete 约 `41.630s`；最后一个 client receive verify 约 `45.672s`（均从 publish dispatch 起）。
- 上行 server 收齐八份约 `258.291s`；八份独立 size/SHA verify 全部匹配。
- runner `transfer_verified=true`、`cleanup_ok=true`、`restore_ok=true`，九端 `audit_final` 全部 true，post inventory errors 为空。
- model 总 wall 时间约 `322.080s`；post-model check-all-ready 和 independent audit 均 rc0。

完整时序、manifest、SHA、cleanup、audit、阶段限制见 [model-summary.json](./model-summary.json)。

## Capture 限制

schema3 passive capture 在 model start 前启动，`capture_ready` marker 为 true，并保存了各节点 timeout PID、lifecycle、停止记录和 journal stderr。但本轮结束时对本地 `timeout -> ssh -> sudo timeout` 链发 SIGTERM，八个 SSH 均以 rc255 收尾，远端 Python 的最终 JSON 未回传；八份 raw JSON 均为 0 bytes。bounded journal 命令还因纳秒级 `--since` 时间戳被拒绝，stderr 原件保留。

因此本轮没有可用 schema3 请求/配对样本：`request_count`、`paired_confirmed_count`、`timely/late/unmatched/right_censored`、state/HUNTING/challenge、socket drops、daemon hunt 均为未知，不能写成 0。不能从成功 model 传输推断 230ms deadline 或 10s liveness，也不能声称 app accepted。capture 原始空文件和 timeout rc255 属于本轮采集生命周期阻断，未删除或伪造成性能样本。

## Admission 与存储

三副本预留为 `671,181,818` bytes。fl09 通过精确成功副本回收后为 `704,249,856`；fl01、fl02、fl07 也因统一 pre-transfer gate 分别精确回收到 `674,279,424`、`690,814,976`、`683,982,848`。所有删除均逐文件核对 size/SHA/nlink/stat，达到门槛即停止；B2、post-fl02 首次成功、second-eight 失败批次及失败/partial/log/manifest/evidence/install 未删。

fl02 新 boot 为 `8ec76341-ea5e-455e-a007-a2c182a60469`；fl08/fl09 新 boot 分别为 `73510648-529d-41ac-bf0f-eec42de0168a` 和 `f4dc7ed0-48a4-464e-b03b-470d9644682f`。所有 boot、wall/monotonic admission marker 与 storage 原件在本目录保存。

## 收尾清单

- [x] model runner 已退出，唯一 run-id 远端目录已收尾。
- [x] 九端 resident daemon/native/TUN 保持常驻，model 临时 units inactive。
- [x] 149/HT40/Long GI/原 MCS/15W 恢复，post audit rc0。
- [x] capture timeout/SSH/PID/lifecycle/raw/journal 错误全部保留。
- [x] 未修改协议、native、通用通信工具或生产端口；未自动 retry model。

本目录的 model baseline 可用于传输耗时和完整性事实；它不是本批 heartbeat deadline 的有效样本。后续采集方案的修复与新测量结果单列于下文，不拼接空raw或将工具修复后的新测量误称为原run的重试。

## 180211 新测量修正

父批准后使用远端文件落盘方案执行新 run `after-fl08-fl09-power-20261110T180211Z-model`，runner 双向传输、八份 SHA、cleanup/restore 均成功。capture 的六端 raw 为空不是 SSH 原因：对应远端 stderr 全部为 collector `annotate` line 231 的 `TypeError: '>' not supported between instances of 'NoneType' and 'float'`，因为 singleton identity gap 为 `None` 而另一 identity gap 为 float。fl02/fl08 raw 可读，已按 model wall bounds、channel 157、request state IDLE、paired 样本重算；149 gap 按连续 channel epoch 单列，跨切频 gap 不用于 10s 判断。完整 per-node 结果见 `model-20261110T180211Z/heartbeat-load-summary.json`。

fl07 在 model runner 完成后独立 post-audit 重查为 `No route to host`，分类为外部 management interruption；不自动 retry 或追加 model。