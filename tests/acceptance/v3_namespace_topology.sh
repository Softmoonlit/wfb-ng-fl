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

SERVER_TUN_LISTEN_PORT="${SERVER_TUN_LISTEN_PORT:-5800}"
SERVER_TUN_PEER_PORT="${SERVER_TUN_PEER_PORT:-5700}"
CLIENT1_TUN_LISTEN_PORT="${CLIENT1_TUN_LISTEN_PORT:-5800}"
CLIENT1_TUN_PEER_PORT="${CLIENT1_TUN_PEER_PORT:-5700}"
CLIENT2_TUN_LISTEN_PORT="${CLIENT2_TUN_LISTEN_PORT:-5800}"
CLIENT2_TUN_PEER_PORT="${CLIENT2_TUN_PEER_PORT:-5700}"

TOKEN_READY_BASE="${TOKEN_READY_BASE:-wfb-scheduler}"
SERVER_TOKEN_GRANT_BASE="${SERVER_TOKEN_GRANT_BASE:-wfb-v3-bridge-grant}"
CLIENT1_TOKEN_GRANT_BASE="${CLIENT1_TOKEN_GRANT_BASE:-wfb-v3-client1-grant}"
CLIENT2_TOKEN_GRANT_BASE="${CLIENT2_TOKEN_GRANT_BASE:-wfb-v3-client2-grant}"

STARTUP_WAIT_SEC="${STARTUP_WAIT_SEC:-1}"

PIDS=()
FAIL_REASON=""
CLEANUP_VERIFIED="否"

SERVER_TUN_PID=""
CLIENT1_TUN_PID=""
CLIENT2_TUN_PID=""
TOKEN_BRIDGE_PID=""

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
# 第三版三 namespace 拓扑验收结果

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

## 控制桥

- server ready 入口: $TOKEN_READY_BASE
- server grant 入口: $SERVER_TOKEN_GRANT_BASE
- node 1 -> client grant 入口: $CLIENT1_NS / $CLIENT1_TOKEN_GRANT_BASE
- node 2 -> client grant 入口: $CLIENT2_NS / $CLIENT2_TOKEN_GRANT_BASE

## 当前切片范围

- 已拉起并验证三 namespace 基础管理链路
- 已在三 namespace 内启动 \`wfb_tun\` 并创建 TUN/IP 接口
- 已启动并停止测试专用跨 namespace 控制桥
- 当前不要求完成跨 namespace 的真实数据路径闭环
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
    if [ -x "$PROJECT_ROOT/wfb_tun" ] && [ -x "$PROJECT_ROOT/wfb_token_namespace_bridge" ]; then
        echo "acceptance 二进制已存在，跳过构建" >"$BUILD_LOG"
        log_pass "acceptance 二进制已存在"
        return
    fi

    require_command make
    log_info "构建 v3 acceptance 所需二进制"
    if ! make wfb_tun wfb_token_namespace_bridge >"$BUILD_LOG" 2>&1; then
        fail_exit "构建 v3 acceptance 二进制失败，查看日志: $BUILD_LOG"
    fi
    require_executable "$PROJECT_ROOT/wfb_tun"
    require_executable "$PROJECT_ROOT/wfb_token_namespace_bridge"
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
        -n "1:$CLIENT1_NS:$CLIENT1_TOKEN_GRANT_BASE" \
        -n "2:$CLIENT2_NS:$CLIENT2_TOKEN_GRANT_BASE" >"$TOKEN_BRIDGE_LOG" 2>&1 &
    pid=$!
    PIDS+=("$pid")
    TOKEN_BRIDGE_PID="$pid"

    sleep "$STARTUP_WAIT_SEC"
    if ! kill -0 "$pid" 2>/dev/null; then
        fail_exit "测试专用控制桥启动失败，查看日志: $TOKEN_BRIDGE_LOG"
    fi
    log_pass "测试专用跨 namespace 控制桥已启动"
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

    start_wfb_tun "$SERVER_NS" "$SERVER_TUN_NAME" "$SERVER_TUN_ADDR" "$SERVER_TUN_LISTEN_PORT" "$SERVER_TUN_PEER_PORT" "$SERVER_TUN_LOG" SERVER_TUN_PID
    start_wfb_tun "$CLIENT1_NS" "$CLIENT1_TUN_NAME" "$CLIENT1_TUN_ADDR" "$CLIENT1_TUN_LISTEN_PORT" "$CLIENT1_TUN_PEER_PORT" "$CLIENT1_TUN_LOG" CLIENT1_TUN_PID
    start_wfb_tun "$CLIENT2_NS" "$CLIENT2_TUN_NAME" "$CLIENT2_TUN_ADDR" "$CLIENT2_TUN_LISTEN_PORT" "$CLIENT2_TUN_PEER_PORT" "$CLIENT2_TUN_LOG" CLIENT2_TUN_PID

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

    verify_management_ping "$CLIENT1_NS" "$SERVER_CLIENT1_IP" "client1 到 server 点对点链路连通性"
    verify_management_ping "$CLIENT2_NS" "$SERVER_CLIENT2_IP" "client2 到 server 点对点链路连通性"

    cleanup
    if ! verify_cleanup; then
        fail_exit "namespace 清理校验失败"
    fi
    CLEANUP_VERIFIED="是"

    render_result "PASS"
    log_pass "第三版三 namespace acceptance 骨架通过"
}

main "$@"
