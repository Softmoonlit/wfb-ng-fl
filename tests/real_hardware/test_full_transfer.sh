#!/bin/bash
# tests/real_hardware/test_full_transfer.sh
# 完整传输测试脚本 - 40MB 模型文件端到端传输测试
#
# 用途：验证系统在真实硬件环境下能稳定传输 40MB 联邦学习模型
#
# 使用方法:
#   sudo ./tests/real_hardware/test_full_transfer.sh
#
# 环境变量:
#   WIFI_IFACE        - 服务端 WiFi 网卡接口名称（默认: wlan0）
#   WIFI_IFACE_CLIENT - 客户端 WiFi 网卡接口名称（默认: wlan1，如果未设置则使用 WIFI_IFACE）
#
# 依赖:
#   - 测试配置文件: tests/config/test_config.sh
#   - 测试数据文件: tests/test_data/model_40mb.bin
#   - UFTP 工具: uftp, uftpd
#   - wfb_core 二进制文件
#
# 日志输出:
#   - 服务器日志: $LOG_DIR/server_transfer.log
#   - 客户端日志: $LOG_DIR/client_transfer.log
#   - UFTP 服务器日志: $LOG_DIR/uftp_server.log
#   - UFTP 客户端日志: $LOG_DIR/uftp_client.log
#   - 抓包文件: $LOG_DIR/capture.pcap（可选）

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

# ============================================
# 辅助函数
# ============================================
log_info()  { echo "[INFO] $(date '+%H:%M:%S') $1"; }
log_pass()  { echo "[PASS] $(date '+%H:%M:%S') $1"; }
log_fail()  { echo "[FAIL] $(date '+%H:%M:%S') $1" >&2; }
log_warn()  { echo "[WARN] $(date '+%H:%M:%S') $1" >&2; }

# ============================================
# 清理机制
# ============================================
cleanup() {
    log_info "清理测试进程..."
    
    # 终止所有后台进程
    if [ -n "$SERVER_PID" ]; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    if [ -n "$CLIENT_PID" ]; then
        kill "$CLIENT_PID" 2>/dev/null || true
        wait "$CLIENT_PID" 2>/dev/null || true
    fi
    if [ -n "$UFTP_SERVER_PID" ]; then
        kill "$UFTP_SERVER_PID" 2>/dev/null || true
        wait "$UFTP_SERVER_PID" 2>/dev/null || true
    fi
    if [ -n "$UFTP_CLIENT_PID" ]; then
        kill "$UFTP_CLIENT_PID" 2>/dev/null || true
        wait "$UFTP_CLIENT_PID" 2>/dev/null || true
    fi
    if [ -n "$TCPDUMP_PID" ]; then
        kill "$TCPDUMP_PID" 2>/dev/null || true
        wait "$TCPDUMP_PID" 2>/dev/null || true
    fi
    
    log_pass "清理完成"
}

# 设置清理陷阱
trap cleanup EXIT

# ============================================
# 传输完成检测
# ============================================
wait_for_transfer_complete() {
    local timeout=$TEST_TIMEOUT
    local elapsed=0
    local check_interval=1
    
    log_info "等待传输完成（最长 ${timeout}s）..."
    
    while [ $elapsed -lt $timeout ]; do
        # 检查 UFTP 服务器日志中的传输完成标记
        if grep -q "传输完成\|Transfer complete\|File transfer complete" "$LOG_DIR/uftp_server.log" 2>/dev/null; then
            log_pass "传输完成！耗时: ${elapsed}s"
            return 0
        fi
        
        # 检查是否有错误
        if grep -q "传输失败\|Transfer failed\|ERROR" "$LOG_DIR/uftp_server.log" 2>/dev/null; then
            log_fail "传输失败，请查看日志: $LOG_DIR/uftp_server.log"
            return 1
        fi
        
        # 打印进度（每 10 秒）
        if [ $((elapsed % 10)) -eq 0 ] && [ $elapsed -gt 0 ]; then
            log_info "传输进行中... (${elapsed}s / ${timeout}s)"
        fi
        
        sleep $check_interval
        elapsed=$((elapsed + check_interval))
    done
    
    log_fail "传输超时（${timeout}s），请查看日志: $LOG_DIR/uftp_server.log"
    return 1
}

