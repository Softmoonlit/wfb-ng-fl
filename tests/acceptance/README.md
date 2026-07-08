# tests/acceptance 目录说明

## 目录角色

`tests/acceptance/` 用于放置 namespace/隔离环境下的验收脚本。这里的脚本更偏自动化验收与结构化结果产出，不等同于 real-hardware 主入口。

## 当前推荐入口

当前 `v6` namespace 入口是：

- `v6_uplink_namespace.sh`
- `v6_downlink_namespace.sh`

它们分别覆盖：

- `v6` 上行 namespace 验收
- `v6` 下行 shared/feedback namespace 验收

## 历史 / 非默认文件

旧 `v1`、`v2`、`v3`、`v4` 阶段脚本已经从当前仓库清理，不再作为历史可运行入口保留。当前应只把本目录中的 `v6` namespace 脚本视为有效入口。

## 与 real-hardware 的边界

- `tests/acceptance/`：namespace/隔离环境验收
- `tests/real_hardware/`：三机真实硬件/现场演示与正式证据入口

当前 real-hardware 主入口不在本目录，而在 `tests/real_hardware/README.md` 所指向的手册与脚本。

## 下一步应该看哪里

- 跑 namespace 验收：直接阅读对应 `v6_*_namespace.sh` 脚本头部说明
- 跑真实硬件：转到 `tests/real_hardware/README.md`
