# 十客户端通信 runner

服务端固定 fl00，客户端固定 fl01/fl02/fl03/fl04/fl05/fl06/fl07/fl08/fl09/fl10，节点集合为 `[1,2,3,4,5,6,7,8,9,10]`。使用已安装 88099f0 的 ServerRole/ClientRole 和正式 UFTP/HTTP 传输；客户端接收载荷后上传同一文件，无训练、聚合或评估。88099f0 commit、release 路径、完整安装身份、native SHA 门槛保留，库存不符会拒绝 prepare。

标准部署身份只接受 `/usr/bin/python3 -I -B -m wfb_ng.fl.server_daemon/client_daemon`。`--release-commit` 默认 `88099f04ffaee56c576bb35a9e50baec5f1113d7`，`--release-root` 默认 `/srv/wfb-ng-fl/releases/heartbeat-88099f04ffae-20261108T100120Z`。后续发布可显式传入二者，无需改源码；commit 必须为完整 40 位小写 SHA，root 必须为 `/srv/wfb-ng-fl/releases/heartbeat-<commit前12位>-<UTC时间戳>`，两层均严格校验。prepare 核对当前 MainPID 的 proc argv 与 executable、发布 actualargs、完整安装/源码 SHA，以及 manifest/build identity 与期望 commit 精确一致，再用独立 `-I -B` 解释器导入 daemon/role 核对解析路径和 SHA。此证据不宣称读取活进程 sys.modules；不读取历史 loaded.json，不支持 daemon.py wrapper。当前 native 期望 grant15/guard5。

## 载荷与执行门槛

model 模式同时必填 `--payload <本机已存在制品>` 和 `--model-manifest <JSON路径>`。载荷接受 1 字节至 64MiB（含上界）的普通文件，在连接前分块计算实际 `size_bytes` 和 SHA-256。manifest 要求 `model_format="cifar10-resnet18-fp32-v1"`、大小和摘要精确匹配；`initialization.seed` 必须为非 bool 的整数且在 0..2**63-1，`initialization.source` 必须为非空字符串。计划记录 manifest 的绝对路径和完整 JSON 内容。

`--smoke` 显式选择通信冒烟模式，不接受 `--payload` 或 `--model-manifest`。runner 在 `--output` 下生成 `smoke-<run-id>.bin`，内容为恰好 1MiB 的零字节。相同 run-id 的现存文件必须大小和摘要完全匹配才可复用，不覆盖其他内容。计划明确标记 `kind=synthetic-smoke-communication`、`payload_mode=smoke`、`generated_payload=true`、`model_manifest=null` 和 `model_manifest_matched=false`。该文件只是合成通信载荷，不是模型。

默认只生成文件（smoke）及打印本地计划，不启动 SSH、临时 unit 或角色：

```sh
python3 .scratch/fl-testbed-magazine-draft/communication/runner.py --smoke --run-id nine-smoke
python3 .scratch/fl-testbed-magazine-draft/communication/runner.py --payload /absolute/path/to/model-artifact --model-manifest /absolute/path/to/model-manifest.json
```

model 计划使用 `kind=model-equivalent-communication`、`model_manifest_matched=true`、`model_semantics_validated_by_runner=false`。工具只校验 manifest 与字节身份，不解析张量、不导入 torch，不承诺正式 FL 生命周期或正式 update 等量。模型公共 load、模型语义及真实 CLI manifest 由调用方另行验证。

本轮计划先执行一次 1MiB smoke，再执行一次 44,770,302 字节的模型制品通信；模型模式继续要求匹配 manifest。默认信道 157、速率 12000kbps。若执行时观察到 USB reset，应停止本轮；runner 不采集内核 USB 日志，执行方须监测。仅有 USB reset 和 native abort 的先后记录不足以判断物理根因，诊断须保留因果边界。

执行仍要求 `--execute --reviewed-sha <bundle_sha256>`。摘要覆盖 runner.py、node_agent.py、recover.py 三文件，修改任一文件都必须重新核验。制品 SHA 与代码冻结 SHA 分别记录。

只有服务端 prepare 收到 `{path,size_bytes,sha256}`；十客户端仅收到大小和摘要。fl00 必须能直接读取载荷路径，管理网不传载荷。服务端 publish 校验源文件，复制到本轮自有工作目录，再验 SHA，随后 UFTP 下发。全部客户端接收校验成功后才统一上传原接收文件；服务端必须收到并独立验证完整十份更新。`max_update_size_bytes` 精确等于载荷实际大小。

`--fec-n {12,14}` 默认 14，`--grant-duration-ms` 默认 15（正整数），`--guard-interval-ms` 默认 5（非负整数）。plan 的 `expected_native` 固定 `fec_k=8` 并记录这三个期望值；它们表示 resident 的实际启动参数，runner 不动态修改。prepare 在保留 native SHA 校验的同时，要求十一端真实 `/proc/<pid>/cmdline` 的 `--fec-k/--fec-n` 匹配，并只在服务端核对 grant/guard；客户端不要求 grant 参数。另用只读 GET_FEC（cmd 3）核对实际活动 FEC。切换 FEC14/FEC12 必须由外部正式部署工具统一重启全部十一板服务。

