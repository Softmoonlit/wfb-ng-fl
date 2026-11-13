# fl02 供电重启后的独立验收续跑

父 root `selected-validation-after-storage-evidence-continuation-20261109T1840Z` 保持 stopped，不覆盖其 trial1 原始 flags、DL/UL、fl02 cleanup SSH 超时、collect 失败、九份服务端 verify、partial、日志和 STOP。父 trial2 为零 execute。本目录不把父 trial1 拼入新的连续两次。

用户确认 fl02 断电后已重启，本事件按基础设施供电事件记录，不归因 FEC/调度。启动准入最多 180 秒分段检查 fl02 管理、boot identity、冻结 source/native、15W、iface/MAC/driver/USB、GET_FEC8/12、GET_RADIO LongGI/HT40/UL4、iw149/HT40/index18、READY；之后完整核验固定十端 actual、九 READY、服务 JSON/argv、旧具名 unit 和专有路由。fl04 不接触。

只有全十管理/身份/FEC/功耗/driver/BW/type/txpower 均正确且唯一问题是信道157偏移时，才允许一次标准常驻服务恢复到149并重新全组核验；不 cold redeploy/install/compile，不改 registry，不删除端。配置保持 FEC12、30/5。

存储门槛仍为全十至少512MiB，目标1GiB；仅按已审 storage.py 的完整成功轮证据，在 fl01/fl02 对逐文件 exists、日志元数据归档、re-stat/hash、exact unlink。bases 必须包含 fec14-remaining、fec12、fec14-matched-completion、旧 selected-validation 和各新 root；B2、失败轮、partial、日志、source、evidence、真实训练目录保护不变。

准入和存储通过后才执行两个新的独立模型轮次。每轮方向600秒、overall1290秒、阶段4500秒；runner 原始 transfer/cleanup/restore 全真、九份独立服务器 size/SHA、exact cleanup、全十恢复、九 READY 才算通过。第一轮失败立即停止，不自动重试或以第二轮凑数；两轮间不重启标准服务凑连续通过。
