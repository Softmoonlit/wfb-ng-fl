#!/bin/bash
# tests/real_hardware/test_full_transfer.sh
# v5 real-hardware 下行补充证据脚本

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [ -f "$PROJECT_ROOT/tests/config/test_config.sh" ]; then
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/tests/config/test_config.sh"
else
    WIFI_IFACE="${WIFI_IFACE:-wlxbcec23372588}"
    WIFI_IFACE_CLIENT="${WIFI_IFACE_CLIENT:-wlxfca386b38672}"
    CHANNEL="${CHANNEL:-157}"
    MCS="${MCS:-0}"
    NODE_ID="${NODE_ID:-1}"
fi

SCENARIO="single"
ANALYZE_ONLY=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --scenario)
            SCENARIO="$2"
            shift 2
            ;;
        --analyze-only)
            ANALYZE_ONLY=true
            shift
            ;;
        --help|-h)
            cat <<'HELP_EOF'
用法:
  sudo bash tests/real_hardware/test_full_transfer.sh [--scenario single|shared] [--analyze-only]

说明:
  - single: 固定的大文件下行完整性补充证据场景
  - shared: 在 single 基础上增加第二个接收端，补齐共享下行/分发相关证据
  - --analyze-only: 不重新执行传输，只对现有 LOG_DIR 产物补采指标与摘要

常用环境变量:
  LOG_DIR                              日志目录
  DOWNLINK_PAYLOAD_SIZE                自动生成 payload 大小，默认 41943040 字节
  DOWNLINK_TRANSFER_TIMEOUT_SEC        最大等待时长，默认 180 秒
  DOWNLINK_SAMPLE_INTERVAL_SEC         采样周期，默认 5 秒
  DOWNLINK_MAX_IDLE_SEC                最大连续无推进窗口，默认 45 秒
  DOWNLINK_RECEIVER_COUNT              接收端数量；single 默认为 1，shared 默认为 2
  DOWNLINK_PAYLOAD_FILE                源 payload 路径，默认 $LOG_DIR/downlink_payload.bin
  DOWNLINK_UFTP_SEND_CMD               自定义发送命令；未设置时使用 uftp
  DOWNLINK_CLIENT1_RECEIVE_CMD         自定义 client1 接收命令；未设置时使用 uftpd
  DOWNLINK_CLIENT2_RECEIVE_CMD         自定义 client2 接收命令；shared 场景可选覆盖
  DOWNLINK_SERVER_TUN_CMD              可选：启动 server TUN 进程
  DOWNLINK_CLIENT1_TUN_CMD             可选：启动 client1 TUN 进程
  DOWNLINK_CLIENT2_TUN_CMD             可选：启动 client2 TUN 进程（shared）
  DOWNLINK_SERVER_TX_CMD               可选：启动 server wfb_tx
  DOWNLINK_SERVER_RX_CMD               可选：启动 server wfb_rx
  DOWNLINK_CLIENT1_TX_CMD              可选：启动 client1 wfb_tx
  DOWNLINK_CLIENT1_RX_CMD              可选：启动 client1 wfb_rx
  DOWNLINK_CLIENT2_TX_CMD              可选：启动 client2 wfb_tx（shared）
  DOWNLINK_CLIENT2_RX_CMD              可选：启动 client2 wfb_rx（shared）
  PCAP_CAPTURE_CMD                     可选：抓包命令；未设置时如本机存在 tcpdump，则对服务端网卡抓包
HELP_EOF
            exit 0
            ;;
        *)
            echo "未知参数: $1" >&2
            exit 1
            ;;
    esac
done

LOG_DIR="${LOG_DIR:-$PROJECT_ROOT/tests/logs/downlink_$(date +%Y%m%d_%H%M%S)}"
DOWNLINK_RESULTS_MD="$LOG_DIR/downlink_results.md"
DOWNLINK_CONTEXT_FILE="$LOG_DIR/downlink_context.txt"
DOWNLINK_SAMPLES_TSV="$LOG_DIR/downlink_samples.tsv"

DOWNLINK_PAYLOAD_SIZE="${DOWNLINK_PAYLOAD_SIZE:-41943040}"
DOWNLINK_TRANSFER_TIMEOUT_SEC="${DOWNLINK_TRANSFER_TIMEOUT_SEC:-180}"
DOWNLINK_SAMPLE_INTERVAL_SEC="${DOWNLINK_SAMPLE_INTERVAL_SEC:-5}"
DOWNLINK_MAX_IDLE_SEC="${DOWNLINK_MAX_IDLE_SEC:-45}"
STARTUP_WAIT_SEC="${STARTUP_WAIT_SEC:-3}"
DOWNLINK_UFTP_RATE_KBPS="${DOWNLINK_UFTP_RATE_KBPS:-50000}"

