#!/bin/bash
# tests/real_hardware/test_v6_uplink_real_hardware.sh
# v6 新底座 same-host 三网卡 real-hardware 上行正式跑数入口。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

LOG_DIR="${LOG_DIR:-$PROJECT_ROOT/tests/logs/v6_realhw_uplink_$(date +%Y%m%d_%H%M%S)}"
RESULT_MD="$LOG_DIR/result.md"
CONTEXT_FILE="$LOG_DIR/context.txt"
FORMAL_CONCLUSION_MD="$LOG_DIR/formal_conclusion.md"
RUN_SUMMARY_JSON="${RUN_SUMMARY_JSON:-$LOG_DIR/formal_2a_summary.json}"
BUILD_LOG="$LOG_DIR/build.log"
SAMPLES_TSV="$LOG_DIR/dual_long_run_samples.tsv"

SERVER_DIR="$LOG_DIR/server"
CLIENT1_DIR="$LOG_DIR/client1"
CLIENT2_DIR="$LOG_DIR/client2"
SERVER_LOG="$SERVER_DIR/wfb_v6_uplink.log"
CLIENT1_LOG="$CLIENT1_DIR/wfb_v6_uplink.log"
CLIENT2_LOG="$CLIENT2_DIR/wfb_v6_uplink.log"
SERVER_QUEUE_SUMMARY_JSON="$SERVER_DIR/server_queue_summary.json"
CLIENT1_QUEUE_SUMMARY_JSON="$CLIENT1_DIR/client1_queue_summary.json"
CLIENT2_QUEUE_SUMMARY_JSON="$CLIENT2_DIR/client2_queue_summary.json"
CLIENT1_FEEDER_LOG="$CLIENT1_DIR/feeder_ping.log"
CLIENT2_FEEDER_LOG="$CLIENT2_DIR/feeder_ping.log"

WFB_V6_UPLINK_BIN="${WFB_V6_UPLINK_BIN:-$PROJECT_ROOT/wfb_v6_uplink}"
SCENARIO="dual-long-run"
USE_NETNS="${USE_NETNS:-0}"

SERVER_NS="${SERVER_NS:-v6rh-server}"
CLIENT1_NS="${CLIENT1_NS:-v6rh-client1}"
CLIENT2_NS="${CLIENT2_NS:-v6rh-client2}"
SERVER_IFACE="${SERVER_IFACE:-${WIFI_IFACE:-wlxbcec23372588}}"
CLIENT1_IFACE="${CLIENT1_IFACE:-${WIFI_IFACE_CLIENT:-wlxfc221c500a88}}"
CLIENT2_IFACE="${CLIENT2_IFACE:-${WIFI_IFACE_CLIENT2:-wlxfc221c300cbc}}"
CHANNEL="${CHANNEL:-157}"
CHANNEL_WIDTH="${CHANNEL_WIDTH:-HT40+}"

SERVER_TUN_NAME="${SERVER_TUN_NAME:-v6rhs0}"
CLIENT1_TUN_NAME="${CLIENT1_TUN_NAME:-v6rhc1}"
CLIENT2_TUN_NAME="${CLIENT2_TUN_NAME:-v6rhc2}"
SERVER_TUN_ADDR="${SERVER_TUN_ADDR:-10.68.0.1/24}"
CLIENT1_TUN_ADDR="${CLIENT1_TUN_ADDR:-10.68.0.11/24}"
CLIENT2_TUN_ADDR="${CLIENT2_TUN_ADDR:-10.68.0.12/24}"
SERVER_TUN_IP="${SERVER_TUN_IP:-10.68.0.1}"
CLIENT1_TUN_IP="${CLIENT1_TUN_IP:-10.68.0.11}"
CLIENT2_TUN_IP="${CLIENT2_TUN_IP:-10.68.0.12}"

SERVER_NODE_ID="${SERVER_NODE_ID:-9}"
CLIENT1_NODE_ID="${CLIENT1_NODE_ID:-1}"
CLIENT2_NODE_ID="${CLIENT2_NODE_ID:-2}"
LINK_ID="${LINK_ID:-406}"
STREAM_ID="${STREAM_ID:-32}"
GRANT_DURATION_MS="${GRANT_DURATION_MS:-120}"
GUARD_INTERVAL_MS="${GUARD_INTERVAL_MS:-20}"
LOG_INTERVAL_MS="${LOG_INTERVAL_MS:-200}"
STARTUP_WAIT_SEC="${STARTUP_WAIT_SEC:-2}"

CLIENT1_UPLINK_PAUSE_THRESHOLD_BYTES="${CLIENT1_UPLINK_PAUSE_THRESHOLD_BYTES:-64}"
CLIENT1_UPLINK_RESUME_THRESHOLD_BYTES="${CLIENT1_UPLINK_RESUME_THRESHOLD_BYTES:-32}"
CLIENT1_UPLINK_QUEUE_PACKETS_LIMIT="${CLIENT1_UPLINK_QUEUE_PACKETS_LIMIT:-8}"
CLIENT2_UPLINK_PAUSE_THRESHOLD_BYTES="${CLIENT2_UPLINK_PAUSE_THRESHOLD_BYTES:-4096}"
CLIENT2_UPLINK_RESUME_THRESHOLD_BYTES="${CLIENT2_UPLINK_RESUME_THRESHOLD_BYTES:-2048}"
CLIENT2_UPLINK_QUEUE_PACKETS_LIMIT="${CLIENT2_UPLINK_QUEUE_PACKETS_LIMIT:-1}"
SERVER_DOWNLINK_PAUSE_THRESHOLD_BYTES="${SERVER_DOWNLINK_PAUSE_THRESHOLD_BYTES:-131072}"
SERVER_DOWNLINK_RESUME_THRESHOLD_BYTES="${SERVER_DOWNLINK_RESUME_THRESHOLD_BYTES:-65536}"
SERVER_DOWNLINK_QUEUE_PACKETS_LIMIT="${SERVER_DOWNLINK_QUEUE_PACKETS_LIMIT:-64}"

