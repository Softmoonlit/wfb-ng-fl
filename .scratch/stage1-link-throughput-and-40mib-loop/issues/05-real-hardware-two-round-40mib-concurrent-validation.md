# 05: 三机真实硬件双 40 MiB 两轮并发正式验收与归档

**What to build:** 在三台真实物理机上执行升级后的端到端两轮双 40 MiB FL Runtime 闭环验收脚本，生成不可覆盖的正式运行归档，经归档验证器判定全 passed，通过服务生命周期审计，正式结算关联 GitHub 工单。

**Blocked by:** 04: 验收套件升级为场景驱动的两轮并发架构

**Status:** resolved

- [x] 三台物理机运行相同 commit，工作区干净，网卡配置正常。
- [x] 执行完整的端到端两轮双 40 MiB 闭环验收流程。
- [x] 生成不可覆盖的独立运行归档，各分区（orchestration、pre_runtime_smoke、formal_runtime_loop、lifecycle、conclusion）全部 passed。
- [x] 自动化归档校验器对归档执行全量校验，判定结果为 passed。
- [x] 角色服务生命周期审计（stop、restart、TUN 回收、无残留孤儿进程）通过。
- [x] 满足 GitHub Issue #54 验收条件，正式关闭 Issue #54 并关闭总 Epic Issue #50。

## Comments

### 2026-09-22 三机真实硬件双 40 MiB 两轮并发正式验收与归档执行报告

#### 1. 运行环境与拓扑状态

- **Commit**: `1adf7ecfc00d46c8a4bc81fda9848928906346e5`（三端 server、vm1、vm2 严格一致，工作区干净）
- **物理总线拓扑**:
  - Server (vm0): `wlxfc221c300cbc`，USB 2.0 (480 Mbit/s)，NODE_ID 255
  - Client1 (vm1): `wlxfc221c300cbb`，USB 3.0 (5000 Mbit/s)，NODE_ID 1
  - Client2 (vm2): `wlxfc221c500a88`，USB 2.0 (480 Mbit/s)，NODE_ID 2
- **射频配置**: Channel 157 (5 GHz), HT40+, Short GI enabled, MCS 3, FEC 8/14, Link ID 406
- **单次 I/O 停滞超时**: 120 秒（严格锁定，未放宽）
- **Runtime 整体时限**: 400 秒

#### 2. 正式验收执行实测数据

- **正式运行归档 Run ID**: `v8_issue41_20260922_095221`
- **归档路径**: `tests/logs/v8_issue41_20260922_095221/`
- **归档校验**: `python3 tests/real_hardware/issue41_validate_archive.py` 严格全量校验 100% 通过（`OK: issue41 archive validation passed`）。
- **全部五个核心分区状态**:
  1. `orchestration`: **passed**（三机拓扑扫描、USB 速率、依赖、确定性 40 MiB fixtures 与 SHA 严格匹配）
  2. `pre_runtime_smoke`: **passed**（同组常驻进程连续 3 周期 40 MiB 双向数据面 Gate 全通）
     - Cycle 1: 86.95s, Client1 lost=1.06%, fec_recovered=22.53%; Client2 lost=1.25%, fec_recovered=24.80%
     - Cycle 2: 84.23s, Client1 lost=2.24%, fec_recovered=23.72%; Client2 lost=2.42%, fec_recovered=25.09%
     - Cycle 3: 83.41s, Client1 lost=1.43%, fec_recovered=23.66%; Client2 lost=1.48%, fec_recovered=26.47%
  3. `formal_runtime_loop`: **passed**（两轮双 40 MiB FL Runtime 闭环，167 秒全部成功，远低于 400 秒时限）
     - **Round 1** (ID: `a2c8d45b-0a27-4129-a61f-6c83e05ceda0`):
       - 下发模型: 41943040 字节 (40 MiB), SHA-256: `a544c81f86c7a9e089dc45b3b0d3ff6490b933bb76153d8a8478c1a7703c7841`
       - Client1 PUT 上传: 41943040 字节, SHA-256: `f2426ff15679379f0a0240b6ee7c9dd1d4672450a062b8d28dca02cc069d456c`, 返回 HTTP 201 Created
       - Client2 PUT 上传: 41943040 字节, SHA-256: `3263d71dd1df30524a916fce845803cd2c95c37fab0d5687f831a2ce8943824d`, 返回 HTTP 201 Created
       - 严格同步收齐: Server 收齐 `[1, 2]`，未提前返回 partial result；Client1 先提交时 Server 正确等待 Client2
       - 遥测: Client1 丢包率 1.93%, FEC 恢复率 25.17%; Client2 丢包率 1.37%, FEC 恢复率 23.68%
     - **Round 2** (ID: `a02a5f9d-f66f-4a57-8fc4-3223b6bb325e`，独立 Round ID):
       - 下发模型: 41943040 字节 (40 MiB), SHA-256: `a544c81f86c7a9e089dc45b3b0d3ff6490b933bb76153d8a8478c1a7703c7841`（占位聚合原样复制模型 SHA）
       - Client1 PUT 上传: 41943040 字节, SHA-256: `f2426ff15679379f0a0240b6ee7c9dd1d4672450a062b8d28dca02cc069d456c` (与模板一致), 返回 HTTP 201 Created
       - Client2 PUT 上传: 41943040 字节, SHA-256: `3263d71dd1df30524a916fce845803cd2c95c37fab0d5687f831a2ce8943824d` (与模板一致), 返回 HTTP 201 Created
       - 严格同步收齐: Server 收齐 `[1, 2]`，未提前返回 partial result
       - 遥测: Client1 丢包率 1.87%, FEC 恢复率 26.13%; Client2 丢包率 1.41%, FEC 恢复率 23.80%
     - 传输过程无 `upload_in_progress` 冲突，无 HTTP 阶段超时。
  4. `lifecycle`: **passed**（三角色服务生命周期审计完全合规）
     - `first_stop`: 三端 service unit 均为 inactive，cgroup 清空，TUN 设备彻底销毁，无残留孤儿进程
     - `restart`: 三端重新拉起达到 active，验证获得全新 MainPID，与旧 PID 集合完全不重叠（no-overlap），TUN 恢复 UP
     - `second_stop`: 再次受控停止，三端 service unit inactive，cgroup 清空，TUN 销毁，无孤儿进程
  5. `conclusion`: **passed** (`reason: "2 轮 40 MiB 严格同步场景证据完整"`, `category: null`)

#### 3. 关联 GitHub 工单验收判定

- **GitHub Issue #60**: 物理链路分源遥测拆分、高丢包根因定位及射频调优验证完成，残留丢包降至 1%~2%，单次 I/O 120s 及整体 400s 时限严格满足。
- **GitHub Issue #53**: 场景驱动验收套件全面升级支持配置化 40 MiB 两轮连续并发，严格同步聚合屏障与 SHA 完整性验证全部达成。
- **GitHub Issue #54**: SSH 编排下真实三机硬件 40 MiB 两轮并发正式闭环验收全项通过，不可覆盖可审计归档全 passed。
- **总 Epic GitHub Issue #50 (Stage 1 阶段)**: 底层物理链路通信与双 40 MiB update 两轮并发闭环目标全部圆满达成。

