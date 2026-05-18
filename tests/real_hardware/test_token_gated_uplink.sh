#!/bin/bash
# tests/real_hardware/test_token_gated_uplink.sh
# Token-gated split-process 真实硬件验收骨架

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [ -f "$PROJECT_ROOT/tests/config/test_config.sh" ]; then
    source "$PROJECT_ROOT/tests/config/test_config.sh"
else
    WIFI_IFACE="${WIFI_IFACE:-wlxbcec23372588}"
    CHANNEL="${CHANNEL:-157}"
    MCS="${MCS:-0}"
    NODE_ID="${NODE_ID:-1}"
fi

SCENARIO="all"
ANALYZE_ONLY=false
LOG_DIR="${LOG_DIR:-$PROJECT_ROOT/tests/logs/test_$(date +%Y%m%d_%H%M%S)}"
TOKEN_STARTUP_WAIT="${TOKEN_STARTUP_WAIT:-3}"
TOKEN_SCHEDULER_WARMUP="${TOKEN_SCHEDULER_WARMUP:-2}"
TOKEN_EXPIRY_WAIT_SEC="${TOKEN_EXPIRY_WAIT_SEC:-2}"
TOKEN_PROBE_TIMEOUT="${TOKEN_PROBE_TIMEOUT:-15}"
TOKEN_RESULTS_TSV="$LOG_DIR/token_results.tsv"
TOKEN_RESULTS_MD="$LOG_DIR/token_results.md"
TOKEN_CONTEXT_FILE="$LOG_DIR/token_context.txt"
KCP_SMALL_FILE_SIZE="${KCP_SMALL_FILE_SIZE:-65536}"
KCP_TRANSFER_TIMEOUT="${KCP_TRANSFER_TIMEOUT:-30}"
KCP_SOURCE_FILE="${KCP_SOURCE_FILE:-$LOG_DIR/kcp_small_source.bin}"
KCP_RECEIVED_FILE="${KCP_RECEIVED_FILE:-$LOG_DIR/kcp_small_received.bin}"
KCP_SOURCE_SHA256=""
KCP_RECEIVED_SHA256=""

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
  sudo bash tests/real_hardware/test_token_gated_uplink.sh [--scenario all|baseline|no-token|single|expiry|dual|kcp-small] [--analyze-only]

环境变量钩子:
  TOKEN_SERVER_START_CMD         服务端启动命令
  TOKEN_CLIENT1_START_CMD        客户端1启动命令
  TOKEN_CLIENT2_START_CMD        客户端2启动命令（双客户端场景）
  TOKEN_SCHEDULER_CMD            调度器启动命令
  TOKEN_PROBE_CMD                单客户端探测命令
  TOKEN_POST_EXPIRY_PROBE_CMD    过期后再次探测命令
  TOKEN_CLIENT1_PROBE_CMD        双客户端下客户端1探测命令
  TOKEN_CLIENT2_PROBE_CMD        双客户端下客户端2探测命令
  KCP_RECEIVER_CMD               KCP 小文件接收命令
  KCP_SENDER_CMD                 KCP 小文件发送命令，可引用 $KCP_SOURCE_FILE
  KCP_SOURCE_FILE                自动生成的小文件路径
  KCP_RECEIVED_FILE              KCP 接收侧落盘文件路径
  KCP_SMALL_FILE_SIZE            自动生成的小文件大小，默认 65536 字节
  KCP_TRANSFER_TIMEOUT           KCP sender 超时，默认 30 秒

说明:
  该脚本优先解决 Issue 16 的“真实硬件验收编排与留痕”问题。
  当前仓库内的 wfb_token_scheduler 可通过 -s <base_socket> 向 wfb_tx 的 <base_socket>.token socket 下发本机 Token 授权事件；
  dual 场景可使用 -s node:base_socket,node:base_socket 按 node_id 下发到不同客户端。
HELP_EOF
            exit 0
            ;;
        *)
            echo "未知参数: $1" >&2
            exit 1
            ;;
    esac
