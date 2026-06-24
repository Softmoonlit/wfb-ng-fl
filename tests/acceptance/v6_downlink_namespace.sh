#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

LOG_DIR="${LOG_DIR:-$PROJECT_ROOT/tests/logs/v6_downlink_namespace_$(date +%Y%m%d_%H%M%S)}"
RESULT_MD="$LOG_DIR/result.md"
BUILD_LOG="$LOG_DIR/build.log"
RUN_SUMMARY_JSON="$LOG_DIR/formal_2a_summary.json"
FORMAL_CONCLUSION_MD="$LOG_DIR/formal_conclusion.md"

SERVER_NS="${SERVER_NS:-v6d-server}"
CLIENT1_NS="${CLIENT1_NS:-v6d-client1}"
CLIENT2_NS="${CLIENT2_NS:-v6d-client2}"

SERVER_DIR="$LOG_DIR/server"
CLIENT1_DIR="$LOG_DIR/client1"
CLIENT2_DIR="$LOG_DIR/client2"

SERVER_LOG="$SERVER_DIR/wfb_v6_uplink.log"
CLIENT1_LOG="$CLIENT1_DIR/wfb_v6_uplink.log"
CLIENT2_LOG="$CLIENT2_DIR/wfb_v6_uplink.log"
SERVER_QUEUE_SUMMARY_JSON="$SERVER_DIR/downlink_queue_summary.json"
CLIENT1_QUEUE_SUMMARY_JSON="$CLIENT1_DIR/uplink_queue_summary.json"
CLIENT2_QUEUE_SUMMARY_JSON="$CLIENT2_DIR/uplink_queue_summary.json"

SERVER_TUN_NAME="${SERVER_TUN_NAME:-v6ds0}"
CLIENT1_TUN_NAME="${CLIENT1_TUN_NAME:-v6dc1}"
CLIENT2_TUN_NAME="${CLIENT2_TUN_NAME:-v6dc2}"
SERVER_TUN_ADDR="${SERVER_TUN_ADDR:-10.67.0.1/24}"
CLIENT1_TUN_ADDR="${CLIENT1_TUN_ADDR:-10.67.0.11/24}"
CLIENT2_TUN_ADDR="${CLIENT2_TUN_ADDR:-10.67.0.12/24}"
SERVER_TUN_IP="${SERVER_TUN_IP:-10.67.0.1}"
CLIENT1_TUN_IP="${CLIENT1_TUN_IP:-10.67.0.11}"
CLIENT2_TUN_IP="${CLIENT2_TUN_IP:-10.67.0.12}"

SERVER_CLIENT1_IF="${SERVER_CLIENT1_IF:-v6d-s1}"
CLIENT1_MGMT_IF="${CLIENT1_MGMT_IF:-v6d-c1}"
SERVER_CLIENT2_IF="${SERVER_CLIENT2_IF:-v6d-s2}"
CLIENT2_MGMT_IF="${CLIENT2_MGMT_IF:-v6d-c2}"
SERVER_CLIENT1_ADDR="${SERVER_CLIENT1_ADDR:-172.62.1.1/30}"
CLIENT1_MGMT_ADDR="${CLIENT1_MGMT_ADDR:-172.62.1.2/30}"
SERVER_CLIENT2_ADDR="${SERVER_CLIENT2_ADDR:-172.62.2.1/30}"
CLIENT2_MGMT_ADDR="${CLIENT2_MGMT_ADDR:-172.62.2.2/30}"
SERVER_CLIENT1_IP="${SERVER_CLIENT1_IP:-172.62.1.1}"
CLIENT1_MGMT_IP="${CLIENT1_MGMT_IP:-172.62.1.2}"
SERVER_CLIENT2_IP="${SERVER_CLIENT2_IP:-172.62.2.1}"
CLIENT2_MGMT_IP="${CLIENT2_MGMT_IP:-172.62.2.2}"

SERVER_NODE_ID="${SERVER_NODE_ID:-9}"
CLIENT1_NODE_ID="${CLIENT1_NODE_ID:-1}"
CLIENT2_NODE_ID="${CLIENT2_NODE_ID:-2}"
LINK_ID="${LINK_ID:-406}"
STREAM_ID="${STREAM_ID:-33}"
SERVER_AIR_PORT="${SERVER_AIR_PORT:-43000}"
CLIENT1_AIR_PORT="${CLIENT1_AIR_PORT:-43011}"
CLIENT2_AIR_PORT="${CLIENT2_AIR_PORT:-43012}"
GRANT_DURATION_MS="${GRANT_DURATION_MS:-120}"
GUARD_INTERVAL_MS="${GUARD_INTERVAL_MS:-20}"
FEEDBACK_WINDOW_PERIOD_MS="${FEEDBACK_WINDOW_PERIOD_MS:-150}"
FEEDBACK_WINDOW_DURATION_MS="${FEEDBACK_WINDOW_DURATION_MS:-50}"
LOG_INTERVAL_MS="${LOG_INTERVAL_MS:-200}"
STARTUP_WAIT_SEC="${STARTUP_WAIT_SEC:-1}"
POST_TRANSFER_WAIT_SEC="${POST_TRANSFER_WAIT_SEC:-1}"

