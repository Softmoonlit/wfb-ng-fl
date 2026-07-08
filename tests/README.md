# tests 目录说明

## 目录角色

`tests/` 用于放置仓库内的人工测试入口、验收脚本、真实硬件脚本与测试辅助配置。

## 当前推荐入口

- 默认构建/测试入口：仓库根目录 `Makefile` 中的 `test` / `test_v6`
- namespace 验收入口：`tests/acceptance/`
- real-hardware 入口：`tests/real_hardware/`

## 子目录说明

- `tests/acceptance/`：namespace/隔离环境下的验收脚本
- `tests/real_hardware/`：真实硬件与现场演示相关脚本、手册、证据生成脚本
- `tests/config/`：测试配置预留目录
- `tests/logs/`：测试运行产物目录，不是手工维护入口

## 历史 / 弃用说明

不要仅凭文件名判断“正式入口”。当前入口以各子目录下的 `README.md` 与仓库根目录 `Makefile` 为准。

特别是：

- `tests/real_hardware/` 内同时存在当前入口、历史脚本与诊断脚本
- `tests/logs/` 只是运行产物，不应作为实现或流程依据

## 下一步应该看哪里

- 做 namespace 验收：先看 `tests/acceptance/README.md`
- 做真实硬件/三机演示：先看 `tests/real_hardware/README.md`
