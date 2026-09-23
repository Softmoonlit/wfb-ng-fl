# 01: ARMv8 单机一键环境部署脚本

**What to build:** 交付适用于通用 ARMv8 aarch64 嵌入式 Linux 的单机一键环境部署脚本 `scripts/deploy_node.sh`。具备幂等性与失败自解释能力，涵盖系统依赖安装、内核头文件与 DKMS RTL8812AU 驱动编译安装、UFTP 源码解压编译与安装、NetworkManager 规则配置、rfkill 解锁、内核网络参数优化（sysctl）、sudo 免密配置以及底座 C++ 编译与系统级安装。

**Reference & Prior Art:** 深度参考真实硬件部署基线（`tests/real_hardware/issue41_fl_runtime_loop.sh` 第 3 节）、`scripts/install-v8.sh` 与 `tests/real_hardware/preflight_checklist.sh`。严格复用其依赖检测、二进制编译与系统安装逻辑，清理残余冲突进程。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] 检查并使用 apt 安装 `build-essential`, `pkg-config`, `python3`, `python3-pip`, `libssl-dev`, `libsodium-dev`, `libpcap-dev`, `iw`, `rfkill`, `unzip`, `git`, `bc`, `dkms`, `net-tools`, `iproute2`。
- [ ] 自动检测并安装当前运行内核对应的头文件包（`linux-headers-$(uname -r)`）。
- [ ] 自动下载或复用本地 `rtl8812au` 源码，调用 `dkms-install.sh` 编译并安装驱动内核模块，自动执行 `modprobe 8812au` 并在失败时给出明确指引。
- [ ] 检查并解压 `../uftp_src-5.0.3.zip`（或本地源码），执行 `make` 编译（链接 OpenSSL `-lcrypto`），并调用 `make install` 将 `uftp` 和 `uftpd` 写入 `/usr/bin`。
- [ ] 在 `/etc/NetworkManager/conf.d/wfb-unmanaged.conf` 中配置忽略 `wlx*` 前缀网卡并重启 NetworkManager 服务。
- [ ] 执行 `rfkill unblock all` 解锁无线射频。
- [ ] 将 `scripts/sysctl/98-wifibroadcast.conf` 安装到 `/etc/sysctl.d/` 并执行 `sysctl --system` 调优 socket 缓冲与队列。
- [ ] 自动在 `/etc/sudoers.d/99-wfb-nopasswd` 写入规则，确保当前用户具备非交互 `sudo` 免密执行权限。
- [ ] 在项目根目录执行 `make build_v6` 编译产出 `wfb_v6_uplink`，并执行 `sudo make install_v8` 完成系统级角色服务与 Python 运行时安装。
