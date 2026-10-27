# 701963a native同构建统一及85秒idle观测

本轮按用户再次授权执行：保留四端Python提交701、确认ABI与构建来源后统一native，再做85秒纯被动观察。没有部署仅工单文本提交b771366，没有训练、标定、人工调RF/MCS/功率、改timeout或硬件操作。

## 原差异与来源证据

四端native源码归档均为 `701963ac254261c31c56a074d06966a9217346f6`，归档SHA-256 `11110cc3d5a32a28593c46bb13ca6505826d6bdbf202d83c611edaf3c353cfe2`，四端native源文件SHA集合一致。每端安装native与本端 `/srv/wfb-ng-fl/deploy-701963a/source/wfb_v6_uplink` 的SHA相等。

原SHA为fl00 `f7c73f41...`、客户端`b786e230...`。该程序是同一多角色程序，不能以server/client角色解释差异。已取得的实际构建环境差异：

| 项目 | fl00 | fl01/02/04 |
| --- | --- | --- |
| GCC包修订（.comment及g++） | 11.4.0-1ubuntu1~22.04.2 | 11.4.0-1ubuntu1~22.04 |
| 原ELF Build ID | 82b57ee251cc5d5bbf7284fc9f7e2819a881d0b0 | 6c5eb1973102a7bf0d5046a204f1c425334b3797 |
| 原native SHA | f7c73f41b730ddb3547e1730b5a5b7b40a55dd7a8e3e5f265dcc6563c6c432b1 | b786e230194a5ed0cb4ce656692a01b4954f12bbb35058e28f36f34944bb5106 |

这证明编译环境/对象产物不同，不声称已逐字节归因全部差异。`flXX-abi-before.json`保留编译器、ELF、ldd、object/source/archive SHA。完整fl00编译/链接flags在上级 `fl00-backup-build.txt`，包含 `-DWFB_VERSION='"0.0.0-701963ac"'`。strings检索没有找到独立发布版本字符串，只找到RX_BATCH/GRANT_SEQ/CLIENT_LOOP格式串，故不能把strings单独当作提交身份；原始检索为 `flXX-strings-version.txt`。

## 唯一canonical制品与安装

选用已核实来源的单次fl00冻结701构建产物 `/srv/wfb-ng-fl/deploy-701963a/source/wfb_v6_uplink`，没有重新编译、没有从dirty源码构建。`canonical-build-manifest.json`固定原archive、源码/object摘要、编译器/ELF及canonicalSHA：

```text
f7c73f41b730ddb3547e1730b5a5b7b40a55dd7a8e3e5f265dcc6563c6c432b1
Build ID 82b57ee251cc5d5bbf7284fc9f7e2819a881d0b0
```

四端AArch64/glibc2.35、ELF解释器一致。先将同一文件暂存到各客户端 `/tmp/wfb08-native701-unified`，各端 `readelf`/`ldd -v`均能解析所需库及符号版本，没有not found。原始为 `flXX-canonical-abi.txt`。这只证明加载兼容前提，不证明无线功能已通过。

确认server IDLE、无active job后，逐端备份旧native至 `/srv/wfb-ng-fl/deploy-701963a/native-unification/native-before`；四端daemon全部停止且无native/UFTP残留后，安装同一canonical文件并启动。Python源码、JSON/drop-in及无线/期限配置未写入。动作日志 `flXX-stop/install/start.txt` 和 `installation.log`。

安装后 `/usr/bin/wfb_v6_uplink` 与四端 `/proc/nativePID/exe` 的SHA均严格相同；Python build identity仍为701，按实际daemon cwd `/` 和实际environ运行find_spec，来源均为 `/usr/local/lib/python3.10/dist-packages`，无PYTHONPATH dirty shadow。核验在 `flXX-runtime-after.json`、`unified-runtime-summary.json`。

部署后daemon PID为fl00 233198、fl01 37355、fl02 38743、fl04 37508；native PID233253/37373/38760/37525。四端active、NRestarts0。观测前API暂时全员READY，serverIDLE/no active job。临时READY不作为持续稳定的证明。

## 85秒观察

完整原始记录和指标见 [passive85s/README.md](passive85s/README.md)。观察窗口内确实再次出现node2 OFFLINE，但按本次用户授权保留故障，不以全员READY作被动取证前提。本轮没有重启/调RF试图让观测通过。

核心事实：fl02 CLIENT_LOOP两计数全窗口0；RX_BATCH最大523us，server161us；有效授权统计增量156，authorized发送42、denied156；有pending但无授权的秒级区间，仍未见到期FEC跳过或零超时spin。HB源最大gap5.238秒、server接收gap13.438秒，1/18 API样本OFFLINE；现场仍不稳定，不能以native同SHA宣称修复。

最后只读API为serverIDLE/no active job、1/2/4均READY，四端daemon active且无采证/安装残留；该状态仅对应 `passive85s/final-status.json` 保存时刻。