done

PIDS=()
STARTED_LOGS=()
RESULT_COUNT=0
PASS_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0

log_info()  { echo "[INFO] $(date '+%H:%M:%S') $1"; }
log_pass()  { echo "[PASS] $(date '+%H:%M:%S') $1"; }
log_fail()  { echo "[FAIL] $(date '+%H:%M:%S') $1" >&2; }
log_warn()  { echo "[WARN] $(date '+%H:%M:%S') $1" >&2; }

cleanup() {
    for pid in "${PIDS[@]:-}"; do
        kill "$pid" 2>/dev/null || true
    done
}
trap cleanup EXIT

prepare_log_dir() {
    mkdir -p "$LOG_DIR"
    : > "$TOKEN_RESULTS_TSV"
    cat > "$TOKEN_CONTEXT_FILE" <<CTX_EOF
log_dir=$LOG_DIR
scenario=$SCENARIO
analyze_only=$ANALYZE_ONLY
wifi_iface=${WIFI_IFACE:-}
channel=${CHANNEL:-}
mcs=${MCS:-}
node_id=${NODE_ID:-}
token_startup_wait=$TOKEN_STARTUP_WAIT
token_scheduler_warmup=$TOKEN_SCHEDULER_WARMUP
token_expiry_wait_sec=$TOKEN_EXPIRY_WAIT_SEC
token_probe_timeout=$TOKEN_PROBE_TIMEOUT
kcp_small_file_size=$KCP_SMALL_FILE_SIZE
kcp_transfer_timeout=$KCP_TRANSFER_TIMEOUT
kcp_source_file=$KCP_SOURCE_FILE
kcp_received_file=$KCP_RECEIVED_FILE
CTX_EOF
}

record_result() {
    local scenario="$1"
    local status="$2"
    local reason="$3"
    RESULT_COUNT=$((RESULT_COUNT + 1))
    case "$status" in
        PASS) PASS_COUNT=$((PASS_COUNT + 1)) ;;
        FAIL) FAIL_COUNT=$((FAIL_COUNT + 1)) ;;
        *) SKIP_COUNT=$((SKIP_COUNT + 1)) ;;
    esac
    printf '%s\t%s\t%s\n' "$scenario" "$status" "$reason" >> "$TOKEN_RESULTS_TSV"
}

render_markdown_results() {
    {
        echo "# Token-gated split-process 验收结果"
        echo
        echo "- 生成时间: $(date)"
        echo "- 日志目录: $LOG_DIR"
        echo
        echo "| 场景 | 结果 | 说明 |"
        echo "| --- | --- | --- |"
        if [ -s "$TOKEN_RESULTS_TSV" ]; then
            while IFS=$'\t' read -r scenario status reason; do
                echo "| $scenario | $status | $reason |"
            done < "$TOKEN_RESULTS_TSV"
        else
            echo "| 未执行 | SKIP | 没有生成任何场景结果 |"
        fi
        echo
        echo "## 日志文件"
        for f in scheduler.log server.log client1.log client2.log probe.log probe_post_expiry.log client1_probe.log client2_probe.log kcp_sender.log kcp_receiver.log; do
            if [ -f "$LOG_DIR/$f" ]; then
                echo "- $f"
            fi
        done
        echo
        echo "## 说明"
        echo "- wfb_token_scheduler 可通过 -s <base_socket> 向 wfb_tx 的 <base_socket>.token socket 下发本机 Token 授权事件；dual 可用 -s node:base_socket,node:base_socket。"
        echo "- KCP 小文件场景用于验证 Token 门控通过后的 KCP 层小文件闭环；40MB 和 10 节点压力测试不阻塞本阶段。"
        if [ -n "$KCP_SOURCE_SHA256$KCP_RECEIVED_SHA256" ]; then
            echo "- KCP 源文件 SHA256: ${KCP_SOURCE_SHA256:-未生成}"
            echo "- KCP 接收文件 SHA256: ${KCP_RECEIVED_SHA256:-未生成}"
        fi
        echo "- 若需要在真实硬件上完成闭环，请通过 TOKEN_*_CMD 和 KCP_*_CMD 环境变量接入你的实际启动命令。"
    } > "$TOKEN_RESULTS_MD"
}

