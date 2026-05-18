#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

LOG_DIR="${LOG_DIR:-$PROJECT_ROOT/tests/logs/v1_mainline_$(date +%Y%m%d_%H%M%S)}"
RESULT_MD="$LOG_DIR/result.md"

LINK_ID="${LINK_ID:-101}"
EPOCH="${EPOCH:-$(date +%s)}"
NODE_ID="${NODE_ID:-1}"

SERVER_TUN_NAME="${SERVER_TUN_NAME:-wfb-v1s}"
CLIENT_TUN_NAME="${CLIENT_TUN_NAME:-wfb-v1c}"
SERVER_TUN_ADDR="${SERVER_TUN_ADDR:-10.23.0.1/24}"
CLIENT_TUN_ADDR="${CLIENT_TUN_ADDR:-10.23.0.2/24}"
SERVER_TUN_IP="${SERVER_TUN_IP:-10.23.0.1}"
CLIENT_TUN_IP="${CLIENT_TUN_IP:-10.23.0.2}"

SERVER_TUN_LISTEN_PORT="${SERVER_TUN_LISTEN_PORT:-5800}"
SERVER_TUN_PEER_PORT="${SERVER_TUN_PEER_PORT:-5700}"
CLIENT_TUN_LISTEN_PORT="${CLIENT_TUN_LISTEN_PORT:-5801}"
CLIENT_TUN_PEER_PORT="${CLIENT_TUN_PEER_PORT:-5701}"

SERVER_TX_INPUT_PORT="${SERVER_TX_INPUT_PORT:-5700}"
CLIENT_TX_INPUT_PORT="${CLIENT_TX_INPUT_PORT:-5701}"

SERVER_RX_DEBUG_PORT="${SERVER_RX_DEBUG_PORT:-41001}"
CLIENT_RX_DEBUG_PORT="${CLIENT_RX_DEBUG_PORT:-41002}"

TOKEN_DURATION_MS="${TOKEN_DURATION_MS:-1000}"
TOKEN_GUARD_MS="${TOKEN_GUARD_MS:-100}"
PING_TIMEOUT_SEC="${PING_TIMEOUT_SEC:-20}"
STARTUP_WAIT_SEC="${STARTUP_WAIT_SEC:-2}"
TCP_TRANSFER_PORT="${TCP_TRANSFER_PORT:-5901}"
TCP_TRANSFER_TIMEOUT_SEC="${TCP_TRANSFER_TIMEOUT_SEC:-30}"
TCP_SOURCE_FILE_SIZE="${TCP_SOURCE_FILE_SIZE:-4096}"

PIDS=()
FAIL_REASON=""

log_info() { echo "[INFO] $(date '+%H:%M:%S') $1"; }
log_pass() { echo "[PASS] $(date '+%H:%M:%S') $1"; }
log_fail() { echo "[FAIL] $(date '+%H:%M:%S') $1" >&2; }

cleanup() {
    local pid
    for pid in "${PIDS[@]:-}"; do
        kill "$pid" 2>/dev/null || true
    done
    wait "${PIDS[@]:-}" 2>/dev/null || true
    ip link delete "$SERVER_TUN_NAME" 2>/dev/null || true
    ip link delete "$CLIENT_TUN_NAME" 2>/dev/null || true
}

trap cleanup EXIT

require_root() {
    if [ "${EUID}" -ne 0 ]; then
        log_fail "必须以 root 运行，wfb_tun 需要创建 TUN 设备"
        exit 1
    fi
}

prepare_log_dir() {
    mkdir -p "$LOG_DIR"
}

require_file() {
    local path="$1"
    if [ ! -e "$path" ]; then
        log_fail "缺少文件: $path"
        exit 1
    fi
}

require_executable() {
    local path="$1"
    if [ ! -x "$path" ]; then
        log_fail "缺少可执行文件: $path"
        exit 1
    fi
}

require_command() {
    local name="$1"
    if ! command -v "$name" >/dev/null 2>&1; then
        log_fail "缺少命令: $name"
        exit 1
    fi
}

assert_no_stale_tun() {
    ip link delete "$SERVER_TUN_NAME" 2>/dev/null || true
    ip link delete "$CLIENT_TUN_NAME" 2>/dev/null || true
}

run_bg() {
    local name="$1"
    local cmd="$2"
    local logfile="$3"
    log_info "启动 $name"
    bash -lc "$cmd" > "$logfile" 2>&1 &
    local pid=$!
    PIDS+=("$pid")
    sleep 1
    if ! kill -0 "$pid" 2>/dev/null; then
        log_fail "$name 启动失败，查看日志: $logfile"
        return 1
    fi
    return 0
}

