# Stage 2 三机无 SSH 运行时自治验收指南

本文给出 Stage 2 常驻双守护架构在真实无线硬件上的开发验收方法，供后续修改 Server Daemon、Client Daemon、控制面、RoleService、FL Coordinator、Runtime 或 Transport 时复用。

本文负责验收边界、执行流程、通过标准和诊断经验，不替代设计文档。架构规则以[系统分层与跨层契约](../design/系统分层与跨层契约.md)、[FL Runtime 轮次与文件契约](../design/FL-Runtime轮次与文件契约.md)、[Transport Backend 传输契约](../design/Transport-Backend传输契约.md)和 [ADR-0012](../adr/0012-脱离ssh的带内双向udp自愈与常驻双守护架构.md)为准。

## 1. 验收结论的准确含义

本验收证明：

- Server 作业启动后，不通过 SSH 调用 Client，也不通过 SSH 搬运模型或 update；
- Server Daemon 能通过带内 UDP 发布任务；
- Client Daemon 能自主派生隔离的 RoleService；
- Server 能等待目标 Client 的作业级就绪确认，再启动 FL Coordinator；
- Coordinator 能驱动多轮模型发布、update 收集、全员判定和聚合；
- UFTP 下行和 HTTP PUT 上行经过真实无线数据面；
- 作业完成或失败后，Server 与 Client 能回收任务资源、重建空闲链路并恢复待命；
- 作业终态后不遗留 RoleService 或 UFTP 任务进程，空闲 `wfb_v6_uplink` 与 TUN 正常保留；停止受管 daemon 单元后，三端不再遗留相关进程或 TUN。

本验收不自动证明：

- 真实机器学习训练、评估或模型收敛；
- 真实 FedAvg 数值计算；
- 未实际执行的制品大小、节点规模或协同范式；
- Web 控制台、外部管理网接入或生产鉴权；
- 链路层正式验收要求的全部内部 2A/2B 证据。

确定性验收算法 `wfb_ng.fl.issue41_algorithm` 使用正式算法入口和正式 Runtime 四接口，但其 `train()` 复制每个节点不同的 update fixture，`aggregate()` 校验 update 后复制上一轮模型。因此，它验证的是基础设施和算法调用契约，不是训练语义。

推荐使用以下结论模板：

> 已完成三机真实射频、作业运行时无 SSH 依赖的端到端本地自治验证，覆盖任务发布、客户端自治启动、多轮协调、双向数据传输、确定性占位训练/聚合、跨轮连续性和全集群自动复位。本次未验证真实 FL 训练语义；制品大小、节点数和协同范式仅按实际执行范围声明。

## 2. “无 SSH”的边界

“无 SSH”特指**作业运行时控制路径无 SSH 依赖**。

允许在作业开始前通过 SSH：

- 同步代码或安装软件包；
- 写入节点静态身份配置；
- 启动预装 systemd 服务，或在开发验收中启动临时受管单元；
- 生成确定性模型和 update fixture。

允许在作业结束后通过 SSH：

- 收集三端 daemon journal、Server 协调摘要和 REST 终态；
- 停止测试单元；
- 核查进程、TUN 和工作树状态。

Client Daemon 在收到作业终态后会立即删除 `job_<job_id>` 沙箱目录，其中的 `role_service.log`、RoleService 配置和客户端 Runtime 制品不能依赖作业结束后再取证。需要额外诊断材料时，只能在作业仍运行时只读复制 `role_service.log`、`uftpd.log` 和生成配置，且不得复制模型、manifest 或 update，不得控制 Client 作业、修改状态或促成成功；正式长期留存仍应以实现自动持久归档后为准。

从 REST 作业请求被接受到作业进入终态期间，禁止通过 SSH：

- 启动 Client 作业；
- 调用 Client Runtime；
- 复制模型、manifest 或 update；
- 手工重启链路以促成成功；
- 修改节点状态或替 Coordinator 补发控制消息。

如果运行期间需要上述操作，该次结果不能判定为本地自治通过。