DOWNLINK_SERVER_TUN_IP="${DOWNLINK_SERVER_TUN_IP:-10.23.0.1}"
DOWNLINK_CLIENT1_TUN_IP="${DOWNLINK_CLIENT1_TUN_IP:-10.23.0.2}"
DOWNLINK_CLIENT2_TUN_IP="${DOWNLINK_CLIENT2_TUN_IP:-10.23.0.3}"

DOWNLINK_UFTP_PUBLIC_MULTICAST_ADDR="${DOWNLINK_UFTP_PUBLIC_MULTICAST_ADDR:-230.4.4.1}"
DOWNLINK_UFTP_PRIVATE_MULTICAST_ADDR="${DOWNLINK_UFTP_PRIVATE_MULTICAST_ADDR:-230.5.5.8}"
DOWNLINK_UFTP_PORT="${DOWNLINK_UFTP_PORT:-1044}"
DOWNLINK_UFTP_SOURCE_PORT="${DOWNLINK_UFTP_SOURCE_PORT:-1045}"

DOWNLINK_PAYLOAD_FILE="${DOWNLINK_PAYLOAD_FILE:-$LOG_DIR/downlink_payload.bin}"
DOWNLINK_CLIENT1_DEST_DIR="${DOWNLINK_CLIENT1_DEST_DIR:-$LOG_DIR/client1/received}"
DOWNLINK_CLIENT2_DEST_DIR="${DOWNLINK_CLIENT2_DEST_DIR:-$LOG_DIR/client2/received}"
DOWNLINK_CLIENT1_TEMP_DIR="${DOWNLINK_CLIENT1_TEMP_DIR:-$LOG_DIR/client1/tmp}"
DOWNLINK_CLIENT2_TEMP_DIR="${DOWNLINK_CLIENT2_TEMP_DIR:-$LOG_DIR/client2/tmp}"
DOWNLINK_CLIENT1_RECEIVED_FILE="${DOWNLINK_CLIENT1_RECEIVED_FILE:-$DOWNLINK_CLIENT1_DEST_DIR/downlink_payload.bin}"
DOWNLINK_CLIENT2_RECEIVED_FILE="${DOWNLINK_CLIENT2_RECEIVED_FILE:-$DOWNLINK_CLIENT2_DEST_DIR/downlink_payload.bin}"

UFTP_SERVER_LOG="${UFTP_SERVER_LOG:-$LOG_DIR/uftp_server.log}"
UFTP_SERVER_STATUS="${UFTP_SERVER_STATUS:-$LOG_DIR/uftp_server.status}"
CLIENT1_UFTPD_LOG="${CLIENT1_UFTPD_LOG:-$LOG_DIR/client1/uftpd.log}"
CLIENT2_UFTPD_LOG="${CLIENT2_UFTPD_LOG:-$LOG_DIR/client2/uftpd.log}"
CLIENT1_UFTPD_STATUS="${CLIENT1_UFTPD_STATUS:-$LOG_DIR/client1/uftpd.status}"
CLIENT2_UFTPD_STATUS="${CLIENT2_UFTPD_STATUS:-$LOG_DIR/client2/uftpd.status}"
CLIENT1_UFTPD_PIDFILE="${CLIENT1_UFTPD_PIDFILE:-$LOG_DIR/client1/uftpd.pid}"
CLIENT2_UFTPD_PIDFILE="${CLIENT2_UFTPD_PIDFILE:-$LOG_DIR/client2/uftpd.pid}"

DOWNLINK_SERVER_LOG="${DOWNLINK_SERVER_LOG:-$LOG_DIR/server.log}"
DOWNLINK_CLIENT1_LOG="${DOWNLINK_CLIENT1_LOG:-$LOG_DIR/client1.log}"
DOWNLINK_CLIENT2_LOG="${DOWNLINK_CLIENT2_LOG:-$LOG_DIR/client2.log}"
PCAP_CAPTURE_LOG="${PCAP_CAPTURE_LOG:-$LOG_DIR/pcap_capture.log}"
CAPTURE_PCAP_FILE="${CAPTURE_PCAP_FILE:-$LOG_DIR/capture.pcap}"

