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
CLIENT1_UPLINK_RELAY_LOG="$CLIENT1_DIR/uftp_uplink_relay.log"
CLIENT2_UPLINK_RELAY_LOG="$CLIENT2_DIR/uftp_uplink_relay.log"
CLIENT1_UFTP_RESPONSE_RELAY_LOG="$CLIENT1_DIR/uftp_response_relay.log"
CLIENT2_UFTP_RESPONSE_RELAY_LOG="$CLIENT2_DIR/uftp_response_relay.log"
CLIENT1_DOWNLINK_PROBE_LISTENER_LOG="$CLIENT1_DIR/downlink_probe_listener.log"
CLIENT2_DOWNLINK_PROBE_LISTENER_LOG="$CLIENT2_DIR/downlink_probe_listener.log"
CLIENT1_DOWNLINK_PROBE_FILE="$CLIENT1_DIR/downlink_probe_received.log"
CLIENT2_DOWNLINK_PROBE_FILE="$CLIENT2_DIR/downlink_probe_received.log"
UFTP_PAYLOAD_FILE="$SERVER_DIR/uftp_payload.bin"
UFTP_SERVER_LOG="$SERVER_DIR/uftp.log"
UFTP_SERVER_STATUS="$SERVER_DIR/uftp.status"
CLIENT1_UFTPD_LOG="$CLIENT1_DIR/uftpd.log"
CLIENT2_UFTPD_LOG="$CLIENT2_DIR/uftpd.log"
CLIENT1_UFTPD_PIDFILE="$CLIENT1_DIR/uftpd.pid"
CLIENT2_UFTPD_PIDFILE="$CLIENT2_DIR/uftpd.pid"
CLIENT1_UFTPD_STATUS="$CLIENT1_DIR/uftpd.status"
CLIENT2_UFTPD_STATUS="$CLIENT2_DIR/uftpd.status"
CLIENT1_UFTP_DEST_DIR="$CLIENT1_DIR/uftp_dest"
CLIENT2_UFTP_DEST_DIR="$CLIENT2_DIR/uftp_dest"
CLIENT1_UFTP_TEMP_DIR="$CLIENT1_DIR/uftp_tmp"
CLIENT2_UFTP_TEMP_DIR="$CLIENT2_DIR/uftp_tmp"
CLIENT1_UFTP_PAYLOAD_FILE="$CLIENT1_UFTP_DEST_DIR/uftp_payload.bin"
CLIENT2_UFTP_PAYLOAD_FILE="$CLIENT2_UFTP_DEST_DIR/uftp_payload.bin"
CLIENT1_TCP_UPDATE_FILE="$CLIENT1_DIR/tcp_update.bin"
CLIENT2_TCP_UPDATE_FILE="$CLIENT2_DIR/tcp_update.bin"
SERVER_CLIENT1_TCP_UPDATE_FILE="$SERVER_DIR/client1_tcp_update.bin"
SERVER_CLIENT2_TCP_UPDATE_FILE="$SERVER_DIR/client2_tcp_update.bin"
CLIENT1_TCP_UPDATE_RECEIVER_LOG="$SERVER_DIR/client1_tcp_update_receiver.log"
CLIENT2_TCP_UPDATE_RECEIVER_LOG="$SERVER_DIR/client2_tcp_update_receiver.log"
CLIENT1_TCP_UPDATE_RECEIVER_STATUS="$SERVER_DIR/client1_tcp_update_receiver.status"
CLIENT2_TCP_UPDATE_RECEIVER_STATUS="$SERVER_DIR/client2_tcp_update_receiver.status"
CLIENT1_TCP_UPDATE_CLIENT_LOG="$CLIENT1_DIR/tcp_update_client.log"
CLIENT2_TCP_UPDATE_CLIENT_LOG="$CLIENT2_DIR/tcp_update_client.log"
CLIENT1_TCP_UPDATE_CLIENT_STATUS="$CLIENT1_DIR/tcp_update_client.status"
CLIENT2_TCP_UPDATE_CLIENT_STATUS="$CLIENT2_DIR/tcp_update_client.status"
TCP_UPDATE_CLIENTS_GATE="$LOG_DIR/tcp_update_clients.go"

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
UFTP_PUBLIC_MULTICAST_ADDR="${UFTP_PUBLIC_MULTICAST_ADDR:-230.4.4.1}"
UFTP_PRIVATE_MULTICAST_ADDR="${UFTP_PRIVATE_MULTICAST_ADDR:-230.5.5.8}"
UFTP_PORT="${UFTP_PORT:-1044}"
UFTP_SOURCE_PORT="${UFTP_SOURCE_PORT:-1045}"
UFTP_RESPONSE_PROXY_PORT="${UFTP_RESPONSE_PROXY_PORT:-5602}"
UFTP_PAYLOAD_SIZE="${UFTP_PAYLOAD_SIZE:-1048576}"
UFTP_TRANSFER_TIMEOUT_SEC="${UFTP_TRANSFER_TIMEOUT_SEC:-40}"
UFTP_SOURCE_SHA256="未生成"
CLIENT1_UFTP_SHA256="未收到"
CLIENT2_UFTP_SHA256="未收到"
TCP_CLIENT1_PORT="${TCP_CLIENT1_PORT:-15001}"
TCP_CLIENT2_PORT="${TCP_CLIENT2_PORT:-15002}"
TCP_UPDATE_PAYLOAD_SIZE="${TCP_UPDATE_PAYLOAD_SIZE:-262144}"
TCP_UPDATE_TIMEOUT_SEC="${TCP_UPDATE_TIMEOUT_SEC:-30}"
CLIENT1_TCP_UPDATE_SOURCE_SHA256="未生成"
CLIENT2_TCP_UPDATE_SOURCE_SHA256="未生成"
CLIENT1_TCP_UPDATE_RECEIVED_SHA256="未收到"
CLIENT2_TCP_UPDATE_RECEIVED_SHA256="未收到"