## 3. 当前三机拓扑

| 角色 | 主机 | Node ID | TUN IP | 身份文件 |
| --- | --- | ---: | --- | --- |
| Server | vm0（本机） | 255 | `10.80.0.1` | Server 配置或默认配置 |
| Client 1 | `vm1` | 1 | `10.80.0.11` | `/etc/wfb-ng-fl/node.json` |
| Client 2 | `vm2` | 2 | `10.80.0.12` | `/etc/wfb-ng-fl/node.json` |

无线接口必须通过 `wlx*` 动态发现，不得把当前接口名写入通用脚本。当前台面测试使用信道 157、发射功率 12 dBm；变更射频参数时遵循现行射频设计与人工确认规则。

平台支持 1～10 号节点身份和槽位，不等于已完成对应实体节点验收。三机结果只能声明 1 个 Server 加 2 个 Client。

## 4. 固定网络契约

| 用途 | 地址或端口 |
| --- | --- |
| Server 控制面广播 | `255.255.255.255:9000` |
| Client 控制面上行 | `10.80.0.1:9001` |
| Server 本地 REST IPC | `http://127.0.0.1:9090` |
| UFTP 数据端口 | UDP `1044` |
| UFTP 公共组播 | `239.80.41.1` |
| UFTP 私有组播 | `239.80.41.2` |
| update HTTP 服务 | `10.80.0.1:8080` |
| 默认 WFB link ID | `7669206` |

控制面端口和 UFTP 数据端口必须隔离。不得把 UDP `9000` 或 `9001` 用作 UFTP 端口。Server 和 Client 的 `link_id`、组播地址、端口及 TUN 地址必须一致。

## 5. 生命周期与所有权

Client 的同名 TUN 在不同阶段只能有一个所有者：

1. **空闲期**：Client Daemon 的持久 `wfb_v6_uplink` 拥有 `fl-cN`，承载 heartbeat 和任务广播。
2. **任务准备期**：Daemon 停止空闲链路并释放 TUN，RoleService 接管同名 TUN。
3. **任务运行期**：RoleService 内的 Runtime/Transport 使用任务链路传输模型和 update。
4. **任务终态**：Server 关闭 ServerRole、重置持久 Server 链路并广播带 `job_id` 的终态消息；Client 只接受当前作业的终态消息，重建空闲链路并恢复 `IDLE`。

禁止同时启动空闲链路和 RoleService 链路。所有“是否可启动空闲链路”的判断必须在 Client Daemon 锁内重新确认，不能依赖锁外快照。

进程管理必须使用 systemd/cgroup 或代码中的正式停止路径。禁止用外层 `timeout` 直接强杀常驻 daemon，因为它可能留下子进程、僵尸进程或 TUN。

## 6. 作业级 READY 屏障

RoleService 的进程已创建不等于数据面已就绪。标准启动顺序是：

1. RoleService 创建任务 TUN、路由和 Transport；
2. UFTP 接收端启动；
3. RoleService 通过私有 `NOTIFY_SOCKET` 发送 `READY=1`；
4. Client Daemon 通过任务链路发送 `TASK_READY(job_id, node_id)`；
5. Client 使用独立 UDP socket 重试，直到收到 Server ACK；
6. Server 按 `job_id` 和目标节点集合去重收集；
7. 全部目标节点到齐后，Server 才启动 Coordinator 和首次 UFTP 发布。

普通 heartbeat 只表达节点拓扑状态，不能替代作业级 READY。固定 sleep 也不能替代 READY 屏障。

## 7. 运行前检查

### 7.1 提交和工作树

正式执行时三端必须处于同一运行时提交，且工作树干净：

```bash
git rev-parse --short HEAD
git status --short
ssh vm1 'cd /home/virt/projects/wfb-ng-fl && git rev-parse --short HEAD && git status --short'
ssh vm2 'cd /home/virt/projects/wfb-ng-fl && git rev-parse --short HEAD && git status --short'
```

