# fl00 libpcap 运行依赖统一

发布前审计发现四端唯一依赖摘要差异为 libpcap：fl00 安装 `1.10.1-4ubuntu1.22.04.1`，fl01/fl02/fl04 安装 `.22.04.2`。四端旧 native 本身仍为此前统一的制品，不应将依赖差异称为 native 混装或已证明 ABI 不兼容。

本次通过 apt 将 fl00 的 `libpcap0.8` 和 `libpcap0.8-dev` 升级到 `.22.04.2`。首次直连下载无进展、超时后残留 apt 进程已终止，`dpkg --audit` 无输出；随后按用户提供的网关代理 `192.168.108.154:20112`，仅对该次 apt 命令设置 Acquire 代理，下载与安装成功。未执行系统全量升级或 autoremove，未持久修改代理配置。

升级后四端 libpcap SHA-256 均为 `dce7aa5224a551815d7bb50deb01d6f4fd2d77416eda63b97bd9df226d78ce60`。见 `before.txt`、`after.txt`、`apt.log` 与 `apt-proxy.log`。发布脚本会重新核验依赖与实际运行制品。
