# 06 客户端宿主软件证据

基线：05 commit `d9386f9`。仅修改 `wfb_ng/fl/client_daemon.py`，新增 `wfb_ng/tests/test_fl_client_recovery.py`；未修改 service.py，未 commit。

终态下行回调只检查身份、记录首次终态和唤醒 worker。回收与报告等待均在 daemon 锁外；startup 收尾和终态 worker 共享单一资源回收所有者。自然返回、合法身份的配置拒绝均保留作业绑定直到服务器终态。重复终态和 ACK 丢失只重报同一 epoch，显式再次真实清理可由 blocked 推进下一 epoch。沙箱保留未确认退出的进程组和未释放 TUN 身份；待命链路退出未确认不能清句柄或让任务接管。ready 要求组退出、任务 TUN 释放、待命进程存活及 TUN 地址／UP／控制路由检查通过。

采用既有 SIGTERM/SIGKILL 进程组机制；新增的是有界退出确认，无通用终止框架，无固定延迟代替退出证明。预算裁决仍由主线 server/coordinator 拥有，客户端不引入完整轮次 deadline。

## 检查

- `red-downlink.txt`：真实子进程 TERM 尚未完成时，原同步终态阻塞下一条下行消息，红灯成立。
- `red-process-group.txt`：模拟内核仍能看到后代时，原代码错误报告 ready，红灯成立。
- `red-retry-epoch.txt`：原路径没有真实重清理后的新 epoch 恢复事实，红灯成立。
- `red-rejected-terminal.txt`：原配置拒绝未保留绑定，服务器终态拿不到恢复事实，红灯成立。
- `applicable-tests.txt`：客户端恢复、服务、射频应用、daemon、JOB_RECOVERY 协议、已有作业报告、终态 registry 合并 **110 passed、2 deselected**。此时新增恢复检查为 10 项。
- `recovery-evidence-tests.txt`：新增未退出 idle transport 拒绝任务接管后，全部 11 项恢复检查及既有归档证据检查 **26 passed**。
- `mypy.txt`：`python -m mypy --follow-imports=silent --ignore-missing-imports --check-untyped-defs wfb_ng/fl/client_daemon.py` 通过。
- 限定本次实现文件的 `git diff --check` 通过。

合并命令：

```sh
python -m pytest -q wfb_ng/tests/test_fl_client_recovery.py wfb_ng/tests/test_fl_client_service.py wfb_ng/tests/test_fl_client_radio_application.py wfb_ng/tests/test_fl_job_recovery_reports.py wfb_ng/tests/test_fl_job_reports.py wfb_ng/tests/test_fl_terminal_registry.py wfb_ng/tests/test_fl_client_daemon.py -k 'not idle_link_hands_tun_to_sandbox_and_recovers_after_completion and not startup_bandwidth_is_the_source_for_hardware_and_link'
python -m pytest -q wfb_ng/tests/test_fl_client_recovery.py wfb_ng/tests/test_fl_evidence.py
```

`client-tests.txt` 保留完整旧客户端检查实际失败：`test_idle_link_hands_tun_to_sandbox_and_recovers_after_completion` 在 terminal 调用返回后立即断言绑定已清空，违反异步回收契约；`test_startup_bandwidth_is_the_source_for_hardware_and_link` 的假进程始终 poll()==None，stop 断言与退出未确认必须保留句柄冲突，含对应 teardown error。本次无权修改旧测试文件，未修改或悄悄标记通过；对应新契约由恢复检查覆盖。`first-client-tests.txt` 为开发阶段历史失败，已修正适用回归，不代表最终结果。

## 边界

这些是本机真实沙箱子进程与可控网络／报告边界的软件检查，不代表四板实际 transport、无线恢复或整机故障通过。实机验证仍由后续硬件工单承担。

当前 io_timeout_seconds 在 client 与 service 的既有 schema 中为正整数；server 接口备注提出 float，需主线统一决定并分配 service.py owner。本次没有自行扩散修改。

## 主线整合追加：float 预算及旧测试迁移

主线已决定三项 budget 为 finite positive number，拒绝 bool。客户端 ClientJobConfig.io_timeout_seconds 注解改为 float，配置及 TASK_ANNOUNCE 两处接受有限正 int/float，不作 int 截断；service.py schema 仍交主线。

已获授权修改 test_fl_client_daemon.py、test_fl_client_service.py。原异步 terminal 立即断言改为 join 回收线程／等待逐作业恢复报告事件；假 link 的 terminate/kill/wait 产生一致的可观察退出。保留原业务断言，不新增固定 sleep，不再 deselect 这两项。