DOWNLINK_SERVER_TUN_CMD="${DOWNLINK_SERVER_TUN_CMD:-}"
DOWNLINK_CLIENT1_TUN_CMD="${DOWNLINK_CLIENT1_TUN_CMD:-}"
DOWNLINK_CLIENT2_TUN_CMD="${DOWNLINK_CLIENT2_TUN_CMD:-}"
DOWNLINK_SERVER_TX_CMD="${DOWNLINK_SERVER_TX_CMD:-}"
DOWNLINK_SERVER_RX_CMD="${DOWNLINK_SERVER_RX_CMD:-}"
DOWNLINK_CLIENT1_TX_CMD="${DOWNLINK_CLIENT1_TX_CMD:-}"
DOWNLINK_CLIENT1_RX_CMD="${DOWNLINK_CLIENT1_RX_CMD:-}"
DOWNLINK_CLIENT2_TX_CMD="${DOWNLINK_CLIENT2_TX_CMD:-}"
DOWNLINK_CLIENT2_RX_CMD="${DOWNLINK_CLIENT2_RX_CMD:-}"
DOWNLINK_UFTP_SEND_CMD="${DOWNLINK_UFTP_SEND_CMD:-}"
DOWNLINK_CLIENT1_RECEIVE_CMD="${DOWNLINK_CLIENT1_RECEIVE_CMD:-}"
DOWNLINK_CLIENT2_RECEIVE_CMD="${DOWNLINK_CLIENT2_RECEIVE_CMD:-}"
PCAP_CAPTURE_CMD="${PCAP_CAPTURE_CMD:-}"
SERVER_WIFI_IFACE="${SERVER_WIFI_IFACE:-${WIFI_IFACE:-}}"

if [ "$SCENARIO" = "shared" ]; then
    DOWNLINK_RECEIVER_COUNT="${DOWNLINK_RECEIVER_COUNT:-2}"
else
    DOWNLINK_RECEIVER_COUNT="${DOWNLINK_RECEIVER_COUNT:-1}"
fi

PIDS=()
RESULT_STATUS="FAIL"
RESULT_REASON="未执行"
DOWNLINK_SOURCE_SHA256="未生成"
CLIENT1_RECEIVED_SHA256="未收到"
CLIENT2_RECEIVED_SHA256="未收到"
SHARED_DISTRIBUTION_CONFIRMED="not_applicable"
SAMPLE_COUNT=0
MAX_IDLE_OBSERVED_SEC=0
STALL_EVENTS=0
ANALYSIS_CHAIN_COMPLETED="no"
TRANSFER_SENDER_PID=""

log_info()  { echo "[INFO] $(date '+%H:%M:%S') $1"; }
log_pass()  { echo "[PASS] $(date '+%H:%M:%S') $1"; }
log_fail()  { echo "[FAIL] $(date '+%H:%M:%S') $1" >&2; }
log_warn()  { echo "[WARN] $(date '+%H:%M:%S') $1" >&2; }

cleanup() {
    local pid
    for pid in "${PIDS[@]:-}"; do
        kill "$pid" 2>/dev/null || true
    done
    wait "${PIDS[@]:-}" 2>/dev/null || true
}
trap cleanup EXIT

prepare_log_dir() {
    mkdir -p "$LOG_DIR" "$DOWNLINK_CLIENT1_DEST_DIR" "$DOWNLINK_CLIENT1_TEMP_DIR"
    if [ "$DOWNLINK_RECEIVER_COUNT" -ge 2 ]; then
        mkdir -p "$DOWNLINK_CLIENT2_DEST_DIR" "$DOWNLINK_CLIENT2_TEMP_DIR"
    fi
}
export LOG_DIR
export DOWNLINK_PAYLOAD_SIZE DOWNLINK_TRANSFER_TIMEOUT_SEC DOWNLINK_SAMPLE_INTERVAL_SEC DOWNLINK_MAX_IDLE_SEC
export DOWNLINK_SERVER_TUN_IP DOWNLINK_CLIENT1_TUN_IP DOWNLINK_CLIENT2_TUN_IP
export DOWNLINK_UFTP_PUBLIC_MULTICAST_ADDR DOWNLINK_UFTP_PRIVATE_MULTICAST_ADDR DOWNLINK_UFTP_PORT DOWNLINK_UFTP_SOURCE_PORT
export DOWNLINK_PAYLOAD_FILE DOWNLINK_CLIENT1_DEST_DIR DOWNLINK_CLIENT2_DEST_DIR DOWNLINK_CLIENT1_TEMP_DIR DOWNLINK_CLIENT2_TEMP_DIR
export DOWNLINK_CLIENT1_RECEIVED_FILE DOWNLINK_CLIENT2_RECEIVED_FILE
export UFTP_SERVER_LOG UFTP_SERVER_STATUS CLIENT1_UFTPD_LOG CLIENT2_UFTPD_LOG CLIENT1_UFTPD_STATUS CLIENT2_UFTPD_STATUS
export CLIENT1_UFTPD_PIDFILE CLIENT2_UFTPD_PIDFILE DOWNLINK_SERVER_LOG DOWNLINK_CLIENT1_LOG DOWNLINK_CLIENT2_LOG
export DOWNLINK_RESULTS_MD DOWNLINK_CONTEXT_FILE DOWNLINK_SAMPLES_TSV CAPTURE_PCAP_FILE PCAP_CAPTURE_LOG
export SCENARIO DOWNLINK_RECEIVER_COUNT