V6_UPLINK_LONGRUN_DURATION_SEC="${V6_UPLINK_LONGRUN_DURATION_SEC:-180}"
V6_UPLINK_PROBE_INTERVAL_SEC="${V6_UPLINK_PROBE_INTERVAL_SEC:-1}"
V6_UPLINK_SAMPLE_INTERVAL_SEC="${V6_UPLINK_SAMPLE_INTERVAL_SEC:-15}"
V6_UPLINK_MIN_GRANTS="${V6_UPLINK_MIN_GRANTS:-60}"
V6_UPLINK_MIN_AUTHORIZED_SENDS="${V6_UPLINK_MIN_AUTHORIZED_SENDS:-10}"
V6_UPLINK_MAX_IDLE_SEC="${V6_UPLINK_MAX_IDLE_SEC:-45}"
FEEDER_INTERVAL_SEC="${FEEDER_INTERVAL_SEC:-0.02}"
FEEDER_PAYLOAD_BYTES="${FEEDER_PAYLOAD_BYTES:-256}"

SERVER_PID=""
CLIENT1_PID=""
CLIENT2_PID=""
CLIENT1_FEEDER_PID=""
CLIENT2_FEEDER_PID=""
RESULT_STATUS="FAIL"
RESULT_REASON="未执行"
CLEANUP_VERIFIED="否"

LONGRUN_ACTUAL_DURATION_SEC="0"
LONGRUN_TOTAL_GRANTS="0"
LONGRUN_CLIENT1_GRANTS="0"
LONGRUN_CLIENT2_GRANTS="0"
LONGRUN_CLIENT1_AUTHORIZED="0"
LONGRUN_CLIENT2_AUTHORIZED="0"
LONGRUN_SERVER_DATA_PACKETS="0"
LONGRUN_MAX_IDLE_OBSERVED_SEC="0"
LONGRUN_SAMPLE_COUNT="0"
RUN_TUN_READ_PAUSE_TOTAL="0"
RUN_TUN_READ_RESUME_TOTAL="0"
RUN_TUN_READ_PAUSE_BYTES_TOTAL="0"
RUN_TUN_READ_PAUSE_PACKETS_TOTAL="0"
RUN_REASSEMBLY_OVERFLOW_EVICT="0"
RUN_UNFINISHED_BLOCK_LIMIT="0"

log_info() { echo "[INFO] $(date '+%H:%M:%S') $1"; }
log_pass() { echo "[PASS] $(date '+%H:%M:%S') $1"; }
log_fail() { echo "[FAIL] $(date '+%H:%M:%S') $1" >&2; }

