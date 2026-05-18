# Issue #6: PRD: 基于拆分架构实现上行 Token Passing 底层通信

- 原始链接: https://github.com/Softmoonlit/wfb-ng/issues/6
- 状态: open
- 标签: enhancement, ready-for-human
- 创建时间: 2026-05-07T13:53:51Z
- 更新时间: 2026-05-07T14:35:34Z

---

## Problem Statement

当前 `wfb_core` 单进程合并架构在真实 server/client 上执行后无法 ping 通，而既有 `wfb_tx`、`wfb_rx`、`wfb_tun` 拆分进程架构已经验证可以跑通并 ping 通。团队需要在不破坏已验证链路的前提下，以最小改动实现联邦学习无线空口的底层上行控制能力：客户端没有 Token 时绝对不发射，只有获得服务器授权窗口时才允许上行，从而为多节点无碰撞传输打基础。

规范文档中的“单进程 + 用户态浅水池 + 动态水位线”方案理论完整，但第一阶段实现复杂度高，且会偏离当前可用基线。因此本 PRD 聚焦第一阶段：保留 `wfb_tx / wfb_rx / wfb_tun` 拆分架构，在现有可 ping 通路径上实现上行 Token Passing 的最小闭环。

## Solution

保留拆分进程架构作为第一阶段主线，不再以 `wfb_core` 单进程作为当前验收目标。实现一个独立 C/C++ 常驻 Token 调度器，按命令行静态节点列表固定轮询发放上行 Token；Client 侧接收进程识别 Token 后，通过 Unix 域 datagram socket 事件驱动通知本机发送进程；发送进程在每次空口注入前检查本机 Token 授权状态，未授权或授权过期时不发射。

第一阶段只控制 Client → Server 上行，不实现 Server → Client 下行呼吸调度、不实现 AEW 双队列调度、不实现裸 Radiotap Token、不实现安全环形队列。安全环形队列作为后续阶段候选，若实现必须以对应用层产生反压为目标，在 TUN read 路径加入队列和水位线控制，或重新评估单进程化。

## User Stories

