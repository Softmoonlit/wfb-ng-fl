本目录仅为最终单候选连续两次模型验收的本地准备。暂按 FEC8/12、授权 30ms、间隔 5ms 编写，不表示已入选；父需先等待 FEC14 剩余四档结束并最终选优。如改选，必须修改脚本、重新核验与生成审阅清单，不能沿用本次 GO。当前没有 GO.json，没有启动 worker，没有 SSH、现场 HTTP、发布或服务变更。

固定参与端为 00+[1,2,3,5,6,7,8,9,10]，04 排除。源提交 1cd6264d5d6be660998ce60f3a349063009ecfa7，native SHA256 为 44d6bbf500f51df4d0b3ae8cd7a53c4a45cb55b09ca66cb5217e7ac75674feb2，冻结通信 bundle 为 ab8697fe4573ac3c1088dd11022a1fd75a25cf13752d2717de02e6aa2e625e9e。模型为 44770302 字节，SHA256 为 4b55cee2f0bdcfbbf610167a505efee0e2c50e3306fed02bda3613807c234e96。

硬件表逐字复用 fec12/hardware-from-actual.json。driver 必须为 rtl88xxau_wfb；00 MAC fc:a3:86:b3:86:72、USB480，10 MAC fc:22:1c:10:01:19、USB5000，05 MAC fc:22:1c:10:01:57。实际功耗条件为 15W、CPU scaling_max_freq 1497600、GPU 612000000、EMC nvpmodel cap 2133000000、online 0-5、mrq_rate_locked 0。无线基线为实际 149/HT40、index18，GETRADIO 为 LGI/BW40/MCS3 下行与 MCS4 上行；不新增把 MCS 当作功率的字段判据。

本地 profile 为 heartbeat-1cd6264d5d6b-20261109T083151Z，使用已有 release-online.py 的 init 生成。artifact 来自 .scratch/heartbeat-restart/native-1cd6264d5d6b/wfb_v6_uplink；expected 为 10/10/12，native_before_sha 与 native_sha 相同。state.completed 为空；source.tar、readelf、native、state 都保留在本目录。该阶段没有执行 AArch64 二进制、编译或升级。

父 GO 的必要证据来自 fec14-matched-completion/after-cold-audit-recovery/：matrix.json 必须 complete，逐四档检查 small 恰执行一次且完成，model 恰执行一次且完成，或 small 未通过且 model 明确 skipped/零 execute；不为满足守门而重跑 small 或补跑 model；final-closeout/result.json 必须全十板实际恢复、九 READY、FEC14 和 server10/10 全通过，ownerfinished 且此前远端执行者已结束、无活动临时 unit。原 matched 根 matrix 的 stopped/零 execute 是冷 audit 03/06 信道不匹配的原始事实，不要求它 complete，也不覆盖它。旧 FEC14 release 仍是 /srv/wfb-ng-fl/releases/heartbeat-1cd6264d5d6b-20261109T080119Z；该旧 profile 七阶段 complete 由 worker 另行核验。

worker 按下列顺序执行（本回合不执行）：

