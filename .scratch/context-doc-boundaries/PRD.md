# 文档边界迁移 PRD

## 背景

当前项目已经把 `CONTEXT.md` 收敛为纯术语表。为了避免长期文档再次混入执行状态，需要把原先放在 `CONTEXT.md` 里的状态型内容统一迁移到本规划文档中。

## 目标

- 明确哪些内容属于短期执行状态
- 将这些状态信息从 `docs/` 和 `CONTEXT.md` 中隔离出去
- 为后续协作提供一个可变更、可追踪的状态记录位置

## 范围

本 PRD 只承载状态型内容，不承载架构、接口或 ADR 级别的稳定知识。

## 已验证

- MAC 控制帧结构及序列化
- Token Passing 调度器
- 拆分进程 Token gate 真实硬件测试路径
- 下行 UFTP 传输（40MB 测试通过）
- KCP 小文件测试工具和真实硬件验证脚本

## 进行中

- FL Runtime / SDK 第一版正常同步流程设计与实现
- Transport Backend 接口及 UFTP 下行、TCP 上行适配
- 集成式 wfb 链路层守护进程设计与实现
- TUN 读写、空口 TX/RX、Token gate 合并到统一队列/水位控制下
- 动态逻辑水位、高低水位和反压实现
- 下行 UFTP 呼吸/反向控制窗口调度
- 上行 TCP per-client 文件上传 baseline
- TCP 上行后的 server ACK 反向控制窗口验证
- 真实硬件 10 节点或等效多节点轮询吞吐测试
- 若 TCP baseline 不满足吞吐/稳定性，再评估 KCP per-client 吞吐 profile

## 实验项

- KCP per-client 上行吞吐 profile
- UDT/QUIC 上行对照 benchmark
- 旧拆分进程路径作为回归和风险隔离参考
- Flower-like API 或 Flower adapter
- 迟到 update、异步 FL、stale-aware aggregation 和动态 client selection

## 暂不纳入范围

- 多个 client 共享一个可靠传输会话
- WebSocket 作为大文件数据面主协议
- 裸 UDP 自研可靠文件传输作为第一版主线
- 让 UFTP/TCP/KCP 直接感知 Token 或调用 Token API
- 让 FL 聚合或训练程序直接调用 UFTP、TCP/KCP、wfb 或 Token API
- 第一版处理 server 下发与 client 上传声明相撞、迟到 update、异步聚合等极端流程
- 依赖旧拆分进程本地 UDP socket 作为最终大文件憋包机制
- 移除 TUN/IP 边界改成业务协议直接调用 wfb 内部接口
- 实现新的物理层调制解调方案

## 维护规则

- 状态型内容优先写入本 PRD，不写回 `CONTEXT.md`
- 设计细节保留在 `docs/联邦学习无线空口传输总设计.md`
- 难逆转取舍保留在 `docs/adr/0003-最终联邦学习传输架构.md`
- 当某项从“进行中”变为稳定约定时，再考虑迁入设计文档或 ADR
