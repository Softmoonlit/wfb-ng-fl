#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

LOG_DIR="${LOG_DIR:-$PROJECT_ROOT/tests/logs/v6_uplink_namespace_$(date +%Y%m%d_%H%M%S)}"
RESULT_MD="$LOG_DIR/result.md"
BUILD_LOG="$LOG_DIR/build.log"

SERVER_NS="${SERVER_NS:-v6u-server}"
CLIENT1_NS="${CLIENT1_NS:-v6u-client1}"
CLIENT2_NS="${CLIENT2_NS:-v6u-client2}"

SERVER_DIR="$LOG_DIR/server"
CLIENT1_DIR="$LOG_DIR/client1"
CLIENT2_DIR="$LOG_DIR/client2"

SERVER_LOG="$SERVER_DIR/wfb_v6_uplink.log"
CLIENT1_LOG="$CLIENT1_DIR/wfb_v6_uplink.log"
CLIENT2_LOG="$CLIENT2_DIR/wfb_v6_uplink.log"
CLIENT1_PING_LOG="$CLIENT1_DIR/ping.log"
CLIENT2_PING_LOG="$CLIENT2_DIR/ping.log"
CLIENT1_QUEUE_SUMMARY_JSON="$CLIENT1_DIR/uplink_queue_summary.json"
CLIENT2_QUEUE_SUMMARY_JSON="$CLIENT2_DIR/uplink_queue_summary.json"
RUN_SUMMARY_JSON="$LOG_DIR/formal_2a_summary.json"
FORMAL_CONCLUSION_MD="$LOG_DIR/formal_conclusion.md"

SERVER_TUN_NAME="${SERVER_TUN_NAME:-v6us0}"
CLIENT1_TUN_NAME="${CLIENT1_TUN_NAME:-v6uc1}"
CLIENT2_TUN_NAME="${CLIENT2_TUN_NAME:-v6uc2}"
SERVER_TUN_ADDR="${SERVER_TUN_ADDR:-10.66.0.1/24}"
CLIENT1_TUN_ADDR="${CLIENT1_TUN_ADDR:-10.66.0.11/24}"
CLIENT2_TUN_ADDR="${CLIENT2_TUN_ADDR:-10.66.0.12/24}"
SERVER_TUN_IP="${SERVER_TUN_IP:-10.66.0.1}"
CLIENT1_TUN_IP="${CLIENT1_TUN_IP:-10.66.0.11}"
CLIENT2_TUN_IP="${CLIENT2_TUN_IP:-10.66.0.12}"

SERVER_CLIENT1_IF="${SERVER_CLIENT1_IF:-v6u-s1}"
CLIENT1_MGMT_IF="${CLIENT1_MGMT_IF:-v6u-c1}"
SERVER_CLIENT2_IF="${SERVER_CLIENT2_IF:-v6u-s2}"
CLIENT2_MGMT_IF="${CLIENT2_MGMT_IF:-v6u-c2}"
SERVER_CLIENT1_ADDR="${SERVER_CLIENT1_ADDR:-172.61.1.1/30}"
CLIENT1_MGMT_ADDR="${CLIENT1_MGMT_ADDR:-172.61.1.2/30}"
SERVER_CLIENT2_ADDR="${SERVER_CLIENT2_ADDR:-172.61.2.1/30}"
CLIENT2_MGMT_ADDR="${CLIENT2_MGMT_ADDR:-172.61.2.2/30}"
SERVER_CLIENT1_IP="${SERVER_CLIENT1_IP:-172.61.1.1}"
CLIENT1_MGMT_IP="${CLIENT1_MGMT_IP:-172.61.1.2}"
SERVER_CLIENT2_IP="${SERVER_CLIENT2_IP:-172.61.2.1}"
CLIENT2_MGMT_IP="${CLIENT2_MGMT_IP:-172.61.2.2}"

