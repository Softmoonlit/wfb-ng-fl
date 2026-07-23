# tests/real_hardware 目录说明

## 目录角色

`tests/real_hardware/` 用于放置真实硬件、三机演示、正式证据生成与现场排障相关脚本和手册。

这是当前最容易混入“现役入口”和“历史脚本”的目录，使用前请先读本文件。

## 当前正式入口

当前 `v6` real-hardware uplink 默认正式入口是：

- 手册：`v6新底座无SSH手动上行演示手册.md`
- 配套执行脚本：`v6_manual_uplink_demo.sh`

仓库根目录 `Makefile` 的 `acceptance_v6_realhw` 也会把操作者引导到这套入口。

当前 `v8` / GitHub issue #41 的真实硬件 FL Runtime 闭环验收入口是：

- 手册：`v8_issue41_SSH编排真实硬件FL闭环验收手册.md`
- 计划配套执行脚本：`issue41_fl_runtime_loop.sh`

该入口通过 SSH 编排三台真实机器完成代码同步、安装、UFTP/HTTP smoke、systemd Runtime loop、lifecycle 和归档。SSH 编排、远端仓库同步和归档汇总机身份仅属于 issue #41 验证阶段，不是正式产品运行语义。

## Issue #55-#57 临时模拟入口

#53 的真实硬件链路问题解决前，可按 `issue55_57_临时终端模拟演示手册.md` 使用三个固定角色入口。它们通过管理网 TCP 控制消息执行真实的三端阶段同步，但模型和 update 传输仍为计时模拟；入口不启动角色服务、不传输 FL 数据、不写入证据，不能作为任何真实硬件 issue 的验收结果。

公共 shell 入口是 `issue55_57_terminal_demo.sh`，控制器是 `issue55_57_demo_control.py`。现场应使用 `issue57_server_demo.sh`、`issue56_client1_demo.sh` 和 `issue56_client2_demo.sh` 三个固定角色入口；三个入口接受相同的 Mbps 位置参数，并根据 40 MiB 大小计算演示时长。

## 当前有效的辅助文件

- `v6_formal_2a_summary.py`：生成 real-hardware 统一 2A 摘要
- `test_v6_manual_uplink_wfb_args.py`：校验手动上行脚本传给 `wfb_v6_uplink` 的关键参数
- `test_v6_manual_uplink_dashboard_pty.py`：覆盖手动上行面板相关行为
- `v6_manual_uplink_make_source.py`
- `v6_manual_uplink_tcp_send_progress.py`
- `v6_manual_uplink_tcp_recv_progress.py`

## 变体入口与历史文件

此前遗留的 SSH 变体手册、token-gated split-process 诊断脚本、one-click 包装入口以及旧的大文件传输/摘要链路脚本，已经从当前仓库清理，不再作为可运行入口保留。

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
