#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

LOG_DIR="${LOG_DIR:-$PROJECT_ROOT/tests/logs/v1_mainline_monitor_$(date +%Y%m%d_%H%M%S)}"
RESULT_MD="$LOG_DIR/result.md"
ENV_SUMMARY_FILE="$LOG_DIR/environment.txt"

LINK_ID="${LINK_ID:-101}"
EPOCH="${EPOCH:-$(date +%s)}"
NODE_ID="${NODE_ID:-1}"

SERVER_TUN_IP="${SERVER_TUN_IP:-10.23.0.1}"
CLIENT_TUN_IP="${CLIENT_TUN_IP:-10.23.0.2}"

TCP_TRANSFER_PORT="${TCP_TRANSFER_PORT:-5901}"
TCP_TRANSFER_TIMEOUT_SEC="${TCP_TRANSFER_TIMEOUT_SEC:-30}"
TCP_SOURCE_FILE_SIZE="${TCP_SOURCE_FILE_SIZE:-4096}"
STARTUP_WAIT_SEC="${STARTUP_WAIT_SEC:-3}"
PING_TIMEOUT_SEC="${PING_TIMEOUT_SEC:-20}"

SERVER_WIFI_IFACE="${SERVER_WIFI_IFACE:-${WIFI_IFACE:-}}"
CLIENT_WIFI_IFACE="${CLIENT_WIFI_IFACE:-${WIFI_IFACE_CLIENT:-$SERVER_WIFI_IFACE}}"

V1_SERVER_TUN_CMD="${V1_SERVER_TUN_CMD:-}"
V1_CLIENT_TUN_CMD="${V1_CLIENT_TUN_CMD:-}"
V1_SERVER_TX_CMD="${V1_SERVER_TX_CMD:-}"
V1_CLIENT_TX_CMD="${V1_CLIENT_TX_CMD:-}"
V1_SERVER_RX_CMD="${V1_SERVER_RX_CMD:-}"
V1_CLIENT_RX_CMD="${V1_CLIENT_RX_CMD:-}"
V1_SCHEDULER_CMD="${V1_SCHEDULER_CMD:-}"

TCP_UPLINK_SOURCE_FILE="$LOG_DIR/tcp_uplink_source.bin"
TCP_UPLINK_RECEIVED_FILE="$LOG_DIR/tcp_uplink_received.bin"
TCP_DOWNLINK_SOURCE_FILE="$LOG_DIR/tcp_downlink_source.bin"
TCP_DOWNLINK_RECEIVED_FILE="$LOG_DIR/tcp_downlink_received.bin"

V1_SERVER_TO_CLIENT_PING_CMD="${V1_SERVER_TO_CLIENT_PING_CMD:-ping -I \"$SERVER_TUN_IP\" -c 3 \"$CLIENT_TUN_IP\"}"
V1_CLIENT_TO_SERVER_PING_CMD="${V1_CLIENT_TO_SERVER_PING_CMD:-ping -I \"$CLIENT_TUN_IP\" -c 3 \"$SERVER_TUN_IP\"}"
V1_TCP_UPLINK_LISTEN_CMD="${V1_TCP_UPLINK_LISTEN_CMD:-timeout \"$TCP_TRANSFER_TIMEOUT_SEC\" nc -l -N \"$SERVER_TUN_IP\" \"$TCP_TRANSFER_PORT\" > \"$TCP_UPLINK_RECEIVED_FILE\"}"
V1_TCP_UPLINK_SEND_CMD="${V1_TCP_UPLINK_SEND_CMD:-timeout \"$TCP_TRANSFER_TIMEOUT_SEC\" nc -N -s \"$CLIENT_TUN_IP\" \"$SERVER_TUN_IP\" \"$TCP_TRANSFER_PORT\" < \"$TCP_UPLINK_SOURCE_FILE\" > /dev/null}"
V1_TCP_DOWNLINK_LISTEN_CMD="${V1_TCP_DOWNLINK_LISTEN_CMD:-timeout \"$TCP_TRANSFER_TIMEOUT_SEC\" nc -l -N \"$CLIENT_TUN_IP\" \"$TCP_TRANSFER_PORT\" > \"$TCP_DOWNLINK_RECEIVED_FILE\"}"
V1_TCP_DOWNLINK_SEND_CMD="${V1_TCP_DOWNLINK_SEND_CMD:-timeout \"$TCP_TRANSFER_TIMEOUT_SEC\" nc -N -s \"$SERVER_TUN_IP\" \"$CLIENT_TUN_IP\" \"$TCP_TRANSFER_PORT\" < \"$TCP_DOWNLINK_SOURCE_FILE\" > /dev/null}"