CLIENT_UPLINK_PAUSE_THRESHOLD_BYTES="${CLIENT_UPLINK_PAUSE_THRESHOLD_BYTES:-4096}"
CLIENT_UPLINK_RESUME_THRESHOLD_BYTES="${CLIENT_UPLINK_RESUME_THRESHOLD_BYTES:-2048}"
CLIENT_UPLINK_QUEUE_PACKETS_LIMIT="${CLIENT_UPLINK_QUEUE_PACKETS_LIMIT:-8}"
SERVER_DOWNLINK_PAUSE_THRESHOLD_BYTES="${SERVER_DOWNLINK_PAUSE_THRESHOLD_BYTES:-1200}"
SERVER_DOWNLINK_RESUME_THRESHOLD_BYTES="${SERVER_DOWNLINK_RESUME_THRESHOLD_BYTES:-600}"
SERVER_DOWNLINK_QUEUE_PACKETS_LIMIT="${SERVER_DOWNLINK_QUEUE_PACKETS_LIMIT:-1}"

UFTP_PUBLIC_MULTICAST_ADDR="${UFTP_PUBLIC_MULTICAST_ADDR:-230.4.4.1}"
UFTP_PRIVATE_MULTICAST_ADDR="${UFTP_PRIVATE_MULTICAST_ADDR:-230.5.5.8}"
UFTP_PORT="${UFTP_PORT:-1044}"
UFTP_SOURCE_PORT="${UFTP_SOURCE_PORT:-1045}"
UFTP_PAYLOAD_SIZE="${UFTP_PAYLOAD_SIZE:-131072}"
UFTP_TRANSFER_TIMEOUT_SEC="${UFTP_TRANSFER_TIMEOUT_SEC:-40}"
UFTP_SERVER_LOG="$SERVER_DIR/uftp.log"
UFTP_SERVER_STATUS="$SERVER_DIR/uftp.status"
CLIENT1_UFTPD_LOG="$CLIENT1_DIR/uftpd.log"
CLIENT2_UFTPD_LOG="$CLIENT2_DIR/uftpd.log"
CLIENT1_UFTPD_STATUS="$CLIENT1_DIR/uftpd.status"
CLIENT2_UFTPD_STATUS="$CLIENT2_DIR/uftpd.status"
CLIENT1_UFTPD_PIDFILE="$CLIENT1_DIR/uftpd.pid"
CLIENT2_UFTPD_PIDFILE="$CLIENT2_DIR/uftpd.pid"
UFTP_PAYLOAD_FILE="$SERVER_DIR/uftp_payload.bin"
CLIENT1_UFTP_DEST_DIR="$CLIENT1_DIR/uftp_dest"
CLIENT2_UFTP_DEST_DIR="$CLIENT2_DIR/uftp_dest"
CLIENT1_UFTP_TEMP_DIR="$CLIENT1_DIR/uftp_tmp"
CLIENT2_UFTP_TEMP_DIR="$CLIENT2_DIR/uftp_tmp"
CLIENT1_UFTP_PAYLOAD_FILE="$CLIENT1_UFTP_DEST_DIR/uftp_payload.bin"
CLIENT2_UFTP_PAYLOAD_FILE="$CLIENT2_UFTP_DEST_DIR/uftp_payload.bin"

FAIL_REASON=""
RESULT_STATUS="FAIL"
CLEANUP_VERIFIED="否"
SERVER_PID=""
CLIENT1_PID=""
CLIENT2_PID=""
CLIENT1_UFTPD_PID=""
CLIENT2_UFTPD_PID=""
UFTP_SOURCE_SHA256="未生成"
CLIENT1_UFTP_SHA256="未收到"
CLIENT2_UFTP_SHA256="未收到"
RUN_GRANT_SENT_TOTAL="0"
RUN_READY_ACCEPTED_TOTAL="0"
RUN_READY_REJECTED_INVALID_SOURCE="0"
RUN_READY_REJECTED_WRONG_INGRESS_OR_LINK_DOMAIN="0"
RUN_READY_REJECTED_UNKNOWN_CLIENT="0"
RUN_FEEDBACK_WINDOW_OPEN_COUNT="0"
RUN_FEEDBACK_WINDOW_CLOSE_COUNT="0"
RUN_FEEDBACK_HIT_TOTAL="0"
RUN_SERVER_TUN_READ_PAUSE_TOTAL="0"
RUN_SERVER_TUN_READ_RESUME_TOTAL="0"
RUN_SERVER_TUN_READ_PAUSE_BYTES_TOTAL="0"
RUN_SERVER_TUN_READ_PAUSE_PACKETS_TOTAL="0"
RUN_REASSEMBLY_OVERFLOW_EVICT="0"
RUN_UNFINISHED_BLOCK_LIMIT="0"

