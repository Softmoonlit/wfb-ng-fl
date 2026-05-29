#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

LOG_DIR="${LOG_DIR:-$PROJECT_ROOT/tests/logs/v4_namespace_topology_$(date +%Y%m%d_%H%M%S)}"
RESULT_MD="$LOG_DIR/result.md"
BUILD_LOG="$LOG_DIR/build.log"

SERVER_DIR="$LOG_DIR/server"
CLIENT1_DIR="$LOG_DIR/client1"
CLIENT2_DIR="$LOG_DIR/client2"

SERVER_TUN_LOG="$SERVER_DIR/wfb_tun.log"
CLIENT1_TUN_LOG="$CLIENT1_DIR/wfb_tun.log"
CLIENT2_TUN_LOG="$CLIENT2_DIR/wfb_tun.log"
SHARED_DOWNLINK_TX_LOG="$SERVER_DIR/shared_downlink_tx.log"
SHARED_DOWNLINK_FANOUT_LOG="$SERVER_DIR/shared_downlink_fanout.log"
CLIENT1_DOWNLINK_RX_LOG="$CLIENT1_DIR/downlink_rx.log"
CLIENT2_DOWNLINK_RX_LOG="$CLIENT2_DIR/downlink_rx.log"
CLIENT1_DOWNLINK_PROBE_LISTENER_LOG="$CLIENT1_DIR/downlink_probe_listener.log"
CLIENT2_DOWNLINK_PROBE_LISTENER_LOG="$CLIENT2_DIR/downlink_probe_listener.log"
CLIENT1_DOWNLINK_PROBE_FILE="$CLIENT1_DIR/downlink_probe_received.log"
CLIENT2_DOWNLINK_PROBE_FILE="$CLIENT2_DIR/downlink_probe_received.log"

SERVER_NS="${SERVER_NS:-v4-server}"
CLIENT1_NS="${CLIENT1_NS:-v4-client1}"
CLIENT2_NS="${CLIENT2_NS:-v4-client2}"

SERVER_CLIENT1_SERVER_IF="${SERVER_CLIENT1_SERVER_IF:-eth1}"
SERVER_CLIENT2_SERVER_IF="${SERVER_CLIENT2_SERVER_IF:-eth2}"
CLIENT1_MGMT_IF="${CLIENT1_MGMT_IF:-eth0}"
CLIENT2_MGMT_IF="${CLIENT2_MGMT_IF:-eth0}"

SERVER_CLIENT1_ADDR="${SERVER_CLIENT1_ADDR:-172.41.31.1/30}"
CLIENT1_MGMT_ADDR="${CLIENT1_MGMT_ADDR:-172.41.31.2/30}"
SERVER_CLIENT2_ADDR="${SERVER_CLIENT2_ADDR:-172.41.32.1/30}"
CLIENT2_MGMT_ADDR="${CLIENT2_MGMT_ADDR:-172.41.32.2/30}"
SERVER_CLIENT1_IP="${SERVER_CLIENT1_IP:-172.41.31.1}"
CLIENT1_MGMT_IP="${CLIENT1_MGMT_IP:-172.41.31.2}"
SERVER_CLIENT2_IP="${SERVER_CLIENT2_IP:-172.41.32.1}"
CLIENT2_MGMT_IP="${CLIENT2_MGMT_IP:-172.41.32.2}"

SERVER_TUN_NAME="${SERVER_TUN_NAME:-wfb-v4s}"
CLIENT1_TUN_NAME="${CLIENT1_TUN_NAME:-wfb-v4c1}"
CLIENT2_TUN_NAME="${CLIENT2_TUN_NAME:-wfb-v4c2}"
SERVER_TUN_ADDR="${SERVER_TUN_ADDR:-10.44.0.1/24}"
CLIENT1_TUN_ADDR="${CLIENT1_TUN_ADDR:-10.44.0.11/24}"
CLIENT2_TUN_ADDR="${CLIENT2_TUN_ADDR:-10.44.0.12/24}"

SERVER_TUN_LISTEN_PORT="${SERVER_TUN_LISTEN_PORT:-6800}"
SERVER_TUN_PEER_PORT="${SERVER_TUN_PEER_PORT:-6700}"
CLIENT1_TUN_LISTEN_PORT="${CLIENT1_TUN_LISTEN_PORT:-6810}"
CLIENT1_TUN_PEER_PORT="${CLIENT1_TUN_PEER_PORT:-6710}"
CLIENT2_TUN_LISTEN_PORT="${CLIENT2_TUN_LISTEN_PORT:-6820}"
CLIENT2_TUN_PEER_PORT="${CLIENT2_TUN_PEER_PORT:-6720}"

