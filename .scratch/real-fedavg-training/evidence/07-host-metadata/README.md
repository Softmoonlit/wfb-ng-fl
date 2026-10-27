# 07 服务端宿主 metadata wrapper

基于 06 commit `e53de00`，未 commit、未部署。仅修改 `wfb_ng/fl/server_daemon.py` 与新增 `wfb_ng/tests/test_fl_training_snapshot_host.py`。

实现边界：

- 仅当部署聚合入口为 `wfb_ng.fl.mnist:aggregate_entry` 时接入 wrapper；generic aggregate 保持不透明，不解析 torch/update。
- wrapper 先调用真实 aggregate，再按冻结 target nodes、当前输入模型 SHA、客户端准备报告 sample_count 调用 `mnist.read_update_metadata`。
- 每个 job 使用独立 `training_by_round` cache；metadata 按节点逐项写入，后续节点失败时保留此前已取得 facts，缺失项不伪造成功。
- `ROUND_COMPLETED` 事件及 Coordinator summary 均先 deepcopy，再合并 training facts；不修改 Coordinator 共享 event/summary 对象。
- terminal 首次 execution/effect/summary 事实不被 metadata 接线改写；失败 summary 可保留已经读取的 training facts。
- 没有新增通用 plugin callback 或 Runtime 接口。

检查：

- `pytest.txt`: 新增 metadata host tests，4 passed。
- `focused-host.txt`: 新增接线及既有关键宿主场景，7 passed。
- `typecheck.txt`: server_daemon.py 与新增测试 mypy 通过。
- `git diff --check` 通过。
- 测试只使用 tmp_path、内存 aggregate/read helper monkeypatch，不创建 UDP socket，不访问真实 10.80.*、255.255.255.255、9000/9001/9002/9200。

最终 focused 宿主检查：7 passed；新增测试单独：4 passed；server_daemon.py 与新增测试 mypy 通过，diff check 通过。测试不会创建 UDP socket或访问生产端口。

P2 diagnostic 契约修复：Console record_event 和宿主 round_results 继续接收 enriched 副本；共享 event_bus.publish 恢复发布原始 event。新增真实 publish_event + ServerEventBus.subscribe + 实际 Console snapshot 回归，验证 Console training 存在、总线无 training、总线接收原始对象且原 event 未修改。测试配置为 loopback 临时端口，拦截 sendto/connect/connect_ex 禁止全部网络出站，未启动网络服务。

`red-diagnostic-event.txt` 为修复前 enriched 事件泄漏的红测；`diagnostic-focused.txt` 最新新增测试文件 5 passed；`typecheck.txt` 两文件 mypy 成功；diff check 通过。未 commit、未改 issue、未修改 10 owner 文件。
