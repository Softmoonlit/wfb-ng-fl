# 票10服务端作业无线策略接线

基于HEAD `69a94d8`（07 `145ea88`，06 `e53de00`）。仅修改server_daemon.py、新增test_fl_job_radio_policy.py及本目录证据。未commit、未部署、未访问四板、未修改service/client/control/algorithm/coordinator/transport/runtime/Console/工单状态。

## 生产接线

- 部署可选radio_policy_entry从ServerDaemonConfig JSON冻结加载，arity=2；job/Web请求不能覆盖。未配置不调用策略。
- 初始评估验证并保存后，在既有可取消准备线程内调用phase=radio_policy/round_index=0；发生在Role创建与TASK_ANNOUNCE前。闭包绑定job、固定目标、准备取消事件，不依赖尚不存在的active_job。
- 同一部署callable包装后通过现有FLCoordinator.radio_policy_fn注入。owner实现已将ROUND_READY屏障、end_round放在策略之前，末轮不调用，下一轮发布在策略成功返回之后。无额外policy_timeout。
- Contextcallback仅在策略同步调用窗口内有效；过期保存context不能配置。活跃轮次必须匹配job及ROUND_REPORT_COMPLETED安全边界。
- host先按正常radio validator检查patch，不允许channel/targets/unknown/bool，20 MHz表未有仍明确拒绝，可由策略处理。随后在registry admission_window内冻结fixed participants UNION online clients；失联固定成员不剔除，后到节点只影响下一次事务。
- 直接复用control.reconfigure_radio(patch,target_nodes,job_id,stop_event)，不走会重置IDLE的空闲执行路径。控制owner拥有真实session、有限事务与回滚预算、内部livenesslease；初始无watch安全跳过租约。
- 每次结果保留request/job/round/session身份、阶段、before/patch/target、确认集合、actual effective、恢复和有限错误。未确认时effective=None，锁定RADIO_ERROR并阻新作业。跨协调后失败锁存，即使策略catch仍强制终止；未知异常同样不伪造全网生效。
- 初始事务后，即使Role准备失败也保留recent结果；普通训练/作业失败不恢复初始配置，不写启动JSON。
- 在成功安全边界设置现有server_role.transport.uftp_rate_kbps属性。初始创建role传入已确认速率，轮次间后续start_downlink读取新值；无新Role或Runtime接口，不重建角色。

## 测试与隔离

`test_fl_job_radio_policy.py`使用公开start_job/status/abort_job和公开ControlPlane报告。继承既有guarded host fixture：control/IPC全部127.*临时端口，持有UDP sink；socket sendto/connect/connect_ex阻止非loopback及9000/9001/9002/9200。物理适配器为内存MockNetworkAdapter，Runtime/Role为明确受控边界，不执行GPU或真实UFTP。完整wire测试仅在内存broadcast边界注入job匹配的逐阶段ACK，真实control状态机与host事实处理持续运行。

- `focused.txt`：新增19项通过，包括部署入口、两个调用点、initial无active、目标union/离线固定成员、前置拒绝、callback过期、提交屏障、停止等待真实退出、失败不能吞、before回退/unknown、未落盘、Role复用/后续速率、真实control逐阶段job wire。
- `affected-host.txt`：自有已隔离五文件合并76 passed。随后新增2项已在focused19pass中通过；未重复扩大运行不属于本worker的网络测试。
- `typecheck.txt`：server_daemon.py和新测试mypy --follow-imports=silent --ignore-missing-imports --check-untyped-defs通过。
- 指定文件compileall、git diff --check通过。
- red-*.txt保留TDD红测：缺部署字段、未调用策略、缺callback、initial失败fact丢失与后续role失败fact丢失。

软件检查不是无线/GPU实测。20MHz普通请求仍拒绝，08/09现场由其owner独占。

## 主线接线点

Console需后续透传job.radio_results的有限字段；本worker未改Console。精确接口与进展见interface-notes.md。当前可用工具无send_message，跨owner信息通过共享证据文件传递。