file_sha256() {
    local path="$1"
    if [ ! -f "$path" ]; then
        return 1
    fi
    sha256sum "$path" | cut -d' ' -f1
}

file_size() {
    local path="$1"
    if [ ! -f "$path" ]; then
        echo 0
        return
    fi
    stat -c %s "$path" 2>/dev/null || echo 0
}


dir_tree_size() {
    local dir="$1"
    local total=0
    local path size

    if [ ! -d "$dir" ]; then
        echo 0
        return
    fi

    while IFS= read -r -d '' path; do
        size="$(stat -c %s "$path" 2>/dev/null || echo 0)"
        total=$((total + size))
    done < <(find "$dir" -type f -print0 2>/dev/null)

    echo "$total"
}

receiver_progress_size() {
    local received_file="$1"
    local temp_dir="$2"
    local final_size

    final_size="$(file_size "$received_file")"
    if [ "$final_size" -gt 0 ]; then
        echo "$final_size"
        return
    fi

    dir_tree_size "$temp_dir"
}
ensure_payload_file() {
    if [ -f "$DOWNLINK_PAYLOAD_FILE" ]; then
        DOWNLINK_SOURCE_SHA256="$(file_sha256 "$DOWNLINK_PAYLOAD_FILE")"
        return
    fi

    log_info "生成下行补充证据 payload: $DOWNLINK_PAYLOAD_FILE (${DOWNLINK_PAYLOAD_SIZE} bytes)"
    PAYLOAD_PATH="$DOWNLINK_PAYLOAD_FILE" PAYLOAD_SIZE="$DOWNLINK_PAYLOAD_SIZE" python3 - <<'PY'
import os
path = os.environ['PAYLOAD_PATH']
size = int(os.environ['PAYLOAD_SIZE'])
os.makedirs(os.path.dirname(path), exist_ok=True)
pattern = b'WFB-V5-DOWNLINK-EVIDENCE\n'
with open(path, 'wb') as fh:
    remaining = size
    while remaining > 0:
        chunk = pattern[:remaining] if remaining < len(pattern) else pattern
        fh.write(chunk)
        remaining -= len(chunk)
PY
    DOWNLINK_SOURCE_SHA256="$(file_sha256 "$DOWNLINK_PAYLOAD_FILE")"
}

run_bg() {
    local name="$1"
    local cmd="$2"
    local logfile="$3"
    local pidvar="$4"
    local pid

    log_info "启动 $name"
    bash -lc "$cmd" >"$logfile" 2>&1 &
    pid=$!
    PIDS+=("$pid")
    printf -v "$pidvar" '%s' "$pid"
}

resolve_uftp_binary() {
    local env_name="$1"
    local fallback="$2"
    local resolved=""

    if [ -n "${!env_name:-}" ]; then
        resolved="${!env_name}"
        if [ ! -x "$resolved" ]; then
            log_fail "指定的 UFTP 可执行文件不可执行: $resolved"
            return 1
        fi
    else
        if ! resolved="$(command -v "$fallback" 2>/dev/null)"; then
            log_fail "缺少命令: $fallback"
            return 1
        fi
    fi

    printf '%s\n' "$resolved"
}

build_default_client_receive_cmd() {
    local bind_ip="$1"
    local dest_dir="$2"
    local temp_dir="$3"
    local logfile="$4"
    local status_file="$5"
    local pidfile="$6"

    printf '%q -I %q -M %q -p %q -D %q -T %q -L %q -F %q -P %q' \
        "$RESOLVED_UFTPD_BIN" \
        "$bind_ip" \
        "$DOWNLINK_UFTP_PUBLIC_MULTICAST_ADDR" \
        "$DOWNLINK_UFTP_PORT" \
        "$dest_dir" \
        "$temp_dir" \
        "$logfile" \
        "$status_file" \
        "$pidfile"
}

build_default_uftp_send_cmd() {
    printf 'timeout %qs %q -I %q -M %q -P %q -p %q -u %q -Y none -R %q -L %q -S %q -D %q %q' \
        "$DOWNLINK_TRANSFER_TIMEOUT_SEC" \
        "$RESOLVED_UFTP_BIN" \
        "$DOWNLINK_SERVER_TUN_IP" \
        "$DOWNLINK_UFTP_PUBLIC_MULTICAST_ADDR" \
        "$DOWNLINK_UFTP_PRIVATE_MULTICAST_ADDR" \
        "$DOWNLINK_UFTP_PORT" \
        "$DOWNLINK_UFTP_SOURCE_PORT" \
        "$DOWNLINK_UFTP_RATE_KBPS" \
        "$UFTP_SERVER_LOG" \
        "$UFTP_SERVER_STATUS" \
        "downlink_payload.bin" \
        "$DOWNLINK_PAYLOAD_FILE"
}

