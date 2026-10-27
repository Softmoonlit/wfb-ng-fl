# MNIST 独立 FP64 审计操作

本工具只审计服务端已经保留的真实模型和 update 数值。它不是三次五轮最终验收，不证明无线、Web、GPU 环境或官方数据来源；这些事实没有输入时，报告固定写为 `missing`。

## 输入边界

05 的 `coordinator_summary.json` 是唯一轮次索引。工具只读取 summary 中的：

- `target_nodes`、`rounds_total`、`rounds_completed` 和 `rounds`；
- 每轮 `input_model_path`/`input_model_sha256`；
- 每轮 `updates[node].path`/`size_bytes`/`sha256`；
- 每轮 `output_model_path`/`output_model_sha256`。

工具不扫描 Runtime 的 `rounds/`，不读取或重放 `round-state.json`，不依赖 UUID 排序，也不使用客户端沙箱。实际 Server update 应位于 05 保存的路径，通常形如：

```text
<server-work-dir>/job_<job_id>_role/rounds/<runtime-round-id>/updates/<node-id>/update.bin
```

输入模型和聚合模型通常位于：

```text
<server-work-dir>/job_<job_id>/artifacts/initial.pt
<server-work-dir>/job_<job_id>/artifacts/global-model-round-0001.bin
...
```

审计前必须确认作业角色已停止、服务端目录仍然存在，且 11/13 尚未完成备份和摘要确认。不要从 `.update.bin.*`、`.upload-preflight.*` 或 Client 的 `update-0001.pt` 取证。

summary manifest `coordinator_summary.manifest.json` 是必需的，且必须通过 `job_id`、canonical `summary_path`、summary SHA-256、schema 和 timestamp 校验。工具先对 summary 和 manifest 各做一次稳定字节 snapshot，再解析；后续报告和 metadata 备份使用这些 snapshot，不把“先 parse、后重新 hash”的两个读取混在一起。

## 最简 CLI

在服务端或已经能读取服务端制品的审计主机执行：

```bash
python3 -m wfb_ng.fl.fp64_audit \
  --job-root /persistent/wfb-ng-fl/server/job_<job_id> \
  --out /persistent/fp64-evidence/<run-id>/<job-id>
```

如 summary 不在默认位置，可显式指定：

```bash
python3 -m wfb_ng.fl.fp64_audit \
  --job-root /persistent/wfb-ng-fl/server/job_<job_id> \
  --summary /persistent/wfb-ng-fl/server/job_<job_id>/coordinator_summary.json \
  --out /persistent/fp64-evidence/<run-id>/<job-id>
```

退出码：

- `0`：所有轮次独立复算通过，且备份文件逐个摘要核对通过；
- `1`：输入缺失、篡改、身份/结构/有限性错误、容差失败或备份失败；报告仍保留失败原因；
- `2`：CLI 参数或工具启动错误。

`--out` 必须是操作者指定的尚不存在的新路径。工具拒绝已有目录、已有 report 或已有 backup manifest 的重试，不覆盖任何既有 evidence；失败时仍在本次新目录保留 partial report 和已核对的原始制品备份。

## 独立判定

工具加载固定 `mnist-two-conv-fp32-v1` 模型和 update，严格检查：

- 参与节点集合、节点键和每轮连续 `round_index`；
- input -> update `base_model_sha256` -> output 的模型链；
- update 的 `sample_count`、optimizer steps、有限训练损失和 GPU 描述；
- 参数键、shape、CPU/FP32 dtype、布局和所有有限性；
- summary 声明的 size/SHA-256 与实际文件；
- 每个实际 update 的相邻 manifest、路径 round/node 和 payload body identity。

每个参数张量都在 CPU 上把客户端 FP32 tensors stack 后转为 `torch.float64`，广播 `sample_count / sum(sample_count)` 权重并执行独立的 `sum(dim=0)`。生产 `aggregate_entry`、生产求和顺序和生产 accumulator 均不被导入或调用；测试中生产函数只用于独立 1:3 对照。实际输出仍按 FP32 模型读取，逐元素判定固定条件：

```text
abs(actual - reference) <= 1e-6 + 1e-5 * abs(reference)
```

任何结构、身份、dtype、非有限值或文件完整性错误都在数值比较前失败，不能由容差放过。容差失败不会提前丢弃已计算的 round/model/error 数据：报告保留每轮 input/output、每张量最大误差、最大允许误差和违规元素数，并继续备份已稳定核对的模型、update、update manifest、summary snapshot 和 summary manifest；CLI 返回非零。所有失败输出目录只能新建，不能用重试覆盖旧报告。


## 输出与版本

输出目录包含：

```text
fp64-audit-report.json
backup-manifest.json
backup/
  models/M0.<suffix>
  models/M1.<suffix> ... models/M5.<suffix>
  updates/round-0001/node-<id>/update.bin
  updates/round-0001/node-<id>/update.manifest.json
  metadata/coordinator_summary.json
  metadata/coordinator_summary.manifest.json
```

报告固定记录：

- `tool.name`、`tool.version`、`audit_rule.version`；
- summary 摘要、M0～M5（或实际轮数）的源路径/备份路径/大小/hash；
- 每轮 input/output hash、每个节点 update hash、sample count 和 weight；
- 每个张量的 shape、dtype、最大绝对误差、最大允许误差、违规元素数和结论；
- 备份清单及每个目标文件的再次摘要核对；
- `wireless`、`web`、`environment` 的 `status: missing`，并将 `overall_acceptance` 固定为 `not_assessed`；
- `total_report_facts` 中的 config、seed、all_attempts、client_audit、faults、radio 等未提供事实；summary 已有的 metrics 原样保留并标记来源为 `coordinator_summary`。

当前工具规则版本为 `fp64-fedavg-v1`，工具版本为 `1.0.0`。固定容差、FP64 计算语义或报告字段发生不兼容变化时升版本，不覆盖旧 evidence。只增加兼容字段时保持报告 schema 版本规则并在文档中记录。

备份目标会重新读回并校验大小和 SHA-256；损坏或短写目标直接拒绝，源文件和失败目录都保留。JSON 报告使用 `write_json_atomic()`。

## 受控权重检查

`wfb_ng/tests/test_fl_fp64_audit.py` 使用可区分参数和 1:3 样本数调用一次正式聚合函数，再用独立 FP64 结果比较；结果必须是 0.25/0.75 加权值，而不是等权值。该测试只证明生产函数的样本权重算术，不替代真实训练，也不进入审计 CLI 的运行路径。

## 清理边界

操作者必须在独立复核、备份和摘要确认完成后，再决定是否清理 Server 作业目录。工具不删除初始模型、update、聚合模型、Runtime 目录、Client 大日志或任何原始证据。11 工具通过不等于 13 的三次五轮、无线、效果或恢复验收通过。
