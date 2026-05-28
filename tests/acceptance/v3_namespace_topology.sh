#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

LOG_DIR="${LOG_DIR:-$PROJECT_ROOT/tests/logs/v3_namespace_topology_$(date +%Y%m%d_%H%M%S)}"
RESULT_MD="$LOG_DIR/result.md"
BUILD_LOG="$LOG_DIR/build.log"

SERVER_DIR="$LOG_DIR/server"
CLIENT1_DIR="$LOG_DIR/client1"
CLIENT2_DIR="$LOG_DIR/client2"

SERVER_TUN_LOG="$SERVER_DIR/wfb_tun.log"
CLIENT1_TUN_LOG="$CLIENT1_DIR/wfb_tun.log"
CLIENT2_TUN_LOG="$CLIENT2_DIR/wfb_tun.log"
TOKEN_BRIDGE_LOG="$LOG_DIR/token_namespace_bridge.log"
TOKEN_SCHEDULER_LOG="$LOG_DIR/token_scheduler.log"
CLIENT1_TX_LOG="$CLIENT1_DIR/wfb_tx.log"
CLIENT2_TX_LOG="$CLIENT2_DIR/wfb_tx.log"
CLIENT1_UPLINK_RX_LOG="$CLIENT1_DIR/uplink_rx.log"
CLIENT2_UPLINK_RX_LOG="$CLIENT2_DIR/uplink_rx.log"
CLIENT1_UPLINK_TRAFFIC_LOG="$CLIENT1_DIR/uplink_traffic.log"
CLIENT2_UPLINK_TRAFFIC_LOG="$CLIENT2_DIR/uplink_traffic.log"
UPLINK_SERVER_LOG="$SERVER_DIR/uplink_server.log"
UPLINK_RECEIVED_FILE="$SERVER_DIR/uplink_received.txt"

SERVER_NS="${SERVER_NS:-v3-server}"
CLIENT1_NS="${CLIENT1_NS:-v3-client1}"
CLIENT2_NS="${CLIENT2_NS:-v3-client2}"

SERVER_CLIENT1_SERVER_IF="${SERVER_CLIENT1_SERVER_IF:-eth1}"
SERVER_CLIENT2_SERVER_IF="${SERVER_CLIENT2_SERVER_IF:-eth2}"
CLIENT1_MGMT_IF="${CLIENT1_MGMT_IF:-eth0}"
CLIENT2_MGMT_IF="${CLIENT2_MGMT_IF:-eth0}"

SERVER_CLIENT1_ADDR="${SERVER_CLIENT1_ADDR:-172.31.31.1/30}"
CLIENT1_MGMT_ADDR="${CLIENT1_MGMT_ADDR:-172.31.31.2/30}"
SERVER_CLIENT2_ADDR="${SERVER_CLIENT2_ADDR:-172.31.32.1/30}"
CLIENT2_MGMT_ADDR="${CLIENT2_MGMT_ADDR:-172.31.32.2/30}"
SERVER_CLIENT1_IP="${SERVER_CLIENT1_IP:-172.31.31.1}"
CLIENT1_MGMT_IP="${CLIENT1_MGMT_IP:-172.31.31.2}"
SERVER_CLIENT2_IP="${SERVER_CLIENT2_IP:-172.31.32.1}"
CLIENT2_MGMT_IP="${CLIENT2_MGMT_IP:-172.31.32.2}"

SERVER_TUN_NAME="${SERVER_TUN_NAME:-wfb-v3s}"
CLIENT1_TUN_NAME="${CLIENT1_TUN_NAME:-wfb-v3c1}"
CLIENT2_TUN_NAME="${CLIENT2_TUN_NAME:-wfb-v3c2}"
SERVER_TUN_ADDR="${SERVER_TUN_ADDR:-10.43.0.1/24}"
CLIENT1_TUN_ADDR="${CLIENT1_TUN_ADDR:-10.43.0.11/24}"
CLIENT2_TUN_ADDR="${CLIENT2_TUN_ADDR:-10.43.0.12/24}"
SERVER_TUN_IP="${SERVER_TUN_IP:-10.43.0.1}"
CLIENT1_TUN_IP="${CLIENT1_TUN_IP:-10.43.0.11}"
CLIENT2_TUN_IP="${CLIENT2_TUN_IP:-10.43.0.12}"

SERVER_TUN_LISTEN_PORT="${SERVER_TUN_LISTEN_PORT:-5800}"
SERVER_TUN_PEER_PORT="${SERVER_TUN_PEER_PORT:-5700}"
CLIENT1_TUN_LISTEN_PORT="${CLIENT1_TUN_LISTEN_PORT:-5800}"
CLIENT1_TUN_PEER_PORT="${CLIENT1_TUN_PEER_PORT:-5700}"
CLIENT2_TUN_LISTEN_PORT="${CLIENT2_TUN_LISTEN_PORT:-5800}"
CLIENT2_TUN_PEER_PORT="${CLIENT2_TUN_PEER_PORT:-5700}"