start_optional_processes() {
    local started=0

    if [ -n "$DOWNLINK_SERVER_TUN_CMD" ]; then
        run_bg "server_tun" "$DOWNLINK_SERVER_TUN_CMD" "$LOG_DIR/server_tun.log" SERVER_TUN_PID
        started=1
    fi
    if [ -n "$DOWNLINK_CLIENT1_TUN_CMD" ]; then
        run_bg "client1_tun" "$DOWNLINK_CLIENT1_TUN_CMD" "$LOG_DIR/client1_tun.log" CLIENT1_TUN_PID
        started=1
    fi
    if [ -n "$DOWNLINK_SERVER_RX_CMD" ]; then
        run_bg "server_rx" "$DOWNLINK_SERVER_RX_CMD" "$DOWNLINK_SERVER_LOG" SERVER_RX_PID
        started=1
    fi
    if [ -n "$DOWNLINK_SERVER_TX_CMD" ]; then
        run_bg "server_tx" "$DOWNLINK_SERVER_TX_CMD" "$LOG_DIR/server_tx.log" SERVER_TX_PID
        started=1
    fi
    if [ -n "$DOWNLINK_CLIENT1_RX_CMD" ]; then
        run_bg "client1_rx" "$DOWNLINK_CLIENT1_RX_CMD" "$DOWNLINK_CLIENT1_LOG" CLIENT1_RX_PID
        started=1
    fi
    if [ -n "$DOWNLINK_CLIENT1_TX_CMD" ]; then
        run_bg "client1_tx" "$DOWNLINK_CLIENT1_TX_CMD" "$LOG_DIR/client1_tx.log" CLIENT1_TX_PID
        started=1
    fi

    if [ "$DOWNLINK_RECEIVER_COUNT" -ge 2 ]; then
        if [ -n "$DOWNLINK_CLIENT2_TUN_CMD" ]; then
            run_bg "client2_tun" "$DOWNLINK_CLIENT2_TUN_CMD" "$LOG_DIR/client2_tun.log" CLIENT2_TUN_PID
            started=1
        fi
        if [ -n "$DOWNLINK_CLIENT2_RX_CMD" ]; then
            run_bg "client2_rx" "$DOWNLINK_CLIENT2_RX_CMD" "$DOWNLINK_CLIENT2_LOG" CLIENT2_RX_PID
            started=1
        fi
        if [ -n "$DOWNLINK_CLIENT2_TX_CMD" ]; then
            run_bg "client2_tx" "$DOWNLINK_CLIENT2_TX_CMD" "$LOG_DIR/client2_tx.log" CLIENT2_TX_PID
            started=1
        fi
    fi

    if [ "$started" -eq 1 ]; then
        sleep "$STARTUP_WAIT_SEC"
    fi
}

start_capture_if_needed() {
    if [ -n "$PCAP_CAPTURE_CMD" ]; then
        run_bg "pcap_capture" "$PCAP_CAPTURE_CMD" "$PCAP_CAPTURE_LOG" PCAP_PID
        return
    fi

    if [ -n "$SERVER_WIFI_IFACE" ] && command -v tcpdump >/dev/null 2>&1; then
        run_bg "pcap_capture" "tcpdump -i \"$SERVER_WIFI_IFACE\" -w \"$CAPTURE_PCAP_FILE\"" "$PCAP_CAPTURE_LOG" PCAP_PID
    fi
}

start_receivers() {
    local client1_cmd="$DOWNLINK_CLIENT1_RECEIVE_CMD"
    local client2_cmd="$DOWNLINK_CLIENT2_RECEIVE_CMD"

    if [ -z "$client1_cmd" ]; then
        client1_cmd="$(build_default_client_receive_cmd "$DOWNLINK_CLIENT1_TUN_IP" "$DOWNLINK_CLIENT1_DEST_DIR" "$DOWNLINK_CLIENT1_TEMP_DIR" "$CLIENT1_UFTPD_LOG" "$CLIENT1_UFTPD_STATUS" "$CLIENT1_UFTPD_PIDFILE")"
    fi
    run_bg "client1_uftpd" "$client1_cmd" "$CLIENT1_UFTPD_LOG" CLIENT1_UFTPD_PID

    if [ "$DOWNLINK_RECEIVER_COUNT" -ge 2 ]; then
        if [ -z "$client2_cmd" ]; then
            client2_cmd="$(build_default_client_receive_cmd "$DOWNLINK_CLIENT2_TUN_IP" "$DOWNLINK_CLIENT2_DEST_DIR" "$DOWNLINK_CLIENT2_TEMP_DIR" "$CLIENT2_UFTPD_LOG" "$CLIENT2_UFTPD_STATUS" "$CLIENT2_UFTPD_PIDFILE")"
        fi
        run_bg "client2_uftpd" "$client2_cmd" "$CLIENT2_UFTPD_LOG" CLIENT2_UFTPD_PID
    fi

    sleep 1
}