CLIENT1_NODE_ID="${CLIENT1_NODE_ID:-1}"
CLIENT2_NODE_ID="${CLIENT2_NODE_ID:-2}"
LINK_ID="${LINK_ID:-404}"
EPOCH="${EPOCH:-$(date +%s)}"
SHARED_DOWNLINK_TX_DEBUG_PORT="${SHARED_DOWNLINK_TX_DEBUG_PORT:-6900}"
CLIENT1_DOWNLINK_RX_DEBUG_PORT="${CLIENT1_DOWNLINK_RX_DEBUG_PORT:-6911}"
CLIENT2_DOWNLINK_RX_DEBUG_PORT="${CLIENT2_DOWNLINK_RX_DEBUG_PORT:-6912}"
DOWNLINK_PROBE_PORT="${DOWNLINK_PROBE_PORT:-6920}"
DOWNLINK_PROBE_TIMEOUT_SEC="${DOWNLINK_PROBE_TIMEOUT_SEC:-15}"

STARTUP_WAIT_SEC="${STARTUP_WAIT_SEC:-1}"

PIDS=()
FAIL_REASON=""
CLEANUP_VERIFIED="否"

SERVER_TUN_PID=""
CLIENT1_TUN_PID=""
CLIENT2_TUN_PID=""
SHARED_DOWNLINK_TX_PID=""
SHARED_DOWNLINK_FANOUT_PID=""
CLIENT1_DOWNLINK_RX_PID=""
CLIENT2_DOWNLINK_RX_PID=""
CLIENT1_DOWNLINK_PROBE_PID=""
CLIENT2_DOWNLINK_PROBE_PID=""

log_info() { echo "[INFO] $(date '+%H:%M:%S') $1"; }
log_pass() { echo "[PASS] $(date '+%H:%M:%S') $1"; }
log_fail() { echo "[FAIL] $(date '+%H:%M:%S') $1" >&2; }

prepare_log_dir() {
    mkdir -p "$SERVER_DIR" "$CLIENT1_DIR" "$CLIENT2_DIR"
}

set_fail_reason() {
    if [ -z "$FAIL_REASON" ]; then
        FAIL_REASON="$1"
    fi
}

