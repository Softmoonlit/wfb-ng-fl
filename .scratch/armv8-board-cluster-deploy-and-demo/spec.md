Status: ready-for-agent

# ARMv8 多板载集群环境自动化部署与上下行大文件传输演示系统

## Problem Statement

操作者需要在多台 ARMv8 架构嵌入式开发板（1 台作为 Server，5~7 台作为 Client）上部署 WFB-FL（Wi-Fi Broadcast 联邦学习空口底座）环境，并向导师现场演示一次下行大文件广播传输和一次上行受控大文件回传。

在当前的开发与部署现状中，操作者面临以下严重障碍：
1. **环境部署繁琐易错**：板载 Linux 系统缺乏自动化安装链路，操作者需手动处理内核头文件、DKMS 驱动编译安装、UFTP 源码编译（包含 OpenSSL 依赖）、NetworkManager 网卡接管排除、rfkill 射频解锁、内核网络队列调优（sysctl）以及底座 C++ 编译安装。手动操作极易因遗漏依赖、命令反向执行（如误执行卸载脚本）或驱动配置不当而导致部署失败。
2. **非交互 SSH 与 Sudo 权限阻塞**：操作者的电脑仅通过以太网局域网 SSH 连接 Server 板子，再由 Server 通过 SSH 远程调度各 Client 板子。若各板子未预先配置 NOPASSWD 免密 sudo 与公钥免密互信，远程执行特权命令（如切换网卡模式、安装系统文件）时会因无 TTY 终端导致 sudo 报密码错误而卡死。
3. **缺乏面向现场演示的轻量级上下行展示工具**：现存的测试脚本多为面向 CI 严格回归的验收套件（包含漫长的 smoke 探针周期、严格的代码工作区洁净度断言与生命周期审计），不适合向导师汇报演示；且缺乏直观展示“UFTP 组播共享下行广播”与“令牌轮询受控上行传输”核心机制的独立、分步演示命令。
4. **指标不透明**：演示过程中若无法在终端实时、详细地展示传输耗时、吞吐速率、空口收包/丢包率、FEC 纠错恢复率、TUN 队列反压流控状态以及 TCP 重传次数，无法有效体现课题对于无线链路控制的核心研究价值。
5. **硬件与环境动态性**：板子局域网 IP 由路由器 DHCP 动态分配，且现场可能临时出现个别板子掉线；同时，无线环境受现场 5GHz 频段干扰影响，需灵活调整信道、频宽（HT40+）、发射功率与调制编码策略（MCS），并避开已知存在驱动缺陷的信道。

## Solution

1. **单机环境全自动一键部署系统 (Single-Node Deployer)**：
   提供专为 ARMv8 嵌入式 Linux 设计的单机部署工具。全自动检测并安装工具链依赖库（`build-essential`, `libsodium-dev`, `libpcap-dev`, `libssl-dev`, `iw`, `rfkill` 等）；自动解压并编译安装 UFTP 源码生成 `uftp` 与 `uftpd` 原生二进制；自动拉取并使用 DKMS 安装 `rtl8812au` 无线网卡驱动并加载；自动向 NetworkManager 写入非托管网卡规则并解锁 rfkill；优化系统内核 socket 与 datagram 队列参数；自动写入 sudo 免密规则消除非交互 SSH 密码卡死；全自动编译并安装底座核心二进制与 Python 运行时。

2. **集群集中编排与免密认证纳管 (Cluster Orchestrator & Auth Setup)**：
   在 Server 板端建立集群全局配置文件，集中管理无线射频参数（信道、发射功率、上下行 MCS、UFTP 发送速率）与所有 Client 节点的局域网 IP 及登录用户。提供一键 SSH 互信与 sudo 权限分发工具，在 Server 端输入一次凭据即可完成全集群免密纳管；并提供集群一键刷机/部署工具，并行推动所有 Client 完成环境装配。

