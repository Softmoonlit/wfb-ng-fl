# 完整 FL Runtime 真实硬件验收

`issue41_fl_runtime_loop.sh run-all` 是完整验收总入口：依次执行预检、安装、连续三周期双向数据面 Gate、配置等价性核验、正式 Runtime 闭环、systemd 生命周期审计、证据采集和归档校验。正式闭环通过 `publish_model()`、`wait_for_model()`、`submit_update()`、`wait_for_updates()` 完成；任何关键阶段或证据失败，总结论都不能通过。

[完整现场手册](v8_issue41_SSH编排真实硬件FL闭环验收手册.md) 保留原验收步骤、参数及证据要求。手册包含历史现场基线；本次运行的客户端集合、文件大小、轮数与无线参数以现役脚本和操作者显式配置为准，本次目录迁移不改变这些值。当前脚本默认客户端集合为 `client1 client2 client3 client4 client6 client7`，管理别名为对应 `vmN`；可通过 `ISSUE41_CLIENT_ROLES` 配置现场参与集合。

现场需要真实 server/client 无线角色机，每台恰好一个可动态发现的 `wlx*` 无线接口、匹配驱动、monitor/原始帧发送与 TUN 支持；需要 SSH 管理连接、sudo、systemd、`ip`、`iw`、Python 3、构建工具和 `uftp`/`uftpd` 等手册规定依赖。所有节点必须使用同一分支与提交，工作区干净。管理网只用于编排和采集，模型/update 必须经过 WFB/TUN 无线链路。

仓库与远端版本已同步、依赖及输入文件已准备好时，从仓库根执行：

```bash
bash tests/fl_runtime/issue41_fl_runtime_loop.sh run-all
```

若需生成验收用确定性模型和各客户端独立 update 模板，先执行下面命令。它写入角色机上的输入目录；已有业务输入时按手册显式配置文件路径。

```bash
bash tests/fl_runtime/issue41_fl_runtime_loop.sh generate-fixtures
bash tests/fl_runtime/issue41_fl_runtime_loop.sh run-all
```

`run-all` 不自动发布分支或同步远端；手册中的 `push-branch`、`sync-remotes` 为显式准备步骤。分阶段执行时，先设置同一个唯一 `ISSUE41_RUN_ID`，让各次调用使用同一归档；`run-runtime-loop` 单阶段成功或 `smoke-gate` 成功不能替代完整验收。

默认归档位于 `tests/logs/v8_issue41_<timestamp>/`，包含 `envelope.json`、`orchestration/`、`pre_runtime_smoke/`、`formal_runtime_loop/`、`lifecycle/`、`raw/`、`issue41_summary.json` 和 `result.md`。Runtime 工作文件位于 `/var/lib/wfb-ng/issue41/{server,client}`。失败时保留证据，目录的受控清理使用手册规定的 `clean` 流程。

辅助文件职责：

| 文件 | 用途 |
| --- | --- |
| `issue41_envelope.py` | 运行包络、拓扑、预检和分区失败记录 |
| `issue41_gate.py` | 双向数据面 Gate 的直接传输与遥测证据校验 |
| `issue41_lifecycle.py` | 服务停止、重启、进程与资源清理审计 |
| `issue41_build_summary.py` | 从 Runtime 结果与日志汇总正式闭环事实 |
| `issue41_validate_archive.py` | 严格校验归档完整性与最终结论 |

离线审阅已有归档可在仓库根执行：

```bash
python3 -m tests.fl_runtime.issue41_validate_archive tests/logs/<run_id>
```

[通信演示](../demo/README.md) 和 [手动上行底座验收](../link_uplink/README.md) 各有自己的证据边界。本目录不提供本机回归或 namespace 验收入口。
