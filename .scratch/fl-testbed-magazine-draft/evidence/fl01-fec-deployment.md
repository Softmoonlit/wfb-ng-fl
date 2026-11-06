# d898d21 native 统一发布记录

提交：`d898d21`（修复客户端授权失效时的 FEC 截止处理）

构建产物：`wfb_v6_uplink`

统一 SHA-256：`ad3340e51eecc6e5691a6edb84f26f3a262b518c3555497df62ea6f7865e1e56`

发布目标：fl00、fl01、fl02、fl04、fl05、fl06、fl07、fl08、fl09、fl10。fl03按固定目标集合跳过。

## 发布动作

- 发布前全部目标管理 SSH、免密 sudo 和服务状态核验通过。
- 每台旧 `/usr/bin/wfb_v6_uplink` 已备份为 `/usr/bin/wfb_v6_uplink.before-d898d21`。
- 同一构建产物复制到全部10台 `/usr/bin/wfb_v6_uplink`。
- fl00 `wfb-fl-server-daemon.service` 及九台客户端 `wfb-fl-client-daemon.service` 已标准重启。
- 运行中 `/proc/<pid>/exe` 的 SHA-256 在10台均为上述统一摘要。

## 发布后状态

服务和 native 进程均运行。发布后状态快照中 fl01、fl02、fl04、fl05、fl06、fl08、fl09、fl10 为 READY/IDLE；fl07仍OFFLINE，fl03按计划不存在。fl01在发布后日志中仍多次进入寻频自愈，不能将本次 native 发布宣称为已解决其全部心跳问题。

因此本记录证明的是**native提交已统一加载并运行**，不证明九客户端10分钟待命门槛或两次通信闭环已通过。下一步必须先处理fl07入网和fl01剩余心跳不稳定，再开始门槛验证。
