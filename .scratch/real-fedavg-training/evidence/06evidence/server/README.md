# 06 服务端宿主整合证据（软件接缝）

基线 `d9386f9`，05 已提交。仅修改 `server_daemon.py`、`test_fl_training_host.py`、`test_fl_real_job.py`、`test_fl_online_target_selection.py`。未改工单状态，未提交代码。

已读取 06 工单、spec、TDD 技能，以及系统分层、Transport、Runtime 三份跨层设计。公开接缝沿用用户指定的 start_job / status / stop / control；真实控制面报告与注册表配合受控 Runtime / 部署算法，只证明软件宿主行为。

已实现：

- ServerDaemonConfig JSON 提供 preparation / round / I/O 三项冻结预算，拒绝 bool、非正数、非有限值；请求不能覆盖部署预算。Web 映射删除硬编码 I/O 输入。
- 准备流程继续复用既有可取消准备句柄。
- task/round 取消绑定 job_id；Coordinator 首因回调取消等待并保留首因，终态回调异步调度，避免 UDP 阻塞和 Coordinator self-join。
- 按主线最新纠正，终态先开启恢复屏障并广播匹配 terminal，独立的作业终态交付线程在冻结 I/O 预算内重试；客户端停止不被服务端不协作计算阻挡。本地仍严格等待 Coordinator 实际退出，再关闭任务角色／注入 Runtime，以 server_recovery_complete 单独表达本地退出证明。客户端全员 ready 也不能越过本地门禁。
- 全部固定目标回报 ready 后才 ready；缺失为 unconfirmed，客户端 blocked 或本机清理失败为 blocked，保留缺节点，阻止新作业。迟到恢复只更新恢复事实。
- 固定参与者失联通过控制面的作业 watcher 触发失败；任务角色通过独立 poll_failure 线程监管，不由 get_status 触发。只预留租约接口，不实现票 10 策略。
- 首次终态幂等，summary 在 recent 保留；后到错误可追加诊断，不能覆盖执行／效果结果。
- stop 同样等待实际计算退出，未获退出证明不关闭仍可能使用的任务角色，保留 blocked。

边界：不增加 UFTP 自然总时长 deadline，不改变 Runtime 四接口，不新增通用终止框架。不声称 GPU、空口、HTTP 绝对 deadline 或整个进程组实机验收已通过；持久 work_dir 由 08 部署配置。

检查结果（紧急端点隔离后的当前证据）：

- `isolated-host-checks.txt`：仅运行本 worker 已隔离的三个文件 `python -m pytest -q wfb_ng/tests/test_fl_training_host.py wfb_ng/tests/test_fl_online_target_selection.py wfb_ng/tests/test_fl_real_job.py`，54 passed。
- `terminal-delivery-checks.txt`：首次发送异常重试、部分 blocked 不漏停其他目标、后到恢复保持首次终态与原错误历史，3 passed；这是在上述合并检查后补入恢复历史保留的最终定向验证。
- `typecheck.txt`：`python -m mypy --follow-imports=silent --ignore-missing-imports --check-untyped-defs wfb_ng/fl/server_daemon.py`，Success。
- 指定文件编译、`git diff --check` 通过。
- `red-terminal-delivery.txt` 是修复前两项 P1 的红测，使用的也是已隔离测试端点。
- 其余旧日志是紧急隔离前的历史检查，不能据此声称其端点隔离；未再运行其他 owner 的网络测试。

测试隔离审查：

- training_host 原来虽 control_bind=127.0.0.1:0 且广播在内存边界替换，但仍保留广播／radio 控制默认端点；online_target_selection 原来保留更多默认部署端点。这些默认值均已移除。
- 两个宿主测试 fixture 现在显式 control_bind_host=127.0.0.1、bind_port=0，广播地址127.0.0.1，广播与radio端口为测试持有的loopback UDP sink 临时端口。内存handle_datagram／registry的消息源也使用127.*与临时端口，不再出现10.80.*:9001消息源。
- autouse fixture 只拦截真正 socket 出站边界 sendto/connect/connect_ex，拒绝非loopback与9000/9001/9002/9200；真实注册表、报告校验、取消及恢复机制保持运行。IPC/HTTP同样为127.0.0.1临时端口；物理适配器与角色依旧使用明确内存边界。
- real_job 仅验证配置构造与JSON，不创建daemon/control/socket，不产生网络发送。
- 只读发现其他owner的 test_fl_job_recovery_reports 仍存在默认广播与client默认端点，已在 interface-notes.md 通知，未改、未再运行。未修改生产control、未停止任何实机服务。

P1 最终行为：terminal delivery 线程建立不依赖首个send成功。每次发送异常独立记录在terminal_delivery_errors，冻结绝对恢复期限内继续发送；单个blocked报告只记录进展，不终止向其他未确认目标广播。只有全员ready、绝对期限到达或daemonstop才停止交付循环；blocked提前唤醒后通过事件等待同一重试时点，避免忙循环与固定sleep。节点原blocked envelope按epoch保留在recovery_report_history，后到ready只更新当前恢复事实。
