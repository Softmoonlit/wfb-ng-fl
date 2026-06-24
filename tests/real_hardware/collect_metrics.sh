#!/bin/bash
# tests/real_hardware/collect_metrics.sh
# v5 real-hardware 指标收集脚本

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [ -f "$PROJECT_ROOT/tests/config/test_config.sh" ]; then
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/tests/config/test_config.sh"
fi

LOG_DIR="${LOG_DIR:-$PROJECT_ROOT/tests/logs}"
DOWNLINK_CONTEXT_FILE="${DOWNLINK_CONTEXT_FILE:-$LOG_DIR/downlink_context.txt}"
DOWNLINK_SAMPLES_TSV="${DOWNLINK_SAMPLES_TSV:-$LOG_DIR/downlink_samples.tsv}"
UFTP_SERVER_LOG="${UFTP_SERVER_LOG:-$LOG_DIR/uftp_server.log}"
CLIENT1_UFTPD_LOG="${CLIENT1_UFTPD_LOG:-$LOG_DIR/client1/uftpd.log}"
CLIENT2_UFTPD_LOG="${CLIENT2_UFTPD_LOG:-$LOG_DIR/client2/uftpd.log}"
DOWNLINK_SERVER_LOG="${DOWNLINK_SERVER_LOG:-$LOG_DIR/server.log}"
DOWNLINK_CLIENT1_LOG="${DOWNLINK_CLIENT1_LOG:-$LOG_DIR/client1.log}"
DOWNLINK_CLIENT2_LOG="${DOWNLINK_CLIENT2_LOG:-$LOG_DIR/client2.log}"

log_info()  { echo "[INFO] $(date '+%H:%M:%S') $1"; }
log_pass()  { echo "[PASS] $(date '+%H:%M:%S') $1"; }
log_fail()  { echo "[FAIL] $(date '+%H:%M:%S') $1" >&2; }
log_warn()  { echo "[WARN] $(date '+%H:%M:%S') $1" >&2; }

context_value() {
    local key="$1"
    local default_value="${2:-}"

    if [ ! -f "$DOWNLINK_CONTEXT_FILE" ]; then
        printf '%s\n' "$default_value"
        return
    fi

    awk -F= -v key="$key" '$1 == key { print substr($0, index($0, "=") + 1) }' "$DOWNLINK_CONTEXT_FILE" | tail -1 || printf '%s\n' "$default_value"
}

safe_count() {
    local pattern="$1"
    shift
    local file count total
    total=0
    for file in "$@"; do
        if [ -f "$file" ]; then
            count=$(grep -Eci "$pattern" "$file" 2>/dev/null || true)
            total=$((total + count))
        fi
    done
    printf '%s\n' "$total"
}

parse_last_number() {
    local pattern="$1"
    local regex="$2"
    local file="$3"

    if [ ! -f "$file" ]; then
        return
    fi

    grep -Ei "$pattern" "$file" 2>/dev/null | tail -1 | grep -Eo "$regex" | tail -1 || true
}

bool_json() {
    case "$1" in
        yes|true|1) echo true ;;
        *) echo false ;;
    esac
}

collect_app_metrics() {
    log_info "收集应用层指标..."

    UFTP_DURATION=$(parse_last_number 'Transfer complete|传输完成|duration|耗时' '[0-9]+([.][0-9]+)?' "$UFTP_SERVER_LOG")
    UFTP_RETRIES=$(parse_last_number 'retries|重传次数' '[0-9]+' "$UFTP_SERVER_LOG")
    TRANSFER_STATUS="$(context_value result_status unknown)"
    TRANSFER_REASON="$(context_value result_reason 未知)"

    if [ -z "$UFTP_DURATION" ]; then
        UFTP_DURATION="null"
    fi
    if [ -z "$UFTP_RETRIES" ]; then
        UFTP_RETRIES=0
    fi

    UFTP_WARNING_COUNT=$(safe_count 'warn|warning|retransmit|retry burst|stall|timeout' "$UFTP_SERVER_LOG" "$CLIENT1_UFTPD_LOG" "$CLIENT2_UFTPD_LOG")
    UFTP_ERROR_COUNT=$(safe_count 'error|fail|失败|超时|timed out' "$UFTP_SERVER_LOG" "$CLIENT1_UFTPD_LOG" "$CLIENT2_UFTPD_LOG")

    log_info "  传输状态: ${TRANSFER_STATUS}"
    log_info "  传输原因: ${TRANSFER_REASON}"
    log_info "  传输耗时: ${UFTP_DURATION}"
    log_info "  重传次数: ${UFTP_RETRIES}"
}

