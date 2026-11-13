# Fresh 30/5 当前状态

状态：`STOP-rebind-hard-block-usb-enumeration`。唯一一次 30/5 smoke 已失败；model 未执行。随后按用户授权完成一次标准 stop 和一次**仅针对 server USB无线接口**的可逆 driver rebind，但 USB 在 rebind 时无法重新枚举；当前 server/client 常驻服务保持 stopped，不能宣称恢复完成，也不再循环 restart/rebind。

在本阶段停止前，server 曾实际恢复到 `149/HT40/LGI/index18/FEC8/12/30/5`；full admission attempt4 曾通过。当前硬阻断是目标 USB 设备本身已从 sysfs/netdev 消失，不是继续等待 registry 或再启动 client 能解决的状态。

固定集合为 `fl00` + `fl01,fl02,fl03,fl05,fl06,fl07,fl08,fl09,fl10`，`fl04` 始终排除。软件 commit 为 `cccac233291c5a79ca7a66bb2ebb210497ab4071`，native SHA 为 `44d6bbf500f51df4d0b3ae8cd7a53c4a45cb55b09ca66cb5217e7ac75674feb2`，FEC 为 `8/12`，heartbeat `230ms/10s` 未改。

## Smoke 结果

DL 方向是**九个客户端接收完成，另有 server 自身的 publish/verify 记录**，不是“十个客户端”。DL 约 `40.128s`。

UL 使用一次 `600s` 硬截止：`fl01、fl03、fl05、fl06、fl07、fl08、fl09、fl10` 完成，`fl02` 在截止现场为 `786432/1048576` bytes partial。因此 smoke 的 `transfer_verified=false`，首个实质失败后立即 STOP；model 执行数为 `0`。现场见 `smoke-failure-field.json`、`smoke-30-d5/fresh30d5-smoke-20261110/progress.jsonl` 和 `execution-closeout.json`。

runner 的独立 cleanup 对十端返回成功；后续恢复期间没有删除本次 failure partial。最终只读回读到 fl02 当前 comm 目录中 1MiB `model.bin/update.bin` 的 smoke SHA，但这不改变截止时的 `transfer_verified=false`。

## 空间维护

用户授权本次参与者范围内的必要空间维护；此前具名清单漏掉 `fl05` 只是证据索引不完整，不是权限限制。fresh-only manifest 以已有 full `transfer_verified=true/cleanup_ok=true/restore_ok=true`、独立 size/SHA、非 B2、非 partial 的证据扩展了 `fl05/fl10`。

- `fl05`：仅精确清理18个历史成功 duplicate，`368668672 -> 1174474752` bytes。
- `fl10`：仅精确清理12个历史成功 duplicate，`549830656 -> 1087041536` bytes。
- 每端保留 B2 `rsmc20261109T060537Z-g30-d5-model` 的一份 `role/rounds/.../model.bin`，并保留当前 40/5 成功 payload 的一份。
- 未触碰失败 partial、日志/evidence、真实训练、install/APT、未知路径或 `fl04`。

证据入口：`fl05-fl10-storage-candidate-review.json`、`fl05-fl10-storage-delete-plan.json`、`fl05-fl10-storage-maintenance-result.json`。

## 当前共同离线诊断

诊断见 `common-offline-diagnostic.json`。关键事实：

- server native 当前 PID=`6971`，startup args 使用当前 release/native，`--fec-k 8 --fec-n 12 --radio-mcs-index 3 --grant-duration-ms 30 --guard-interval-ms 5 --radio-control-port 9200`，native exe/source SHA 均为目标 SHA。
- server GET_RADIO 为 HT40/DL3/LGI，GET_FEC 为8/12；`iw` 为 Ch149/HT40，显示 `txpower -18.00 dBm`。本项目把它记录为**驱动功率索引18的 iw readback/配置值**，不能解释成物理射频功率 `-18 dBm`。
- server 的 TUN `10.80.0.1/24`、REST `192.168.0.190:8080`、本地 IPC `127.0.0.1:9090`、控制 UDP `9001`、native radio control `127.0.0.1:9200` 均在监听；fresh 临时 unit 和两条专用组播 route 为空。
- server 接口计数为 RX errors=0、TX errors=0，TX dropped=`25355`；native 当前 control 协议只暴露 GET_RADIO/GET_FEC，没有可读取的 GRANT/READY 发射计数器。last100 中没有可计数的 native GRANT/READY 事件，不能把缺少日志当作“未发送”证明。
- server daemon last100 的当前17:56启动记录显示网卡149/index18、native启动、TUN和9001/9090 bind成功；其中较早的历史 traceback 是 `WEB_ADDRESS_NOT_ASSIGNED`，不是当前启动阶段。kernel 还记录17:45 USB无线设备断开/重新枚举，这一事实保留但未单独归因。
- 历史成功 startup 与当前 startup 的 release/native/link-id/uplink-stream/downlink-stream/FEC/bandwidth/MCS 结构一致；当前仅调度字段为30/5。当前启动没有把持久配置里的 `radio_txpower_dbm=14` 作为 native `--radio-txpower-dbm` 参数传入；实际 native/iw readback 是驱动索引18。
- 持久 `server.json` 的 `radio_txpower_dbm=14`、`uftp_rate_kbps=15000` 是配置字段观察值。实验 smoke 内 runner patch 使用实际 UFTP12000；标准恢复后 registry 显示 UFTP15000。两者是不同阶段语义，未把持久字段误报为 smoke 实际值。
- 所有客户端 daemon/native 曾以相同 release/native/link-id=`7669206`启动，但在 server 重启后共同失去 heartbeat 并进入 hunt，当前实际频道分散在149/153/157。现有证据支持“共同的 server 下行/控制面或启动时序故障需要继续排查”，不支持把原因归给 fl02 单端；5分钟内未取得唯一根因。

## 历史 Attempt

`full-admission-attempt1.json` 和 `attempt2` 是 fresh-only probe 自身脚本错误，未产生远端实验副作用；`attempt3` 是旧状态下 fl01 不可达、fl05空间不足；`attempt4-rejoin-and-storage` 在用户报告 fl01 重新启动并完成空间维护后通过，已被当前 smoke 结果取代。所有历史 attempt 保留，只作为时间线，不作为当前态。

最终执行/恢复事实见 `final-closeout.json`、`recovery-final.json`。最终没有 model DL/UL、九份 model SHA 或模型恢复结果。`evidence-integrity.json` 最终校验通过。

## USB Rebind Hard Block

在保存 active job、临时 unit、route、目标 driver/device/MAC、USB480 和 wired management `.190/enP8p1s0` 后，已标准 stop 全部 clients 与 server，确认 native 全部退出。仅对 `rtl88xxau_wfb` 的 `1-2.3:1.0` 做一次 unbind/bind；没有卸载模块、修改 autosuspend/功耗或触碰管理接口。

rebind 返回 `No such device`。kernel 随后记录 firmware changed、USB disconnect、`error -71`、descriptor/configuration 读取失败和 `unable to enumerate USB device`，目标 `wlxfca386b38672` 已从 sysfs/netdev 消失。server/client services 保持 stopped；这需要物理/USB恢复，任务内不再循环 restart/rebind。完整证据见 `rebind-final.json`、`rebind-operation.json`、`rebind-after.json`。
