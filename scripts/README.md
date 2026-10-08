# scripts 部署与系统集成入口

本目录维护当前联邦学习集群的部署、认证、代码同步、角色服务安装和无线网卡配置工具。现场操作从 [ARMv8 集群部署与演示操作手册](../docs/ARMv8集群部署与演示操作手册.md) 开始；真实硬件演示与验收按用途位于 [tests](../tests/README.md)。

## ARMv8 集群部署

集群节点和无线参数统一读取仓库根目录的 [cluster_nodes.conf](../cluster_nodes.conf)。执行集群操作前，按手册填写客户端地址、登录用户和无线参数。

| 文件 | 职责 |
| --- | --- |
| `deploy_node.sh` | 安装单机依赖、驱动、UFTP 和 FL 运行环境，应用系统网络配置 |
| `setup_cluster_auth.sh` | 配置 Server 到各 Client 的 SSH 公钥与免密 sudo，验证非交互访问 |
| `deploy_cluster.sh` | 同步集群代码，并行部署节点并验证就绪条件 |
| `cluster_config.sh` | 读取并校验集群配置，供部署和演示入口调用 |
| `radio_interface.sh` | 发现无线网卡，供部署与演示入口调用 |

常用命令在 Server 的仓库根目录执行：

```bash
bash scripts/deploy_node.sh --dry-run
bash scripts/setup_cluster_auth.sh
bash scripts/deploy_cluster.sh
```

实际部署会安装软件、调整网卡和系统配置，并需要 sudo 权限。参数、前置准备和部署验证以操作手册及各脚本 `--help` 为准。

## 电脑到 Server 的代码同步

- `sync_to_server.sh`：Linux 或 WSL 的 rsync 同步入口。
- `sync_to_server.ps1`：Windows PowerShell 入口，调用 WSL 完成增量同步。

这两个工具同步代码并输出部署与演示命令。调用方式见 [操作手册](../docs/ARMv8集群部署与演示操作手册.md)。现场传输演示入口为 `bash tests/demo/run_fl_demo.sh all`。

## FL 角色服务安装

已具备编译依赖及原生 `uftp`、`uftpd` 的 Linux 主机，可在仓库根目录执行：

```bash
make build_v6
sudo make install_v8
```

`install-v8.sh` 安装底座程序、Python FL 运行层、角色启动程序、systemd 单元及默认配置。它支持 `PREFIX`、`DESTDIR` 和 `PYTHON`；这些变量用于选择安装位置和 Python 解释器。

| 文件或目录 | 职责 |
| --- | --- |
| `wfb-fl-server`、`wfb-fl-client` | 服务端和客户端角色进程入口 |
| `v8-wfb-ng-init.py` | 安装时使用的轻量 Python 包初始化文件 |
| `systemd/wfb-fl-server.service`、`systemd/wfb-fl-client.service` | 两类 FL 角色服务 |
| `default/fl-server.json`、`default/fl-client.json` | 角色服务默认配置 |
| `sysctl/98-wifibroadcast.conf` | 单机部署使用的内核网络参数 |

服务配置、启停和资源生命周期见 [部署与运行基准](../docs/部署与运行基准.md)。完整真实硬件 FL Runtime 验收见 [tests/fl_runtime](../tests/fl_runtime/README.md)。

## 构建与打包

`make` 和 `make build_v6` 构建正式链路底座，`make rpm`、`make deb`、`make bdist` 使用当前 FL 程序、角色服务和配置生成安装包。打包依赖由 `Makefile`、`setup.py` 和 `stdeb.cfg` 定义。

## 维护边界

当前目录维护 FL 集群部署。旧无人机与地面站绑定、旧 WFB 集群服务、FPV 视频、RTSP 和 OSD 部署资产及其安装注册已经退役；缺少构建上下文的 Docker 交叉打包目标及 QEMU 辅助文件一并退役。历史内容通过 Git 查询。

本机自动回归和 namespace 隔离验收已退役。需要运行测试时，从 [tests/README.md](../tests/README.md) 选择现场演示、完整 FL Runtime 验收或手动上行链路验收。
