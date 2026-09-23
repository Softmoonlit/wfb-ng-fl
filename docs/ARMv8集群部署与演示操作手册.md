# ARMv8 多板载集群环境部署与现场演示操作手册

本文档为面向操作者和现场汇报者的全流程实操指南，涵盖：**从电脑端推送代码到 Server 开发板、Server 本机一键环境装配、集群免密互信分发、多 Client 节点批量并行部署，以及面向导师现场汇报的大文件组播广播与受控上行回传演示**。

---

## 目录

1. [系统架构与网络拓扑](#一系统架构与网络拓扑)
2. [环境准备与依赖要求](#二环境准备与依赖要求)
3. [阶段零：从 Windows 电脑向 Server 开发板传输代码](#三阶段零从-windows-电脑向-server-开发板传输代码)
4. [阶段一：配置集群全局参数 (`cluster_nodes.conf`)](#四阶段一配置集群全局参数-cluster_nodesconf)
5. [阶段二：Server 本机一键环境装配 (`deploy_node.sh`)](#五阶段二server-本机一键环境装配-deploy_nodesh)
6. [阶段三：一键建立全集群免密互信 (`setup_cluster_auth.sh`)](#六阶段三一键建立全集群免密互信-setup_cluster_authsh)
7. [阶段四：全集群一键并行批量部署 (`deploy_cluster.sh`)](#七阶段四全集群一键并行批量部署-deploy_clustersh)
8. [阶段五：现场演示与多维指标看板 (`run_fl_demo.sh`)](#八阶段五现场演示与多维指标看板-run_fl_demosh)
9. [常见故障排查与避坑指南](#九常见故障排查与避坑指南)

---

## 一、系统架构与网络拓扑

整个测试床采用**带外管理平面**与**空口数据平面**完全物理隔离的双平面架构：

```
[操作者电脑 (Windows)]
        │ (以太网 / 路由器 Wi-Fi)
        ▼
 ┌──────────────┐   带外以太网局域网 (DHCP, 192.168.1.x)   ┌──────────────┐
 │ Server 开发板 │ ───────────────────────────────────────> │ 各 Client 节点│ (client1 ~ client7)
 └──────────────┘                                         └──────────────┘
        │                                                        │
        │ [USB RTL8812AU 网卡]                                   │ [USB RTL8812AU 网卡]
        │ (Monitor 模式, 信道 157 HT40+, 功率 12 dBm)               │ (Monitor 模式, 相同信道)
        ▼                                                        ▼
 ═══════════════════════════════════════════════════════════════════════════════
                   WFB-FL 空口数据平面 (100% 真实无线链路)
       • 下行：UFTP UDP 组播广播 (239.80.41.1:1044, MCS 3, 15 Mbps)
       • 上行：受控 HTTP PUT 回传 (TUN 10.80.0.x, 令牌轮转, 反压流控)
 ═══════════════════════════════════════════════════════════════════════════════
```

- **带外管理平面**：电脑通过 SSH 连接 Server 开发板，Server 通过 SSH 调度各 Client 板子，仅用于命令下发、节点存活感知与遥测数据采集；
- **空口数据平面**：所有待测大文件（如模型参数包）100% 通过 WFB-FL 构建的 TUN 虚拟网卡与 802.11 原始注入/监听链路传输，严禁走以太网。

---

## 二、环境准备与依赖要求

### 1. 硬件准备
- **开发板**：1 台作为 Server，5~7 台作为 Client（推荐 ARMv8 架构，如 RK3588/RK3568/树莓派 4B 等，系统为 Ubuntu 20.04/22.04 LTS aarch64）；
- **无线网卡**：每台开发板外接 1 个 RTL8812AU 双频 USB 无线网卡（系统内识别为唯一的 `wlx*` 接口）；
- **局域网交换机/路由器**：将所有开发板以太网口与操作者电脑连入同一局域网（确保彼此网络可达）。

### 2. 本地文件准备
在操作者电脑上，确保目录结构如下：
```text
projects/
├── uftp_src-5.0.3.zip     # UFTP 离线源码包 (约 318 KB)
└── wfb-ng-fl/             # 项目代码仓库根目录
```

---

## 三、阶段零：从 Windows 电脑向 Server 开发板传输代码

在 Windows 上使用系统自带的 **PowerShell** 终端，将代码和离线依赖一次性推送到 Server 开发板。

项目已为你内置了 Windows 专用的**一键同步脚本 (`scripts/sync_to_server.ps1`)**。该脚本在后台全自动完成：
1. 自动定位上级目录中的 `..\uftp_src-5.0.3.zip` 离线源码包并上传至 Server 端父级目录；
2. 利用 Windows 10/11 内置的 `tar.exe` 管道流极速上传项目代码，**自动排除庞大的 `.git` 历史与编译垃圾**，在 Server 端就地秒级解压；
3. 全面支持 IP 地址（如 `192.168.1.100`）与 `~/.ssh/config` 中定义的 SSH 别名（如 `vm0`）。

### 1. 一键脚本使用方法 (推荐)

打开 **Windows PowerShell**，进入 `wfb-ng-fl` 目录执行：

```powershell
# 场景 A: 使用开发板局域网 IP
powershell -File .\scripts\sync_to_server.ps1 -Server 192.168.1.100 -User ubuntu

# 场景 B: 使用 SSH 配置别名 (如 vm0，自动应用别名内绑定的用户名与私钥)
powershell -File .\scripts\sync_to_server.ps1 -Server vm0

# 场景 C: 预先演练 (仅查看即将执行的操作，不产生实际网络传输)
powershell -File .\scripts\sync_to_server.ps1 -Server vm0 -DryRun
```

### 2. 手动执行方式 (脚本底层原理说明)

若希望了解底层命令或手动执行，可在 PowerShell 中运行等价命令：

```powershell
# 假定 Server 开发板局域网 IP 为 192.168.1.100，用户名为 ubuntu
$SERVER_IP = "192.168.1.100"
$SERVER_USER = "ubuntu"

# 1. 确保 Server 开发板上存在 ~/projects 目录
ssh ${SERVER_USER}@${SERVER_IP} "mkdir -p ~/projects"

# 2. 将 uftp 离线源码包上传到 Server 的 ~/projects/ 目录
scp ..\uftp_src-5.0.3.zip ${SERVER_USER}@${SERVER_IP}:~/projects/

# 3. 管道极速打包上传代码 (排除 .git 与临时编译产物，远端就地解压)
tar --exclude=".git" --exclude="*.o" -czf - . | ssh ${SERVER_USER}@${SERVER_IP} "mkdir -p ~/projects/wfb-ng-fl && tar -xzf - -C ~/projects/wfb-ng-fl"
```

传输完成后，在 PowerShell 中直接登录 Server 开发板：
```powershell
ssh ubuntu@192.168.1.100
cd ~/projects/wfb-ng-fl
```

---

## 四、阶段一：配置集群全局参数 (`cluster_nodes.conf`)

在 Server 板端登录后，所有后续操作均在 Server 终端中执行。

### 1. 修改配置文件
```bash
nano cluster_nodes.conf
```

### 2. 核心参数配置建议

```ini
# ------------------------------------------------------------------------------
# 1. 物理射频与空口参数
# ------------------------------------------------------------------------------
# 射频信道: 推荐 157 (硬性安全红线: 严禁使用 161！161 存在驱动缺陷会导致内核崩溃瘫痪)
WIRELESS_CHANNEL=157

# 频宽: 推荐 HT40+ (40MHz 带宽)
WIRELESS_CHANNEL_WIDTH=HT40+

# 物理发射功率: 推荐 12 dBm (近距台面测试建议 10~13 dBm，切勿超过 15 dBm，防止接收端 LNA 饱和失真)
WIRELESS_TXPOWER_DBM=12

# 调制编码策略 (MCS): 下行组播推荐 3，上行单播调度推荐 6
DOWNLINK_MCS=3
UPLINK_MCS=6

# 下行 UFTP 组播发送速率: MCS 3 对应安全推荐速率为 15000 Kbps (15 Mbps)
UFTP_RATE_KBPS=15000

# ------------------------------------------------------------------------------
# 2. 服务端配置
# ------------------------------------------------------------------------------
SERVER_NODE_ID=255
SERVER_TUN_IP=10.80.0.1/24

# ------------------------------------------------------------------------------
# 3. 客户端拓扑列表 (根据路由器实际分配的以太网 IP 填写 5~7 台 Client)
# 格式: 角色名  管理网IP  SSH用户名  底座节点ID(1~254)  TUN虚拟IP
# ------------------------------------------------------------------------------
CLIENTS=(
    "client1 192.168.1.101 ubuntu 1 10.80.0.11"
    "client2 192.168.1.102 ubuntu 2 10.80.0.12"
    "client3 192.168.1.103 ubuntu 3 10.80.0.13"
    "client4 192.168.1.104 ubuntu 4 10.80.0.14"
    "client5 192.168.1.105 ubuntu 5 10.80.0.15"
)
```

### 3. 快速语法与有效性校验
执行自带的解析校验器，确保配置无误：
```bash
./scripts/cluster_config.sh cluster_nodes.conf
```
终端输出 `[PASS] 配置文件校验通过` 即表示参数合法。

---

## 五、阶段二：Server 本机一键环境装配 (`deploy_node.sh`)

在 Server 板端执行单机部署脚本，自动装配所有系统依赖、驱动与底座组件：

```bash
sudo ./scripts/deploy_node.sh
```

### 脚本自动执行的 5 大阶段：
1. **系统依赖自动安装**：自动执行 `apt-get update` 并安装 `build-essential`、`libsodium-dev`、`libpcap-dev`、`libssl-dev`、`dkms`、`iw`、`rfkill`、`net-tools` 等；
2. **RTL8812AU 网卡驱动构建**：自动检测/安装当前内核头文件，自动调用 DKMS 编译并加载 `8812au` 驱动；
3. **UFTP 离线编译与安装**：自动读取 `../uftp_src-5.0.3.zip`，就地针对 ARMv8 编译并把原生二进制 `uftp` 和 `uftpd` 安装至 `/usr/bin/`（**无需外网下载**）；
4. **系统网络与权限调优**：
   - 自动向 NetworkManager 写入规则，永久忽略 `wlx*` 前缀的无线网卡，防止系统重置 Monitor 模式；
   - 自动解锁 `rfkill unblock all`；
   - 自动应用 `scripts/sysctl/98-wifibroadcast.conf`，将内核 socket 与 datagram 队列大幅扩容；
   - 自动为当前用户配置免密 `sudo`（写入 `/etc/sudoers.d/99-wfb-nopasswd`），消除非交互卡死；
5. **底座核心编译与系统安装**：编译产出 `wfb_v6_uplink` 并完成系统安装。

> **演练选项**：若只想检查步骤而不实际改动系统，可运行 `./scripts/deploy_node.sh --dry-run`。

---

## 六、阶段三：一键建立全集群免密互信 (`setup_cluster_auth.sh`)

为使 Server 能在后台自动化调度和管理各 Client 板端，执行集群免密纳管工具：

```bash
./scripts/setup_cluster_auth.sh
```

### 操作说明：
- 脚本自动检测 Server 端的 `~/.ssh/id_rsa.pub`，若无则自动生成；
- 提示输入一次所有板子通用的登录密码（例如 `ubuntu`）；
- 自动调用 `ssh-copy-id` 将公钥分发给各 Client 节点；
- 远程向各 Client 写入免密 `sudo` 授权规则；
- 最终自动通过 `ssh -o BatchMode=yes <client> "sudo -n true"` 验证全流程畅通无阻。

---

## 七、阶段四：全集群一键并行批量部署 (`deploy_cluster.sh`)

全集群免密互信打通后，在 Server 端执行集群编排器，并行推进所有 Client 节点的环境部署：

```bash
# 1. 预检集群连通性与配置
./scripts/deploy_cluster.sh --check-only

# 2. 正式并发推送到全部 Client 板端进行安装
./scripts/deploy_cluster.sh
```

### 编排器特性：
- **存活探测与动态容错**：自动探测在线 Client 集合，有掉线节点会给出明显告警并自动以在线集合继续推进；
- **全并行高效同步**：使用多进程并发通过 `rsync` 向各 Client 推送代码树与 `uftp_src-5.0.3.zip`；
- **并发触发与就绪门禁**：在各 Client 端并发触发 `deploy_node.sh`，并在完成后执行严格的门禁检查（网卡是否存在、驱动是否加载、UFTP 二进制是否就绪、免密 sudo 是否生效）；
- **终端状态表格**：部署完成后在终端输出格式化的节点就绪汇总表。

---

## 八、阶段五：现场演示与多维指标看板 (`run_fl_demo.sh`)

面向导师现场汇报的核心总控脚本为 `tests/real_hardware/run_fl_demo.sh`。

### 1. 汇报前演练（Dry-Run 模式）
在不影响网卡硬件状态的前提下，快速演练全流程并确认指标看板渲染效果：
```bash
bash tests/real_hardware/run_fl_demo.sh all --dry-run
```

---

### 2. 正式全流程演示（一键跑完下行 + 上行）
现场最常用的汇报演示命令：
```bash
bash tests/real_hardware/run_fl_demo.sh all
```

**演示全流程自动化逻辑**：
1. **动态探测在线 Client**：自动通过 ping 和 SSH 探测在线节点集合，若有个别板子掉线，输出黄色告警并容错跳过，不会中断汇报；
2. **空口网卡准备**：在 Server 和所有在线 Client 上探测唯一的 `wlx*` 网卡，切换为 monitor 模式，严格锁定到信道 157、频宽 HT40+ 和功率 12 dBm；
3. **下行组播广播传输**：
   - 现场自动生成 40MB 确定性测试文件（或使用 `--file` 传入真实模型），计算并打印初始 SHA-256；
   - 远程在各 Client 拉起 `uftpd` 监听组播组，并在终端明确打印落盘绝对路径：
     `/var/lib/wfb-ng/fl_demo/received/model_40mb.bin`；
   - Server 启动底座组播通道并调用 `uftp` 广播下发；
   - 传输完成后远程核对每个 Client 接收文件的 SHA-256，终端渲染**【下行指标看板】**；
4. **上行受控回传传输**：
   - 各 Client 自动复用刚才接收到的 40MB 文件作为更新，并发发起受控 HTTP PUT 上传回 Server；
   - Server 端启动受控接收端与 `wfb_v6_uplink` 调度服务，底座通过令牌时隙（Token Passing）与反压机制控制空口发送权；
   - 实时解析物理遥测、TUN 队列流控（PAUSE/RESUME）与 Linux TCP 重传指标；
   - 计算 Server 最终落盘文件的 SHA-256 并与原文件严格核对，终端渲染**【上行指标看板】**。

---

### 3. 分步独立演示（适合向导师逐步深入剖析机制）

- **单独演示下行组播广播机制**（例如想用自己的真实大文件）：
  ```bash
  bash tests/real_hardware/run_fl_demo.sh downlink --file /path/to/my_model.bin
  ```
- **单独演示上行受控回传与反压机制**：
  ```bash
  bash tests/real_hardware/run_fl_demo.sh uplink
  ```

---

### 4. 现场看板关键指标解读指南（汇报要点）

#### 【下行大文件组播传输看板】
- **广播传输耗时与空口平均吞吐**：体现 1 次广播同时送达全部 5~7 台客户端的高效性（与单播相比，带宽消耗不随客户端数量增加而线性膨胀）；
- **落盘绝对路径**：向导师明确展示各客户端落地路径；
- **哈希一致性校验**：所有 Client 的 SHA-256 必须与源文件 100% 绝对一致，证明空口广播的无损可靠性。

#### 【上行受控调度回传看板】
- **聚合有效吞吐与各 Client 分源速率**：展示受控时隙划分下多客户端并发回传的聚合效率；
- **物理空中丢包与 FEC 恢复包数**：
  - *丢包数与丢包率*：体现真实无线信道的电磁干扰与衰落现实；
  - *FEC 恢复率*：向导师证明底座带内 FEC 纠错算法在物理层成功挽救了空中丢包；
- **TUN 反压流控 (PAUSE / RESUME 次数)**：证明当空口队列水位过高时，底座成功对上层 TUN 驱动实施了反压暂停，未击穿内核缓冲；
- **TCP 协议重传次数**：体现端到端传输层在物理层受控调度下的极高稳定性；
- **最终哈希校验**：Server 归档保存的各个 Client 更新文件与源文件 SHA-256 绝对一致，完成闭环验收。

---

### 5. 演示结束清理
演示完毕后，若需停止残留后台进程、释放端口并清理 TUN 虚拟网卡，执行：
```bash
bash tests/real_hardware/run_fl_demo.sh clean
```

---

## 九、常见故障排查与避坑指南

### 1. 为什么不能配置信道 161？
- **原因**：RTL8812AU 驱动内核代码中缺失 163 频点的定义，配置 161 会触发驱动内部数组越界（UBSAN 报错），直接导致驱动崩溃瘫痪；
- **防护机制**：系统在 `cluster_config.sh` 和 `run_fl_demo.sh` 中对信道 161 实施了**硬性黑名单阻断**，一旦配置立即报错退出。请一律使用 149、153、157、165 等合法信道。

### 2. 为什么近距测试发射功率建议 12 dBm？
- **原因**：开发板在实验台面上距离较近（通常小于 2 米）。若功率设得过大（如 20 dBm），发射信号过强会导致接收端低噪声放大器（LNA）进入深度非线性饱和失真，反而导致空中丢包率剧增；
- **建议**：近距测试推荐维持在 10 ~ 13 dBm 之间。

### 3. 如果现场某台 Client 板子临时掉线怎么办？
- **机制**：`run_fl_demo.sh` 在启动前会自动进行存活探测。只要在线客户端在 1 台及以上，脚本会自动打印黄色警告并跳过掉线节点，动态收敛为当前在线节点集合继续演示，绝不会因为单板物理接触不良而导致整个汇报中断。

### 4. 网卡 monitor 模式被系统重置？
- **原因**：Ubuntu 的 NetworkManager 会在后台扫描无线网络并尝试将网卡设回 managed 模式；
- **排查**：检查 `/etc/NetworkManager/conf.d/wfb-unmanaged.conf` 是否存在且内容为：
  ```ini
  [keyfile]
  unmanaged-devices=interface-name:wlx*
  ```
  `deploy_node.sh` 会自动写入此配置并重载服务。

### 5. 快速检查全套命令速查表

| 操作阶段 | 命令 |
| :--- | :--- |
| **Windows 传代码** | `tar --exclude=".git" -czf - . \| ssh ubuntu@<IP> "mkdir -p ~/projects/wfb-ng-fl && tar -xzf - -C ~/projects/wfb-ng-fl"` |
| **Windows 传UFTP** | `scp ..\uftp_src-5.0.3.zip ubuntu@<IP>:~/projects/` |
| **修改集群配置** | `nano cluster_nodes.conf` |
| **校验集群配置** | `./scripts/cluster_config.sh cluster_nodes.conf` |
| **Server 本机装配** | `sudo ./scripts/deploy_node.sh` |
| **全集群免密互信** | `./scripts/setup_cluster_auth.sh` |
| **批量部署预检** | `./scripts/deploy_cluster.sh --check-only` |
| **全集群并行部署** | `./scripts/deploy_cluster.sh` |
| **演示全流程演练** | `bash tests/real_hardware/run_fl_demo.sh all --dry-run` |
| **现场全流程演示** | `bash tests/real_hardware/run_fl_demo.sh all` |
| **单步下行组播演示** | `bash tests/real_hardware/run_fl_demo.sh downlink` |
| **单步上行受控演示** | `bash tests/real_hardware/run_fl_demo.sh uplink` |
| **演示环境清理** | `bash tests/real_hardware/run_fl_demo.sh clean` |