usage() {
    cat <<'EOF'
用法:
  sudo bash tests/real_hardware/test_v6_uplink_real_hardware.sh

关键环境变量:
  SERVER_IFACE / CLIENT1_IFACE / CLIENT2_IFACE  三张真实 Wi-Fi 网卡
  CHANNEL / CHANNEL_WIDTH                       默认 157 / HT40+
  LOG_DIR                                       日志目录
  V6_UPLINK_LONGRUN_DURATION_SEC                默认 180
  V6_UPLINK_MIN_GRANTS                          默认 60

本脚本只启动 wfb_v6_uplink --role server/client，不使用 wfb_rx/wfb_tx/wfb_token_scheduler，也不接受旧 -K key。
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo "未知参数: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

kill_if_running() {
    local pid="$1"
    if [ -n "$pid" ]; then
        kill "$pid" 2>/dev/null || true
    fi
}

cleanup() {
    kill_if_running "$CLIENT1_FEEDER_PID"
    kill_if_running "$CLIENT2_FEEDER_PID"
    kill_if_running "$SERVER_PID"
    kill_if_running "$CLIENT1_PID"
    kill_if_running "$CLIENT2_PID"
    sleep 0.2
    kill -9 "$CLIENT1_FEEDER_PID" "$CLIENT2_FEEDER_PID" "$SERVER_PID" "$CLIENT1_PID" "$CLIENT2_PID" 2>/dev/null || true
    ip netns delete "$SERVER_NS" 2>/dev/null || true
    ip netns delete "$CLIENT1_NS" 2>/dev/null || true
    ip netns delete "$CLIENT2_NS" 2>/dev/null || true
    if ! ip netns list 2>/dev/null | grep -Eq "(^| )($SERVER_NS|$CLIENT1_NS|$CLIENT2_NS)( |$)"; then
        CLEANUP_VERIFIED="是"
    fi
}
trap cleanup EXIT

prepare_log_dir() {
    mkdir -p "$SERVER_DIR" "$CLIENT1_DIR" "$CLIENT2_DIR"
    cat > "$CONTEXT_FILE" <<EOF
log_dir=$LOG_DIR
scenario=$SCENARIO
run_kind=real_hardware
link_security_mode=trusted_plaintext
v6_formal_evidence_allowed=true
formal_2a_summary=$RUN_SUMMARY_JSON
entrypoint=tests/real_hardware/test_v6_uplink_real_hardware.sh
wfb_v6_uplink_bin=$WFB_V6_UPLINK_BIN
server_ns=$SERVER_NS
client1_ns=$CLIENT1_NS
client2_ns=$CLIENT2_NS
server_iface=$SERVER_IFACE
client1_iface=$CLIENT1_IFACE
client2_iface=$CLIENT2_IFACE
channel=$CHANNEL
channel_width=$CHANNEL_WIDTH
server_tun=$SERVER_TUN_NAME $SERVER_TUN_ADDR
client1_tun=$CLIENT1_TUN_NAME $CLIENT1_TUN_ADDR
client2_tun=$CLIENT2_TUN_NAME $CLIENT2_TUN_ADDR
link_id=$LINK_ID
stream_id=$STREAM_ID
grant_duration_ms=$GRANT_DURATION_MS
guard_interval_ms=$GUARD_INTERVAL_MS
v6_uplink_longrun_duration_sec=$V6_UPLINK_LONGRUN_DURATION_SEC
v6_uplink_sample_interval_sec=$V6_UPLINK_SAMPLE_INTERVAL_SEC
v6_uplink_min_grants=$V6_UPLINK_MIN_GRANTS
v6_uplink_min_authorized_sends=$V6_UPLINK_MIN_AUTHORIZED_SENDS
v6_uplink_max_idle_sec=$V6_UPLINK_MAX_IDLE_SEC
client1_queue_summary=$CLIENT1_QUEUE_SUMMARY_JSON
client2_queue_summary=$CLIENT2_QUEUE_SUMMARY_JSON
server_queue_summary=$SERVER_QUEUE_SUMMARY_JSON
EOF
    printf 'elapsed_sec	grants_total	grants_client1	grants_client2	client1_authorized	client2_authorized	server_data_packets	tun_read_pause_total	tun_read_resume_total	progress
' > "$SAMPLES_TSV"
}

require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        log_fail "需要 root，用 sudo 执行"
        exit 2
    fi
}

require_binary() {
    if [ "$(basename "$WFB_V6_UPLINK_BIN")" != "wfb_v6_uplink" ]; then
        log_fail "WFB_V6_UPLINK_BIN basename 必须是 wfb_v6_uplink"
        exit 2
    fi
    if [ ! -x "$WFB_V6_UPLINK_BIN" ]; then
        log_info "构建 wfb_v6_uplink"
        if ! make -C "$PROJECT_ROOT" wfb_v6_uplink >"$BUILD_LOG" 2>&1; then
            log_fail "构建失败: $BUILD_LOG"
            exit 2
        fi
    fi
}

require_no_legacy_commands() {
    if env | grep -E 'TOKEN_.*START_CMD|TOKEN_SCHEDULER_CMD' >/dev/null; then
        log_fail "检测到旧 TOKEN_* split-process 命令环境变量；拒绝作为 v6 正式证据运行"
        exit 2
    fi
}

run_maybe_ns() {
    local ns="$1"
    shift
    if [ "$USE_NETNS" = "1" ]; then
        ip netns exec "$ns" "$@"
    else
        "$@"
    fi
}

configure_root_iface() {
    local iface="$1"
    if ! ip link show "$iface" >/dev/null 2>&1; then
        log_fail "找不到网卡 $iface"
        exit 2
    fi
    ip link set "$iface" down || true
    iw dev "$iface" set type monitor
    ip link set "$iface" up
    iw dev "$iface" set channel "$CHANNEL" "$CHANNEL_WIDTH"
}

prepare_network_topology() {
    if [ "$USE_NETNS" = "1" ]; then
        create_namespace "$SERVER_NS"
        create_namespace "$CLIENT1_NS"
        create_namespace "$CLIENT2_NS"
        move_and_configure_iface "$SERVER_NS" "$SERVER_IFACE"
        move_and_configure_iface "$CLIENT1_NS" "$CLIENT1_IFACE"
        move_and_configure_iface "$CLIENT2_NS" "$CLIENT2_IFACE"
        return
    fi
    configure_root_iface "$SERVER_IFACE"
    configure_root_iface "$CLIENT1_IFACE"
    configure_root_iface "$CLIENT2_IFACE"
}

create_namespace() {
    local ns="$1"
    ip netns delete "$ns" 2>/dev/null || true
    ip netns add "$ns"
    ip -n "$ns" link set lo up
}

iface_wiphy() {
    local iface="$1"
    iw dev "$iface" info | awk '/wiphy/ {print "phy" $2; exit}'
}