PCAP_CAPTURE_CMD="${PCAP_CAPTURE_CMD:-}"

PIDS=()
STARTED_NAMES=()
FAIL_REASON=""

log_info() { echo "[INFO] $(date '+%H:%M:%S') $1"; }
log_pass() { echo "[PASS] $(date '+%H:%M:%S') $1"; }
log_fail() { echo "[FAIL] $(date '+%H:%M:%S') $1" >&2; }
log_warn() { echo "[WARN] $(date '+%H:%M:%S') $1" >&2; }

usage() {
    cat <<'EOF'
用法:
  sudo bash tests/real_hardware/v1_mainline_monitor.sh

必填环境变量:
  V1_SERVER_TUN_CMD
  V1_CLIENT_TUN_CMD
  V1_SERVER_TX_CMD
  V1_CLIENT_TX_CMD
  V1_SERVER_RX_CMD
  V1_CLIENT_RX_CMD
  V1_SCHEDULER_CMD

可选环境变量:
  SERVER_WIFI_IFACE              服务端 monitor 网卡名，用于本地证据采集
  CLIENT_WIFI_IFACE              客户端 monitor 网卡名，用于本地证据采集
  V1_SERVER_TO_CLIENT_PING_CMD   Server -> Client ping 命令
  V1_CLIENT_TO_SERVER_PING_CMD   Client -> Server ping 命令
  V1_TCP_UPLINK_LISTEN_CMD       上行接收监听命令
  V1_TCP_UPLINK_SEND_CMD         上行发送命令
  V1_TCP_DOWNLINK_LISTEN_CMD     下行接收监听命令
  V1_TCP_DOWNLINK_SEND_CMD       下行发送命令
  PCAP_CAPTURE_CMD               抓包命令；未设置且本地可用时，默认对 SERVER_WIFI_IFACE 抓包
  LOG_DIR                        日志目录
  SERVER_TUN_IP                  默认 10.23.0.1
  CLIENT_TUN_IP                  默认 10.23.0.2
  TCP_TRANSFER_PORT              默认 5901
  TCP_TRANSFER_TIMEOUT_SEC       默认 30
  TCP_SOURCE_FILE_SIZE           默认 4096
  STARTUP_WAIT_SEC               默认 3
  PING_TIMEOUT_SEC               默认 20

说明:
  该脚本只负责前置检查、真实硬件验收编排与留痕，不自动切换网卡到 monitor 模式。
  若角色分布在多台机器上，可把 V1_*_CMD 设置为 ssh 启动命令。
EOF
}

cleanup() {
    local pid
    for pid in "${PIDS[@]:-}"; do
        kill "$pid" 2>/dev/null || true
    done
    wait "${PIDS[@]:-}" 2>/dev/null || true
}

trap cleanup EXIT

prepare_log_dir() {
    mkdir -p "$LOG_DIR"
}

require_command() {
    local name="$1"
    if ! command -v "$name" >/dev/null 2>&1; then
        log_fail "缺少命令: $name"
        exit 1
    fi
}

require_env() {
    local name="$1"
    local value="${!name:-}"
    if [ -z "$value" ]; then
        log_fail "缺少环境变量: $name"
        exit 1
    fi
}

iface_exists_local() {
    local iface="$1"
    [ -n "$iface" ] && ip link show "$iface" >/dev/null 2>&1
}

iface_mode() {
    local iface="$1"
    if ! iface_exists_local "$iface"; then
        echo "not-local-or-unavailable"
        return
    fi
    iw dev "$iface" info 2>/dev/null | grep -oP 'type\s+\K\w+' | head -1 || echo "unknown"
}