require_cmd() {
    local name="$1"
    local value="${!name:-}"
    if [ -z "$value" ]; then
        return 1
    fi
    return 0
}

run_bg() {
    local name="$1"
    local cmd="$2"
    local logfile="$3"
    log_info "启动 $name"
    bash -lc "$cmd" > "$logfile" 2>&1 &
    local pid=$!
    PIDS+=("$pid")
    STARTED_LOGS+=("$logfile")
    sleep 1
    if ! kill -0 "$pid" 2>/dev/null; then
        log_fail "$name 启动失败，查看日志: $logfile"
        return 1
    fi
    return 0
}

run_probe() {
    local name="$1"
    local cmd="$2"
    local logfile="$3"
    log_info "执行探测 $name"
    if timeout "$TOKEN_PROBE_TIMEOUT" bash -lc "$cmd" > "$logfile" 2>&1; then
        return 0
    fi
    return 1
}

run_kcp_sender_cmd() {
    local cmd="$1"
    local logfile="$2"
    log_info "执行 KCP 小文件上传"
    if timeout "$KCP_TRANSFER_TIMEOUT" bash -lc "$cmd" > "$logfile" 2>&1; then
        return 0
    fi
    return 1
}

reset_runtime() {
    cleanup
    PIDS=()
}

count_scheduler_grants() {
    local logfile="$1"
    if [ ! -f "$logfile" ]; then
        echo 0
        return
    fi
    grep -c '^grant seq=' "$logfile" 2>/dev/null || echo 0
}

latest_token_filter_line() {
    local logfile="$1"
    if [ ! -f "$logfile" ]; then
        return
    fi
    grep 'TOKEN_FILTER' "$logfile" 2>/dev/null | tail -1 || true
}

latest_token_auth_line() {
    local logfile="$1"
    if [ ! -f "$logfile" ]; then
        return
    fi
    grep 'TOKEN_AUTH' "$logfile" 2>/dev/null | tail -1 || true
}