3. **双平面物理隔离传输架构 (Dual-Plane Network Isolation)**：
   - **带外管理平面**：电脑通过以太网/路由器局域网连接 Server，Server 通过局域网连接各 Client，仅用于命令下发、节点存活感知与遥测数据采集；
   - **空口数据平面**：各板子的 USB RTL8812AU 网卡进入 Monitor 模式并绑定同一射频信道，通过 WFB-FL 构建 TUN 虚拟网卡（`10.80.0.x`），所有大文件数据 100% 走无线链路。

4. **分步式现场演示总控工具 (Modular FL Transmission Demo)**：
   提供面向现场演示的测试总控程序，支持三种运行模式：
   - **单独下行广播模式 (`downlink`)**：Server 指定发送源文件（支持自定义文件路径，未指定时自动现场生成 40MB 确定性测试文件），通过 UFTP 组播一次性下发至所有在线 Client；各 Client 自动落盘到专用接收目录并向终端打印接收文件的绝对路径；完成后在终端输出【下行传输指标看板】；
   - **单独上行受控模式 (`uplink`)**：各 Client 自动复用刚才下行接收到的文件作为 update，通过 WFB-FL 受控 HTTP PUT 上传回 Server；Server 自动按节点 ID 归档保存，完成后在终端输出【上行传输指标看板】；
   - **一键全流程模式 (`all`)**：串联下行与上行两个阶段，并在每个阶段结束时分别独立渲染对应的指标看板。

5. **多维链路质量与传输事实终端看板 (Rich Terminal Telemetry Dashboard)**：
   下行和上行完成时，终端实时渲染详尽的指标看板：
   - 下行面板：下发源路径、文件大小、源 SHA-256、射频信道、功率、MCS、设定速率、总耗时、有效吞吐速率、各 Client 接收路径、各 Client 哈希比对与无损校验结果；
   - 上行面板：回传文件大小、上行 MCS、令牌时隙与保护间隔、总耗时、集群综合吞吐；分源列出各 Client 的传输耗时、实际速率、TCP 重传次数、空口物理收包数、原始丢包数与丢包率、FEC 恢复包数与恢复率、净丢包率、TUN 队列 PAUSE/RESUME 反压触发次数，以及最终接收文件 SHA-256 完整性校验结果。

6. **节点在线动态容错与射频参数安全防护 (Node Fault-Tolerance & Safe Radio Bounds)**：
   测试启动前自动通过管理网探测各 Client 连通性，若配置的 5~7 台中有节点掉线，打印告警并自动将在线节点动态收敛为当前参与方，继续执行下行广播与轮流上行，防止演示中断；全面支持操作者自定义信道与调制参数，硬屏蔽已知存在驱动越界崩溃缺陷的 Channel 161，将近距台面发射功率安全约束在推荐区间（10~13 dBm），并根据选定的下行 MCS 动态提供匹配的 UFTP 安全发送速率推荐值，防止击穿内核 TUN 缓冲。

## User Stories