iface_channel() {
    local iface="$1"
    if ! iface_exists_local "$iface"; then
        echo "not-local-or-unavailable"
        return
    fi
    iw dev "$iface" info 2>/dev/null | grep -oP 'channel\s+\K[^ ]+' | head -1 || echo "unknown"
}

assert_monitor_mode_if_local() {
    local iface="$1"
    local role="$2"
    local mode

    if [ -z "$iface" ]; then
        log_warn "$role 未提供网卡名，无法采集本地 monitor 模式证据"
        return
    fi

    if ! iface_exists_local "$iface"; then
        log_warn "$role 网卡 $iface 不在本机，跳过本地 monitor 模式检查"
        return
    fi

    mode="$(iface_mode "$iface")"
    if [ "$mode" != "monitor" ]; then
        log_fail "$role 网卡 $iface 当前模式为 $mode，不是 monitor"
        log_info "请先手动配置: sudo ip link set $iface down && sudo iw dev $iface set type monitor && sudo ip link set $iface up"
        exit 1
    fi
}

count_grants() {
    local logfile="$1"
    if [ ! -f "$logfile" ]; then
        echo 0
        return
    fi
    grep -c '^grant seq=' "$logfile" 2>/dev/null || true
}

latest_token_auth_line() {
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
    line="$(latest_token_auth_line "$logfile")"
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

run_bg() {
    local name="$1"
    local cmd="$2"
    local logfile="$3"
    log_info "启动 $name"
    bash -lc "$cmd" > "$logfile" 2>&1 &
    local pid=$!
    PIDS+=("$pid")
    STARTED_NAMES+=("$name")
    sleep 1
    if ! kill -0 "$pid" 2>/dev/null; then
        log_fail "$name 启动失败，查看日志: $logfile"
        return 1
    fi
    return 0
}

run_cmd() {
    local name="$1"
    local cmd="$2"
    local logfile="$3"
    log_info "执行 $name"
    timeout "$PING_TIMEOUT_SEC" bash -lc "$cmd" > "$logfile" 2>&1
}

run_transfer() {
    local listen_name="$1"
    local listen_cmd="$2"
    local listen_log="$3"
    local send_name="$4"
    local send_cmd="$5"
    local send_log="$6"
    local listen_pid

    log_info "启动 $listen_name"
    bash -lc "$listen_cmd" > "$listen_log" 2>&1 &
    listen_pid=$!
    PIDS+=("$listen_pid")
    STARTED_NAMES+=("$listen_name")
    sleep 1
    if ! kill -0 "$listen_pid" 2>/dev/null; then
        log_fail "$listen_name 启动失败，查看日志: $listen_log"
        return 1
    fi

    log_info "执行 $send_name"
    if ! bash -lc "$send_cmd" > "$send_log" 2>&1; then
        return 1
    fi

    if ! wait "$listen_pid"; then
        return 1
    fi

    return 0
}

start_capture_if_possible() {
    local cmd="$PCAP_CAPTURE_CMD"
    if [ -z "$cmd" ] && [ -n "$SERVER_WIFI_IFACE" ] && iface_exists_local "$SERVER_WIFI_IFACE" && command -v tcpdump >/dev/null 2>&1; then
        cmd="tcpdump -i \"$SERVER_WIFI_IFACE\" -w \"$LOG_DIR/capture.pcap\""
    fi

    if [ -z "$cmd" ]; then
        log_warn "未配置抓包命令，且无法生成本地默认抓包；将只保留环境摘要和进程日志"
        return
    fi

    run_bg "pcap_capture" "$cmd" "$LOG_DIR/pcap_capture.log" || log_warn "抓包启动失败，继续执行验收"
}

write_environment_summary() {
    cat > "$ENV_SUMMARY_FILE" <<EOF
generated_at=$(date --iso-8601=seconds)
log_dir=$LOG_DIR
link_id=$LINK_ID
epoch=$EPOCH
node_id=$NODE_ID
server_tun_ip=$SERVER_TUN_IP
client_tun_ip=$CLIENT_TUN_IP
tcp_transfer_port=$TCP_TRANSFER_PORT
server_wifi_iface=${SERVER_WIFI_IFACE:-unset}
server_wifi_mode=$(iface_mode "$SERVER_WIFI_IFACE")
server_wifi_channel=$(iface_channel "$SERVER_WIFI_IFACE")
client_wifi_iface=${CLIENT_WIFI_IFACE:-unset}
client_wifi_mode=$(iface_mode "$CLIENT_WIFI_IFACE")
client_wifi_channel=$(iface_channel "$CLIENT_WIFI_IFACE")
pcap_capture_cmd=${PCAP_CAPTURE_CMD:-auto-or-disabled}

[commands]
V1_SERVER_TUN_CMD=$V1_SERVER_TUN_CMD
V1_CLIENT_TUN_CMD=$V1_CLIENT_TUN_CMD
V1_SERVER_TX_CMD=$V1_SERVER_TX_CMD
V1_CLIENT_TX_CMD=$V1_CLIENT_TX_CMD
V1_SERVER_RX_CMD=$V1_SERVER_RX_CMD
V1_CLIENT_RX_CMD=$V1_CLIENT_RX_CMD
V1_SCHEDULER_CMD=$V1_SCHEDULER_CMD
V1_SERVER_TO_CLIENT_PING_CMD=$V1_SERVER_TO_CLIENT_PING_CMD
V1_CLIENT_TO_SERVER_PING_CMD=$V1_CLIENT_TO_SERVER_PING_CMD
V1_TCP_UPLINK_LISTEN_CMD=$V1_TCP_UPLINK_LISTEN_CMD
V1_TCP_UPLINK_SEND_CMD=$V1_TCP_UPLINK_SEND_CMD
V1_TCP_DOWNLINK_LISTEN_CMD=$V1_TCP_DOWNLINK_LISTEN_CMD
V1_TCP_DOWNLINK_SEND_CMD=$V1_TCP_DOWNLINK_SEND_CMD
EOF
}

render_result() {
    local status="$1"
    local scheduler_grants="$2"
    local client_token_auth="$3"
    local tcp_uplink_source_sha256="$4"
    local tcp_uplink_received_sha256="$5"
    local tcp_downlink_source_sha256="$6"
    local tcp_downlink_received_sha256="$7"
    local server_mode="$(iface_mode "$SERVER_WIFI_IFACE")"
    local client_mode="$(iface_mode "$CLIENT_WIFI_IFACE")"
    local server_channel="$(iface_channel "$SERVER_WIFI_IFACE")"
    local client_channel="$(iface_channel "$CLIENT_WIFI_IFACE")"

    cat > "$RESULT_MD" <<EOF
# v1-mainline 真实硬件 monitor 验收结果

- 结果: $status
- 原因: ${FAIL_REASON:-无}
- 日志目录: $LOG_DIR
- 环境摘要: $ENV_SUMMARY_FILE
- link_id: $LINK_ID
- epoch: $EPOCH
- node_id: $NODE_ID
- tcp_transfer_port: $TCP_TRANSFER_PORT
- scheduler_grants: $scheduler_grants
- client_token_auth: ${client_token_auth:-未捕获}
- server_wifi_iface: ${SERVER_WIFI_IFACE:-未提供}
- server_wifi_mode: $server_mode
- server_wifi_channel: $server_channel
- client_wifi_iface: ${CLIENT_WIFI_IFACE:-未提供}
- client_wifi_mode: $client_mode
- client_wifi_channel: $client_channel
- tcp_uplink_source_sha256: ${tcp_uplink_source_sha256:-未生成}
- tcp_uplink_received_sha256: ${tcp_uplink_received_sha256:-未生成}
- tcp_downlink_source_sha256: ${tcp_downlink_source_sha256:-未生成}
- tcp_downlink_received_sha256: ${tcp_downlink_received_sha256:-未生成}

## 进程拓扑

- server_tx: 常开发送，不启用 Token gate
- client_tx: 启用 Token gate，由单一 NODE_ID 调度
- scheduler: 面向 client_tx 发放授权
- 底层链路: 真实 monitor/injection Wi-Fi 网卡，不是 wlan emulation

## 验收项

- Server -> Client ping
- Client -> Server ping
- Client -> Server TCP 小文件传输
- Server -> Client TCP 小文件传输
- 单客户端固定窗口调度
- monitor 模式环境留痕

## 日志文件

- environment.txt
- server_tun.log
- client_tun.log
- server_tx.log
- client_tx.log
- server_rx.log
- client_rx.log
- scheduler.log
- ping_server_to_client.log
- ping_client_to_server.log
- tcp_uplink_listener.log
- tcp_uplink_sender.log
- tcp_uplink_source.bin
- tcp_uplink_received.bin
- tcp_downlink_listener.log
- tcp_downlink_sender.log
- tcp_downlink_source.bin
- tcp_downlink_received.bin
EOF

    if [ -f "$LOG_DIR/pcap_capture.log" ]; then
        cat >> "$RESULT_MD" <<EOF
- pcap_capture.log
EOF
    fi

    if [ -f "$LOG_DIR/capture.pcap" ]; then
        cat >> "$RESULT_MD" <<EOF
- capture.pcap
EOF
    fi
}

main() {
    if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
        usage
        exit 0
    fi

    prepare_log_dir
    require_command bash
    require_command dd
    require_command sha256sum
    require_command timeout
    require_command ip
    if command -v iw >/dev/null 2>&1; then
        :
    else
        log_warn "未找到 iw，无法采集本地网卡模式/信道证据"
    fi

    require_env V1_SERVER_TUN_CMD
    require_env V1_CLIENT_TUN_CMD
    require_env V1_SERVER_TX_CMD
    require_env V1_CLIENT_TX_CMD
    require_env V1_SERVER_RX_CMD
    require_env V1_CLIENT_RX_CMD
    require_env V1_SCHEDULER_CMD

    export LOG_DIR RESULT_MD ENV_SUMMARY_FILE
    export LINK_ID EPOCH NODE_ID SERVER_TUN_IP CLIENT_TUN_IP
    export TCP_TRANSFER_PORT TCP_TRANSFER_TIMEOUT_SEC TCP_SOURCE_FILE_SIZE STARTUP_WAIT_SEC PING_TIMEOUT_SEC
    export TCP_UPLINK_SOURCE_FILE TCP_UPLINK_RECEIVED_FILE TCP_DOWNLINK_SOURCE_FILE TCP_DOWNLINK_RECEIVED_FILE

    if command -v iw >/dev/null 2>&1; then
        assert_monitor_mode_if_local "$SERVER_WIFI_IFACE" "服务端"
        assert_monitor_mode_if_local "$CLIENT_WIFI_IFACE" "客户端"
    fi

    write_environment_summary
    start_capture_if_possible

    local scheduler_grants="0"
    local client_token_auth=""
    local authorized_before_transfer="0"
    local authorized_after_transfer="0"
    local tcp_uplink_source_sha256=""
    local tcp_uplink_received_sha256=""
    local tcp_downlink_source_sha256=""
    local tcp_downlink_received_sha256=""

    run_bg "server_tun" "$V1_SERVER_TUN_CMD" "$LOG_DIR/server_tun.log"
    run_bg "client_tun" "$V1_CLIENT_TUN_CMD" "$LOG_DIR/client_tun.log"
    run_bg "server_rx" "$V1_SERVER_RX_CMD" "$LOG_DIR/server_rx.log"
    run_bg "client_rx" "$V1_CLIENT_RX_CMD" "$LOG_DIR/client_rx.log"
    run_bg "server_tx" "$V1_SERVER_TX_CMD" "$LOG_DIR/server_tx.log"
    run_bg "client_tx" "$V1_CLIENT_TX_CMD" "$LOG_DIR/client_tx.log"
    run_bg "scheduler" "$V1_SCHEDULER_CMD" "$LOG_DIR/scheduler.log"

    sleep "$STARTUP_WAIT_SEC"

    if ! run_cmd "Server -> Client ping" "$V1_SERVER_TO_CLIENT_PING_CMD" "$LOG_DIR/ping_server_to_client.log"; then
        FAIL_REASON="Server -> Client ping 失败"
        scheduler_grants="$(count_grants "$LOG_DIR/scheduler.log")"
        client_token_auth="$(latest_token_auth_line "$LOG_DIR/client_tx.log")"
        render_result "FAIL" "$scheduler_grants" "$client_token_auth" "$tcp_uplink_source_sha256" "$tcp_uplink_received_sha256" "$tcp_downlink_source_sha256" "$tcp_downlink_received_sha256"
        exit 1
    fi
    log_pass "Server -> Client ping 成功"

    if ! run_cmd "Client -> Server ping" "$V1_CLIENT_TO_SERVER_PING_CMD" "$LOG_DIR/ping_client_to_server.log"; then
        FAIL_REASON="Client -> Server ping 失败"
        scheduler_grants="$(count_grants "$LOG_DIR/scheduler.log")"
        client_token_auth="$(latest_token_auth_line "$LOG_DIR/client_tx.log")"
        render_result "FAIL" "$scheduler_grants" "$client_token_auth" "$tcp_uplink_source_sha256" "$tcp_uplink_received_sha256" "$tcp_downlink_source_sha256" "$tcp_downlink_received_sha256"
        exit 1
    fi
    log_pass "Client -> Server ping 成功"

    dd if=/dev/urandom of="$TCP_UPLINK_SOURCE_FILE" bs="$TCP_SOURCE_FILE_SIZE" count=1 status=none
    dd if=/dev/urandom of="$TCP_DOWNLINK_SOURCE_FILE" bs="$TCP_SOURCE_FILE_SIZE" count=1 status=none
    tcp_uplink_source_sha256="$(file_sha256 "$TCP_UPLINK_SOURCE_FILE")"
    tcp_downlink_source_sha256="$(file_sha256 "$TCP_DOWNLINK_SOURCE_FILE")"
    authorized_before_transfer="$(extract_token_auth_field "$LOG_DIR/client_tx.log" authorized_sends)"

    if ! run_transfer "tcp_uplink_listener" "$V1_TCP_UPLINK_LISTEN_CMD" "$LOG_DIR/tcp_uplink_listener.log" "tcp_uplink_sender" "$V1_TCP_UPLINK_SEND_CMD" "$LOG_DIR/tcp_uplink_sender.log"; then
        FAIL_REASON="Client -> Server TCP 小文件传输失败"
        scheduler_grants="$(count_grants "$LOG_DIR/scheduler.log")"
        client_token_auth="$(latest_token_auth_line "$LOG_DIR/client_tx.log")"
        tcp_uplink_received_sha256="$(file_sha256 "$TCP_UPLINK_RECEIVED_FILE" 2>/dev/null || true)"
        render_result "FAIL" "$scheduler_grants" "$client_token_auth" "$tcp_uplink_source_sha256" "$tcp_uplink_received_sha256" "$tcp_downlink_source_sha256" "$tcp_downlink_received_sha256"
        exit 1
    fi

    if [ ! -f "$TCP_UPLINK_RECEIVED_FILE" ]; then
        FAIL_REASON="上行接收文件未生成"
        scheduler_grants="$(count_grants "$LOG_DIR/scheduler.log")"
        client_token_auth="$(latest_token_auth_line "$LOG_DIR/client_tx.log")"
        render_result "FAIL" "$scheduler_grants" "$client_token_auth" "$tcp_uplink_source_sha256" "" "$tcp_downlink_source_sha256" "$tcp_downlink_received_sha256"
        exit 1
    fi

    tcp_uplink_received_sha256="$(file_sha256 "$TCP_UPLINK_RECEIVED_FILE")"
    if [ "$tcp_uplink_source_sha256" != "$tcp_uplink_received_sha256" ]; then
        FAIL_REASON="上行接收文件校验和不匹配"
        scheduler_grants="$(count_grants "$LOG_DIR/scheduler.log")"
        client_token_auth="$(latest_token_auth_line "$LOG_DIR/client_tx.log")"
        render_result "FAIL" "$scheduler_grants" "$client_token_auth" "$tcp_uplink_source_sha256" "$tcp_uplink_received_sha256" "$tcp_downlink_source_sha256" "$tcp_downlink_received_sha256"
        exit 1
    fi
    log_pass "Client -> Server TCP 小文件传输成功且内容一致"

    if ! run_transfer "tcp_downlink_listener" "$V1_TCP_DOWNLINK_LISTEN_CMD" "$LOG_DIR/tcp_downlink_listener.log" "tcp_downlink_sender" "$V1_TCP_DOWNLINK_SEND_CMD" "$LOG_DIR/tcp_downlink_sender.log"; then
        FAIL_REASON="Server -> Client TCP 小文件传输失败"
        scheduler_grants="$(count_grants "$LOG_DIR/scheduler.log")"
        client_token_auth="$(latest_token_auth_line "$LOG_DIR/client_tx.log")"
        tcp_downlink_received_sha256="$(file_sha256 "$TCP_DOWNLINK_RECEIVED_FILE" 2>/dev/null || true)"
        render_result "FAIL" "$scheduler_grants" "$client_token_auth" "$tcp_uplink_source_sha256" "$tcp_uplink_received_sha256" "$tcp_downlink_source_sha256" "$tcp_downlink_received_sha256"
        exit 1
    fi

    if [ ! -f "$TCP_DOWNLINK_RECEIVED_FILE" ]; then
        FAIL_REASON="下行接收文件未生成"
        scheduler_grants="$(count_grants "$LOG_DIR/scheduler.log")"
        client_token_auth="$(latest_token_auth_line "$LOG_DIR/client_tx.log")"
        render_result "FAIL" "$scheduler_grants" "$client_token_auth" "$tcp_uplink_source_sha256" "$tcp_uplink_received_sha256" "$tcp_downlink_source_sha256" ""
        exit 1
    fi

    tcp_downlink_received_sha256="$(file_sha256 "$TCP_DOWNLINK_RECEIVED_FILE")"
    if [ "$tcp_downlink_source_sha256" != "$tcp_downlink_received_sha256" ]; then
        FAIL_REASON="下行接收文件校验和不匹配"
        scheduler_grants="$(count_grants "$LOG_DIR/scheduler.log")"
        client_token_auth="$(latest_token_auth_line "$LOG_DIR/client_tx.log")"
        render_result "FAIL" "$scheduler_grants" "$client_token_auth" "$tcp_uplink_source_sha256" "$tcp_uplink_received_sha256" "$tcp_downlink_source_sha256" "$tcp_downlink_received_sha256"
        exit 1
    fi
    log_pass "Server -> Client TCP 小文件传输成功且内容一致"

    scheduler_grants="$(count_grants "$LOG_DIR/scheduler.log")"
    client_token_auth="$(latest_token_auth_line "$LOG_DIR/client_tx.log")"
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
        FAIL_REASON="上行 TCP 后 authorized_sends 未增长，无法确认上传走过 Token 门控发送路径"
        render_result "FAIL" "$scheduler_grants" "$client_token_auth" "$tcp_uplink_source_sha256" "$tcp_uplink_received_sha256" "$tcp_downlink_source_sha256" "$tcp_downlink_received_sha256"
        exit 1
    fi

    if grep -q 'TOKEN_AUTH' "$LOG_DIR/server_tx.log" 2>/dev/null; then
        FAIL_REASON="server_tx 输出了 TOKEN_AUTH，说明下行发送路径没有保持常开"
        render_result "FAIL" "$scheduler_grants" "$client_token_auth" "$tcp_uplink_source_sha256" "$tcp_uplink_received_sha256" "$tcp_downlink_source_sha256" "$tcp_downlink_received_sha256"
        exit 1
    fi

    render_result "PASS" "$scheduler_grants" "$client_token_auth" "$tcp_uplink_source_sha256" "$tcp_uplink_received_sha256" "$tcp_downlink_source_sha256" "$tcp_downlink_received_sha256"
    log_pass "v1-mainline 真实硬件 monitor 主线验收脚本执行完成"
}

main "$@"