RESOLVED_UFTP_BIN=""
RESOLVED_UFTPD_BIN=""
PIDS=()

log_info() { echo "[INFO] $(date '+%H:%M:%S') $1"; }
log_pass() { echo "[PASS] $(date '+%H:%M:%S') $1"; }
log_fail() { echo "[FAIL] $(date '+%H:%M:%S') $1" >&2; }
set_fail_reason() { FAIL_REASON="$1"; log_fail "$1"; }

prepare_log_dir() {
    mkdir -p "$SERVER_DIR" "$CLIENT1_DIR" "$CLIENT2_DIR" "$CLIENT1_UFTP_DEST_DIR" "$CLIENT2_UFTP_DEST_DIR" "$CLIENT1_UFTP_TEMP_DIR" "$CLIENT2_UFTP_TEMP_DIR"
}

render_result() {
    cat > "$RESULT_MD" <<EOF
# v6 trusted_plaintext 下行 shared UFTP + feedback window namespace 验收结果

- 结果: $RESULT_STATUS
- 原因: ${FAIL_REASON:-无}
- cleanup_verified: $CLEANUP_VERIFIED
- summary_json: $RUN_SUMMARY_JSON
- formal_conclusion: $FORMAL_CONCLUSION_MD
- server log: $SERVER_LOG
- client1 log: $CLIENT1_LOG
- client2 log: $CLIENT2_LOG
- server queue summary: $SERVER_QUEUE_SUMMARY_JSON
- client1 queue summary: $CLIENT1_QUEUE_SUMMARY_JSON
- client2 queue summary: $CLIENT2_QUEUE_SUMMARY_JSON
- UFTP_BIN: ${RESOLVED_UFTP_BIN:-未解析}
- UFTPD_BIN: ${RESOLVED_UFTPD_BIN:-未解析}
- UFTP source sha256: $UFTP_SOURCE_SHA256
- client1 UFTP sha256: $CLIENT1_UFTP_SHA256
- client2 UFTP sha256: $CLIENT2_UFTP_SHA256
- grant_sent_total: $RUN_GRANT_SENT_TOTAL
- ready_accepted_total: $RUN_READY_ACCEPTED_TOTAL
- ready_rejected.invalid_source: $RUN_READY_REJECTED_INVALID_SOURCE
- ready_rejected.wrong_ingress_or_link_domain: $RUN_READY_REJECTED_WRONG_INGRESS_OR_LINK_DOMAIN
- ready_rejected.unknown_client: $RUN_READY_REJECTED_UNKNOWN_CLIENT
- feedback_window_open_count: $RUN_FEEDBACK_WINDOW_OPEN_COUNT
- feedback_window_close_count: $RUN_FEEDBACK_WINDOW_CLOSE_COUNT
- feedback_uplink_hit_total: $RUN_FEEDBACK_HIT_TOTAL
- server tun_read_pause_total: $RUN_SERVER_TUN_READ_PAUSE_TOTAL
- server tun_read_resume_total: $RUN_SERVER_TUN_READ_RESUME_TOTAL
- server queued_bytes_threshold pauses: $RUN_SERVER_TUN_READ_PAUSE_BYTES_TOTAL
- server queued_packets_limit pauses: $RUN_SERVER_TUN_READ_PAUSE_PACKETS_TOTAL
- run reassembly_overflow_evict: $RUN_REASSEMBLY_OVERFLOW_EVICT
- run unfinished_block_limit: $RUN_UNFINISHED_BLOCK_LIMIT
- 已覆盖: trusted_plaintext 新底座 shared UFTP 一发多收下行语义
- 已覆盖: server 下行固定容量队列与 TUN 停读/恢复 2A 摘要
- 已覆盖: feedback window 短 GRANT 轮询与反向上行命中证据
EOF
}