1. 作为 现场操作者，我希望在 ARMv8 板子上执行一个部署脚本即可自动安装全部系统依赖，以便免去手动逐一查找和安装编译库的麻烦。
2. 作为 现场操作者，我希望部署脚本能自动解压、编译并安装 UFTP 源码包，以便同时在系统路径生成可用的 `uftp` 发送器和 `uftpd` 接收守护进程。
3. 作为 现场操作者，我希望部署脚本自动检测内核头文件并正确调用 DKMS 安装 RTL8812AU 网卡驱动，以便驱动顺利加载且网卡进入可用状态。
4. 作为 现场操作者，我希望部署脚本自动配置 NetworkManager 忽略 `wlx*` 前缀的无线网卡，以便系统网络管理器不会私自重置或干扰网卡的 Monitor 模式。
5. 作为 现场操作者，我希望部署脚本自动执行 rfkill 解锁，以便无线网卡射频不会处于软件关闭状态。
6. 作为 现场操作者，我希望部署脚本自动将内核 socket 缓冲区与 unix domain datagram 队列长度配置生效，以便高吞吐无线注入时内核不会静默丢包。
7. 作为 现场操作者，我希望部署脚本能自动为当前运行用户配置免密 sudo 权限，以便后续通过 SSH 执行远程命令时不会因无 TTY 输入密码而卡死。
8. 作为 现场操作者，我希望部署脚本自动编译 `wfb_v6_uplink` 并执行项目底座安装，以便链路层核心二进制与 Python 模块正确就位。
9. 作为 现场操作者，我希望能够在 Server 板端的一个集中配置文件中配置所有 Client 的局域网 IP 与用户名，以便应对路由器 DHCP 动态分配的地址。
10. 作为 现场操作者，我希望在集中配置文件中能够自定义无线信道、频宽、发射功率、上下行 MCS 以及 UFTP 速率，以便根据现场不同距离和电磁干扰环境进行测试与对比。
11. 作为 现场操作者，我希望系统提供一键集群认证工具，只输入一次密码即可将 Server 的 SSH 公钥分发到全部 Client，以便建立全自动化远程调用通道。
12. 作为 现场操作者，我希望系统提供集群批量部署工具，在 Server 上执行一次即可远程并行驱动所有 Client 完成环境初始化，以便无需逐台插键盘显示器配置。
13. 作为 现场操作者，我希望我的笔记本电脑只需通过局域网 SSH 连接 Server，即可完成整个集群的演示调度，以便我的电脑无需直连任何 Client 板子。
14. 作为 现场操作者，我希望集群通信严格将“带外管理以太网”与“空口无线数据网”隔离，以便向导师明确证明大文件完全通过无线空口传输。
15. 作为 演示汇报者，我希望演示脚本支持单独执行下行大文件传输，以便在向导师介绍下行组播广播机制时能够单步聚焦展示。
16. 作为 演示汇报者，我希望演示脚本支持单独执行上行受控大文件传输，以便在向导师介绍令牌轮转和流控反压机制时能够单步聚焦展示。
17. 作为 演示汇报者，我希望演示脚本支持一键跑完全流程（下行后接上行），并在下行完成后和上行完成后分别展示独立的指标看板，以便现场节奏自然清晰。
18. 作为 演示汇报者，我希望在执行下行传输时能够自由传入指定的大文件路径，以便向导师演示任意真实模型或测试资产的传输。
19. 作为 演示汇报者，我希望在未指定文件时脚本能自动现场生成 40MB 的标准大文件，以便现场手头缺少合适大文件时也能一键顺利开跑。
20. 作为 现场操作者，我希望各 Client 接收到下行文件后，终端能明确打印出保存的绝对目录路径，以便我能清楚知道到哪里去查看文件。
21. 作为 现场操作者，我希望执行上行传输时各 Client 自动将刚才下行接收到的文件回传给 Server，以便无需再向每个 Client 单独分发上行文件。
22. 作为 演示汇报者，我希望在下行看板中看到文件大小、广播耗时、有效吞吐速率以及每个 Client 的接收完整性与 SHA-256 哈希比对结果，以便证明组播广播的无损性。
23. 作为 演示汇报者，我希望在上行看板中看到集群综合吞吐、各 Client 单独的上传耗时和传输速率，以便体现多客户端并发回传性能。
24. 作为 演示汇报者，我希望在上行看板中看到各 Client 的物理收包数、原始空中丢包数与丢包率，以便向导师展示真实无线信道的不确定性。
25. 作为 演示汇报者，我希望在上行看板中看到各 Client 对应的 FEC 纠错恢复包数与恢复率，以便向导师展示底座 FEC 纠错算法化解空中丢包的能力。
26. 作为 演示汇报者，我希望在上行看板中看到各 Client 队列触发 PAUSE 与 RESUME 的次数，以便向导师证明底座反压机制有效防止了缓冲区溢出。
27. 作为 演示汇报者，我希望在上行看板中看到 TCP 实际重传次数，以便向导师证明在受控时隙保护下传输层连接极为稳健。
28. 作为 演示汇报者，我希望在上行看板中看到 Server 最终接收文件与原始文件的 SHA-256 比对结果，以便给出闭环数据 100% 正确的铁证。
29. 作为 现场操作者，我希望在演示启动前脚本能自动检查 Client 在线情况，如果配置了 7 台但有 1~2 台掉线，能自动告警并跳过掉线节点，以便演示不会因单板异常而中断。
30. 作为 现场操作者，我希望配置文件中能够明确限制信道 161 处于不可配置状态，以便我绝不会因误设具有驱动越界崩溃缺陷的信道而导致网卡瘫痪。
31. 作为 现场操作者，我希望近距台面测试时发射功率默认设置在推荐的 12 dBm，以便避免网卡近距离功率过大导致射频接收端 LNA 饱和失真。

