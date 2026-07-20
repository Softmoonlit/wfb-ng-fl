# tests/real_hardware 目录说明

## 目录角色

`tests/real_hardware/` 用于放置真实硬件、三机演示、正式证据生成与现场排障相关脚本和手册。

这是当前最容易混入“现役入口”和“历史脚本”的目录，使用前请先读本文件。

## 当前正式入口

- v6 双 client uplink 基线：`v6新底座无SSH手动上行演示手册.md` + `v6_manual_uplink_demo.sh`
- issue #41 三机 shared downlink/feedback + FL Runtime 闭环：`issue41三机正式验收手册.md`

issue #41 直接扩展 uplink 基线，不是另一套拓扑，也不接受 namespace/same-host 替代。

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

## Issue #41 辅助文件

- `preflight_checklist.sh`：按单机角色执行 `clean`/`runtime` fail-closed 检查
- `issue41_generate_configs.py`：生成三端正式角色配置、算法 JSON、systemd 环境文件和现场参数快照
- `issue41_ssh_orchestrate.sh`：由本机 server 通过 SSH 分阶段控制两台独立远程 client
- `issue41_ssh.env.example`：SSH 三机目标、仓库路径、网卡和归档参数示例
- `wfb_ng.fl.acceptance_fixture`：随正式安装产物提供、仅用 Runtime 四接口完成确定性单轮 fixture
- `issue41_collect.sh`：分别采集运行中快照和完成后 round 归档，不控制远端
- `issue41_collect_stopped.sh`：记录 unit stop、MainPID/cgroup 和无孤儿事实
- `issue41_restart_lifecycle.sh`：归档后执行 restart/active/cgroup/stop/no-orphan 生命周期检查
- `issue41_validate_archive.py`：离线输出 baseline/downlink/runtime 三份独立结论
- `test_issue41_archive.py`：合成完整 PASS 与缺证据 FAIL fixture

## 证据口径

v6 uplink 基线至少保留 `result.md`、`formal_2a_summary.json`、原始日志、queue summary 和 SHA256。

issue #41 还必须保留两 client x 两文件 UFTP matrix、feedback open/close/hit、算法 JSON、三端 round-state、model/update manifest 与 SHA、systemd/cgroup 运行中快照及停止后无孤儿记录。离线校验器缺证据即 FAIL，且分别输出：

- `baseline_uplink.json`
- `downlink_feedback.json`
- `runtime_loop.json`
- `formal_summary.json` / `result.md`

## 下一步应该看哪里

- 做当前正式三机上行基线：先看 `v6新底座无SSH手动上行演示手册.md`
- 做 issue #41 完整三机闭环：看 `issue41三机正式验收手册.md`
- 做 namespace 验收：转到 `../acceptance/README.md`
- 看仓库级测试导航：转到 `../README.md`
