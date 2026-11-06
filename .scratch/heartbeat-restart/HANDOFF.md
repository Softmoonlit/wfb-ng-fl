# 心跳跨重启修复交接

本任务仅修改 control.py 和相关心跳协议测试。未修改既有脏 daemon、配置或界面文件，未提交、未部署，未新增 ADR。server_daemon/console 两个脏测试中的心跳 fixture 由父任务另派 agent 迁移，本任务未触碰。

## 证据修正

server_daemon.get_status_report 优先用 record.last_heartbeat_wall_time × 1000 填充 last_heartbeat_timestamp_ms；该展示字段不是心跳请求序列。原始红测使用真实抓包序列 9038344 → 62406。用户另行提供的离线诊断脚本使用 9038344 HUNTING → 9038345 IDLE → 重启后 62406/62407，属于旧协议问题诊断，不作为最终协议契约测试。

## 最终方案：无状态 HMAC 挑战 cookie

每个 registry 实例启动时通过 os.urandom(32) 分配随机密钥，仅保留每个节点当前已接纳会话。挑战使用标准库 hmac/hashlib 的 HMAC-SHA256，无新依赖、持久存储、待确认槽或历史 UUID 集合。

cookie 绑定 node_id、新 boot_id、当前已接纳 session 元组、来源端点、信道与请求 timestamp。输入用固定数组顺序和 JSON 编码；ensure_ascii=True 让 Unicode 输入也有明确字节表示。相同请求在相同 current 下得到相同 cookie。未知 session 或空 session 请求均获得本请求自己的 cookie，不会覆盖其他请求。

仅在服务端生效信道上收到匹配 cookie 的 HUNTING 回传，才接纳新会话。校验使用 hmac.compare_digest 的 bytes 参数；输入 session 使用 UTF-8 surrogatepass 编码，非 ASCII 字符及孤立代理字符均不会触发字符串比较异常。identity 与当前会话相同的请求继续走原有同代序列规则。

成功切代后，current 变化使所有先前签发的 cookie 失效；服务端重启后更换随机密钥，同样使旧 cookie 失效。只有未确认请求之间的先后到达不形成已接纳代际顺序；第一个有效挑战回传接纳会话，之后绑定先前 current 的其他 cookie 均失效。旧未见代际的历史报文不能用旧 cookie 覆盖已经接纳的新代。

新会话 HUNTING 清除旧验证状态并记录 CONNECTING，只有随后严格更新的 IDLE 才授予 READY。同会话内继续严格拒绝旧序列和重复序列，不清空 registry，也不放宽旧包规则。

只有挑战、没有最终确认的回执不能建立客户端联系或续期活性，也不会调用服务端业务心跳回调、发生活性通知或触发射频对齐。客户端在原请求截止时间内回传 cookie，不延长等待窗口；任务接管时回传前重新取得状态锁，确认 HUNTING 仍拥有提交权。每轮新的 hunt_once 从空 session 开始；直接持旧 session 上报 HUNTING 的路径也能完成服务端重启后的重新协商。

## 协议与构造约束

NodeHeartbeat 在线路上必须包含 boot_id 和 session。每个 ControlPlaneClient 实例生成一个固定启动 UUID，寻频与普通心跳发送路径均显式复用它。NodeHeartbeat 内部构造默认 factory 每次会生成新 UUID，仅用于内部构造便利；未来发送方必须显式复用实例启动身份，不可依赖逐包构造默认。本轮未将其改为构造必填参数，避免扩大 fixture 迁移范围；线路解码仍严格拒绝缺少 boot_id 的报文。

HeartbeatAck 必须包含 boot_id、session、confirmed、ack、heartbeat_timestamp_ms 和 channel。缺少字段直接拒绝，不保留旧协议兼容路径。

## 二次评审修复与证据

先前单 pending 槽仍存在乱序缺陷：old null → new null → old null duplicate 会覆盖新挑战，使 new confirmation 失败。本轮先在公开 NodeHorizonRegistry.update_heartbeat 接缝补充精确红测，再彻底移除单槽状态，改为上述无状态 cookie。

- `01-red.txt`：原跨重启红测失败，记录停留在 9038344，未更新为 62406。
- `05-regression.txt`：任务接管时收到挑战的竞态红测，之后已修复。
- `12-final-regression.txt`：第一轮相关回归 184 项通过。
- `13-idempotency-red.txt`：重复空请求旋转 token、旧 confirmation 破坏新 pending 的红测。
- `16-idempotency-final-regression.txt`：上一轮 189 项通过；其单槽实现仍有二次评审指出的旧 null 覆盖问题，不能作为最终验收结论。
- `17-cookie-red.txt`：精确 old null → new null → old null duplicate → new confirmation 红测失败。
- `18-cookie-green.txt`：无状态 cookie 实现后，19 项重启专项测试通过。
- `19-cookie-final-regression.txt`：最终 **192 项全部通过**，耗时 34.49 秒；包含原有 189 项及新增精确乱序和两项 Unicode 校验用例。两条警告来自既有依赖弃用提示。
- `git diff --check` 通过。

最终命令：

```bash
python -m pytest -q wfb_ng/tests/test_fl_heartbeat_restart.py wfb_ng/tests/test_fl_heartbeat_ack_identity.py wfb_ng/tests/test_fl_heartbeat_liveness.py wfb_ng/tests/test_fl_control_plane.py wfb_ng/tests/test_fl_terminal_registry.py wfb_ng/tests/test_fl_control_review.py wfb_ng/tests/test_fl_job_reports.py wfb_ng/tests/test_fl_job_recovery_reports.py wfb_ng/tests/test_fl_radio_switch.py wfb_ng/tests/test_fl_radio_transactions.py
```

专项覆盖 OS reboot 单调钟归零（9038344 → 62406）、同 OS boot 的 daemon 重建、新代不继承 READY、同代单调拒旧、旧代较大序列、旧未见代际及迟到挑战、来源/请求/信道绑定、旧协议拒绝、真实回环客户端恢复、挑战未确认不得建立联系、未确认心跳不得触发业务回调、重复空请求 cookie 幂等、旧 null/旧 confirmation 交错不破坏有效协商、重复/乱序 challenge ACK、服务端重启后的旧 session 协商和 Unicode session 安全拒绝。

## 父任务接入范围

无需 daemon 持久存储接入或 state dir。其他未运行测试若手工构造心跳，应使用每实例固定 boot_id、显式挑战握手和已接纳 session；预期 ACK 字典应包含 boot_id/session/confirmed。没有已接纳会话的 IDLE/RUNNING 不会创建 registry 节点。测试均通过公开协议边界验证，不依赖私有 pending 字典。

全仓测试和硬件验证未运行；隔离回归通过不代表实机验收完成。