wait_for_tun() {
    local tun_name="$1"
    local tries=20
    local i
    for ((i = 0; i < tries; i++)); do
        if ip link show "$tun_name" >/dev/null 2>&1; then
            return 0
        fi
        sleep 0.5
    done
    return 1
}

wait_for_startup() {
    sleep "$STARTUP_WAIT_SEC"
}

run_ping() {
    local name="$1"
    local src_ip="$2"
    local dst_ip="$3"
    local logfile="$4"
    log_info "执行 $name"
    timeout "$PING_TIMEOUT_SEC" ping -I "$src_ip" -c 3 "$dst_ip" > "$logfile" 2>&1
}

count_grants() {
    local logfile="$1"
    if [ ! -f "$logfile" ]; then
        echo 0
        return
    fi
    grep -c '^grant seq=' "$logfile" 2>/dev/null || true
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

file_sha256() {
    local path="$1"
    if [ ! -f "$path" ]; then
        return 1
    fi
    sha256sum "$path" | cut -d' ' -f1
}

run_tcp_uplink_transfer() {
    local source_file="$1"
    local received_file="$2"
    local server_log="$3"
    local client_log="$4"
    local listen_pid

    log_info "执行 Client -> Server TCP 小文件传输"
    timeout "$TCP_TRANSFER_TIMEOUT_SEC" nc -l -N "$SERVER_TUN_IP" "$TCP_TRANSFER_PORT" > "$received_file" 2> "$server_log" &
    listen_pid=$!
    PIDS+=("$listen_pid")

    sleep 1

    if ! timeout "$TCP_TRANSFER_TIMEOUT_SEC" nc -N -s "$CLIENT_TUN_IP" "$SERVER_TUN_IP" "$TCP_TRANSFER_PORT" < "$source_file" > /dev/null 2> "$client_log"; then
        return 1
    fi

    if ! wait "$listen_pid"; then
        return 1
    fi

    return 0
}

run_tcp_downlink_transfer() {
    local source_file="$1"
    local received_file="$2"
    local server_log="$3"
    local client_log="$4"
    local listen_pid

    log_info "执行 Server -> Client TCP 小文件传输"
    timeout "$TCP_TRANSFER_TIMEOUT_SEC" nc -l -N "$CLIENT_TUN_IP" "$TCP_TRANSFER_PORT" > "$received_file" 2> "$client_log" &
    listen_pid=$!
    PIDS+=("$listen_pid")

    sleep 1

    if ! timeout "$TCP_TRANSFER_TIMEOUT_SEC" nc -N -s "$SERVER_TUN_IP" "$CLIENT_TUN_IP" "$TCP_TRANSFER_PORT" < "$source_file" > /dev/null 2> "$server_log"; then
        return 1
    fi

    if ! wait "$listen_pid"; then
        return 1
    fi

    return 0
}

render_result() {
    local status="$1"
    local scheduler_grants="$2"
    local client_token_auth="$3"
    local client_token_socket="${CLIENT_TX_INPUT_PORT}.token"
    local tcp_uplink_source_sha256="${4:-}"
    local tcp_uplink_received_sha256="${5:-}"
    local tcp_downlink_source_sha256="${6:-}"
    local tcp_downlink_received_sha256="${7:-}"
    cat > "$RESULT_MD" <<EOF
# 第一版正式主线验收结果

- 结果: $status
- 原因: ${FAIL_REASON:-无}
- 日志目录: $LOG_DIR
- link_id: $LINK_ID
- epoch: $EPOCH
- node_id: $NODE_ID
- token_duration_ms: $TOKEN_DURATION_MS
- token_guard_ms: $TOKEN_GUARD_MS
- scheduler_grants: $scheduler_grants
- client_token_auth: ${client_token_auth:-未捕获}
- tcp_transfer_port: $TCP_TRANSFER_PORT
- tcp_uplink_source_sha256: ${tcp_uplink_source_sha256:-未生成}
- tcp_uplink_received_sha256: ${tcp_uplink_received_sha256:-未生成}
- tcp_downlink_source_sha256: ${tcp_downlink_source_sha256:-未生成}
- tcp_downlink_received_sha256: ${tcp_downlink_received_sha256:-未生成}

## 进程拓扑

- server_tx: 常开发送，不启用 Token gate
- client_tx: 启用 Token gate，由单一 NODE_ID 调度
- scheduler: 仅向 client 的 $client_token_socket 发放授权

## 验收项

- Server -> Client ping
- Client -> Server ping
- Client -> Server TCP 小文件传输
- Server -> Client TCP 小文件传输
- 单客户端固定窗口调度
- 脚本自动清理

## 日志文件

- server_tx.log
- server_rx.log
- server_tun.log
- client_tx.log
- client_rx.log
- client_tun.log
- scheduler.log
- ping_server_to_client.log
- ping_client_to_server.log
- tcp_uplink_server.log
- tcp_uplink_client.log
- tcp_uplink_source.bin
- tcp_uplink_received.bin
- tcp_downlink_server.log
- tcp_downlink_client.log
- tcp_downlink_source.bin
- tcp_downlink_received.bin
EOF
}

main() {
    require_root
    prepare_log_dir
    require_executable "$PROJECT_ROOT/wfb_tun"
    require_executable "$PROJECT_ROOT/wfb_tx"
    require_executable "$PROJECT_ROOT/wfb_rx"
    require_executable "$PROJECT_ROOT/wfb_token_scheduler"
    require_file "$PROJECT_ROOT/gs.key"
    require_file "$PROJECT_ROOT/drone.key"
    require_command nc
    require_command sha256sum
    require_command timeout
    assert_no_stale_tun

    local server_tun_cmd
    local client_tun_cmd
    local server_tx_cmd
    local client_tx_cmd
    local server_rx_cmd
    local client_rx_cmd
    local scheduler_cmd
    local scheduler_grants
    local client_token_auth
    local authorized_before_transfer
    local authorized_after_transfer
    local tcp_uplink_source_file="$LOG_DIR/tcp_uplink_source.bin"
    local tcp_uplink_received_file="$LOG_DIR/tcp_uplink_received.bin"
    local tcp_downlink_source_file="$LOG_DIR/tcp_downlink_source.bin"
    local tcp_downlink_received_file="$LOG_DIR/tcp_downlink_received.bin"
    local tcp_uplink_source_sha256=""
    local tcp_uplink_received_sha256=""
    local tcp_downlink_source_sha256=""
    local tcp_downlink_received_sha256=""

    server_tun_cmd="'$PROJECT_ROOT/wfb_tun' -t '$SERVER_TUN_NAME' -a '$SERVER_TUN_ADDR' -l '$SERVER_TUN_LISTEN_PORT' -u '$SERVER_TUN_PEER_PORT'"
    client_tun_cmd="'$PROJECT_ROOT/wfb_tun' -t '$CLIENT_TUN_NAME' -a '$CLIENT_TUN_ADDR' -l '$CLIENT_TUN_LISTEN_PORT' -u '$CLIENT_TUN_PEER_PORT'"

    server_tx_cmd="'$PROJECT_ROOT/wfb_tx' -K '$PROJECT_ROOT/gs.key' -u '$SERVER_TX_INPUT_PORT' -D '$CLIENT_RX_DEBUG_PORT' -i '$LINK_ID' -e '$EPOCH' -R 524288 -s 524288 server-downlink"
    client_tx_cmd="'$PROJECT_ROOT/wfb_tx' -g -K '$PROJECT_ROOT/drone.key' -u '$CLIENT_TX_INPUT_PORT' -D '$SERVER_RX_DEBUG_PORT' -i '$LINK_ID' -e '$EPOCH' -R 524288 -s 524288 client-uplink"

    server_rx_cmd="'$PROJECT_ROOT/wfb_rx' -a '$SERVER_RX_DEBUG_PORT' -K '$PROJECT_ROOT/gs.key' -c 127.0.0.1 -u '$SERVER_TUN_LISTEN_PORT' -N 0 -i '$LINK_ID' -e '$EPOCH' -R 524288 -s 524288"
    client_rx_cmd="'$PROJECT_ROOT/wfb_rx' -a '$CLIENT_RX_DEBUG_PORT' -K '$PROJECT_ROOT/drone.key' -c 127.0.0.1 -u '$CLIENT_TUN_LISTEN_PORT' -N '$NODE_ID' -i '$LINK_ID' -e '$EPOCH' -R 524288 -s 524288"

    scheduler_cmd="'$PROJECT_ROOT/wfb_token_scheduler' -n '$NODE_ID' -d '$TOKEN_DURATION_MS' -g '$TOKEN_GUARD_MS' -s '$CLIENT_TX_INPUT_PORT'"

    run_bg "server_tun" "$server_tun_cmd" "$LOG_DIR/server_tun.log"
    run_bg "client_tun" "$client_tun_cmd" "$LOG_DIR/client_tun.log"

    if ! wait_for_tun "$SERVER_TUN_NAME"; then
        FAIL_REASON="server TUN 未创建成功"
        render_result "FAIL" 0 ""
        exit 1
    fi

    if ! wait_for_tun "$CLIENT_TUN_NAME"; then
        FAIL_REASON="client TUN 未创建成功"
        render_result "FAIL" 0 ""
        exit 1
    fi

    run_bg "server_rx" "$server_rx_cmd" "$LOG_DIR/server_rx.log"
    run_bg "client_rx" "$client_rx_cmd" "$LOG_DIR/client_rx.log"
    run_bg "server_tx" "$server_tx_cmd" "$LOG_DIR/server_tx.log"
    run_bg "client_tx" "$client_tx_cmd" "$LOG_DIR/client_tx.log"
    run_bg "scheduler" "$scheduler_cmd" "$LOG_DIR/scheduler.log"

    wait_for_startup

    if ! run_ping "Server -> Client ping" "$SERVER_TUN_IP" "$CLIENT_TUN_IP" "$LOG_DIR/ping_server_to_client.log"; then
        FAIL_REASON="Server -> Client ping 失败"
        scheduler_grants="$(count_grants "$LOG_DIR/scheduler.log")"
        client_token_auth="$(extract_token_auth_line "$LOG_DIR/client_tx.log")"
        render_result "FAIL" "$scheduler_grants" "$client_token_auth"
        exit 1
    fi
    log_pass "Server -> Client ping 成功"

    if ! run_ping "Client -> Server ping" "$CLIENT_TUN_IP" "$SERVER_TUN_IP" "$LOG_DIR/ping_client_to_server.log"; then
        FAIL_REASON="Client -> Server ping 失败"
        scheduler_grants="$(count_grants "$LOG_DIR/scheduler.log")"
        client_token_auth="$(extract_token_auth_line "$LOG_DIR/client_tx.log")"
        render_result "FAIL" "$scheduler_grants" "$client_token_auth"
        exit 1
    fi
    log_pass "Client -> Server ping 成功"

    dd if=/dev/urandom of="$tcp_uplink_source_file" bs="$TCP_SOURCE_FILE_SIZE" count=1 status=none
    dd if=/dev/urandom of="$tcp_downlink_source_file" bs="$TCP_SOURCE_FILE_SIZE" count=1 status=none
    tcp_uplink_source_sha256="$(file_sha256 "$tcp_uplink_source_file")"
    tcp_downlink_source_sha256="$(file_sha256 "$tcp_downlink_source_file")"
    authorized_before_transfer="$(extract_token_auth_field "$LOG_DIR/client_tx.log" authorized_sends)"

    if ! run_tcp_uplink_transfer "$tcp_uplink_source_file" "$tcp_uplink_received_file" "$LOG_DIR/tcp_uplink_server.log" "$LOG_DIR/tcp_uplink_client.log"; then
        FAIL_REASON="Client -> Server TCP 小文件传输失败"
        scheduler_grants="$(count_grants "$LOG_DIR/scheduler.log")"
        client_token_auth="$(extract_token_auth_line "$LOG_DIR/client_tx.log")"
        tcp_uplink_received_sha256="$(file_sha256 "$tcp_uplink_received_file" 2>/dev/null || true)"
        render_result "FAIL" "$scheduler_grants" "$client_token_auth" "$tcp_uplink_source_sha256" "$tcp_uplink_received_sha256" "$tcp_downlink_source_sha256" "$tcp_downlink_received_sha256"
        exit 1
    fi

    if [ ! -f "$tcp_uplink_received_file" ]; then
        FAIL_REASON="服务端未生成接收文件"
        scheduler_grants="$(count_grants "$LOG_DIR/scheduler.log")"
        client_token_auth="$(extract_token_auth_line "$LOG_DIR/client_tx.log")"
        render_result "FAIL" "$scheduler_grants" "$client_token_auth" "$tcp_uplink_source_sha256" "" "$tcp_downlink_source_sha256" "$tcp_downlink_received_sha256"
        exit 1
    fi

    tcp_uplink_received_sha256="$(file_sha256 "$tcp_uplink_received_file")"
    if [ "$tcp_uplink_source_sha256" != "$tcp_uplink_received_sha256" ]; then
        FAIL_REASON="服务端接收文件校验和不匹配"
        scheduler_grants="$(count_grants "$LOG_DIR/scheduler.log")"
        client_token_auth="$(extract_token_auth_line "$LOG_DIR/client_tx.log")"
        render_result "FAIL" "$scheduler_grants" "$client_token_auth" "$tcp_uplink_source_sha256" "$tcp_uplink_received_sha256" "$tcp_downlink_source_sha256" "$tcp_downlink_received_sha256"
        exit 1
    fi
    log_pass "Client -> Server TCP 小文件传输成功且内容一致"

    if ! run_tcp_downlink_transfer "$tcp_downlink_source_file" "$tcp_downlink_received_file" "$LOG_DIR/tcp_downlink_server.log" "$LOG_DIR/tcp_downlink_client.log"; then
        FAIL_REASON="Server -> Client TCP 小文件传输失败"
        scheduler_grants="$(count_grants "$LOG_DIR/scheduler.log")"
        client_token_auth="$(extract_token_auth_line "$LOG_DIR/client_tx.log")"
        tcp_downlink_received_sha256="$(file_sha256 "$tcp_downlink_received_file" 2>/dev/null || true)"
        render_result "FAIL" "$scheduler_grants" "$client_token_auth" "$tcp_uplink_source_sha256" "$tcp_uplink_received_sha256" "$tcp_downlink_source_sha256" "$tcp_downlink_received_sha256"
        exit 1
    fi

    if [ ! -f "$tcp_downlink_received_file" ]; then
        FAIL_REASON="客户端未生成下行接收文件"
        scheduler_grants="$(count_grants "$LOG_DIR/scheduler.log")"
        client_token_auth="$(extract_token_auth_line "$LOG_DIR/client_tx.log")"
        render_result "FAIL" "$scheduler_grants" "$client_token_auth" "$tcp_uplink_source_sha256" "$tcp_uplink_received_sha256" "$tcp_downlink_source_sha256" ""
        exit 1
    fi

    tcp_downlink_received_sha256="$(file_sha256 "$tcp_downlink_received_file")"
    if [ "$tcp_downlink_source_sha256" != "$tcp_downlink_received_sha256" ]; then
        FAIL_REASON="客户端下行接收文件校验和不匹配"
        scheduler_grants="$(count_grants "$LOG_DIR/scheduler.log")"
        client_token_auth="$(extract_token_auth_line "$LOG_DIR/client_tx.log")"
        render_result "FAIL" "$scheduler_grants" "$client_token_auth" "$tcp_uplink_source_sha256" "$tcp_uplink_received_sha256" "$tcp_downlink_source_sha256" "$tcp_downlink_received_sha256"
        exit 1
    fi
    log_pass "Server -> Client TCP 小文件传输成功且内容一致"

    scheduler_grants="$(count_grants "$LOG_DIR/scheduler.log")"
    client_token_auth="$(extract_token_auth_line "$LOG_DIR/client_tx.log")"
    authorized_after_transfer="$(extract_token_auth_field "$LOG_DIR/client_tx.log" authorized_sends)"

    if [ "$scheduler_grants" -le 0 ]; then
        FAIL_REASON="调度器未产生任何 grant"
        render_result "FAIL" "$scheduler_grants" "$client_token_auth" "$tcp_uplink_source_sha256" "$tcp_uplink_received_sha256" "$tcp_downlink_source_sha256" "$tcp_downlink_received_sha256"
        exit 1
    fi

    if [ -z "$client_token_auth" ]; then
        FAIL_REASON="client_tx 未输出 TOKEN_AUTH 统计，无法确认上行门控链路"
        render_result "FAIL" "$scheduler_grants" "$client_token_auth" "$tcp_uplink_source_sha256" "$tcp_uplink_received_sha256" "$tcp_downlink_source_sha256" "$tcp_downlink_received_sha256"
        exit 1
    fi

    if [ "$authorized_after_transfer" -le "$authorized_before_transfer" ]; then
        FAIL_REASON="TCP 上行后 authorized_sends 未增长，无法确认上传走过 Token 门控发送路径"
        render_result "FAIL" "$scheduler_grants" "$client_token_auth" "$tcp_uplink_source_sha256" "$tcp_uplink_received_sha256" "$tcp_downlink_source_sha256" "$tcp_downlink_received_sha256"
        exit 1
    fi

    render_result "PASS" "$scheduler_grants" "$client_token_auth" "$tcp_uplink_source_sha256" "$tcp_uplink_received_sha256" "$tcp_downlink_source_sha256" "$tcp_downlink_received_sha256"
    log_pass "第一版正式主线单客户端不对称 Token 闭环与双向 TCP 小文件验收通过"
}

main "$@"
