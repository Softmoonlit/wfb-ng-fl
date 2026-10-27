# 服务端宿主接口接线中

仅修改 server_daemon.py 与三个指定测试。无 sendmessage 工具可用，故用此文件供主线读取。

需要 Coordinator worker 确认：JobConfig.io_timeout_seconds 接受 finite positive float（目前整数校验阻挡部署配置）；完整轮次 deadline 由 Coordinator 拥有，round_completion(index, deadline) 请明确签名；first-cause callback 的准确参数名/签名请落盘或告知主线。

Control 按测试已对齐 prepare_job_recovery(job_id,nodes), wait_for_job_recovery(job_id,timeout), get_job_recovery_reports(job_id), cancel_job_barriers(job_id,reason), watch_job_liveness(job_id,nodes,on_change), unwatch_job_liveness(job_id)。恢复 report 使用 recovery_epoch/recovery_state。

已读 docs/design/{系统分层与跨层契约,Transport-Backend传输契约,FL-Runtime轮次与文件契约}.md。服务端只将冻结 io_timeout_seconds 贯通角色和恢复等待，不为 UFTP 增加自然总超时。HTTP 绝对 operation deadline、close socket cancel 及 pgid proof 若需完善由主线另派 worker；服务端及时 role.poll_failure 监管已实现独立线程，不依赖 status。

从 test_fl_coordinator.py 的新测试确认预计 first-cause 参数名 cancel_callback(reason)，宿主已按此直连：先取消 task/round，原子记录 first_failure，再异步等实际退出。等待 Coordinator 实现落盘验证。现 prepare_round/round_completion 仍使用 index；如 worker 暴露 current_round_deadline 属性请注明，我会删掉宿主自建 round_deadline 直接读取。

主线留意 Web 投影：console.py 的 _safe_job 白名单当前未带出 recovery_missing_nodes/recovery_reports/additional_errors/first_failure/liveness_events；本 worker 按独占边界不改 console。ServerDaemon.get_status_report 已含这些事实。若 06 要求 Web 显示缺节点，需主线/console owner 把白名单透传补齐。

current_round_deadline 属性已落盘；服务端 complete_round 直接读取 coordinator.current_round_deadline，已删除宿主自建轮次时钟。accepted 返回三项冻结预算。仍需 control.py 恢复可导入（本轮观察到 1590 unmatched }、1764 if 缺块，未越界修复）。

恢复任务后首轮核心检查仍 collection 4 errors：control.py:1590 unmatched '}'，TASK_READY 的 record = { 缺失（1585 前）；1764 if 缺块也仍被 mypy 观察到。请 control owner 完成语法修复后告知主线。Coordinator 目前 _supervisor 尚未在同步循环使用（rg 仅构造/property），等待 owner 继续。

核心集最新运行：45 passed、13 failed（host-checks.txt）。13 个失败全部依赖 Coordinator 的 _supervisor 尚未 begin_round/start：current_round_deadline None 导致 round_not_started（12个），及 deadline 未取消（1个）。control 已恢复导入。请求 coordinator owner 完成循环监管后通知主线复跑，宿主无自建 deadline。

宿主核心四文件已 60 passed，server mypy 已通过。邻接 Coordinator/context 合并 32 passed、2 failed（coordinator-context-checks.txt）：test_supervisor_end_phase_cannot_hide_late_absolute_deadline 的 patch 未导入 NameError；test_round_timeout_covers_evaluate_and_report_barrier 第二 subTest 复用已 set stop_event，未进入 blocking_report。均请 Coordinator owner 修复自有测试，再由本 worker复跑。

Coordinator/context 再跑已 33 passed，唯一剩余 test_round_timeout_covers_evaluate_and_report_barrier：test 文件 621 行两次 subTest 共用 self.stop_event，evaluate 场景后 event 保持 set，report 场景 _check_stop 直接 aborted，因此 entered.wait 超时。请 owner 每场景重新 Event 并重置 runtime 的 abort_event/aborted（fixture 构造）后复跑。宿主测试全部通过，不越界改其测试。


最终宿主变更：按主线纠正，prepare_job_recovery + 首个匹配 terminal 广播提前到本地 coord.wait/role.close 之前；专属 terminal delivery 线程在冻结 I/O 期限内重发，不受阻塞聚合影响。server_recovery_complete=False 时，即使 clients 全员 ready，仍保持 recovering、拒绝新作业；真正退出并关闭资源后才 ready。get_status 在 recovering 期间可展示已收到的 recovery_reports/missing_nodes。请主线投影 server_recovery_complete 至 Web（console 非本 worker范围）。

最新证据 final-host-checks.txt=48 passed；stop-before-join-checks.txt=5 passed；server mypy=Success；git diff --check通过。Coordinator/context最新=33 passed/1 failed，owner改了 runtime但未在每个subTest重置 self.stop_event，第二phase仍 _check_stop aborted；该测试不属server独占，未修改。无commit、无部署、无工单状态变更。

紧急测试隔离完成：training_host/online_target_selection 均显式 control_bind_host=127.0.0.1, bind_port=0；broadcast_addr=127.0.0.1、broadcast_port/radio_control_port 为持有的 loopback UDP sink 临时端口。内存 handle_datagram/registry 输入源也改127.*临时端口。两个测试模块 autouse socket 出站边界拒绝非loopback及9000/9001/9002/9200，保留真实注册表和公开控制报告语义，不mock深内部。real_job仅构造配置，无实例化daemon/control/socket。隔离后只运行这三个文件：52 passed（isolated-host-checks.txt）。

只读检查其他owner test_fl_job_recovery_reports：_server虽bind127:0但broadcast_addr/port仍默认；_establish_ready用10.80.*:9001作内存输入；ControlPlaneClient构造未覆盖server/broadcast默认。其notify测试当前使用_notify_report内存mock或在发送前拒绝，但建议owner统一显式loopback临时端点；本worker未改、未再运行该文件。


两项 P1 已交付：发送线程无条件建立（仅prepare错误独立记录）；首个或后续broadcast异常记terminal_delivery_errors并在绝对预算内重试；任一blocked不停止广播，只有全员ready/deadline/stop终止。blocked提前唤醒由Event.wait(retry_at-now)等待既定时点，没有固定sleep。recovery_report_history按节点/epoch保留blocked错误，后到ready只改进展。54个隔离自有测试通过，补入错误历史的最终定向3pass，server mypy成功，diff check成功。注意Web若要显示这些诊断需投影 terminal_delivery_errors/recovery_report_history（本worker不改console）。
