# 02: 集群全局配置模板与 SSH/Sudo 免密互信分发工具

**What to build:** 交付集群集中配置文件模板（`cluster_nodes.conf`）以及在 Server 端运行的一键 SSH/sudo 互信配置工具（`scripts/setup_cluster_auth.sh`）。支持配置 5~7 台 Client 动态 DHCP 局域网 IP 与登录用户，支持自定义射频信道（带 161 黑名单校验）、发射功率、上下行 MCS 与 UFTP 速率；在 Server 上执行一次交互输入，自动向所有 Client 分发公钥并配置免密 sudo。

**Blocked by:** None (can start immediately)

**Status:** resolved

- [x] 在项目根目录提供 `cluster_nodes.conf` 模板，明确定义无线参数区（`WIRELESS_CHANNEL`、`WIRELESS_CHANNEL_WIDTH`、`WIRELESS_TXPOWER_DBM`、`DOWNLINK_MCS`、`UPLINK_MCS`、`UFTP_RATE_KBPS`）与节点列表区（`role_name host_ip ssh_user node_id tun_ip`）。
- [x] 配置文件解析器严格校验：禁止将信道设为已知存在驱动越界缺陷的 `161`；将发射功率限制在合法区间（10~20 dBm，默认推荐 12 dBm）；根据 `DOWNLINK_MCS` 校验 UFTP 速率合理性。
- [x] 编写 `scripts/setup_cluster_auth.sh` 脚本：
  - 检查 Server 本机 `~/.ssh/id_rsa.pub`，若无则自动生成；
  - 逐一测试配置文件中登记的 Client，提示用户输入一次 SSH 登录密码，自动通过 `ssh-copy-id` 拷贝公钥；
  - 通过 SSH 在各 Client 远程执行免密配置，写入 `/etc/sudoers.d/99-wfb-nopasswd`；
  - 逐一执行 `ssh -o BatchMode=yes <user>@<ip> "sudo -n true"` 验证免密连通性并输出绿色通过标志。
