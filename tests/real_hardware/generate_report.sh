#!/bin/bash
# tests/real_hardware/generate_report.sh
# v5 real-hardware 摘要报告脚本

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [ -f "$PROJECT_ROOT/tests/config/test_config.sh" ]; then
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/tests/config/test_config.sh"
fi

LOG_DIR="${LOG_DIR:-$PROJECT_ROOT/tests/logs}"
DOWNLINK_CONTEXT_FILE="${DOWNLINK_CONTEXT_FILE:-$LOG_DIR/downlink_context.txt}"
METRICS_FILE="$LOG_DIR/metrics.json"
SUMMARY_FILE="$LOG_DIR/summary.txt"

log_info()  { echo "[INFO] $(date '+%H:%M:%S') $1"; }
log_pass()  { echo "[PASS] $(date '+%H:%M:%S') $1"; }
log_fail()  { echo "[FAIL] $(date '+%H:%M:%S') $1" >&2; }

context_value() {
    local key="$1"
    local default_value="${2:-}"

    if [ ! -f "$DOWNLINK_CONTEXT_FILE" ]; then
        printf '%s\n' "$default_value"
        return
    fi

    awk -F= -v key="$key" '$1 == key { print substr($0, index($0, "=") + 1) }' "$DOWNLINK_CONTEXT_FILE" | tail -1 || printf '%s\n' "$default_value"
}

json_number() {
    local key="$1"
    local line value

    if [ ! -f "$METRICS_FILE" ]; then
        echo "N/A"
        return
    fi

    line=$(grep -E '"'"$key"'"' "$METRICS_FILE" | head -1 || true)
    if [ -z "$line" ]; then
        echo "N/A"
        return
    fi

    value=$(printf '%s\n' "$line" | grep -Eo ': (null|true|false|[0-9]+([.][0-9]+)?)' | sed 's/^: //' || true)
    if [ -z "$value" ]; then
        echo "N/A"
    else
        echo "$value"
    fi
}

json_string() {
    local key="$1"
    local line value

    if [ ! -f "$METRICS_FILE" ]; then
        echo ""
        return
    fi

    line=$(grep -E '"'"$key"'"' "$METRICS_FILE" | head -1 || true)
    if [ -z "$line" ]; then
        echo ""
        return
    fi

    value=$(printf '%s\n' "$line" | sed -E 's/.*: "(.*)".*/\1/' || true)
    echo "$value"
}

shared_distribution_summary() {
    local scenario receiver_count confirmed
    scenario="$(context_value scenario unknown)"
    receiver_count="$(context_value receiver_count 0)"
    confirmed="$(context_value shared_distribution_confirmed no)"

    if [ "$scenario" = "single" ] || [ "$receiver_count" -lt 2 ]; then
        echo 'N/A（single 场景）'
        return
    fi

    if [ "$confirmed" = "yes" ]; then
        echo 'PASS'
    else
        echo 'FAIL'
    fi
}

