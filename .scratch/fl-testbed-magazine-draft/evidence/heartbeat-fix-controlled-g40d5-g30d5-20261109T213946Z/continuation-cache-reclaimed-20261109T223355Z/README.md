# 40/5与30/5受控比较缓存维护后 continuation

状态：STOP（执行完成但恢复不完整；40/5模型通过，30/5模型submit超时，模型已用满2次）。

本 continuation 建立于安装缓存维护完成后，旧 root 和旧冻结材料只读保留。固定服务端为 `fl00`，客户端为 `fl01`、`fl02`、`fl03`、`fl05`、`fl06`、`fl07`、`fl08`、`fl09`、`fl10`，`fl04` 排除。软件 commit、native SHA、FEC8/12、230ms ACK等待、10s持续失活门限、硬件和15W/频率约束均不变。

固定射频为149基线、157测量、HT40、LGI、驱动功率索引18、DL MCS3、UL MCS4、UFTP实际目标12000。旧30/5两轮307.240/302.049秒只作历史参考，不并入本批，也不宣称统计最优。

## 执行顺序

1. STOP期间完成全十端只读准入：loaded identity/native唯一性、40项安装/源码 SHA、CPU/GPU/EMC/nvpmodel/jtop、`iw`、GET_FEC/GET_RADIO、九端READY/IDLE、旧unit/路由、空间至少512MiB并以1GiB级余量复核。
2. 解除STOP后只执行一次40/5 1MiB smoke gate。
3. gate通过后执行一次40/5等量模型；下行和上行各600秒硬截止。
4. 成功恢复完整集合后执行一次30/5同版本等量模型控制。
5. 首个实质失败立即STOP并完整恢复；不自动重试、不删端；模型最多2次 execute。

## 维护事实

fl01的9个wheel/deb已在父root外专用 cache archive 中完成原字节归档、SHA复核和精确unlink；fl06按成功完整证据删除17份重复model/update至1GiB级空间；fl02最终确认 `/382_aaaadbca` 为历史失败的PackageKit update-packages记录，当前无GetTransactionList对象、无install-cache fd、无apt/dpkg进程和锁，因此在不停止PackageKit的情况下完成同样的安装缓存归档/精确unlink，服务保持active/static。维护证据均由父root引用，未复制大包进入本root。

当前只保留STOP，不自动开始执行。心跳预算研究仅记录为后续正式接缝/回归/发布事项，230ms本批不动；不复测10/10、18/2，不复活历史捕获工单。

## 本批结果与恢复边界

40/5的1MiB smoke gate通过；随后40/5等量模型一次完整通过，九端收发、cleanup、restore和final audit均为真。实测通信时间如下；约 `416.455s` 是执行器总耗时，包含编排，不能作为上下行通信之和。

| 项目 | 下行秒 | 上行秒 | 完整闭环 |
| --- | ---: | ---: | --- |
| 40/5，1MiB | 4.603 | 8.914 | 通过 |
| 40/5，模型 | 50.824 | 348.669 | 九端通过，通信合计399.493秒 |
| 30/5，模型 | 47.556 | 600秒截止未全员完成 | 失败，仅六端完成 |

30/5已完成的六端中最晚提交约233.787秒，这不是九端上行完成时间。

切回30/5后，同版本等量模型只执行一次。下行完成，但上行在600秒submit预算内未达到九端最小配额，runner返回失败；可见partial为fl02 `23330816` bytes、fl06 `36634624` bytes、fl10 `15335424` bytes。没有自动重试，模型执行总数恰为2，未追加第三次。

本轮没有建立40/5优于30/5的结论：40/5只有一次完整样本，30/5同期样本伴随fl06/fl10管理网失联，失联原因尚未确认；旧30/5两次完整通信307.240/302.049秒仅作历史参照。仍保留30/5为已通过两次完整闭环的基线，不因本次失败替换为40/5。未开始230ms预算实验，10秒失活门限也保持不变。

失败后的独立cleanup在fl00、fl01、fl02、fl03、fl05、fl07、fl08、fl09返回成功；fl06、fl10管理网均为No route to host。随后按标准顺序对全部可达客户端先restart、再restart server，实际读回149/HT40/LGI/索引18/FEC8/12/30/5；PackageKit保持active/static，server当前已回149、active job为空。fl06/fl10仍只有一次bounded ping/ARP/SSH不可达证据，其临时unit和最终READY无法核实，不能宣称完整九端恢复。详细结果见 `final-closeout.json`、`comparison-result.json` 和 `recovery-final.json`。STOP保留，后续不得在此现场继续模型。
