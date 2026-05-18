# 第一阶段 Issue 执行顺序索引

## 目标

本索引用于在本地仓库中执行和跟踪第一阶段 `Token Passing + split-process` 主线工作。对应的需求总入口是：

- [ISSUE-6-基于拆分架构实现上行-Token-Passing-底层通信](./ISSUE-6-基于拆分架构实现上行-Token-Passing-底层通信.md)

第一阶段核心原则：
- 保持 `wfb_tx / wfb_rx / wfb_tun` 拆分进程架构为主线
- `wfb_core` 仅作为实验路径，不作为验收目标
- 先打通 Token gating 最小闭环，再接 KCP
- 40MB 传输和大规模压力测试不阻塞第一阶段完成

## 推荐执行顺序

### 阶段 0：边界固化
1. [ISSUE-7-确立拆分架构第一阶段边界](./ISSUE-7-确立拆分架构第一阶段边界.md)
   - 目的：统一文档、验收描述和实现边界
   - 当前状态：已完成；拆分进程架构主线、`wfb_core` 实验路径与第一阶段延期项已固定

### 阶段 1：调度器与协议基础
2. [ISSUE-8-实现独立-C-Cpp-Token-调度器最小闭环](./ISSUE-8-实现独立-C-Cpp-Token-调度器最小闭环.md)
   - 目的：先建立独立 scheduler 进程与固定 round-robin 发放能力
   - 产出：独立 C/C++ scheduler、静态 node list、`duration_ms`、`guard_interval`、日志、干净退出
   - 当前状态：已完成；已交付 `wfb_token_scheduler`、Catch2 单测与本地冒烟验证

3. [ISSUE-9-定义-Token-控制包格式与解析测试](./ISSUE-9-定义-Token-控制包格式与解析测试.md)
   - 目的：明确 Token 控制包字段与解析规则
   - 产出：Token 格式定义、合法/非法解析测试

### 阶段 2：接收路径过滤与 IPC
4. [ISSUE-10-实现-Token-targeting-与-stale-duplicate-过滤](./ISSUE-10-实现-Token-targeting-与-stale-duplicate-过滤.md)
   - 目的：只接受发给本节点的新 Token
   - 产出：node_id targeting、stale/duplicate/expired 忽略逻辑

5. [ISSUE-11-实现-receiver-到-sender-的-Unix-datagram-IPC-事件发送](./ISSUE-11-实现-receiver-到-sender-的-Unix-datagram-IPC-事件发送.md)
   - 目的：将 receiver 识别出的有效 Token 通过本机 Unix datagram 发送给 sender
   - 产出：消息结构、fail-closed IPC、干净清理
   - 当前状态：已完成；已接入 `wfb_rx` 本地 `-U unix_socket` 路径，并补齐 IPC / receiver 集成测试

### 阶段 3：发送侧状态机与门控
6. [ISSUE-12-实现-sender-侧-Token-授权状态机](./ISSUE-12-实现-sender-侧-Token-授权状态机.md)
   - 目的：在 sender 维护本地授权状态与过期时间
   - 产出：允许/拒绝/过期/替换状态机
   - 当前状态：已完成；已接入 sender 侧 Unix datagram 授权接收、状态机与发送前门控辅助逻辑

7. [ISSUE-13-在-sender-空口注入前接入-Token-门控](./ISSUE-13-在-sender-空口注入前接入-Token-门控.md)
   - 目的：在实际发射前接入 Token gate
   - 产出：无 Token 不发射、过期即停发、且不改 `wfb_tun` 核心路径

### 阶段 4：可观测性与本地自动化
8. [ISSUE-14-补齐-Token-counters-与日志可观测性](./ISSUE-14-补齐-Token-counters-与日志可观测性.md)
   - 目的：补齐 receiver / sender / scheduler 的关键 counters 与日志
   - 产出：received / accepted / ignored / authorized sends / denied sends

9. [ISSUE-15-补齐本地-Token-Passing-自动化回归测试](./ISSUE-15-补齐本地-Token-Passing-自动化回归测试.md)
   - 目的：建立一键可跑的本地自动化验证
   - 产出：baseline、scheduler 顺序、IPC、expiry、targeting、单/双客户端场景测试

### 阶段 5：真实硬件与 KCP 验收
10. [ISSUE-16-在真实硬件上验证-Token-gated-split-process-上行](./ISSUE-16-在真实硬件上验证-Token-gated-split-process-上行.md)
    - 目的：在真实 server/client 硬件上验证第一阶段主链路
    - 产出：无 Token 静默、单客户端小流量通过、双客户端只有 Token holder 可发、日志留档

11. [ISSUE-17-在-Token-门控通过后验证-KCP-小文件上传](./ISSUE-17-在-Token-门控通过后验证-KCP-小文件上传.md)
    - 目的：在 Token correctness 已证明后，再验证 KCP 小文件上传
    - 产出：KCP 小文件完整性验证、问题分层定位

## 依赖关系摘要

```text
Issue 6 (PRD)
└── Issue 7 边界固化
    └── Issue 8 独立 scheduler
        └── Issue 9 Token 格式与解析
            └── Issue 10 targeting / stale / duplicate 过滤
                └── Issue 11 receiver -> sender IPC
                    └── Issue 12 sender 授权状态机
                        └── Issue 13 注入前 Token 门控
                            └── Issue 14 counters 与日志
                                └── Issue 15 本地自动化回归
                                    └── Issue 16 真实硬件验收
                                        └── Issue 17 KCP 小文件上传验收
```

## 实施建议

### 先做什么
如果现在开始真正写代码，建议从以下 2 个 issue 作为下一批实现目标：

1. `Issue #9` — Token 控制包格式与解析测试
2. `Issue #10` — Token targeting 与 stale/duplicate 过滤

原因：
- issue 7、8 已完成，当前应继续定义第一阶段控制面的最小协议与接收过滤行为
- 仍不需要先改动 `wfb_tun` 核心逻辑
- 是后续 IPC、sender 状态机和门控的前置基础

### 暂时不要做什么
在 `Issue #13` 前，暂时不要把精力放在：
- KCP 集成
- 40MB 传输
- 多节点压力测试
- `wfb_tun` 核心重写
- 单进程 `wfb_core` 路线

## 完成定义

当以下条件都满足时，可以认为第一阶段底层闭环基本成立：

- 独立 scheduler 能稳定 round-robin 发放 Token
- receiver 能识别并过滤无效 Token
- receiver 能通过 Unix datagram 向 sender 发授权事件
- sender 能维护本地授权状态并在发射前严格门控
- 本地自动化测试通过
- 真实硬件上单/双客户端 Token gating 验证通过
- 之后再进行 KCP 小文件上传验证
