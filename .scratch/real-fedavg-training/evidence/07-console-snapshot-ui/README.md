# 工单 07：Console 快照与训练结果页面证据

状态：`need-for-review`，本轮不提交 commit。

## 实现边界

本票只修改 Console/UI 与页面检查：

- `wfb_ng/fl/console.py`
- `wfb_ng/fl/static/index.html`
- `wfb_ng/fl/static/console.js`
- Console/page/browser tests

Console projector 只消费 Server Daemon 已在内存中的 raw job/summary facts。它不导入 torch，不读取 update/model 文件，不解析 `summary.rounds[].updates`，不读取 Runtime manifest，也不建立缓存指纹或历史平台。05/06 主线提交后，宿主把参考算法 metadata 放入 `summary.rounds[].training`，07 页面即可直接显示。

## 已验证事实

Console 白名单覆盖：

- 初始及逐轮 metrics、模型 SHA/大小、未判定 verdict
- 冻结算法入口和安全配置摘要
- 固定参与者当前在线事实、准备报告、训练 metadata
- `recovery_missing_nodes`
- `recovery_reports`（去掉 source address）
- `server_recovery_complete` 三态事实
- `additional_errors`、`first_failure`、`liveness_events`、client error
- execution/effect/recovery 的独立显示

页面在缺少指标时显示“未取得”，缺少 verdict 时显示“未判定”，合法的 `0` 指标保留为 `0`，不伪造成功。

## Checks

- Console controlled snapshot and Standards edge fixtures：5 项 targeted tests passed
- Full `test_fl_console.py` baseline（主线提供的 `/tmp/wfb07-console-resumed.txt`）：34 passed
- Host metadata helper/宿主相关 checks：主线提供 37 passed
- `test_fl_sse_abort.py` + `test_fl_online_target_selection.py`：20 passed
- Packaged Console VM：通过
- Online target VM：通过
- Chromium CDP online/selection/reload helper：通过
- Chromium CDP training/evaluation/recovery/reload helper：连续 3/3 通过
- `mypy --follow-imports=silent --ignore-missing-imports --check-untyped-defs wfb_ng/fl/console.py`：通过
- Python compile、Node syntax check、`git diff --check`：通过

TDD 覆盖受控内存 snapshot 白名单、未知算法 config 拒绝、zero/10**400 指标稳健性、terminal 未确认提交显示、06 recovery facts、页面 VM 行为和 HTTP/SSE/reload 选择恢复。真实 Chromium 检查阻断 SSE、等待新 `Page.loadEventFired`，拒绝旧 DOM sentinel，直接断言主 `job.recovery.state/recovery_state=unconfirmed`、M0/M1/M2 每行模型 SHA/损失/准确率/效果/提交状态、最终 SHA 和 reload 后 `/api/v1/jobs` 请求数为 0。

浏览器检查均使用 `/snap/bin/chromium` headless、Node 原生 WebSocket/CDP 和 loopback 临时 HTTP 端口；未连接真实 9001/9002/9200，也未改变无线配置。

## Pending

- 等待 Server 06 宿主 wrapper 把参考算法 8-field metadata 接入实际 `summary.rounds[].training`；当前 controlled facts 和 UI projector 已就绪。
- 无线事务结果仍由后续无线工单接入同一快照契约。
- 07 不修改 `server_daemon.py`、Coordinator 或 MNIST；不提交 commit，等待主线统一 check/review。