move_and_configure_iface() {
    local ns="$1"
    local iface="$2"
    if ! ip link show "$iface" >/dev/null 2>&1; then
        log_fail "找不到网卡 $iface；若它残留在旧 namespace，请先删除旧 namespace 或重插网卡"
        exit 2
    fi
    local phy
    phy="$(iface_wiphy "$iface")"
    if [ -z "$phy" ]; then
        log_fail "无法解析 $iface 对应 phy"
        exit 2
    fi
    ip link set "$iface" down || true
    iw dev "$iface" set type monitor
    iw phy "$phy" set netns name "$ns"
    ip netns exec "$ns" ip link set "$iface" up
    ip netns exec "$ns" iw dev "$iface" set channel "$CHANNEL" "$CHANNEL_WIDTH"
}

start_server() {
    log_info "启动 server v6 新底座"
    run_maybe_ns "$SERVER_NS" "$WFB_V6_UPLINK_BIN" \
        --role server \
        --tun-name "$SERVER_TUN_NAME" \
        --tun-addr "$SERVER_TUN_ADDR" \
        --node-id "$SERVER_NODE_ID" \
        --link-id "$LINK_ID" \
        --stream "$STREAM_ID" \
        --air-interface "$SERVER_IFACE" \
        --known-clients "$CLIENT1_NODE_ID,$CLIENT2_NODE_ID" \
        --client-target "$CLIENT1_NODE_ID:$CLIENT1_TUN_IP:127.0.0.1:1" \
        --client-target "$CLIENT2_NODE_ID:$CLIENT2_TUN_IP:127.0.0.1:1" \
        --grant-duration-ms "$GRANT_DURATION_MS" \
        --guard-interval-ms "$GUARD_INTERVAL_MS" \
        --downlink-pause-threshold-bytes "$SERVER_DOWNLINK_PAUSE_THRESHOLD_BYTES" \
        --downlink-resume-threshold-bytes "$SERVER_DOWNLINK_RESUME_THRESHOLD_BYTES" \
        --downlink-queue-packets-limit "$SERVER_DOWNLINK_QUEUE_PACKETS_LIMIT" \
        --queue-summary-file "$SERVER_QUEUE_SUMMARY_JSON" \
        --log-interval "$LOG_INTERVAL_MS" >"$SERVER_LOG" 2>&1 &
    SERVER_PID=$!
}

start_client() {
    local ns="$1"
    local tun_name="$2"
    local tun_addr="$3"
    local node_id="$4"
    local iface="$5"
    local queue_summary="$6"
    local pause_bytes="$7"
    local resume_bytes="$8"
    local packet_limit="$9"
    local logfile="${10}"
    local pid_var="${11}"

    log_info "启动 client node_id=$node_id v6 新底座"
    run_maybe_ns "$ns" "$WFB_V6_UPLINK_BIN" \
        --role client \
        --tun-name "$tun_name" \
        --tun-addr "$tun_addr" \
        --node-id "$node_id" \
        --link-id "$LINK_ID" \
        --stream "$STREAM_ID" \
        --air-interface "$iface" \
        --uplink-pause-threshold-bytes "$pause_bytes" \
        --uplink-resume-threshold-bytes "$resume_bytes" \
        --uplink-queue-packets-limit "$packet_limit" \
        --queue-summary-file "$queue_summary" \
        --log-interval "$LOG_INTERVAL_MS" >"$logfile" 2>&1 &
    printf -v "$pid_var" '%s' "$!"
}

wait_for_startup() {
    sleep "$STARTUP_WAIT_SEC"
    for item in "server:$SERVER_PID:$SERVER_LOG" "client1:$CLIENT1_PID:$CLIENT1_LOG" "client2:$CLIENT2_PID:$CLIENT2_LOG"; do
        local name="${item%%:*}"
        local rest="${item#*:}"
        local pid="${rest%%:*}"
        local logfile="${rest#*:}"
        if ! kill -0 "$pid" 2>/dev/null; then
            log_fail "$name 提前退出: $logfile"
            exit 1
        fi
        if ! grep -q 'trusted_plaintext risk=受信任环境/无链路机密性' "$logfile"; then
            log_fail "$name 启动日志缺少 trusted_plaintext 风险标记: $logfile"
            exit 1
        fi
    done
}

wait_for_tun() {
    local ns="$1"
    local tun_name="$2"
    if [ "$USE_NETNS" = "1" ]; then
        for _ in $(seq 1 50); do
            if ip -n "$ns" link show "$tun_name" >/dev/null 2>&1; then
                return 0
            fi
            sleep 0.1
        done
    else
        for _ in $(seq 1 50); do
            if ip link show "$tun_name" >/dev/null 2>&1; then
                return 0
            fi
            sleep 0.1
        done
    fi
    log_fail "$ns 缺少 TUN $tun_name"
    exit 1
}

start_feeders() {
    log_info "启动双客户端持续 feeder"
    if [ "$USE_NETNS" = "1" ]; then
        ip netns exec "$CLIENT1_NS" ping -n -i "$FEEDER_INTERVAL_SEC" -s "$FEEDER_PAYLOAD_BYTES" "$SERVER_TUN_IP" >"$CLIENT1_FEEDER_LOG" 2>&1 &
        CLIENT1_FEEDER_PID=$!
        ip netns exec "$CLIENT2_NS" ping -n -i "$FEEDER_INTERVAL_SEC" -s "$FEEDER_PAYLOAD_BYTES" "$SERVER_TUN_IP" >"$CLIENT2_FEEDER_LOG" 2>&1 &
        CLIENT2_FEEDER_PID=$!
    else
        ping -n -I "$CLIENT1_TUN_NAME" -i "$FEEDER_INTERVAL_SEC" -s "$FEEDER_PAYLOAD_BYTES" "$SERVER_TUN_IP" >"$CLIENT1_FEEDER_LOG" 2>&1 &
        CLIENT1_FEEDER_PID=$!
        ping -n -I "$CLIENT2_TUN_NAME" -i "$FEEDER_INTERVAL_SEC" -s "$FEEDER_PAYLOAD_BYTES" "$SERVER_TUN_IP" >"$CLIENT2_FEEDER_LOG" 2>&1 &
        CLIENT2_FEEDER_PID=$!
    fi
}