start_sender() {
    local send_cmd="$DOWNLINK_UFTP_SEND_CMD"

    if [ -z "$send_cmd" ]; then
        send_cmd="$(build_default_uftp_send_cmd)"
    fi

    run_bg "uftp_sender" "$send_cmd" "$UFTP_SERVER_LOG" TRANSFER_SENDER_PID
}

payloads_complete() {
    local client1_bytes client2_bytes
    client1_bytes="$(file_size "$DOWNLINK_CLIENT1_RECEIVED_FILE")"
    if [ "$client1_bytes" -lt "$DOWNLINK_PAYLOAD_SIZE" ]; then
        return 1
    fi

    if [ "$DOWNLINK_RECEIVER_COUNT" -ge 2 ]; then
        client2_bytes="$(file_size "$DOWNLINK_CLIENT2_RECEIVED_FILE")"
        if [ "$client2_bytes" -lt "$DOWNLINK_PAYLOAD_SIZE" ]; then
            return 1
        fi
    fi

    return 0
}

monitor_transfer_progress() {
    local start_epoch now elapsed client1_bytes client2_bytes last_client1 last_client2 last_progress_epoch progressed idle_sec in_stall

    last_client1=-1
    last_client2=-1
    in_stall=0
    start_epoch="$(date +%s)"
    last_progress_epoch="$start_epoch"

    cat > "$DOWNLINK_SAMPLES_TSV" <<'EOF'
elapsed_sec	client1_bytes	client2_bytes	progressed	idle_sec
EOF

    while true; do
        now="$(date +%s)"
        elapsed=$((now - start_epoch))
        client1_bytes="$(receiver_progress_size "$DOWNLINK_CLIENT1_RECEIVED_FILE" "$DOWNLINK_CLIENT1_TEMP_DIR")"
        client2_bytes=0
        if [ "$DOWNLINK_RECEIVER_COUNT" -ge 2 ]; then
            client2_bytes="$(receiver_progress_size "$DOWNLINK_CLIENT2_RECEIVED_FILE" "$DOWNLINK_CLIENT2_TEMP_DIR")"
        fi

        progressed="NO"
        if [ "$client1_bytes" -ne "$last_client1" ] || [ "$client2_bytes" -ne "$last_client2" ]; then
            progressed="YES"
            last_progress_epoch="$now"
            in_stall=0
        fi

        idle_sec=$((now - last_progress_epoch))
        if [ "$idle_sec" -gt "$MAX_IDLE_OBSERVED_SEC" ]; then
            MAX_IDLE_OBSERVED_SEC="$idle_sec"
        fi

        if [ "$idle_sec" -ge "$DOWNLINK_MAX_IDLE_SEC" ] && [ "$progressed" = "NO" ] && [ "$in_stall" -eq 0 ]; then
            STALL_EVENTS=$((STALL_EVENTS + 1))
            in_stall=1
        fi

        echo -e "${elapsed}\t${client1_bytes}\t${client2_bytes}\t${progressed}\t${idle_sec}" >> "$DOWNLINK_SAMPLES_TSV"
        SAMPLE_COUNT=$((SAMPLE_COUNT + 1))

        if payloads_complete; then
            return 0
        fi

        if [ "$idle_sec" -gt "$DOWNLINK_MAX_IDLE_SEC" ]; then
            RESULT_REASON="连续无推进窗口超过 ${DOWNLINK_MAX_IDLE_SEC} 秒"
            return 1
        fi

        if [ "$elapsed" -ge "$DOWNLINK_TRANSFER_TIMEOUT_SEC" ]; then
            RESULT_REASON="下行补充场景在 ${DOWNLINK_TRANSFER_TIMEOUT_SEC} 秒内未完成"
            return 1
        fi

        last_client1="$client1_bytes"
        last_client2="$client2_bytes"
        sleep "$DOWNLINK_SAMPLE_INTERVAL_SEC"
    done
}