TOKEN_READY_BASE="${TOKEN_READY_BASE:-wfb-scheduler}"
SERVER_TOKEN_GRANT_BASE="${SERVER_TOKEN_GRANT_BASE:-wfb-v3-bridge-grant}"
CLIENT1_TX_INPUT_PORT="${CLIENT1_TX_INPUT_PORT:-$CLIENT1_TUN_PEER_PORT}"
CLIENT2_TX_INPUT_PORT="${CLIENT2_TX_INPUT_PORT:-$CLIENT2_TUN_PEER_PORT}"
CLIENT1_TOKEN_GRANT_BASE="${CLIENT1_TOKEN_GRANT_BASE:-$CLIENT1_TX_INPUT_PORT}"
CLIENT2_TOKEN_GRANT_BASE="${CLIENT2_TOKEN_GRANT_BASE:-$CLIENT2_TX_INPUT_PORT}"

LINK_ID="${LINK_ID:-303}"
EPOCH="${EPOCH:-$(date +%s)}"
CLIENT1_NODE_ID="${CLIENT1_NODE_ID:-1}"
CLIENT2_NODE_ID="${CLIENT2_NODE_ID:-2}"
TOKEN_DURATION_MS="${TOKEN_DURATION_MS:-500}"
TOKEN_GUARD_MS="${TOKEN_GUARD_MS:-100}"
CLIENT1_UPLINK_RX_DEBUG_PORT="${CLIENT1_UPLINK_RX_DEBUG_PORT:-41011}"
CLIENT2_UPLINK_RX_DEBUG_PORT="${CLIENT2_UPLINK_RX_DEBUG_PORT:-41012}"
UPLINK_TRANSFER_PORT="${UPLINK_TRANSFER_PORT:-5903}"
UPLINK_STREAM_SECONDS="${UPLINK_STREAM_SECONDS:-8}"
UPLINK_COLLECT_TIMEOUT_SEC="${UPLINK_COLLECT_TIMEOUT_SEC:-18}"
CLIENT_JOIN_GAP_SEC="${CLIENT_JOIN_GAP_SEC:-0.2}"
CLIENT1_UPLINK_MARKER="${CLIENT1_UPLINK_MARKER:-CLIENT1_UPLINK}"
CLIENT2_UPLINK_MARKER="${CLIENT2_UPLINK_MARKER:-CLIENT2_UPLINK}"

STARTUP_WAIT_SEC="${STARTUP_WAIT_SEC:-1}"

PIDS=()
FAIL_REASON=""
CLEANUP_VERIFIED="否"

SERVER_TUN_PID=""
CLIENT1_TUN_PID=""
CLIENT2_TUN_PID=""
TOKEN_BRIDGE_PID=""
TOKEN_SCHEDULER_PID=""
CLIENT1_TX_PID=""
CLIENT2_TX_PID=""
CLIENT1_UPLINK_RX_PID=""
CLIENT2_UPLINK_RX_PID=""
UPLINK_COLLECTOR_PID=""
CLIENT1_TRAFFIC_PID=""
CLIENT2_TRAFFIC_PID=""

log_info() { echo "[INFO] $(date '+%H:%M:%S') $1"; }
log_pass() { echo "[PASS] $(date '+%H:%M:%S') $1"; }
log_fail() { echo "[FAIL] $(date '+%H:%M:%S') $1" >&2; }

prepare_log_dir() {
    mkdir -p "$SERVER_DIR" "$CLIENT1_DIR" "$CLIENT2_DIR"
}

