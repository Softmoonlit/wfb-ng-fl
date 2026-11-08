# 固定10板矩阵：FEC12冷启动profile（仅本地准备）

本轮只完成工具/测试/文档和本地init。没有连接远端，没有执行preflight/stage/prepare/stop/install/start/audit，没有备份live_args或写远端release root，不打断父agent的FEC14组smoke。

## 固定参与集合

发布入口TARGETS与node host白名单均硬编码为：fl00、fl01、fl02、fl03、fl05、fl06、fl07、fl08、fl09、fl10。fl04不参与任何新batch。不是通用任意子集参数；带fl04、缺节点、重复节点、非fl00首位均拒绝。业务端production ALL10/known_clients/slot未改动。

矩阵由父agent执行：guard10，grant[10,20,30,40,60] × FEC[14,12]。本profile仅用于切入FEC12组，不负责运行smoke或矩阵内grant切换。

## 本地冻结身份

```text
--release-root /srv/wfb-ng-fl/releases/heartbeat-1cd6264d5d6b-20261108T124420Z
--release-commit 1cd6264d5d6be660998ce60f3a349063009ecfa7
```

此root尚未在远端创建。state.expected为grant_duration_ms=10、guard_interval_ms=10、fec_n=12，completed=[]。

唯一复用artifact：`/home/fl/projects/wfb-ng-fl/.scratch/heartbeat-restart/native-1cd6264d5d6b/wfb_v6_uplink`。
本批native SHA与before SHA都为 `44d6bbf500f51df4d0b3ae8cd7a53c4a45cb55b09ca66cb5217e7ac75674feb2`。未build新native。source archive严格来自exact1cd而非当前HEAD，额外包含同一artifact；归档SHA、LC_ALL=C readelf证据与node helper SHA已冻结在本目录。helper和测试快照一并保存，不修改任何旧release历史。

## 获得父agent信号后执行的阶段屏障

1. 父agent确认FEC14试验结束、无活动实验unit，并将切组起点source JSON schedule恢复10/10。当前helper不自动改grant/guard；server install只修改FEC，必须保留schedule10/10，否则拒绝。不得在父agent并行14组试验中预先prepare/缓存live_args。
2. 使用本state执行固定10板preflight、stage、prepare。所有10板管理可达、当前native44基线、artifact ABI/ldd、source依赖与interface检查通过后才coldstop；任一节点失败整批中止，不临时缩目标，不动fl04。
3. 全10stop屏障→全10install屏障→全10start。server JSON仅原子改fec_n12，客户端标准模块启动选项仅将--fec-n改12，并记录before/after/expected。actualargs将在真正prepare时采集并冻结，未来audit必须与实际/proc严格一致。
4. 全10audit实际native /proc/exeSHA44、GET_FEC8/12、Python exact1cd source/manifest、UFTP、15W、149/HT40/LGI/idx18与初始DL3/UL4；schedule实际10/10。READY状态单独记录，不能以READY替代GET_FEC。
5. FEC12组结束后父agent另行授权还原14时，以同exact1cd/native44和固定10板重新init新时间root、expected10/10/FEC14；不重用本12profile或旧11板state，不混source，不引入当前HEAD配置文档。

## 本地验证

local-tests.txt：5项artifact/目标门禁测试+11项既有FEC/schedule测试全部通过。test_fixed_ten_targets_reject_fl04_and_arbitrary_subsets实际通过CLI证明错误目标在artifact读取及任何发布动作前拒绝。state/archive/artifact/helper摘要与固定目标集合经独立本地复核。未声明远端预飞已通过。