verify_received_payloads() {
    if [ ! -f "$DOWNLINK_CLIENT1_RECEIVED_FILE" ]; then
        RESULT_REASON="client1 未生成接收文件"
        return 1
    fi

    CLIENT1_RECEIVED_SHA256="$(file_sha256 "$DOWNLINK_CLIENT1_RECEIVED_FILE")"
    if [ "$DOWNLINK_SOURCE_SHA256" != "$CLIENT1_RECEIVED_SHA256" ]; then
        RESULT_REASON="client1 接收文件校验和不匹配"
        return 1
    fi

    if [ "$DOWNLINK_RECEIVER_COUNT" -ge 2 ]; then
        if [ ! -f "$DOWNLINK_CLIENT2_RECEIVED_FILE" ]; then
            RESULT_REASON="client2 未生成接收文件"
            return 1
        fi

        CLIENT2_RECEIVED_SHA256="$(file_sha256 "$DOWNLINK_CLIENT2_RECEIVED_FILE")"
        if [ "$DOWNLINK_SOURCE_SHA256" != "$CLIENT2_RECEIVED_SHA256" ]; then
            RESULT_REASON="client2 接收文件校验和不匹配"
            return 1
        fi
        SHARED_DISTRIBUTION_CONFIRMED="yes"
    else
        SHARED_DISTRIBUTION_CONFIRMED="not_applicable"
    fi

    return 0
}

shared_distribution_status() {
    if [ "$DOWNLINK_RECEIVER_COUNT" -lt 2 ]; then
        echo "N/A"
        return
    fi

    if [ "$SHARED_DISTRIBUTION_CONFIRMED" = "yes" ]; then
        echo "PASS"
    else
        echo "FAIL"
    fi
}

render_context() {
    cat > "$DOWNLINK_CONTEXT_FILE" <<EOF
scenario=$SCENARIO
analyze_only=$ANALYZE_ONLY
log_dir=$LOG_DIR
receiver_count=$DOWNLINK_RECEIVER_COUNT
payload_size_bytes=$DOWNLINK_PAYLOAD_SIZE
payload_file=$DOWNLINK_PAYLOAD_FILE
client1_received_file=$DOWNLINK_CLIENT1_RECEIVED_FILE
client2_received_file=$DOWNLINK_CLIENT2_RECEIVED_FILE
source_sha256=$DOWNLINK_SOURCE_SHA256
client1_sha256=$CLIENT1_RECEIVED_SHA256
client2_sha256=$CLIENT2_RECEIVED_SHA256
sample_file=$DOWNLINK_SAMPLES_TSV
sample_count=$SAMPLE_COUNT
max_idle_threshold_sec=$DOWNLINK_MAX_IDLE_SEC
max_idle_observed_sec=$MAX_IDLE_OBSERVED_SEC
stall_events=$STALL_EVENTS
result_status=$RESULT_STATUS
result_reason=$RESULT_REASON
shared_distribution_expected=$([ "$DOWNLINK_RECEIVER_COUNT" -ge 2 ] && echo yes || echo no)
shared_distribution_confirmed=$SHARED_DISTRIBUTION_CONFIRMED
uftp_server_log=$UFTP_SERVER_LOG
client1_uftpd_log=$CLIENT1_UFTPD_LOG
client2_uftpd_log=$CLIENT2_UFTPD_LOG
pcap_file=$CAPTURE_PCAP_FILE
pcap_log=$PCAP_CAPTURE_LOG
analysis_chain_completed=$ANALYSIS_CHAIN_COMPLETED
EOF
}