render_result() {
    local status="$1"

    mkdir -p "$LOG_DIR"
    cat > "$RESULT_MD" <<EOF
# 第四版共享下行 namespace 验收结果

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

## Shared Downlink

- shared downlink sender: 1
- shared downlink sender 日志: $SHARED_DOWNLINK_TX_LOG
- client1 downlink receiver 日志: $CLIENT1_DOWNLINK_RX_LOG
- client2 downlink receiver 日志: $CLIENT2_DOWNLINK_RX_LOG
- fan-out 日志: $SHARED_DOWNLINK_FANOUT_LOG
- fan-out 输入: server namespace 127.0.0.1:$SHARED_DOWNLINK_TX_DEBUG_PORT
- fan-out 输出: $CLIENT1_MGMT_IP:$CLIENT1_DOWNLINK_RX_DEBUG_PORT, $CLIENT2_MGMT_IP:$CLIENT2_DOWNLINK_RX_DEBUG_PORT
- client1 probe: $CLIENT1_DOWNLINK_PROBE_FILE
- client2 probe: $CLIENT2_DOWNLINK_PROBE_FILE
- 说明: fan-out 为测试专用，不代表生产传输组件

## 关键日志

- server wfb_tun: $SERVER_TUN_LOG
- client1 wfb_tun: $CLIENT1_TUN_LOG
- client2 wfb_tun: $CLIENT2_TUN_LOG
- shared downlink tx: $SHARED_DOWNLINK_TX_LOG
- shared downlink fan-out: $SHARED_DOWNLINK_FANOUT_LOG
- client1 downlink rx: $CLIENT1_DOWNLINK_RX_LOG
- client2 downlink rx: $CLIENT2_DOWNLINK_RX_LOG
- build: $BUILD_LOG

## 当前 v4 覆盖范围

- 已覆盖: 独立于 v3 的 v4 acceptance 入口
- 已覆盖: server/client1/client2 三 namespace 与点对点管理链路创建
- 已覆盖: 三端 wfb_tun 进程启动、TUN 接口存在校验、TUN 地址校验
- 已覆盖: namespace 管理链路连通性校验
- 已覆盖: shared downlink sender 与测试专用 fan-out
- 已覆盖: client1/client2 各自 downlink receiver 接入各自 TUN/IP 路径
- 已覆盖: client1/client2 通过共享下行路径收到基础探针流量
- 已覆盖: 成功与失败路径的 namespace 和后台进程清理

## 当前 v4 未覆盖范围

- 未覆盖: UFTP 共享下行 payload
- 未覆盖: TCP per-client 上行 update payload
EOF
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

cleanup_resources() {
    cleanup_processes
    delete_namespace_if_exists "$SERVER_NS"
    delete_namespace_if_exists "$CLIENT1_NS"
    delete_namespace_if_exists "$CLIENT2_NS"
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

on_exit() {
    local rc="$1"

    trap - EXIT
    set +e

    cleanup_resources
    if verify_cleanup; then
        CLEANUP_VERIFIED="是"
    else
        CLEANUP_VERIFIED="否"
        set_fail_reason "namespace 清理校验失败"
        rc=1
    fi

    if [ "$rc" -eq 0 ]; then
        render_result "PASS"
        log_pass "第四版 namespace 传输闭环骨架 acceptance 通过"
    else
        if [ -z "$FAIL_REASON" ]; then
            FAIL_REASON="验收失败，退出码: $rc"
        fi
        render_result "FAIL"
        log_fail "$FAIL_REASON"
    fi

    exit "$rc"
}

trap 'on_exit $?' EXIT

require_root() {
    if [ "$EUID" -ne 0 ]; then
        set_fail_reason "必须以 root 运行，network namespace 与 TUN 设备创建需要特权"
        return 1
    fi
}

require_command() {
    local name="$1"
    if ! command -v "$name" >/dev/null 2>&1; then
        set_fail_reason "缺少命令: $name"
        return 1
    fi
}

require_executable() {
    local path="$1"
    if [ ! -x "$path" ]; then
        set_fail_reason "缺少可执行文件: $path"
        return 1
    fi
}

build_acceptance_binaries() {
    local required_binaries=(
        wfb_tun
        wfb_tx
        wfb_rx
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
        echo "acceptance 二进制已存在，跳过构建" > "$BUILD_LOG"
        log_pass "v4 acceptance 二进制已存在"
    else
        log_info "构建 v4 acceptance 所需二进制"
        if ! make wfb_tun wfb_tx wfb_rx wfb_keygen > "$BUILD_LOG" 2>&1; then
            set_fail_reason "构建 v4 acceptance 二进制失败，查看日志: $BUILD_LOG"
            return 1
        fi
        log_pass "v4 acceptance 二进制构建成功"
    fi

    for binary in "${required_binaries[@]}"; do
        require_executable "$PROJECT_ROOT/$binary"
    done

    if [ ! -f "$PROJECT_ROOT/gs.key" ] || [ ! -f "$PROJECT_ROOT/drone.key" ]; then
        log_info "生成 acceptance 所需密钥"
        if ! "$PROJECT_ROOT/wfb_keygen" >> "$BUILD_LOG" 2>&1; then
            set_fail_reason "生成 acceptance 密钥失败，查看日志: $BUILD_LOG"
            return 1
        fi
    fi
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

    ip -n "$left_namespace" addr add "$left_addr" dev "$left_if"
    ip -n "$left_namespace" link set "$left_if" up
    ip -n "$right_namespace" addr add "$right_addr" dev "$right_if"
    ip -n "$right_namespace" link set "$right_if" up
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
        sleep 0.25
    done

    return 1
}

assert_interface_address() {
    local namespace="$1"
    local ifname="$2"
    local addr="$3"

    if ! ip -n "$namespace" -o -4 addr show dev "$ifname" | grep -Fq " $addr "; then
        set_fail_reason "$namespace 的接口 $ifname 未配置地址 $addr"
        return 1
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
        -u "$peer_port" > "$logfile" 2>&1 &
    pid=$!
    PIDS+=("$pid")
    printf -v "$pid_var" '%s' "$pid"

    sleep "$STARTUP_WAIT_SEC"
    if ! kill -0 "$pid" 2>/dev/null; then
        set_fail_reason "$namespace 的 wfb_tun 启动失败，查看日志: $logfile"
        return 1
    fi
}

assert_process_alive() {
    local pid="$1"
    local name="$2"

    if ! kill -0 "$pid" 2>/dev/null; then
        set_fail_reason "$name 已提前退出"
        return 1
    fi
}

wait_for_file_contains() {
    local path="$1"
    local marker="$2"
    local title="$3"
    local tries=60
    local i

    for ((i = 0; i < tries; i++)); do
        if [ -f "$path" ] && grep -Fq "$marker" "$path"; then
            log_pass "$title"
            return 0
        fi
        sleep 0.25
    done

    set_fail_reason "$title 未在文件中出现: $path"
    return 1
}

verify_management_ping() {
    local namespace="$1"
    local target_ip="$2"
    local title="$3"

    log_info "验证 $title"
    if ! ip netns exec "$namespace" ping -c 1 -W 2 "$target_ip" >/dev/null 2>&1; then
        set_fail_reason "$title 失败"
        return 1
    fi
    log_pass "$title 成功"
}

start_downlink_rx_for_client() {
    local namespace="$1"
    local node_id="$2"
    local rx_debug_port="$3"
    local client_tun_listen_port="$4"
    local logfile="$5"
    local pid_var="$6"
    local pid

    log_info "在 $namespace 启动 downlink wfb_rx"
    ip netns exec "$namespace" "$PROJECT_ROOT/wfb_rx" \
        -a "$rx_debug_port" \
        -K "$PROJECT_ROOT/drone.key" \
        -u "$client_tun_listen_port" \
        -N "$node_id" \
        -i "$LINK_ID" \
        -e "$EPOCH" \
        -R 524288 \
        -s 524288 > "$logfile" 2>&1 &
    pid=$!
    PIDS+=("$pid")
    printf -v "$pid_var" '%s' "$pid"

    sleep "$STARTUP_WAIT_SEC"
    if ! kill -0 "$pid" 2>/dev/null; then
        set_fail_reason "$namespace 的 downlink wfb_rx 启动失败，查看日志: $logfile"
        return 1
    fi
}

start_shared_downlink_fanout() {
    local pid

    log_info "在 $SERVER_NS 启动测试专用 shared downlink fan-out"
    ip netns exec "$SERVER_NS" python3 -u - \
        "$SHARED_DOWNLINK_TX_DEBUG_PORT" \
        "$CLIENT1_MGMT_IP" "$CLIENT1_DOWNLINK_RX_DEBUG_PORT" \
        "$CLIENT2_MGMT_IP" "$CLIENT2_DOWNLINK_RX_DEBUG_PORT" <<'PY' > "$SHARED_DOWNLINK_FANOUT_LOG" 2>&1 &
import socket
import sys

listen_port = int(sys.argv[1])
targets = [
    (sys.argv[2], int(sys.argv[3])),
    (sys.argv[4], int(sys.argv[5])),
]

rx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
rx_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
rx_sock.bind(("127.0.0.1", listen_port))

tx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
count = 0

print(f"fanout listening on 127.0.0.1:{listen_port} -> {targets}", flush=True)

while True:
    data, _ = rx_sock.recvfrom(65535)
    count += 1
    for target in targets:
        tx_sock.sendto(data, target)
    print(f"fanout forwarded datagram={count} bytes={len(data)}", flush=True)
PY
    pid=$!
    PIDS+=("$pid")
    SHARED_DOWNLINK_FANOUT_PID="$pid"

    sleep "$STARTUP_WAIT_SEC"
    if ! kill -0 "$pid" 2>/dev/null; then
        set_fail_reason "shared downlink fan-out 启动失败，查看日志: $SHARED_DOWNLINK_FANOUT_LOG"
        return 1
    fi
}

start_shared_downlink_sender() {
    local pid

    log_info "在 $SERVER_NS 启动 shared downlink wfb_tx"
    ip netns exec "$SERVER_NS" "$PROJECT_ROOT/wfb_tx" \
        -K "$PROJECT_ROOT/gs.key" \
        -u "$SERVER_TUN_PEER_PORT" \
        -D "$SHARED_DOWNLINK_TX_DEBUG_PORT" \
        -i "$LINK_ID" \
        -e "$EPOCH" \
        -R 524288 \
        -s 524288 \
        shared-downlink > "$SHARED_DOWNLINK_TX_LOG" 2>&1 &
    pid=$!
    PIDS+=("$pid")
    SHARED_DOWNLINK_TX_PID="$pid"

    sleep "$STARTUP_WAIT_SEC"
    if ! kill -0 "$pid" 2>/dev/null; then
        set_fail_reason "shared downlink sender 启动失败，查看日志: $SHARED_DOWNLINK_TX_LOG"
        return 1
    fi
}

start_tun_probe_listener() {
    local namespace="$1"
    local bind_ip="$2"
    local output_file="$3"
    local logfile="$4"
    local pid_var="$5"
    local pid

    rm -f "$output_file"

    log_info "在 $namespace 启动 TUN/IP 下行探针接收器"
    ip netns exec "$namespace" python3 -u - \
        "$bind_ip" "$DOWNLINK_PROBE_PORT" "$output_file" "$DOWNLINK_PROBE_TIMEOUT_SEC" <<'PY' > "$logfile" 2>&1 &
import socket
import sys

bind_ip = sys.argv[1]
port = int(sys.argv[2])
output_file = sys.argv[3]
timeout = float(sys.argv[4])

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((bind_ip, port))
sock.settimeout(timeout)

data, addr = sock.recvfrom(65535)
payload = data.decode("utf-8", errors="replace")

with open(output_file, "w", encoding="utf-8") as fh:
    fh.write(payload)

print(f"received from {addr[0]}:{addr[1]} payload={payload}", flush=True)
PY
    pid=$!
    PIDS+=("$pid")
    printf -v "$pid_var" '%s' "$pid"

    sleep 0.25
    if ! kill -0 "$pid" 2>/dev/null; then
        set_fail_reason "$namespace 的 TUN/IP 下行探针接收器启动失败，查看日志: $logfile"
        return 1
    fi
}

send_shared_downlink_probes() {
    log_info "从 $SERVER_NS 发送 shared downlink 基础探针流量"
    ip netns exec "$SERVER_NS" python3 -u - \
        "$CLIENT1_TUN_ADDR" "$CLIENT2_TUN_ADDR" "$DOWNLINK_PROBE_PORT" <<'PY'
import ipaddress
import socket
import sys

client1_ip = str(ipaddress.ip_interface(sys.argv[1]).ip)
client2_ip = str(ipaddress.ip_interface(sys.argv[2]).ip)
port = int(sys.argv[3])

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.sendto(b"V4_SHARED_DOWNLINK_CLIENT1", (client1_ip, port))
sock.sendto(b"V4_SHARED_DOWNLINK_CLIENT2", (client2_ip, port))
PY
}

main() {
    prepare_log_dir
    require_root
    require_command ip
    require_command ping
    require_command grep
    require_command make
    require_command python3

    build_acceptance_binaries

    log_info "清理旧的 v4 namespace 骨架资源"
    cleanup_resources

    log_info "创建三 namespace 与点对点管理链路"
    create_namespace "$SERVER_NS"
    create_namespace "$CLIENT1_NS"
    create_namespace "$CLIENT2_NS"

    attach_namespaces_with_veth \
        "$SERVER_NS" "v4s1p0" "$SERVER_CLIENT1_SERVER_IF" "$SERVER_CLIENT1_ADDR" \
        "$CLIENT1_NS" "v4c1p0" "$CLIENT1_MGMT_IF" "$CLIENT1_MGMT_ADDR"
    attach_namespaces_with_veth \
        "$SERVER_NS" "v4s2p0" "$SERVER_CLIENT2_SERVER_IF" "$SERVER_CLIENT2_ADDR" \
        "$CLIENT2_NS" "v4c2p0" "$CLIENT2_MGMT_IF" "$CLIENT2_MGMT_ADDR"
    assert_interface_address "$SERVER_NS" "$SERVER_CLIENT1_SERVER_IF" "$SERVER_CLIENT1_ADDR"
    assert_interface_address "$CLIENT1_NS" "$CLIENT1_MGMT_IF" "$CLIENT1_MGMT_ADDR"
    assert_interface_address "$SERVER_NS" "$SERVER_CLIENT2_SERVER_IF" "$SERVER_CLIENT2_ADDR"
    assert_interface_address "$CLIENT2_NS" "$CLIENT2_MGMT_IF" "$CLIENT2_MGMT_ADDR"
    log_pass "三 namespace 点对点管理链路创建完成"

    start_wfb_tun "$SERVER_NS" "$SERVER_TUN_NAME" "$SERVER_TUN_ADDR" "$SERVER_TUN_LISTEN_PORT" "$SERVER_TUN_PEER_PORT" "$SERVER_TUN_LOG" SERVER_TUN_PID
    start_wfb_tun "$CLIENT1_NS" "$CLIENT1_TUN_NAME" "$CLIENT1_TUN_ADDR" "$CLIENT1_TUN_LISTEN_PORT" "$CLIENT1_TUN_PEER_PORT" "$CLIENT1_TUN_LOG" CLIENT1_TUN_PID
    start_wfb_tun "$CLIENT2_NS" "$CLIENT2_TUN_NAME" "$CLIENT2_TUN_ADDR" "$CLIENT2_TUN_LISTEN_PORT" "$CLIENT2_TUN_PEER_PORT" "$CLIENT2_TUN_LOG" CLIENT2_TUN_PID

    if ! wait_for_interface "$SERVER_NS" "$SERVER_TUN_NAME"; then
        set_fail_reason "server TUN 未按预期创建"
        return 1
    fi
    if ! wait_for_interface "$CLIENT1_NS" "$CLIENT1_TUN_NAME"; then
        set_fail_reason "client1 TUN 未按预期创建"
        return 1
    fi
    if ! wait_for_interface "$CLIENT2_NS" "$CLIENT2_TUN_NAME"; then
        set_fail_reason "client2 TUN 未按预期创建"
        return 1
    fi

    assert_interface_address "$SERVER_NS" "$SERVER_TUN_NAME" "$SERVER_TUN_ADDR"
    assert_interface_address "$CLIENT1_NS" "$CLIENT1_TUN_NAME" "$CLIENT1_TUN_ADDR"
    assert_interface_address "$CLIENT2_NS" "$CLIENT2_TUN_NAME" "$CLIENT2_TUN_ADDR"
    log_pass "三 namespace TUN/IP 接口创建完成"

    assert_process_alive "$SERVER_TUN_PID" "server wfb_tun"
    assert_process_alive "$CLIENT1_TUN_PID" "client1 wfb_tun"
    assert_process_alive "$CLIENT2_TUN_PID" "client2 wfb_tun"

    start_downlink_rx_for_client "$CLIENT1_NS" "$CLIENT1_NODE_ID" "$CLIENT1_DOWNLINK_RX_DEBUG_PORT" "$CLIENT1_TUN_LISTEN_PORT" "$CLIENT1_DOWNLINK_RX_LOG" CLIENT1_DOWNLINK_RX_PID
    start_downlink_rx_for_client "$CLIENT2_NS" "$CLIENT2_NODE_ID" "$CLIENT2_DOWNLINK_RX_DEBUG_PORT" "$CLIENT2_TUN_LISTEN_PORT" "$CLIENT2_DOWNLINK_RX_LOG" CLIENT2_DOWNLINK_RX_PID
    start_shared_downlink_fanout
    start_shared_downlink_sender

    assert_process_alive "$CLIENT1_DOWNLINK_RX_PID" "client1 downlink wfb_rx"
    assert_process_alive "$CLIENT2_DOWNLINK_RX_PID" "client2 downlink wfb_rx"
    assert_process_alive "$SHARED_DOWNLINK_FANOUT_PID" "shared downlink fan-out"
    assert_process_alive "$SHARED_DOWNLINK_TX_PID" "shared downlink sender"

    verify_management_ping "$CLIENT1_NS" "$SERVER_CLIENT1_IP" "client1 到 server 点对点链路连通性"
    verify_management_ping "$SERVER_NS" "$CLIENT1_MGMT_IP" "server 到 client1 点对点链路连通性"
    verify_management_ping "$CLIENT2_NS" "$SERVER_CLIENT2_IP" "client2 到 server 点对点链路连通性"
    verify_management_ping "$SERVER_NS" "$CLIENT2_MGMT_IP" "server 到 client2 点对点链路连通性"

    start_tun_probe_listener "$CLIENT1_NS" "${CLIENT1_TUN_ADDR%%/*}" "$CLIENT1_DOWNLINK_PROBE_FILE" "$CLIENT1_DOWNLINK_PROBE_LISTENER_LOG" CLIENT1_DOWNLINK_PROBE_PID
    start_tun_probe_listener "$CLIENT2_NS" "${CLIENT2_TUN_ADDR%%/*}" "$CLIENT2_DOWNLINK_PROBE_FILE" "$CLIENT2_DOWNLINK_PROBE_LISTENER_LOG" CLIENT2_DOWNLINK_PROBE_PID
    send_shared_downlink_probes
    wait_for_file_contains "$CLIENT1_DOWNLINK_PROBE_FILE" "V4_SHARED_DOWNLINK_CLIENT1" "client1 通过共享下行路径收到基础探针"
    wait_for_file_contains "$CLIENT2_DOWNLINK_PROBE_FILE" "V4_SHARED_DOWNLINK_CLIENT2" "client2 通过共享下行路径收到基础探针"
}

main "$@"