render_formal_conclusion() {
    local evidence_ok="否"
    local rerun_required="是"
    if [ "$RESULT_STATUS" = "PASS" ]; then
        evidence_ok="是"
        rerun_required="否"
    fi

    cat > "$FORMAL_CONCLUSION_MD" <<EOF
## v6 namespace 正式验收结论

- 运行批次：$(basename "$LOG_DIR")
- 对应 issue：#25
- 结论状态：$RESULT_STATUS

### 运行场景与前置条件
- run_kind: namespace
- link_security_mode: trusted_plaintext
- scenario_id: v6_namespace_downlink_shared_uftp_feedback
- feedback_window_covered: true
- 执行入口: tests/acceptance/v6_downlink_namespace.sh

### 原始产物层引用
- 日志目录: $LOG_DIR
- 统一 2A 摘要: $RUN_SUMMARY_JSON
- 结论文件: $RESULT_MD
- 2B 证据: $SERVER_LOG, $CLIENT1_LOG, $CLIENT2_LOG, $SERVER_QUEUE_SUMMARY_JSON, $CLIENT1_QUEUE_SUMMARY_JSON, $CLIENT2_QUEUE_SUMMARY_JSON, $UFTP_SERVER_LOG, $CLIENT1_UFTPD_LOG, $CLIENT2_UFTPD_LOG

### 运行时长与关键事件计数
- grant_sent_total: $RUN_GRANT_SENT_TOTAL
- ready_accepted_total: $RUN_READY_ACCEPTED_TOTAL
- ready_rejected.invalid_source: $RUN_READY_REJECTED_INVALID_SOURCE
- ready_rejected.wrong_ingress_or_link_domain: $RUN_READY_REJECTED_WRONG_INGRESS_OR_LINK_DOMAIN
- ready_rejected.unknown_client: $RUN_READY_REJECTED_UNKNOWN_CLIENT
- feedback_window_open_count: $RUN_FEEDBACK_WINDOW_OPEN_COUNT
- feedback_window_close_count: $RUN_FEEDBACK_WINDOW_CLOSE_COUNT
- feedback_uplink_hit_total: $RUN_FEEDBACK_HIT_TOTAL
- tun_read_pause_total: $RUN_SERVER_TUN_READ_PAUSE_TOTAL
- tun_read_resume_total: $RUN_SERVER_TUN_READ_RESUME_TOTAL
- queued_bytes_threshold pauses: $RUN_SERVER_TUN_READ_PAUSE_BYTES_TOTAL
- queued_packets_limit pauses: $RUN_SERVER_TUN_READ_PAUSE_PACKETS_TOTAL
- reassembly_overflow_evict: $RUN_REASSEMBLY_OVERFLOW_EVICT
- unfinished_block_limit: $RUN_UNFINISHED_BLOCK_LIMIT

### 关键异常与风险信号
- 结果原因: ${FAIL_REASON:-无}
- client1/client2 UFTP SHA256: $CLIENT1_UFTP_SHA256 / $CLIENT2_UFTP_SHA256
- 2B 细节仍保留在 server/client 原始日志、queue summary 与 UFTP 状态文件中，不混入统一 2A 摘要。

### 结论
- 可作为 issue #25 namespace 正式证据: $evidence_ok
- 是否需要重跑: $rerun_required
EOF
}

cleanup() {
    for pid in "$SERVER_PID" "$CLIENT1_PID" "$CLIENT2_PID"; do
        if [ -n "$pid" ]; then
            kill "$pid" 2>/dev/null || true
            wait "$pid" 2>/dev/null || true
        fi
    done
    if [ -f "$CLIENT1_UFTPD_PIDFILE" ]; then
        kill "$(cat "$CLIENT1_UFTPD_PIDFILE")" 2>/dev/null || true
    fi
    if [ -f "$CLIENT2_UFTPD_PIDFILE" ]; then
        kill "$(cat "$CLIENT2_UFTPD_PIDFILE")" 2>/dev/null || true
    fi
    ip netns delete "$SERVER_NS" 2>/dev/null || true
    ip netns delete "$CLIENT1_NS" 2>/dev/null || true
    ip netns delete "$CLIENT2_NS" 2>/dev/null || true

    local netns_list
    netns_list="$(ip netns list 2>/dev/null || true)"
    if [[ "$netns_list" != *"$SERVER_NS"* ]] && [[ "$netns_list" != *"$CLIENT1_NS"* ]] && [[ "$netns_list" != *"$CLIENT2_NS"* ]]; then
        CLEANUP_VERIFIED="是"
    fi
    render_result
    render_formal_conclusion
}
trap cleanup EXIT

require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        set_fail_reason "需要 root 权限"
        return 1
    fi
}

