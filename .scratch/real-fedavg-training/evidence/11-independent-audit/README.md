# 工单 11：独立 FP64 审计工具证据

## 范围

本目录记录工单 11 的纯工具实现、TDD 和类型检查，不声称已经完成三次真实五轮硬件作业，也不填充无线、Web 或部署环境事实。正式三次作业由 13 在 08/12 等前置完成后执行。

实现只新增：

- `wfb_ng/fl/fp64_audit.py`
- `wfb_ng/tests/test_fl_fp64_audit.py`
- `docs/deployment/MNIST独立FP64审计操作.md`

没有修改 Coordinator、MNIST、reference model、artifacts、Runtime 或 11 工单状态。审计只消费 05 `rounds[].updates` 和模型引用，不读 Runtime 重启状态。

## 固定契约

- 工具版本：`1.0.0`。
- 审计规则：`fp64-fedavg-v1`。
- 参考计算：CPU `torch.float64`，实际聚合输出：CPU `torch.float32`。
- 判定：`abs(actual-reference) <= 1e-6 + 1e-5 * abs(reference)`。
- 参与集合、节点、轮序、base model hash、sample count、有限性、参数键/shape/dtype 和文件 size/SHA-256 均严格校验。
- 备份包括 M0、每轮聚合模型、每个实际 Server update、相邻 update manifest、summary 及 summary manifest；每份目标再次校验摘要。
- wireless/Web/environment 没有作为工具输入，报告明确记录 `missing`，不生成占位成功事实。

## 检查结果

`new-tests.txt` 记录本轮实际 14 项 pytest cases：

- 正式生产聚合函数的 1:3 可区分参数对照；
- 独立 FP64 stack*weights.sum(dim=0) 逐张量误差、权重和备份；
- 最简 CLI 及报告输出；
- 缺失、篡改和非有限 update 的 fail-closed；
- summary manifest 的 job_id/summary_path 绑定；
- 已存在 `--out` 拒绝覆盖；
- update manifest/path/body round binding；
- summary 缺失 `execution_result` 时直接拒绝，不按 status 兼容回退；
- 固定容差超限保留逐轮错误、最大误差/违规数和 partial backup；
- backup 目标重新读回大小/摘要校验，包括损坏目标拒绝；

`typecheck.txt` 记录新模块和新测试的 mypy 检查；`pycompile.txt` 记录语法编译检查。当前结果来自共享工作树 HEAD `b28e7fb` 的 focused 执行，保留其他主线未提交修改；没有运行 control imports 大集，共享工作树 core 未被本票修改。

## 证据边界

本目录不包含真实作业的 M0～M5 或 45 份 update。没有实际 11/13 作业时，不创建数值通过报告、不宣称持久备份已完成。操作员指定 08/13 的持久位置后，按操作文档执行 CLI 并把 CLI 输出和 `backup-manifest.json` 纳入正式总报告。
