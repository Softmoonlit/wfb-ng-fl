# 04: 通过 Web 启动同步文件仿真作业

**What to build:** 让操作者可以选择模型摘要、固定目标节点和同步轮次数量启动文件仿真作业，并通过已有 Server/Client Daemon、无线控制面和无线数据面完成多轮文件闭环。该作业跳过真实训练和聚合，只验证平台编排、文件交付和完整性。

**Blocked by:** 01（建立 Web 管理入口与可恢复状态快照）、02（通过 Web 管理内容寻址模型库）

**Status:** resolved


## Answer

已实现 Web 同步文件仿真作业入口。`POST /api/v1/jobs` 只接受模型库中存在的完整小写 SHA-256、固定目标节点集合、正整数 `rounds` 和 `Idempotency-Key`，拒绝旧路径、算法/训练/聚合字段、异步模式和重复幂等键复用。请求通过现有 Server Daemon、TASK_READY 屏障、Runtime、UFTP 和 HTTP PUT 数据面进入作业生命周期。

文件仿真客户端直接回传收到的模型文件，Server 端逐轮验证 update 与本轮模型的大小和 SHA-256，并通过身份聚合保持跨轮摘要连续。控制台快照展示当前轮次、完成轮数、总轮数和 Server 阶段；作业终态继续独立报告执行结果与资源恢复状态。模型缺失、资源冲突和非法请求映射为稳定 HTTP 错误对象。

验证：`pytest -q`，524 passed；另有新增 Web schema/idempotency、文件仿真算法和模型一致性测试。

## Comments

- 2026-10-09：完成实现、全量测试和双轴代码审查；未执行真实硬件闭环，沿用本工单规定的无硬件验证边界。