require_uftp_binary() {
    local env_name="$1"
    local binary_name="$2"
    local resolved=""
    if [ -n "${!env_name:-}" ]; then
        if [ ! -x "${!env_name}" ]; then
            set_fail_reason "缺少 UFTP 可执行文件: ${!env_name}"
            return 1
        fi
        resolved="${!env_name}"
    else
        resolved="$(command -v "$binary_name" || true)"
        if [ -z "$resolved" ]; then
            set_fail_reason "缺少 UFTP 可执行文件: $binary_name"
            return 1
        fi
    fi
    if [ "$env_name" = "UFTP_BIN" ]; then
        RESOLVED_UFTP_BIN="$resolved"
    else
        RESOLVED_UFTPD_BIN="$resolved"
    fi
}

require_uftp_binaries() {
    require_uftp_binary UFTP_BIN uftp || return 1
    require_uftp_binary UFTPD_BIN uftpd || return 1
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
    local tmp_left="${left_if}-tmp"
    local tmp_right="${right_if}-tmp"

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
    set_fail_reason "$ns 缺少接口 $ifname"
    return 1
}

wait_for_file() {
    local path="$1"
    local title="$2"
    local tries="$3"
    local i
    for ((i = 0; i < tries; i++)); do
        if [ -f "$path" ]; then
            log_pass "$title"
            return 0
        fi
        sleep 0.25
    done
    set_fail_reason "$title 未出现: $path"
    return 1
}

sha256_of_file() {
    local path="$1"
    local output
    output="$(sha256sum "$path")"
    printf '%s\n' "${output%% *}"
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
        --feedback-window-period-ms "$FEEDBACK_WINDOW_PERIOD_MS" \
        --feedback-window-duration-ms "$FEEDBACK_WINDOW_DURATION_MS" \
        --downlink-pause-threshold-bytes "$SERVER_DOWNLINK_PAUSE_THRESHOLD_BYTES" \
        --downlink-resume-threshold-bytes "$SERVER_DOWNLINK_RESUME_THRESHOLD_BYTES" \
        --downlink-queue-packets-limit "$SERVER_DOWNLINK_QUEUE_PACKETS_LIMIT" \
        --queue-summary-file "$SERVER_QUEUE_SUMMARY_JSON" \
        --log-interval "$LOG_INTERVAL_MS" >"$SERVER_LOG" 2>&1 &
    SERVER_PID=$!
    sleep "$STARTUP_WAIT_SEC"
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        set_fail_reason "server 启动失败，查看 $SERVER_LOG"
        return 1
    fi
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
    local queue_summary_file="$9"

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
        --uplink-pause-threshold-bytes "$CLIENT_UPLINK_PAUSE_THRESHOLD_BYTES" \
        --uplink-resume-threshold-bytes "$CLIENT_UPLINK_RESUME_THRESHOLD_BYTES" \
        --uplink-queue-packets-limit "$CLIENT_UPLINK_QUEUE_PACKETS_LIMIT" \
        --queue-summary-file "$queue_summary_file" \
        --log-interval "$LOG_INTERVAL_MS" >"$logfile" 2>&1 &
    printf -v "$pid_var" '%s' "$!"
    sleep "$STARTUP_WAIT_SEC"
    if ! kill -0 "${!pid_var}" 2>/dev/null; then
        set_fail_reason "$ns 启动失败，查看 $logfile"
        return 1
    fi
}

add_uftp_multicast_routes() {
    log_info "为 UFTP multicast 显式添加 TUN 路由"
    ip -n "$SERVER_NS" route replace "$UFTP_PUBLIC_MULTICAST_ADDR/32" dev "$SERVER_TUN_NAME"
    ip -n "$SERVER_NS" route replace "$UFTP_PRIVATE_MULTICAST_ADDR/32" dev "$SERVER_TUN_NAME"
    ip -n "$CLIENT1_NS" route replace "$UFTP_PUBLIC_MULTICAST_ADDR/32" dev "$CLIENT1_TUN_NAME"
    ip -n "$CLIENT1_NS" route replace "$UFTP_PRIVATE_MULTICAST_ADDR/32" dev "$CLIENT1_TUN_NAME"
    ip -n "$CLIENT2_NS" route replace "$UFTP_PUBLIC_MULTICAST_ADDR/32" dev "$CLIENT2_TUN_NAME"
    ip -n "$CLIENT2_NS" route replace "$UFTP_PRIVATE_MULTICAST_ADDR/32" dev "$CLIENT2_TUN_NAME"
}

generate_uftp_payload() {
    log_info "生成 UFTP payload: $UFTP_PAYLOAD_SIZE bytes"
    python3 -u - "$UFTP_PAYLOAD_FILE" "$UFTP_PAYLOAD_SIZE" <<'PY'
import sys
path = sys.argv[1]
size = int(sys.argv[2])
pattern = b"wfb-ng-v6-uftp-payload\n"
with open(path, "wb") as fh:
    remaining = size
    while remaining > 0:
        chunk = pattern[:remaining] if remaining < len(pattern) else pattern
        fh.write(chunk)
        remaining -= len(chunk)
PY
    UFTP_SOURCE_SHA256="$(sha256_of_file "$UFTP_PAYLOAD_FILE")"
}