HT40 下行 MCS4、12000kbps 候选的 acquire_radio 使用专用 calibrate endpoint；HT40、速率 >=18000kbps 使用普通 reconfigure。HT20 保留 calibrate；生产 API 准入另行检查。

## 无线与回收边界

- 保留常驻 daemon、native 和 TUN。prepare 核对冻结安装身份及 native SHA，要求安装目录顶层 `*.py` 集合、冻结源码顶层 `*.py` 集合与 manifest 中 `.py` 条目集合三者严格相等；manifest 允许 `build_identity.json` 等顶层非 Python 文件，所有 manifest 条目都逐文件核验安装 SHA，Python 文件另核验冻结源码 SHA；服务端要求完整十客户端 READY/IDLE 且无活动作业。十一节点 prepare 全通过后才重配和启动角色。
- 默认 157/40MHz/Long GI/功率索引18、MCS3/4、RS(8,14)、UFTP 12000kbps，使用普通 reconfigure。可选 `--downlink-mcs {3,4}`、`--uplink-mcs {4,6}` 和 `--uftp-rate-kbps 1..26000`，默认仍为 MCS3/4、12000kbps；API 准入另行检查。现有 HT20 选项保持 calibrate 行为。工具不持有 daemon 的作业准入租约，测试期间不得并发启动其他 FL 任务。
- prepare 从真实 native argv 读取启动 MCS，严格要求服务端3/客户端4，再用 GET 验证相同当前基线并一次性记录 `original_mcs` 快照；如果在线 MCS 已偏离启动参数则拒绝 prepare，保留现场。verify_radio 核对候选 MCS（如4/6），audit_final 显式核对保存的原始 MCS（3/4），候选不得覆盖恢复目标。
- GET 必须实际返回 `short_gi=0`，并验证带宽和 MCS；不能仅凭计划宣称 Long GI。UFTP 1144、HTTP 18080 和 239.80.41.1/239.80.42.1 保持固定。
- 期限集中在 `TIMING`：阶段 I/O 每方向 600 秒，主编排 2×600+90=1290 秒，节点信号及临时 unit 生命周期 1305 秒，独立回收预算 60 秒。回收使用编排开始后 1350 秒绝对截止点，不逐次重置预算。
- prepare 或任意 phase 首个失败即退出编排。传输失败时不把 close 排到阻塞 RPC 后面；dispose 唤醒本机 RPC 工作线程、关闭自有会话，随后独立停止本轮临时 unit。清理线程池显式 `shutdown(wait=false, cancel_futures=true)`，不通过上下文管理器退出等待挂起 future。不会等待其他 publish 到 1290 秒，也不会杀 resident。
- 临时 unit 使用 `KillMode=control-group`、`TimeoutStopSec=8`；范围只含本轮代理及后代。独立回收只停止 `comm-smoke-<run-id>-<host>.service`。专有组播路由标记 proto186，按同一 ifindex 和路由快照回收；接口或路由身份改变则保留现场。常驻进程只核验 PID/starttime。
- 正常全角色 close 后恢复原六项无线配置，然后十一端并发 `audit_final`。每端验证原信道/40MHz/-18dBm、GET Long GI/MCS3或4、resident PID/starttime、15W/mode0 及既有 CPU/GPU/EMC 基线。仅全部按时审计成功才设置 `restore_ok=true`。
- 正常恢复或最终审计失败时，独立 recover 先确认全部测试 unit 结束，且每端返回的 `ok` 必须严格为布尔 `true`，再按持久化身份尝试恢复。独立恢复仅报告 `rf_response_ok` 与 `audited=false`，保持最终恢复失败；传输验证、清理、恢复分别记录。

上传阶段主线程使用 `futures.wait` 等待，每 5 秒在 fl00 本机同步调用一次 `sudo -n python3`，单次超时最多 3 秒且受编排绝对截止限制；不增加采样线程。只读本轮 `/srv/wfb-ng-fl/comm-<run-id>/role/rounds/*/updates/<1..10>/` 下的 `.update.bin.*` 与 `update.bin`，跳过符号链接，记录 `phase=upload_progress`。`bytes` 是可见文件的最大大小，`partial_bytes` 是可见临时文件大小总和，`committed` 仅表示 `update.bin` 存在，最终完整性仍由 verify 确认。缺失文件记录 `bytes=null, observed=false`，不能推断为零字节：临时文件可能已被 cleanup 删除。采样失败独立记录，不替代传输结果。

## 本地验证

```sh
python3 -m unittest discover -s .scratch/fl-testbed-magazine-draft/communication -p test_mock.py -v
```

54 项本地测试覆盖完整固定集合、完整 READY 门槛、十一端 prepare、并行 publish 与上传屏障、十份更新门槛、manifest 校验和缺失拒绝、model/smoke dry-run 不启动进程、显式生成恰好 1MiB、发布失败立即退出并独立回收、真实 RPC 工作线程唤醒、各节点只回收自有 unit、GET shortGI 实际断言、全员最终审计及硬件/PID 漂移拒绝、流式载荷校验、真实字节复制和篡改拦截、客户端原文件上传、路由身份保护。测试不导入 torch 或生产角色，不连接远端，也不调用生产 UDP。
