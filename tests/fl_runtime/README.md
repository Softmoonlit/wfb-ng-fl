# 完整 FL Runtime 真实硬件验收

Stage 3 常驻双 daemon 的唯一正式入口为 `stage3_vm_physical_loop.sh`，参见 [Stage 3 三机执行器](stage3三机真实射频执行器.md)。它使用独立 envelope 和 validator，通过 Server 本地 REST 发起作业并验证作业运行时无 SSH 依赖。以下内容介绍 Issue #41 的 SSH 编排验收，属于另一套验收边界。

Stage 4 三机实体 Web 闭环的唯一入口为 `stage4_hardware_acceptance.sh`。它固定使用本机 Server、`vm1`、`vm2`，从 Web 上传 canonical 40 MiB fixture，正常执行两轮同步文件作业，再通过 Web 发起一次急停；客户端 SSH 在作业窗口内由执行器硬拒绝。归档分为 `management-web/`、`control-plane/`、`data-plane/`、`lifecycle/` 和 `summary/`，离线校验命令为：

```bash
bash tests/fl_runtime/stage4_hardware_acceptance.sh
```

管理网地址通过 `STAGE4_WEB_URL` 指定。分阶段复核已有归档时：

```bash
python3 -m tests.fl_runtime.stage4_hardware_archive tests/logs/<run_id>
```

实体验收要求 Server 与两个 Client 提交一致、工作树干净、无线接口动态发现为 `wl*`，并由现有硬件探测器确认 `rtl88xxau_wfb` 与 `xhci_hcd`。管理 Web 成功不会替代控制面或数据面门禁；任一门禁失败，归档结论均为 `failed`。

`issue41_fl_runtime_loop.sh run-all` 是完整验收总入口：依次执行预检、安装、连续三周期双向数据面 Gate、配置等价性核验、正式 Runtime 闭环、systemd 生命周期审计、证据采集和归档校验。正式闭环通过 `publish_model()`、`wait_for_model()`、`submit_update()`、`wait_for_updates()` 完成；任何关键阶段或证据失败，总结论都不能通过。

[完整现场手册](v8_issue41_SSH编排真实硬件FL闭环验收手册.md) 保留原验收步骤、参数及证据要求。手册包含历史现场基线；本次运行的客户端集合、文件大小、轮数与无线参数以现役脚本和操作者显式配置为准，本次目录迁移不改变这些值。当前脚本默认客户端集合为 `client1 client2 client3 client4 client6 client7`，管理别名为对应 `vmN`；可通过 `ISSUE41_CLIENT_ROLES` 配置现场参与集合。

现场需要真实 server/client 无线角色机，每台恰好一个可动态发现的 `wl*` 无线接口、匹配驱动、monitor/原始帧发送与 TUN 支持；需要 SSH 管理连接、sudo、systemd、`ip`、`iw`、Python 3、构建工具和 `uftp`/`uftpd` 等手册规定依赖。所有节点必须使用同一分支与提交，工作区干净。管理网只用于编排和采集，模型/update 必须经过 WFB/TUN 无线链路。

仓库与远端版本已同步、依赖及输入文件已准备好时，从仓库根执行：

```bash
bash tests/fl_runtime/issue41_fl_runtime_loop.sh run-all
```

若需生成验收用确定性模型和各客户端独立 update 模板，先执行下面命令。它写入角色机上的输入目录；已有业务输入时按手册显式配置文件路径。

```bash
bash tests/fl_runtime/issue41_fl_runtime_loop.sh generate-fixtures
bash tests/fl_runtime/issue41_fl_runtime_loop.sh run-all
```

`run-all` 不自动发布分支或同步远端；手册中的 `push-branch`、`sync-remotes` 为显式准备步骤。分阶段执行时，先设置同一个唯一 `ISSUE41_RUN_ID`，让各次调用使用同一归档；`run-runtime-loop` 单阶段成功或 `smoke-gate` 成功不能替代完整验收。

默认归档位于 `tests/logs/v8_issue41_<timestamp>/`，包含 `envelope.json`、`orchestration/`、`pre_runtime_smoke/`、`formal_runtime_loop/`、`lifecycle/`、`raw/`、`issue41_summary.json` 和 `result.md`。Runtime 工作文件位于 `/var/lib/wfb-ng/issue41/{server,client}`。失败时保留证据，目录的受控清理使用手册规定的 `clean` 流程。

辅助文件职责：

| 文件 | 用途 |
| --- | --- |
| `issue41_envelope.py` | 运行包络、拓扑、预检和分区失败记录 |
| `issue41_gate.py` | 双向数据面 Gate 的直接传输与遥测证据校验 |
| `issue41_lifecycle.py` | 服务停止、重启、进程与资源清理审计 |
| `issue41_build_summary.py` | 从 Runtime 结果与日志汇总正式闭环事实 |
| `issue41_validate_archive.py` | 严格校验归档完整性与最终结论 |

