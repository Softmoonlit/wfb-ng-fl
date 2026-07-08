# tests/real_hardware 目录说明

## 目录角色

`tests/real_hardware/` 用于放置真实硬件、三机演示、正式证据生成与现场排障相关脚本和手册。

这是当前最容易混入“现役入口”和“历史脚本”的目录，使用前请先读本文件。

## 当前正式入口

当前 `v6` real-hardware uplink 默认正式入口是：

- 手册：`v6新底座无SSH手动上行演示手册.md`
- 配套执行脚本：`v6_manual_uplink_demo.sh`

仓库根目录 `Makefile` 的 `acceptance_v6_realhw` 也会把操作者引导到这套入口。

## 当前有效的辅助文件

- `v6_formal_2a_summary.py`：生成 real-hardware 统一 2A 摘要
- `test_v6_manual_uplink_wfb_args.py`：校验手动上行脚本传给 `wfb_v6_uplink` 的关键参数
- `test_v6_manual_uplink_dashboard_pty.py`：覆盖手动上行面板相关行为
- `v6_manual_uplink_make_source.py`
- `v6_manual_uplink_tcp_send_progress.py`
- `v6_manual_uplink_tcp_recv_progress.py`

## 变体入口与历史文件

- `v6新底座现场演示手册.md`
  - 这是 SSH 变体演示手册，不是当前默认正式入口
- `test_token_gated_uplink.sh`
  - 旧 split-process / token-gated 真实硬件诊断脚本，不作为 `v6 trusted_plaintext` 正式证据入口
- `one_click_test.sh`
  - 旧 token-gated split-process 包装入口，不是当前 `v6` 主入口
- `test_full_transfer.sh`、`collect_metrics.sh`、`generate_report.sh`
  - 保留的历史/补充链路资产，使用前应先确认是否仍适配当前目标

## 不要误用的判断原则

- 看到 `wfb_rx` / `wfb_tx` / `wfb_token_scheduler` / `-K`，通常说明它不是当前 `v6 trusted_plaintext` 主入口
- 看到 same-host / netns / split-process 语义，通常说明它不是当前三机手动上行正式入口
- 正式 real-hardware uplink 入口应围绕 `wfb_v6_uplink --role server/client` 展开

## 证据口径

当前 uplink 正式证据至少应保留：

- `result.md`
- `formal_2a_summary.json`
- 原始日志
- queue summary
- SHA256 结果

## 下一步应该看哪里

- 做当前正式三机上行：先看 `v6新底座无SSH手动上行演示手册.md`
- 做 namespace 验收：转到 `../acceptance/README.md`
- 看仓库级测试导航：转到 `../README.md`