count_grants_for_node() {
    local node_id="$1"
    grep -Ec '^grant seq=.* node_id='"$node_id"' ' "$SERVER_LOG" 2>/dev/null || true
}

extract_authorized_sends() {
    local logfile="$1"
    awk -F'[:\t]' '/TOKEN_AUTH/ {value=$5} END {print value + 0}' "$logfile" 2>/dev/null
}

extract_server_data_packets() {
    awk -F'[:\t]' '/\tPKT\t/ {value=$7} END {print value + 0}' "$SERVER_LOG" 2>/dev/null
}

queue_counter() {
    local field="$1"
    PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 - "$field" "$CLIENT1_QUEUE_SUMMARY_JSON" "$CLIENT2_QUEUE_SUMMARY_JSON" <<'PY'
import json
import os
import sys
field = sys.argv[1]
total = 0
for path in sys.argv[2:]:
    if not os.path.exists(path):
        continue
    with open(path, 'r') as fh:
        data = json.load(fh)
    if field.startswith('reason.'):
        total += int(data.get('tun_read_pause_total_by_reason', {}).get(field.split('.', 1)[1], 0))
    else:
        total += int(data.get(field, 0))
print(total)
PY
}

sample_metrics() {
    LONGRUN_CLIENT1_GRANTS="$(count_grants_for_node "$CLIENT1_NODE_ID")"
    LONGRUN_CLIENT2_GRANTS="$(count_grants_for_node "$CLIENT2_NODE_ID")"
    LONGRUN_TOTAL_GRANTS=$((LONGRUN_CLIENT1_GRANTS + LONGRUN_CLIENT2_GRANTS))
    LONGRUN_CLIENT1_AUTHORIZED="$(extract_authorized_sends "$CLIENT1_LOG")"
    LONGRUN_CLIENT2_AUTHORIZED="$(extract_authorized_sends "$CLIENT2_LOG")"
    LONGRUN_SERVER_DATA_PACKETS="$(extract_server_data_packets)"
    RUN_TUN_READ_PAUSE_TOTAL="$(queue_counter tun_read_pause_total)"
    RUN_TUN_READ_RESUME_TOTAL="$(queue_counter tun_read_resume_total)"
    RUN_TUN_READ_PAUSE_BYTES_TOTAL="$(queue_counter reason.queued_bytes_threshold)"
    RUN_TUN_READ_PAUSE_PACKETS_TOTAL="$(queue_counter reason.queued_packets_limit)"
}

run_longrun() {
    local start_ts end_ts now_ts next_sample_ts last_progress_ts
    local prev_grants=0 prev_c1_auth=0 prev_c2_auth=0 prev_server_data=0
    local progress="NO"
    start_ts="$(date +%s)"
    end_ts=$((start_ts + V6_UPLINK_LONGRUN_DURATION_SEC))
    next_sample_ts=$((start_ts + V6_UPLINK_SAMPLE_INTERVAL_SEC))
    last_progress_ts="$start_ts"

    while :; do
        now_ts="$(date +%s)"
        if [ "$now_ts" -ge "$end_ts" ]; then
            break
        fi
        for item in "server:$SERVER_PID" "client1:$CLIENT1_PID" "client2:$CLIENT2_PID" "client1_feeder:$CLIENT1_FEEDER_PID" "client2_feeder:$CLIENT2_FEEDER_PID"; do
            local name="${item%%:*}"
            local pid="${item#*:}"
            if ! kill -0 "$pid" 2>/dev/null; then
                RESULT_REASON="$name 在 dual-long-run 期间提前退出"
                return 1
            fi
        done

        sleep "$V6_UPLINK_PROBE_INTERVAL_SEC"
        now_ts="$(date +%s)"
        if [ "$now_ts" -lt "$next_sample_ts" ] && [ "$now_ts" -lt "$end_ts" ]; then
            continue
        fi

        sample_metrics
        progress="NO"
        if [ "$LONGRUN_TOTAL_GRANTS" -gt "$prev_grants" ] || [ "$LONGRUN_CLIENT1_AUTHORIZED" -gt "$prev_c1_auth" ] || [ "$LONGRUN_CLIENT2_AUTHORIZED" -gt "$prev_c2_auth" ] || [ "$LONGRUN_SERVER_DATA_PACKETS" -gt "$prev_server_data" ]; then
            progress="YES"
            last_progress_ts="$now_ts"
        fi
        local idle_sec=$((now_ts - last_progress_ts))
        if [ "$idle_sec" -gt "$LONGRUN_MAX_IDLE_OBSERVED_SEC" ]; then
            LONGRUN_MAX_IDLE_OBSERVED_SEC="$idle_sec"
        fi
        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
            "$((now_ts - start_ts))" "$LONGRUN_TOTAL_GRANTS" "$LONGRUN_CLIENT1_GRANTS" "$LONGRUN_CLIENT2_GRANTS" \
            "$LONGRUN_CLIENT1_AUTHORIZED" "$LONGRUN_CLIENT2_AUTHORIZED" "$LONGRUN_SERVER_DATA_PACKETS" \
            "$RUN_TUN_READ_PAUSE_TOTAL" "$RUN_TUN_READ_RESUME_TOTAL" "$progress" >> "$SAMPLES_TSV"
        LONGRUN_SAMPLE_COUNT=$((LONGRUN_SAMPLE_COUNT + 1))
        prev_grants="$LONGRUN_TOTAL_GRANTS"
        prev_c1_auth="$LONGRUN_CLIENT1_AUTHORIZED"
        prev_c2_auth="$LONGRUN_CLIENT2_AUTHORIZED"
        prev_server_data="$LONGRUN_SERVER_DATA_PACKETS"
        next_sample_ts=$((next_sample_ts + V6_UPLINK_SAMPLE_INTERVAL_SEC))
    done

    LONGRUN_ACTUAL_DURATION_SEC=$(( $(date +%s) - start_ts ))
    sample_metrics
    return 0
}

