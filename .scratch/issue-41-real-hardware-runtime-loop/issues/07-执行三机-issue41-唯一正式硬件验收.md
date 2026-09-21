# 07: 执行三机 Issue 41 唯一正式硬件验收

**What to build:** 在三台已上线且可由当前 server 通过 SSH 控制的机器上，以一个全新的 run ID 执行 Issue #41 唯一正式验收。该次运行从拓扑发现和 preflight 开始，在同一归档中完成连续三周期双向数据面 gate、正式一轮 Runtime 和完整 lifecycle，并由最终验证器给出唯一 passed 或 failed 结论。

**Blocked by:** 06 组装唯一的 Issue 41 fail-closed 正式入口

**Status:** need-for-review

- [x] 本次运行重新发现并归档三机主机身份、管理地址、仓库位置和实际无线接口，不使用旧现场值。
- [x] 三台机器运行同一分支和 commit，工作区干净，安装产物和角色配置可追溯。
- [x] 管理网只用于 SSH 编排和证据回收，模型和 update 的成功事实全部来自真实 TUN 与无线数据面。
- [x] 同一正式 run 内连续完成三个双向数据面周期，且每个周期的 UFTP、HTTP、大小、SHA-256、固定整体 deadline 和链路遥测证据完整。
- [x] 同一正式 run 内完成安装后 systemd 角色服务的一轮 Runtime 四接口严格同步闭环，并满足固定整体轮次 deadline。
- [x] 同一正式 run 内完成 restart/no-overlap、unit、cgroup、TUN 和无孤儿进程生命周期验证。
- [x] smoke 与 Runtime 的解析后链路配置一致，正式配置不包含候选 feedback 行为。
- [x] READY/GRANT、authorized sends、server 接收、queue/backpressure、reassembly、sender isolation、feedback、丢包、FEC 恢复、TCP 重传、队列峰值和阶段耗时证据完整；自然发生 queue pause 时存在对应 resume 且最终恢复。
- [x] 成功时唯一正式归档通过最终验证器，所有必需分区均为 passed，并保留完整原始证据。
- [x] 失败时仍生成不可覆盖的 failed 归档，记录最后成功层、第一失败层和失败分类，不在同一 run 内调参续跑。
- [x] 环境、工具或现有实现修复后使用新 run ID 重跑；只有直接证据证明链路契约不足时才创建独立 blocker spec 和 tickets，本票在 blocker 完成前保持未完成。
- [x] 正式通过后状态转为 `need-for-review`，等待相对主线的标准与规格双轴代码审查。

## 验收执行记录

- **验收时间**: 2026-09-21 14:57:21
- **Run ID**: `v8_issue41_20260921_145721`
- **共同提交**: `595f5ff281c0b2caafe176aec849bba5dcf77849`
- **归档路径**: `tests/logs/v8_issue41_20260921_145721`
- **最终结论**: `passed`（校验器 `issue41_validate_archive.py` 退出码 0，全量 247 个单元测试全部通过）
- **五个分区结果**:
  1. `orchestration`: `passed`（8 层 fail-closed 预检通过，拓扑 3 节点，USB 速率 server=480M, client1=5000M, client2=480M）
  2. `pre_runtime_smoke`: `passed`（连续 3 周期双向数据面 Gate 全部通过，周期总耗时分别为 13.15s, 12.51s, 13.06s，单周期完成时间远在 240s deadline 内，进程在三轮间保持常驻未重启）
  3. `formal_runtime_loop`: `passed`（角色服务配置与 Gate 链路配置严格等价核验通过；单轮 4 MiB 严格同步闭环在 10s 内完成，shared UFTP downlink 一次交付两 client，双 4 MiB HTTP PUT 上行返回 HTTP 201 且 digest 一致）
  4. `lifecycle`: `passed`（first_stop 全部 inactive/cgroup clean/无孤儿；restart 后 PID 不复用 server: 225981 vs 225360, client1: 160780 vs 159375, client2: 161745 vs 160098，cgroup 无重叠且 TUN 均恢复 UP；second_stop 再次全部 inactive/cgroup clean/无孤儿）
  5. `conclusion`: `passed`（reason: "一轮 4 MiB 严格同步确定性场景证据完整"）
