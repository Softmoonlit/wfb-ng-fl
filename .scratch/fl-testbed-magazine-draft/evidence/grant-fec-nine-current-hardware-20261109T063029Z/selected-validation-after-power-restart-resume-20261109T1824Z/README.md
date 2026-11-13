# 供电重启后零执行续跑批次

这是父批次 `selected-validation-after-power-restart` 的平级续跑 root。父批次的 `PAUSE`、`STOP`、审计失败和用户确认的 fl05 供电事件全部保留；父批次明确 `model_executed=false`、`run_ids=[]`，本目录不把任何旧 trial 计入连续通过。

本续跑只重新做完整全十实际准入：fl00、fl01、fl02、fl03、fl05、fl06、fl07、fl08、fl09、fl10，排除且不接触 fl04。fl05 必须以最新启动实测 boot identity、常驻服务和冻结源码/native SHA、15W、实际 iface/MAC/USB/`rtl88xxau_wfb`、iw 149/HT40/index18、GET_RADIO LongGI/MCS4、GET_FEC8/12 和九 READY 通过；服务端对应 GET_RADIO MCS3。其他九端同样必须通过实际身份、硬件、电源、FEC、服务和 READY 审计。

仅当所有全十实测错误都严格是实际 iw 信道偏离 149，且管理、身份、USB、功耗、FEC、BW、type、driver、MAC、native SHA、临时 unit 和路由均正确时，才允许一次标准常驻服务恢复并重新全量审计。配置已经为 `30/5` 时不重启配置；不改 registry、不伪造 READY、不冷切 FEC、不重跑 small 或参数矩阵。

模型仍未执行。只有全十实际基线、九 READY、旧 trial2 具名 unit/路由精确清理和十端存储准入都通过后，才按新 run ID 连续执行两轮；第一轮失败不自动重试，模型方向 600 秒、overall 1290 秒、阶段 4500 秒，runner 原始三 flags、九份独立文件 SHA、exact cleanup 和最终恢复必须全真。
