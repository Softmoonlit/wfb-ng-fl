# 05: 服务端常驻协同守护引擎、预置链路层槽位池、本地专用 REST IPC 与作业前置门禁

**What to build:** 交付 `wfb-fl-server-daemon` 可执行程序与 systemd 服务单元。独占纳管服务端网卡，拉起底层 `wfb_v6_uplink` 时一次性全量预置平台 1~10 号节点白名单槽位（`--known-clients 1..10` 及其静态 `tun_ip` 映射），实现新节点即插即用无需重启底座；维护内存级全网拓扑与细粒度状态注册表（`NodeHorizonRegistry`）；暴露专用本地回环 HTTP REST API（`http://127.0.0.1:9090`，支持 `/status`、`/survey`、`/jobs/start`、`/jobs/abort`、`/logs/stream`）；在 `/jobs/start` 构筑 4 项确定性前置门禁（目标节点 10s 内确认 IDLE、模型存在且大小合法、下行速率安全区间、无冲突作业）。

**Blocked by:** 01: 5GHz 扫频探路模块、网卡预设引擎与速率区间校验器, 03: 对称 UDP 控制信令平面、三级寻频自愈与两军问题防护状态机, 04: 两阶段射频重配引擎与防脑裂租约看门狗自愈机制

**Status:** ready-for-agent

- [ ] 交付 `wfb-fl-server-daemon` 可执行程序与 systemd 服务单元。
- [ ] 开机独占纳管服务端物理无线网卡，拉起底层 `wfb_v6_uplink` 时一次性全量预置平台支持的 1~10 号节点白名单槽位（`--known-clients 1..10` 及其静态 `tun_ip` 映射），确保后续任何新节点接入时均可立即被 C++ Token 调度器授予 Grant 并建立单播下行路由，无需重启底座。
- [ ] 监听 UDP 9001 端口，维护内存级全网拓扑与细粒度状态注册表（`NodeHorizonRegistry`），毫秒级记录节点 ID、IP、当前状态机阶段、已耗时、信道与最后心跳时间戳。
- [ ] 对外暴露专用本地回环 HTTP REST API（`http://127.0.0.1:9090`），实现 `GET /api/v1/status`、`POST /api/v1/survey`、`POST /api/v1/jobs/start`、`POST /api/v1/jobs/abort`、`GET /api/v1/logs/stream`（SSE 协议）。
- [ ] 在 `POST /api/v1/jobs/start` 处构筑 4 项确定性前置就绪门禁：① 目标节点 10s 内确认处于 `IDLE` 状态；② 初始模型文件存在且大小校验一致；③ 下行 UFTP 速率符合所选预设安全区间（越界需带 `--force`）；④ 当前引擎处于空闲，无并发作业冲突。
- [ ] 提供对 REST IPC 接口和四项前置门禁的端到端自动化测试。