generate_formal_2a_summary() {
    PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 "$SCRIPT_DIR/v6_formal_2a_summary.py" \
        --mode uplink \
        --scenario dual-long-run \
        --output "$RUN_SUMMARY_JSON" \
        --queue-summary "$CLIENT1_QUEUE_SUMMARY_JSON" \
        --queue-summary "$CLIENT2_QUEUE_SUMMARY_JSON" \
        --queue-summary "$SERVER_QUEUE_SUMMARY_JSON" \
        --reassembly-log "$SERVER_LOG" \
        --reassembly-log "$CLIENT1_LOG" \
        --reassembly-log "$CLIENT2_LOG" >/dev/null
    RUN_REASSEMBLY_OVERFLOW_EVICT="$(PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 - "$RUN_SUMMARY_JSON" <<'PY'
import json, sys
with open(sys.argv[1]) as fh:
    data = json.load(fh)
print(data.get('reassembly_overflow_evict', 0))
PY
)"
    RUN_UNFINISHED_BLOCK_LIMIT="$(PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 - "$RUN_SUMMARY_JSON" <<'PY'
import json, sys
with open(sys.argv[1]) as fh:
    data = json.load(fh)
print(data.get('unfinished_block_limit', 0))
PY
)"
}

append_final_context() {
    cat >> "$CONTEXT_FILE" <<EOF
result_status=$RESULT_STATUS
result_reason=$RESULT_REASON
actual_duration_sec=$LONGRUN_ACTUAL_DURATION_SEC
dual_long_run_total_grants=$LONGRUN_TOTAL_GRANTS
dual_long_run_client1_grants=$LONGRUN_CLIENT1_GRANTS
dual_long_run_client2_grants=$LONGRUN_CLIENT2_GRANTS
dual_long_run_client1_authorized=$LONGRUN_CLIENT1_AUTHORIZED
dual_long_run_client2_authorized=$LONGRUN_CLIENT2_AUTHORIZED
dual_long_run_server_data_packets=$LONGRUN_SERVER_DATA_PACKETS
dual_long_run_max_idle_observed_sec=$LONGRUN_MAX_IDLE_OBSERVED_SEC
dual_long_run_sample_count=$LONGRUN_SAMPLE_COUNT
tun_read_pause_total=$RUN_TUN_READ_PAUSE_TOTAL
tun_read_resume_total=$RUN_TUN_READ_RESUME_TOTAL
tun_read_pause_bytes_total=$RUN_TUN_READ_PAUSE_BYTES_TOTAL
tun_read_pause_packets_total=$RUN_TUN_READ_PAUSE_PACKETS_TOTAL
reassembly_overflow_evict=$RUN_REASSEMBLY_OVERFLOW_EVICT
unfinished_block_limit=$RUN_UNFINISHED_BLOCK_LIMIT
cleanup_verified=$CLEANUP_VERIFIED
EOF
}

