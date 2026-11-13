# Capture 生命周期修复验证

## Scope

本 repair 只处理 passive schema3 capture 的远端持久化和停止生命周期。未修改 `capture-heartbeats-batch.py`、schema、协议、radio/native、model payload 或通用 communication 工具；原成功 model 保存在父目录的 `model-20261110T171130Z/`，本 repair 没有启动 model。

## 根因

原 model 轮次把 collector JSON 只接到本地 SSH stdout，并在 model 完成后 TERM 本地 `timeout -> SSH` 链。SSH 断开时远端 Python 的最终 stdout JSON 没有可靠落盘，八份 raw 为 0 bytes、SSH rc255。旧 bounded journal 查询另因纳秒级 `--since` 时间戳被 journalctl 拒绝。

## 修复实现

- 审核 collector 原文件未改，copy SHA 为 `30b03bda86536fe12e2f502ca26b7bc822defb8f3cd4ca8fd4a7758af54e3dd7`。
- 管理网将 collector 复制到每端独立目录 `/var/tmp/wfb-heartbeat-capture-repair-20261110T172426Z-v4/flXX/`；此目录与 `/srv/wfb-ng-fl/comm-*` 业务目录分离。
- 远端 root shell 写入 `collector.pid`，随后 `exec /usr/bin/python3 -I <具名远端路径> ... > raw.json 2> stderr`。model payload 未经过该路径。
- model 未启动的短验证为 5 秒被动窗口；model 不再依赖 SSH stdout。
- 短验证后通过独立 SSH 读取远端 `raw.json`、stderr、stat 和 journal；远端 raw/collector 文件保留。
- local communication tests 未改原代码，`Ran 59 tests in 0.476s`, `OK`。

## v4 结果

证据在 `short-capture-v4/short-capture-v4-validation.json` 和 `capture-repair-summary.json`。

- 八端 raw 全部非空，schema version 全为 3。
- 八端 `errors=[]`、正常 `stop_requested=false`、窗口约 5.000 秒。
- 八端 `PACKET_STATISTICS` drops 均为 0；请求数为 2 或 3，配对样本均有，属于工具短验证，不是 model 负载样本。
- 八端 remote collector SHA 与审核副本一致。
- post probe 八端均 `not_alive`，`v4-residual-after.txt` 未发现 capture collector 残留。
- 5 秒过短，独立 running PID probe 的 SSH 返回晚于 collector 正常退出，未形成 `alive_verified` 记录；因此本轮没有 TERM 任何 PID，也没有杀 wrapper。远端 `collector.pid`、raw、stat、launch lifecycle 和 probe stderr 均保留，不能把未观测 alive marker 伪称已验证。

## Gate

本 repair 现在只提交给父 review。父 review 前不启动 model；后续 model 若获批准，必须使用新的唯一 run-id，capture 先 ready、远端文件先落盘，model 完成后另 SSH 读取 raw。原成功 model 仍独立保留，不能称自动无线 retry。