## Implementation Decisions

- **集群全局配置契约 (Cluster Configuration Contract)**：
  - 在项目根目录提供统一配置文件（`cluster_nodes.conf`），采用键值对与列表相结合的声明式结构：
    - `WIRELESS_CHANNEL`：默认 `157`（支持 `149`、`153`、`157`、`165` 等合法信道，硬校验拒绝 `161`）；
    - `WIRELESS_CHANNEL_WIDTH`：默认 `HT40+`；
    - `WIRELESS_TXPOWER_DBM`：默认 `12`（合法范围 10~20 dBm）；
    - `DOWNLINK_MCS`：默认 `3`（范围 3~6）；
    - `UPLINK_MCS`：默认 `6`（范围 3~6）；
    - `UFTP_RATE_KBPS`：默认 `15000`（根据 `DOWNLINK_MCS` 提供建议安全速率区间）；
    - `SERVER_NODE_ID`：固定为 `255`，TUN 地址为 `10.80.0.1/24`；
    - `CLIENTS`：定义 Client 列表，每行声明 `role_name host_ip ssh_user node_id tun_ip`，例如：
      ```text
      client1 192.168.1.101 ubuntu 1 10.80.0.11
      client2 192.168.1.102 ubuntu 2 10.80.0.12
      ...
      ```

- **单机环境安装器契约 (Single-Node Deployer Contract)**：
  - 提供单机部署脚本 `scripts/deploy_node.sh`，具备幂等性（重复执行安全）：
    - 阶段 1（APT 基础包）：自动检查并安装 `build-essential`, `pkg-config`, `python3`, `python3-pip`, `libssl-dev`, `libsodium-dev`, `libpcap-dev`, `iw`, `rfkill`, `unzip`, `git`, `bc`, `dkms`, `net-tools`, `iproute2`；
    - 阶段 2（内核头文件与驱动）：检测 `linux-headers-$(uname -r)`，若未安装则自动通过 apt 安装；检查本地是否有 `rtl8812au` 源码，若无则克隆官方仓库；调用 `sudo ./dkms-install.sh` 编译并安装模块，调用 `sudo modprobe 8812au` 加载；
    - 阶段 3（UFTP 编译安装）：检查本地是否有 `../uftp_src-5.0.3.zip` 或解压目录，解压后执行 `make` 编译（链接 OpenSSL `-lcrypto`），调用 `sudo make install` 将 `uftp` 和 `uftpd` 写入 `/usr/bin`；
    - 阶段 4（系统网络与权限优化）：
      - 写入 `/etc/NetworkManager/conf.d/wfb-unmanaged.conf` 排除 `wlx*` 接口并重启 NetworkManager；
      - 执行 `sudo rfkill unblock all`；
      - 将 `scripts/sysctl/98-wifibroadcast.conf` 安装至 `/etc/sysctl.d/` 并执行 `sysctl --system`；
      - 写入 `/etc/sudoers.d/99-wfb-nopasswd` 授予当前用户免密 sudo；
    - 阶段 5（底座编译与系统安装）：在项目根目录执行 `make build_v6` 产出 `wfb_v6_uplink`，执行 `sudo make install_v8` 完成系统安装。