collect_os_metrics() {
    log_info "收集操作系统层指标..."

    local tun_dev="wfb0"
    if [ ! -e "/sys/class/net/$tun_dev/statistics/tx_dropped" ]; then
        TX_DROPPED=0
        TX_ERRORS=0
        TX_PACKETS=0
        return
    fi

    TX_DROPPED=$(cat "/sys/class/net/$tun_dev/statistics/tx_dropped" 2>/dev/null || echo 0)
    TX_ERRORS=$(cat "/sys/class/net/$tun_dev/statistics/tx_errors" 2>/dev/null || echo 0)
    TX_PACKETS=$(cat "/sys/class/net/$tun_dev/statistics/tx_packets" 2>/dev/null || echo 0)
}

collect_mac_metrics() {
    log_info "收集 MAC 层指标..."

    TOKENS_SENT=$(safe_count 'Token 发送|Token sent' "$DOWNLINK_SERVER_LOG")
    TOKENS_RECEIVED=$(safe_count 'Token 接收|Token received' "$DOWNLINK_CLIENT1_LOG" "$DOWNLINK_CLIENT2_LOG")
    PACKETS_SENT=$(safe_count '发送数据包|Packet sent|PKT' "$DOWNLINK_SERVER_LOG")
    PACKETS_RECEIVED=$(safe_count '接收数据包|Packet received|PKT' "$DOWNLINK_CLIENT1_LOG" "$DOWNLINK_CLIENT2_LOG")
}

collect_token_metrics() {
    log_info "收集 Token 验收指标..."

    local scheduler_log="$LOG_DIR/scheduler.log"
    local client1_log="$LOG_DIR/client1.log"
    local client2_log="$LOG_DIR/client2.log"

    TOKEN_GRANTS=$(grep -c '^grant seq=' "$scheduler_log" 2>/dev/null || echo 0)
    TOKEN_GUARDS=$(grep -c '^guard seq=' "$scheduler_log" 2>/dev/null || echo 0)
    CLIENT1_TOKEN_FILTER=$(grep 'TOKEN_FILTER' "$client1_log" 2>/dev/null | tail -1 | sed 's/"/\\"/g' || true)
    CLIENT1_TOKEN_AUTH=$(grep 'TOKEN_AUTH' "$client1_log" 2>/dev/null | tail -1 | sed 's/"/\\"/g' || true)
    CLIENT2_TOKEN_FILTER=$(grep 'TOKEN_FILTER' "$client2_log" 2>/dev/null | tail -1 | sed 's/"/\\"/g' || true)
    CLIENT2_TOKEN_AUTH=$(grep 'TOKEN_AUTH' "$client2_log" 2>/dev/null | tail -1 | sed 's/"/\\"/g' || true)
}

