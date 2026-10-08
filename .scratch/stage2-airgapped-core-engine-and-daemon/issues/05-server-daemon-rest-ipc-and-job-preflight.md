# 05: 服务端常驻协同守护引擎、预置链路层槽位池、本地专用 REST IPC 与作业前置门禁

**What to build:** 交付 `wfb-fl-server-daemon` 可执行程序与 systemd 服务单元。独占纳管服务端网卡，拉起底层 `wfb_v6_uplink` 时依赖其内置固化的 120ms/10ms 调度默认值（无需外部传参），并一次性全量预置平台 1~10 号节点白名单槽位（`--known-clients 1,2,3,4,5,6,7,8,9,10` 及其静态 `tun_ip` 映射），确保静默节点自动修剪（Silent Node Removal）生效，实现新节点即插即用无需重启底座；维护内存级全网拓扑与细粒度状态注册表（`NodeHorizonRegistry`）；暴露专用本地回环 HTTP REST API（`http://127.0.0.1:9090`，支持 `/status`、`/survey`、`/jobs/start`、`/jobs/abort`、`/logs/stream`）；在 `/jobs/start` 构筑 3 项确定性前置门禁（目标节点 10s 内确认处于 IDLE、模型存在且大小合法、当前引擎空闲无冲突作业）。

**当前测试拓扑：** 涉及跨节点、硬件或端到端验证的测试仅使用本机 Server、`vm1`（client1）和 `vm2`（client2）；1~10 号节点支持属于平台容量约束，未提供节点只做配置或单元测试，不作为本阶段实体集群验收依据。

**Blocked by:** 01: 5GHz 扫频探路模块、扁平射频参数直配与速率约束生成器, 03: 对称 UDP 控制信令平面、三级寻频自愈与两军问题防护状态机, 04: 两阶段射频重配引擎与防脑裂租约看门狗自愈机制

**Status:** resolved

- [x] 交付 `wfb-fl-server-daemon` 可执行程序与 systemd 服务单元。
- [x] 开机独占纳管服务端物理无线网卡，拉起底层 `wfb_v6_uplink` 时一次性全量预置平台支持的 1~10 号节点白名单槽位（`--known-clients 1,2,3,4,5,6,7,8,9,10` 及其静态 `tun_ip` 映射），依赖底座内置默认 120ms/10ms 时隙参数，确保静默节点自动修剪正常运转，后续任何新节点接入时均可立即被 C++ Token 调度器授予 Grant 并建立单播下行路由，无需重启底座。
- [x] 监听 UDP 9001 端口，维护内存级全网拓扑与细粒度状态注册表（`NodeHorizonRegistry`），毫秒级记录节点 ID（支持 1~10）、IP、当前状态机阶段、已耗时、信道、当前射频配置与最后心跳时间戳。
- [x] 对外暴露专用本地回环 HTTP REST API（`http://127.0.0.1:9090`），实现 `GET /api/v1/status`、`POST /api/v1/survey`、`POST /api/v1/jobs/start`、`POST /api/v1/jobs/abort`、`GET /api/v1/logs/stream`（SSE 协议）。
- [x] 在 `POST /api/v1/jobs/start` 处构筑 3 项确定性前置就绪门禁：① 目标节点 10s 内确认处于 `IDLE` 状态；② 初始模型文件存在且大小校验一致；③ 当前协同引擎处于空闲，无并发作业冲突。
- [x] 提供对 REST IPC 接口和三项前置门禁的端到端自动化测试。

## 验收结论

1. 交付了 `wfb_ng/fl/server_daemon.py` 与 `wfb-fl-server-daemon` 命令行入口，并在 `setup.py` 中注册 `console_scripts` 和 `data_files`。
2. 交付了 systemd 服务单元 `scripts/systemd/wfb-fl-server-daemon.service` 与默认配置文件 `scripts/default/server.json`。
3. `build_v6_uplink_server_command` 实现了预置 1~10 号节点槽位与静态 `tun_ip` 映射，严格遵循 ADR-0014，依赖底座默认固化的 120ms/10ms 调度时隙，不传递 `--grant-duration-ms` 和 `--guard-interval-ms`。
4. 交付了本地回环专用 REST IPC（`http://127.0.0.1:9090`），实现了 `/status`、`/survey`、`/jobs/start`、`/jobs/abort`、`/logs/stream`（SSE）。
5. 在 `/jobs/start` 构筑了三项确定性前置门禁：
   - 门禁 1：目标节点 10 秒内确认处于 `IDLE` 状态，对未上线、寻频中、两军防虚假就绪连接中、超时离线节点严格 fail-closed；
   - 门禁 2：初始模型文件存在、非空，且与预期大小一致；
   - 门禁 3：协同引擎处于 `IDLE` 空闲态，并发冲突作业拒绝并返回 HTTP 409。
6. 交付全套自动化测试 `wfb_ng/tests/test_fl_server_daemon.py`（22 个用例全部通过），全量测试套件 148 个用例 100% 通过。
