# FEC12 本地编排审阅稿

当前状态：仅本地准备，等待父明确 GO。尚未启动 worker，未连接远端，未执行 profile 的任何命令或发布阶段。未修改生产源码、冻结通信工具、pending profile、根 README 或 experiment-plan。父负责等待另一 actor 的 FEC14 40/10、60/10 收尾，本阶段不自行回 FEC14、不做最终选优。

本会话没有 `read_agent_response` 接口，已按授权直接读取 pending profile 的 README、state、发布入口及 node helper。profile 为 `.scratch/heartbeat-restart/heartbeat-1cd6264d5d6b-20261108T124420Z`，本地核验要求 `completed=[]`、expected 为 10/10/FEC12。source commit 固定 `1cd6264d5d6be660998ce60f3a349063009ecfa7`，native SHA 固定 `44d6bbf500f51df4d0b3ae8cd7a53c4a45cb55b09ca66cb5217e7ac75674feb2`，运行 release 为 `/srv/wfb-ng-fl/releases/heartbeat-1cd6264d5d6b-20261108T124420Z`。保留正确的 `source/wfb_ng/fl` 布局，不编译 native、不升级依赖、不修改生产源码。原安装脚本中的 `make build_identity` 仅生成构建身份记录，不调用 native 编译目标；仍使用已冻结 native。

固定节点为 00＋01、02、03、05、06、07、08、09、10，排除 04。完整 MAC 和明确 interface 来自兄弟 `after-storage-models/initial-audit.json` 的全十端 actual 读回，来源 SHA 与表保存在 `hardware-from-actual.json`。00 为 `fc:a3:86:b3:86:72`，10 为 `fc:22:1c:10:01:19`，05 为 `fc:22:1c:10:01:57`。运行前逐端复核对应 interface、MAC 和 sysfs `rtl88xxau_wfb`，不接管管理网。未新增 GET_RADIO 字段断言；沿用正确协议核验 GI/BW/MCS，功率仅通过明确 interface 的 iw 查询，启动参数 MCS 为 DL3/UL4。

执行顺序固定如下，每格最多一次 execute，不重试同一候选、不缩客户端：

| 顺序 | grant/guard（毫秒） | 首轮 | 通过后的模型轮 |
| --- | --- | --- | --- |
| 1 | 10/10 | 1MiB smoke | 44770302 字节模型 |
| 2 | 20/10 | 1MiB smoke | 同一模型 |
| 3 | 30/10 | 1MiB smoke | 同一模型 |
| 4 | 40/10 | 1MiB smoke | 同一模型 |
| 5 | 60/10 | 1MiB smoke | 同一模型 |
| 6 | 20/5 | 1MiB smoke | 同一模型 |
| 7 | 30/5 | 1MiB smoke | 同一模型 |

每档 smoke 必须完成全九客户端下载／上传、size/SHA、精确清理、全十端实际恢复和九 READY，才进入该档模型。最多 7 次 smoke＋7 次模型＝14 次 execute。已通过的旧小文件测试不重复，也不把 FEC14 的通过结果当作 FEC12 的 smoke 通过。模型 SHA 为 `4b55cee2f0bdcfbbf610167a505efee0e2c50e3306fed02bda3613807c234e96`。方向时限 600 秒、overall 1290 秒、实际 UFTP 12000kbps；157 测试、149 准备与恢复，HT40/LGI、index18、DL3/UL4、15W。冻结 runner/node_agent/recover 三文件的 bundle SHA 保持 `ab8697fe4573ac3c1088dd11022a1fd75a25cf13752d2717de02e6aa2e625e9e`。

GO 后先检查本地无其他 worker/runner，再以旧 release 单独记录 FEC14 10/10 基线。随后 pending profile 按 `preflight → stage → prepare → stop → install → start → audit` 推进，每阶段全部十端成功后才下一阶段，不跳过屏障。每阶段命令、日志和十端 checkpoint 独立保存。最终实际读取 GET_FEC 8/12，再通过 FEC12 全十端 actual 审计与九 READY。旧 FEC14 基线只存在于冷切换前的专门目录，正常 prepare/restored/final baseline 均为 FEC12；最终恢复 schedule 10/10，不自行还原 FEC14。

每次模型前全十端 statvfs 可用空间须 ≥512MiB 且有可用 inode。若 01/02 低于门槛，仅回收本地已有完整日志与 size/SHA 证据覆盖的 `comm-*` 内旧完整重复 `model.bin`／`update.bin`。复用先前清理 worker 的 HASH／ARCHIVE／DELETE 常量，逐文件核验，单次 hash 仅一个文件、远端调用最长 50 秒；不执行原清理 worker。先确认 canonical 本地模型 SHA，保留各端 B2 原件；先建立具名 manifest，归档并读回核验日志元数据，再 hash、re-stat、精确 unlink。保留 partial、日志 JSON、evidence、source、release、真实训练数据和 install-cache；不执行 rm-rf 或目录通配删除。1GiB 为代理选择的执行预算目标，不是用户明确指定的阈值；授权范围内无法达到 512MiB 就停，不扩大删除范围。空间恢复后仍重查全十端，不以单端缓存回收结果代替准入。

runner 的原始 `transfer_verified/cleanup_ok/restore_ok` 保存为 `original_runner_final_flags`；首次独立恢复结果保存为 `immediate_independent_restore_ok/immediate_independent_ready_ok`；最终独立结果另存 `independent_exact_cleanup_ok/independent_restore_ok/independent_ready_ok`。瞬时 false 与最终 true 使用不同文件和字段，不覆盖。容许一次 settled 检查，必要时一次标准服务恢复。传输失败但 exact cleanup＋全十端 actual FEC12 恢复＋九 READY 均通过，允许下一不同候选；smoke 失败则跳过该档模型。management 失联、USB reset 或独立清理残留立即停止后续矩阵及自动变更；实际恢复不齐也停止，不把单次传输失败泛化成停止整矩阵。

GO 后总预算为 150 分钟，运行过程中持续写 matrix/checkpoint、每模型 phase、各端完成时间与上传中间进度；每个候选前保留完整执行与恢复预算，预算不足即停止。worker 后台运行并自行监督 runner；轮询 2–5 秒，sleep 不超过 5 秒。runner 到监督边界后先 SIGINT，必要时在 30 秒后 SIGTERM、35 秒后 SIGKILL。父轮询工具每次等候不得超过 30 秒。冷切换中断不跳阶段、不自行混合恢复旧组，由父审阅已完成阶段和十端证据。

父审阅并明确 GO 后，可在本目录创建 `GO.json`，内容应包含 `parent_authorization: "GO FEC12"`、`fec14_actor_finished: true`、`scope: "cold-switch-and-seven-fec12-candidates"`，并用后台进程启动 `worker.py`，stdout/stderr 指向本目录具名日志。本回合没有创建 GO 文件或启动进程。worker 拒绝没有上述明确授权的运行，也拒绝已存在 worker-process 的重复启动。

供父审阅的文件：`worker.py`（冷启动屏障、七档与恢复政策）、`driver-base.py`（FEC12 参数、actual 核验、runner 监督）、`helpers.py`（独立恢复和逐文件证据）、`storage.py`（精确回收）、`hardware-from-actual.json`、`plan.json`、`local-verification.json`、`review-manifest.json`。`verify-local.py` 仅做本地语法、AST、字节摘要和计划核验，不导入或启动 worker，不执行 profile 命令，不访问 HTTP/SSH。
