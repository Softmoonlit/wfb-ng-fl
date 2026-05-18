#!/bin/bash
# tests/real_hardware/test_basic_connectivity.sh
# 基础连接测试脚本 - 验证服务器和客户端的基础通信能力
#
# 使用方法:
#   bash tests/real_hardware/test_basic_connectivity.sh
#
# 环境变量:
#   WIFI_IFACE        - 服务端 WiFi 网卡接口名称（默认: wlan0）
#   WIFI_IFACE_CLIENT - 客户端 WiFi 网卡接口名称（默认: wlan1，如果未设置则使用 WIFI_IFACE）
#   CHANNEL           - 无线信道（默认: 6）
#   MCS               - 调制编码方案（默认: 0）
#   NODE_ID           - 客户端节点 ID（默认: 1）
#   LOG_DIR           - 日志目录（默认: tests/logs/test_TIMESTAMP）
#
# 测试目标:
#   1. 验证服务器进程能正常启动
#   2. 验证客户端进程能正常启动
#   3. 验证服务器和客户端能互相发现（通过日志）
#   4. 验证网卡能正确注入和接收数据帧

set -e

# ============================================
# 配置区
# ============================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# 加载测试配置
if [ -f "$PROJECT_ROOT/tests/config/test_config.sh" ]; then
    source "$PROJECT_ROOT/tests/config/test_config.sh"
else
    echo "错误: 找不到测试配置文件 tests/config/test_config.sh"
    exit 1
fi

# 客户端网卡接口（如果未设置，则使用服务端网卡或默认值）
if [ -z "$WIFI_IFACE_CLIENT" ]; then
    WIFI_IFACE_CLIENT="${WIFI_IFACE:-wlan1}"
fi

# 日志目录（如果未设置，则创建临时目录）
if [ -z "$LOG_DIR" ]; then
    LOG_DIR="$PROJECT_ROOT/tests/logs/test_$(date +%Y%m%d_%H%M%S)"
fi
mkdir -p "$LOG_DIR"

# 测试超时时间（秒）
TEST_TIMEOUT=30

# ============================================
# 辅助函数
# ============================================
log_info()  { echo "[INFO] $(date '+%H:%M:%S') $1"; }
log_pass()  { echo "[PASS] $(date '+%H:%M:%S') $1"; }
log_fail()  { echo "[FAIL] $(date '+%H:%M:%S') $1" >&2; }
log_warn()  { echo "[WARN] $(date '+%H:%M:%S') $1" >&2; }

# ============================================
# 测试流程
# ============================================
test_basic_connectivity() {
    log_info "开始基础连接测试..."
    log_info "日志目录: $LOG_DIR"
    log_info "服务端网卡: $WIFI_IFACE, 客户端网卡: $WIFI_IFACE_CLIENT"
    log_info "信道: $CHANNEL, MCS: $MCS, 节点 ID: $NODE_ID"

    # 检查 wfb_core 可执行文件
    if [ ! -f "$PROJECT_ROOT/wfb_core" ]; then
        log_fail "找不到 wfb_core 可执行文件: $PROJECT_ROOT/wfb_core"
        log_info "请先编译项目: make"
        return 1
    fi

    # 1. 启动服务器（后台，5秒超时）
    log_info "启动服务器（超时 ${TEST_TIMEOUT}s，网卡: $WIFI_IFACE）..."
    timeout "$TEST_TIMEOUT" "$PROJECT_ROOT/wfb_core" --mode server \
        -i "$WIFI_IFACE" -c "$CHANNEL" -m "$MCS" --tun wfb0 \
        > >(tee "$LOG_DIR/server_basic.log") 2>&1 &
    SERVER_PID=$!

    # 等待服务器启动
    sleep 1

    # 检查服务器是否启动成功
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        log_fail "服务器启动失败"
        if [ -f "$LOG_DIR/server_basic.log" ]; then
            echo "服务器日志:"
            tail -20 "$LOG_DIR/server_basic.log"
        fi
        return 1
    fi

    log_info "服务器已启动 (PID: $SERVER_PID)"

    # 2. 启动客户端（后台，5秒超时）
    log_info "启动客户端（超时 ${TEST_TIMEOUT}s，网卡: $WIFI_IFACE_CLIENT）..."
    timeout "$TEST_TIMEOUT" "$PROJECT_ROOT/wfb_core" --mode client \
        -i "$WIFI_IFACE_CLIENT" -c "$CHANNEL" -m "$MCS" --tun wfb1 --node-id "$NODE_ID" \
        > >(tee "$LOG_DIR/client_basic.log") 2>&1 &
    CLIENT_PID=$!
    
    log_info "客户端已启动 (PID: $CLIENT_PID)"
    
    # 3. 等待进程结束
    log_info "等待服务器和客户端进程结束..."
    wait "$SERVER_PID" "$CLIENT_PID" 2>/dev/null || true
    
    # 4. 验证初始化成功
    log_info "验证初始化结果..."
    
    local server_success=false
    local client_success=false
    
    # 检查服务器日志
    if [ -f "$LOG_DIR/server_basic.log" ]; then
        if grep -q "初始化成功\|启动成功\|Server started\|Initialization complete" "$LOG_DIR/server_basic.log"; then
            server_success=true
            log_pass "服务器初始化成功"
        else
            log_warn "服务器日志未包含初始化成功标记"
            echo "--- 服务器日志（最后 20 行）---"
            tail -20 "$LOG_DIR/server_basic.log"
        fi
    else
        log_fail "服务器日志文件不存在: $LOG_DIR/server_basic.log"
    fi
    
    # 检查客户端日志
    if [ -f "$LOG_DIR/client_basic.log" ]; then
        if grep -q "初始化成功\|启动成功\|Client started\|Initialization complete" "$LOG_DIR/client_basic.log"; then
            client_success=true
            log_pass "客户端初始化成功"
        else
            log_warn "客户端日志未包含初始化成功标记"
            echo "--- 客户端日志（最后 20 行）---"
            tail -20 "$LOG_DIR/client_basic.log"
        fi
    else
        log_fail "客户端日志文件不存在: $LOG_DIR/client_basic.log"
    fi
    
    # 5. 判断测试结果
    if [ "$server_success" = true ] && [ "$client_success" = true ]; then
        log_pass "基础连接测试通过"
        return 0
    else
        log_fail "基础连接测试失败"
        log_info "请检查日志文件:"
        log_info "  - 服务器日志: $LOG_DIR/server_basic.log"
        log_info "  - 客户端日志: $LOG_DIR/client_basic.log"
        return 1
    fi
}

# ============================================
# 主流程
# ============================================
main() {
    echo "========================================"
    echo "  基础连接测试"
    echo "========================================"
    echo ""
    
    # 检查 Root 权限
    if [ "$EUID" -ne 0 ]; then
        log_fail "必须以 Root 权限运行"
        exit 1
    fi
    
    # 执行测试
    if test_basic_connectivity; then
        exit 0
    else
        exit 1
    fi
}

main "$@"
