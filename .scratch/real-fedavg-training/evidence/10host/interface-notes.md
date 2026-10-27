# 10host 当前接口接线（不commit/no hardware）

HEAD69a94d8，07为145ea88。仅server_daemon.py+test_fl_job_radio_policy.py。
当前无send_message工具，使用共享文件同步主线。

- ServerDaemonConfig JSON radio_policy_entry: Optional[str]，load_algorithm arity=2。job请求不可选入口。
- 初始策略在prepare_model线程内、初始评估验证和保存之后、role/announce之前；闭包绑定job/model_preparation事件，active_job不存在时可用。
- 同一deployed callable传入Coordinator radio_policy_fn wrapper；wrapper每次绑定round_index与安全调用窗口，结束后旧context不能再配置。控制事务失败锁存，finally强制上抛，策略catch不能续训。
- 当前控制接口 reconfigure_radio(patch,target_nodes,*,job_id,stop_event)直接使用。host预校验正常组合表后，registry.admission_window内冻结sorted(fixed targets UNION online clients)；后到节点不加入当前事务。
- 控制owner已在真实session下内部livenesslease，initial无watch安全skip；不由host猜session或再造租约。
- result保存job.radio_results，含request_id/job_id/round_index/session_id/phase/transaction_state/before_config/requested_patch/target_config/target_nodes/confirmed_nodes/unconfirmed_nodes/effective_config/recovery_state/error_code/reason。actual有效值仅来自RadioSwitchResult.effective_config；unknown为None且RADIO_ERROR阻新作业；普通回收不恢复初始值、不写startup JSON。
- 下一轮速率只在成功事务返回后设置server_role.transport.uftp_rate_kbps现有属性；初始成功后创建role自然传入current confirmed rate。无Runtime/role新接口。
- Console需主线透传radio_results（当前仅算法投影已有radio_policy_entry，未替主线改console）。

检查进展：新增17pass，mypy两文件通过；合并自有隔离宿主76项首次75pass/1fail，唯一失败为0.1s准备预算先耗在模型冻结而未进入policy。测试预算调为1.0s以保持验收“policy阶段被准备期限取消”语义，正在最终复跑。

收尾更新：新增19pass，两文件mypy成功，diff check/编译成功。新增“控制调用未获结果抛异常”保留effective=None+RADIO_ERROR，及fixed participants即使已OFFLINE仍在事务集合；不访问板卡。最近合并自有隔离宿主76pass，之后仅新增上述2项并针对性复验。
初始已成功无线事务、但后续role准备失败也保留recent.radio_results和已确认current config。恢复未知时recent.recovery_error=RADIO_CONFIGURATION_UNKNOWN，不用task/clientready冒充无线恢复。纯policy无新预算、初始归prep、真实control事务实际session lease沿owner实现。

迁移 test_fl_radio_runtime_application：CONFIRMED 后缺确认现在明确断言 daemon.get_status_report()['radio'] is None、result.effective_config is None；单独 result.local_committed_config 保留本机已提交 downlink_mcs=4。rolled_back仍断言旧配置，finalized断言有效新配置。为避开当前并行Console缺常量的无关错误，测试使用daemon.radio_config构造patch与公开daemon.reconfigure_radio，running_server为127.0.0.1临时UDP。迁移3pass，server mypy成功，compile/diff check通过。未改control/console。
