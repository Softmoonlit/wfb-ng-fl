# 四客户端模型等量通信 runner

服务端 fl00，固定客户端 fl01/fl02/fl04/fl05。使用已安装 c665948 的 ServerRole/ClientRole 和正式 UFTP/HTTP 传输；四客户端接收真实模型制品字节后上传同一文件，无训练、聚合或评估。

## 载荷与执行门槛

`--payload <本机已存在制品>` 必填，接受 1 字节至 64MiB（含上界）的普通文件。runner 在连接前以 1MiB 分块读取，记录绝对路径、实际 `size_bytes` 和 SHA-256。空文件、缺失文件和超限文件直接拒绝；旧的固定 1MiB 接口已移除。

CLI 同时必填 `--model-manifest <JSON路径>`，连接前要求 `model_format="cifar10-resnet18-fp32-v1"`、`size_bytes` 与 SHA-256 精确匹配载荷；`initialization.seed` 必须为非 bool 的整数且在 0..2**63-1，`initialization.source` 必须为非空字符串。计划记录 manifest 绝对路径和完整 JSON 内容，保留调用方补充的 torch 版本等元数据。

计划使用 `kind=model-equivalent-communication`、`model_manifest_matched=true`、`model_semantics_validated_by_runner=false`，不再设置 `formal_resnet_equivalent`。本工具仅校验 manifest 与字节身份，不解析张量、不导入 torch，不承诺正式 FL 生命周期或正式 update 等量。模型公共 load、模型语义及真实 CLI manifest 由调用方另行验证；本地 fixture 不产生模型验收结论。

默认仅打印本地计划，不调用 SSH 或启动角色：

```sh
python3 .scratch/fl-testbed-magazine-draft/communication/runner.py --payload /absolute/path/to/model-artifact --model-manifest /absolute/path/to/model-manifest.json
```

审查制品 manifest、计划和当前代码后，执行仍要求 `--execute --reviewed-sha <bundle_sha256>`。摘要覆盖 runner.py、node_agent.py、recover.py 三文件，修改任一文件都必须重新核验。制品 SHA 与代码冻结 SHA 分别记录，不能互相替代。

只有服务端 prepare 收到 `{path,size_bytes,sha256}`；四客户端仅收到大小和摘要。fl00 必须能直接读取本机路径，管理网不传制品。服务端 prepare 校验源文件，publish 再次校验并复制到本轮自有工作目录，复制后再次验 SHA，随后 UFTP 下发这些真实字节。四客户端接收校验全部通过后统一上传原接收文件；服务端必须收到并独立验证全部四份。所有文件 SHA 均流式计算，`max_update_size_bytes` 精确等于该制品实际大小。

## 固定无线与回收边界

- 保留常驻 daemon、native 和 TUN。prepare 核对 c665 来源、完整安装身份、native SHA、全部四端 READY/IDLE 且无活动作业；五节点 prepare 全通过后才重配和启动角色。测试期间不得并发启动其他 FL 任务。
- 仅用现有普通 reconfigure：157/40MHz/Long GI/功率索引18、MCS3/4、RS(8,14)、UFTP 15000kbps。不使用 calibrate，不持有 daemon 的作业准入租约。
- 使用 UFTP 1144、HTTP 18080 和 239.80.41.1/239.80.42.1。测试专有组播路由标记 proto186，按同一 ifindex 和路由快照回收。
- 期限集中在 runner 的 `TIMING`，通过 prepare 传给节点：每阶段 I/O 300 秒，主编排 690 秒（含正常 close/restore 余量），节点信号及临时 unit 生命周期均 705 秒，独立回收预算 60 秒。回收共用起点为编排开始的 750 秒绝对截止点，禁止逐次重置预算。
- 临时 unit 使用 KillMode=control-group、TimeoutStopSec=8；节点清理最多 10 秒且受原 705 秒期限约束，close 不重新增加生命周期。范围只含本轮代理及后代，不含常驻进程。
- 全角色 close 后正常恢复原六项无线配置（正常基线为 149），然后并发向五端调用 `audit_final`；全部审计按时成功才设置 `restore_ok=true`。每端 prepare 保存实际原信道（149/157），最终精确验证原信道/40MHz/-18dBm、GET Long GI/MCS3或4、全部 resident PID/starttime、nvpmodel 15W/mode0、CPU0..5 全在线且 max1497600、GPU max612000000、EMC cap2133000000 和 mrq_rate_locked0，并返回各项 actual。路径依据既有 `evidence/communication-four-20261106T165744Z/*-final-audit-and-facts.json`、`evidence/board-power/jtop-audit-20261106T095719Z/{fl00,fl01,fl02,fl04,fl05}.json` 及 `evidence/board-power/audit-jtop.py`；仅只读，不修改硬件参数，也不延长 705 秒警报。
- 正常恢复或最终审计失败/迟到时，独立 recover 先确认所有测试 unit 结束，再按持久化身份恢复；仅报告 `rf_response_ok` 与 `audited=false`，即使 HTTP 成功也保持最终恢复失败，不能覆盖五端审计失败。传输验证、清理和恢复分别记录。

## 本地验证

```sh
python3 -m unittest discover -s .scratch/fl-testbed-magazine-draft/communication -p test_mock.py -v
```

20 项临时小文件与 mock 测试覆盖 manifest 格式/大小/SHA 不匹配时不启动 SSH、初始化元数据范围、五端最终审计全员/迟到/缺失门槛、硬件锁频和 PID 漂移拒绝，以及流式元数据、SHA/大小不符、空/缺失文件、服务端真实字节复制和篡改拦截、客户端原文件上传、漏目标会话/更新、全员 prepare 门槛、并发下发和上传屏障、必填 payload、默认 dry-run 不启动 SSH、拒绝未审查执行、完整无线请求及接口身份变化时保留路由。测试不导入 torch 或生产角色，不连接远端、不调用生产 UDP，也不构造大文件。

正式模型生产 API 的扩展、模型语义验收和角色独立功率不属于本 runner 的范围。旧 1MiB 实验记录保留在既有归档中，新接口不提供 fallback。
