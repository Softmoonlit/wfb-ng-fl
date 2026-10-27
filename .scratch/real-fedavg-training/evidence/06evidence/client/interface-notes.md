# 06 客户端宿主接口交付

05 基线 d9386f9；只改 client_daemon.py 与新增 test_fl_client_recovery.py，不改 service.py，不 commit。没有 sendmessage 工具，通过此文件供主线及 /explore06 读取。

已按确定签名调用 ControlPeer.notify_job_recovery(job_id, recovery_epoch, recovery_state, *, error_code=None, reason=None)。客户端首个事实 epoch=1，重复 terminal 重报缓存；显式 abort_job 再次实际清理可将 blocked 提升为 epoch+1 ready。服务器终态前保留当前绑定，防止本地自然结束后接受冲突新任务。未知终态忽略。

ControlPlaneClient.notify_job_recovery 已落盘并完成签名对齐；客户端 mypy 通过。复用 ControlPeer 缓存的 report_id，不添加 envelope 或兼容 shim。

跨文件注意：现有 client ClientJobConfig / TASK_ANNOUNCE 与 service._read_config 都将 io_timeout_seconds 校验为正整数。server/interface-notes.md 提到希望改为 finite positive float；如果主线决定三项预算统一 float，需要主线指定其他 owner 修改 service.py，客户端可同步放宽，不会自行改 service。

新增 11 项恢复检查；适用合并 110 passed、2 deselected，恢复与证据检查 26 passed。适用旧检查有一个立即断言 terminal 同步清空绑定，和一个以永远 poll()==None 的假 link 检查 stop 的测试，与异步终态／未退出不可清句柄要求冲突；保留原文件，新增公开恢复接缝覆盖新契约。


## 整合追加（进行中）

已按主线决定将 ClientJobConfig.io_timeout_seconds 和 TASK_ANNOUNCE I/O 字段放宽为 finite positive int/float，拒绝 bool，不作 int 截断。主线负责 service.py 对应 schema。

已获授权迁移 test_fl_client_service.py 的终态恢复等待及 test_fl_client_daemon.py 的异步终态／假链路退出行为。测试不再 deselect，等待恢复报告或 join 回收线程，不添加固定 sleep。

当前重新收集测试被共享 control.py:1588 的 unmatched '}' 阻断：TASK_READY 提交段缺少 self._task_ready_reports[payload["node_id"]] = { 开头。此文件不属于客户端 owner，未改动；需 control owner 修复后才能全量验证。

追加最新状态：四个客户端文件 py_compile / git diff --check 通过。完整四项客户端检查已取消 -k 排除，但收集被 coordinator.py:466 NameError(JobConfig 未定义) 阻断；mypy 仍被 control.py:1585–1590 语法错误阻断。日志 integration-budget-tests.txt / integration-budget-mypy.txt。两个受影响旧测试已迁移，等待共享模块修复后复验。不 commit。

## Spec review ACK 门禁修复接口（请主线转 explore06）

宿主缓存每个 epoch 的 stable report_id，notify_job_recovery 需要新增关键字参数 report_id: str。宿主已按 report_id=fact[5] 调用；ControlPeer 应原样发送该 ID，并校验同 epoch 的缓存事实及 ID 一致，重建 ControlPeer 不能重新生成该 ID。若 control owner 希望参数可选以保留现有独立调用测试，可使用 Optional[str]=None，但宿主始终显式传入。

本地 ready 不清 binding、不发 IDLE；仅服务器 ACK 成功后，在锁内核对同一 fact 与同一 control 实例，才解除接单门禁。ACK 超时返回后仍保留绑定；重复 terminal 仅重报缓存、不重新回收。red-ack-gate.txt 已验证原逻辑错误放行的红灯。

主线已确认：notify_job_recovery(..., *, report_id: str, ...) 必填显式 ID；宿主已生成 UUID 并保存在 epoch receipt，ControlPeer 仅 encode+重试，不再另 cache ID。客户端 ACK 前使用已有 ABORTING 状态，不假报 IDLE。重建 ControlPeer 后同一 epoch/内容/report_id 完全一致，新增公开宿主测试已通过。

最新聚焦结果：95 passed（四个完整客户端文件），外加真实 ControlPeer encode 跨重建测试等待 report_id 签名落盘；该测试当前 TypeError，mypy 同一处 call-arg。已额外收紧 daemon.state：待确认为 ABORTING；配置拒绝报告后也不发 IDLE。直接 trigger_job 与 TASK_ANNOUNCE 同样受恢复绑定门禁约束，不能绕过。

## 紧急客户端测试端点隔离

已暂停旧未隔离命令。四个客户端测试模块均导入自动启用的 isolated_client_network fixture；默认 ControlPeer UDP 由 FakeControlPeer 接管，patch start 不再遗留真实 heartbeat UDP。所有 ClientDaemonConfig 构造显式传入 client_network_kwargs()（127.0.0.1 + 三个临时端口）。真实心跳往返仅在 radio rebuild 检查显式开启回环 peer+临时端口；广播监听由测试直接分发替代，不再 wildcard bind。真实 link radio UDP 仅对临时回环端口。

新增真实 socket 边界断言：非127地址、实机控制端口9000/9001/9002/9200和wildcard bind在系统调用发送前拒绝；未更改生产默认值。UNIX算法报告不受影响。保留的10.80地址仅身份／配置文本，或Fake ReportPeer直接handle_datagram的模拟source，无外部send。

最终：control report_id 接口已对齐。隔离网络后四个完整客户端文件101 passed，无deselect；client_daemon mypy通过。新helper `_client_test_network.py` 是测试唯一共享网络边界；真实socket仅127回环临时端口，默认control UDP全Fake。生产默认值未改，不依赖停服务，不commit。请主线以 isolated-network-tests.txt / ack-gate-mypy.txt 为最终证据。