SERVER_NODE_ID="${SERVER_NODE_ID:-9}"
CLIENT1_NODE_ID="${CLIENT1_NODE_ID:-1}"
CLIENT2_NODE_ID="${CLIENT2_NODE_ID:-2}"
LINK_ID="${LINK_ID:-406}"
STREAM_ID="${STREAM_ID:-32}"
SERVER_AIR_PORT="${SERVER_AIR_PORT:-42000}"
CLIENT1_AIR_PORT="${CLIENT1_AIR_PORT:-42011}"
CLIENT2_AIR_PORT="${CLIENT2_AIR_PORT:-42012}"
GRANT_DURATION_MS="${GRANT_DURATION_MS:-120}"
GUARD_INTERVAL_MS="${GUARD_INTERVAL_MS:-20}"
PING_COUNT="${PING_COUNT:-5}"
PING_DEADLINE_SEC="${PING_DEADLINE_SEC:-15}"
STARTUP_WAIT_SEC="${STARTUP_WAIT_SEC:-1}"
LOG_INTERVAL_MS="${LOG_INTERVAL_MS:-200}"
CLIENT1_UPLINK_PAUSE_THRESHOLD_BYTES="${CLIENT1_UPLINK_PAUSE_THRESHOLD_BYTES:-64}"
CLIENT1_UPLINK_RESUME_THRESHOLD_BYTES="${CLIENT1_UPLINK_RESUME_THRESHOLD_BYTES:-32}"
CLIENT1_UPLINK_QUEUE_PACKETS_LIMIT="${CLIENT1_UPLINK_QUEUE_PACKETS_LIMIT:-8}"
CLIENT2_UPLINK_PAUSE_THRESHOLD_BYTES="${CLIENT2_UPLINK_PAUSE_THRESHOLD_BYTES:-4096}"
CLIENT2_UPLINK_RESUME_THRESHOLD_BYTES="${CLIENT2_UPLINK_RESUME_THRESHOLD_BYTES:-2048}"
CLIENT2_UPLINK_QUEUE_PACKETS_LIMIT="${CLIENT2_UPLINK_QUEUE_PACKETS_LIMIT:-1}"

FAIL_REASON=""
CLEANUP_VERIFIED="否"
SERVER_PID=""
CLIENT1_PID=""
CLIENT2_PID=""
CLIENT1_READY_ACCEPTS="0"
CLIENT2_READY_ACCEPTS="0"
CLIENT1_GRANTS="0"
CLIENT2_GRANTS="0"
CLIENT1_AUTHORIZED_SENDS="0"
CLIENT2_AUTHORIZED_SENDS="0"
CLIENT1_TUN_READ_PAUSE_TOTAL="0"
CLIENT1_TUN_READ_RESUME_TOTAL="0"
CLIENT1_TUN_READ_PAUSE_BYTES_TOTAL="0"
CLIENT1_TUN_READ_PAUSE_PACKETS_TOTAL="0"
CLIENT2_TUN_READ_PAUSE_TOTAL="0"
CLIENT2_TUN_READ_RESUME_TOTAL="0"
CLIENT2_TUN_READ_PAUSE_BYTES_TOTAL="0"
CLIENT2_TUN_READ_PAUSE_PACKETS_TOTAL="0"
RUN_TUN_READ_PAUSE_TOTAL="0"
RUN_TUN_READ_RESUME_TOTAL="0"
RUN_TUN_READ_PAUSE_BYTES_TOTAL="0"
RUN_TUN_READ_PAUSE_PACKETS_TOTAL="0"
RUN_REASSEMBLY_OVERFLOW_EVICT="0"
RUN_UNFINISHED_BLOCK_LIMIT="0"

log_info() { echo "[INFO] $(date '+%H:%M:%S') $1"; }
log_pass() { echo "[PASS] $(date '+%H:%M:%S') $1"; }
log_fail() { echo "[FAIL] $(date '+%H:%M:%S') $1" >&2; }

set_fail_reason() {
    if [ -z "$FAIL_REASON" ]; then
        FAIL_REASON="$1"
    fi
}

prepare_log_dir() {
    mkdir -p "$SERVER_DIR" "$CLIENT1_DIR" "$CLIENT2_DIR"
}