1. As a 底层通信开发者, I want to keep the already pingable `wfb_tx / wfb_rx / wfb_tun` architecture, so that I can add Token Passing without losing the known-good baseline.
2. As a 底层通信开发者, I want `wfb_core` to be treated as experimental, so that current implementation work does not chase a path that cannot ping today.
3. As a 系统集成者, I want a standalone C/C++ Token scheduler, so that server-side scheduling can be started, stopped, and debugged independently.
4. As a 系统集成者, I want the Token scheduler to accept a static node list from command line arguments, so that fixed hardware test topologies can be reproduced easily.
5. As a 系统集成者, I want to configure fixed `duration_ms` per Token, so that first-stage timing behavior is deterministic.
6. As a 系统集成者, I want to configure `GUARD_INTERVAL` between nodes, so that node switching leaves room for radio queue drain.
7. As a Client node, I want to remain silent when no valid Token is present, so that I do not collide with other nodes on the half-duplex wireless channel.
8. As a Client node, I want to transmit only inside my authorized Token window, so that uplink use of the air is centrally controlled.
9. As a Client node, I want Tokens targeted at other nodes to disable my local transmit permission, so that stale local state does not cause accidental transmission.
10. As a Client node, I want Token expiration to be checked before each air injection, so that transmission stops promptly when the authorization window ends.
11. As a Client receiver process, I want to identify Token control packets, so that the local sender can be authorized with minimal latency.
12. As a Client receiver process, I want to ignore stale or duplicate Token events, so that old control information does not reopen transmission unexpectedly.
13. As a Client sender process, I want to receive Token authorization through Unix 域 datagram socket IPC, so that there is no `usleep()` blind polling loop between receiver and sender.
14. As a Client sender process, I want the Token IPC path to preserve message boundaries, so that authorization events are parsed reliably.
15. As a Client sender process, I want the Token IPC path to avoid network ports, so that it does not interfere with existing local UDP data paths.
16. As a Client sender process, I want to maintain local `token_expire_time_ms`, so that transmission decisions are local and cheap.
17. As a Client sender process, I want to avoid reading or transmitting data when no Token is valid, so that unauthorized air injection never occurs.
18. As a Server operator, I want the first-stage scheduler to use fixed round-robin Token issuance, so that behavior is easy to reason about before AEW is introduced.
19. As a Server operator, I want logs showing which node received each Token, so that real hardware tests can be correlated with observed traffic.
20. As a test engineer, I want the baseline split architecture to ping without Token changes enabled, so that regressions are detected early.
21. As a test engineer, I want a single-client Token test using ping or small UDP/KCP packets, so that Token authorization is validated before multi-node tests.
22. As a test engineer, I want a two-client test where only the Token holder can transmit, so that collision-prevention behavior is observable.
23. As a test engineer, I want KCP integration to come after Token gating is proven, so that failures can be attributed clearly.
24. As a 联邦学习系统集成者, I want KCP small-file upload to be tested before 40MB upload, so that protocol and scheduling issues are separated.
25. As a 联邦学习系统集成者, I want 40MB transfer to remain out of first-stage hard acceptance, so that the first milestone is not blocked by buffering and throughput optimization.
26. As a maintainer, I want `wfb_tun` core read/write/batching behavior to remain unchanged in first stage, so that the pingable TUN/IP path is preserved.
27. As a maintainer, I want TUN `txqueuelen=100` to remain the preferred external queue setting, so that later backpressure work has the correct kernel-side shape.
28. As a maintainer, I want the safety ring buffer to be explicitly deferred, so that first-stage implementation does not accidentally become a buffering redesign.
29. As a maintainer, I want future safety ring buffer work to target application backpressure, so that it solves the real large-flow problem rather than moving packet drops to another process.
30. As a future maintainer, I want ADR-0002 to explain why split architecture is the first-stage mainline, so that the single-process spec is not reintroduced accidentally.
31. As a future maintainer, I want downlink breath scheduling out of scope for first stage, so that uplink Token Passing can stabilize first.
32. As a future maintainer, I want AEW active/sleep queues out of scope for first stage, so that dynamic scheduling does not obscure basic Token correctness.
33. As a future maintainer, I want raw Radiotap Token frames out of scope for first stage, so that control-plane reliability improvements can be added after the existing channel works.
34. As a developer, I want the implementation to expose simple counters for Token received, Token accepted, Token ignored, authorized sends, and denied sends, so that field debugging is possible.
35. As a developer, I want the implementation to fail safely when Token IPC is unavailable, so that a Client remains silent instead of transmitting unsafely.
36. As a developer, I want clean shutdown behavior for the scheduler and IPC socket, so that repeated hardware tests do not leave stale control endpoints.
37. As a developer, I want command-line parameters to be explicit and reproducible, so that test scripts can launch the same topology consistently.
38. As a developer, I want no Python runtime in the Token scheduler, so that timing behavior avoids GC/runtime jitter.
39. As an operator, I want the first-stage system to work with the existing process supervision model, so that deployment does not require a full architecture rewrite.
40. As an operator, I want each process boundary to remain debuggable with logs and simple tools, so that hardware test failures can be isolated quickly.

## Implementation Decisions

