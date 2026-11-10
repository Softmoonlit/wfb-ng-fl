# 本轮新增文件交接清单

本清单只列本批小证据、脚本和运行日志；明确排除 runner 生成的 payload `.bin`、cache、`__pycache__`、模型源包和 install 内容。

## 根目录

- `README.md`
- `progress-status.json`
- `hardware-from-actual.json`
- `preflight-readonly.json`
- `release-audit.json`
- `release-actualargs-rebind.json`
- `fl00-service-config-update.json`
- `bundle-manifest.json`
- `batchhardwareprobe.py`
- `final-recovery-audit.py`
- `final-recovery-audit.json`
- `final-recovery-audit-initial.json`
- `final-recovery-audit-recheck.json`
- `fl02-bounded-diagnosis.json`
- `fl02-model-server-update-audit.json`
- `fl08-storage-reclamation.json`
- `smoke-result.json`
- `model-result.json`
- `check-all-ready.json`
- `check-all-ready.stderr`
- `run-id-preflight.json`
- `executor-process.json`
- `executor-process-failed-actualargs.json`
- `executor-result.json`
- `executor.pid`
- `runner-process.json`
- `raw-inventory-pre.json`
- `raw-inventory-post.json`
- `raw-inventory-pre-failed-actualargs.json`
- `raw-inventory-post-failed-actualargs.json`
- `model-raw-inventory-pre.json`
- `model-raw-inventory-post.json`
- `executor-console.log`
- `executor-console-failed-actualargs.log`
- `batch-runner.log`

## 三次 run 目录

- `runs/eight-20261110T131800Z-smoke/progress.jsonl` 及 9 个 `fl*.stderr`
- `runs/eight-20261110T132200Z-smoke/progress.jsonl` 及 9 个 `fl*.stderr`
- `runs/eight-20261110T132500Z-model/progress.jsonl` 及 9 个 `fl*.stderr`

## 说明

`eight-20261110T131800Z-smoke` 是 prepare-only 的旧 release actualargs 失败；`eight-20261110T132200Z-smoke` 是成功 smoke；`eight-20261110T132500Z-model` 是保留的 fl02 长尾失败。`raw-inventory-pre/post.json` 当前为 model inventory；成功 smoke 的 raw inventory 未独立保存，已在 `smoke-result.json` 和 README 明确标注，不冒充 smoke 原始证据。