跨节点同步使用 `git fetch` 和 `git merge --ff-only`，禁止使用 `git reset --hard`。

### 7.2 节点身份

```bash
ssh vm1 'sudo cat /etc/wfb-ng-fl/node.json'
ssh vm2 'sudo cat /etc/wfb-ng-fl/node.json'
```

期望值：

```json
{"node_id": 1, "tun_ip": "10.80.0.11"}
{"node_id": 2, "tun_ip": "10.80.0.12"}
```

### 7.3 无残留

执行前确认三端不存在旧 daemon、RoleService、`wfb_v6_uplink`、`uftp`、`uftpd` 或 `fl-s`/`fl-cN`。发现残留时先通过所属 systemd 单元停止，不要直接开始下一轮。

### 7.4 软件回归

运行时变更进入硬件测试前至少执行：

```bash
pytest -q
mypy --follow-imports=silent \
  wfb_ng/fl/control.py \
  wfb_ng/fl/client_daemon.py \
  wfb_ng/fl/server_daemon.py \
  wfb_ng/fl/runtime.py
```

## 8. 准备确定性制品

以下示例生成 4 MiB 功能验收制品。4 MiB 结果不能替代 40 MiB 容量验收。

Server：

```bash
cd /home/virt/projects/wfb-ng-fl
sudo mkdir -p /var/tmp/wfb-fl-hw
sudo python3 - <<'PY'
from wfb_ng.fl.issue41_fixtures import generate_model_fixture
print(generate_model_fixture('/var/tmp/wfb-fl-hw/model-4mib.bin'))
PY
```

Client 1：

```bash
ssh vm1 "sudo mkdir -p /tmp/wfb-ng-fl/client && \
  cd /home/virt/projects/wfb-ng-fl && sudo python3 - <<'PY'
from wfb_ng.fl.issue41_fixtures import generate_client_fixture
print(generate_client_fixture('/tmp/wfb-ng-fl/client/update-client1-template.bin', 1))
PY"
```

Client 2 同理，路径使用 `update-client2-template.bin`，节点 ID 使用 `2`。两个 update fixture 的 SHA-256 必须不同，否则无法证明 Server 确实收到了两个节点各自的 update。

## 9. 启动受管服务

正式部署优先使用预装 systemd unit。源码开发验收可使用临时受管单元，但必须设置 `KillMode=control-group`：

Server：

```bash
sudo systemd-run \
  --unit=wfb-fl-hw-server \
  --property=Type=exec \
  --property=KillMode=control-group \
  --property=TimeoutStopSec=10s \
  --working-directory=/home/virt/projects/wfb-ng-fl \
  /usr/bin/python3 -m wfb_ng.fl.server_daemon --channel 157 --txpower 12
```

Client：

```bash
ssh vm1 "sudo systemd-run \
  --unit=wfb-fl-hw-client \
  --property=Type=exec \
  --property=KillMode=control-group \
  --property=TimeoutStopSec=10s \
  --working-directory=/home/virt/projects/wfb-ng-fl \
  /usr/bin/python3 -m wfb_ng.fl.client_daemon"
```

在 vm2 执行同一命令。启动后通过 Server REST 状态等待 node 1/2 都达到 `IDLE/READY`，不能仅凭进程存在判定就绪。

## 10. 提交两轮自治作业

在 vm0 本地调用 REST；不要通过 SSH 调用 Client：

```bash
python3 - <<'PY'
import json
import urllib.request
import uuid

run_id = f'stage2_hw_{uuid.uuid4().hex}'
job_id = f'{run_id}_sync_2round_4mib'
payload = {
    'run_id': run_id,
    'job_id': job_id,
    'mode': 'sync',
    'rounds': 2,
    'target_nodes': [1, 2],
    'model_path': '/var/tmp/wfb-fl-hw/model-4mib.bin',
    'model_size_bytes': 4 * 1024 * 1024,
    'round_timeout_seconds': 60.0,
    'io_timeout_seconds': 120,
    'live_observation': True,
    'algorithm': 'wfb_ng.fl.issue41_algorithm:client_main',
    'algorithm_config': {
        'update_template_path': '/tmp/wfb-ng-fl/client/update-client{node_id}-template.bin',
    },
}
request = urllib.request.Request(
    'http://127.0.0.1:9090/api/v1/jobs/start',
    data=json.dumps(payload).encode(),
    headers={'Content-Type': 'application/json'},
    method='POST',
)
with urllib.request.urlopen(request, timeout=20) as response:
    print(f'run_id={run_id} job_id={job_id}')
    print(response.read().decode())
PY
```

