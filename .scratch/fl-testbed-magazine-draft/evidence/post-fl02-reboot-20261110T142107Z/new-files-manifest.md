# 本轮小证据清单

批次：`post-fl02-reboot-20261110T142107Z`

## 准入与清理

- `storage-reclamation.json`：fl08/fl09 精确成功重复清理，逐路径删除前后 stat、SHA、B2 保留与空间结果。
- `admission-post-reclamation.json`：清理后九板硬件及三模型 reserve admission。
- `admission-post-reclamation.rc`、`check-all-ready-post-reclamation.rc`、`check-all-ready-post-reclamation.stdout`、`check-all-ready-post-reclamation.stderr`。
- `final-recovery-audit-pre-smoke.json`：重启后、smoke 前旧独立审计原件。
- `final-recovery-audit-after-smoke.json`：smoke 后独立审计原件。
- `final-recovery-audit.json`、`final-recovery-audit-model.rc`、`final-recovery-audit-model.stdout`、`final-recovery-audit-model.stderr`：model 后最终独立审计。

## Smoke

- `smoke/batchhardwareprobe.py`、`smoke/hardware-from-actual.json`：本阶段独立 wrapper 与硬件映射副本。
- `smoke/raw-inventory-pre.json`、`smoke/raw-inventory-post.json`：九板传输前后 raw inventory。
- `smoke/run-id-preflight.json`、`smoke/executor-process.json`、`smoke/runner-process.json`、`smoke/executor-result.json`。
- `smoke/batch-runner.log`、`smoke/runs/post-fl02-20261110T142107Z-smoke/progress.jsonl`、各端 `.stderr`。
- `smoke/cli-argument-error/`：第一次仅 argparse 失败的 wrapper 证据，未启动传输，未覆盖正式 smoke 证据。

## Model

- `model/batchhardwareprobe.py`、`model/hardware-from-actual.json`、`model/model-manifest-input.json`：本阶段独立副本。
- `model/raw-inventory-pre.json`、`model/raw-inventory-post.json`：九板传输前后 raw inventory。
- `model/run-id-preflight.json`、`model/executor-process.json`、`model/runner-process.json`、`model/executor-result.json`。
- `model/batch-runner.log`、`model/runs/post-fl02-20261110T142107Z-model/progress.jsonl`、各端 `.stderr`。

## 摘要

- `validation-summary.json`：准入、smoke、model、最终 registry 与文件发送状态。
- smoke payload SHA：`30e14955ebf1352266dc2ff8067e68104607e750abb9d3b36582b8af909fcb58`。
- model payload size/SHA：`44770302` / `4b55cee2f0bdcfbbf610167a505efee0e2c50e3306fed02bda3613807c234e96`。

本清单显式排除 `*.bin` payload、cache 与 `__pycache__`；这些文件未纳入清单，不代表删除历史 evidence。执行代理未暂存或推送；实际已通过WFB发送小文件与模型载荷，详见原始进度。
