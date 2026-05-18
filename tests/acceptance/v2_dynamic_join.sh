#!/bin/bash

set -euo pipefail

# 该脚本保留为 issue02 的最小专项验收入口。
# 第二版主线统一分层验收入口为 tests/acceptance/v2_mainline.sh。

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

LOG_DIR="${LOG_DIR:-$PROJECT_ROOT/tests/logs/v2_dynamic_join_$(date +%Y%m%d_%H%M%S)}"
RESULT_MD="$LOG_DIR/result.md"
BUILD_LOG="$LOG_DIR/build.log"
SCHEDULER_TEST_LOG="$LOG_DIR/token_scheduler_dynamic_join.log"
INTEGRATION_TEST_LOG="$LOG_DIR/tx_authorization_dynamic_join.log"

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
# 第二版动态加入验收结果

- 结果: $status
- 原因: ${FAIL_REASON:-无}
- 日志目录: $LOG_DIR

## 硬验收范围

- 运行中动态加入的新客户端排到活跃队列队尾
- 新客户端不会抢占当前 grant 或提前插队
- 新客户端只在游标自然轮到时获得 Token 授权

## 验收入口

- token_scheduler_test: TokenScheduler 运行中动态加入的节点排到队尾并自然轮到
- tx_authorization_integration_test: 运行中动态加入的客户端排到队尾并在自然轮到时获得授权

## 日志文件

- build.log
- token_scheduler_dynamic_join.log
- tx_authorization_dynamic_join.log
EOF
}

main() {
    prepare_log_dir
    require_command make

    log_info "构建 issue02 动态加入验收所需测试二进制"
    if ! make token_scheduler_test tx_authorization_integration_test >"$BUILD_LOG" 2>&1; then
        FAIL_REASON="构建测试二进制失败"
        render_result "FAIL"
        log_fail "$FAIL_REASON，查看日志: $BUILD_LOG"
        exit 1
    fi
    log_pass "测试二进制构建成功"

    log_info "执行调度器动态加入行为验收"
    if ! "$PROJECT_ROOT/token_scheduler_test" "TokenScheduler 运行中动态加入的节点排到队尾并自然轮到" >"$SCHEDULER_TEST_LOG" 2>&1; then
        FAIL_REASON="调度器动态加入行为验收失败"
        render_result "FAIL"
        log_fail "$FAIL_REASON，查看日志: $SCHEDULER_TEST_LOG"
        exit 1
    fi
    log_pass "调度器动态加入行为验收通过"

    log_info "执行真实授权路径动态加入验收"
    if ! "$PROJECT_ROOT/tx_authorization_integration_test" "运行中动态加入的客户端排到队尾并在自然轮到时获得授权" >"$INTEGRATION_TEST_LOG" 2>&1; then
        FAIL_REASON="真实授权路径动态加入验收失败"
        render_result "FAIL"
        log_fail "$FAIL_REASON，查看日志: $INTEGRATION_TEST_LOG"
        exit 1
    fi
    log_pass "真实授权路径动态加入验收通过"

    render_result "PASS"
    log_pass "第二版动态加入 acceptance 通过"
}

main "$@"