# ============================================
# 完整传输测试
# ============================================
test_full_transfer() {
    log_info "[Test 3] 40MB 完整传输测试..."
    
    # 1. 检查测试数据文件
    if [ ! -f "$TEST_MODEL" ]; then
        log_fail "测试数据文件不存在: $TEST_MODEL"
        log_info "请运行以下命令生成测试数据："
        log_info "  dd if=/dev/urandom of=$TEST_MODEL bs=1M count=40"
        return 1
    fi
    
    # 2. 检查 UFTP 工具
    if ! command -v uftp &>/dev/null; then
        log_fail "未找到 uftp 命令，请安装 UFTP 工具"
        log_info "安装方法: sudo apt-get install uftp"
        return 1
    fi
    
    if ! command -v uftpd &>/dev/null; then
        log_fail "未找到 uftpd 命令，请安装 UFTP 工具"
        log_info "安装方法: sudo apt-get install uftp"
        return 1
    fi
    
    # 3. 检查 wfb_core 二进制文件
    if [ ! -f "$PROJECT_ROOT/wfb_core" ]; then
        log_fail "未找到 wfb_core 二进制文件: $PROJECT_ROOT/wfb_core"
        log_info "请先编译项目: make"
        return 1
    fi
    
    # 4. 启动抓包（可选，用于事后分析）
    if command -v tcpdump &>/dev/null; then
        log_info "启动抓包..."
        tcpdump -i "$WIFI_IFACE" -w "$LOG_DIR/capture.pcap" 2>/dev/null &
        TCPDUMP_PID=$!
        sleep 1
    else
        log_warn "未找到 tcpdump，跳过抓包"
    fi
    
    # 5. 启动 wfb_core 服务器
    log_info "启动 wfb_core 服务器（网卡: $WIFI_IFACE）..."
    "$PROJECT_ROOT/wfb_core" --mode server \
        -i "$WIFI_IFACE" -c "$CHANNEL" -m "$MCS" --tun wfb0 \
        --fec-n "$FEC_N" --fec-k "$FEC_K" \
        2>&1 | tee "$LOG_DIR/server_transfer.log" &
    SERVER_PID=$!
    sleep 2

    # 验证服务器启动
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        log_fail "服务器启动失败，请查看日志: $LOG_DIR/server_transfer.log"
        return 1
    fi

    # 6. 启动 wfb_core 客户端
    log_info "启动 wfb_core 客户端（网卡: $WIFI_IFACE_CLIENT）..."
    "$PROJECT_ROOT/wfb_core" --mode client \
        -i "$WIFI_IFACE_CLIENT" -c "$CHANNEL" -m "$MCS" --tun wfb1 --node-id "$NODE_ID" \
        --fec-n "$FEC_N" --fec-k "$FEC_K" \
        2>&1 | tee "$LOG_DIR/client_transfer.log" &
    CLIENT_PID=$!
    sleep 2

    # 验证客户端启动
    if ! kill -0 "$CLIENT_PID" 2>/dev/null; then
        log_fail "客户端启动失败，请查看日志: $LOG_DIR/client_transfer.log"
        return 1
    fi
    
    # 启动 UFTP 服务器 - 传输 tests/test_data/model_40mb.bin 文件
    log_info "启动 UFTP 服务器..."
    log_info "  文件: $TEST_MODEL"
    log_info "  速率: ${UFTP_RATE}kbps"
    log_info "  组播: $UFTP_GROUP:$UFTP_PORT"
    
    # uftp 参数说明：
    # -R 7200: 传输速率 7200kbps
    # -g 224.1.1.1: 组播地址
    # -p 1042: 组播端口
    # -T: 显示时间戳
    # -r 5: 重传次数
    uftp -R $UFTP_RATE -g $UFTP_GROUP -p $UFTP_PORT \
        -T 10 -r $UFTP_RETRIES "$TEST_MODEL" \
        2>&1 | tee "$LOG_DIR/uftp_server.log" &
    UFTP_SERVER_PID=$!
    sleep 2
    
    # 验证 UFTP 服务器启动
    if ! kill -0 "$UFTP_SERVER_PID" 2>/dev/null; then
        log_fail "UFTP 服务器启动失败，请查看日志: $LOG_DIR/uftp_server.log"
        return 1
    fi
    
    # 8. 启动 UFTP 客户端
    log_info "启动 UFTP 客户端..."
    # 创建接收目录
    mkdir -p "$LOG_DIR/received"

    # uftpd 参数说明：
    # -p 1042: 监听端口
    # -M 224.1.1.1: 组播地址
    # -D: 接收文件保存目录
    # -d: 前台运行（非守护进程模式）
    uftpd -d -p $UFTP_PORT -M $UFTP_GROUP -D "$LOG_DIR/received" \
        2>&1 | tee "$LOG_DIR/uftp_client.log" &
    UFTP_CLIENT_PID=$!
    sleep 2
    
    # 验证 UFTP 客户端启动
    if ! kill -0 "$UFTP_CLIENT_PID" 2>/dev/null; then
        log_fail "UFTP 客户端启动失败，请查看日志: $LOG_DIR/uftp_client.log"
        return 1
    fi
    
    # 9. 等待传输完成
    if wait_for_transfer_complete; then
        log_pass "[Test 3] 40MB 完整传输测试通过"
        return 0
    else
        log_fail "[Test 3] 40MB 完整传输测试失败"
        log_info "调试建议："
        log_info "  1. 检查网卡是否在正确的 Monitor 模式: iwconfig $WIFI_IFACE"
        log_info "  2. 检查服务器和客户端是否使用相同的信道: iw dev $WIFI_IFACE info"
        log_info "  3. 检查 UFTP 日志: cat $LOG_DIR/uftp_server.log"
        log_info "  4. 检查 wfb_core 日志: cat $LOG_DIR/server_transfer.log"
        log_info "  5. 使用 Wireshark 分析抓包文件: wireshark $LOG_DIR/capture.pcap"
        return 1
    fi
}

# ============================================
# 主流程
# ============================================
main() {
    echo "========================================"
    echo "  40MB 完整传输测试"
    echo "========================================"
    echo ""
    
    # 打印配置摘要
    print_config
    echo ""
    
    # 创建日志目录
    prepare_log_dir
    
    # 执行测试
    if test_full_transfer; then
        echo ""
        log_pass "测试完成。日志目录: $LOG_DIR"
        exit 0
    else
        echo ""
        log_fail "测试失败。日志目录: $LOG_DIR"
        exit 1
    fi
}

main "$@"
