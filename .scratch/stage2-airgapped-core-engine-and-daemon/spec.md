Status: ready-for-agent

# Stage 2: 无网络环境下的无 SSH 核心协同引擎与 Client 常驻服务

## 阶段目标

在 Stage 1 达成稳固的高吞吐数据面后，构建脱离 SSH 依赖的中心协同引擎（Server 侧 Headless Core Engine）与客户端常驻服务（Client 侧 Daemon Agent），利用经 WFB FEC 纠错保护的 TUN 虚拟网络承载应用层仿真控制信令（任务配置下发、状态监控、超参同步与心跳保活）。

## 核心架构原则

1. **信令与数据分层**：
   - 链路层 `WFB_PACKET_CONTROL` 保持极简，严格仅用于微秒/毫秒级空口时隙仲裁（GRANT / READY）；
   - 仿真控制信令、配置下发与节点状态监控作为**应用层普通数据**，经由 TUN 接口传输，享受 FEC 纠错与可靠投递保障。
2. **失联防锁死保护（Anti-Lockup Watchdog）**：
   - 当需要切换射频参数（信道、带宽）时，采用两阶段确认机制；
   - Client 在切换到新信道后若超时未收到 Server 的带内心跳，自动回退到默认备用信道（Default Channel 157）。
3. **Client 端无人值守常驻**：
   - Client 开机自启 systemd 守护服务 `wfb-fl-client-daemon`，自动完成网卡就绪初始化，通过带内信令听从 Server 指挥，无需现场人员操作终端。

## 待细化内容（本阶段启动前需经 Grill 细化）
- 具体的应用层通信协议（如 JSON-RPC over TCP / UDP 组播）；
- Client 常驻状态机的详细跃迁规则与失败自愈机制；
- 射频切换与回退守护的详细时序。