generate_summary_file() {
    local scenario result_status result_reason receiver_count payload_size source_sha client1_sha client2_sha
    local transfer_duration retries max_idle stall_events warning_count error_count anomaly_count token_line shared_summary sample_file

    scenario="$(context_value scenario unknown)"
    result_status="$(context_value result_status unknown)"
    result_reason="$(context_value result_reason 未知)"
    receiver_count="$(context_value receiver_count 0)"
    payload_size="$(context_value payload_size_bytes 0)"
    source_sha="$(context_value source_sha256 未生成)"
    client1_sha="$(context_value client1_sha256 未收到)"
    client2_sha="$(context_value client2_sha256 未收到)"
    sample_file="$(context_value sample_file "$LOG_DIR/downlink_samples.tsv")"

    transfer_duration="$(json_number transfer_duration_s)"
    retries="$(json_number retries)"
    max_idle="$(json_number max_idle_observed_sec)"
    stall_events="$(json_number stall_events)"
    warning_count="$(json_number warning_count)"
    error_count="$(json_number error_count)"
    anomaly_count="$(json_number anomaly_count)"
    shared_summary="$(shared_distribution_summary)"

    token_line='Token 验收: N/A（本次未运行）'
    if [ -f "$LOG_DIR/token_results.tsv" ]; then
        local token_pass token_fail token_skip
        token_pass=$(grep -c $'\tPASS\t' "$LOG_DIR/token_results.tsv" 2>/dev/null || echo 0)
        token_fail=$(grep -c $'\tFAIL\t' "$LOG_DIR/token_results.tsv" 2>/dev/null || echo 0)
        token_skip=$(grep -c $'\tSKIP\t' "$LOG_DIR/token_results.tsv" 2>/dev/null || echo 0)
        token_line="Token 验收: PASS=${token_pass} FAIL=${token_fail} SKIP=${token_skip}"
    fi

    cat > "$SUMMARY_FILE" <<EOF
========================================
    v5 real-hardware 迁移证据摘要
========================================
生成时间: $(date)
日志目录: $LOG_DIR
场景: $scenario
自动结论: $result_status
原因: $result_reason

--- 自动摘要 ---
大文件下行完整性: $result_status
共享下行/分发证据: $shared_summary
接收端数量: $receiver_count
payload 大小: ${payload_size} bytes
传输耗时: ${transfer_duration}
重传次数: ${retries}
最大连续无推进窗口: ${max_idle}
stall_events: ${stall_events}
warning_count: ${warning_count}
error_count: ${error_count}
anomaly_count: ${anomaly_count}
source sha256: $source_sha
client1 sha256: $client1_sha
client2 sha256: $client2_sha
$token_line

--- 需要人工判读 ---
- 对照 $sample_file，确认平台期是否接近或超过门槛。
- 结合 uftp_server.log、client1/2 uftpd.log 与底层 server/client 日志，解释时延抬升、重传增加、恢复变慢。
- 若启用了 capture.pcap，请将抓包与本摘要、人工判读结论一起归档到 issue / tracking issue。

--- Issue 回填最小字段 ---
- 运行场景与前置条件
- 原始产物层引用（日志目录、关键附件、自动摘要文件）
- 运行时长与关键事件计数
- 关键异常与风险信号
- 是否可作为正式 v5 基线证据
- 是否需要重跑
- tracking issue 未按固定模板回填正式结论前不得关闭


--- 归档产物 ---
- $DOWNLINK_CONTEXT_FILE
- $sample_file
- $LOG_DIR/metrics.json
- $LOG_DIR/downlink_results.md
- $LOG_DIR/uftp_server.log
- $LOG_DIR/client1/uftpd.log
- $LOG_DIR/client2/uftpd.log
- $LOG_DIR/capture.pcap
EOF

    log_pass "摘要文件已生成: $SUMMARY_FILE"
}

print_terminal_summary() {
    local scenario result_status shared_summary transfer_duration retries max_idle stall_events

    scenario="$(context_value scenario unknown)"
    result_status="$(context_value result_status unknown)"
    shared_summary="$(shared_distribution_summary)"
    transfer_duration="$(json_number transfer_duration_s)"
    retries="$(json_number retries)"
    max_idle="$(json_number max_idle_observed_sec)"
    stall_events="$(json_number stall_events)"

    echo ""
    echo "========================================"
    echo "       v5 real-hardware 摘要"
    echo "========================================"
    echo "场景: $scenario"
    echo "自动结论: $result_status"
    echo "共享下行/分发证据: $shared_summary"
    echo "传输耗时: $transfer_duration"
    echo "重传次数: $retries"
    echo "最大无推进窗口: $max_idle"
    echo "stall_events: $stall_events"
    echo "日志目录: $LOG_DIR"
    echo "========================================"
}

generate_report() {
    if [ ! -d "$LOG_DIR" ]; then
        log_fail "日志目录不存在: $LOG_DIR"
        return 1
    fi

    generate_summary_file
    print_terminal_summary
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    generate_report
fi
