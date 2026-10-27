# 701963a 部署与观测门禁结果

冻结提交：`701963ac254261c31c56a074d06966a9217346f6`。archive SHA-256：`11110cc3d5a32a28593c46bb13ca6505826d6bdbf202d83c611edaf3c353cfe2`。

## 已完成

四端完成 JSON/systemd/drop-in 备份、daemon stop/退出确认、同一 archive 构建、`install-v8.sh` 安装、持久 MNIST/workdir 配置恢复及启动。未调用 dirty workspace，同步过程没有包含 10/11/12 未提交文件。逐端日志：`flXX-backup-build.txt`、`flXX-stop.txt`、`flXX-install-config.txt`、`flXX-start.txt`。

四端实际 Python build identity 均为：

```text
701963ac254261c31c56a074d06966a9217346f6
```

实际运行来源审计使用 systemd PID、`/proc/PID/cwd`、`/proc/PID/environ` 及从 `/`、清空 `PYTHONPATH` 的 `find_spec`；均来自安装目录 `/usr/local/lib/python3.10/dist-packages`，cwd 为 `/`，没有 dirty repo shadow。原始文件为 `flXX-runtime-audit.json`。

701 源码已包含 native 观测字段 `GRANT_SEQ`、`CLIENT_LOOP`；源文件读取和构建记录保留在 archive 目录及安装日志中。

## 门禁失败

最终主 API 状态：server `IDLE`、无 active job；fl01/fl04 为 `READY/IDLE`；**fl02 为 `OFFLINE/IDLE`**。因为用户规定任一端不 READY 即停止，85 秒纯 idle 被动观测没有启动，也没有假造结果。状态原文见 `status-after-701.json`。

701 每板从同一冻结源码独立构建后的运行 native SHA：

- fl00：`f7c73f41b730ddb3547e1730b5a5b7b40a55dd7a8e3e5f265dcc6563c6c432b1`
- fl01/02/04：`b786e230194a5ed0cb4ce656692a01b4954f12bbb35058e28f36f34944bb5106`

本次没有把该差异解释成 server/client 正常差异，也没有替换二进制、重建或继续观测。该现象与此前编译器修订/Build ID差异方向一致，但仍需在下一维护窗口完成同一 native 制品分发或进一步确认构建环境，才能作为下一次实测基线。701 本次四端 Python 提交身份是一致的；native 不同已如实记录。

## 禁止动作均未执行

没有训练、stop 作业、RF/频道/MCS/功率修改、timeout修改、硬件操作或 10 部署。由于 fl02 未 READY，观测门禁停止；不将部署完成写成 08 验证通过。

## 后续授权执行：native已统一、85秒故障观察已完成

以上是首次门禁阻断的历史结果，不覆盖。用户随后明确授权在确认ABI/构建来源后统一native，并允许fl02在OFFLINE状态下被动取证。该后续动作现已完成：

- 四端Python仍是701963ac254261c31c56a074d06966a9217346f6；不部署仅工单提交b771366。701冻结发布本身包含已提交票10，这是授权的整体发布，不是现场私改/混装未提交10。
- 选同一fl00冻结701构建产物分发，安装及运行/proc/exe四端SHA均为f7c73f41b730ddb3547e1730b5a5b7b40a55dd7a8e3e5f265dcc6563c6c432b1。ABI/ELF/编译器/object/source/archive证据、备份及停服安装日志在native-unification。
- 已执行一次85秒纯idle被动观察，没有训练、标定、人工改RF/timeout或硬件操作。窗口内node2有1个OFFLINE API样本，CLIENT_LOOP两计数增量0，RX_BATCH.max_us为client523/server161，有持续HB与间歇上行积压。
- 结束API为serverIDLE、无作业、1/2/4均READY；所有服务active且无采证残留。该瞬时状态不能作为持续稳定通过。

最新结果见 [native-unification/README.md](native-unification/README.md) 和 [passive85s/README.md](native-unification/passive85s/README.md)，原始失败记录保持不变。