cleanup() {
    local rc=$?
    set +e

    for pid in "$CLIENT2_PID" "$CLIENT1_PID" "$SERVER_PID"; do
        if [ -n "$pid" ]; then
            kill "$pid" 2>/dev/null || true
            wait "$pid" 2>/dev/null || true
        fi
    done

    ip netns delete "$CLIENT2_NS" 2>/dev/null || true
    ip netns delete "$CLIENT1_NS" 2>/dev/null || true
    ip netns delete "$SERVER_NS" 2>/dev/null || true

    if ! ip netns list | grep -Fq "$SERVER_NS" && ! ip netns list | grep -Fq "$CLIENT1_NS" && ! ip netns list | grep -Fq "$CLIENT2_NS"; then
        CLEANUP_VERIFIED="是"
    fi

    render_result "$([ "$rc" -eq 0 ] && echo PASS || echo FAIL)"
    render_formal_conclusion "$([ "$rc" -eq 0 ] && echo PASS || echo FAIL)"
    exit "$rc"
}
trap cleanup EXIT

render_result() {
    local status="$1"
    cat > "$RESULT_MD" <<EOF
# v6 trusted_plaintext 上行 namespace 验收结果

- 结果: $status
- 原因: ${FAIL_REASON:-无}
- 日志目录: $LOG_DIR
- 构建日志: $BUILD_LOG
- cleanup_verified: $CLEANUP_VERIFIED

## 拓扑

- server namespace: $SERVER_NS
- client1 namespace: $CLIENT1_NS
- client2 namespace: $CLIENT2_NS
- server TUN: $SERVER_TUN_NAME ($SERVER_TUN_ADDR)
- client1 TUN: $CLIENT1_TUN_NAME ($CLIENT1_TUN_ADDR)
- client2 TUN: $CLIENT2_TUN_NAME ($CLIENT2_TUN_ADDR)
- server air listen: $SERVER_CLIENT1_IP/$SERVER_CLIENT2_IP:$SERVER_AIR_PORT
- client1 air listen: $CLIENT1_MGMT_IP:$CLIENT1_AIR_PORT
- client2 air listen: $CLIENT2_MGMT_IP:$CLIENT2_AIR_PORT

## 证据

- server log: $SERVER_LOG
- client1 log: $CLIENT1_LOG
- client2 log: $CLIENT2_LOG
- client1 ping log: $CLIENT1_PING_LOG
- client2 ping log: $CLIENT2_PING_LOG
- client1 queue summary: $CLIENT1_QUEUE_SUMMARY_JSON
- client2 queue summary: $CLIENT2_QUEUE_SUMMARY_JSON
- run 2A summary: $RUN_SUMMARY_JSON
- formal conclusion: $FORMAL_CONCLUSION_MD
- client1 ready accepts: $CLIENT1_READY_ACCEPTS
- client2 ready accepts: $CLIENT2_READY_ACCEPTS
- client1 grants: $CLIENT1_GRANTS
- client2 grants: $CLIENT2_GRANTS
- client1 authorized sends: $CLIENT1_AUTHORIZED_SENDS
- client2 authorized sends: $CLIENT2_AUTHORIZED_SENDS
- client1 tun_read_pause_total: $CLIENT1_TUN_READ_PAUSE_TOTAL
- client1 tun_read_resume_total: $CLIENT1_TUN_READ_RESUME_TOTAL
- client1 queued_bytes_threshold pauses: $CLIENT1_TUN_READ_PAUSE_BYTES_TOTAL
- client1 queued_packets_limit pauses: $CLIENT1_TUN_READ_PAUSE_PACKETS_TOTAL
- client2 tun_read_pause_total: $CLIENT2_TUN_READ_PAUSE_TOTAL
- client2 tun_read_resume_total: $CLIENT2_TUN_READ_RESUME_TOTAL
- client2 queued_bytes_threshold pauses: $CLIENT2_TUN_READ_PAUSE_BYTES_TOTAL
- client2 queued_packets_limit pauses: $CLIENT2_TUN_READ_PAUSE_PACKETS_TOTAL
- run tun_read_pause_total: $RUN_TUN_READ_PAUSE_TOTAL
- run tun_read_resume_total: $RUN_TUN_READ_RESUME_TOTAL
- run queued_bytes_threshold pauses: $RUN_TUN_READ_PAUSE_BYTES_TOTAL
- run queued_packets_limit pauses: $RUN_TUN_READ_PAUSE_PACKETS_TOTAL
- run reassembly_overflow_evict: $RUN_REASSEMBLY_OVERFLOW_EVICT
- run unfinished_block_limit: $RUN_UNFINISHED_BLOCK_LIMIT
- 已覆盖: trusted_plaintext client TUN -> 空口 -> server TUN 首条新底座上行路径
- 已覆盖: 双客户端 READY/GRANT 持续推进与独立 TCP/IP 会话语义（以并发 ping 往返证明）
- 已覆盖: issue #22 固定容量用户态队列、双阈值水位与 TUN 反压 2A 摘要
- 已覆盖: issue #23 统一结构化摘要已接入 RX 重组窗口字段（当前 namespace 场景不主动触发溢出）
EOF
}

