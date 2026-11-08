# fl08恢复后的20/5新批次：fl06管理失联停止

fl08用户断电重启已验收：新boot ID c9ee4fde-f987-4bb3-9ae7-b9377fe4442c，旧20/5 unit inactive。本批初始十板实际149/HT40、Long GI、索引18、MCS3/4、GET_FEC8/14、15W、1cd源码与native44审计通过；完整九端READY/IDLE准入通过。

新唯一run-id p8-fec14-g20-d5-smoke 在prepare时只有fl06未回应。启动前内置的监督器在单节点未应答>=60秒且一次SSH失败时生效：约69秒时向经cmdline核对的runner172679发SIGINT。没有触发acquire_radio/publish/submit，DL/UL均0字节。这是管理基础设施失败，不形成射频或调度参数结论，不假定fl06断电。

初始清理时共有九板可达（含server及fl07），runner finally精确回收这九板的本轮自有unit；当时仅fl06清理SSH失败、unit状态未知。后续closeout又发现fl07管理SSH超时，因此最终可达数降为八板，不能沿用初始九板可达的结论。原始transfer_verified/cleanup_ok/restore_ok均false保留，独立完整恢复未通过。STOP标记存在，20/5 model及30/5 small/model均未启动；server停在20/5配置，无新增射频事务。冻结三文件bundle不变；未操作共享Git/index。

证据：matrix.json、checkpoint.json、result.json、fec14-g20-d5-smoke/controlled-prepare-abort.json、live-status-events.jsonl、p8-fec14-g20-d5-smoke/progress.jsonl、after-audit.json。运行中的STOP/PAUSE由实际driver在边界检查；prepare管理失联监督器在源码启动前安装，未热改工具。

## 最终收尾状态

server已通过标准restart恢复10/10，配置149/HT40/FEC14不变。可达fl00/01/02/03/05/08/09/10八板已确认本轮exact-ownedunit inactive，并通过实际149/HT40、Long GI、索引18、MCS3/4、GET_FEC8/14、1cd/source/native44及15W审计。

fl06单次ping2/2丢失、SSH No route to host、邻居INCOMPLETE，原因未知；收尾时fl07单次SSH连接超时，原因也未知。两端本轮unit当前状态未知（fl07仅有初始清理时inactive的历史证据，不能代表closeout时仍可验证），完整九客户端恢复验收未齐，不再等待或重试。原始transfer_verified=false、cleanup_ok=false、restore_ok=false全部保留，后续八板partial closeout不覆盖这些flag。09/08历史断电是用户确认原因，与06/07新管理故障分开记录。详情final-closeout/result.json。
