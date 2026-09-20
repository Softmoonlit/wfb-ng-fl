# 07: 执行三机 Issue 41 唯一正式硬件验收

**What to build:** 在三台已上线且可由当前 server 通过 SSH 控制的机器上，以一个全新的 run ID 执行 Issue #41 唯一正式验收。该次运行从拓扑发现和 preflight 开始，在同一归档中完成连续三周期双向数据面 gate、正式一轮 Runtime 和完整 lifecycle，并由最终验证器给出唯一 passed 或 failed 结论。

**Blocked by:** 06 组装唯一的 Issue 41 fail-closed 正式入口

**Status:** ready-for-agent

- [ ] 本次运行重新发现并归档三机主机身份、管理地址、仓库位置和实际无线接口，不使用旧现场值。
- [ ] 三台机器运行同一分支和 commit，工作区干净，安装产物和角色配置可追溯。
- [ ] 管理网只用于 SSH 编排和证据回收，模型和 update 的成功事实全部来自真实 TUN 与无线数据面。
- [ ] 同一正式 run 内连续完成三个双向数据面周期，且每个周期的 UFTP、HTTP、大小、SHA-256、固定整体 deadline 和链路遥测证据完整。
- [ ] 同一正式 run 内完成安装后 systemd 角色服务的一轮 Runtime 四接口严格同步闭环，并满足固定整体轮次 deadline。
- [ ] 同一正式 run 内完成 restart/no-overlap、unit、cgroup、TUN 和无孤儿进程生命周期验证。
- [ ] smoke 与 Runtime 的解析后链路配置一致，正式配置不包含候选 feedback 行为。
- [ ] READY/GRANT、authorized sends、server 接收、queue/backpressure、reassembly、sender isolation、feedback、丢包、FEC 恢复、TCP 重传、队列峰值和阶段耗时证据完整；自然发生 queue pause 时存在对应 resume 且最终恢复。
- [ ] 成功时唯一正式归档通过最终验证器，所有必需分区均为 passed，并保留完整原始证据。
- [ ] 失败时仍生成不可覆盖的 failed 归档，记录最后成功层、第一失败层和失败分类，不在同一 run 内调参续跑。
- [ ] 环境、工具或现有实现修复后使用新 run ID 重跑；只有直接证据证明链路契约不足时才创建独立 blocker spec 和 tickets，本票在 blocker 完成前保持未完成。
- [ ] 正式通过后状态转为 `need-for-review`，等待相对主线的标准与规格双轴代码审查。