render_formal_conclusion() {
    local status="$1"
    local evidence_ok="否"
    local rerun_required="是"
    if [ "$status" = "PASS" ]; then
        evidence_ok="是"
        rerun_required="否"
    fi

    cat > "$FORMAL_CONCLUSION_MD" <<EOF
## v6 namespace 正式验收结论

- 运行批次：$(basename "$LOG_DIR")
- 对应 issue：#25
- 结论状态：$status

### 运行场景与前置条件
- run_kind: namespace
- link_security_mode: trusted_plaintext
- scenario_id: v6_namespace_uplink_backpressure_reassembly
- feedback_window_covered: false
- 执行入口: tests/acceptance/v6_uplink_namespace.sh

### 原始产物层引用
- 日志目录: $LOG_DIR
- 统一 2A 摘要: $RUN_SUMMARY_JSON
- 结论文件: $RESULT_MD
- 2B 证据: $SERVER_LOG, $CLIENT1_LOG, $CLIENT2_LOG, $CLIENT1_QUEUE_SUMMARY_JSON, $CLIENT2_QUEUE_SUMMARY_JSON, $CLIENT1_PING_LOG, $CLIENT2_PING_LOG

### 运行时长与关键事件计数
- client1 ready accepts: $CLIENT1_READY_ACCEPTS
- client2 ready accepts: $CLIENT2_READY_ACCEPTS
- client1 grants: $CLIENT1_GRANTS
- client2 grants: $CLIENT2_GRANTS
- client1 authorized sends: $CLIENT1_AUTHORIZED_SENDS
- client2 authorized sends: $CLIENT2_AUTHORIZED_SENDS
- tun_read_pause_total: $RUN_TUN_READ_PAUSE_TOTAL
- tun_read_resume_total: $RUN_TUN_READ_RESUME_TOTAL
- queued_bytes_threshold pauses: $RUN_TUN_READ_PAUSE_BYTES_TOTAL
- queued_packets_limit pauses: $RUN_TUN_READ_PAUSE_PACKETS_TOTAL
- reassembly_overflow_evict: $RUN_REASSEMBLY_OVERFLOW_EVICT
- unfinished_block_limit: $RUN_UNFINISHED_BLOCK_LIMIT

### 关键异常与风险信号
- 结果原因: ${FAIL_REASON:-无}
- 当前 namespace 场景不主动制造 RX 重组溢出；`reassembly_overflow_evict=0` 仍可为预期。
- 2B 细节仍保留在原始日志与 queue summary 中，不混入统一 2A 摘要。

### 结论
- 可作为 issue #25 namespace 正式证据: $evidence_ok
- 是否需要重跑: $rerun_required
EOF
}

require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        set_fail_reason "需要 root 权限"
        log_fail "$FAIL_REASON"
        exit 1
    fi
}

require_env() {
    if [ ! -e /dev/net/tun ]; then
        set_fail_reason "/dev/net/tun 不存在"
        log_fail "$FAIL_REASON"
        exit 1
    fi
}

