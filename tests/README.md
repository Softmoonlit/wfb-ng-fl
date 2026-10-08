# 真实硬件演示与验收

`tests/` 只保留真实硬件演示、验收编排和证据辅助代码，不提供本机回归测试或 namespace 验收入口。Python 包文件用于稳定导入辅助模块，不是测试发现入口。

| 目录 | 职责 | 成功结果的含义 |
| --- | --- | --- |
| [demo/](demo/README.md) | SSH 编排的文件下发、回传与指标看板 | 通信演示成功；不证明 Runtime 四接口闭环或服务生命周期通过 |
| [fl_runtime/](fl_runtime/README.md) | issue #41 完整 Runtime 验收 | 预检、连续三周期双向 Gate、配置等价性、正式 Runtime、生命周期及归档全部通过 |
| [link_uplink/](link_uplink/README.md) | 无 SSH 的三机手动双客户端上行底座验收 | 文件完整、发送许可与上行链路证据通过；不覆盖完整 Runtime 闭环 |

实际运行需要独立 server/client 角色机器及真实无线网卡、匹配驱动、monitor 模式、原始帧发送和 TUN 支持。演示与 Runtime 编排还需要 SSH 管理连接；模型和 update 的数据面必须经过真实 WFB/TUN 无线链路。各目录 README 列出依赖、产物和最简命令；命令均从仓库根执行。

```bash
# 集群、依赖和配置准备好后，执行通信演示
bash tests/demo/run_fl_demo.sh all

# 版本、输入文件与硬件准备好后，执行完整 Runtime 验收
bash tests/fl_runtime/issue41_fl_runtime_loop.sh run-all

# 无 SSH 手动上行：先生成本轮名称，再按子目录说明分机、分终端执行
bash tests/link_uplink/v6_manual_uplink_demo.sh new-name
```

`tests/logs/` 保存手动上行与 Runtime 的运行归档，演示默认产物位于 `/var/lib/wfb-ng/fl_demo/`。已有日志、缓存及其他忽略产物不属于入口代码；清理入口时不改动这些产物。演示的 `--dry-run` 仅供模拟看板，不能作为真实硬件验收证据。
