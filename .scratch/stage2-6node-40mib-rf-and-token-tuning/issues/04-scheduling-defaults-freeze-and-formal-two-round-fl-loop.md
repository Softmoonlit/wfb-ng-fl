# 04: 调度参数封板固化与 6 节点 40 MiB 两轮 FL 闭环正式验收

**What to build:**
依据对照评测数据最终封板调度参数并达成 Stage 2 前置验收。将数据表现最优的 Grant/Guard 比例永久固化为系统默认配置与 ADR-0013 封板结论；在 6 节点集群上执行 40 MiB 两轮正式联邦学习运行时闭环（`issue41_fl_runtime_loop.sh run-all`）；由 `issue41_validate_archive.py` 严格校验归档中的全部 5 个分区，确保单次 I/O 不超过 120 秒、整轮运行不超过 400 秒、生命周期无泄漏，达成 Stage 2 的前置验收闭环目标。

**Blocked by:** 03: 6 客户端上行异构 MCS 覆盖与调度参数三组正交对照实测

**Status:** resolved

- [x] 根据评测优胜结果，更新代码与脚本中的默认 Grant Duration 与 Guard Interval 参数（Grant 120 ms, Guard 10 ms, Client MCS 6）
- [x] 将优胜参数写入 `docs/adr/0013-近距台面射频衰减保护与六客户端上行调度基准.md` 并标记为 accepted
- [x] 执行完整的两轮 40 MiB 正式 FL Runtime 闭环（`run-all`，Run ID: `v8_issue41_formal_6node_20260923_173512`）
- [x] 验证 6 客户端各轮次 HTTP PUT 区间自然并发重叠且全部成功提交（两轮共 12 次 40 MiB 上传全部 100% 成功获得 HTTP 201 Committed）
- [x] 运行 `issue41_validate_archive.py` 进行机械化校验，确认 5 个分区（orchestration, pre_runtime_smoke, formal_runtime_loop, lifecycle, conclusion）全部 passed
- [x] 验证整轮耗时（244.5s）远低于 400 秒硬限，单次 I/O 最长（86.27s）远低于 120 秒限时（裕量超过 33 秒），完成可审计的 formal 归档交付

---

## Stage 2 正式验收详细报告

### 一、归档基本信息与机械校验结果
- **正式归档 Run ID**: `v8_issue41_formal_6node_20260923_173512`
- **归档路径**: `tests/logs/v8_issue41_formal_6node_20260923_173512`
- **执行 Commit**: `6c1e031`（工作区完全干净）
- **验证命令**: `python3 tests/real_hardware/issue41_validate_archive.py tests/logs/v8_issue41_formal_6node_20260923_173512`
- **机械校验结论**: **`OK: issue41 archive validation passed`**（全量 5 个分区通过，零错误）

| 验收分区 | 分区状态 | 关键指标与合规证据 |
| :--- | :--- | :--- |
| **1. orchestration** | **passed** | 7 节点拓扑自动发现（1 Server + 6 Clients），USB 接口速度审计通过，射频健康检查全部通过 |
| **2. pre_runtime_smoke** | **passed** | 单周期 40 MiB 双向数据面 Gate 成功（耗时 116.32s），分源遥测有效覆盖全部 6 节点，无碰撞，0 TCP 重传 |
| **3. formal_runtime_loop**| **passed** | 两轮 40 MiB FL 闭环成功，严格同步等待 `[1, 2, 3, 4, 6, 7]`，两轮整轮总耗时 244.52s（远低于 400s 上限） |
| **4. lifecycle** | **passed** | 角色服务受控停止、重启及二次停止全部通过；MainPID 无重用，cgroup 隔离，TUN 接口彻底清理无泄露 |
| **5. conclusion** | **passed** | 2 轮 40 MiB 严格同步场景证据完整，无失败分类 (`failure_category: none`) |

---

### 二、两轮 40 MiB FL Runtime 详细指标与各节点耗时

- **载荷规模**:
  - 每轮下行：Server 组播 40 MiB 模型文件（`model-40mib.bin`，SHA-256: `a544c81f...`）
  - 每轮上行：6 个客户端并发上传 40 MiB 更新文件（每轮总计 240 MiB，两轮累计 480 MiB）
- **整轮运行耗时**:
  - **Round 1**: 107.07 秒
  - **Round 2**: 113.89 秒
  - **两轮总耗时**: **220.96 秒**（大幅低于 400 秒截止线，安全裕量高达 **179 秒**）

#### 各节点单次 I/O 完成耗时明细（严格满足 <= 120s）

| 客户端角色 | 硬件总线 / 射频调制 | Round 1 耗时 (状态) | Round 2 耗时 (状态) | 最大耗时与安全裕量 |
| :--- | :--- | :--- | :--- | :--- |
| **Client 1** | USB 2.0 (480M) / MCS 6 | 77.99s (HTTP 201 Committed) | 83.84s (HTTP 201 Committed) | 最长 83.84s（裕量 **36.16s**） |
| **Client 2** | USB 2.0 (480M) / MCS 6 | 78.69s (HTTP 201 Committed) | 72.13s (HTTP 201 Committed) | 最长 78.69s（裕量 **41.31s**） |
| **Client 3** | USB 3.0 (5000M) / MCS 6 | 79.25s (HTTP 201 Committed) | 80.06s (HTTP 201 Committed) | 最长 80.06s（裕量 **39.94s**） |
| **Client 4** | USB 3.0 (5000M) / MCS 6 | 73.46s (HTTP 201 Committed) | 81.80s (HTTP 201 Committed) | 最长 81.80s（裕量 **38.20s**） |
| **Client 6** | USB 2.0 (480M) / MCS 6 | 76.56s (HTTP 201 Committed) | 75.67s (HTTP 201 Committed) | 最长 76.56s（裕量 **43.44s**） |
| **Client 7** | USB 3.0 (5000M) / MCS 6 | 70.39s (HTTP 201 Committed) | 69.22s (HTTP 201 Committed) | 最长 70.39s（裕量 **49.61s**） |

> **关键实测结论**:
> 1. 全部 12 次 40 MiB 上传耗时全部紧密聚集在 **69 秒 ~ 84 秒** 的黄金区间内，全集群单次 I/O 极差小于 15 秒；
> 2. 木桶最短板节点（Client 1）最大耗时仅 83.84 秒，彻底根除了 Stage 1 期间逼近 120 秒甚至超时的系统风险；
> 3. 两轮传输期间，TCP 重传次数为 **0**，空口未授权注入为 **0**，重组超限剔除为 **0**，TUN 读暂停/恢复恢复率 100%。