build_binary() {
    log_info "构建 wfb_v6_uplink"
    if ! make wfb_v6_uplink >"$BUILD_LOG" 2>&1; then
        set_fail_reason "构建 wfb_v6_uplink 失败，查看 $BUILD_LOG"
        return 1
    fi
}

create_namespace() {
    local ns="$1"
    ip netns add "$ns"
    ip -n "$ns" link set lo up
}

attach_veth_pair() {
    local left_ns="$1"
    local left_if="$2"
    local left_addr="$3"
    local right_ns="$4"
    local right_if="$5"
    local right_addr="$6"
    local tmp_left
    local tmp_right

    tmp_left="${left_if}-tmp"
    tmp_right="${right_if}-tmp"
    ip link add "$tmp_left" type veth peer name "$tmp_right"
    ip link set "$tmp_left" netns "$left_ns"
    ip link set "$tmp_right" netns "$right_ns"
    ip -n "$left_ns" link set "$tmp_left" name "$left_if"
    ip -n "$right_ns" link set "$tmp_right" name "$right_if"
    ip -n "$left_ns" addr add "$left_addr" dev "$left_if"
    ip -n "$right_ns" addr add "$right_addr" dev "$right_if"
    ip -n "$left_ns" link set "$left_if" up
    ip -n "$right_ns" link set "$right_if" up
}

wait_for_interface() {
    local ns="$1"
    local ifname="$2"
    local tries="${3:-40}"
    local i
    for ((i = 0; i < tries; i++)); do
        if ip -n "$ns" link show dev "$ifname" >/dev/null 2>&1; then
            return 0
        fi
        sleep 0.25
    done
    return 1
}

start_server() {
    log_info "启动 server 新底座守护进程"
    ip netns exec "$SERVER_NS" "$PROJECT_ROOT/wfb_v6_uplink" \
        --role server \
        --tun-name "$SERVER_TUN_NAME" \
        --tun-addr "$SERVER_TUN_ADDR" \
        --node-id "$SERVER_NODE_ID" \
        --link-id "$LINK_ID" \
        --stream "$STREAM_ID" \
        --air-listen-port "$SERVER_AIR_PORT" \
        --known-clients "$CLIENT1_NODE_ID,$CLIENT2_NODE_ID" \
        --client-target "$CLIENT1_NODE_ID:$CLIENT1_TUN_IP:$CLIENT1_MGMT_IP:$CLIENT1_AIR_PORT" \
        --client-target "$CLIENT2_NODE_ID:$CLIENT2_TUN_IP:$CLIENT2_MGMT_IP:$CLIENT2_AIR_PORT" \
        --grant-duration-ms "$GRANT_DURATION_MS" \
        --guard-interval-ms "$GUARD_INTERVAL_MS" \
        --log-interval "$LOG_INTERVAL_MS" >"$SERVER_LOG" 2>&1 &
    SERVER_PID=$!
    sleep "$STARTUP_WAIT_SEC"
    kill -0 "$SERVER_PID" 2>/dev/null
}

start_client() {
    local ns="$1"
    local tun_name="$2"
    local tun_addr="$3"
    local node_id="$4"
    local listen_port="$5"
    local air_target_ip="$6"
    local logfile="$7"
    local pid_var="$8"
    local pause_threshold_bytes="$9"
    local resume_threshold_bytes="${10}"
    local queue_packets_limit="${11}"
    local queue_summary_file="${12}"

    log_info "启动 $ns 新底座守护进程"
    ip netns exec "$ns" "$PROJECT_ROOT/wfb_v6_uplink" \
        --role client \
        --tun-name "$tun_name" \
        --tun-addr "$tun_addr" \
        --node-id "$node_id" \
        --link-id "$LINK_ID" \
        --stream "$STREAM_ID" \
        --air-listen-port "$listen_port" \
        --air-target "$air_target_ip:$SERVER_AIR_PORT" \
        --uplink-pause-threshold-bytes "$pause_threshold_bytes" \
        --uplink-resume-threshold-bytes "$resume_threshold_bytes" \
        --uplink-queue-packets-limit "$queue_packets_limit" \
        --queue-summary-file "$queue_summary_file" \
        --log-interval "$LOG_INTERVAL_MS" >"$logfile" 2>&1 &
    printf -v "$pid_var" '%s' "$!"
    sleep "$STARTUP_WAIT_SEC"
    if ! kill -0 "${!pid_var}" 2>/dev/null; then
        set_fail_reason "$ns 启动失败，查看 $logfile"
        return 1
    fi
}

