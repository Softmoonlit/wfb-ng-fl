# 06 Coordinator deadline evidence

基线：05 `d9386f9`。本次只修改 `wfb_ng/fl/coordinator.py` 与 `wfb_ng/tests/test_fl_coordinator.py`，未修改 server/client/control/Runtime/Transport。

## TDD

先加入期限、首因、协作退出和 I/O/round 分界测试，旧实现红灯：

- `cancel_callback` 尚不存在；
- 非有限 timeout 未拒绝；
- publish、waitupdates、aggregate、evaluate、report 没有完整 round supervisor；
- `abort()` 会在计算线程退出前写 ABORTED 摘要。

绿灯结果：

```text
python -m pytest -q wfb_ng/tests/test_fl_coordinator.py
32 passed
```

重点覆盖：

- round deadline 在 `prepare_round()` 之前建立，覆盖 publish、waitupdates（含客户端训练）、aggregate、evaluate、round report；
- publish 使用 `min(round_deadline, publish_start + io_timeout)`，不向 Runtime 四接口传 timeout；
- publish 返回晚于旧 I/O deadline 时，先裁决 `io_timeout`，不会被切回 round deadline 吞掉；
- round 到期和 I/O 到期都设置共享 `context.stop_event`，调用 `runtime.abort(reason)`，并只调用一次 `cancel_callback(reason)`；
- callback 先于可能阻塞的 Runtime abort，callback 只调度不 join；计算未协作退出时 summary、终态回调和回收证明均不提前形成；
- late operator stop 不能覆盖已裁决的 `round_timeout`；
- 操作者首因仍为 `aborted`，效果事实和 05 的 `execution_result/effect_result` 保留；
- 每个 subtest 使用 fresh Runtime/stop context，避免跨测试共享 Event 污染。

## Interfaces

Coordinator 构造新增可选参数：

```python
cancel_callback: Optional[Callable[[str], None]] = None
```

回调参数只取首因字符串：`"round_timeout"`、`"io_timeout"` 或操作者停止原因。回调必须快速返回，只负责唤醒/调度宿主 barrier；不得 join Coordinator、算法计算或 Runtime 回收。

Coordinator 公开只读属性：

```python
current_round_deadline: Optional[float]
```

它返回当前轮次的绝对 `time.monotonic()` deadline；`begin_round()` 在 `prepare_round(index)` 之前建立；轮次结束或 Coordinator 退出后为 `None`。server06 的 `complete_round()` 可以直接读取该属性，不需要宿主自建轮次时钟，也不改变 `round_completion(index)` 签名。

`JobConfig.round_timeout_seconds` 与 `io_timeout_seconds` 均接受有限正 `int/float`，拒绝 bool、NaN、正负无穷和非正值，并冻结为 `float`。

## Verification

```text
python -m py_compile wfb_ng/fl/coordinator.py wfb_ng/tests/test_fl_coordinator.py
python import JobConfig/FLCoordinator: passed
mypy --follow-imports=silent --ignore-missing-imports --check-untyped-defs wfb_ng/fl/coordinator.py
Success: no issues found in 1 source file
python -m pytest -q wfb_ng/tests/test_fl_coordinator.py \
  wfb_ng/tests/test_fl_training_host.py \
  wfb_ng/tests/test_fl_mnist.py \
  wfb_ng/tests/test_fl_real_job.py
120 passed, 3 skipped
```

测试运行产生的两条系统依赖弃用 warning 不影响结果。
