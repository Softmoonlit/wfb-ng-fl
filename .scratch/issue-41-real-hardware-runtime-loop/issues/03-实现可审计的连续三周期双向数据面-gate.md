# 03: 实现可审计的连续三周期双向数据面 gate

**What to build:** 在已通过 preflight 的三机运行包络内建立正式 Runtime 之前的协议级双向数据面硬门槛。同一组链路、UFTP receiver 和 HTTP receiver 进程连续完成三个周期；每周期包含一次向两个 client 的 shared UFTP 4 MiB 下行，以及两个 client 各一次 4 MiB HTTP PUT 上行。每个周期独立校验直接传输和链路遥测事实，任一周期失败都禁止 Runtime 启动。

**Blocked by:** 01 建立可审计的三机运行包络

**Status:** resolved

- [x] 三个周期复用同一组链路、UFTP receiver 和 HTTP receiver 进程，周期之间不重置无线设备或修改链路参数。
- [x] 每周期使用一次 shared UFTP operation 向两个 client 交付确定性 4 MiB 文件和 manifest。
- [x] 每周期验证两个 client 的连接成功事实、逐文件完成矩阵、文件大小和 SHA-256。
- [x] 每周期由两个 client 分别完成一次 4 MiB HTTP PUT，并验证客户端 HTTP 201、server committed、大小和 SHA-256 一致。
- [x] 每周期结束时活动上传集合收敛为空，且没有遗留本周期临时状态。
- [x] 每周期归档 READY/GRANT、authorized sends、server 接收、queue/backpressure、reassembly、sender isolation、feedback、丢包、FEC 恢复、TCP 重传、队列峰值和阶段耗时；自然发生 queue pause 时必须存在对应 resume 且最终恢复。
- [x] 单次 I/O 连续 120 秒无进展时 fail-closed，每个 smoke 周期另有不可在运行中放大的固定整体 deadline。
- [x] 任一周期失败时正式 Runtime 启动事实为 false，并通过运行包络停止、收集和生成包含最后成功层、第一失败层及失败分类的 failed gate 归档。
- [x] Issue #41 正式数据面路径不启用或保留立即 feedback window 候选行为。
- [x] gate 的原始证据、分区摘要和验证规则随本行为交付，并覆盖完整成功、各周期失败、阶段 deadline 和缺失遥测测试。