assess_result() {
    if grep -R 'Unable to decrypt packet' "$SERVER_LOG" "$CLIENT1_LOG" "$CLIENT2_LOG" >/dev/null 2>&1; then
        RESULT_REASON="出现 Unable to decrypt packet；trusted_plaintext 路径不成立"
        return 1
    fi
    if grep -R -- '-K ./\|wfb_rx\|wfb_tx\|wfb_token_scheduler' "$SERVER_LOG" "$CLIENT1_LOG" "$CLIENT2_LOG" >/dev/null 2>&1; then
        RESULT_REASON="日志出现旧 split-process/-K 痕迹"
        return 1
    fi
    if [ "$LONGRUN_ACTUAL_DURATION_SEC" -lt "$V6_UPLINK_LONGRUN_DURATION_SEC" ]; then
        RESULT_REASON="实际运行仅 ${LONGRUN_ACTUAL_DURATION_SEC}s，未达到 ${V6_UPLINK_LONGRUN_DURATION_SEC}s"
        return 1
    fi
    if [ "$LONGRUN_TOTAL_GRANTS" -lt "$V6_UPLINK_MIN_GRANTS" ]; then
        RESULT_REASON="grant 总数仅 $LONGRUN_TOTAL_GRANTS，低于最小门槛 $V6_UPLINK_MIN_GRANTS"
        return 1
    fi
    if [ "$LONGRUN_CLIENT1_GRANTS" -le 0 ] || [ "$LONGRUN_CLIENT2_GRANTS" -le 0 ]; then
        RESULT_REASON="未观察到两个 client 都拿到 grant"
        return 1
    fi
    if [ "$LONGRUN_CLIENT1_AUTHORIZED" -lt "$V6_UPLINK_MIN_AUTHORIZED_SENDS" ] || [ "$LONGRUN_CLIENT2_AUTHORIZED" -lt "$V6_UPLINK_MIN_AUTHORIZED_SENDS" ]; then
        RESULT_REASON="authorized_sends 未达门槛"
        return 1
    fi
    if [ "$LONGRUN_SERVER_DATA_PACKETS" -le 0 ]; then
        RESULT_REASON="server 未记录到上行数据包"
        return 1
    fi
    if [ "$LONGRUN_MAX_IDLE_OBSERVED_SEC" -gt "$V6_UPLINK_MAX_IDLE_SEC" ]; then
        RESULT_REASON="最长无推进窗口 ${LONGRUN_MAX_IDLE_OBSERVED_SEC}s 超出门槛 $V6_UPLINK_MAX_IDLE_SEC"
        return 1
    fi
    if [ "$RUN_TUN_READ_PAUSE_TOTAL" -le 0 ] || [ "$RUN_TUN_READ_RESUME_TOTAL" -le 0 ]; then
        RESULT_REASON="issue #22 未观察到 TUN 停读/恢复"
        return 1
    fi
    if [ "$RUN_TUN_READ_PAUSE_BYTES_TOTAL" -le 0 ] && [ "$RUN_TUN_READ_PAUSE_PACKETS_TOTAL" -le 0 ]; then
        RESULT_REASON="未观察到按原因归类的 TUN 停读"
        return 1
    fi
    if [ "$RUN_UNFINISHED_BLOCK_LIMIT" -le 0 ]; then
        RESULT_REASON="unfinished_block_limit 未产出"
        return 1
    fi
    RESULT_REASON="v6 新底座 real-hardware dual-long-run 满足固定口径"
    return 0
}

render_results() {
    cat > "$RESULT_MD" <<EOF
# v6 新底座 real-hardware 上行正式跑数结果

- 结果: $RESULT_STATUS
- 原因: $RESULT_REASON
- 日志目录: $LOG_DIR
- 统一 2A 摘要: $RUN_SUMMARY_JSON
- 上下文: $CONTEXT_FILE
- 采样文件: $SAMPLES_TSV
- cleanup_verified: $CLEANUP_VERIFIED

## 启动路径

- server/client 均直接启动: wfb_v6_uplink --role server/client
- link_security_mode: trusted_plaintext
- 禁止路径: wfb_rx / wfb_tx / wfb_token_scheduler / 旧 -K key

## dual-long-run 指标

| 指标 | 数值 |
| --- | ---: |
| actual_duration_sec | $LONGRUN_ACTUAL_DURATION_SEC |
| total_grants | $LONGRUN_TOTAL_GRANTS |
| client1_grants | $LONGRUN_CLIENT1_GRANTS |
| client2_grants | $LONGRUN_CLIENT2_GRANTS |
| client1_authorized | $LONGRUN_CLIENT1_AUTHORIZED |
| client2_authorized | $LONGRUN_CLIENT2_AUTHORIZED |
| server_data_packets | $LONGRUN_SERVER_DATA_PACKETS |
| max_idle_observed_sec | $LONGRUN_MAX_IDLE_OBSERVED_SEC |
| tun_read_pause_total | $RUN_TUN_READ_PAUSE_TOTAL |
| tun_read_resume_total | $RUN_TUN_READ_RESUME_TOTAL |
| tun_read_pause_bytes_total | $RUN_TUN_READ_PAUSE_BYTES_TOTAL |
| tun_read_pause_packets_total | $RUN_TUN_READ_PAUSE_PACKETS_TOTAL |
| reassembly_overflow_evict | $RUN_REASSEMBLY_OVERFLOW_EVICT |
| unfinished_block_limit | $RUN_UNFINISHED_BLOCK_LIMIT |

## 正式归档要求

- issue #27 回填前必须引用本目录、$RUN_SUMMARY_JSON、$CONTEXT_FILE、$SAMPLES_TSV。
- 若结果不是 PASS，不得关闭 issue #27。
EOF
}

