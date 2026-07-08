# scripts 目录说明

## 目录角色

`scripts/` 用于放置部署脚本、系统集成辅助文件、默认配置、systemd 单元、交叉构建辅助脚本以及少量历史脚本。

这个目录不是当前 `v6` 三机正式验收入口，但它会影响部署、安装和历史运行路径判断。

## 当前推荐关注点

- 默认配置：`default/`
- systemd 单元：`systemd/`
- 系统调优：`sysctl/`、`logrotate/`
- 安装辅助：`install_gs.sh`

## 子目录说明

- `default/`：默认配置文件
- `systemd/`：systemd unit 文件
- `bind/`：绑定/初始化辅助脚本
- `qemu/`：QEMU/交叉构建相关辅助文件
- `sysctl/`：内核参数配置片段
- `logrotate/`：日志轮转配置片段

## 历史 / 弃用说明

本目录中存在历史 standalone 脚本与早期辅助脚本，不应误当成当前 `v6` real-hardware 主入口。

判断原则：

- 如果脚本围绕 `wfb_rx` / `wfb_tx` 的 standalone 运行方式展开，通常不是当前 `v6` 三机正式入口
- 当前 `v6` real-hardware uplink 正式入口不在本目录，而在 `tests/real_hardware/`

## 下一步应该看哪里

- 看真实硬件正式入口：转到 `tests/real_hardware/README.md`
- 看文档权威入口：转到 `docs/README.md`