- The first-stage mainline is the existing split process architecture: `wfb_tx`, `wfb_rx`, and `wfb_tun` remain separate.
- `wfb_core` single-process architecture is experimental and is not the first-stage acceptance target.
- Token Passing first stage controls only Client → Server uplink transmission.
- Server → Client downlink remains on the existing working path; downlink breath scheduling is deferred.
- The first-stage scheduler is an independent C/C++ resident process.
- The scheduler sends fixed round-robin Tokens based on a static node list supplied by command-line arguments.
- The scheduler supports configurable Token duration and guard interval.
- The scheduler reuses the existing command/control sending style rather than introducing raw Radiotap Token frames.
- Client-side receive logic must recognize Token control packets in the first stage.
- Client-side receive logic sends authorization events to the local sender through Unix 域 datagram socket IPC.
- Client-side sender logic owns the local authorization state and Token expiration timestamp.
- Client-side sender logic checks authorization before every air injection.
- When no Token is valid, the Client sender must fail closed and remain silent.
- `wfb_tun` core read/write, batching, and local UDP forwarding behavior are not changed in the first stage.
- TUN `txqueuelen=100` remains the intended small kernel-side queue setting.
- KCP remains the target uplink application protocol, but implementation order is Token gating first, small traffic second, KCP small-file third, 40MB later.
- Safety ring buffer work is explicitly deferred from first stage.
- If a safety ring buffer is implemented later, it must target application backpressure by controlling the TUN read path, or the team should re-evaluate single-process architecture.
- AEW active/sleep queue scheduling, dynamic window growth, watchdog false-negative protection, and actual-used-time feedback are deferred.
- Raw Radiotap Token frames, lowest-MCS Token transmission, and Token triple-send are deferred.
- No Python runtime is used for the first-stage Token scheduler.
- The design follows ADR-0002: preserve the pingable split architecture and accept local UDP buffering limitations as a first-stage risk.

## Testing Decisions

- Good tests should validate externally visible communication behavior: ping success, small UDP/KCP packet delivery, Token-gated transmission, and no unauthorized air injection.
- Tests should not depend on internal timing implementation details beyond configured Token duration and guard interval.
- Baseline regression test: existing split architecture without Token gating must still ping.
- Single-client Token test: after Token scheduler grants the Client, ping or small UDP traffic must pass within the authorization window.
- Token-denial test: without a valid Token, Client uplink traffic must not be injected into the air.
- Two-client test: with two Clients running, only the currently authorized Client should transmit during its Token window.
- Token expiration test: after Token expiry, sender must stop injecting packets before a new valid Token arrives.
- Token targeting test: Client must ignore Tokens for other node IDs.
- IPC test: receiver-to-sender Unix domain datagram authorization events must update sender authorization state without polling sleeps.
- Scheduler test: fixed node list, duration, and guard interval should produce repeatable Token order.
- KCP test sequence: only after ping/small UDP passes should KCP small-file upload be tested.
- 40MB transfer and 10-node stress are not first-stage hard acceptance tests; they belong to later buffering and scheduling validation.
- Existing local tests for KCP and core communication can serve as prior art for small-packet and KCP validation, but first-stage acceptance must also include real split-process behavior.

## Out of Scope

- Replacing the split process architecture with `wfb_core` single-process architecture.
- Implementing the safety ring buffer in the first stage.
- Implementing dynamic watermarks in the first stage.
- Implementing downlink breath scheduling for UFTP NACK windows.
- Implementing AEW active/sleep queues, interleaved polling, additive window growth, or demand-fitting logic.
- Implementing raw Radiotap Token frames, lowest-MCS control frame templates, or Token triple-send.
- Modifying UFTP source code.
- Reworking `wfb_tun` core TUN read/write/batching logic in the first stage.
- Making GitHub Issues, Python FL control, or dynamic business state part of real-time Token scheduling.
- Treating 40MB/10-node transfer as the first-stage hard gate.

## Further Notes

This PRD intentionally deviates from the full architecture specification in order to preserve the currently working split-process baseline. The full spec remains useful for later phases, especially safety ring buffer, application backpressure, downlink breath scheduling, and AEW scheduling. The key first-stage principle is fail-closed uplink control: a Client without a valid Token must be silent.

Relevant accepted decision: ADR-0002 records why split process architecture is the first-stage mainline despite the single-process design in the deeper architecture specification.
