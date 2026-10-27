# e53de00 正式部署与 08 少轮执行结果

时间：2026-10-27 UTC。执行提交：`e53de0076c6ed118d824a2d31261e75de3223333`，归档 SHA-256：`245c20500fab8a0dbbc1fcf643dd6d0498b7e962640b699a6ef82e787d96f4a3`。未包含工作区未提交的 07、11、12 等内容。

## 部署

主 API 首次门禁遇到本机代理导致的瞬态 502；原始诊断保留，确认 curl 直连后继续。四端随后按顺序完成配置备份、daemon cgroup 停止退出、同一归档构建/安装和启动。fl00、fl01、fl02、fl04 的配置与 unit/drop-in 备份分别在 `backup/` 及 `flXX-backup-build.txt` 中。

初次安装验证曾因本地验证命令从仓库工作区导入旧 build identity `99865ef` 而失败；板端安装实际已完成。修正验证工作目录后，四端安装身份均确认提交 `e53de0076c6ed118d824a2d31261e75de3223333`。该本地验证脚本错误和修复记录保留在 `fl00-install-config.txt`、`fl00-identity-retry.txt`。

客户端发现 `/run/systemd/system/wfb-fl-client-daemon.service.d/radio.conf` 覆盖 `/etc/.../08-mnist.conf` 的 ExecStart，导致首个作业客户端没有 `mnist_data_dir`。先复制到 `/srv/wfb-ng-fl/deploy-e53de00/backup/run-dropins/`，再移除该运行时冲突 drop-in，daemon reload/restart 后三端有效 ExecStart 均为：

```text
/usr/bin/wfb-fl-client-daemon --config /etc/wfb-ng-fl/node.json --channel 157 --bandwidth-mhz 40 --radio-txpower-dbm 14 --uplink-mcs 4 --mnist-data-dir /srv/wfb-ng-fl/mnist --work-dir /srv/wfb-ng-fl/client
```

服务器有效配置为 `/etc/wfb-ng-fl/server.json`，实际解析值为：

```text
work_dir=/srv/wfb-ng-fl/server
mnist_data_dir=/srv/wfb-ng-fl/mnist
client_entry=wfb_ng.fl.mnist:client_entry
aggregate_entry=wfb_ng.fl.mnist:aggregate_entry
evaluate_entry=wfb_ng.fl.mnist:evaluate_entry
data_seed=2026
preparation_timeout_seconds=300.0
round_timeout_seconds=1800.0
io_timeout_seconds=120.0
```

四端服务保持 active。最终主 API 状态为 server `IDLE`、无 current job、`recovery_state=ready`；节点 1/2/4 均 READY/IDLE/online；无线仍为 157、HT40+、14 dBm、MCS3/MCS4、15000 Kbps。服务和无线没有因诊断而停留在停止状态。

## 官方模型与作业尝试

初始模型由已安装 e53de00 入口生成，seed 7，SHA-256：`b1a1c1734bec69acd26df1ed6c6aaafecd0b7a6b5b3c2f9b594043d7fca453d1`，大小 38792 字节。已上传到模型库，实际模型库响应和摘要保留在 `model-upload.json`。

第一次两轮请求：

- job `web-6727da200d4e49b992134ea35989a4a1`
- run `web-9876cf14b9bf4a658ee0fe27be17c3cc`
- 目标 `[1,2,4]`，两轮，期限 300/1800/120 秒
- 结果：`failed`，`reason=invalid_mnist_data`，`error=必须配置本机 mnist_data_dir`
- `current_round=0`、`rounds_completed=0`；随后 `RECOVERY_STARTED`、`RECOVERY_COMPLETED`，恢复 ready

该失败定位为客户端运行时 drop-in 覆盖问题，已修复并验证有效 ExecStart。

第二次两轮请求使用新幂等键：

- job `web-f317d54238864f64ab66164f151d3622`
- run `web-b7a96f0f69e5428382d339a5b001ccf3`
- 目标 `[1,2,4]`，同一模型和期限
- 结果：`failed`，`reason=participant_offline`，`error=固定参与者 2 心跳失联`
- `current_round=0`、`rounds_completed=0`；随后 recovery completed，最终 `recovery_state=ready`

第二次失败期间三个客户端都进入 PREPARING 并派生 `wfb_ng.fl.mnist:client_entry` RoleService，随后因服务器固定参与者失联而 ABORTING；日志显示恢复 ACK 和回到 IDLE，没有进入真实 epoch、上传、聚合或评估。没有把它计为训练成功，也没有启动独立训练中 stop，因为尚未进入训练轮次。

## 当前阻塞与证据

目前 08 尚未取得两轮真实训练、模型链、训练中停止或全员 confirmed recovery 的成功证据。首个配置错误已解决；当前未决阻塞是 40 MHz 运行期间控制面/无线心跳稳定性，第二次尝试在 node 2 上触发失联。fl01/fl02/fl04 角色日志分别保存在 `fl01-role-log-after-failure.txt`、`fl02-role-log-after-failure.txt`、`fl04-role-log-after-failure.txt`；主 API 的失败/恢复事件在 `final-status-after-failure.json` 及两次作业响应中。

轮询由主 API 状态进行，未通过 SSH 推进任何轮次。SSH 仅用于部署维护、配置备份和事后只读日志。没有 CPU fallback、跳过节点、缩减目标或盲目第三次重试。
