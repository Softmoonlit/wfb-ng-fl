# 新 USB 连接批次 30/5 STOP 收尾

状态：**STOP，已恢复待命，未执行模型**。

本 root 是用户物理重插后的独立新连接批次。fl00 目标网卡以 MAC `fc:a3:86:b3:86:72`、netdev `wlxfca386b38672`、driver `rtl88xxau_wfb` 重新枚举，当前 sysfs 接口路径为 `1-2.4:1.0`，历史路径为 `1-2.3:1.0`，USB speed `480`。这被记录为新 USB 连接批次，不作严格同口比较。成功枚举时间边界后 kernel 没有新的 `error -71`、USB disconnect、descriptor failure 或 reset，因此本次失败不归因于 USB。

固定 server `fl00`，clients 为 `fl01,fl02,fl03,fl05,fl06,fl07,fl08,fl09,fl10`，fl04 排除。commit 为 `cccac233291c5a79ca7a66bb2ebb210497ab4071`，native SHA 为 `44d6bbf500f51df4d0b3ae8cd7a53c4a45cb55b09ca66cb5217e7ac75674feb2`，FEC8/12、heartbeat 230ms/10s、149 基线、157 测量、HT40/LGI/index18、DL3/UL4、实际 UFTP12000、30/5 均保持不变。未改软件、native、硬件、USB 功耗或管理网。

## 执行事实

1. server 先启动，native/API ready；随后 9 client 启动。full admission 通过：实际 identity/source/native/UFTP、15W、CPU1497600、GPU612000000、EMC cap2133000000、EMC unlocked、radio/FEC、旧 unit/route 和 space hard gate 均通过。最终恢复审计再次通过，9 client 为 `READY/IDLE`，fl04 保持 OFFLINE，active job 为 null。
2. 首次旧 run ID `fresh30d5-smoke-20261110` 在 PREPARE 因远端已有历史 `/srv/wfb-ng-fl/comm-fresh30d5-smoke-20261110` 报 `FileExistsError`。该工具 ID 冲突发生在 0 payload、0 DL、0 UL 之前，已独立复制保存于 `smoke-30-d5-IDerror/`；旧远端目录和 payload 未删除、未覆盖，不计参数性能或真实传输 retry。
3. 修正后唯一实际 run ID 为 `replug30d5-20261110T103150Z-smoke`，本地 output 为 `newsmoke-30-d5`，并在 prepare 前对本地 output 与十端新 comm 路径做了不存在断言。实际 smoke 只执行一次，执行到 radio verify gate，**未开始 publish、未开始 submit，实际传输 payload bytes=0**；model 执行数为 0。
4. 157 radio transaction session `switch_e4d70c46929b4848bfad4fcd3f62ccc7` 已 finalized，但 verify 阶段 registry gate 拒绝目标2。现场快照见 `replug30d5-20261110T103150Z-smoke-failure-field-exact-uftp-kernel-files.json`：目标2 boot ID `8c60fa57-ef3e-47a7-a433-f91fb4d8917b`、daemon PID `7827`、native PID `7844`、native SHA 正确；失败时 `reported_state=HUNTING`、`readiness=CONNECTING`、`current_channel=157`、`error_code=null`，selection blockers 为 `NODE_NOT_READY` 和 `NODE_NOT_IDLE`。server PID `8103`、native PID `8116` 未变，server 当时仍 `IDLE`、active job null。该事实说明是 149->157 射频事务后的目标2 reconnect/hunt readiness gate failure，不能据此宣称 USB 或断电根因。
5. 现成 `recover.py --restore` 已完成 baseline RF restore，restore session `switch_40ffeeab57c64579b09f35cdc6ec930c` finalized，`rf_response_ok=true`，恢复为 `149/HT40/LGI/index18/FEC8/12`。helper 的 `ok=false` 仅因其设计不做最终 audit；随后标准 full admission 复核通过，9 端 READY/IDLE、所有目标 iw、active job null 均恢复。所有本批临时 unit inactive，所有 executor 已退出。

## 边界与结论

本批真实 smoke=1 次 admission gate failure，payload=0；model=0；没有自动 retry。旧 40/5 结果与旧 30/5 两次通过（307.240s、302.049s）均保留，不与本批合并，也没有被本批取代。旧失败、partial、日志、evidence、运行安装源均保留；本批没有删除远端历史 payload。

最小下一步不是重跑矩阵，而是另立授权批次，专门诊断目标2在 149->157 radio transaction 后的 HUNTING/CONNECTING 恢复边界，并保留相同 session/READY/boot/PID 证据。当前批次不再启动 smoke/model，不执行 rebind 循环。

证据入口：`final-closeout.json`、`progress-status.json`、`evidence-integrity.json`、`full-admission.json`、`replug30d5-20261110T103150Z-smoke-failure-field-exact-uftp-kernel-files.json`、`newsmoke-30-d5/replug30d5-20261110T103150Z-smoke/progress.jsonl`、`smoke-30-d5-IDerror/`。`evidence-integrity.json` 为本 root 83 个非缓存证据文件的 SHA-256 manifest。