start_uftpd_for_client() {
    local ns="$1"
    local bind_ip="$2"
    local dest_dir="$3"
    local temp_dir="$4"
    local logfile="$5"
    local status_file="$6"
    local pidfile="$7"

    rm -f "$status_file" "$pidfile"
    log_info "在 $ns 启动 UFTPD"
    ip netns exec "$ns" "$RESOLVED_UFTPD_BIN" \
        -I "$bind_ip" \
        -M "$UFTP_PUBLIC_MULTICAST_ADDR" \
        -p "$UFTP_PORT" \
        -D "$dest_dir" \
        -T "$temp_dir" \
        -L "$logfile" \
        -F "$status_file" \
        -P "$pidfile"
    if ! wait_for_file "$pidfile" "$ns UFTPD pidfile 创建完成" 20; then
        return 1
    fi
    if ! kill -0 "$(cat "$pidfile")" 2>/dev/null; then
        set_fail_reason "$ns 的 UFTPD 启动失败，查看 $logfile"
        return 1
    fi
}

run_uftp_server() {
    log_info "在 $SERVER_NS 发送 shared UFTP payload"
    if ! timeout "${UFTP_TRANSFER_TIMEOUT_SEC}s" ip netns exec "$SERVER_NS" "$RESOLVED_UFTP_BIN" \
        -I "$SERVER_TUN_IP" \
        -M "$UFTP_PUBLIC_MULTICAST_ADDR" \
        -P "$UFTP_PRIVATE_MULTICAST_ADDR" \
        -p "$UFTP_PORT" \
        -u "$UFTP_SOURCE_PORT" \
        -Y none \
        -R 50000 \
        -L "$UFTP_SERVER_LOG" \
        -S "$UFTP_SERVER_STATUS" \
        -D "uftp_payload.bin" \
        "$UFTP_PAYLOAD_FILE"; then
        set_fail_reason "UFTP server 发送失败，查看 $UFTP_SERVER_LOG"
        return 1
    fi
}

verify_uftp_payloads() {
    wait_for_file "$CLIENT1_UFTP_PAYLOAD_FILE" "client1 收到 UFTP payload" "$((UFTP_TRANSFER_TIMEOUT_SEC * 4))" || return 1
    wait_for_file "$CLIENT2_UFTP_PAYLOAD_FILE" "client2 收到 UFTP payload" "$((UFTP_TRANSFER_TIMEOUT_SEC * 4))" || return 1
    CLIENT1_UFTP_SHA256="$(sha256_of_file "$CLIENT1_UFTP_PAYLOAD_FILE")"
    CLIENT2_UFTP_SHA256="$(sha256_of_file "$CLIENT2_UFTP_PAYLOAD_FILE")"
    if [ "$CLIENT1_UFTP_SHA256" != "$UFTP_SOURCE_SHA256" ]; then
        set_fail_reason "client1 UFTP payload sha256 不匹配"
        return 1
    fi
    if [ "$CLIENT2_UFTP_SHA256" != "$UFTP_SOURCE_SHA256" ]; then
        set_fail_reason "client2 UFTP payload sha256 不匹配"
        return 1
    fi
    log_pass "client1/client2 UFTP payload sha256 均匹配"
}

