# 06: 组装唯一的 Issue 41 fail-closed 正式入口

**What to build:** 把已经独立验证的三周期数据面 gate、正式一轮 Runtime 和 lifecycle 组装成 Issue #41 唯一正式运行行为。入口复用运行包络已经建立的 run identity、preflight、失败归档和分类机制，负责固定阶段顺序、比较 smoke 与 Runtime 的解析后配置、汇总已有分区，并让最终验证器只对同一次完整 run 给出 passed 或 failed 结论。

**Blocked by:** 03 实现可审计的连续三周期双向数据面 gate; 05 验证三角色服务 restart 与资源生命周期

**Status:** resolved

- [x] 正式入口严格按 preflight、数据面 gate、配置等价比较、Runtime、lifecycle、收集和最终验证的顺序组合已有行为。
- [x] gate 未通过时 Runtime 不启动；Runtime 未通过时不得报告 lifecycle 或总体验收成功。
- [x] smoke 与 Runtime 的信道、带宽、MCS、FEC、方向链路域、TUN、队列阈值和 feedback 配置逐项相同。
- [x] 成功运行只产生一个包含 orchestration、gate、Runtime、lifecycle 和 conclusion 分区且通过验证器的正式归档。
- [x] 任一阶段失败都复用同一 run 的受控停止、收集和分类机制生成 failed 归档，不丢失已完成阶段的证据。
- [x] 最终验证器拒绝不同 run ID 的证据、被覆盖的旧归档、缺失分区、运行中修改配置、diagnostic 模式和候选 feedback 行为。
- [x] 每个 smoke 周期和正式轮次的固定整体 deadline 均进入解析配置与归档，且运行中不可修改。
- [x] 最终差异不包含 Issue #53 场景、旧 fixture profile、终端演示、立即 feedback window 候选或任何兼容/fallback 路径。
- [x] 现场手册与唯一正式入口、执行前置条件、阶段顺序、失败分流和通过条件一致。
- [x] 完整合成运行、各阶段失败、配置不等价、混合 run、缺失分区和 diagnostic 模式的自动化测试通过。

## 验证结论

1. **唯一正式入口重构与阶段顺序固定 (`tests/real_hardware/issue41_fl_runtime_loop.sh`)**：
   - 确立 `issue41_fl_runtime_loop.sh run-all` 作为 Issue #41 的唯一正式总入口，严格按阶段顺序执行：`preflight`（8层fail-closed预检） -> `install` -> `smoke-gate`（同组进程连续3周期双向Gate） -> `verify-config-equivalence`（严格链路配置等价性核验） -> `run-runtime-loop`（正式Runtime四接口闭环） -> `lifecycle-stop-restart`（stop/restart/no-overlap审计） -> `collect` -> `summary`。
   - 实现了阶段依赖硬阻断：Gate 未通过时禁止启动 Runtime；Runtime 未通过时禁止标记 lifecycle 或总体验收通过；任意阶段失败时受控停止并生成 failed 归档。

2. **配置等价性与不可修改性强约束 (`issue41_envelope.py`, `issue41_gate.py`, `issue41_validate_archive.py`)**：
   - 包络初始化时将全量解析后配置 `resolved_config` 写入 `envelope.json`，涵盖无线参数（channel/channel_width/bandwidth/MCS/FEC/short_gi）、TUN 接口与规划地址、队列反压阈值、feedback window 周期及三大固定 deadline / 超时配置（`smoke_cycle_deadline_seconds=240`, `runtime_timeout_seconds=180`, `io_timeout_seconds=120`）。
   - 在正式 Runtime 启动前调用 `verify-config-equivalence` 逐项比对角色服务解析配置与 Gate 链路配置，拒绝任何不一致。
   - `issue41_validate_archive.py` 严格核验归档中的 deadlines 与 envelope 一致，拒绝任何运行中修改配置的行为。

3. **归档合规性审计规则升级 (`tests/real_hardware/issue41_validate_archive.py`)**：
   - 强校验 `envelope.json`、`pre_runtime_smoke`、`formal_runtime_loop`、`lifecycle` 及 summary 顶层 `run_id` 的全局一致性，拒绝混合 run。
   - 强校验归档未被覆盖（拒绝 `overwritten: true`）。
   - 强校验运行模式，非 `formal`（如 `diagnostic`）模式的归档严禁判定为 passed。
   - 深度递归检测并严厉拒绝 `--feedback-window-start-immediately` 候选 feedback 行为。
   - 强制五大必要分区完整存在（`orchestration`, `pre_runtime_smoke`, `formal_runtime_loop`, `lifecycle`, `conclusion`）。

4. **失败受控停止与已完成阶段证据保留**：
   - 在预检、Gate、Runtime、Lifecycle 各阶段发生失败时，调用 `record_stage_failure` 触发受控停止并保留前序已完成阶段证据，记录 `last_successful_layer`、`first_failing_layer`、`failure_category`（`environment`/`tooling`/`implementation`/`link_capability`）与 `failure_reason`，生成的失败归档自身完全符合校验器规则。

5. **现场手册更新 (`tests/real_hardware/v8_issue41_SSH编排真实硬件FL闭环验收手册.md`)**：
   - 全面更新验收手册，对齐 8 层 fail-closed 预检、连续三周期双向 Gate、配置等价核验、唯一入口顺序、五个归档分区结构及严格通过条件。

6. **测试用例与全量自动化验证**：
   - 新增 `tests/real_hardware/test_issue41_formal_entry.py`，完整覆盖合成运行通过、各阶段失败保留、配置修改拒绝、候选 feedback 拒绝、diagnostic 模式拒绝、混合 run 拒绝等场景。
   - 运行 `python3 -m unittest discover -s tests/real_hardware -p "test_*.py"`，全量 246 个自动化测试全部通过。
   - 提交已归入分支：`e6ac0d9 feat(issue41): 组装唯一的 issue41 fail-closed 正式入口与端到端归档校验`。