新增 0.2 从 TASK_ANNOUNCE 到角色 JSON 不截断的公开宿主检查，以及 bool、零、负数、NaN、正负 Infinity、字符串和 None 的配置／TASK 双入口拒绝检查。

本轮四个文件 py_compile 与限定 git diff --check 已通过。当前整体测试和 mypy 被并行整合中的外部文件阻断：integration-budget-tests.txt 最新记录 coordinator.py:466 的 JobConfig NameError；integration-budget-mypy.txt 记录 control.py:1585–1590 的语法错误。red-fractional-budget.txt 也仅为外部语法错误造成的收集失败，**不是预算断言红灯证据**。因此上文 110/26 passed 是此前版本结果，不宣称追加预算与迁移后的完整检查通过。外部文件恢复可导入后，应执行四项客户端检查且不加 -k 排除。

## Spec review 实质修复：ACK 门禁与宿主稳定报告身份

本地 ready 仅缓存 `(job_id, epoch, state, error_code, reason, report_id)`，UUID 在事实形成时生成一次。此时不清当前绑定、不发 IDLE；daemon 与控制状态使用既有 ABORTING。只有 notify_job_recovery 返回 ACK 成功，且当前 ControlPeer 与缓存事实均未变化，才清绑定并发 IDLE。配置拒绝后同样保持待终态状态。TASK_ANNOUNCE 与公开 trigger_job 均受绑定门禁约束。

重复 terminal、ACK 丢失、ControlPeer 重建均复用同一事实与 report_id，不重复资源清理。主线已确定 control 方法新增显式关键字 report_id，由宿主提供，ControlPeer 只编码与有限重试、不另生成 ID。

- red-ack-gate.txt：修复前，ACK 失败仍清空绑定，红灯。
- red-direct-ack-gate.txt：直接 trigger_job 绕过待 ACK 门禁，红灯；已修正。
- 新增 ACK 失败拒绝新任务、跨 ControlPeer 重建不重回收且 ID 稳定、真实 ControlPeer 编码前后 envelope 相同三项检查。
- ack-gate-applicable-tests.txt：最新七文件适用检查 **130 passed、1 failed**，无 deselect。唯一失败是尚待 control owner 落盘 report_id 关键字，真实编码检查收到 TypeError；不为此加兼容 shim 或修改别人的文件。
- ack-gate-mypy.txt：同一外部接口尚未落盘，唯一 call-arg 错误。当前共享文件已可 import，原临时语法／JobConfig 问题已解除。

待 control owner 更新签名后，复跑上述完整检查与 mypy；本轮不 commit，不改 service.py。

## 紧急现场端点隔离及最终复验

四个客户端检查模块（client_recovery、client_service、client_daemon、client_radio_application）统一使用新增 `_client_test_network.py`：

- 所有 ClientDaemonConfig 显式传入回环 server_control_host 和临时 control/broadcast/radio 端口，不改生产默认值。
- 默认 ControlPeer UDP 使用 FakeControlPeer，不创建真实 UDP socket；不再仅 patch start 而让 notify_state_change 泄漏真实 heartbeat。
- 广播消息由测试直接分发，不创建真实 wildcard 广播监听。
- 真实心跳往返仅在专用 radio rebuild 检查显式启用回环临时端口，真实 link radio 检查也仅使用回环临时端口。真实 socket 的 connect/sendto 自动显式绑定回环临时本地端口。
- 真实 socket 边界在系统调用前拒绝非127地址、实机控制端口9000/9001/9002/9200和wildcard bind；新增五项检查验证拒绝发生在发送前。AF_UNIX 算法通知保留真实通信。
- 测试中途的 monkeypatch.undo 已去除，避免提前撤掉网络隔离。保留的10.80仅是身份／冻结配置文本或Fake ReportPeer直接handle_datagram的source模拟，不是外部发送端点。

Control owner 的显式 report_id 签名已经落盘；真实编码跨 ControlPeer 重建的检查通过，两次 envelope 与宿主 receipt 的 UUID 一致。

最终完整命令（不排除、不停实机服务）：

```sh
python -m pytest -q wfb_ng/tests/test_fl_client_recovery.py wfb_ng/tests/test_fl_client_service.py wfb_ng/tests/test_fl_client_daemon.py wfb_ng/tests/test_fl_client_radio_application.py
python -m mypy --follow-imports=silent --ignore-missing-imports --check-untyped-defs wfb_ng/fl/client_daemon.py
```

`isolated-network-tests.txt`：**101 passed**。`ack-gate-mypy.txt`：**Success: no issues found in 1 source file**。限定本次文件的 diff 检查通过。以上结果覆盖预算 float、旧测试迁移、ACK 门禁、稳定报告身份及端点隔离，取代上文开发阶段待接口结果。未 commit，未改 service.py、control.py 或生产默认值。
