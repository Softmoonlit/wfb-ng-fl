# 多客户端重启后的零执行恢复续跑

本目录是第三个独立恢复续跑 root。父批次 `selected-validation-after-power-restart` 和 `selected-validation-after-power-restart-resume-20261109T1824Z` 的 stopped、STOP、供电事件、管理失联事实全部保留；两者均明确 `model_executed=false`、`run_ids=[]`，不覆盖、不拼接连续通过。

用户确认多个客户端管理网因重启恢复。本批次以本次人工恢复为新准入条件，在最多 180 秒内分段检查全十端管理可达和启动身份，然后完整核验冻结 source/native、15W、接口/MAC/USB/driver、GET_FEC8/12、GET_RADIO HT40 LongGI DL3UL4、iw index18、READY，以及旧 trial2 具名 unit 和两条专有路由。fl04 始终排除且不接触。

仅当十端管理、源码/native、硬件、电源、FEC、driver、MAC、USB、BW/type/txpower 等全部通过，剩余错误严格只有实际信道偏移时，才允许一次标准常驻服务恢复到149；恢复后必须重新完成十端实际审计和九 READY。若有任何管理、USB、身份、功耗、FEC、残留或空间异常，立即停止留证，不伪造 READY、不改 registry、不冷切 FEC、不重编译安装、不改变超时。

模型仍未执行。准入通过后才执行两次新的独立 `44770302` 字节模型闭环，方向600秒、overall1290秒、阶段4500秒，最多2次；每轮必须有 runner 三 flags 全真、九份独立服务端 size/SHA、exact cleanup、全十恢复和九 READY。两轮之间不重启标准服务凑连续通过；第一轮失败不重试、不拼接旧 trial。