collect_metrics() {
    if ! eval "$(PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 - "$SERVER_QUEUE_SUMMARY_JSON" "$SERVER_LOG" "$CLIENT1_LOG" "$CLIENT2_LOG" "$RUN_SUMMARY_JSON" <<'PY'
import re
import shlex
import sys

from wfb_ng.tests.v6_formal_summary import (
    SCENARIO_V6_NAMESPACE_DOWNLINK,
    build_summary,
    write_summary,
)

server_queue_path, server_log_path, client1_log_path, client2_log_path, run_summary_path = sys.argv[1:6]


def load_json(path):
    import json

    with open(path, 'r') as fh:
        return json.load(fh)


def parse_last_ready_filter(path):
    received = accepted = invalid_source = wrong_ingress = unknown_client = None
    with open(path, 'r') as fh:
        for line in fh:
            match = re.search(r'\tREADY_FILTER\t(\d+):(\d+):(\d+):(\d+):(\d+)', line)
            if match:
                received = int(match.group(1))
                accepted = int(match.group(2))
                invalid_source = int(match.group(3))
                wrong_ingress = int(match.group(4))
                unknown_client = int(match.group(5))
    if received is None:
        raise SystemExit('missing READY_FILTER stats in %s' % path)
    return {
        'received': received,
        'accepted': accepted,
        'invalid_source': invalid_source,
        'wrong_ingress_or_link_domain': wrong_ingress,
        'unknown_client': unknown_client,
    }


def parse_last_reassembly(path):
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
    return {'reassembly_overflow_evict': total, 'unfinished_block_limit': limit}


server_queue = load_json(server_queue_path)
ready_filter = parse_last_ready_filter(server_log_path)
rx_reassembly = {
    'server': parse_last_reassembly(server_log_path),
    'client1': parse_last_reassembly(client1_log_path),
    'client2': parse_last_reassembly(client2_log_path),
}
unfinished_limits = {entry['unfinished_block_limit'] for entry in rx_reassembly.values()}
if len(unfinished_limits) != 1:
    raise SystemExit('inconsistent unfinished_block_limit: %r' % sorted(unfinished_limits))

grant_sent_total = 0
feedback_window_open_count = 0
feedback_window_close_count = 0
feedback_hits = {}
with open(server_log_path, 'r') as fh:
    for line in fh:
        if line.startswith('grant seq='):
            grant_sent_total += 1
        if line.startswith('feedback_window_open '):
            feedback_window_open_count += 1
        if line.startswith('feedback_window_close '):
            feedback_window_close_count += 1
        match = re.match(r'feedback_uplink_hit node_id=(\d+) sequence=(\d+) total=(\d+)', line)
        if match:
            feedback_hits[match.group(1)] = int(match.group(3))

summary = build_summary(
    SCENARIO_V6_NAMESPACE_DOWNLINK,
    grant_sent_total=grant_sent_total,
    ready_accepted_total=ready_filter['accepted'],
    ready_rejected_total_by_reason={
        'invalid_source': ready_filter['invalid_source'],
        'wrong_ingress_or_link_domain': ready_filter['wrong_ingress_or_link_domain'],
        'unknown_client': ready_filter['unknown_client'],
    },
    feedback_window_open_count=feedback_window_open_count,
    feedback_window_close_count=feedback_window_close_count,
    feedback_uplink_hit_total_by_node=feedback_hits,
    feedback_uplink_hit_total=sum(feedback_hits.values()),
    tun_read_pause_total=server_queue['tun_read_pause_total'],
    tun_read_resume_total=server_queue['tun_read_resume_total'],
    tun_read_pause_total_by_reason=server_queue['tun_read_pause_total_by_reason'],
    reassembly_overflow_evict=sum(entry['reassembly_overflow_evict'] for entry in rx_reassembly.values()),
    unfinished_block_limit=unfinished_limits.pop(),
)
write_summary(run_summary_path, summary)

values = {
    'RUN_GRANT_SENT_TOTAL': summary['grant_sent_total'],
    'RUN_READY_ACCEPTED_TOTAL': summary['ready_accepted_total'],
    'RUN_READY_REJECTED_INVALID_SOURCE': summary['ready_rejected_total_by_reason']['invalid_source'],
    'RUN_READY_REJECTED_WRONG_INGRESS_OR_LINK_DOMAIN': summary['ready_rejected_total_by_reason']['wrong_ingress_or_link_domain'],
    'RUN_READY_REJECTED_UNKNOWN_CLIENT': summary['ready_rejected_total_by_reason']['unknown_client'],
    'RUN_FEEDBACK_WINDOW_OPEN_COUNT': summary['feedback_window_open_count'],
    'RUN_FEEDBACK_WINDOW_CLOSE_COUNT': summary['feedback_window_close_count'],
    'RUN_FEEDBACK_HIT_TOTAL': summary['feedback_uplink_hit_total'],
    'RUN_SERVER_TUN_READ_PAUSE_TOTAL': summary['tun_read_pause_total'],
    'RUN_SERVER_TUN_READ_RESUME_TOTAL': summary['tun_read_resume_total'],
    'RUN_SERVER_TUN_READ_PAUSE_BYTES_TOTAL': summary['tun_read_pause_total_by_reason']['queued_bytes_threshold'],
    'RUN_SERVER_TUN_READ_PAUSE_PACKETS_TOTAL': summary['tun_read_pause_total_by_reason']['queued_packets_limit'],
    'RUN_REASSEMBLY_OVERFLOW_EVICT': summary['reassembly_overflow_evict'],
    'RUN_UNFINISHED_BLOCK_LIMIT': summary['unfinished_block_limit'],
}
for key, value in values.items():
    print('%s=%s' % (key, shlex.quote(str(value))))
PY
)"; then
        set_fail_reason "解析统一 2A 摘要失败"
        return 1
    fi
}