- **集群认证与分发编排契约 (Cluster Orchestration Contract)**：
  - 提供 `scripts/setup_cluster_auth.sh`：
    - 读取 `cluster_nodes.conf`，检查 Server 本机是否有 `~/.ssh/id_rsa.pub`，若无则自动生成；
    - 遍历所有配置的 Client，提示用户输入一次 SSH 登录密码，自动通过 `ssh-copy-id` 拷贝公钥；
    - 通过 SSH 远程在 Client 上配置 sudo 免密（写入 `/etc/sudoers.d/99-wfb-nopasswd`）；
    - 验证 `ssh -o BatchMode=yes <client> "sudo -n true"` 连通无阻后确认纳管成功。
  - 提供 `scripts/deploy_cluster.sh`：
    - 读取 `cluster_nodes.conf`，并行的在所有已纳管的 Client 上执行仓库代码同步；
    - 远程触发各 Client 运行 `sudo ./scripts/deploy_node.sh` 并汇总输出结果。

- **演示测试总控契约 (Demo Runner Contract)**：
  - 提供演示脚本 `tests/real_hardware/run_fl_demo.sh`：
    - 支持命令：`all`、`downlink`、`uplink`；
    - 支持选项：`--file <path>`（指定发送文件）、`--config <path>`（指定集群配置文件，默认 `./cluster_nodes.conf`）；
    - **预检与容错**：探测配置中的 Client 局域网连通性，自动过滤掉离线节点，仅对当前在线节点拉起测试；检查各节点是否有唯一的 `wlx*` 网卡，自动 down/up 切换为 monitor 模式并锁定到指定信道和频宽；
    - **下行执行逻辑**：
      - 校验源文件存在，计算 SHA-256 并记录初始时间；
      - 各 Client 远端拉起 `uftpd` 监听指定组播组 `239.80.41.1`，落盘目录为 `/var/lib/wfb-ng/fl_demo/received/`；
      - Server 启动底座组播通道并调用 `uftp` 发送大文件；
      - 等待传输结束，远程计算各 Client 接收文件的 SHA-256 并比对，统计下行耗时与吞吐速率；
      - 终端渲染【下行传输指标看板】；
    - **上行执行逻辑**：
      - 确认各 Client 端 `/var/lib/wfb-ng/fl_demo/received/` 存在有效模型文件；
      - Server 启动受控 HTTP PUT 接收服务与 `wfb_v6_uplink` 调度服务（开启令牌分配与反压日志收集）；
      - 各 Client 并发/轮流通过 WFB TUN 发起 HTTP PUT 回传文件；
      - Server 保存文件至 `/var/lib/wfb-ng/fl_demo/server_received/client_<id>_update.bin`；
      - 解析底座带内遥测日志（`\tPKT\t`, `\tTOKEN_AUTH\t`, `TUN_PAUSE`, `TUN_RESUME`）与 Linux TCP 统计信息（`retransmits`）；
      - 比对最终接收文件与原始文件的 SHA-256；
      - 终端渲染【上行传输指标看板】。

