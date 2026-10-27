# 06 Transport 窄 I/O 预算检查

基线：`d9386f9`。仅产品修改 `wfb_ng/fl/transport.py`，新增 `wfb_ng/tests/test_fl_transport_deadlines.py`；不提交。

依据：06 工单及 spec 的期限／停止要求、`docs/design/Transport-Backend传输契约.md` 的 HTTP 有界等待和最终 manifest 不可逆提交、`docs/design/系统分层与跨层契约.md:110`。绝对 HTTP 预算与设计无冲突；UFTP 自然传输仍采用原生协议终态，未新增 UFTP operation 总期限或 Runtime 参数。Server publish 的监督取消由宿主负责。模型候选等待可跨越训练／策略等待，不套 HTTP I/O timeout。

实现：client 在操作开始固定 monotonic 期限，首次地址的一次 connect、headers、100、各 body chunk、最终响应和错误 body 的底层 socket I/O 使用剩余预算。server 在连接读取 headers 时使用绝对预算，预留接受上传后固定该 operation 的期限，100、body 各底层 recv 和响应共享该接受预算；提交前检查预算，manifest 原子提交后不追溯失败。BufferedReader 的原始读取适配使用标准库 io/socket，每次 recv 重新计算 remaining，避免单次 read/readline 内慢滴流续期。client 在 connect 前登记 socket；两端 close 显式 shutdown 连接并等待 operation／请求线程退出，server 也等待可靠失败回调完成。

TDD 小证据：

- 初始 client 慢滴流／阶段预算用例：3 failed，均为期望失败但旧实现成功返回。
- server close 用例：旧行为等待 body socket timeout，`callback_entered.wait(2)` 失败；修复后通过。
- client connect 取消用例：旧行为 connect 返回前没有登记 socket，`shutdown.wait(1)` 失败；修复后通过。
- 最终 `python -m pytest -q wfb_ng/tests/test_fl_transport_deadlines.py`：11 passed，2.41s。覆盖慢滴流、跨 phase、多 body chunk、server body、关闭唤醒与可靠结果交付回收、connect 取消、已提交成功响应预算耗尽、正常 loopback 上传、模型等待跨 I/O 预算。
- `python -m mypy --follow-imports=silent --check-untyped-defs wfb_ng/fl/transport.py wfb_ng/tests/test_fl_transport_deadlines.py`：Success: no issues found in 2 source files。包含测试函数体。
- `python -m pytest -q wfb_ng/tests/test_fl_file_job.py wfb_ng/tests/test_fl_evidence.py`：21 passed，5.57s，2 个现有依赖弃用警告。

扩展检查限制：包含 training_host/client_service/radio_runtime_application 的更大集合达到 60s 上限，不计通过。`-x` 定位首个失败为 training_host 的 `test_initial_evaluation_precedes_runtime_and_announce_and_uses_explicit_algorithms`：`缺少必填参数 'io_timeout_seconds' 或值非法，必须为大于 0 的正整数`。当前测试 fixture 使用 0.2，失败发生在 Transport 创建前，未越界修改。另一次不含 training_host 的 52 项扩展集合输出 43 个通过点后也达到 60s；没有把该次未结束检查计为通过。

测试采用受控 clock/socket 以及本地 loopback；并发用 Event 同步，无新增依赖、无以固定 sleep 掩盖竞态。未执行硬件验收，也不据此声称 06 全范围／实机通过。


## 追加：RoleService 小数预算 schema

按新增授权，仅追加修改 `wfb_ng/fl/service.py` 与新增 `wfb_ng/tests/test_fl_service_deadlines.py`。`io_timeout_seconds` 移出整数循环，严格接受 int/float，拒绝 bool、NaN、正负无穷、非正数、非数值及无法转成有限 float 的大整数，校验后统一转为 float。其他整数参数规则保持不变。