1. 校验 GO、审阅清单 SHA、continuation 证据 SHA 和 ownerfinished，检查其他本地 worker/runner 已退出；检查 frozen bundle、模型与时间界限。
2. 实测旧 FEC14 全十板与九 READY，再执行新 FEC12 的 preflight/stage/prepare/stop/install/start/audit，每阶段十端屏障。start 后先条件轮询实际 native/无线/身份及九 READY，成功才调用发布 audit。首次条件轮询最多30秒；只对实际未恢复的端允许一次标准服务重启，总条件恢复窗口最多60秒。管理、USB、身份错误即停。所有轮询样本保留；失败和最后通过文件分开。绝不改 registry。
3. 七阶段均通过后，仅在 setup 配置一次 30/5。此后的两次模型之间不 configure、不重启 server/native。每次模型前核验全十端存储至少512MiB、有 inode、实际基线与九 READY。
4. 仅执行 trial1、trial2 两次新独立完整模型，不执行 small。run_tag 包含 trial1/2，run_id 不会与容量矩阵探索重复。每方向600秒、overall1290秒、最多2 execute、总预算75分钟，冻结工具不变。现场测试 157/40、DL3/UL4、UFTP 实际12000，闭环恢复149/40。
5. 每轮保留 runner 原始 transfer_verified/cleanup_ok/restore_ok，必须全为 true；归档全端日志、kernel 与重新计算的文件尺寸/SHA，按 runner 的九个精确路径独立核对服务端九份44770302字节文件。执行精确 cleanup 并核实实际 full10 恢复和九 READY。每次完成事实独立写入 completion-fact.json。
6. 全验收仅允许一次 settled 实际复核，不用固定 sleep 掩盖失败。如两次之间需要标准服务重启，验收失败，不补跑、不凑连续通过。任何一轮失败即停止后续模型，保留全部原始失败事实；允许范围内完成精确清理与实际恢复后交父选下一候选。管理失联、USB reset、空间拒绝、身份错误、清理残留触发停止边界，不追加现场测试或 native 诊断修复。
7. 最终实际基线、九 READY、临时 units 退出通过后输出 final-closeout/result.json。成功时持久保留 FEC12+30/5 的 server JSON 与实际 native，绝不回10/10。最终审计包含真实配置、PID/native argv、TUN/links、无线与实际15W限制。实际采样157与恢复149分别记录；恢复行为不计通信成功。

存储仅复用具名精确回收机制。仅01/02低于512MiB才回收；1GiB为代理预算、512MiB为硬门槛。证据从 fec14-remaining、fec12、fec14-matched-completion、本 validation root 四处递归读取，采用已经本地归档日志、逐文件 size/SHA 且整轮 runner 原始三项全通过的 comm 完整模型重复件。失败轮、partial/source/release/install-cache/evidence/训练及其他目录不碰；B2 comm-rsmc20261109T060537Z-g30-d5-model 各端 role/rounds/model.bin 原件保留。每文件先校验本地 canonical、具名 manifest、元数据日志归档，再 stat/hash/re-stat/exact unlink，不用 rmrf；已不存在的路径跳过。BATCHROOT 明确为本目录父级，避免 continuation 的嵌套 ROOT 错位。

本地核验命令为 `python3 selected-validation/verify-local.py`（在证据批次目录运行）。它只读本地文件、编译源码对象、核验 AST 与 SHA，不导入或启动 worker，也不触及现场。review-manifest.json 绑定脚本、文档、计划、硬件来源、profile 原始文件、canonical 与冻结工具。local-verification.json 记录核验结果；这些本地结论不能替代现场验收。

父确认选中该候选后，在本目录创建 GO.json。字段如下，路径必须指向 continuation，SHA 必须按最终文件计算；本回合不创建此文件：

```json
{
  "parent_authorization": "GO SELECTED VALIDATION",
  "selected_candidate": {"fec_k": 8, "fec_n": 12, "grant_ms": 30, "guard_ms": 5},
  "fec14_four_candidates_complete": true,
  "fec14_continuation_complete": true,
  "ownerfinished": true,
  "prior_remote_owners_finished": true,
  "no_active_units": true,
  "final_full10_nine_ready_server10_10": true,
  "review_manifest_sha256": "父审阅后计算",
  "fec14_matrix_path": "continuation matrix.json 的绝对路径",
  "fec14_matrix_path_sha256": "最终 matrix 的 SHA256",
  "fec14_final_result_path": "continuation final-closeout/result.json 的绝对路径",
  "fec14_final_result_path_sha256": "最终 result 的 SHA256"
}
```

只有父 GO 后才可后台启动 `python3 -u worker.py > worker.log 2>&1 &`。父每次监督工具等待不超过30秒，读取 checkpoint.json、matrix.json 和日志，不长阻塞。worker 在阶段与执行期间持续写 checkpoint。不得重启同一 worker；worker-process.json 是禁止重复执行的持久标记。

父负责最终选优、主文档与 Git。本目录不修改根 README、experiment-plan、report-capacity、冻结通信工具或生产代码，不换卡、不更改功耗或管理网络。