run_dual_ping() {
    log_info "并发执行双客户端 ping"
    ip netns exec "$CLIENT1_NS" ping -I "$CLIENT1_TUN_NAME" -c "$PING_COUNT" -W 1 -w "$PING_DEADLINE_SEC" "$SERVER_TUN_IP" >"$CLIENT1_PING_LOG" 2>&1 &
    local pid1=$!
    ip netns exec "$CLIENT2_NS" ping -I "$CLIENT2_TUN_NAME" -c "$PING_COUNT" -W 1 -w "$PING_DEADLINE_SEC" "$SERVER_TUN_IP" >"$CLIENT2_PING_LOG" 2>&1 &
    local pid2=$!

    if ! wait "$pid1"; then
        set_fail_reason "client1 ping 未在时限内成功"
        return 1
    fi
    if ! wait "$pid2"; then
        set_fail_reason "client2 ping 未在时限内成功"
        return 1
    fi
}

collect_queue_backpressure_metrics() {
    if ! eval "$(PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 - "$CLIENT1_QUEUE_SUMMARY_JSON" "$CLIENT2_QUEUE_SUMMARY_JSON" "$RUN_SUMMARY_JSON" "$SERVER_LOG" "$CLIENT1_LOG" "$CLIENT2_LOG" <<'PY'
import shlex
import sys

from wfb_ng.tests.v6_formal_summary import (
    SCENARIO_V6_NAMESPACE_UPLINK,
    build_summary,
    write_summary,
)

client1_path, client2_path, run_summary_path, server_log_path, client1_log_path, client2_log_path = sys.argv[1:7]


def load_json(path):
    import json

    with open(path, 'r') as fh:
        return json.load(fh)


def parse_last_reassembly(path):
    import re

    total = None
    limit = None
    with open(path, 'r') as fh:
        for line in fh:
            match = re.search(r'\tREASSEMBLY\t(\d+):(\d+)', line)
            if match:
                total = int(match.group(1))
                limit = int(match.group(2))
    if total is None or limit is None:
        raise SystemExit('missing REASSEMBLY stats in %s' % path)
    return {
        'reassembly_overflow_evict': total,
        'unfinished_block_limit': limit,
    }


client1 = load_json(client1_path)
client2 = load_json(client2_path)
rx_reassembly = {
    'server': parse_last_reassembly(server_log_path),
    'client1': parse_last_reassembly(client1_log_path),
    'client2': parse_last_reassembly(client2_log_path),
}
unfinished_limits = {entry['unfinished_block_limit'] for entry in rx_reassembly.values()}
if len(unfinished_limits) != 1:
    raise SystemExit('inconsistent unfinished_block_limit: %r' % sorted(unfinished_limits))

run_summary = build_summary(
    SCENARIO_V6_NAMESPACE_UPLINK,
    tun_read_pause_total=client1['tun_read_pause_total'] + client2['tun_read_pause_total'],
    tun_read_resume_total=client1['tun_read_resume_total'] + client2['tun_read_resume_total'],
    tun_read_pause_total_by_reason={
        'queued_bytes_threshold': client1['tun_read_pause_total_by_reason']['queued_bytes_threshold'] + client2['tun_read_pause_total_by_reason']['queued_bytes_threshold'],
        'queued_packets_limit': client1['tun_read_pause_total_by_reason']['queued_packets_limit'] + client2['tun_read_pause_total_by_reason']['queued_packets_limit'],
    },
    reassembly_overflow_evict=sum(entry['reassembly_overflow_evict'] for entry in rx_reassembly.values()),
    unfinished_block_limit=unfinished_limits.pop(),
)
write_summary(run_summary_path, run_summary)

values = {
    'CLIENT1_TUN_READ_PAUSE_TOTAL': client1['tun_read_pause_total'],
    'CLIENT1_TUN_READ_RESUME_TOTAL': client1['tun_read_resume_total'],
    'CLIENT1_TUN_READ_PAUSE_BYTES_TOTAL': client1['tun_read_pause_total_by_reason']['queued_bytes_threshold'],
    'CLIENT1_TUN_READ_PAUSE_PACKETS_TOTAL': client1['tun_read_pause_total_by_reason']['queued_packets_limit'],
    'CLIENT2_TUN_READ_PAUSE_TOTAL': client2['tun_read_pause_total'],
    'CLIENT2_TUN_READ_RESUME_TOTAL': client2['tun_read_resume_total'],
    'CLIENT2_TUN_READ_PAUSE_BYTES_TOTAL': client2['tun_read_pause_total_by_reason']['queued_bytes_threshold'],
    'CLIENT2_TUN_READ_PAUSE_PACKETS_TOTAL': client2['tun_read_pause_total_by_reason']['queued_packets_limit'],
    'RUN_TUN_READ_PAUSE_TOTAL': run_summary['tun_read_pause_total'],
    'RUN_TUN_READ_RESUME_TOTAL': run_summary['tun_read_resume_total'],
    'RUN_TUN_READ_PAUSE_BYTES_TOTAL': run_summary['tun_read_pause_total_by_reason']['queued_bytes_threshold'],
    'RUN_TUN_READ_PAUSE_PACKETS_TOTAL': run_summary['tun_read_pause_total_by_reason']['queued_packets_limit'],
    'RUN_REASSEMBLY_OVERFLOW_EVICT': run_summary['reassembly_overflow_evict'],
    'RUN_UNFINISHED_BLOCK_LIMIT': run_summary['unfinished_block_limit'],
}

for key, value in values.items():
    print(f'{key}={shlex.quote(str(value))}')
PY
)"; then
        set_fail_reason "解析 v6 namespace 统一结构化摘要失败"
        return 1
    fi
}

