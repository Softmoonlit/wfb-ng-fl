# FEC14 同硬件四档补齐审阅稿

状态：仅本地准备，等待父明确 GO。本回合没有 SSH、live HTTP、worker 启动或发布阶段执行。另一 actor `fec12-current-hardware-matrix` 仍为唯一远端操作者。生产源码、冻结 bundle、兄弟 actor 文件、根 README、experiment-plan 和 report-capacity.py 均未修改。

新 profile 在本目录 `heartbeat-1cd6264d5d6b-20261109T080119Z/`。通过原 `.scratch/heartbeat-restart/heartbeat-1cd6264d5d6b-20261108T124420Z/release-online.py init` 仅本地创建，再移入本目录并复制原发布入口及 helper。命令和原输出见 `profile-init-local.json`。expected 为 10/10/FEC14，`completed=[]`。source 固定 `1cd6264d5d6be660998ce60f3a349063009ecfa7`，native-before/native SHA 均为 `44d6bbf500f51df4d0b3ae8cd7a53c4a45cb55b09ca66cb5217e7ac75674feb2`，artifact 为 `.scratch/heartbeat-restart/native-1cd6264d5d6b/wfb_v6_uplink`。正常 release 为 `/srv/wfb-ng-fl/releases/heartbeat-1cd6264d5d6b-20261109T080119Z`。不编译 native，不替换库、驱动、硬件或功耗管理网配置。安装脚本中的 `make build_identity` 仅写构建身份。

候选固定按以下顺序，每格最多一次 execute：

| 顺序 | grant/guard 毫秒 | 先执行 | 通过后执行 |
| --- | --- | --- | --- |
| 1 | 10/10 | 1MiB 小文件 | 44770302 字节模型 |
| 2 | 20/10 | 1MiB 小文件 | 同一模型 |
| 3 | 30/10 | 1MiB 小文件 | 同一模型 |
| 4 | 20/5 | 1MiB 小文件 | 同一模型 |

本阶段最多 8 execute，不重跑当前 FEC14 的 30/5 B2、40/10、60/10，不新增档位、不选优、不进行额外连续验收。小文件须九端收发 size/SHA、精确 cleanup、全十端 actual 恢复和九 READY 全通过才进入同档模型。通过的小文件不重发；失败的小文件跳过该档模型。方向 600 秒、overall 1290 秒，UFTP 实际 12000kbps。157 测试，149 准备与恢复；HT40/LGI、index18、DL3/UL4、15W，CPU scaling_max 1497600、GPU 612M、EMC nvpmodel cap 2133M、online 0-5、mrq 0。模型 SHA 为 `4b55cee2f0bdcfbbf610167a505efee0e2c50e3306fed02bda3613807c234e96`，bundle SHA 为 `ab8697fe4573ac3c1088dd11022a1fd75a25cf13752d2717de02e6aa2e625e9e`。

完整 MAC/interface 复制自 `fec12/hardware-from-actual.json`，sysfs/USB 映射从该文件引用的同一份 actual 证据提取，保存在本目录同名文件。00 固定 `fc:a3:86:b3:86:72` USB480，10 固定 `fc:22:1c:10:01:19` USB5000，05 固定 `fc:22:1c:10:01:57`；所有节点按实际 sysfs/USB 映射核验，驱动为 `rtl88xxau_wfb`。不新增 GET_RADIO 字段断言，保留 FEC12 已纠正的协议判据。

启动前必须父明确 GO 且 FEC12 actor 已结束，`fec12/matrix.json` 为 complete，`fec12/final-closeout/result.json` 记录 FEC12、server10/10、full10/9READY。随后运行时再次检查本地无其他 worker/runner，独立审计旧 FEC12 `/srv/wfb-ng-fl/releases/heartbeat-1cd6264d5d6b-20261108T124420Z` 的全十端 actual、149、10/10、无 active job/units 和九 READY。冷切前基线单独放在 `pre-cold-switch-old-fec12/`。新 profile 严格执行 `preflight → stage → prepare → stop → install → start → audit`，各阶段十板全部通过后才下一阶段。完成后十板一致 GET_FEC 8/14、新 release actual 全十端和九 READY 才可文件测试。后续正常恢复均要求 FEC14/新 profile。

保留 runner 原始 `original_runner_final_flags`，首次独立结果为 `immediate_independent_restore_ok/immediate_independent_ready_ok`，最终结果为 `independent_exact_cleanup_ok/independent_restore_ok/independent_ready_ok`，不覆盖首次瞬时 false。最多一次 settled 检查、必要时一次标准恢复，不修改 registry。普通 transfer 失败但精确 cleanup、full10 actual、九 READY 全通过可进入下一不同候选；management 失联、USB reset、cleanup 残留或 actual 恢复不齐停止后续自动变更。日志或进度发现 UFTP 8、Not enough disk、ENOSPC 等基础设施故障，在清理与独立恢复后停止矩阵，不进入下一档。

每次模型前对全十端 statvfs，硬门为可用空间 ≥512MiB 且有可用 inode。只有 01/02 空间不足时允许精确回收已归档完整重复模型，新低磁盘节点在 01/02 外则停止。回收证据源覆盖 `fec14-remaining/**/*exact-uftp-kernel-files.json`、`fec12/**/*exact-uftp-kernel-files.json` 和本目录递归同类文件，已删路径跳过。保留各端 B2 `comm-rsmc20261109T060537Z-g30-d5-model` 的 role/rounds/model.bin 原件；逐文件 size44770302/SHA、本地 canonical SHA、具名 manifest、日志元数据归档读回、远端逐文件 hash/re-stat 后精确 unlink。失败 partial、日志 JSON、source/release/install-cache/evidence/真实训练均保留；禁止目录删除。1GiB 是代理执行预算目标，512MiB 是准入硬门。仅归档日志元数据与证据，不重复拷贝模型二进制。

GO 后本阶段总预算 110 分钟，每次执行前保留完整执行与恢复预算，不足则停止并收尾。后台 worker 持续记录 checkpoint/matrix、模型 phase、各端完成时刻和上传进度，轮询 2–5 秒。父监督工具每次 wait ≤30 秒。结束目标为 server10/10、149、FEC14、新 profile、full10 actual、九 READY；management/USB/cleanup 等硬停则保留证据交父处理，不自行继续变更。

父审阅并明确 GO 后，才可创建本目录 `GO.json`，内容为：

```json
{"parent_authorization":"GO FEC14","fec12_actor_finished":true,"scope":"cold-switch-and-four-fec14-candidates"}
```

然后以后台进程运行本目录 `worker.py` 并把 stdout/stderr 写入具名日志。本回合不创建 GO 文件，不启动进程。若审阅后修改输入，应重新运行 `verify-local.py` 并复核 `review-manifest.json`；worker 在任何 live 操作之前核验全部审阅摘要，拒绝已存在 worker-process 的重启。

父审阅入口为 `review-manifest.json`、`plan.json`、`local-verification.json`，实现文件为 `worker.py`、`driver-base.py`、`helpers.py`、`storage.py`。`verify-local.py` 仅核验语法、AST、嵌入代码、profile/source/native/bundle/model 摘要、硬件来源和四档计划，不导入 worker 或 driver，不访问 HTTP/SSH，不执行发布阶段。离线故障识别核验见 `offline-policy-verification.json`，不代表任何远端预飞或传输已通过。
