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
- client1 ready accepts: $CLIENT1_READY_ACCEPTS
- client2 ready accepts: $CLIENT2_READY_ACCEPTS
- client1 grants: $CLIENT1_GRANTS
- client2 grants: $CLIENT2_GRANTS
- client1 authorized sends: $CLIENT1_AUTHORIZED_SENDS
- client2 authorized sends: $CLIENT2_AUTHORIZED_SENDS
- 已覆盖: trusted_plaintext client TUN -> 空口 -> server TUN 首条新底座上行路径
- 已覆盖: 双客户端 READY/GRANT 持续推进与独立 TCP/IP 会话语义（以并发 ping 往返证明）
- 未覆盖: issue #22 队列/反压、issue #23 RX 溢出、issue #24 反馈窗口正式验收
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
        --guard-interval-ms "$GUARD_INTERVAL_MS" >"$SERVER_LOG" 2>&1 &
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

    log_info "启动 $ns 新底座守护进程"
    ip netns exec "$ns" "$PROJECT_ROOT/wfb_v6_uplink" \
        --role client \
        --tun-name "$tun_name" \
        --tun-addr "$tun_addr" \
        --node-id "$node_id" \
        --link-id "$LINK_ID" \
        --stream "$STREAM_ID" \
        --air-listen-port "$listen_port" \
        --air-target "$air_target_ip:$SERVER_AIR_PORT" >"$logfile" 2>&1 &
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

collect_metrics() {
    CLIENT1_READY_ACCEPTS="$(grep -c '^ready_accept node_id='"$CLIENT1_NODE_ID" "$SERVER_LOG" 2>/dev/null || true)"
    CLIENT2_READY_ACCEPTS="$(grep -c '^ready_accept node_id='"$CLIENT2_NODE_ID" "$SERVER_LOG" 2>/dev/null || true)"
    CLIENT1_GRANTS="$(grep -Ec '^grant seq=.* node_id='"$CLIENT1_NODE_ID"' ' "$SERVER_LOG" 2>/dev/null || true)"
    CLIENT2_GRANTS="$(grep -Ec '^grant seq=.* node_id='"$CLIENT2_NODE_ID"' ' "$SERVER_LOG" 2>/dev/null || true)"
    CLIENT1_AUTHORIZED_SENDS="$(grep 'TOKEN_AUTH' "$CLIENT1_LOG" 2>/dev/null | tail -1 | awk -F'[:	]' '{print $5}' || true)"
    CLIENT2_AUTHORIZED_SENDS="$(grep 'TOKEN_AUTH' "$CLIENT2_LOG" 2>/dev/null | tail -1 | awk -F'[:	]' '{print $5}' || true)"
    CLIENT1_AUTHORIZED_SENDS="${CLIENT1_AUTHORIZED_SENDS:-0}"
    CLIENT2_AUTHORIZED_SENDS="${CLIENT2_AUTHORIZED_SENDS:-0}"
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
    start_client "$CLIENT1_NS" "$CLIENT1_TUN_NAME" "$CLIENT1_TUN_ADDR" "$CLIENT1_NODE_ID" "$CLIENT1_AIR_PORT" "$SERVER_CLIENT1_IP" "$CLIENT1_LOG" CLIENT1_PID
    start_client "$CLIENT2_NS" "$CLIENT2_TUN_NAME" "$CLIENT2_TUN_ADDR" "$CLIENT2_NODE_ID" "$CLIENT2_AIR_PORT" "$SERVER_CLIENT2_IP" "$CLIENT2_LOG" CLIENT2_PID

    wait_for_interface "$SERVER_NS" "$SERVER_TUN_NAME"
    wait_for_interface "$CLIENT1_NS" "$CLIENT1_TUN_NAME"
    wait_for_interface "$CLIENT2_NS" "$CLIENT2_TUN_NAME"

    run_dual_ping
    collect_metrics
    assert_metrics

    log_pass "v6 trusted_plaintext 上行 namespace 验收通过"
}

main "$@"
