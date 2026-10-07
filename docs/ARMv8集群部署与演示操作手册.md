# ARMv8 多板载集群环境部署与现场演示操作手册

本文档为面向操作者和现场汇报者的全流程实操指南，涵盖：**从电脑端推送代码到 Server 开发板、Server 本机一键环境装配、集群免密互信分发、多 Client 节点批量并行部署，以及面向导师现场汇报的大文件组播广播与受控上行回传演示**。

---

## 目录

1. [系统架构与网络拓扑](#一系统架构与网络拓扑)
2. [环境准备与依赖要求](#二环境准备与依赖要求)
3. [阶段零：从 WSL / 本地 Linux 向 Server 开发板传输代码](#三阶段零从-wsl--本地-linux-向-server-开发板传输代码)
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
[操作者电脑 (WSL / Linux)]
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

- **带外管理平面**：电脑 (WSL) 通过 SSH 连接 Server 开发板，Server 通过 SSH 调度各 Client 板子，仅用于命令下发、节点存活感知与遥测数据采集；
- **空口数据平面**：所有待测大文件（如模型参数包）100% 通过 WFB-FL 构建的 TUN 虚拟网卡与 802.11 原始注入/监听链路传输，严禁走以太网。

---

## 二、环境准备与依赖要求

### 1. 硬件准备
- **开发板**：1 台作为 Server，3~7 台作为 Client（推荐 ARMv8 架构，如 RK3588/RK3568/树莓派 4B 等，系统为 Ubuntu 20.04/22.04 LTS aarch64）；
- **无线网卡**：每台开发板外接 1 个 RTL8812AU 双频 USB 无线网卡。接口名由 Linux udev 决定，统一视为 `wl*` 无线接口，**不得假设一定是 `wlx*`**；演示脚本按 RTL8812AU 内核驱动绑定关系识别空口网卡，以避免误选手机热点管理网卡；
- **局域网交换机/路由器**：将所有开发板以太网口与操作者电脑连入同一局域网（确保彼此网络可达）。

### 2. 本地文件准备
在操作者电脑（WSL 终端，如 `~/projects/`）上，确保目录结构如下：
```text
projects/
├── uftp_src-5.0.3.zip     # UFTP 离线源码包 (约 318 KB)
└── wfb-ng-fl/             # 项目代码仓库根目录
```

---

## 三、阶段零：从 WSL / 本地 Linux 向 Server 开发板传输代码

在切换到 WSL（Windows Subsystem for Linux）或本地 Linux 环境后，代码传输方式升级为业界标准的 **`rsync` 增量同步**（与集群内部 `deploy_cluster.sh` 的分发机制完全统一）：
- **秒级增量传输**：只传输产生变动的文件差异，修改代码后数毫秒至数百毫秒即可同步完毕，无需每次重复打包解压；
- **镜像对齐与自动清理 (`--delete`)**：本地删除或重命名文件后，远端同步删除废弃旧文件，彻底杜绝编译产物残留引起的隐蔽 Bug；
- **平滑向下兼容**：若新刷机的 Server 开发板尚未安装 `rsync`，一键脚本会自动无缝降级为 `tar` 管道流传输，确保 100% 可用。

项目已内置了 WSL / Linux 专用的**一键同步脚本 (`scripts/sync_to_server.sh`)**（同时保留兼容 Windows PowerShell 的 `scripts/sync_to_server.ps1`）。该脚本在后台全自动完成：
1. 自动定位上级目录中的 `../uftp_src-5.0.3.zip` 离线源码包并同步至 Server 端父级目录；
2. 优先采用 `rsync -avz --delete` 极速增量同步项目代码，**自动排除庞大的 `.git` 历史、编译二进制与日志**；
3. 全面支持 IP 地址（如 `192.168.1.100`）、`~/.ssh/config` 中定义的 SSH 别名（如 `vm0`），并能自动读取 `cluster_nodes.conf` 中配置的 `SERVER_HOST` 与 `SERVER_USER`。

### 1. 一键脚本使用方法 (推荐)

在 WSL 终端中进入 `wfb-ng-fl` 目录执行：

```bash
# 场景 A: 自动读取 cluster_nodes.conf 中的 SERVER_HOST 与 SERVER_USER (免输入参数)
./scripts/sync_to_server.sh

# 场景 B: 显式指定开发板局域网 IP 与登录用户
./scripts/sync_to_server.sh -s 192.168.1.100 -u ubuntu

# 场景 C: 使用 SSH 配置别名 (如 vm0，自动应用别名内绑定的用户名与私钥)
./scripts/sync_to_server.sh -s vm0

# 场景 D: 预先演练 (仅查看即将执行的操作与命令，不产生实际网络传输)
./scripts/sync_to_server.sh -s vm0 --dry-run
```

> **进阶选项**：
> - 强制使用 tar 管道模式：`./scripts/sync_to_server.sh -s vm0 -m tar`
> - rsync 不删除远端多余文件：`./scripts/sync_to_server.sh -s vm0 --no-delete`

### 2. 手动执行方式 (脚本底层原理说明)

若希望了解底层原理或在纯命令行手动操作，推荐使用 **rsync** 标准命令：

```bash
# 假定 Server 开发板局域网 IP 为 192.168.1.100，用户名为 ubuntu
SERVER_IP="192.168.1.100"
SERVER_USER="ubuntu"

# 1. 确保 Server 开发板上存在 ~/projects 目录并推送 UFTP 源码包
ssh ${SERVER_USER}@${SERVER_IP} "mkdir -p ~/projects"
rsync -az ../uftp_src-5.0.3.zip ${SERVER_USER}@${SERVER_IP}:~/projects/

# 2. rsync 极速增量同步代码库 (排除 .git 与编译产物，远端镜像对齐)
rsync -avz --delete \
    --exclude=".git/" \
    --exclude="*.o" \
    --exclude="*.so" \
    --exclude="*.a" \
    --exclude="wfb_v6_uplink" \
    --exclude="__pycache__/" \
    --exclude="*.pyc" \
    --exclude="logs/" \
    ./ ${SERVER_USER}@${SERVER_IP}:~/projects/wfb-ng-fl/
```

*(备选方案：若远端板子为刚烧录的最小镜像且尚未安装 rsync，可使用 `tar` 管道打包传输)*：
```bash
tar --exclude=".git" --exclude="*.o" --exclude="*.so" --exclude="logs" -czf - . | ssh ${SERVER_USER}@${SERVER_IP} "mkdir -p ~/projects/wfb-ng-fl && tar -xzf - -C ~/projects/wfb-ng-fl"
```

传输完成后，在 WSL 终端中直接登录 Server 开发板：
```bash
ssh ubuntu@192.168.1.100
cd ~/projects/wfb-ng-fl
```

> **注（Windows PowerShell 原生用户备选）**：若在纯 Windows 环境下未使用 WSL，也可在 PowerShell 中执行 `powershell -ExecutionPolicy Bypass -File .\scripts\sync_to_server.ps1 -Server 192.168.1.100`。

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

# 物理发射功率: 推荐 14 dBm (台面近距实测最佳值: 兼顾下行抗 LNA 削顶失真与上行边缘节点 SNR 充裕度)
WIRELESS_TXPOWER_DBM=14

# 调制编码策略 (MCS): 下行组播推荐 3 (15000 Kbps 注入)，上行单播调度推荐 4 (16-QAM 3/4 稳健高吞吐)
DOWNLINK_MCS=3
UPLINK_MCS=4

# 下行 UFTP 组播发送速率: MCS 3 对应安全推荐速率为 15000 Kbps (15 Mbps)
UFTP_RATE_KBPS=15000

# ------------------------------------------------------------------------------
# 2. 服务端配置
# ------------------------------------------------------------------------------
SERVER_NODE_ID=255
SERVER_TUN_IP=10.80.0.1/24

# ------------------------------------------------------------------------------
# 3. 客户端拓扑列表 (根据路由器实际分配的以太网 IP 填写 3~7 台 Client)
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
2. **RTL8812AU 网卡驱动构建**：自动检测/安装当前内核头文件，调用已同步的 `rtl8812au/dkms-install.sh` 执行 DKMS 编译安装，并加载实际模块 `88XXau_wfb`；
3. **UFTP 离线编译与安装**：自动读取 `../uftp_src-5.0.3.zip`，就地针对 ARMv8 编译并把原生二进制 `uftp` 和 `uftpd` 安装至 `/usr/bin/`（**无需外网下载**）；
4. **系统网络与权限调优**：
   - 自动向 NetworkManager 写入规则，按 RTL8812AU 驱动标识永久忽略对应的 `wl*` 空口网卡，防止系统重置 Monitor 模式；手机热点管理网卡不纳入该规则；
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
2. **空口网卡准备**：在 Server 和所有在线 Client 上按 RTL8812AU 驱动绑定关系探测唯一空口网卡。接口名可能是任意 `wl*` 前缀（例如 `wlP...` 或 `wlx...`），脚本不会把手机热点管理网卡误当作空口网卡，然后切换为 monitor 模式并严格锁定到信道 157、频宽 HT40+ 和功率 12 dBm；
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
- **广播传输耗时与空口平均吞吐**：体现 1 次广播同时送达全部 3~7 台客户端的高效性（与单播相比，带宽消耗不随客户端数量增加而线性膨胀）；
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

### 2. 物理发射功率精细设定原则：为什么台面测试推荐 14 dBm？
- **机理背景**：
  - **过大功率危害（> 15 dBm）**：板卡密集摆放（距离 < 1.5 米）时，若发射功率过高（如 18~20 dBm），到达天线的信号高达 $-5 \sim -10\,\text{dBm}$，远超 RTL8812AU 接收端低噪声放大器（LNA）的 1dB 压缩点，引起严重非线性削顶失真，导致组播严重丢块、下行重传轮次激增（实测下行耗时会从 30 秒剧增至 250 秒以上）；
  - **过小功率危害（< 12 dBm）**：若功率过低，稍有空间衰减或天线角度偏差的节点（RSSI 跌至 $-68 \sim -70\,\text{dBm}$）在上行高阶解调时信噪比不足，引发上行丢包；
- **实测黄金基准**：台面近距测试建议锁定为 **`14 dBm`**（驱动底层写入负值 `-14.00 dBm`），既彻底避开 LNA 饱和失真（下行稳定 30 秒内完成），又保证全网边缘节点拥有充裕的解调 SNR。

### 3. 上行调制策略选择：为什么推荐 MCS 4 而不是 MCS 5/6？
- **机理分析**：
  - **64-QAM (MCS 5/6) 的局限**：MCS 5/6 采用 64 阶高阶调制，对信噪比（SNR）门限要求极高（要求 $> 20\,\text{dB}$）。一旦某台客户端天线稍有遮挡或微弱多径衰落（RSSI 处于 $-65 \sim -70\,\text{dBm}$ 边缘），解调误码率将指数级恶化，空中残余丢包率达到 10% 以上；
  - **TCP 拥塞窗口崩溃效应**：上行大文件传输基于 TCP（HTTP PUT）。一旦底层物理丢包超过 FEC 纠错极限，Linux TCP 协议栈会触发超时重传（RTO）并将拥塞窗口（CWND）重置为 1 MSS，随后进入指数退避（1s, 2s, 4s...）。此时即使服务端连续派发 Grant，客户端协议栈因退避静默也无数据写入 TUN，导致吞吐瞬间跌零；
- **推荐策略**：上行统一锁定为 **`MCS 4 (16-QAM 3/4)`**！
  - 16-QAM 比 64-QAM 的抗噪容限高出整整 **`6 ~ 8 dB`**，在复杂电磁与弱信号环境下鲁棒性极强；
  - 配合 **FEC 8/14** 强力自愈，全网实测空中丢包率仅 **2.67%**，FEC 自愈率高达 **83.84%**，TCP 重传仅个位数；
  - 物理速率高达 81 Mbps，全集群 5 节点并发 200MB 回传耗时收敛在 **220 秒左右**，完全在超时安全窗口内。

### 4. 前向纠错（FEC）：全网固化 8/14 (75% 冗余带内自愈)
- **参数规格**：底座核心统一使用 `FEC_K=8`、`FEC_N=14`（每 8 个数据包附加 6 个纠错冗余包）；
- **实战价值**：在 10.6 万个空口收发包实测中，FEC 8/14 在物理层成功自动修复了 **15,182 个空中丢包**！将物理层突发丢包在被应用层感知前透明愈合，杜绝了 TCP 退避与 UFTP NAK 风暴。

### 5. Jetson Orin Nano USB 控制器故障避坑 (LPM U1/U2 异常与总线降级)
- **典型故障特征**：
  某节点此前测试正常，但突然出现**空口完全收不到任何包**（收包统计全为 0）或**大流量写入死锁**。
- **内核排查定位**：
  在问题节点执行 `sudo dmesg -T`，若发现以下类似记录：
  ```text
  usb 2-1.2: Disable of device-initiated U1/U2 failed.
  usb 2-1.2: reset SuperSpeed USB device number 4 using tegra-xusb
  usb 2-1.2: device not accepting address 4, error -71
  usb 1-2.2: new high-speed USB device number 7 using tegra-xusb
  rtl88xxau_wfb 1-2.2:1.0 wlxfc...: renamed from wlan0
  ```
- **故障根因**：
  Jetson 平台的 `tegra-xusb` 控制器在 USB 3.0 链路电源管理（LPM U1/U2）通信失败或供电轻微抖动时，会复位设备并报错 `error -71`，甚至将 5000M SuperSpeed 网卡**强行静默降级枚举至 480M USB 2.0 甚至挂死**。
- **排查与处置手段**：
  1. 执行 `lsusb -t` 确认网卡是否位于 `5000M` 树下；
  2. 严禁使用松动或劣质 USB 延长线/扩展坞，必须直插开发板原生 USB 3.0 接口；
  3. 若出现 `error -71`，必须**彻底物理拔出网卡重新插入**，以触发芯片冷复位与干净枚举；若依旧频繁报错，及时更换备用网卡。

### 6. 多节点 UFTP 临时目录 (`_restart` 文件) 污染
- **典型故障特征**：下行 UFTP 广播在开始阶段即报错（返回码 7、8 或 9），日志提示 `Registration unconfirmed` 或 `Lost connection`；
- **故障根因**：前序测试异常中止后，各 Client 的 `/var/lib/wfb-ng/fl_demo/tmp/` 遗留了历史传输的 `_group_*_restart` 断点续传文件。UFTP 客户端启动时误读残留状态导致会话错乱；
- **解决规范**：演示脚本已固化在拉起 `uftpd` 前自动执行 `sudo rm -rf '$WORK_DIR/tmp'/*`，确保各节点每次传输均处于绝对纯净的会话上下文。

### 7. HTTP Receiver 端口 8080 残留排查
- **典型故障特征**：上行步骤启动时报错 `Server HTTP PUT 接收端启动超时`，日志显示 `OSError: [Errno 98] Address already in use`；
- **故障根因**：上一轮演示异常退出后，后端的 `python3 fl_demo_metrics.py http-receiver` 成为孤儿进程仍持有 8080 端口；
- **处置方案**：`run_fl_demo.sh` 清理流程已增强写入 `sudo pkill -f 'fl_demo_metrics.py http-receiver'`。手动排查命令为：
  ```bash
  sudo ss -tulpn | grep 8080
  sudo pkill -f 'fl_demo_metrics.py http-receiver'
  ```

### 8. 多节点并发上行单次 I/O 超时（`--timeout 260` 设定必要性）
- **机理分析**：
  全集群多节点（如 5~7 台）并发向 Server 发送 40MB（全网总数据量达 200~280 MB）。由于底层采用 TDMA 集中式时隙轮转，各节点根据链路状况错峰完成（实测最快的节点 90 秒完成，最慢的节点排队至 220 秒完成）。若上行 HTTP 请求超时设为默认的 120 秒，排在后面的正常节点会被过早掐断导致 `TimeoutError`；
- **标准设定**：演示脚本统一为上行客户端注入 `--timeout 260`，为全集群平稳消化 200MB+ 大载荷提供充裕的调度裕量。

### 9. 重点监控命令与遥测指标一键解析
在演示执行完毕或中途排障时，可通过以下命令在 Server 端一键解析底座底层遥测日志，重点观察分源丢包率与自愈状况：

```bash
# 解析 Server 端 wfb.log 并输出结构化遥测 JSON
python3 tests/real_hardware/fl_demo_metrics.py parse-telemetry \
    --server-log /var/lib/wfb-ng/fl_demo/run/wfb.log \
    --output /tmp/telemetry.json

# 查看关键指标
cat /tmp/telemetry.json | jq '{loss_rate, fec_recovery_rate, tcp_retransmits, loss_and_fec_by_node}'
```

- **正常验收基准值参考**：
  - `loss_rate`（全网空中丢包率）：应保持在 **`< 5%`**（实测约 2.67%）；
  - `fec_recovery_rate`（FEC 自愈恢复率）：应大于 **`> 75%`**（实测约 83.84%）；
  - `tcp_retransmits`（TCP 协议重传次数）：全网应为**个位数**（实测仅 9 次）；
  - 单个节点的 `packets_lost` 应被 `packets_fec_recovered` 大部分覆盖（如丢失 500 包，FEC 恢复 3000 包，确保应用层净丢包趋近于零）。

### 10. 如果现场某台 Client 板子临时掉线怎么办？
- **机制**：`run_fl_demo.sh` 在启动前会自动进行存活探测。只要在线客户端在 1 台及以上，脚本会自动打印黄色警告并跳过掉线节点，动态收敛为当前在线节点集合继续演示，绝不会因为单板物理接触不良而导致整个汇报中断。

### 11. 网卡 monitor 模式被系统重置？
- **原因**：Ubuntu 的 NetworkManager 会在后台扫描无线网络并尝试将网卡设回 managed 模式；
- **排查**：检查 `/etc/NetworkManager/conf.d/wfb-unmanaged.conf` 是否存在且内容为：
  ```ini
  [keyfile]
  unmanaged-devices=driver:rtl88xxau_wfb
  ```
  `deploy_node.sh` 会自动写入此配置并重载服务。

### 12. 快速检查全套命令速查表

| 操作阶段 | 命令 |
| :--- | :--- |
| **WSL 传代码与UFTP** | `./scripts/sync_to_server.sh -s <IP>`<br>或 `rsync -avz --delete --exclude=".git/" --exclude="*.o" ./ ubuntu@<IP>:~/projects/wfb-ng-fl/` |
| **WSL 传UFTP (手动)** | `scp ../uftp_src-5.0.3.zip ubuntu@<IP>:~/projects/` |
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
| **遥测丢包指标解析** | `python3 tests/real_hardware/fl_demo_metrics.py parse-telemetry --server-log /var/lib/wfb-ng/fl_demo/run/wfb.log --output /tmp/telemetry.json` |
| **演示环境清理** | `bash tests/real_hardware/run_fl_demo.sh clean` |