latest_server_pkt_line() {
    local logfile="$1"
    if [ ! -f "$logfile" ]; then
        return
    fi
    grep $'\tPKT\t' "$logfile" 2>/dev/null | tail -1 || true
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

extract_server_data_packets() {
    local logfile="$1"
    local max_count=0
    while IFS= read -r line; do
        if [[ "$line" =~ PKT[[:space:]]+([0-9]+):([0-9]+):([0-9]+):([0-9]+):([0-9]+) ]]; then
            local val="${BASH_REMATCH[5]}"
            if [ "$val" -gt "$max_count" ]; then
                max_count="$val"
            fi
        fi
    done < "$logfile"
    echo "$max_count"
}

file_sha256() {
    local path="$1"
    if [ ! -f "$path" ]; then
        return 1
    fi
    sha256sum "$path" | cut -d' ' -f1
}

prepare_kcp_small_file() {
    mkdir -p "$(dirname "$KCP_SOURCE_FILE")"
    dd if=/dev/urandom of="$KCP_SOURCE_FILE" bs="$KCP_SMALL_FILE_SIZE" count=1 status=none
    KCP_SOURCE_SHA256="$(file_sha256 "$KCP_SOURCE_FILE")"
}

append_kcp_analysis() {
    {
        echo "kcp_source_file=$KCP_SOURCE_FILE"
        echo "kcp_received_file=$KCP_RECEIVED_FILE"
        echo "kcp_source_sha256=${KCP_SOURCE_SHA256:-}"
        echo "kcp_received_sha256=${KCP_RECEIVED_SHA256:-}"
        if [ -f "$LOG_DIR/kcp_sender.log" ]; then
            echo "kcp_sender_pass=$(grep -c '\[PASS\]' "$LOG_DIR/kcp_sender.log" 2>/dev/null || true)"
        fi
        if [ -f "$LOG_DIR/kcp_receiver.log" ]; then
            echo "kcp_receiver_pass=$(grep -c '\[PASS\]' "$LOG_DIR/kcp_receiver.log" 2>/dev/null || true)"
        fi
    } >> "$TOKEN_CONTEXT_FILE"
}

append_analysis() {
    {
        echo
        echo "[$(date '+%H:%M:%S')] 自动分析摘录"
        if [ -f "$LOG_DIR/scheduler.log" ]; then
            echo "scheduler_grants=$(count_scheduler_grants "$LOG_DIR/scheduler.log")"
        fi
        if [ -f "$LOG_DIR/server.log" ]; then
            echo "server_pkt=$(latest_server_pkt_line "$LOG_DIR/server.log")"
            echo "server_data_packets=$(extract_server_data_packets "$LOG_DIR/server.log")"
        fi
        if [ -f "$LOG_DIR/client1.log" ]; then
            echo "client1_token_filter=$(latest_token_filter_line "$LOG_DIR/client1.log")"
            echo "client1_token_auth=$(latest_token_auth_line "$LOG_DIR/client1.log")"
        fi
        if [ -f "$LOG_DIR/client2.log" ]; then
            echo "client2_token_filter=$(latest_token_filter_line "$LOG_DIR/client2.log")"
            echo "client2_token_auth=$(latest_token_auth_line "$LOG_DIR/client2.log")"
        fi
    } >> "$TOKEN_CONTEXT_FILE"
}

run_baseline() {
    local scenario="baseline"
    if ! require_cmd TOKEN_SERVER_START_CMD || ! require_cmd TOKEN_CLIENT1_START_CMD || ! require_cmd TOKEN_PROBE_CMD; then
        record_result "$scenario" "SKIP" "缺少 TOKEN_SERVER_START_CMD / TOKEN_CLIENT1_START_CMD / TOKEN_PROBE_CMD"
        return
    fi

    reset_runtime
    run_bg "服务端" "$TOKEN_SERVER_START_CMD" "$LOG_DIR/server.log" || {
        record_result "$scenario" "FAIL" "服务端启动失败"
        return
    }
    run_bg "客户端1" "$TOKEN_CLIENT1_START_CMD" "$LOG_DIR/client1.log" || {
        record_result "$scenario" "FAIL" "客户端1启动失败"
        return
    }
    sleep "$TOKEN_STARTUP_WAIT"

    if run_probe "baseline" "$TOKEN_PROBE_CMD" "$LOG_DIR/probe.log"; then
        record_result "$scenario" "PASS" "基线探测命令成功"
    else
        record_result "$scenario" "FAIL" "基线探测命令失败"
    fi
}

run_no_token() {
    local scenario="no-token"
    if ! require_cmd TOKEN_SERVER_START_CMD || ! require_cmd TOKEN_CLIENT1_START_CMD || ! require_cmd TOKEN_PROBE_CMD; then
        record_result "$scenario" "SKIP" "缺少 TOKEN_SERVER_START_CMD / TOKEN_CLIENT1_START_CMD / TOKEN_PROBE_CMD"
        return
    fi

    reset_runtime
    run_bg "服务端" "$TOKEN_SERVER_START_CMD" "$LOG_DIR/server.log" || {
        record_result "$scenario" "FAIL" "服务端启动失败"
        return
    }
    run_bg "客户端1" "$TOKEN_CLIENT1_START_CMD" "$LOG_DIR/client1.log" || {
        record_result "$scenario" "FAIL" "客户端1启动失败"
        return
    }
    sleep "$TOKEN_STARTUP_WAIT"

    run_probe "no-token" "$TOKEN_PROBE_CMD" "$LOG_DIR/probe.log" || true
    local denied_sends
    denied_sends="$(extract_token_auth_field "$LOG_DIR/client1.log" denied_sends)"

    if [ "$denied_sends" -gt 0 ]; then
        record_result "$scenario" "PASS" "无 Token 时探测被拒绝 (denied_sends=$denied_sends)，符合静默预期"
    else
        record_result "$scenario" "FAIL" "无 Token 时探测未被拒绝或未能提取 denied_sends 计数，未保持静默"
    fi
}

run_single() {
    local scenario="single"
    if ! require_cmd TOKEN_SERVER_START_CMD || ! require_cmd TOKEN_CLIENT1_START_CMD || ! require_cmd TOKEN_SCHEDULER_CMD || ! require_cmd TOKEN_PROBE_CMD; then
        record_result "$scenario" "SKIP" "缺少 TOKEN_SERVER_START_CMD / TOKEN_CLIENT1_START_CMD / TOKEN_SCHEDULER_CMD / TOKEN_PROBE_CMD"
        return
    fi

    reset_runtime
    run_bg "服务端" "$TOKEN_SERVER_START_CMD" "$LOG_DIR/server.log" || {
        record_result "$scenario" "FAIL" "服务端启动失败"
        return
    }
    run_bg "客户端1" "$TOKEN_CLIENT1_START_CMD" "$LOG_DIR/client1.log" || {
        record_result "$scenario" "FAIL" "客户端1启动失败"
        return
    }
    run_bg "调度器" "$TOKEN_SCHEDULER_CMD" "$LOG_DIR/scheduler.log" || {
        record_result "$scenario" "FAIL" "调度器启动失败"
        return
    }
    sleep "$TOKEN_SCHEDULER_WARMUP"

    local grants
    grants="$(count_scheduler_grants "$LOG_DIR/scheduler.log")"
    if run_probe "single" "$TOKEN_PROBE_CMD" "$LOG_DIR/probe.log"; then
        if [ "$grants" -gt 0 ]; then
            record_result "$scenario" "PASS" "探测成功，且 scheduler 输出了 $grants 次 grant"
        else
            record_result "$scenario" "FAIL" "探测成功，但 scheduler 未输出 grant 日志"
        fi
    else
        if [ "$grants" -gt 0 ]; then
            record_result "$scenario" "FAIL" "scheduler 已输出 grant=$grants，但探测失败"
        else
            record_result "$scenario" "FAIL" "scheduler 未输出 grant，且探测失败"
        fi
    fi
}

run_expiry() {
    local scenario="expiry"
    if ! require_cmd TOKEN_SERVER_START_CMD || ! require_cmd TOKEN_CLIENT1_START_CMD || ! require_cmd TOKEN_SCHEDULER_CMD || ! require_cmd TOKEN_PROBE_CMD; then
        record_result "$scenario" "SKIP" "缺少 TOKEN_SERVER_START_CMD / TOKEN_CLIENT1_START_CMD / TOKEN_SCHEDULER_CMD / TOKEN_PROBE_CMD"
        return
    fi

    reset_runtime
    run_bg "服务端" "$TOKEN_SERVER_START_CMD" "$LOG_DIR/server.log" || {
        record_result "$scenario" "FAIL" "服务端启动失败"
        return
    }
    run_bg "客户端1" "$TOKEN_CLIENT1_START_CMD" "$LOG_DIR/client1.log" || {
        record_result "$scenario" "FAIL" "客户端1启动失败"
        return
    }
    run_bg "调度器" "$TOKEN_SCHEDULER_CMD" "$LOG_DIR/scheduler.log" || {
        record_result "$scenario" "FAIL" "调度器启动失败"
        return
    }
    sleep "$TOKEN_SCHEDULER_WARMUP"

    run_probe "expiry-before-stop" "$TOKEN_PROBE_CMD" "$LOG_DIR/probe.log" || true
    sleep 1 # Wait for logs

    local authorized_sends
    authorized_sends="$(extract_token_auth_field "$LOG_DIR/client1.log" authorized_sends)"

    if [ "$authorized_sends" -eq 0 ]; then
        record_result "$scenario" "FAIL" "过期前探测失败(authorized_sends=0)，无法继续验证过期行为"
        return
    fi

    local scheduler_pid=""
    if [ "${#PIDS[@]}" -gt 0 ]; then
        local last_index=$(( ${#PIDS[@]} - 1 ))
        scheduler_pid="${PIDS[$last_index]}"
        kill "$scheduler_pid" 2>/dev/null || true
    fi
    sleep "$TOKEN_EXPIRY_WAIT_SEC"

    local post_cmd="${TOKEN_POST_EXPIRY_PROBE_CMD:-$TOKEN_PROBE_CMD}"
    run_probe "expiry-after-stop" "$post_cmd" "$LOG_DIR/probe_post_expiry.log" || true
    sleep 1

    local denied_sends
    denied_sends="$(extract_token_auth_field "$LOG_DIR/client1.log" denied_sends)"

    if [ "$denied_sends" -gt 0 ]; then
        record_result "$scenario" "PASS" "停止调度器并等待过期后，探测被拒绝 (denied_sends>0)，符合过期停发预期"
    else
        record_result "$scenario" "FAIL" "停止调度器并等待过期后，没有发现 denied_sends，说明未停发"
    fi
}

run_dual() {
    local scenario="dual"
    if ! require_cmd TOKEN_SERVER_START_CMD || ! require_cmd TOKEN_CLIENT1_START_CMD || ! require_cmd TOKEN_CLIENT2_START_CMD || ! require_cmd TOKEN_SCHEDULER_CMD; then
        record_result "$scenario" "SKIP" "缺少 TOKEN_SERVER_START_CMD / TOKEN_CLIENT1_START_CMD / TOKEN_CLIENT2_START_CMD / TOKEN_SCHEDULER_CMD"
        return
    fi

    reset_runtime
    run_bg "服务端" "$TOKEN_SERVER_START_CMD" "$LOG_DIR/server.log" || {
        record_result "$scenario" "FAIL" "服务端启动失败"
        return
    }
    run_bg "客户端1" "$TOKEN_CLIENT1_START_CMD" "$LOG_DIR/client1.log" || {
        record_result "$scenario" "FAIL" "客户端1启动失败"
        return
    }
    run_bg "客户端2" "$TOKEN_CLIENT2_START_CMD" "$LOG_DIR/client2.log" || {
        record_result "$scenario" "FAIL" "客户端2启动失败"
        return
    }
    run_bg "调度器" "$TOKEN_SCHEDULER_CMD" "$LOG_DIR/scheduler.log" || {
        record_result "$scenario" "FAIL" "调度器启动失败"
        return
    }
    sleep "$TOKEN_SCHEDULER_WARMUP"

    local c1_status="NOT_RUN"
    local c2_status="NOT_RUN"
    if require_cmd TOKEN_CLIENT1_PROBE_CMD; then
        if run_probe "dual-client1" "$TOKEN_CLIENT1_PROBE_CMD" "$LOG_DIR/client1_probe.log"; then
            c1_status="OK"
        else
            c1_status="FAIL"
        fi
    fi
    if require_cmd TOKEN_CLIENT2_PROBE_CMD; then
        if run_probe "dual-client2" "$TOKEN_CLIENT2_PROBE_CMD" "$LOG_DIR/client2_probe.log"; then
            c2_status="OK"
        else
            c2_status="FAIL"
        fi
    fi

    local c1_authorized
    local c1_denied
    local c2_authorized
    local c2_denied
    local server_data_packets
    c1_authorized="$(extract_token_auth_field "$LOG_DIR/client1.log" authorized_sends)"
    c1_denied="$(extract_token_auth_field "$LOG_DIR/client1.log" denied_sends)"
    c2_authorized="$(extract_token_auth_field "$LOG_DIR/client2.log" authorized_sends)"
    c2_denied="$(extract_token_auth_field "$LOG_DIR/client2.log" denied_sends)"
    server_data_packets="$(extract_server_data_packets "$LOG_DIR/server.log")"

    if [ "$c1_status" = "NOT_RUN" ] || [ "$c2_status" = "NOT_RUN" ]; then
        record_result "$scenario" "SKIP" "缺少 TOKEN_CLIENT1_PROBE_CMD 或 TOKEN_CLIENT2_PROBE_CMD，无法自动判定 dual"
    elif [ "$server_data_packets" -eq 0 ]; then
        record_result "$scenario" "FAIL" "双客户端探测后 server 未记录数据包 (client1 authorized=$c1_authorized denied=$c1_denied client2 authorized=$c2_authorized denied=$c2_denied)"
    elif [ "$c1_authorized" -gt 0 ] && [ "$c2_authorized" -gt 0 ]; then
        record_result "$scenario" "PASS" "双客户端均在各自 grant 窗口上行成功 (client1 authorized=$c1_authorized denied=$c1_denied client2 authorized=$c2_authorized denied=$c2_denied server_data=$server_data_packets)"
    else
        record_result "$scenario" "FAIL" "未观察到两个 holder 都能上行 (client1 authorized=$c1_authorized denied=$c1_denied client2 authorized=$c2_authorized denied=$c2_denied server_data=$server_data_packets)"
    fi
}

run_kcp_small() {
    local scenario="kcp-small"
    if ! require_cmd TOKEN_SERVER_START_CMD || ! require_cmd TOKEN_CLIENT1_START_CMD || ! require_cmd TOKEN_SCHEDULER_CMD || ! require_cmd KCP_RECEIVER_CMD || ! require_cmd KCP_SENDER_CMD; then
        record_result "$scenario" "SKIP" "缺少 TOKEN_SERVER_START_CMD / TOKEN_CLIENT1_START_CMD / TOKEN_SCHEDULER_CMD / KCP_RECEIVER_CMD / KCP_SENDER_CMD"
        return
    fi

    reset_runtime
    prepare_kcp_small_file
    rm -f "$KCP_RECEIVED_FILE"

    local receiver_cmd="${KCP_RECEIVER_CMD//\$KCP_RECEIVED_FILE/$KCP_RECEIVED_FILE}"
    local sender_cmd="${KCP_SENDER_CMD//\$KCP_SOURCE_FILE/$KCP_SOURCE_FILE}"

    run_bg "服务端" "$TOKEN_SERVER_START_CMD" "$LOG_DIR/server.log" || {
        record_result "$scenario" "FAIL" "服务端启动失败，尚未进入 Token/KCP 判定"
        return
    }
    run_bg "KCP 接收端" "$receiver_cmd" "$LOG_DIR/kcp_receiver.log" || {
        record_result "$scenario" "FAIL" "KCP 接收端启动失败"
        return
    }
    run_bg "客户端1" "$TOKEN_CLIENT1_START_CMD" "$LOG_DIR/client1.log" || {
        record_result "$scenario" "FAIL" "客户端1启动失败，尚未进入 Token/KCP 判定"
        return
    }
    run_bg "调度器" "$TOKEN_SCHEDULER_CMD" "$LOG_DIR/scheduler.log" || {
        record_result "$scenario" "FAIL" "调度器启动失败，无法形成 Token 授权窗口"
        return
    }
    sleep "$TOKEN_SCHEDULER_WARMUP"

    local grants_before
    local authorized_before
    local server_data_before
    grants_before="$(count_scheduler_grants "$LOG_DIR/scheduler.log")"
    authorized_before="$(extract_token_auth_field "$LOG_DIR/client1.log" authorized_sends)"
    server_data_before="$(extract_server_data_packets "$LOG_DIR/server.log")"

    run_kcp_sender_cmd "$sender_cmd" "$LOG_DIR/kcp_sender.log" || true
    sleep 1

    local grants_after
    local authorized_after
    local denied_after
    local server_data_after
    grants_after="$(count_scheduler_grants "$LOG_DIR/scheduler.log")"
    authorized_after="$(extract_token_auth_field "$LOG_DIR/client1.log" authorized_sends)"
    denied_after="$(extract_token_auth_field "$LOG_DIR/client1.log" denied_sends)"
    server_data_after="$(extract_server_data_packets "$LOG_DIR/server.log")"

    if [ -f "$KCP_RECEIVED_FILE" ]; then
        KCP_RECEIVED_SHA256="$(file_sha256 "$KCP_RECEIVED_FILE")"
    fi
    append_kcp_analysis

    if [ "$grants_after" -eq 0 ]; then
        record_result "$scenario" "FAIL" "Token 调度器未输出 grant，优先定位 Token 授权问题"
    elif [ "$authorized_after" -le "$authorized_before" ]; then
        record_result "$scenario" "FAIL" "Token grant=$grants_after 但 authorized_sends 未增长 ($authorized_before->$authorized_after)，优先定位 Token gate/窗口问题"
    elif [ ! -f "$KCP_RECEIVED_FILE" ]; then
        record_result "$scenario" "FAIL" "Token 已授权发送 ($authorized_before->$authorized_after)，但未找到 KCP 接收文件 $KCP_RECEIVED_FILE，优先定位 KCP 重组/落盘"
    elif [ "$KCP_SOURCE_SHA256" != "$KCP_RECEIVED_SHA256" ]; then
        record_result "$scenario" "FAIL" "KCP 接收文件校验失败 source=$KCP_SOURCE_SHA256 received=$KCP_RECEIVED_SHA256，Token 已通过，优先定位 KCP/缓冲完整性"
    elif [ "$server_data_after" -le "$server_data_before" ]; then
        record_result "$scenario" "PASS" "KCP 小文件上传完成且 SHA256 一致，Token grant=$grants_after authorized=$authorized_after denied=$denied_after；server PKT 计数未增长 ($server_data_before->$server_data_after)，需另查 PKT 统计口径"
    else
        record_result "$scenario" "PASS" "KCP 小文件上传完成且 SHA256 一致，Token grant=$grants_after authorized=$authorized_after denied=$denied_after server_data=$server_data_after；40MB 与 10 节点压力测试不阻塞本阶段"
    fi
}

run_selected_scenarios() {
    case "$SCENARIO" in
        all)
            run_baseline
            run_no_token
            run_single
            run_expiry
            run_dual
            ;;
        baseline)
            run_baseline
            ;;
        no-token)
            run_no_token
            ;;
        single)
            run_single
            ;;
        expiry)
            run_expiry
            ;;
        dual)
            run_dual
            ;;
        kcp-small)
            run_kcp_small
            ;;
        *)
            echo "不支持的场景: $SCENARIO" >&2
            exit 1
            ;;
    esac
}