collect_downlink_metrics() {
    log_info "收集下行补充证据指标..."

    DOWNLINK_SCENARIO="$(context_value scenario unknown)"
    DOWNLINK_RECEIVER_COUNT="$(context_value receiver_count 0)"
    DOWNLINK_PAYLOAD_SIZE_BYTES="$(context_value payload_size_bytes 0)"
    DOWNLINK_SOURCE_SHA256="$(context_value source_sha256 '')"
    DOWNLINK_CLIENT1_SHA256="$(context_value client1_sha256 '')"
    DOWNLINK_CLIENT2_SHA256="$(context_value client2_sha256 '')"
    DOWNLINK_SHARED_CONFIRMED="$(context_value shared_distribution_confirmed no)"
    DOWNLINK_MAX_IDLE_THRESHOLD_SEC="$(context_value max_idle_threshold_sec 0)"
    if [ "$DOWNLINK_SCENARIO" = "shared" ]; then
        DOWNLINK_SCENARIO_ID="v6_real_hardware_downlink_shared_uftp_feedback"
        DOWNLINK_FEEDBACK_WINDOW_COVERED=true
    else
        DOWNLINK_SCENARIO_ID="v6_real_hardware_downlink_single_uftp"
        DOWNLINK_FEEDBACK_WINDOW_COVERED=false
    fi
    DOWNLINK_RESULT_STATUS="$(context_value result_status unknown)"
    DOWNLINK_RESULT_REASON="$(context_value result_reason 未知)"

    DOWNLINK_SAMPLE_COUNT=0
    DOWNLINK_MAX_IDLE_OBSERVED=0
    DOWNLINK_STALL_EVENTS=0
    DOWNLINK_CLIENT1_FINAL_BYTES=0
    DOWNLINK_CLIENT2_FINAL_BYTES=0

    if [ -f "$DOWNLINK_SAMPLES_TSV" ]; then
        DOWNLINK_SAMPLE_COUNT=$(awk 'NR > 1 { count++ } END { print count + 0 }' "$DOWNLINK_SAMPLES_TSV")
        DOWNLINK_MAX_IDLE_OBSERVED=$(awk 'NR > 1 && $5 + 0 > max { max = $5 + 0 } END { print max + 0 }' "$DOWNLINK_SAMPLES_TSV")
        DOWNLINK_STALL_EVENTS=$(awk -F'\t' -v threshold="$DOWNLINK_MAX_IDLE_THRESHOLD_SEC" '
            NR > 1 {
                stalled = ($5 + 0 >= threshold) && ($4 == "NO")
                if (stalled && !prev) {
                    count++
                }
                prev = stalled
            }
            END { print count + 0 }
        ' "$DOWNLINK_SAMPLES_TSV")
        DOWNLINK_CLIENT1_FINAL_BYTES=$(awk 'NR > 1 { val = $2 + 0 } END { print val + 0 }' "$DOWNLINK_SAMPLES_TSV")
        DOWNLINK_CLIENT2_FINAL_BYTES=$(awk 'NR > 1 { val = $3 + 0 } END { print val + 0 }' "$DOWNLINK_SAMPLES_TSV")
    fi

    DOWNLINK_WARNING_COUNT=$(safe_count 'warn|warning|retransmit|retry|stall|timeout|remove|evict|drop|lost' "$UFTP_SERVER_LOG" "$CLIENT1_UFTPD_LOG" "$CLIENT2_UFTPD_LOG" "$DOWNLINK_SERVER_LOG" "$DOWNLINK_CLIENT1_LOG" "$DOWNLINK_CLIENT2_LOG")
    DOWNLINK_ERROR_COUNT=$(safe_count 'error|fail|失败|超时|timed out' "$UFTP_SERVER_LOG" "$CLIENT1_UFTPD_LOG" "$CLIENT2_UFTPD_LOG" "$DOWNLINK_SERVER_LOG" "$DOWNLINK_CLIENT1_LOG" "$DOWNLINK_CLIENT2_LOG")
    DOWNLINK_ANOMALY_COUNT=$(safe_count 'drop|lost|overflow|stall|timeout|rejoin|remove|evict' "$UFTP_SERVER_LOG" "$CLIENT1_UFTPD_LOG" "$CLIENT2_UFTPD_LOG" "$DOWNLINK_SERVER_LOG" "$DOWNLINK_CLIENT1_LOG" "$DOWNLINK_CLIENT2_LOG")

    if [ "$DOWNLINK_SCENARIO" = "shared" ] && [ "$DOWNLINK_SHARED_CONFIRMED" = "yes" ]; then
        SHARED_DISTRIBUTION_JSON=true
    else
        SHARED_DISTRIBUTION_JSON=false
    fi

    log_info "  场景: ${DOWNLINK_SCENARIO}"
    log_info "  接收端数量: ${DOWNLINK_RECEIVER_COUNT}"
    log_info "  样本数: ${DOWNLINK_SAMPLE_COUNT}"
    log_info "  最大无推进窗口: ${DOWNLINK_MAX_IDLE_OBSERVED}s"
    log_info "  stall_events: ${DOWNLINK_STALL_EVENTS}"
}

generate_metrics_json() {
    log_info "生成指标文件..."

    cat > "$LOG_DIR/metrics.json" <<EOF
{
  "timestamp": "$(date -Iseconds)",
  "formal_2a": {
    "run_kind": "real_hardware",
    "link_security_mode": "trusted_plaintext",
    "scenario_id": "${DOWNLINK_SCENARIO_ID:-v6_real_hardware_downlink_single_uftp}",
    "feedback_window_covered": ${DOWNLINK_FEEDBACK_WINDOW_COVERED:-false},
    "summary_file": "${RUN_SUMMARY_JSON:-$LOG_DIR/formal_2a_summary.json}"
  },
  "test_config": {
    "wifi_iface": "${WIFI_IFACE:-}",
    "channel": ${CHANNEL:-0},
    "mcs": ${MCS:-0},
    "node_id": ${NODE_ID:-0},
    "scenario": "${DOWNLINK_SCENARIO}",
    "receiver_count": ${DOWNLINK_RECEIVER_COUNT:-0},
    "payload_size_bytes": ${DOWNLINK_PAYLOAD_SIZE_BYTES:-0},
    "multicast_public": "${DOWNLINK_UFTP_PUBLIC_MULTICAST_ADDR:-}",
    "multicast_private": "${DOWNLINK_UFTP_PRIVATE_MULTICAST_ADDR:-}"
  },
  "application": {
    "transfer_duration_s": ${UFTP_DURATION},
    "retries": ${UFTP_RETRIES:-0},
    "status": "${TRANSFER_STATUS}",
    "reason": "${TRANSFER_REASON//"/\\\"}"
  },
  "visible_congestion": {
    "sample_file": "${DOWNLINK_SAMPLES_TSV}",
    "sample_count": ${DOWNLINK_SAMPLE_COUNT:-0},
    "max_idle_threshold_sec": ${DOWNLINK_MAX_IDLE_THRESHOLD_SEC:-0},
    "max_idle_observed_sec": ${DOWNLINK_MAX_IDLE_OBSERVED:-0},
    "stall_events": ${DOWNLINK_STALL_EVENTS:-0},
    "warning_count": ${DOWNLINK_WARNING_COUNT:-0},
    "error_count": ${DOWNLINK_ERROR_COUNT:-0},
    "anomaly_count": ${DOWNLINK_ANOMALY_COUNT:-0},
    "manual_review_required": true,
    "manual_review_focus": [
      "检查时延抬升与重传增加是否显著恶化。",
      "检查样本文件中的平台期是否接近或超过门槛。",
      "检查异常是否能从 server/client/UFTP 日志中得到解释。"
    ]
  },
  "downlink_evidence": {
    "receiver_count": ${DOWNLINK_RECEIVER_COUNT:-0},
    "shared_distribution_confirmed": ${SHARED_DISTRIBUTION_JSON:-false},
    "source_sha256": "${DOWNLINK_SOURCE_SHA256}",
    "client1_sha256": "${DOWNLINK_CLIENT1_SHA256}",
    "client2_sha256": "${DOWNLINK_CLIENT2_SHA256}",
    "client1_final_bytes": ${DOWNLINK_CLIENT1_FINAL_BYTES:-0},
    "client2_final_bytes": ${DOWNLINK_CLIENT2_FINAL_BYTES:-0},
    "result_status": "${DOWNLINK_RESULT_STATUS}",
    "result_reason": "${DOWNLINK_RESULT_REASON//"/\\\"}"
  },
  "os": {
    "tun_tx_dropped": ${TX_DROPPED:-0},
    "tun_tx_errors": ${TX_ERRORS:-0},
    "tun_tx_packets": ${TX_PACKETS:-0}
  },
  "mac": {
    "tokens_sent": ${TOKENS_SENT:-0},
    "tokens_received": ${TOKENS_RECEIVED:-0},
    "packets_sent": ${PACKETS_SENT:-0},
    "packets_received": ${PACKETS_RECEIVED:-0}
  },
  "token_validation": {
    "scheduler_grants": ${TOKEN_GRANTS:-0},
    "scheduler_guards": ${TOKEN_GUARDS:-0},
    "client1_token_filter": "${CLIENT1_TOKEN_FILTER:-}",
    "client1_token_auth": "${CLIENT1_TOKEN_AUTH:-}",
    "client2_token_filter": "${CLIENT2_TOKEN_FILTER:-}",
    "client2_token_auth": "${CLIENT2_TOKEN_AUTH:-}"
  }
}
EOF

    log_pass "指标已保存到 $LOG_DIR/metrics.json"
}

print_metrics_summary() {
    echo ""
    echo "========================================"
    echo "  指标收集摘要"
    echo "========================================"
    echo "场景: ${DOWNLINK_SCENARIO}"
    echo "接收端数量: ${DOWNLINK_RECEIVER_COUNT}"
    echo "传输结果: ${DOWNLINK_RESULT_STATUS}"
    echo "传输原因: ${DOWNLINK_RESULT_REASON}"
    echo "传输耗时: ${UFTP_DURATION}"
    echo "重传次数: ${UFTP_RETRIES}"
    echo "样本数: ${DOWNLINK_SAMPLE_COUNT}"
    echo "最大无推进窗口: ${DOWNLINK_MAX_IDLE_OBSERVED}s"
    echo "stall_events: ${DOWNLINK_STALL_EVENTS}"
    echo "warning_count: ${DOWNLINK_WARNING_COUNT}"
    echo "error_count: ${DOWNLINK_ERROR_COUNT}"
    echo "========================================"
}

collect_all_metrics() {
    if [ ! -d "$LOG_DIR" ]; then
        log_fail "日志目录不存在: $LOG_DIR"
        return 1
    fi

    collect_app_metrics
    collect_os_metrics
    collect_mac_metrics
    collect_token_metrics
    collect_downlink_metrics
    generate_metrics_json
    print_metrics_summary
    log_pass "指标收集完成"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    collect_all_metrics
fi