请求返回 `accepted` 只表示作业通过前置门禁并越过 READY 屏障，不表示作业最终成功。最终结论必须读取：

```text
/tmp/wfb-ng-fl/server/job_<job_id>/coordinator_summary.json
```

## 11. 通过标准

一次两轮 `sync` 自治验收必须同时满足：

- REST 启动作业成功，目标节点严格为 `[1, 2]`；
- 两个 Client 均由 TASK_ANNOUNCE 自主启动 RoleService；
- Server 收到两个当前 `job_id` 的 TASK_READY 后才发布模型；
- `status == "succeeded"`；
- `rounds_completed == 2`；
- 每轮 `committed_nodes == [1, 2]`；
- 每轮 `dropped_out_nodes == []`；
- 第 2 轮 `input_model_sha256` 等于第 1 轮 `output_model_sha256`；
- Server 最终恢复 `IDLE`；
- node 1/2 在约定窗口内恢复 `IDLE/READY`；
- 停止测试单元后，三端无相关进程和 TUN 残留。

占位聚合保持模型内容不变，因此最终 SHA-256 不变是当前 fixture 的预期行为。它不能解释为真实模型已聚合或收敛。

## 12. 证据归档

至少保存以下可稳定取得的证据：

- 三端提交 ID 和工作树状态；
- 作业请求 JSON；
- Server 的 `coordinator_summary.json` 及其 SHA-256 manifest；
- 三端 daemon journal；
- 作业前、运行中、终态后的 REST 状态；
- 清理后的进程和 TUN 核查结果。

ServerRole 日志、每轮 Server 模型与 update manifest 可一并归档。Client 的 `role_service.log`、生成配置和 Runtime 制品位于终态即删除的沙箱目录，不得列为事后必然可取的证据；若为故障诊断在运行中只读复制 Client 日志或生成配置，应单独记录取证时间和 SHA-256，仍不得复制模型、manifest 或 update。

`/tmp` 内容会丢失。需要作为正式证据时，应在停止服务前复制到带 run ID 的持久归档目录，并记录文件 SHA-256。只保存终端截图不构成完整归档。

## 13. 推荐诊断顺序

故障诊断按生命周期推进，不要先调射频参数。

### 13.1 节点始终 OFFLINE

依次检查：

1. Client 是否存在空闲 `wfb_v6_uplink`；
2. `fl-cN` 是否由空闲链路创建；
3. Client 到 `10.80.0.1:9001` 的控制上行是否经过 TUN；
4. Server 是否监听 UDP 9001；
5. Server/Client `link_id` 是否一致；
6. 物理接口是否仍在同一信道。

只创建 TUN、不启动客户端链路底座时，heartbeat 会写入无人消费的 TUN，继续等待不会恢复。

### 13.2 RoleService 启动后立即退出

当前失败清理路径会立即删除 Client 的 `job_<job_id>` 沙箱目录，事后通常无法再读取其中的 `role_service.log`、`uftpd.log` 或生成配置。先检查 Client daemon journal 中记录的启动阶段、退出码和异常；如果需要沙箱内细节，应在复现时实时只读跟踪日志，或先实现失败证据自动持久化，不能假定失败后目录仍存在。

曾确认的确定性错误包括：

- UFTP 使用 UDP 9000，与控制面监听冲突；
- 使用 `224.0.0.1/224.0.0.2` 导致组播加入失败；
- 空闲链路与沙箱链路同时操作同名 TUN；
- Server 与 Client 使用不同 `link_id`。