- **真实硬件多机基线的继承与改造契约 (Inheritance and Adaptation from Real-Hardware Baseline)**：
  - 核心定位：本套部署与演示系统以项目已有的真实硬件多机运行基线脚本（`tests/real_hardware/issue41_fl_runtime_loop.sh` 及配套 `issue41_gate.py`）为硬核技术底座，全面继承其在 Stage 1 与 Stage 2 真实多机环境中验证成熟的空口传输机制，同时坚决剥离其漫长、严苛的 CI 门禁，实现“高可靠通信内核 + 演示友好型交互”。
  - **必须深度继承复用的成熟机制**：
    1. **无线网卡探测与状态切换**：严格复用 `find_wlx` 逻辑（通过 `iw dev` 动态发现 `wlx*` 真实 USB 网卡，禁止硬编码网卡名），并遵循 `down -> set type monitor -> up -> set channel` 状态转移契约；
    2. **链路参数与底座契约**：严格继承底层 `wfb_v6_uplink` 所需参数格式（`--link-id`, `--uplink-stream`, `--downlink-stream`, `--known-clients`, `--client-target`, 调度时隙 `120ms/10ms` 等）；
    3. **UFTP 组播参数契约**：严格继承组播组（`239.80.41.1`）、服务端口（`1044`）、UID 动态映射与限速参数契约；
    4. **遥测数据解析器**：深度复用基线配套遥测解析逻辑（`issue41_gate.py`），从底座运行日志提取分源物理收包数、空中丢包数、FEC 恢复包数、TUN 队列 PAUSE/RESUME 次数，以及从系统网络读取 TCP 重传次数；
    5. **残留进程安全清理**：严格继承环境初始化与退出时的进程/资源清理顺序（`wfb_v6_uplink`、`uftp`、`uftpd` 与旧 TUN 设备）。
  - **明确剔除或改造的非必要环节（不照搬）**：
    1. **剔除 Git 状态死锁拦截**：原基线脚本强制要求全集群处于指定 git 分支、指定 commit 且工作区绝对干净（任何临时文件或改动均阻断）。演示系统彻底废除该项检查，允许现场灵活调整配置；
    2. **剔除 3 周期 Smoke-Gate 探针**：原基线脚本正式运行前强制执行 3 周期双向 gate 探针验收（耗时数分钟），演示系统直接进入演示主题；
    3. **剔除 systemd 停服重启审计**：原基线脚本在跑完后执行冗长的 stop/restart/no-overlap 生命周期审计，演示系统将其剔除；
    4. **改造固定模板为自定义文件**：原基线脚本硬编码校验 4MB/40MB 预置输入文件，演示系统全面放开支持用户传入任意 `--file` 路径或自动现场生成。

## Testing Decisions

- **外部行为黑盒验证 (External Behavior Testing)**：
  - 测试聚焦于整机命令执行状态、退出码、网络接口状态（`iw dev` monitor 与信道）、文件落盘存在性、文件字节大小精准匹配以及 SHA-256 哈希绝对一致性，不插桩内部逻辑；
  - 验证命令参数解析与容错：测试当配置文件中包含不可达 IP 时，演示脚本能否正确输出告警并优雅降级为在线节点集合执行；
  - 验证信道安全检查：测试当在配置文件中故意指定信道 `161` 时，脚本能否严格 fail-closed 拦截并给出明确禁止原因。
- **现有测试切面继承 (Prior Art Alignment)**：
  - 复用 `tests/real_hardware/issue41_gate.py` 中的遥测日志解析与分源丢包/FEC/TCP 重传统计计算逻辑；
  - 复用 `tests/real_hardware/v6_manual_uplink_demo.sh` 中的无线网卡 monitor 设置与信道锁定经验。

## Out of Scope

1. **真实模型训练与梯度聚合计算**：本次演示定位为传输层与运行时文件分发验证，不加载 PyTorch/TensorFlow 执行真实神经网络反向传播与浮点权重聚合，大文件内容作为不透明二进制载荷传输。
2. **Web GUI 界面呈现**：本次交付以终端 CLI 与彩色指标看板为主，不构建前台 Web 网页控制面板。
3. **脱离局域网的无 SSH 自动带内心跳组网**：虽然底座设计具备无网络协同能力，但本次明确在有路由器以太网局域网环境下，由 Server 通过 SSH 远程统一编排各 Client。

## Further Notes

- RTL8812AU 网卡在发射大流量时峰值功耗较大，开发板的 USB 供电必须充足（建议使用独立供电 HUB 或板端稳定 5V/3A 供电），防止因欠压触发 USB 网卡热重置。
- 演示现场若距离较近（如置于同一实验台面，距离 < 2 米），严禁将发射功率调至 15 dBm 以上，保持默认推荐的 `12 dBm` 可防止强信号导致接收端 LNA 放大器深度饱和失真。