collect_metrics() {
    CLIENT1_READY_ACCEPTS="$(grep -c '^ready_accept node_id='"$CLIENT1_NODE_ID" "$SERVER_LOG" 2>/dev/null || true)"
    CLIENT2_READY_ACCEPTS="$(grep -c '^ready_accept node_id='"$CLIENT2_NODE_ID" "$SERVER_LOG" 2>/dev/null || true)"
    CLIENT1_GRANTS="$(grep -Ec '^grant seq=.* node_id='"$CLIENT1_NODE_ID"' ' "$SERVER_LOG" 2>/dev/null || true)"
    CLIENT2_GRANTS="$(grep -Ec '^grant seq=.* node_id='"$CLIENT2_NODE_ID"' ' "$SERVER_LOG" 2>/dev/null || true)"
    CLIENT1_AUTHORIZED_SENDS="$(grep 'TOKEN_AUTH' "$CLIENT1_LOG" 2>/dev/null | tail -1 | awk -F'[:	]' '{print $5}' || true)"
    CLIENT2_AUTHORIZED_SENDS="$(grep 'TOKEN_AUTH' "$CLIENT2_LOG" 2>/dev/null | tail -1 | awk -F'[:	]' '{print $5}' || true)"
    CLIENT1_AUTHORIZED_SENDS="${CLIENT1_AUTHORIZED_SENDS:-0}"
    CLIENT2_AUTHORIZED_SENDS="${CLIENT2_AUTHORIZED_SENDS:-0}"
    collect_queue_backpressure_metrics
}

