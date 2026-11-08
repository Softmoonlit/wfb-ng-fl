# 1cd6264 新native统一发布结果

完整commit：1cd6264d5d6be660998ce60f3a349063009ecfa7。

```text
--release-root /srv/wfb-ng-fl/releases/heartbeat-1cd6264d5d6b-20261108T115743Z
--release-commit 1cd6264d5d6be660998ce60f3a349063009ecfa7
```

父agent提供并授权的唯一artifact为 `/home/fl/projects/wfb-ng-fl/.scratch/heartbeat-restart/native-1cd6264d5d6b/wfb_v6_uplink`，SHA256 `44d6bbf500f51df4d0b3ae8cd7a53c4a45cb55b09ca66cb5217e7ac75674feb2`。未在节点重新build，未复用旧ad334作为新artifact。旧native基线显式为ad3340e51eecc6e5691a6edb84f26f3a262b518c3555497df62ea6f7865e1e56。

## 软件发布与审计事实

全11含fl03管理preflight通过；统一artifact先stage，再全11prepare校验SHA、LC_ALL=C readelf的ELF64/AArch64/little endian/解释器、ldd -v依赖无not found，完成旧binary备份。全11prepare通过后才stop，全部stop通过后才install，全部install通过后统一start。

冻结源码安装脚本通过DESTDIR生成暂存install-image；helper部署Python和其它标准安装文件，保留已有node/server JSON，再使用同目录临时文件+fsync+os.replace原子替换真实native。没有改动冻结commit的安装脚本，也没有wrapper、按节点build或隐式旧native fallback。备份在各节点release root/backup/wfb_v6_uplink，native-before.json记录旧SHA、新SHA、commit和新artifact ABI。

首次audit fl01/fl03/fl05/fl09的iw处于启动寻频未满足149，完整失败结果保存在audit-first-attempt；未调参或重启，随后第二次全11audit通过。实际/proc/native exe SHA全部44d6...，GET_FEC全部8/14；server18/2，149/HT40/LGI/idx18，DL3/UL4，15W，UFTP与备份摘要相同。Python安装文件与冻结源码/manifest匹配，build identity为确切1cd6264完整commit。identity-fl*.json另核对旧binary备份、源artifact/安装新SHA及配置内容不变。实际iface与preflight保持一致，包括fl02现有fc卡与fl04现有bcec卡。

Python来源是实际proc身份、隔离解释器映射和源码SHA证据，不声称直接读取resident sys.modules。

## 有界READY观察：未达到全10，不宣称通信通过

初始状态fl04/fl08 OFFLINE。ready-observation-30s.json记录t0/t10/t20/t30四次观察：fl04持续OFFLINE，fl09初次CONNECTING后恢复READY，fl08恢复READY。最终9个client READY，fl04 OFFLINE。软件审计通过不代表全链路可用，更不证明此软件修复能解决网卡问题。没有等待无界、重置全局模块、改power/USB、换卡、额外射频事务或传输实验。

state.json记录七阶段全部完成；final-verification.json明确software_audit_all_eleven_passed=true、communication_success_claimed=false并保留最终readiness。父agent可据此继续讨论受控诊断方案，本任务不自动执行后续动作。

## helper正式变化与本地测试

所有新batch的init强制--native-artifact和--native-before-sha。artifact SHA、来源路径、ABI和包含artifact的source archive冻结；stage发送同一artifact，prepare验证再全停，install原子替换，audit以state新SHA检查实际/proc/exe。历史state不迁移，不支持隐式reuse。

4项新增artifact本地测试+11项既有schedule/FEC测试均通过（local-tests.txt），覆盖required artifact、原子替换保留旧open inode、错误SHA拒绝且保留旧binary、LC_ALL=C ABI依赖缺失拒绝。只提交helper、自有测试和本轮证据，不混父其它工作。
