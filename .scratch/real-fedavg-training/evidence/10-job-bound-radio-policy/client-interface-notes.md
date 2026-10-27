# 10 客户端窄分片接口（供主线转 control owner）

当前可调用工具没有 send_message，故落盘共享。

客户端已接 `ControlPlaneClient.on_radio_prepare(job_id: Optional[str], config: RadioConfig) -> None`。必须在 PREPARE_ACK 之前、任何物理应用之前调用，异常则拒绝。任务绑定只接受 same job；空闲客户端接受带 job 的事务（初始策略／在线非参与节点）。客户端不新增 wire 字段／window token。

本地安全依据：JobSandbox 在合法 prepared/round_complete notice 到达时、调用 forward 之前记录 `_radio_boundary_round`；即使 TASK_READY/ROUND_READY ACK 还没返回，也已满足 submit 返回后的本地事实。PREPARE 门禁读取当前任务已有 Runtime `rounds/*/round-state.json`：只接受 role=client/state=succeeded 且成功轮数与 notice 一致；初始prepared允许0轮。model_received 在 wait_for_model 返回前原子落盘，因此下一轮训练／传输／损坏状态不沿用旧notice。发布与事务之间的跨进程竞争仍由 server publish 屏障串行解决，无新通知／通道。

任务应用复用原 apply_link_radio(config.radio_control_port, bandwidth, mcs)，保持原 RoleService/link PID与TUN，不ensure_idle，不启动idlelink；ControlPeer仍负责channel/power adapter应用和完整before_config回滚。on_radio_applied允许已验证事务在stop时恢复旧配置，直到任务link退出；新PREPARE仍拒绝stop。

任务角色link_args已显式接入daemon的radio_control_port。普通20缺表依旧由原validator在PREPARE前拒绝；未改表、协议回滚、service或生产默认值。

当前public callback+真实fakev6 loopback动态端口检查23 passed（jobradio+既有clientradio）。包括prepared ACK前／ROUND_READY ACK前同PID/TUN更新，runtime model_received/submitting_update/failed拒绝，错job／改信道拒绝。完整wire事务检查与mypy等待control声明并调用on_radio_prepare。

待确认：bind_job_context/clear 的准确签名和生命周期作用。客户端可在TASK受理、控制重建时绑定当前job，并在恢复ACK成功后解除；请owner声明client端还是server端接口，避免误接。

## HEAD 69a94d8 接续：已完成 wire 门禁与 bind/clear 接线

已从 control.py 确认准确签名 bind_job_context(job_id: str), clear_job_context(job_id: str)。宿主在TASK受理、合法job配置拒绝、直接trigger_job、ControlPeer重建时bind；恢复ready ACK返回后先clear，clear成功后才清daemon绑定、发IDLE。pending事务导致clear拒绝时保持门禁。on_radio_prepare已正式声明并在PREPARE_ACK前执行，mypy通过。

新增真实回环UDP注入 ControlPeer._broadcast_loop 的wire检查：prepared/round_complete报告ACK前完成PREPARE、COMMIT、PING、FINALIZED、CONFIRMED；原任务PID、TUN owner及同角色保持；model_received/submitting_update/failed在PREPARE被拒绝，强行后发COMMIT不产生物理修改；不同job在协议层拒绝。另补已有idle clientradio检查，同job事务可更新在线非参与节点的原idle进程，不重建TUN。全部真实socket只127+动态端口，ACK/心跳出口FakeControlPeer，无硬件操作。

当前客户端范围119 passed（临时排除新增1项control协议回滚红灯），mypy通过。证据client/focused-tests.txt、client/typecheck.txt。

### 请主线紧急转 control owner explore06：唯一剩余跨分片红灯

当前 control.py `_rollback_switch` 仍在3206行调用 `_apply_channel_switch(DEFAULT_BENCHMARK_CHANNEL, ...)`，未按票10要求恢复 `previous.channel`。新增真实wire `test_wire_reversible_failure_restores_operator_channel_and_same_owner` 从153开始，已确认before_config.channel=153，提交前STOP+ABORT后仍错误回157。没有新windowtoken，没有第二个operator_channel。

红灯：client/red-operator-channel-rollback.txt。修复权属control owner，本client slice未修改control.py，也未在daemon伪造缓存掩盖。修复后复验：
`python -m pytest -q wfb_ng/tests/test_fl_client_jobradio.py wfb_ng/tests/test_fl_client_radio_application.py wfb_ng/tests/test_fl_client_recovery.py wfb_ng/tests/test_fl_client_service.py wfb_ng/tests/test_fl_client_daemon.py`

当前可调用工具仍只有functions.read/bash/edit/write，无send_message；已主动完成接口代码核对并落盘共享，无法声称已私信owner。

## control 三修后的测试迁移与复验

control owner 现行语义：不同 job 的 PREPARE 返回 `PREPARE_ACK(ok=false, job_id, session_id)`，不是静默无输出。`test_wire_different_job_rejected_before_physical_change` 已迁移为核对拒绝 ACK，并继续核对 effective config、channel history、pending 状态、fake-v6 更新文件均不变；后续 COMMIT 也无物理修改。

HEAD 69a94d8 后 10 专项正好 18 tests，全部通过。当前 client guardchecks（jobradio、client_radio_application、client_recovery、client_service、client_daemon、job recovery/reports、terminal registry、evidence）共 **167 passed**；client_daemon focused mypy 通过；限定文件 diff check 通过。无硬件、无部署、无 core/control 修改、未 commit。