离线审阅已有归档可在仓库根执行：

```bash
python3 -m tests.fl_runtime.issue41_validate_archive tests/logs/<run_id>
```

## 真实硬件测试现场经验与踩坑排查指南

在多机真实硬件与 VMware 虚拟机测试现场，总结沉淀了以下关键经验与排查准则：

### 1. 宿主机 USB 接口与虚拟 USB 控制器（关键避坑）
- **现象**：客户端虚拟机在 40 MiB 组播下行（15 Mbps）接收时，底层空口接收包数断崖式下跌（丢包率高达 87%），频繁发送大量 NAK 并最终在 120 秒单次 I/O 超时门禁被强行终止。
- **根本原因**：
  - 若物理网卡插在宿主机的物理 USB 2.0 接口（黑色/白色母口）或 USB 2.0 Hub 扩展坞上，VMware 会将其分配给虚拟机的虚拟 `ehci-pci` 控制器。
  - VMware 的虚拟 `ehci-pci` 模拟层在高吞吐（15 Mbps 广播帧，每秒万级数据包）下存在严重的虚拟中断合并与调度延迟，导致 Linux 内核与驱动无法及时取包，在虚拟 USB 总线处直接丢弃了 87% 的空口包。
- **强制规范**：
  - 所有无线网卡必须插在宿主机的 **物理 USB 3.0 蓝色接口（或标有 SS 标志的接口）** / USB 3.0 扩展坞上。
  - 在虚拟机内通过 `lsusb -t` 检查，驱动必须挂载在 **`xhci_hcd`** 根控制器下（即使网卡协商速率为 480M，只要控制器是 `xhci_hcd`，中断延时极低，即可达成 ~23 秒 40 MiB 稳定收全且零超时）。严禁挂载在 `ehci-pci` 控制器下。

### 2. 物理网卡个体特性与台面角色分配
- **Server 端发射标定**：MAC 为 **`5c:ff:ff:af:6d:8c`** 的网卡经多轮实测 5GHz 广播覆盖最广、发射最稳定，**必须固定分配给 Server 虚拟机** 担任多播发射机。
- **弱网卡规避**：MAC 为 **`fc:22:1c:10:01:19`** 的网卡在 5GHz HT40+ MCS 6 高调制下发射衰减大、空口分片丢失率高（例如客户端向 Server 发送 REGISTER 握手包时，Server 仅能收到 4 个分片，达不到 FEC $K=8$ 恢复阈值导致 UFTP 握手失败）。严禁将该网卡分配给核心收发节点。
- **客户端分配**：优先选用 `fc:22:1c:30:0f:04`、`fc:22:1c:10:01:57`、`fc:22:1c:30:0c:bb`、`fc:22:1c:30:0c:bc` 等经过全量验证的网卡。

### 3. VMware 挂载防闪断与连接确认
- **现象**：网卡插拔或重新分配后，虚拟机内核 `dmesg` 显示网卡连接后 1 秒内被自动断开（`USB disconnect, device number N`），导致 `find_wlx` 找不到网卡。
- **处理方式**：宿主机焦点切换可能导致 VMware 重定向丢失。必须在对应虚拟机窗口手动点击：`虚拟机 (VM) -> 可移动设备 (Removable Devices) -> Realtek 802.11n NIC -> 连接 (断开与主机的连接)`。测试前执行 `bash tests/fl_runtime/issue41_fl_runtime_loop.sh preflight` 验证所有节点网卡均稳定在线。

### 4. 运行环境变量与拓扑控制
- **部分节点测试**：现场节点数少于默认 6 台时（如 1 Server + 2 Client 或 1 Server + 4 Client），必须显式指定参测集合：
  ```bash
  export ISSUE41_CLIENT_ROLES="client1 client2"
  ```
- **冒烟门禁轮数**：独立运行的 `smoke-gate` 缺乏 FL Server 守护进程的多轮生命周期编排，硬件测试应导出单周期冒烟：
  ```bash
  export ISSUE41_SMOKE_CYCLE_COUNT=1
  ```
  正式的多轮训练与状态机流转由 `formal_runtime_loop`（默认 2 轮）完整验证。

### 5. 验收基准指标对照
- **数据面 Gate 单轮耗时**：60~100 秒（UFTP 下行 20~30 秒，并发 HTTP PUT 上行 35~80 秒）。
- **底层空口丢包率**：< 5%（FEC 恢复率 20~30%）。
- **Formal FL 闭环（2 轮 40 MiB）**：总耗时 90~250 秒（远低于 400 秒时限，裕量 > 150 秒）。
- **数据面结果**：全客户端 SHA-256 校验 100% 吻合，HTTP 状态码 100% 达成 201。

[通信演示](../demo/README.md) 和 [手动上行底座验收](../link_uplink/README.md) 各有自己的证据边界。本目录不提供本机回归或 namespace 验收入口。