render_result() {
    local status="$1"
    mkdir -p "$LOG_DIR"
    cat > "$RESULT_MD" <<EOF
# 第三版三 namespace 多客户端轮换上行验收结果

- 结果: $status
- 原因: ${FAIL_REASON:-无}
- 日志目录: $LOG_DIR
- 构建日志: $BUILD_LOG
- cleanup_verified: $CLEANUP_VERIFIED

## Namespace

- server: $SERVER_NS
- client1: $CLIENT1_NS
- client2: $CLIENT2_NS

## 管理链路

- server <-> client1: $SERVER_CLIENT1_ADDR / $CLIENT1_MGMT_ADDR
- server <-> client2: $SERVER_CLIENT2_ADDR / $CLIENT2_MGMT_ADDR

## TUN/IP 拓扑

- server TUN: $SERVER_TUN_NAME ($SERVER_TUN_ADDR)
- client1 TUN: $CLIENT1_TUN_NAME ($CLIENT1_TUN_ADDR)
- client2 TUN: $CLIENT2_TUN_NAME ($CLIENT2_TUN_ADDR)

## 进程日志

- server: $SERVER_TUN_LOG
- client1: $CLIENT1_TUN_LOG
- client2: $CLIENT2_TUN_LOG
- token bridge: $TOKEN_BRIDGE_LOG
- token scheduler: $TOKEN_SCHEDULER_LOG
- client1 tx: $CLIENT1_TX_LOG
- client2 tx: $CLIENT2_TX_LOG
- client1 uplink rx: $CLIENT1_UPLINK_RX_LOG
- client2 uplink rx: $CLIENT2_UPLINK_RX_LOG
- server uplink received: $UPLINK_RECEIVED_FILE

## 控制桥

- server ready 入口: $TOKEN_READY_BASE
- server grant 入口: $SERVER_TOKEN_GRANT_BASE
- node 1 -> client grant 入口: $CLIENT1_NS / $CLIENT1_TOKEN_GRANT_BASE
- node 2 -> client grant 入口: $CLIENT2_NS / $CLIENT2_TOKEN_GRANT_BASE

## 真实 TUN/IP 上行路径

- client1: $CLIENT1_TUN_IP -> $SERVER_TUN_IP:$UPLINK_TRANSFER_PORT
- client2: $CLIENT2_TUN_IP -> $SERVER_TUN_IP:$UPLINK_TRANSFER_PORT
- client2 动态加入延迟: ${CLIENT_JOIN_GAP_SEC}s
- token_duration_ms: $TOKEN_DURATION_MS
- token_guard_ms: $TOKEN_GUARD_MS

## issue #4 验收点

- 运行中动态加入: 已验证 client2 后续上行就绪声明进入 [$CLIENT1_NODE_ID,$CLIENT2_NODE_ID] 活跃队列，并在移除旧静默节点前自然轮到 grant
- 粗粒度移除后自愈重入: 已验证 client1 被 remove 后通过后续上行就绪声明以 [$CLIENT2_NODE_ID,$CLIENT1_NODE_ID] 重入并再次获得 grant
- 日志验证来源: $TOKEN_SCHEDULER_LOG

## 当前切片范围

- 已拉起并验证三 namespace 基础管理链路
- 已在三 namespace 内启动 \`wfb_tun\` 并创建 TUN/IP 接口
- 已启动测试专用跨 namespace 控制桥与 Token scheduler
- 已验证运行中动态加入、粗粒度移除后自愈重入，以及两个 client 按 Token Passing 轮换获得上行机会
- 已在 server TUN/IP 侧观察两个 client 的真实上行 marker
EOF
}

fail_exit() {
    FAIL_REASON="$1"
    render_result "FAIL"
    log_fail "$FAIL_REASON"
    exit 1
}

cleanup_processes() {
    local pid
    for pid in "${PIDS[@]:-}"; do
        kill "$pid" 2>/dev/null || true
    done
    for pid in "${PIDS[@]:-}"; do
        wait "$pid" 2>/dev/null || true
    done
    PIDS=()
}

delete_namespace_if_exists() {
    local namespace="$1"
    ip netns del "$namespace" 2>/dev/null || true
}

cleanup() {
    cleanup_processes
    delete_namespace_if_exists "$SERVER_NS"
    delete_namespace_if_exists "$CLIENT1_NS"
    delete_namespace_if_exists "$CLIENT2_NS"
}

trap cleanup EXIT

require_root() {
    if [ "${EUID}" -ne 0 ]; then
        fail_exit "必须以 root 运行，network namespace 与 TUN 设备创建需要特权"
    fi
}

require_command() {
    local name="$1"
    if ! command -v "$name" >/dev/null 2>&1; then
        fail_exit "缺少命令: $name"
    fi
}

require_executable() {
    local path="$1"
    if [ ! -x "$path" ]; then
        fail_exit "缺少可执行文件: $path"
    fi
}

build_acceptance_binaries() {
    local required_binaries=(
        wfb_tun
        wfb_token_namespace_bridge
        wfb_tx
        wfb_rx
        wfb_token_scheduler
        wfb_keygen
    )
    local binary
    local need_build="否"

    for binary in "${required_binaries[@]}"; do
        if [ ! -x "$PROJECT_ROOT/$binary" ]; then
            need_build="是"
            break
        fi
    done

    if [ "$need_build" = "否" ]; then
        echo "acceptance 二进制已存在，跳过构建" >"$BUILD_LOG"
        log_pass "acceptance 二进制已存在"
    else
        require_command make
        log_info "构建 v3 acceptance 所需二进制"
        if ! make wfb_tun wfb_token_namespace_bridge wfb_tx wfb_rx wfb_token_scheduler wfb_keygen >"$BUILD_LOG" 2>&1; then
            fail_exit "构建 v3 acceptance 二进制失败，查看日志: $BUILD_LOG"
        fi
    fi

    for binary in "${required_binaries[@]}"; do
        require_executable "$PROJECT_ROOT/$binary"
    done

    if [ ! -f "$PROJECT_ROOT/gs.key" ] || [ ! -f "$PROJECT_ROOT/drone.key" ]; then
        log_info "生成 acceptance 所需密钥"
        if ! "$PROJECT_ROOT/wfb_keygen" >>"$BUILD_LOG" 2>&1; then
            fail_exit "生成 acceptance 密钥失败，查看日志: $BUILD_LOG"
        fi
    fi
    log_pass "v3 acceptance 二进制构建成功"
}

create_namespace() {
    local namespace="$1"
    ip netns add "$namespace"
    ip -n "$namespace" link set lo up
}

attach_namespaces_with_veth() {
    local left_namespace="$1"
    local left_tmp_if="$2"
    local left_if="$3"
    local left_addr="$4"
    local right_namespace="$5"
    local right_tmp_if="$6"
    local right_if="$7"
    local right_addr="$8"

    ip link add "$left_tmp_if" type veth peer name "$right_tmp_if"
    ip link set "$left_tmp_if" netns "$left_namespace"
    ip link set "$right_tmp_if" netns "$right_namespace"

    if [ "$left_tmp_if" != "$left_if" ]; then
        ip -n "$left_namespace" link set "$left_tmp_if" name "$left_if"
    fi
    if [ "$right_tmp_if" != "$right_if" ]; then
        ip -n "$right_namespace" link set "$right_tmp_if" name "$right_if"
    fi

    ip -n "$left_namespace" link set "$left_if" up
    ip -n "$left_namespace" addr add "$left_addr" dev "$left_if"
    ip -n "$right_namespace" link set "$right_if" up
    ip -n "$right_namespace" addr add "$right_addr" dev "$right_if"
}

wait_for_interface() {
    local namespace="$1"
    local ifname="$2"
    local tries=20
    local i

    for ((i = 0; i < tries; i++)); do
        if ip -n "$namespace" link show dev "$ifname" >/dev/null 2>&1; then
            return 0
        fi
        sleep 0.5
    done

    return 1
}

assert_interface_address() {
    local namespace="$1"
    local ifname="$2"
    local addr="$3"

    if ! ip -n "$namespace" -o -4 addr show dev "$ifname" | grep -Fq " $addr "; then
        fail_exit "$namespace 的接口 $ifname 未配置地址 $addr"
    fi
}

start_wfb_tun() {
    local namespace="$1"
    local tun_name="$2"
    local tun_addr="$3"
    local listen_port="$4"
    local peer_port="$5"
    local logfile="$6"
    local pid_var="$7"
    local pid

    log_info "在 $namespace 启动 wfb_tun"
    ip netns exec "$namespace" "$PROJECT_ROOT/wfb_tun" \
        -t "$tun_name" \
        -a "$tun_addr" \
        -c 127.0.0.1 \
        -l "$listen_port" \
        -u "$peer_port" >"$logfile" 2>&1 &
    pid=$!
    PIDS+=("$pid")
    printf -v "$pid_var" '%s' "$pid"

    sleep "$STARTUP_WAIT_SEC"
    if ! kill -0 "$pid" 2>/dev/null; then
        fail_exit "$namespace 的 wfb_tun 启动失败，查看日志: $logfile"
    fi
}

start_token_namespace_bridge() {
    local pid

    log_info "启动测试专用跨 namespace 控制桥"
    "$PROJECT_ROOT/wfb_token_namespace_bridge" \
        -S "$SERVER_NS" \
        -r "$TOKEN_READY_BASE" \
        -g "$SERVER_TOKEN_GRANT_BASE" \
        -n "$CLIENT1_NODE_ID:$CLIENT1_NS:$CLIENT1_TOKEN_GRANT_BASE" \
        -n "$CLIENT2_NODE_ID:$CLIENT2_NS:$CLIENT2_TOKEN_GRANT_BASE" >"$TOKEN_BRIDGE_LOG" 2>&1 &
    pid=$!
    PIDS+=("$pid")
    TOKEN_BRIDGE_PID="$pid"

    sleep "$STARTUP_WAIT_SEC"
    if ! kill -0 "$pid" 2>/dev/null; then
        fail_exit "测试专用控制桥启动失败，查看日志: $TOKEN_BRIDGE_LOG"
    fi
    log_pass "测试专用跨 namespace 控制桥已启动"
}

start_token_scheduler() {
    local pid

    log_info "在 $SERVER_NS 启动 Token scheduler"
    ip netns exec "$SERVER_NS" "$PROJECT_ROOT/wfb_token_scheduler" \
        -n "$CLIENT1_NODE_ID,$CLIENT2_NODE_ID" \
        -d "$TOKEN_DURATION_MS" \
        -g "$TOKEN_GUARD_MS" \
        -s "$SERVER_TOKEN_GRANT_BASE" >"$TOKEN_SCHEDULER_LOG" 2>&1 &
    pid=$!
    PIDS+=("$pid")
    TOKEN_SCHEDULER_PID="$pid"

    sleep "$STARTUP_WAIT_SEC"
    if ! kill -0 "$pid" 2>/dev/null; then
        fail_exit "Token scheduler 启动失败，查看日志: $TOKEN_SCHEDULER_LOG"
    fi
    log_pass "Token scheduler 已启动"
}

start_client_uplink_rx() {
    local namespace="$1"
    local debug_port="$2"
    local server_mgmt_ip="$3"
    local logfile="$4"
    local pid_var="$5"
    local pid

    log_info "在 $namespace 启动上行 wfb_rx"
    ip netns exec "$namespace" "$PROJECT_ROOT/wfb_rx" \
        -a "$debug_port" \
        -K "$PROJECT_ROOT/gs.key" \
        -c "$server_mgmt_ip" \
        -u "$SERVER_TUN_LISTEN_PORT" \
        -N 0 \
        -i "$LINK_ID" \
        -e "$EPOCH" \
        -R 524288 \
        -s 524288 >"$logfile" 2>&1 &
    pid=$!
    PIDS+=("$pid")
    printf -v "$pid_var" '%s' "$pid"

    sleep "$STARTUP_WAIT_SEC"
    if ! kill -0 "$pid" 2>/dev/null; then
        fail_exit "$namespace 的上行 wfb_rx 启动失败，查看日志: $logfile"
    fi
}

start_client_tx() {
    local namespace="$1"
    local node_id="$2"
    local input_port="$3"
    local debug_port="$4"
    local logfile="$5"
    local pid_var="$6"
    local pid

    log_info "在 $namespace 启动 Token gate wfb_tx"
    ip netns exec "$namespace" "$PROJECT_ROOT/wfb_tx" \
        -g \
        -q "$node_id" \
        -K "$PROJECT_ROOT/drone.key" \
        -u "$input_port" \
        -D "$debug_port" \
        -i "$LINK_ID" \
        -e "$EPOCH" \
        -R 524288 \
        -s 524288 \
        "$namespace-uplink" >"$logfile" 2>&1 &
    pid=$!
    PIDS+=("$pid")
    printf -v "$pid_var" '%s' "$pid"

    sleep "$STARTUP_WAIT_SEC"
    if ! kill -0 "$pid" 2>/dev/null; then
        fail_exit "$namespace 的 wfb_tx 启动失败，查看日志: $logfile"
    fi
}

start_udp_uplink_collector() {
    local pid

    log_info "在 $SERVER_NS 启动 server TUN 侧 UDP 上行收集器"
    ip netns exec "$SERVER_NS" timeout "$UPLINK_COLLECT_TIMEOUT_SEC" \
        nc -u -l -k "$SERVER_TUN_IP" "$UPLINK_TRANSFER_PORT" >"$UPLINK_RECEIVED_FILE" 2>"$UPLINK_SERVER_LOG" &
    pid=$!
    PIDS+=("$pid")
    UPLINK_COLLECTOR_PID="$pid"

    sleep 1
    if ! kill -0 "$pid" 2>/dev/null; then
        fail_exit "server TUN 侧 UDP 上行收集器启动失败，查看日志: $UPLINK_SERVER_LOG"
    fi
}

start_udp_uplink_stream() {
    local namespace="$1"
    local marker="$2"
    local logfile="$3"
    local pid_var="$4"
    local start_delay="${5:-0}"
    local pid

    log_info "在 $namespace 产生真实 TUN/IP 上行流量"
    setsid ip netns exec "$namespace" bash -lc '
        marker="$1"
        target_ip="$2"
        target_port="$3"
        duration="$4"
        start_delay="$5"
        sleep "$start_delay"
        end=$((SECONDS + duration))
        seq_no=0
        while [ "$SECONDS" -lt "$end" ]; do
            printf "%s seq=%04d\n" "$marker" "$seq_no" >"/dev/udp/$target_ip/$target_port" || true
            seq_no=$((seq_no + 1))
            sleep 0.1
        done
    ' _ "$marker" "$SERVER_TUN_IP" "$UPLINK_TRANSFER_PORT" "$UPLINK_STREAM_SECONDS" "$start_delay" >"$logfile" 2>&1 &
    pid=$!
    PIDS+=("$pid")
    printf -v "$pid_var" '%s' "$pid"
}

assert_process_alive() {
    local pid="$1"
    local name="$2"
    if ! kill -0 "$pid" 2>/dev/null; then
        fail_exit "$name 已提前退出"
    fi
}

verify_management_ping() {
    local namespace="$1"
    local target_ip="$2"
    local title="$3"

    log_info "验证 $title"
    if ! ip netns exec "$namespace" ping -c 1 -W 2 "$target_ip" >/dev/null 2>&1; then
        fail_exit "$title 失败"
    fi
    log_pass "$title 成功"
}

wait_for_log_pattern() {
    local logfile="$1"
    local pattern="$2"
    local title="$3"
    local tries=40
    local i

    for ((i = 0; i < tries; i++)); do
        if [ -f "$logfile" ] && grep -Fq "$pattern" "$logfile"; then
            log_pass "$title"
            return 0
        fi
        sleep 0.25
    done

    fail_exit "$title 未在日志中出现: $logfile"
}

wait_for_file_contains() {
    local path="$1"
    local marker="$2"
    local title="$3"
    local tries=40
    local i

    for ((i = 0; i < tries; i++)); do
        if [ -f "$path" ] && grep -Fq "$marker" "$path"; then
            log_pass "$title"
            return 0
        fi
        sleep 0.25
    done

    fail_exit "$title 未在 server TUN 侧观测到: $path"
}

count_grants_for_node() {
    local node_id="$1"
    grep -Ec "^grant seq=.* node_id=$node_id " "$TOKEN_SCHEDULER_LOG" 2>/dev/null || true
}

assert_scheduler_rotated_two_clients() {
    local line
    local grant_node
    local previous_node=""
    local alternating_streak=0
    local streak_seen_client1="否"
    local streak_seen_client2="否"

    while IFS= read -r line; do
        if [[ "$line" =~ ^grant\ seq=.*node_id=([0-9]+).*active_queue=\[([0-9]+),([0-9]+)\] ]]; then
            if [ "${BASH_REMATCH[2]}" = "${BASH_REMATCH[3]}" ]; then
                continue
            fi
            grant_node="${BASH_REMATCH[1]}"
            if [ -n "$previous_node" ] && [ "$grant_node" = "$previous_node" ]; then
                alternating_streak=1
                streak_seen_client1="否"
                streak_seen_client2="否"
            else
                alternating_streak=$((alternating_streak + 1))
            fi

            if [ "$grant_node" = "$CLIENT1_NODE_ID" ]; then
                streak_seen_client1="是"
            fi
            if [ "$grant_node" = "$CLIENT2_NODE_ID" ]; then
                streak_seen_client2="是"
            fi

            if [ "$alternating_streak" -ge 4 ] && [ "$streak_seen_client1" = "是" ] && [ "$streak_seen_client2" = "是" ]; then
                log_pass "双客户端按 Token Passing 轮换获得 grant"
                return 0
            fi

            previous_node="$grant_node"
        fi
    done < "$TOKEN_SCHEDULER_LOG"

    fail_exit "未观察到稳定的双客户端 grant 交替窗口"
}

assert_dynamic_join_grant() {
    local line
    local dynamic_join_seen="否"

    while IFS= read -r line; do
        if [[ "$line" =~ ^join/rejoin\ node_id=$CLIENT2_NODE_ID\ active_queue=\[$CLIENT1_NODE_ID,$CLIENT2_NODE_ID\] ]]; then
            dynamic_join_seen="是"
            continue
        fi

        if [ "$dynamic_join_seen" = "是" ] && [[ "$line" =~ ^remove\ node_id= ]]; then
            break
        fi

        if [ "$dynamic_join_seen" = "是" ] && [[ "$line" =~ ^grant\ seq=.*node_id=$CLIENT2_NODE_ID.*active_queue=\[$CLIENT1_NODE_ID,$CLIENT2_NODE_ID\] ]]; then
            log_pass "运行中新 client 在双客户端活跃队列中自然轮到 grant"
            return 0
        fi
    done < "$TOKEN_SCHEDULER_LOG"

    fail_exit "未观察到运行中新 client 在双客户端活跃队列中自然轮到 grant"
}

assert_self_healing_rejoin() {
    local line
    local removal_seen="否"
    local rejoin_seen="否"

    while IFS= read -r line; do
        if [[ "$line" =~ ^remove\ node_id=$CLIENT1_NODE_ID\  ]]; then
            removal_seen="是"
            continue
        fi

        if [ "$removal_seen" = "是" ] && [[ "$line" =~ ^join/rejoin\ node_id=$CLIENT1_NODE_ID\ active_queue=\[$CLIENT2_NODE_ID,$CLIENT1_NODE_ID\] ]]; then
            rejoin_seen="是"
            continue
        fi

        if [ "$rejoin_seen" = "是" ] && [[ "$line" =~ ^grant\ seq=.*node_id=$CLIENT1_NODE_ID.*active_queue=\[$CLIENT2_NODE_ID,$CLIENT1_NODE_ID\] ]]; then
            log_pass "静默 client 粗粒度移除后通过上行就绪声明自愈重入并再次获得 grant"
            return 0
        fi
    done < "$TOKEN_SCHEDULER_LOG"

    fail_exit "未观察到静默 client 移除后的自愈重入 grant"
}

extract_token_auth_line() {
    local logfile="$1"
    if [ ! -f "$logfile" ]; then
        return
    fi
    grep 'TOKEN_AUTH' "$logfile" 2>/dev/null | tail -1 || true
}

extract_token_auth_field() {
    local logfile="$1"
    local field="$2"
    local line
    line="$(extract_token_auth_line "$logfile")"
    if [[ ! "$line" =~ TOKEN_AUTH[[:space:]]+([0-9]+):([0-9]+):([0-9]+):([0-9]+) ]]; then
        echo 0
        return
    fi

    case "$field" in
        accepted_events) echo "${BASH_REMATCH[1]}" ;;
        rejected_events) echo "${BASH_REMATCH[2]}" ;;
        authorized_sends) echo "${BASH_REMATCH[3]}" ;;
        denied_sends) echo "${BASH_REMATCH[4]}" ;;
        *) echo 0 ;;
    esac
}

assert_client_authorized_progress() {
    local logfile="$1"
    local title="$2"
    local authorized_sends
    authorized_sends="$(extract_token_auth_field "$logfile" authorized_sends)"
    if [ "$authorized_sends" -le 0 ]; then
        fail_exit "$title 未产生授权发送进展，查看日志: $logfile"
    fi
    log_pass "$title 产生授权发送进展"
}

verify_cleanup() {
    if ip netns list | grep -Eq "(^| )${SERVER_NS}( |$)"; then
        return 1
    fi
    if ip netns list | grep -Eq "(^| )${CLIENT1_NS}( |$)"; then
        return 1
    fi
    if ip netns list | grep -Eq "(^| )${CLIENT2_NS}( |$)"; then
        return 1
    fi
    return 0
}

main() {
    prepare_log_dir
    require_root
    require_command ip
    require_command ping
    require_command grep
    require_command nc
    require_command timeout
    require_command setsid

    build_acceptance_binaries

    log_info "清理旧的 v3 namespace 骨架资源"
    cleanup

    log_info "创建三 namespace 与点对点管理链路"
    create_namespace "$SERVER_NS"
    create_namespace "$CLIENT1_NS"
    create_namespace "$CLIENT2_NS"

    attach_namespaces_with_veth \
        "$SERVER_NS" "v3s1p0" "$SERVER_CLIENT1_SERVER_IF" "$SERVER_CLIENT1_ADDR" \
        "$CLIENT1_NS" "v3c1p0" "$CLIENT1_MGMT_IF" "$CLIENT1_MGMT_ADDR"
    attach_namespaces_with_veth \
        "$SERVER_NS" "v3s2p0" "$SERVER_CLIENT2_SERVER_IF" "$SERVER_CLIENT2_ADDR" \
        "$CLIENT2_NS" "v3c2p0" "$CLIENT2_MGMT_IF" "$CLIENT2_MGMT_ADDR"
    assert_interface_address "$SERVER_NS" "$SERVER_CLIENT1_SERVER_IF" "$SERVER_CLIENT1_ADDR"
    assert_interface_address "$CLIENT1_NS" "$CLIENT1_MGMT_IF" "$CLIENT1_MGMT_ADDR"
    assert_interface_address "$SERVER_NS" "$SERVER_CLIENT2_SERVER_IF" "$SERVER_CLIENT2_ADDR"
    assert_interface_address "$CLIENT2_NS" "$CLIENT2_MGMT_IF" "$CLIENT2_MGMT_ADDR"
    log_pass "三 namespace 点对点管理链路创建完成"

    start_token_namespace_bridge
    start_token_scheduler

    start_wfb_tun "$SERVER_NS" "$SERVER_TUN_NAME" "$SERVER_TUN_ADDR" "$SERVER_TUN_LISTEN_PORT" "$SERVER_TUN_PEER_PORT" "$SERVER_TUN_LOG" SERVER_TUN_PID
    start_wfb_tun "$CLIENT1_NS" "$CLIENT1_TUN_NAME" "$CLIENT1_TUN_ADDR" "$CLIENT1_TUN_LISTEN_PORT" "$CLIENT1_TUN_PEER_PORT" "$CLIENT1_TUN_LOG" CLIENT1_TUN_PID
    start_wfb_tun "$CLIENT2_NS" "$CLIENT2_TUN_NAME" "$CLIENT2_TUN_ADDR" "$CLIENT2_TUN_LISTEN_PORT" "$CLIENT2_TUN_PEER_PORT" "$CLIENT2_TUN_LOG" CLIENT2_TUN_PID
    start_client_uplink_rx "$CLIENT1_NS" "$CLIENT1_UPLINK_RX_DEBUG_PORT" "$SERVER_CLIENT1_IP" "$CLIENT1_UPLINK_RX_LOG" CLIENT1_UPLINK_RX_PID
    start_client_uplink_rx "$CLIENT2_NS" "$CLIENT2_UPLINK_RX_DEBUG_PORT" "$SERVER_CLIENT2_IP" "$CLIENT2_UPLINK_RX_LOG" CLIENT2_UPLINK_RX_PID
    start_client_tx "$CLIENT1_NS" "$CLIENT1_NODE_ID" "$CLIENT1_TX_INPUT_PORT" "$CLIENT1_UPLINK_RX_DEBUG_PORT" "$CLIENT1_TX_LOG" CLIENT1_TX_PID
    start_client_tx "$CLIENT2_NS" "$CLIENT2_NODE_ID" "$CLIENT2_TX_INPUT_PORT" "$CLIENT2_UPLINK_RX_DEBUG_PORT" "$CLIENT2_TX_LOG" CLIENT2_TX_PID

    if ! wait_for_interface "$SERVER_NS" "$SERVER_TUN_NAME"; then
        fail_exit "server TUN 未按预期创建"
    fi
    if ! wait_for_interface "$CLIENT1_NS" "$CLIENT1_TUN_NAME"; then
        fail_exit "client1 TUN 未按预期创建"
    fi
    if ! wait_for_interface "$CLIENT2_NS" "$CLIENT2_TUN_NAME"; then
        fail_exit "client2 TUN 未按预期创建"
    fi
    assert_interface_address "$SERVER_NS" "$SERVER_TUN_NAME" "$SERVER_TUN_ADDR"
    assert_interface_address "$CLIENT1_NS" "$CLIENT1_TUN_NAME" "$CLIENT1_TUN_ADDR"
    assert_interface_address "$CLIENT2_NS" "$CLIENT2_TUN_NAME" "$CLIENT2_TUN_ADDR"
    log_pass "三 namespace TUN/IP 接口创建完成"

    assert_process_alive "$SERVER_TUN_PID" "server wfb_tun"
    assert_process_alive "$CLIENT1_TUN_PID" "client1 wfb_tun"
    assert_process_alive "$CLIENT2_TUN_PID" "client2 wfb_tun"
    assert_process_alive "$TOKEN_BRIDGE_PID" "token namespace bridge"
    assert_process_alive "$TOKEN_SCHEDULER_PID" "token scheduler"
    assert_process_alive "$CLIENT1_UPLINK_RX_PID" "client1 uplink rx"
    assert_process_alive "$CLIENT2_UPLINK_RX_PID" "client2 uplink rx"
    assert_process_alive "$CLIENT1_TX_PID" "client1 tx"
    assert_process_alive "$CLIENT2_TX_PID" "client2 tx"

    verify_management_ping "$CLIENT1_NS" "$SERVER_CLIENT1_IP" "client1 到 server 点对点链路连通性"
    verify_management_ping "$CLIENT2_NS" "$SERVER_CLIENT2_IP" "client2 到 server 点对点链路连通性"

    start_udp_uplink_collector
    start_udp_uplink_stream "$CLIENT2_NS" "$CLIENT2_UPLINK_MARKER" "$CLIENT2_UPLINK_TRAFFIC_LOG" CLIENT2_TRAFFIC_PID "$CLIENT_JOIN_GAP_SEC"
    start_udp_uplink_stream "$CLIENT1_NS" "$CLIENT1_UPLINK_MARKER" "$CLIENT1_UPLINK_TRAFFIC_LOG" CLIENT1_TRAFFIC_PID
    wait_for_log_pattern "$TOKEN_SCHEDULER_LOG" "join/rejoin node_id=$CLIENT1_NODE_ID active_queue=[$CLIENT1_NODE_ID]" "client1 上行就绪声明进入活跃队列"
    wait_for_log_pattern "$TOKEN_SCHEDULER_LOG" "join/rejoin node_id=$CLIENT2_NODE_ID active_queue=[$CLIENT1_NODE_ID,$CLIENT2_NODE_ID]" "client2 顺序上行就绪声明进入活跃队列"
    sleep 2
    assert_dynamic_join_grant

    log_info "停止 client1 上行流量以触发静默移除"
    kill -TERM "-$CLIENT1_TRAFFIC_PID" 2>/dev/null || kill "$CLIENT1_TRAFFIC_PID" 2>/dev/null || true
    wait "$CLIENT1_TRAFFIC_PID" 2>/dev/null || true
    wait_for_log_pattern "$TOKEN_SCHEDULER_LOG" "remove node_id=$CLIENT1_NODE_ID " "client1 静默后被粗粒度移除"
    start_udp_uplink_stream "$CLIENT1_NS" "$CLIENT1_UPLINK_MARKER" "$CLIENT1_UPLINK_TRAFFIC_LOG" CLIENT1_TRAFFIC_PID
    wait_for_log_pattern "$TOKEN_SCHEDULER_LOG" "join/rejoin node_id=$CLIENT1_NODE_ID active_queue=[$CLIENT2_NODE_ID,$CLIENT1_NODE_ID]" "client1 后续上行就绪声明自愈重入"
    assert_self_healing_rejoin

    wait "$CLIENT1_TRAFFIC_PID" 2>/dev/null || true
    wait "$CLIENT2_TRAFFIC_PID" 2>/dev/null || true
    sleep 1

    if [ "$(count_grants_for_node "$CLIENT1_NODE_ID")" -le 0 ]; then
        fail_exit "未观察到 client1 grant 留痕"
    fi
    if [ "$(count_grants_for_node "$CLIENT2_NODE_ID")" -le 0 ]; then
        fail_exit "未观察到 client2 grant 留痕"
    fi
    assert_scheduler_rotated_two_clients
    wait_for_file_contains "$UPLINK_RECEIVED_FILE" "$CLIENT1_UPLINK_MARKER" "server TUN 侧观测到 client1 上行 marker"
    wait_for_file_contains "$UPLINK_RECEIVED_FILE" "$CLIENT2_UPLINK_MARKER" "server TUN 侧观测到 client2 上行 marker"
    assert_client_authorized_progress "$CLIENT1_TX_LOG" "client1"
    assert_client_authorized_progress "$CLIENT2_TX_LOG" "client2"

    cleanup
    if ! verify_cleanup; then
        fail_exit "namespace 清理校验失败"
    fi
    CLEANUP_VERIFIED="是"

    render_result "PASS"
    log_pass "第三版三 namespace 多客户端轮换上行 acceptance 通过"
}

main "$@"
