# 固定10板矩阵：FEC12冷启动记录

本profile最初仅在本地准备，未连接远端。2026-11-09在当前硬件FEC14补测完成、完整恢复且无活动试验后，已顺序执行固定十板preflight／stage／prepare／stop／install／start／audit，各阶段全十通过；各端实际GET_FEC=8/12，源码及native摘要不变。逐端记录见本目录对应阶段JSON，完整编排与后续矩阵见[当前硬件FEC12记录](../../fl-testbed-magazine-draft/evidence/grant-fec-nine-current-hardware-20261109T063029Z/fec12/README.md)。

## 固定参与集合

发布入口TARGETS与node host白名单均硬编码为：fl00、fl01、fl02、fl03、fl05、fl06、fl07、fl08、fl09、fl10。fl04不参与任何新batch。不是通用任意子集参数；带fl04、缺节点、重复节点、非fl00首位均拒绝。业务端production ALL10/known_clients/slot未改动。

矩阵顺序为授权／调度间隔ms：10/10、20/10、30/10、40/10、60/10、20/5、30/5。本profile只负责切入FEC12组，smoke和矩阵内授权调度切换由独立通信编排执行。

## 本地冻结身份

```text
--release-root /srv/wfb-ng-fl/releases/heartbeat-1cd6264d5d6b-20261108T124420Z
--release-commit 1cd6264d5d6be660998ce60f3a349063009ecfa7
```

此root已在全部当前十板创建。state.expected为grant_duration_ms=10、guard_interval_ms=10、fec_n=12，completed已包含全部七阶段；这是冷切换结束时的基线，后续逐档调度和恢复以当前矩阵审计为准。

唯一复用artifact：`/home/fl/projects/wfb-ng-fl/.scratch/heartbeat-restart/native-1cd6264d5d6b/wfb_v6_uplink`。
本批native SHA与before SHA都为 `44d6bbf500f51df4d0b3ae8cd7a53c4a45cb55b09ca66cb5217e7ac75674feb2`。未build新native。source archive严格来自exact1cd而非当前HEAD，额外包含同一artifact；归档SHA、LC_ALL=C readelf证据与node helper SHA已冻结在本目录。helper和测试快照一并保存，不修改任何旧release历史。

## 切组阶段屏障与本次执行契约

1. 父agent确认FEC14试验结束、无活动实验unit，并将切组起点source JSON schedule恢复10/10。当前helper不自动改grant/guard；server install只修改FEC，必须保留schedule10/10，否则拒绝。不得在父agent并行14组试验中预先prepare/缓存live_args。
2. 使用本state执行固定10板preflight、stage、prepare。所有10板管理可达、当前native44基线、artifact ABI/ldd、source依赖与interface检查通过后才coldstop；任一节点失败整批中止，不临时缩目标，不动fl04。
3. 全10stop屏障→全10install屏障→全10start。server JSON仅原子改fec_n12，客户端标准模块启动选项仅将--fec-n改12，并记录before/after/expected。actualargs将在真正prepare时采集并冻结，未来audit必须与实际/proc严格一致。
4. 全10audit实际native /proc/exeSHA44、GET_FEC8/12、Python exact1cd source/manifest、UFTP、15W、149/HT40/LGI/idx18与初始DL3/UL4；schedule实际10/10。READY状态单独记录，不能以READY替代GET_FEC。
5. FEC12组结束后父agent另行授权还原14时，以同exact1cd/native44和固定10板重新init新时间root、expected10/10/FEC14；不重用本12profile或旧11板state，不混source，不引入当前HEAD配置文档。

## 本地验证

local-tests.txt保留初次本地准备的验证：5项artifact／目标门禁测试及11项既有FEC／schedule测试通过；错误目标在artifact读取和发布前拒绝。本次切组前又核对state／archive／artifact／helper摘要与固定目标集合，远端预飞与后续六阶段结果分别存于本目录逐端JSON；冷启动通过不替代后续模型闭环验收。