RESOLVED_UFTP_BIN=""
RESOLVED_UFTPD_BIN=""

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
CLIENT1_UPLINK_RELAY_PID=""
CLIENT2_UPLINK_RELAY_PID=""
CLIENT1_UFTP_RESPONSE_RELAY_PID=""
CLIENT2_UFTP_RESPONSE_RELAY_PID=""
CLIENT1_DOWNLINK_PROBE_PID=""
CLIENT2_DOWNLINK_PROBE_PID=""
CLIENT1_TCP_UPDATE_RECEIVER_PID=""
CLIENT2_TCP_UPDATE_RECEIVER_PID=""
CLIENT1_TCP_UPDATE_CLIENT_PID=""
CLIENT2_TCP_UPDATE_CLIENT_PID=""

log_info() { echo "[INFO] $(date '+%H:%M:%S') $1"; }
log_pass() { echo "[PASS] $(date '+%H:%M:%S') $1"; }
log_fail() { echo "[FAIL] $(date '+%H:%M:%S') $1" >&2; }

prepare_log_dir() {
    mkdir -p \
        "$SERVER_DIR" \
        "$CLIENT1_DIR" \
        "$CLIENT2_DIR" \
        "$CLIENT1_UFTP_DEST_DIR" \
        "$CLIENT2_UFTP_DEST_DIR" \
        "$CLIENT1_UFTP_TEMP_DIR" \
        "$CLIENT2_UFTP_TEMP_DIR"
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
- UFTP 回程 relay: client TUN UDP -> server wfb_tun UDP，仅用于 UFTP control responses
- client1 probe: $CLIENT1_DOWNLINK_PROBE_FILE
- client2 probe: $CLIENT2_DOWNLINK_PROBE_FILE
- 说明: fan-out 为测试专用，不代表生产传输组件

## UFTP 共享下行 Payload

- UFTP_BIN: ${RESOLVED_UFTP_BIN:-未解析}
- UFTPD_BIN: ${RESOLVED_UFTPD_BIN:-未解析}
- UFTP public multicast: $UFTP_PUBLIC_MULTICAST_ADDR/32 via TUN
- UFTP private multicast: $UFTP_PRIVATE_MULTICAST_ADDR/32 via TUN
- UFTP port: $UFTP_PORT
- UFTP source port: $UFTP_SOURCE_PORT
- UFTP response proxy port: $UFTP_RESPONSE_PROXY_PORT
- UFTP payload size: $UFTP_PAYLOAD_SIZE bytes
- UFTP source payload: $UFTP_PAYLOAD_FILE
- UFTP source sha256: $UFTP_SOURCE_SHA256
- client1 UFTP payload: $CLIENT1_UFTP_PAYLOAD_FILE
- client1 UFTP sha256: $CLIENT1_UFTP_SHA256
- client2 UFTP payload: $CLIENT2_UFTP_PAYLOAD_FILE
- client2 UFTP sha256: $CLIENT2_UFTP_SHA256
- server UFTP 日志: $UFTP_SERVER_LOG
- server UFTP 状态: $UFTP_SERVER_STATUS
- client1 UFTPD 日志: $CLIENT1_UFTPD_LOG
- client2 UFTPD 日志: $CLIENT2_UFTPD_LOG

## TCP Per-Client 上行 Update Payload

- TCP update payload size: $TCP_UPDATE_PAYLOAD_SIZE bytes
- client1 TCP port: $TCP_CLIENT1_PORT
- client2 TCP port: $TCP_CLIENT2_PORT
- client1 TCP update source: $CLIENT1_TCP_UPDATE_FILE
- client1 TCP update source sha256: $CLIENT1_TCP_UPDATE_SOURCE_SHA256
- client1 TCP update received: $SERVER_CLIENT1_TCP_UPDATE_FILE
- client1 TCP update received sha256: $CLIENT1_TCP_UPDATE_RECEIVED_SHA256
- client2 TCP update source: $CLIENT2_TCP_UPDATE_FILE
- client2 TCP update source sha256: $CLIENT2_TCP_UPDATE_SOURCE_SHA256
- client2 TCP update received: $SERVER_CLIENT2_TCP_UPDATE_FILE
- client2 TCP update received sha256: $CLIENT2_TCP_UPDATE_RECEIVED_SHA256
- client1 TCP receiver 日志: $CLIENT1_TCP_UPDATE_RECEIVER_LOG
- client2 TCP receiver 日志: $CLIENT2_TCP_UPDATE_RECEIVER_LOG
- client1 TCP upload 日志: $CLIENT1_TCP_UPDATE_CLIENT_LOG
- client2 TCP upload 日志: $CLIENT2_TCP_UPDATE_CLIENT_LOG

## 关键日志

- server wfb_tun: $SERVER_TUN_LOG
- client1 wfb_tun: $CLIENT1_TUN_LOG
- client2 wfb_tun: $CLIENT2_TUN_LOG
- shared downlink tx: $SHARED_DOWNLINK_TX_LOG
- shared downlink fan-out: $SHARED_DOWNLINK_FANOUT_LOG
- client1 downlink rx: $CLIENT1_DOWNLINK_RX_LOG
- client2 downlink rx: $CLIENT2_DOWNLINK_RX_LOG
- client1 UFTP uplink relay: $CLIENT1_UPLINK_RELAY_LOG
- client2 UFTP uplink relay: $CLIENT2_UPLINK_RELAY_LOG
- client1 UFTP response relay: $CLIENT1_UFTP_RESPONSE_RELAY_LOG
- client2 UFTP response relay: $CLIENT2_UFTP_RESPONSE_RELAY_LOG
- server UFTP: $UFTP_SERVER_LOG
- client1 UFTPD: $CLIENT1_UFTPD_LOG
- client2 UFTPD: $CLIENT2_UFTPD_LOG
- client1 TCP update receiver: $CLIENT1_TCP_UPDATE_RECEIVER_LOG
- client2 TCP update receiver: $CLIENT2_TCP_UPDATE_RECEIVER_LOG
- client1 TCP update client: $CLIENT1_TCP_UPDATE_CLIENT_LOG
- client2 TCP update client: $CLIENT2_TCP_UPDATE_CLIENT_LOG
- build: $BUILD_LOG

## 当前 v4 覆盖范围

- 已覆盖: 独立于 v3 的 v4 acceptance 入口
- 已覆盖: server/client1/client2 三 namespace 与点对点管理链路创建
- 已覆盖: 三端 wfb_tun 进程启动、TUN 接口存在校验、TUN 地址校验
- 已覆盖: namespace 管理链路连通性校验
- 已覆盖: shared downlink sender 与测试专用 fan-out
- 已覆盖: client1/client2 各自 downlink receiver 接入各自 TUN/IP 路径
- 已覆盖: client1/client2 通过共享下行路径收到基础探针流量
- 已覆盖: UFTP 共享下行 payload
- 已覆盖: UFTP public/private multicast /32 路由显式指向 TUN 接口
- 已覆盖: client1/client2 UFTP payload sha256 完成屏障
- 已覆盖: TCP per-client 上行 update payload
- 已覆盖: server 同时监听两个独立 TCP 端口，client1/client2 并发上传 update payload
- 已覆盖: server 端 TCP update payload sha256 校验
- 已覆盖: 成功与失败路径的 namespace 和后台进程清理

## 当前 v4 未覆盖范围

- 未覆盖: ready 标记、真实训练 update、上行注册协议
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
    if [ -f "$CLIENT1_UFTPD_PIDFILE" ]; then
        kill "$(cat "$CLIENT1_UFTPD_PIDFILE")" 2>/dev/null || true
    fi
    if [ -f "$CLIENT2_UFTPD_PIDFILE" ]; then
        kill "$(cat "$CLIENT2_UFTPD_PIDFILE")" 2>/dev/null || true
    fi
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

require_uftp_binary() {
    local env_name="$1"
    local binary_name="$2"
    local resolved

    if [ -n "${!env_name:-}" ]; then
        if [ ! -x "${!env_name}" ]; then
            set_fail_reason "缺少 UFTP 可执行文件: ${!env_name}。请从 SourceForge 下载 UFTP 源码后手动编译 uftp/uftpd，并通过 UFTP_BIN 和 UFTPD_BIN 指定路径。"
            return 1
        fi
        resolved="${!env_name}"
    else
        if ! resolved="$(command -v "$binary_name" 2>/dev/null)"; then
            set_fail_reason "缺少 UFTP 可执行文件: $binary_name。请从 SourceForge 下载 UFTP 源码后手动编译 uftp/uftpd，并通过 UFTP_BIN 和 UFTPD_BIN 指定路径，或将二进制加入 PATH。"
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
    require_uftp_binary UFTP_BIN uftp
    require_uftp_binary UFTPD_BIN uftpd
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

start_uftp_uplink_relay_for_client() {
    local namespace="$1"
    local listen_port="$2"
    local server_ip="$3"
    local logfile="$4"
    local pid_var="$5"
    local pid

    log_info "在 $namespace 启动测试专用 UFTP 回程 relay"
    ip netns exec "$namespace" python3 -u - \
        "$listen_port" "$server_ip" "$SERVER_TUN_LISTEN_PORT" <<'PY' > "$logfile" 2>&1 &
import socket
import sys

listen_port = int(sys.argv[1])
server_ip = sys.argv[2]
server_port = int(sys.argv[3])

rx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
rx_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
rx_sock.bind(("127.0.0.1", listen_port))

tx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
count = 0

print(f"uplink relay listening on 127.0.0.1:{listen_port} -> {server_ip}:{server_port}", flush=True)

while True:
    data, _ = rx_sock.recvfrom(65535)
    count += 1
    tx_sock.sendto(data, (server_ip, server_port))
    print(f"uplink relay forwarded datagram={count} bytes={len(data)}", flush=True)
PY
    pid=$!
    PIDS+=("$pid")
    printf -v "$pid_var" '%s' "$pid"

    sleep "$STARTUP_WAIT_SEC"
    if ! kill -0 "$pid" 2>/dev/null; then
        set_fail_reason "$namespace 的 UFTP 回程 relay 启动失败，查看日志: $logfile"
        return 1
    fi
}

start_uftp_response_relay_for_client() {
    local namespace="$1"
    local server_tun_ip="$2"
    local logfile="$3"
    local pid_var="$4"
    local pid

    log_info "在 $namespace 启动测试专用 UFTP response relay"
    ip netns exec "$namespace" python3 -u - \
        "$UFTP_RESPONSE_PROXY_PORT" "$server_tun_ip" "$UFTP_SOURCE_PORT" <<'PY' > "$logfile" 2>&1 &
import socket
import sys

listen_port = int(sys.argv[1])
server_tun_ip = sys.argv[2]
server_port = int(sys.argv[3])

rx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
rx_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
rx_sock.bind(("127.0.0.1", listen_port))

tx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
count = 0

print(f"response relay listening on 127.0.0.1:{listen_port} -> {server_tun_ip}:{server_port}", flush=True)

while True:
    data, addr = rx_sock.recvfrom(65535)
    count += 1
    tx_sock.sendto(data, (server_tun_ip, server_port))
    print(f"response relay forwarded datagram={count} bytes={len(data)} from={addr[0]}:{addr[1]}", flush=True)
PY
    pid=$!
    PIDS+=("$pid")
    printf -v "$pid_var" '%s' "$pid"

    sleep "$STARTUP_WAIT_SEC"
    if ! kill -0 "$pid" 2>/dev/null; then
        set_fail_reason "$namespace 的 UFTP response relay 启动失败，查看日志: $logfile"
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

add_uftp_multicast_routes() {
    log_info "显式添加 UFTP multicast /32 路由到各 namespace TUN 接口"
    ip -n "$SERVER_NS" route replace "$UFTP_PUBLIC_MULTICAST_ADDR/32" dev "$SERVER_TUN_NAME"
    ip -n "$SERVER_NS" route replace "$UFTP_PRIVATE_MULTICAST_ADDR/32" dev "$SERVER_TUN_NAME"
    ip -n "$CLIENT1_NS" route replace "$UFTP_PUBLIC_MULTICAST_ADDR/32" dev "$CLIENT1_TUN_NAME"
    ip -n "$CLIENT1_NS" route replace "$UFTP_PRIVATE_MULTICAST_ADDR/32" dev "$CLIENT1_TUN_NAME"
    ip -n "$CLIENT2_NS" route replace "$UFTP_PUBLIC_MULTICAST_ADDR/32" dev "$CLIENT2_TUN_NAME"
    ip -n "$CLIENT2_NS" route replace "$UFTP_PRIVATE_MULTICAST_ADDR/32" dev "$CLIENT2_TUN_NAME"
}

generate_uftp_payload() {
    log_info "生成 UFTP 下行测试 payload: $UFTP_PAYLOAD_SIZE bytes"
    python3 -u - "$UFTP_PAYLOAD_FILE" "$UFTP_PAYLOAD_SIZE" <<'PY'
import os
import sys

path = sys.argv[1]
size = int(sys.argv[2])
pattern = b"wfb-ng-v4-uftp-payload\n"

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
    local namespace="$1"
    local bind_ip="$2"
    local dest_dir="$3"
    local temp_dir="$4"
    local logfile="$5"
    local status_file="$6"
    local pidfile="$7"

    rm -f "$pidfile" "$status_file"
    log_info "在 $namespace 启动 UFTPD client"
    ip netns exec "$namespace" "$RESOLVED_UFTPD_BIN" \
        -I "$bind_ip" \
        -M "$UFTP_PUBLIC_MULTICAST_ADDR" \
        -p "$UFTP_PORT" \
        -D "$dest_dir" \
        -T "$temp_dir" \
        -L "$logfile" \
        -F "$status_file" \
        -P "$pidfile"

    if ! wait_for_file "$pidfile" "$namespace UFTPD pidfile 创建完成" 20; then
        return 1
    fi

    if ! kill -0 "$(cat "$pidfile")" 2>/dev/null; then
        set_fail_reason "$namespace 的 UFTPD 启动失败，查看日志: $logfile"
        return 1
    fi
}

run_uftp_server() {
    log_info "在 $SERVER_NS 通过 TUN 接口发送 UFTP payload"
    if ! timeout "${UFTP_TRANSFER_TIMEOUT_SEC}s" ip netns exec "$SERVER_NS" "$RESOLVED_UFTP_BIN" \
        -I "${SERVER_TUN_ADDR%%/*}" \
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
        set_fail_reason "UFTP server 发送失败，查看日志: $UFTP_SERVER_LOG"
        return 1
    fi
}

verify_uftp_payloads() {
    wait_for_file "$CLIENT1_UFTP_PAYLOAD_FILE" "client1 收到 UFTP payload" "$((UFTP_TRANSFER_TIMEOUT_SEC * 4))"
    wait_for_file "$CLIENT2_UFTP_PAYLOAD_FILE" "client2 收到 UFTP payload" "$((UFTP_TRANSFER_TIMEOUT_SEC * 4))"

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

    log_pass "client1/client2 UFTP payload sha256 均匹配 server 源文件"
}

generate_tcp_update_payload() {
    local path="$1"
    local label="$2"

    log_info "生成 $label TCP update payload: $TCP_UPDATE_PAYLOAD_SIZE bytes"
    python3 -u - "$path" "$TCP_UPDATE_PAYLOAD_SIZE" "$label" <<'PY'
import sys

path = sys.argv[1]
size = int(sys.argv[2])
label = sys.argv[3].encode("utf-8")
pattern = b"wfb-ng-v4-tcp-update-" + label + b"\n"

with open(path, "wb") as fh:
    remaining = size
    while remaining > 0:
        chunk = pattern[:remaining] if remaining < len(pattern) else pattern
        fh.write(chunk)
        remaining -= len(chunk)
PY
}

generate_tcp_update_payloads() {
    generate_tcp_update_payload "$CLIENT1_TCP_UPDATE_FILE" client1
    generate_tcp_update_payload "$CLIENT2_TCP_UPDATE_FILE" client2
    CLIENT1_TCP_UPDATE_SOURCE_SHA256="$(sha256_of_file "$CLIENT1_TCP_UPDATE_FILE")"
    CLIENT2_TCP_UPDATE_SOURCE_SHA256="$(sha256_of_file "$CLIENT2_TCP_UPDATE_FILE")"
}

start_tcp_update_receiver() {
    local port="$1"
    local output_file="$2"
    local logfile="$3"
    local status_file="$4"
    local pid_var="$5"
    local pid

    rm -f "$output_file" "$status_file"
    log_info "在 $SERVER_NS 启动 TCP update receiver: $port"
    ip netns exec "$SERVER_NS" python3 -u - \
        "${SERVER_TUN_ADDR%%/*}" "$port" "$output_file" "$status_file" "$TCP_UPDATE_TIMEOUT_SEC" <<'PY' > "$logfile" 2>&1 &
import socket
import sys

bind_ip = sys.argv[1]
port = int(sys.argv[2])
output_file = sys.argv[3]
status_file = sys.argv[4]
timeout = float(sys.argv[5])

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((bind_ip, port))
    server.listen(1)
    server.settimeout(timeout)
    print(f"tcp update receiver listening on {bind_ip}:{port}", flush=True)
    conn, addr = server.accept()
    print(f"tcp update receiver accepted {addr[0]}:{addr[1]}", flush=True)
    with conn:
        with open(output_file, "wb") as fh:
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                fh.write(chunk)

with open(status_file, "w", encoding="utf-8") as fh:
    fh.write("PASS\n")

print(f"tcp update receiver wrote {output_file}", flush=True)
PY
    pid=$!
    PIDS+=("$pid")
    printf -v "$pid_var" '%s' "$pid"

    sleep "$STARTUP_WAIT_SEC"
    if ! kill -0 "$pid" 2>/dev/null; then
        set_fail_reason "server TCP update receiver 启动失败，查看日志: $logfile"
        return 1
    fi
}

start_tcp_update_upload() {
    local namespace="$1"
    local source_file="$2"
    local port="$3"
    local logfile="$4"
    local status_file="$5"
    local pid_var="$6"
    local pid

    rm -f "$status_file"
    log_info "在 $namespace 启动 TCP update upload: $port"
    ip netns exec "$namespace" python3 -u - \
        "${SERVER_TUN_ADDR%%/*}" "$port" "$source_file" "$status_file" "$TCP_UPDATE_TIMEOUT_SEC" "$TCP_UPDATE_CLIENTS_GATE" <<'PY' > "$logfile" 2>&1 &
import os
import socket
import sys
import time

server_ip = sys.argv[1]
port = int(sys.argv[2])
source_file = sys.argv[3]
status_file = sys.argv[4]
timeout = float(sys.argv[5])
gate_file = sys.argv[6]
deadline = time.time() + timeout

while not os.path.exists(gate_file):
    if time.time() >= deadline:
        raise TimeoutError(f"timed out waiting for upload gate {gate_file}")
    time.sleep(0.05)

with socket.create_connection((server_ip, port), timeout=timeout) as sock:
    with open(source_file, "rb") as fh:
        while True:
            chunk = fh.read(65536)
            if not chunk:
                break
            sock.sendall(chunk)

with open(status_file, "w", encoding="utf-8") as fh:
    fh.write("PASS\n")

print(f"tcp update upload sent {source_file} to {server_ip}:{port}", flush=True)
PY
    pid=$!
    PIDS+=("$pid")
    printf -v "$pid_var" '%s' "$pid"
}

wait_for_pid_success() {
    local pid="$1"
    local title="$2"

    if ! wait "$pid"; then
        set_fail_reason "$title 失败"
        return 1
    fi

    log_pass "$title 成功"
}

run_tcp_per_client_update_payload() {
    log_info "UFTP 下行完成屏障已成立，开始 TCP per-client 上行 update payload"
    rm -f "$TCP_UPDATE_CLIENTS_GATE"
    generate_tcp_update_payloads

    start_tcp_update_receiver "$TCP_CLIENT1_PORT" "$SERVER_CLIENT1_TCP_UPDATE_FILE" "$CLIENT1_TCP_UPDATE_RECEIVER_LOG" "$CLIENT1_TCP_UPDATE_RECEIVER_STATUS" CLIENT1_TCP_UPDATE_RECEIVER_PID
    start_tcp_update_receiver "$TCP_CLIENT2_PORT" "$SERVER_CLIENT2_TCP_UPDATE_FILE" "$CLIENT2_TCP_UPDATE_RECEIVER_LOG" "$CLIENT2_TCP_UPDATE_RECEIVER_STATUS" CLIENT2_TCP_UPDATE_RECEIVER_PID
    start_tcp_update_upload "$CLIENT1_NS" "$CLIENT1_TCP_UPDATE_FILE" "$TCP_CLIENT1_PORT" "$CLIENT1_TCP_UPDATE_CLIENT_LOG" "$CLIENT1_TCP_UPDATE_CLIENT_STATUS" CLIENT1_TCP_UPDATE_CLIENT_PID
    start_tcp_update_upload "$CLIENT2_NS" "$CLIENT2_TCP_UPDATE_FILE" "$TCP_CLIENT2_PORT" "$CLIENT2_TCP_UPDATE_CLIENT_LOG" "$CLIENT2_TCP_UPDATE_CLIENT_STATUS" CLIENT2_TCP_UPDATE_CLIENT_PID

    : > "$TCP_UPDATE_CLIENTS_GATE"

    wait_for_pid_success "$CLIENT1_TCP_UPDATE_CLIENT_PID" "client1 TCP update upload"
    wait_for_pid_success "$CLIENT2_TCP_UPDATE_CLIENT_PID" "client2 TCP update upload"
    wait_for_pid_success "$CLIENT1_TCP_UPDATE_RECEIVER_PID" "server client1 TCP update receiver"
    wait_for_pid_success "$CLIENT2_TCP_UPDATE_RECEIVER_PID" "server client2 TCP update receiver"

    CLIENT1_TCP_UPDATE_RECEIVED_SHA256="$(sha256_of_file "$SERVER_CLIENT1_TCP_UPDATE_FILE")"
    CLIENT2_TCP_UPDATE_RECEIVED_SHA256="$(sha256_of_file "$SERVER_CLIENT2_TCP_UPDATE_FILE")"

    if [ "$CLIENT1_TCP_UPDATE_RECEIVED_SHA256" != "$CLIENT1_TCP_UPDATE_SOURCE_SHA256" ]; then
        set_fail_reason "client1 TCP update payload sha256 不匹配"
        return 1
    fi
    if [ "$CLIENT2_TCP_UPDATE_RECEIVED_SHA256" != "$CLIENT2_TCP_UPDATE_SOURCE_SHA256" ]; then
        set_fail_reason "client2 TCP update payload sha256 不匹配"
        return 1
    fi

    log_pass "client1/client2 TCP update payload sha256 均匹配各自源文件"
}

run_uftp_shared_downlink_payload() {
    add_uftp_multicast_routes
    generate_uftp_payload
    start_uftp_uplink_relay_for_client "$CLIENT1_NS" "$CLIENT1_TUN_PEER_PORT" "$SERVER_CLIENT1_IP" "$CLIENT1_UPLINK_RELAY_LOG" CLIENT1_UPLINK_RELAY_PID
    start_uftp_uplink_relay_for_client "$CLIENT2_NS" "$CLIENT2_TUN_PEER_PORT" "$SERVER_CLIENT2_IP" "$CLIENT2_UPLINK_RELAY_LOG" CLIENT2_UPLINK_RELAY_PID
    start_uftp_response_relay_for_client "$CLIENT1_NS" "${SERVER_TUN_ADDR%%/*}" "$CLIENT1_UFTP_RESPONSE_RELAY_LOG" CLIENT1_UFTP_RESPONSE_RELAY_PID
    start_uftp_response_relay_for_client "$CLIENT2_NS" "${SERVER_TUN_ADDR%%/*}" "$CLIENT2_UFTP_RESPONSE_RELAY_LOG" CLIENT2_UFTP_RESPONSE_RELAY_PID
    start_uftpd_for_client "$CLIENT1_NS" "${CLIENT1_TUN_ADDR%%/*}" "$CLIENT1_UFTP_DEST_DIR" "$CLIENT1_UFTP_TEMP_DIR" "$CLIENT1_UFTPD_LOG" "$CLIENT1_UFTPD_STATUS" "$CLIENT1_UFTPD_PIDFILE"
    start_uftpd_for_client "$CLIENT2_NS" "${CLIENT2_TUN_ADDR%%/*}" "$CLIENT2_UFTP_DEST_DIR" "$CLIENT2_UFTP_TEMP_DIR" "$CLIENT2_UFTPD_LOG" "$CLIENT2_UFTPD_STATUS" "$CLIENT2_UFTPD_PIDFILE"
    run_uftp_server
    verify_uftp_payloads
}

main() {
    prepare_log_dir
    require_root
    require_command ip
    require_command ping
    require_command grep
    require_command make
    require_command python3
    require_command sha256sum
    require_command timeout

    require_uftp_binaries

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
    run_uftp_shared_downlink_payload
    run_tcp_per_client_update_payload
}

main "$@"