assert_metrics() {
    if [ ! -f "$RUN_SUMMARY_JSON" ]; then
        set_fail_reason "缺少统一 2A 摘要"
        return 1
    fi
    if [ "$RUN_GRANT_SENT_TOTAL" -le 0 ]; then
        set_fail_reason "grant_sent_total 未产生"
        return 1
    fi
    if [ "$RUN_READY_ACCEPTED_TOTAL" -le 0 ]; then
        set_fail_reason "ready_accepted_total 未产生"
        return 1
    fi
    if [ "$RUN_FEEDBACK_WINDOW_OPEN_COUNT" -le 0 ] || [ "$RUN_FEEDBACK_WINDOW_CLOSE_COUNT" -le 0 ]; then
        set_fail_reason "feedback window 开闭计数不足"
        return 1
    fi
    if [ "$RUN_FEEDBACK_HIT_TOTAL" -le 0 ]; then
        set_fail_reason "未观察到短 GRANT 时隙内的反向上行命中"
        return 1
    fi
    if [ "$RUN_SERVER_TUN_READ_PAUSE_TOTAL" -le 0 ] || [ "$RUN_SERVER_TUN_READ_RESUME_TOTAL" -le 0 ]; then
        set_fail_reason "server 下行未观察到 TUN 停读/恢复"
        return 1
    fi
    if [ "$RUN_SERVER_TUN_READ_PAUSE_BYTES_TOTAL" -le 0 ] && [ "$RUN_SERVER_TUN_READ_PAUSE_PACKETS_TOTAL" -le 0 ]; then
        set_fail_reason "server 下行未观察到按原因停读"
        return 1
    fi
    if [ "$RUN_UNFINISHED_BLOCK_LIMIT" -le 0 ]; then
        set_fail_reason "unfinished_block_limit 未产出"
        return 1
    fi
}

main() {
    prepare_log_dir
    require_root
    require_uftp_binaries
    build_binary

    log_info "创建 namespace 与管理链路"
    create_namespace "$SERVER_NS"
    create_namespace "$CLIENT1_NS"
    create_namespace "$CLIENT2_NS"
    attach_veth_pair "$SERVER_NS" "$SERVER_CLIENT1_IF" "$SERVER_CLIENT1_ADDR" "$CLIENT1_NS" "$CLIENT1_MGMT_IF" "$CLIENT1_MGMT_ADDR"
    attach_veth_pair "$SERVER_NS" "$SERVER_CLIENT2_IF" "$SERVER_CLIENT2_ADDR" "$CLIENT2_NS" "$CLIENT2_MGMT_IF" "$CLIENT2_MGMT_ADDR"

    start_server
    start_client "$CLIENT1_NS" "$CLIENT1_TUN_NAME" "$CLIENT1_TUN_ADDR" "$CLIENT1_NODE_ID" "$CLIENT1_AIR_PORT" "$SERVER_CLIENT1_IP" "$CLIENT1_LOG" CLIENT1_PID "$CLIENT1_QUEUE_SUMMARY_JSON"
    start_client "$CLIENT2_NS" "$CLIENT2_TUN_NAME" "$CLIENT2_TUN_ADDR" "$CLIENT2_NODE_ID" "$CLIENT2_AIR_PORT" "$SERVER_CLIENT2_IP" "$CLIENT2_LOG" CLIENT2_PID "$CLIENT2_QUEUE_SUMMARY_JSON"

    wait_for_interface "$SERVER_NS" "$SERVER_TUN_NAME"
    wait_for_interface "$CLIENT1_NS" "$CLIENT1_TUN_NAME"
    wait_for_interface "$CLIENT2_NS" "$CLIENT2_TUN_NAME"

    add_uftp_multicast_routes
    generate_uftp_payload
    start_uftpd_for_client "$CLIENT1_NS" "$CLIENT1_TUN_IP" "$CLIENT1_UFTP_DEST_DIR" "$CLIENT1_UFTP_TEMP_DIR" "$CLIENT1_UFTPD_LOG" "$CLIENT1_UFTPD_STATUS" "$CLIENT1_UFTPD_PIDFILE"
    start_uftpd_for_client "$CLIENT2_NS" "$CLIENT2_TUN_IP" "$CLIENT2_UFTP_DEST_DIR" "$CLIENT2_UFTP_TEMP_DIR" "$CLIENT2_UFTPD_LOG" "$CLIENT2_UFTPD_STATUS" "$CLIENT2_UFTPD_PIDFILE"
    run_uftp_server
    verify_uftp_payloads
    sleep "$POST_TRANSFER_WAIT_SEC"
    collect_metrics
    assert_metrics

    RESULT_STATUS="PASS"
    log_pass "v6 trusted_plaintext shared UFTP + feedback window namespace 验收通过"
}

main "$@"
