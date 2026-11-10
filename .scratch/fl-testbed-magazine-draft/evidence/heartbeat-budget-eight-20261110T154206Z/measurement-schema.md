# heartbeat-budget-eight 采集 schema 与窗口

## 任务边界

- 唯一任务 root：`heartbeat-budget-eight-20261110T154206Z/`。
- 固定拓扑：`fl00` + `[fl01, fl02, fl03, fl05, fl06, fl07, fl08, fl09]`；`fl04`、`fl10` 不启动、不纳入样本。
- 固定 release：`66f15358d309d646d7dca887624a66c46099a54a`，native `44d6bbf500f51df4d0b3ae8cd7a53c4a45cb55b09ca66cb5217e7ac75674feb2`。
- 固定无线基线：`149/HT40/Long GI/index18/DL3/UL4/RS(8,12)/30/5`，保留 `230ms` 单 ACK 和 `10s` 失活阈值；本任务不改门限、不改 retry、不扩参数矩阵。
- `fl00` 身份硬门槛：MAC `fc:22:1c:10:01:19`、`rtl88xxau_wfb`、USB `2-1.1:1.0`、`5000M`。
- 新 capture 只在八个 client 上绑定各自已有 TUN 的 AF_PACKET 接收 socket；`fl00` 不作为 measurement capture host，不生成 server RTT。`lo` 只用于本地动态测试，不是生产样本。脚本不向生产 UDP/TUN 发包，不切频，不启动训练，不安装依赖。

## 证据层级

1. `wire_ack_candidate`：同一 host `time.monotonic_ns` 中，出站 heartbeat 到入站 ACK 的身份匹配和 RTT。它只能回答 230ms 预算的线速候选，不表示 daemon accepted。
2. `daemon_liveness_observation`：capture 窗口对应的 daemon bounded journal、寻频/失活 warning 次数和时间。它用于交叉核对 hunt，不能把 pcap ACK 收到时间改写为 `_idle_liveness_anchor`。本批不启动 server capture，不生成 server-side RTT。
3. `application_acceptance_observed`：本任务不部署 private anchor 或 debug instrumentation；因此为 `false/unknown`。10s gap 不称实测 accepted gap，只能报告 `wire_receive_gap_candidate` 加 daemon hunt evidence。

## 样本识别

`capture-heartbeats-batch.py` 输出一个 host JSON。每个事件至少包含：

- `mono_ns`、`wall_ns`、`direction`、`source={ip,port}`、`destination={ip,port}`、`payload_sha256`、`kind`、`body`。
- heartbeat identity：`node_id`、`boot_id`、`session`、`request_timestamp_ms=timestamp_ms`、`channel=current_channel`。
- ACK identity：`node_id`（由客户端目的 IP 得出）、`boot_id`、`session`、`request_timestamp_ms=heartbeat_timestamp_ms`、`channel`。
- request 与 ACK 必须拥有相同 `node_id/boot_id/session/request_timestamp_ms/channel`，且 source/destination IP+port 为严格反向端点，才生成 `sample_id`；client 先限定本机 node，再做完整反向端点匹配。
- `confirmed=false`、缺字段、非法 JSON、来源不反向匹配、超窗口、或重复包均不进入有效 ACK 计数；重复包保留在 `events`，标记 `duplicate_packet=true`，单独计数。
- 每个 host JSON 的 summary 固定包含 `request_count`、`paired_confirmed_count`、`timely_count`、`late_count`、`unmatched_count`、`right_censored_count`、`challenge_count`。`candidate_ratio_paired_confirmed=timely/paired_confirmed` 反映 paired ACK 中晚到比例；`candidate_ratio_window_requests=timely/(request_count-right_censored_count)` 反映窗口内请求收到 timely ACK 的比例，两个分母同时保存。未配对只写本窗口未观察到对应有效 ACK，不称物理 loss。
- `within_230ms_candidate` 使用严格 `<230ms`；恰好 `230ms` 归入 `late_count`。

`session=null` 的首次 challenge 只作为 invalid/challenge 观察；只有后续同身份的 `confirmed=true` ACK 才能成为 wire candidate。ACK 收到不等同 accepted。

## 时钟与 drops

- RTT 只用同一 host 的 `time.monotonic_ns`，不做跨板 monotonic 相减。
- 每 host 保存 monotonic 实现、分辨率、start/end monotonic、start/end wall；wall 只用于阶段对齐，字段明确记录跨 host alignment error 未消除。
- socket 保存 `PACKET_STATISTICS` 前后 `packets/drops`；drops 不隐瞒，capture 错误和 start/stop 均写入结果。
- phase marker 由远端 capture 和 fl00 runner 各自记录本机 monotonic/wall；模型阶段仅以 wall 对齐标记，不把远端 monotonic 与 runner monotonic 比较。

## 窗口

### Idle

- 准入只读九板 audit 通过后，八个 client 各自被动抓取约 `120s`，目标约 `60 samples/client`（2s heartbeat）；不启动 server capture。
- 统计 `request_count`、`paired_confirmed_count`、`timely_count`、`late_count`、`unmatched_count`、`right_censored_count`、`challenge_count`、`duplicate_packet_count` 及两个显式分母的 candidate ratio；不写应用 accepted 计数。
- 同时取得对应 bounded daemon journal 的寻频、`10s` warning、进程 start/stop、错误和 drops。窗口前后的 prepare/cleanup 记录单独标记，不能把切频抑制算成 idle loss。

### Model

- 只有父 review capture schema/样本识别并放行后执行一次等量模型上下行负载；使用现有已审 runner/制品，不新增训练、不经管理网传模型、不改协议行为。
- 载荷固定既有 `44,770,302` bytes / SHA `4b55cee2f0bdcfbbf610167a505efee0e2c50e3306fed02bda3613807c234e96`。模型下行和上行各自硬截止 `600s`；全 capture 有 `1500s` 上界，超过即退出并保留 partial/log/manifest，不 retry。
- phase 记录必须区分 `prepare`、`model_downlink`、`model_uplink`、`post_cleanup_audit`；只有 downlink/uplink 的 capture 进入负载统计，前后准备和恢复不算 idle loss。
- 每方向记录 wire RTT、230ms timely candidate ratio、invalid/duplicate/drops、ACK receive gap candidate，以及同窗 daemon hunt journal；不从 `cycle*8*35` 推导 230ms 必须变更，也不从任何单次 gap 直接调大 10s。

## gap 结论字段

- `max_wire_ack_receive_gap_ms_by_identity` 按 `node+boot_id+session+channel` 分段，保留 wire ACK receive gap；`max_timely_candidate_send_gap_ms_by_identity` 只取 timely candidate 的 request send monotonic 时间差，用于 liveness 初筛，明确为 non-app-anchor。
- `actual_accepted_anchor_gap_ms`：本任务固定为 `null`，因为 `_idle_liveness_anchor` 是客户端发送时间、默认无成功 ACK debug 日志，且不部署 private instrumentation。
- `daemon_hunt_count` / `daemon_hunt_log_evidence`：bounded journal 中的可见寻频证据。
- `gap_evidence_class`：`wire_receive_gap_plus_daemon_log_inference` 或 `no_observed_hunt`，不写 `actual_accepted`。

最终父侧结论必须分别写：模型通信成功/失败、应用 liveness 可见性、deadline 过晚 ACK 候选；三者不能合并为“通信成功所以 liveness 成功”。
