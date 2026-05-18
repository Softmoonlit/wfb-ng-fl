#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
export PROJECT_ROOT

LOG_DIR="${LOG_DIR:-$PROJECT_ROOT/tests/logs/v2_mainline_$(date +%Y%m%d_%H%M%S)}"
RESULT_MD="$LOG_DIR/result.md"
BUILD_LOG="$LOG_DIR/build.log"

SCHEDULER_LOG_ROTATION="$LOG_DIR/token_scheduler_rotation.log"
SCHEDULER_LOG_REFRESH="$LOG_DIR/token_scheduler_refresh.log"
SCHEDULER_LOG_REJOIN="$LOG_DIR/token_scheduler_rejoin.log"
INTEGRATION_LOG_ROTATION="$LOG_DIR/tx_authorization_rotation.log"
INTEGRATION_LOG_DYNAMIC_JOIN="$LOG_DIR/tx_authorization_dynamic_join.log"
INTEGRATION_LOG_REJOIN="$LOG_DIR/tx_authorization_rejoin.log"
DATA_GATE_LOG_FIRST_DECLARE="$LOG_DIR/tx_data_source_first_declare.log"
DATA_GATE_LOG_REDECLARE="$LOG_DIR/tx_data_source_redeclare.log"
DATA_GATE_LOG_TOKEN_RECEIVED="$LOG_DIR/tx_data_source_token_received.log"

FAIL_REASON=""

log_info() { echo "[INFO] $(date '+%H:%M:%S') $1"; }
log_pass() { echo "[PASS] $(date '+%H:%M:%S') $1"; }
log_fail() { echo "[FAIL] $(date '+%H:%M:%S') $1" >&2; }

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

render_result() {
    local status="$1"
    cat > "$RESULT_MD" <<EOF
# 第二版主线分层验收结果

- 结果: $status
- 原因: ${FAIL_REASON:-无}
- 日志目录: $LOG_DIR

## 三类硬验收场景

- 双客户端顺序入队轮换上行成功
- 长传输中动态加入成功
- 粗粒度移除后自愈重入成功

## 分层入口

- 调度层: token_scheduler_test
- 授权链路层: tx_authorization_integration_test
- 客户端声明/Token 状态层: tx_data_source_gate_test

## 关键日志文件

- build.log
- token_scheduler_rotation.log
- token_scheduler_refresh.log
- token_scheduler_rejoin.log
- tx_authorization_rotation.log
- tx_authorization_dynamic_join.log
- tx_authorization_rejoin.log
- tx_data_source_first_declare.log
- tx_data_source_redeclare.log
- tx_data_source_token_received.log
EOF
}

run_case() {
    local title="$1"
    local cmd="$2"
    local logfile="$3"

    log_info "$title"
    if ! bash -lc "$cmd" >"$logfile" 2>&1; then
        FAIL_REASON="$title 失败"
        render_result "FAIL"
        log_fail "$FAIL_REASON，查看日志: $logfile"
        exit 1
    fi
    log_pass "$title 通过"
}

main() {
    prepare_log_dir
    require_command make

    log_info "构建 v2-mainline 分层验收所需测试二进制"
    if ! make token_scheduler_test tx_authorization_integration_test tx_data_source_gate_test >"$BUILD_LOG" 2>&1; then
        FAIL_REASON="构建测试二进制失败"
        render_result "FAIL"
        log_fail "$FAIL_REASON，查看日志: $BUILD_LOG"
        exit 1
    fi
    log_pass "测试二进制构建成功"

    run_case \
        "调度层: 双客户端顺序入队轮换" \
        '"$PROJECT_ROOT/token_scheduler_test" "run_token_scheduler 输出日志并可干净退出"' \
        "$SCHEDULER_LOG_ROTATION"

    run_case \
        "授权链路层: 双客户端顺序入队轮换上行" \
        '"$PROJECT_ROOT/tx_authorization_integration_test" "双客户端声明后按顺序轮换授权并各自完成一次发送"' \
        "$INTEGRATION_LOG_ROTATION"

    run_case \
        "授权链路层: 长传输中动态加入" \
        '"$PROJECT_ROOT/tx_authorization_integration_test" "运行中动态加入的客户端排到队尾并在自然轮到时获得授权"' \
        "$INTEGRATION_LOG_DYNAMIC_JOIN"

    run_case \
        "调度层: 重复声明刷新活性" \
        '"$PROJECT_ROOT/token_scheduler_test" "run_token_scheduler 对活跃节点重复声明时刷新活性但不改变轮换顺序"' \
        "$SCHEDULER_LOG_REFRESH"

    run_case \
        "调度层: 粗粒度移除后自愈重入" \
        '"$PROJECT_ROOT/token_scheduler_test" "run_token_scheduler 粗粒度移除静默节点并允许其重声明后按队尾重入"' \
        "$SCHEDULER_LOG_REJOIN"

    run_case \
        "授权链路层: 粗粒度移除后自愈重入" \
        '"$PROJECT_ROOT/tx_authorization_integration_test" "客户端被粗粒度移除后可通过重声明按队尾重入并再次获得授权"' \
        "$INTEGRATION_LOG_REJOIN"

    run_case \
        "客户端状态层: 首次声明日志留痕" \
        '"$PROJECT_ROOT/tx_data_source_gate_test" "process_data_packet 在首次未授权时发送 ready 声明"' \
        "$DATA_GATE_LOG_FIRST_DECLARE"

    run_case \
        "客户端状态层: 重声明日志留痕" \
        '"$PROJECT_ROOT/tx_data_source_gate_test" "process_data_packet 在长时间无 Token 时重发 ready 声明但不会短时间连发"' \
        "$DATA_GATE_LOG_REDECLARE"

    run_case \
        "客户端状态层: Token 接收日志留痕" \
        '"$PROJECT_ROOT/tx_data_source_gate_test" "drain_authorization_events 按顺序吸收多条授权事件"' \
        "$DATA_GATE_LOG_TOKEN_RECEIVED"

    render_result "PASS"
    log_pass "第二版主线分层 acceptance 通过"
}

main "$PROJECT_ROOT"