render_result() {
    local shared_status
    shared_status="$(shared_distribution_status)"

    cat > "$DOWNLINK_RESULTS_MD" <<EOF
# v5 real-hardware 下行补充证据结果

- 结果: $RESULT_STATUS
- 场景: $SCENARIO
- 原因: $RESULT_REASON
- 日志目录: $LOG_DIR
- payload size: $DOWNLINK_PAYLOAD_SIZE bytes
- receiver_count: $DOWNLINK_RECEIVER_COUNT
- source sha256: $DOWNLINK_SOURCE_SHA256
- client1 sha256: $CLIENT1_RECEIVED_SHA256
- client2 sha256: $CLIENT2_RECEIVED_SHA256
- 共享下行/分发证据: $shared_status
- 样本文件: $DOWNLINK_SAMPLES_TSV
- 样本数: $SAMPLE_COUNT
- 最大连续无推进窗口: ${MAX_IDLE_OBSERVED_SEC} 秒
- stall_events: $STALL_EVENTS

## 与双客户端上行 long-run 的分工

- 本场景用于大文件下行完整性、共享下行/分发补充证据，以及下行负载下可见堵塞现象采样。
- 双客户端 Token-gated 上行 long-run 仍是 real-hardware 主场景；本场景不替代其长稳结论。

## 自动判定覆盖

- 大文件下行完整性：以源文件与接收文件 SHA256 一致为准。
- 共享下行/分发证据：仅在 shared 场景下要求两个接收端都收到同一 payload。
- 可见堵塞采样：记录 downlink_samples.tsv，用连续无推进窗口替代内部队列观测。

## 人工判读关注点

- 结合 downlink_samples.tsv、uftp_server.log 与接收端日志，判断时延抬升、重传增加、恢复变慢是否显著恶化。
- 若自动判定为 PASS，但样本中出现接近门槛的平台期，仍需人工说明其上下文。
- 若启用了抓包，请将 capture.pcap 与摘要结论一起归档到 issue / tracking issue。

## 正式归档要求

- 原始产物层至少保留 \`downlink_results.md\`、\`downlink_context.txt\`、\`downlink_samples.tsv\`、\`metrics.json\`、\`summary.txt\` 与关键日志/抓包。
- 正式结论层必须回填到 issue / tracking issue，不能只把日志目录留在本地。
- Issue 回填最小字段：运行场景与前置条件、原始产物层引用、运行时长与关键事件计数、关键异常与风险信号、是否可作为正式 \`v5\` 基线证据、是否需要重跑。
- tracking issue 未按固定模板回填正式结论前不得关闭。


## 关键产物

- $DOWNLINK_CONTEXT_FILE
- $DOWNLINK_SAMPLES_TSV
- $UFTP_SERVER_LOG
- $CLIENT1_UFTPD_LOG
- $CLIENT2_UFTPD_LOG
EOF
}

run_analysis_chain() {
    export LOG_DIR DOWNLINK_CONTEXT_FILE DOWNLINK_SAMPLES_TSV UFTP_SERVER_LOG CLIENT1_UFTPD_LOG CLIENT2_UFTPD_LOG
    export DOWNLINK_SERVER_LOG DOWNLINK_CLIENT1_LOG DOWNLINK_CLIENT2_LOG CAPTURE_PCAP_FILE

    # shellcheck disable=SC1091
    source "$SCRIPT_DIR/collect_metrics.sh"
    collect_all_metrics

    # shellcheck disable=SC1091
    source "$SCRIPT_DIR/generate_report.sh"
    generate_report
}

finalize() {
    render_context
    render_result

    if run_analysis_chain; then
        ANALYSIS_CHAIN_COMPLETED="yes"
        render_context
        render_result
    else
        ANALYSIS_CHAIN_COMPLETED="no"
        if [ "$RESULT_STATUS" = "PASS" ]; then
            RESULT_STATUS="FAIL"
            RESULT_REASON="指标采集或摘要生成失败"
        fi
        render_context
        render_result
    fi

    if [ "$RESULT_STATUS" = "PASS" ]; then
        log_pass "下行补充证据完成。日志目录: $LOG_DIR"
        exit 0
    fi

    log_fail "下行补充证据失败。日志目录: $LOG_DIR"
    exit 1
}

main() {
    prepare_log_dir

    if [ "$DOWNLINK_RECEIVER_COUNT" -lt 1 ] || [ "$DOWNLINK_RECEIVER_COUNT" -gt 2 ]; then
        RESULT_REASON="DOWNLINK_RECEIVER_COUNT 仅支持 1 或 2"
        finalize
    fi

    if [ "$ANALYZE_ONLY" = true ]; then
        if [ -f "$DOWNLINK_CONTEXT_FILE" ]; then
            RESULT_STATUS="$(awk -F= '/^result_status=/{print substr($0, index($0, "=")+1)}' "$DOWNLINK_CONTEXT_FILE" | tail -1)"
            RESULT_REASON="$(awk -F= '/^result_reason=/{print substr($0, index($0, "=")+1)}' "$DOWNLINK_CONTEXT_FILE" | tail -1)"
        else
            RESULT_STATUS="FAIL"
            RESULT_REASON="--analyze-only 缺少现有 downlink_context.txt"
        fi
        finalize
    fi

    if [ -z "$DOWNLINK_UFTP_SEND_CMD" ] || [ -z "$DOWNLINK_CLIENT1_RECEIVE_CMD" ] || { [ "$DOWNLINK_RECEIVER_COUNT" -ge 2 ] && [ -z "$DOWNLINK_CLIENT2_RECEIVE_CMD" ]; }; then
        RESOLVED_UFTP_BIN="$(resolve_uftp_binary UFTP_BIN uftp)"
        RESOLVED_UFTPD_BIN="$(resolve_uftp_binary UFTPD_BIN uftpd)"
    else
        RESOLVED_UFTP_BIN="custom"
        RESOLVED_UFTPD_BIN="custom"
    fi

    ensure_payload_file
    start_optional_processes
    start_capture_if_needed
    start_receivers
    start_sender

    if monitor_transfer_progress && wait "$TRANSFER_SENDER_PID" && verify_received_payloads; then
        RESULT_STATUS="PASS"
        RESULT_REASON="大文件下行与补充证据满足固定口径"
    else
        local primary_reason="${RESULT_REASON:-}"
        if [ -z "$primary_reason" ] || [ "$primary_reason" = "未执行" ]; then
            primary_reason="下行补充证据执行失败"
        fi
        verify_received_payloads || true
        RESULT_REASON="$primary_reason"
        RESULT_STATUS="FAIL"
    fi

    finalize
}

main "$@"