analyze_only() {
    if [ ! -d "$LOG_DIR" ]; then
        log_fail "日志目录不存在: $LOG_DIR"
        exit 1
    fi
    if [ ! -f "$TOKEN_RESULTS_TSV" ]; then
        : > "$TOKEN_RESULTS_TSV"
        record_result "analysis" "SKIP" "仅执行日志分析，未找到既有 token_results.tsv"
    fi
}

main() {
    prepare_log_dir
    log_info "日志目录: $LOG_DIR"
    log_info "场景: $SCENARIO"

    if [ "$ANALYZE_ONLY" = true ]; then
        analyze_only
    else
        run_selected_scenarios
    fi

    append_analysis
    render_markdown_results

    echo "========================================"
    echo "  Token 验收结果"
    echo "========================================"
    echo "PASS: $PASS_COUNT"
    echo "FAIL: $FAIL_COUNT"
    echo "SKIP: $SKIP_COUNT"
    echo "结果文件: $TOKEN_RESULTS_MD"
    echo "========================================"

    if [ "$RESULT_COUNT" -eq 0 ]; then
        exit 2
    fi
    if [ "$FAIL_COUNT" -gt 0 ]; then
        exit 1
    fi
    if [ "$PASS_COUNT" -eq 0 ]; then
        exit 2
    fi
    exit 0
}

main "$@"