assert_metrics() {
    if [ "$CLIENT1_READY_ACCEPTS" -le 0 ]; then
        set_fail_reason "server 未接受 client1 READY"
        return 1
    fi
    if [ "$CLIENT2_READY_ACCEPTS" -le 0 ]; then
        set_fail_reason "server 未接受 client2 READY"
        return 1
    fi
    if [ "$CLIENT1_GRANTS" -le 0 ]; then
        set_fail_reason "未观察到发给 client1 的 GRANT"
        return 1
    fi
    if [ "$CLIENT2_GRANTS" -le 0 ]; then
        set_fail_reason "未观察到发给 client2 的 GRANT"
        return 1
    fi
    if [ "$CLIENT1_AUTHORIZED_SENDS" -le 0 ]; then
        set_fail_reason "client1 未产生授权发送"
        return 1
    fi
    if [ "$CLIENT2_AUTHORIZED_SENDS" -le 0 ]; then
        set_fail_reason "client2 未产生授权发送"
        return 1
    fi
    if [ "$RUN_TUN_READ_PAUSE_TOTAL" -le 0 ]; then
        set_fail_reason "issue #22 未观察到 TUN 停读"
        return 1
    fi
    if [ "$RUN_TUN_READ_RESUME_TOTAL" -le 0 ]; then
        set_fail_reason "issue #22 未观察到 TUN 恢复读"
        return 1
    fi
    if [ "$CLIENT1_TUN_READ_PAUSE_BYTES_TOTAL" -le 0 ]; then
        set_fail_reason "client1 未触发 queued_bytes_threshold 停读"
        return 1
    fi
    if [ "$CLIENT2_TUN_READ_PAUSE_PACKETS_TOTAL" -le 0 ]; then
        set_fail_reason "client2 未触发 queued_packets_limit 停读"
        return 1
    fi
    if [ ! -f "$RUN_SUMMARY_JSON" ]; then
        set_fail_reason "缺少统一 2A 摘要"
        return 1
    fi
    if [ "$RUN_UNFINISHED_BLOCK_LIMIT" -le 0 ]; then
        set_fail_reason "issue #23 未产出 unfinished_block_limit 摘要"
        return 1
    fi
}

main() {
    prepare_log_dir
    require_root
    require_env
    build_binary

    log_info "创建 namespace 与管理链路"
    create_namespace "$SERVER_NS"
    create_namespace "$CLIENT1_NS"
    create_namespace "$CLIENT2_NS"
    attach_veth_pair "$SERVER_NS" "$SERVER_CLIENT1_IF" "$SERVER_CLIENT1_ADDR" "$CLIENT1_NS" "$CLIENT1_MGMT_IF" "$CLIENT1_MGMT_ADDR"
    attach_veth_pair "$SERVER_NS" "$SERVER_CLIENT2_IF" "$SERVER_CLIENT2_ADDR" "$CLIENT2_NS" "$CLIENT2_MGMT_IF" "$CLIENT2_MGMT_ADDR"

    start_server
    start_client "$CLIENT1_NS" "$CLIENT1_TUN_NAME" "$CLIENT1_TUN_ADDR" "$CLIENT1_NODE_ID" "$CLIENT1_AIR_PORT" "$SERVER_CLIENT1_IP" "$CLIENT1_LOG" CLIENT1_PID "$CLIENT1_UPLINK_PAUSE_THRESHOLD_BYTES" "$CLIENT1_UPLINK_RESUME_THRESHOLD_BYTES" "$CLIENT1_UPLINK_QUEUE_PACKETS_LIMIT" "$CLIENT1_QUEUE_SUMMARY_JSON"
    start_client "$CLIENT2_NS" "$CLIENT2_TUN_NAME" "$CLIENT2_TUN_ADDR" "$CLIENT2_NODE_ID" "$CLIENT2_AIR_PORT" "$SERVER_CLIENT2_IP" "$CLIENT2_LOG" CLIENT2_PID "$CLIENT2_UPLINK_PAUSE_THRESHOLD_BYTES" "$CLIENT2_UPLINK_RESUME_THRESHOLD_BYTES" "$CLIENT2_UPLINK_QUEUE_PACKETS_LIMIT" "$CLIENT2_QUEUE_SUMMARY_JSON"

    wait_for_interface "$SERVER_NS" "$SERVER_TUN_NAME"
    wait_for_interface "$CLIENT1_NS" "$CLIENT1_TUN_NAME"
    wait_for_interface "$CLIENT2_NS" "$CLIENT2_TUN_NAME"

    run_dual_ping
    collect_metrics
    assert_metrics

    log_pass "v6 trusted_plaintext 上行 namespace 验收通过"
}

main "$@"