测试复用 Stage 3 完整 server_cfg 形状，并替换 client 专属字段；用临时 JSON 经公开 `load_role_service()` 加载真实 RoleService／Transport，替代外部可执行文件探测，不启动链路或 UFTP。TDD 先获得 6 failed（小数被整数 schema 拒绝／整数未转 float），修复后 6 passed，再补非法输入矩阵。

- `python -m pytest -q wfb_ng/tests/test_fl_service_deadlines.py wfb_ng/tests/test_fl_transport_deadlines.py`：43 passed，2.51s（schema 32 项，Transport 11 项）。
- `python -m mypy --follow-imports=silent --check-untyped-defs wfb_ng/fl/service.py wfb_ng/tests/test_fl_service_deadlines.py`：Success: no issues found in 2 source files。
- `git diff --check -- wfb_ng/fl/service.py`：通过。
- 附加 training_host／Stage 3 回归在收集时受并行编辑文件 `wfb_ng/fl/control.py:1590` 的 `SyntaxError: unmatched '}'` 阻断，未计为通过，未改其他 worker 文件。该次也含一个错误的 Stage 3 node id，不能据此声称该分支运行过。

未 commit；clientJobConfig／TASK 和 Coordinator JobConfig 的同步由各自 worker 负责。


## review06-spec P1／Standards P2 修复

P1 更正：之前“server 在预留接受后固定新预算”的实现会刷新 header 已用预算，已移除该重置。HTTP handler setup 建立的同一个绝对 deadline 覆盖 request headers、100、body 和最终 response。预留接受仍只建立上下文和正式结果责任，不能刷新 I/O 期限；未接受 header 读取本身也受 setup deadline 约束。client 继续共用其操作开始期限。

真实 loopback TDD（受控 monotonic/socket，无固定 sleep）：逐字节 header 消耗 0.6 秒，再让 body 消耗 0.5 秒；以及 header 0.6 + body 0.2 + response 0.3。旧实现两项都在 1.1 秒成功，测试为 2 failed；修复后 1 秒耗尽，前者可靠交付 upload_incomplete 并清理临时文件，后者已提交 manifest 不回调失败且保留成功文件，最终 response 缺失。100 的阶段同步由服务端写出边界触发，避免 client 读到100与 server 已进入body recv 的竞态。

P2：传输契约没有明确承诺 DNS；实际任务 TUN 为数值 IPv4。新增共享 validate_server_http_host，ClientTransport 构造器和 RoleService client JSON schema 统一使用标准库 IPv4Address 严格校验字符串 IPv4；拒绝域名、IPv6、空值、非法 IPv4 和整数形式。submit_update 直接 socket(AF_INET, SOCK_STREAM, IPPROTO_TCP) 并 connect 数值地址，不再 getaddrinfo；socket 在 connect 前注册到 operation，close 可显式 shutdown 并等 operation 退出。没有新增 DNS 线程、兼容解析或 fallback。

P2 TDD：5 个 constructor 拒绝用例与一个公开 submit/close 禁用 getaddrinfo 的正常 loopback 上传用例，旧实现 6 failed，修复后通过；另补 client RoleService 公开加载入口非法地址矩阵 7 项。

最终验证：

- `python -m pytest -q wfb_ng/tests/test_fl_transport_deadlines.py wfb_ng/tests/test_fl_service_deadlines.py wfb_ng/tests/test_fl_file_job.py wfb_ng/tests/test_fl_evidence.py`：79 passed，9.15s，2 个现有依赖弃用警告。其中 Transport 19 项、Service schema 39 项、文件作业／证据 21 项。
- `python -m mypy --follow-imports=silent --check-untyped-defs wfb_ng/fl/transport.py wfb_ng/fl/service.py wfb_ng/tests/test_fl_transport_deadlines.py wfb_ng/tests/test_fl_service_deadlines.py`：Success: no issues found in 4 source files。
- 四个 owner 文件 `git diff --check` 通过。

本轮仅修改 transport.py、service.py 和两个新增测试及本证据；未改其他核心文件，未 commit。基线仍为 d9386f9。前文旧预算描述以本节为准。