### 13.3 UFTP ANNOUNCE 无接收端

先确认 RoleService 是否已经通过 `READY=1`，再确认 Server 是否收齐 TASK_READY。不得通过增加固定 sleep 掩盖竞态。

如果只有部分 Client REGISTER：

- 检查该 Client 的 `uftpd.log`；
- 检查任务链路是否收到 GRANT；
- 检查组播路由和 UFTP 绑定接口；
- 区分单次空口控制帧丢失与稳定配置错误。

### 13.4 作业成功但节点不能恢复 READY

检查时间顺序：

1. Client RoleService 何时退出；
2. Server 何时关闭 ServerRole 并重置持久链路；
3. Server 何时广播 `JOB_COMPLETED(job_id)`；
4. Client 是否只接受当前作业的终态消息；
5. Client 是否在终态消息后强制重建空闲链路。

不能只看 Client 已创建新进程；必须确认 Server REST 中节点重新成为 `IDLE/READY`。

### 13.5 监控脚本等待到超时

Server 很快回到 `IDLE` 不等于成功。监控器必须同时观察：

- 当前 `active_job`；
- coordinator summary 是否出现；
- summary 的 `status` 和 `error_code`；
- Client 状态变化。

不要只等待一个短暂的 `RUNNING` 状态，否则快速失败可能被误判为“仍在执行”。

## 14. 停止和残留核查

使用 systemd 停止整个 cgroup：

```bash
sudo systemctl stop wfb-fl-hw-server.service
ssh vm1 'sudo systemctl stop wfb-fl-hw-client.service'
ssh vm2 'sudo systemctl stop wfb-fl-hw-client.service'
```

随后精确检查：

- `python3 -m wfb_ng.fl.server_daemon`；
- `python3 -m wfb_ng.fl.client_daemon`；
- `wfb_ng.fl.role_service`；
- `wfb_v6_uplink`；
- `uftp` / `uftpd`；
- `fl-s` / `fl-c1` / `fl-c2`。

避免使用会匹配检查命令自身的宽泛 `pgrep -f`；可结合 `ps -eo pid,comm,args` 和精确进程名核查。

## 15. 变更后的回归范围

| 变更范围 | 最低软件回归 | 真实硬件要求 |
| --- | --- | --- |
| 文档，不改变运行时 | 文档链接和命令复核 | 可复用已有同提交运行时证据 |
| Coordinator 纯状态逻辑 | Coordinator 单测、E2E mock、全量测试 | 影响正式调度语义时重跑 |
| 控制面、READY、daemon 生命周期 | 控制面、Client/Server daemon、E2E mock、全量测试、Mypy | 必须重跑三机自治闭环 |
| Runtime/Transport/UFTP/HTTP | 对应单测、E2E mock、全量测试、Mypy | 必须重跑；容量相关变更还要跑目标大小 |
| 射频、链路调度或 WFB 二进制 | 链路测试与 Runtime 测试 | 必须执行对应真实硬件正式验收 |
| 算法实现 | 算法契约、Runtime、多轮连续性测试 | 真实训练声明必须使用真实算法和数据 |

## 16. 已验证基线

2026-10-09 的开发验收基线：

- 运行时提交：`a0c9284`；
- 拓扑：vm0 + vm1 + vm2；
- 作业：`hw_sync_2round_4mib_a0c9284`；
- 模式：`sync`，2 轮，4 MiB；
- READY 握手：1.284 秒；
- 第 1 轮：6.351 秒；
- 第 2 轮：7.641 秒；
- 两轮均由 node 1/2 提交，无掉队；
- 第 2 轮输入 SHA-256 等于第 1 轮输出 SHA-256；
- 作业终态后 node 1/2 均恢复 `IDLE/READY`；
- 停止单元后三端无相关进程或 TUN 残留。

该基线证明 4 MiB 三机自治功能闭环，不替代 40 MiB 实体容量验收，也不证明真实训练或 FedAvg。