render_formal_conclusion() {
    cat > "$FORMAL_CONCLUSION_MD" <<EOF
## v6 real-hardware 正式证据结论

- 运行批次：$(date +%F) / agent 执行 / 本机三网卡 same-host real-hardware namespaces
- 对应 issue：#27
- 结论状态：$RESULT_STATUS

### 运行场景与前置条件
- 上行主场景：dual-long-run。
- 下行补充场景：未在本脚本运行。
- 无线拓扑与网卡：server=$SERVER_IFACE，client1=$CLIENT1_IFACE，client2=$CLIENT2_IFACE，channel=$CHANNEL $CHANNEL_WIDTH，三端分别位于 network namespace。
- 固定口径：duration=${V6_UPLINK_LONGRUN_DURATION_SEC}s，sample=${V6_UPLINK_SAMPLE_INTERVAL_SEC}s，min_grants=$V6_UPLINK_MIN_GRANTS，min_authorized=$V6_UPLINK_MIN_AUTHORIZED_SENDS，max_idle=${V6_UPLINK_MAX_IDLE_SEC}s。
- 关键启动命令：直接使用 wfb_v6_uplink --role server/client；未使用 wfb_rx/wfb_tx/wfb_token_scheduler；未使用旧 -K key。

### 原始产物层引用
- 上行日志目录：$LOG_DIR
- 统一 2A 摘要：$RUN_SUMMARY_JSON
- 上下文：$CONTEXT_FILE
- 采样：$SAMPLES_TSV
- 原始日志：$SERVER_LOG、$CLIENT1_LOG、$CLIENT2_LOG

### 统一 2A 摘要元数据
- run_kind: real_hardware
- link_security_mode: trusted_plaintext
- scenario_id: v6_real_hardware_uplink_dual_long_run
- feedback_window_covered: false

### 运行时长与关键事件计数
- dual-long-run：actual=${LONGRUN_ACTUAL_DURATION_SEC}s，grant=$LONGRUN_TOTAL_GRANTS，client1_auth=$LONGRUN_CLIENT1_AUTHORIZED，client2_auth=$LONGRUN_CLIENT2_AUTHORIZED，server_data=$LONGRUN_SERVER_DATA_PACKETS，max_idle=${LONGRUN_MAX_IDLE_OBSERVED_SEC}s。
- issue #22 TUN 反压：pause=$RUN_TUN_READ_PAUSE_TOTAL，resume=$RUN_TUN_READ_RESUME_TOTAL，bytes_reason=$RUN_TUN_READ_PAUSE_BYTES_TOTAL，packets_reason=$RUN_TUN_READ_PAUSE_PACKETS_TOTAL。
- RX 重组摘要：reassembly_overflow_evict=$RUN_REASSEMBLY_OVERFLOW_EVICT，unfinished_block_limit=$RUN_UNFINISHED_BLOCK_LIMIT。

### 关键异常与风险信号
- 解密错误：脚本自动拒绝任何 Unable to decrypt packet。
- 旧路径污染：脚本自动拒绝 wfb_rx / wfb_tx / wfb_token_scheduler / -K 痕迹。
- 结果说明：$RESULT_REASON。

### 人工判读结论
- 是否可作为正式 v6 real-hardware 上行证据：$([ "$RESULT_STATUS" = "PASS" ] && echo 是 || echo 否)
- 是否需要重跑：$([ "$RESULT_STATUS" = "PASS" ] && echo 否 || echo 是)
- 对后续新底座跑数的风险提示：本脚本只覆盖上行主场景；issue #27 若要求下行 shared 反馈窗口仍需另行运行 v6 新底座下行 real-hardware 入口。
EOF
}

main() {
    prepare_log_dir
    require_root
    require_no_legacy_commands
    require_binary

    log_info "准备真实网卡拓扑"
    prepare_network_topology

    start_server
    start_client "$CLIENT1_NS" "$CLIENT1_TUN_NAME" "$CLIENT1_TUN_ADDR" "$CLIENT1_NODE_ID" "$CLIENT1_IFACE" "$CLIENT1_QUEUE_SUMMARY_JSON" "$CLIENT1_UPLINK_PAUSE_THRESHOLD_BYTES" "$CLIENT1_UPLINK_RESUME_THRESHOLD_BYTES" "$CLIENT1_UPLINK_QUEUE_PACKETS_LIMIT" "$CLIENT1_LOG" CLIENT1_PID
    start_client "$CLIENT2_NS" "$CLIENT2_TUN_NAME" "$CLIENT2_TUN_ADDR" "$CLIENT2_NODE_ID" "$CLIENT2_IFACE" "$CLIENT2_QUEUE_SUMMARY_JSON" "$CLIENT2_UPLINK_PAUSE_THRESHOLD_BYTES" "$CLIENT2_UPLINK_RESUME_THRESHOLD_BYTES" "$CLIENT2_UPLINK_QUEUE_PACKETS_LIMIT" "$CLIENT2_LOG" CLIENT2_PID
    wait_for_startup
    wait_for_tun "$SERVER_NS" "$SERVER_TUN_NAME"
    wait_for_tun "$CLIENT1_NS" "$CLIENT1_TUN_NAME"
    wait_for_tun "$CLIENT2_NS" "$CLIENT2_TUN_NAME"
    start_feeders

    if run_longrun; then
        generate_formal_2a_summary
        if assess_result; then
            RESULT_STATUS="PASS"
        else
            RESULT_STATUS="FAIL"
        fi
    else
        RESULT_STATUS="FAIL"
        generate_formal_2a_summary || true
    fi

    cleanup
    append_final_context
    render_results
    render_formal_conclusion

    log_info "结果文件: $RESULT_MD"
    if [ "$RESULT_STATUS" = "PASS" ]; then
        log_pass "$RESULT_REASON"
        exit 0
    fi
    log_fail "$RESULT_REASON"
    exit 1
}

main "$@"
