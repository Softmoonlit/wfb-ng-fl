# Stage 3 三机真实射频执行器

正式入口是 `tests/fl_runtime/stage3_vm_physical_loop.sh`，固定使用本机 vm0 Server 和 SSH 别名 vm1、vm2 Client。后续真实复验经验与 Stage 4 接缝参见[三机验收经验与后续验证指南](../../docs/acceptance/stage3-stage4三机验收经验与后续验证指南.md)。正式规格为 [Stage 3 spec](../../.scratch/stage3-vm-managed-physical-radio-loop/spec.md)，生命周期和现场诊断参考 [Stage 2 指南](../../docs/acceptance/stage2三机无SSH运行时自治验收指南.md)。Stage 2 的 4 MiB 和临时 unit 结果不能作为 Stage 3 通过依据。

## 执行

先通过 `git fetch` 和 `git merge --ff-only` 同步三端提交，保证工作树干净；将三端真实无线网卡直通到 xHCI，保证唯一 `wl*` 接口。Client 的 `/etc/wfb-ng-fl/node.json` 分别使用 `node_id=1/2` 和 `tun_ip=10.80.0.11/12`。不要用脚本清理无法确认归属的旧进程、TUN 或任务沙箱；预检发现这些资源会拒绝运行。

```bash
bash tests/fl_runtime/stage3_vm_physical_loop.sh run-all
```

执行顺序固定为 `preflight → install → start-services → run-sync → run-radio-recovery → collect → stop-services → validate`。入口帮助列出分阶段参数。归档根目录默认 `tests/logs/`，通过 `STAGE3_ARCHIVE_ROOT` 覆盖。每次运行生成新的 `run_id` 和 `job_id`；失败后使用新的归档重跑，禁止拼接旧证据。

安装阶段在本机临时独立 clean worktree 执行 `make deb`，只接受一个 Debian 包。三端安装同一包，并回读包版本、文件归属、入口、unit、链路二进制和 build identity 的摘要。执行器只启动正式 `wfb-fl-server-daemon.service` 和 `wfb-fl-client-daemon.service`。

正常场景只向 Server 本地 REST 提交双 Client、两轮、40 MiB 的 sync 作业。作业窗口内执行器不连接 vm1/vm2；Client 由现有 `TASK_ANNOUNCE` 和作业级 `TASK_READY` 屏障自主启动。执行器完成的所有命令以阶段、目标、开始/结束时间、类别和退出码记录，SSH 不复用或保持后台连接。

射频场景在 Client 2 的 `fl-c2` UDP 9000 输入路径安装仅匹配 `NEW_CHANNEL_PING` 与 `RADIO_SWITCH_FINALIZED` 的 payload 规则；通过本地 REST 人工确认 157→149。它等待 Server 回退和 Client 的 session 绑定租约日志，观察全集群在 157 上恢复待命，随后删除规则。预检用未挂接的链测试 matcher；异常或中断清理故障规则并停止本次受管服务，保留失败现场与机器可读分类。

## 证据与结论

Client 证据直接来自 `/var/lib/wfb-ng-fl/evidence/<job_id>/` 的原始小型文件；不通过 SSH 抢救运行中的沙箱，不复制 Client 模型/update 本体。Server 保存实际模型、update 和聚合输出，供离线 SHA-256 核验。Transport 配置、Coordinator 配置、链路启动配置、journal 和终态资源核查均从实际运行结果回读。

离线 validator 同时核对原始 preflight 拓扑、运行后三端提交与干净工作树、活动 unit 与 Debian 制品摘要，以及终态 REST 快照和作业身份绑定的 daemon journal。终态广播失败时 Server 停止接单；Client 的成功 lifecycle 允许完成信令终止角色产生 `-15/-9`，但清理日志退出码必须与 evidence manifest 一致。归档封口只提供完整性，不能替代这些语义检查。

收集先保存空闲期资源，停服后追加三端进程、cgroup 和 TUN 核查，最后封口并验证。离线复核：

```bash
python3 -m tests.fl_runtime.stage3_archive validate tests/logs/<run_id>
```

只有完整归档的 validator 返回 `passed` 才形成 Stage 3 通过。该结论为**作业运行时无 SSH 依赖**，机械证明仅覆盖执行器记录，不排除其他管理员行为；管理网络保持可达。算法为确定性占位训练/聚合，不证明真实训练效果。SHA-256 提供完整性检查，不提供签名或不可抵赖。

## 复用审计边界

| 能力 | 处理 |
| --- | --- |
| Issue #41 动态无线发现 | 小范围提取到 `hardware.py`，Issue #41 与 Stage 3 共同调用；增加本接口 sysfs 祖先 xHCI 核查 |
| Issue #41 停服资源检查 | 直接调用参数化 `LifecycleConfig` 和 `audit_stopped_node`，补充 daemon/RoleService 完整命令行检查 |
| fixture 与 SHA-256 | 直接调用 `wfb_ng.fl.issue41_fixtures` 与 `wfb_ng.fl.artifacts` |
| 构建安装 | Stage 3 专用独立 worktree Debian 流程；Issue #41 的 `make install_v8` 不安装正式 daemon unit |
| envelope、失败归档与 validator | Stage 3 专用身份、阶段、证据矩阵及结论；旧 envelope 的五分区和 SSH RoleService 编排不能表达本场景 |
| 运行时 | 直接复用现有 Server/Client Daemon、RoleService、Coordinator、Runtime、UFTP/HTTP 和 TASK_READY；只补实际配置及租约日志观测 |

执行器不新增运行时或传输实现。无硬件测试覆盖归档拒绝篡改、生命周期必选文件、SSH 越界、故障规则范围及失败清理；实体三机验收由工单 06 独立记录。
