# 08 RoleService 启动顺序修复

范围：仅修改 `wfb_ng/fl/service.py`，新增 `wfb_ng/tests/test_fl_service_start_order.py`；不改 transport/client/server，不部署、不执行硬件测试，不 commit。该修复归属 08，后续由主线在 07 提交后安排独立必要修复提交。

问题依据：diagnose08 已确认旧 main 在 load_role_service/start 前 load_algorithm；MNIST 顶层 import torch 可使客户端 idle link 已拆、任务 link 尚未建立的空窗增长。SIGTERM handler 只设置停止事件，旧流程会在同步 import 返回后继续 service.start 和算法线程启动。

实现：保留角色／算法配置 JSON 读取和检查，随后 load_role_service/start，使任务 link／接收器先启动，再按原 arity=3 动态加载算法。在 load_role_service 前、加载角色对象后但 start 前、start 后但重型加载前、重型加载返回后均检查 stop_event；已收到停止就从 main 返回，finally 清理已取得的真实 service 并保留算法线程 join。没有新线程包装算法导入，没有新 timeout/grace/threshold，不声称 Python import 可以中断；导入期间的停止必须等待该同步导入返回后才能在检查点生效。基础资源 ready 仍不等同准备报告，导入失败仍走现有 prepare 错误报告／失败返回，不发 prepared。

TDD：新增公共 main 入口、fakeRoleService 与 Event 控制 loader 的首个用例，旧实现 1 failed，观察顺序为 import → load_service → start → close；修复后 load_service → start → import → close，import 期间停止后不运行算法。使用已经存在的完整 role JSON fixture，不新建 fixture 平台。额外覆盖：配置阶段停止、角色对象加载阶段停止、start 完成时停止、导入失败 prepare 报告、非法算法 JSON 在启动前拒绝、真实 RoleService 清理次序、真实 loader 拒绝旧二参数入口。导入测试的同步使用 Event，无固定 sleep。

验证：

- 新增启动顺序测试 8 项。
- `python -m pytest -q wfb_ng/tests/test_fl_service_start_order.py wfb_ng/tests/test_fl_service_deadlines.py wfb_ng/tests/test_fl_client_service.py -k 'not test_service_rejects_legacy_two_argument_entry_and_reports_error'`：65 passed，1 deselected，9.08s，2 个已有依赖弃用警告。
- 上述完整集合首次运行时，旧 `test_service_rejects_legacy_two_argument_entry_and_reports_error` 失败：其 stub `_read_config` 只返回 `{'role': 'client'}` 且不 stub load_role_service，依赖旧算法导入先行，故新流程在真实 load_role_service 访问 link_args 时 KeyError。未改其 owner 文件；新增 `test_legacy_arity_still_reports_invalid_configuration_after_link_start` 使用完整角色 JSON + fake role service + 真实 load_algorithm，确认仍回报 invalid_configuration／prepare，未运行旧入口。
- `python -m mypy --follow-imports=silent --check-untyped-defs wfb_ng/fl/service.py wfb_ng/tests/test_fl_service_start_order.py`：Success: no issues found in 2 source files。
- `git diff --check -- wfb_ng/fl/service.py`：通过。

准备期限数值及宿主管理不变；没有延长期限或忽略准备错误。该本地验证不代替现场任务 link 心跳、真实导入时长或后续少轮训练／停止验收。


## 最终追加：迁移旧 arity 测试，不再排除

按新增授权，仅迁移 `test_fl_client_service.py::test_service_rejects_legacy_two_argument_entry_and_reports_error`：删除不完整 `_read_config` stub，临时文件写入合法完整 client role JSON，由 main 的真实 schema 校验；mock RoleService 的资源边界，让新流程真实经过 start 再进行原 loader 的 arity 检查。保留原 error／invalid_configuration 业务断言，并补 prepare 阶段、start/close 顺序和报告接收线程退出断言。其他测试函数未修改。模块动态 run 赋值改用 setattr，使该修改函数自身可通过类型检查。

最终无筛选验证：

`python -m pytest -q wfb_ng/tests/test_fl_service_start_order.py wfb_ng/tests/test_fl_service_deadlines.py wfb_ng/tests/test_fl_client_service.py`

**66 passed，2 warnings，9.15s；没有 deselect。** 其中启动顺序新增 8 项、schema 39 项、client_service 19 项。本结果替代前文排除旧用例的中间结果。

类型与适用范围：

- `python -m mypy --follow-imports=silent --check-untyped-defs wfb_ng/fl/service.py wfb_ng/tests/test_fl_service_start_order.py wfb_ng/tests/test_fl_service_deadlines.py`：Success: no issues found in 3 source files。
- 从原文件 AST 原样提取本次修改函数及顶层 imports 到临时文件，运行同样 mypy 函数体检查：Success: no issues found in 1 source file；没有修改或屏蔽该函数的检查项。
- 整个既有 test_fl_client_service.py 运行该检查仍有 **19 个其他函数的既有错误**；以 `git show HEAD:wfb_ng/tests/test_fl_client_service.py` 配合 mypy --shadow-file 对照，原 HEAD 有 20 个错误。本次消除了对应修改函数的 1 个动态模块属性错误，其余函数未越界改动，不能声称整个旧测试文件 mypy 全绿。
- owner 文件 git diff --check 通过。

没有硬件操作、部署或提交。这里只确认启动顺序缺陷与停止检查点修复通过本地验证，**不声称解决全部 idle 链路波动根因**；后续由主线在 07 commit 后独立提交 08 必要修复，再冻结部署版本并复测。
