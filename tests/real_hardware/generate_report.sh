#!/bin/bash
# tests/real_hardware/generate_report.sh
# 报告生成脚本 - 测试结果可视化展示
#
# 用途：生成测试结果摘要文件和终端打印，方便用户快速了解测试状态和定位问题
#
# 使用方法:
#   source tests/real_hardware/generate_report.sh
#   generate_summary_file
#   print_terminal_summary
#
# 依赖:
#   - 测试配置文件: tests/config/test_config.sh
#   - 日志文件: $LOG_DIR/*.log
#   - 指标文件: $LOG_DIR/metrics.json
#
# 输出:
#   - 摘要文件: $LOG_DIR/summary.txt
#   - 终端摘要打印

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

# ============================================
# 辅助函数
# ============================================
log_info()  { echo "[INFO] $(date '+%H:%M:%S') $1"; }
log_pass()  { echo "[PASS] $(date '+%H:%M:%S') $1"; }
log_fail()  { echo "[FAIL] $(date '+%H:%M:%S') $1" >&2; }
log_warn()  { echo "[WARN] $(date '+%H:%M:%S') $1" >&2; }

# ============================================
# 摘要文件生成
# ============================================
generate_summary_file() {
    log_info "生成摘要文件..."
    
    # 创建摘要文件头部
    cat > "$LOG_DIR/summary.txt" << EOF
========================================
    真实硬件测试报告
========================================
测试时间: $(date)
日志目录: $LOG_DIR

--- 测试配置 ---
网卡接口: $WIFI_IFACE
信道: $CHANNEL
MCS: $MCS
节点 ID: $NODE_ID
UFTP 速率: ${UFTP_RATE} kbps

--- 测试结果 ---
EOF
    
    # 检查基础连接测试结果
    if [ -f "$LOG_DIR/server_basic.log" ]; then
        if grep -q '初始化成功\|启动成功' "$LOG_DIR/server_basic.log" 2>/dev/null; then
            echo "基础连接: PASS" >> "$LOG_DIR/summary.txt"
        else
            echo "基础连接: FAIL" >> "$LOG_DIR/summary.txt"
        fi
    elif [ -f "$LOG_DIR/server.log" ]; then
        if grep -q '初始化成功\|启动成功' "$LOG_DIR/server.log" 2>/dev/null; then
            echo "基础连接: PASS" >> "$LOG_DIR/summary.txt"
        else
            echo "基础连接: FAIL" >> "$LOG_DIR/summary.txt"
        fi
    else
        echo "基础连接: N/A (未执行)" >> "$LOG_DIR/summary.txt"
    fi
    
    # 检查完整传输测试结果
    if [ -f "$LOG_DIR/uftp_server.log" ]; then
        if grep -q '传输完成\|Transfer complete\|File transfer complete' "$LOG_DIR/uftp_server.log" 2>/dev/null; then
            echo "完整传输: PASS" >> "$LOG_DIR/summary.txt"
        else
            echo "完整传输: FAIL" >> "$LOG_DIR/summary.txt"
        fi
    else
        echo "完整传输: N/A (未执行)" >> "$LOG_DIR/summary.txt"
    fi
    
    # 检查 Token 验收测试结果
    if [ -f "$LOG_DIR/token_results.tsv" ]; then
        local token_fail_count=$(grep -c $'\tFAIL\t' "$LOG_DIR/token_results.tsv" 2>/dev/null || echo '0')
        local token_pass_count=$(grep -c $'\tPASS\t' "$LOG_DIR/token_results.tsv" 2>/dev/null || echo '0')
        local token_skip_count=$(grep -c $'\tSKIP\t' "$LOG_DIR/token_results.tsv" 2>/dev/null || echo '0')
        echo "Token 验收: PASS=${token_pass_count} FAIL=${token_fail_count} SKIP=${token_skip_count}" >> "$LOG_DIR/summary.txt"
    else
        echo "Token 验收: N/A (未执行)" >> "$LOG_DIR/summary.txt"
    fi

    echo "" >> "$LOG_DIR/summary.txt"
    echo "--- 指标汇总 ---" >> "$LOG_DIR/summary.txt"
    
    # 从 metrics.json 提取关键指标
    if [ -f "$LOG_DIR/metrics.json" ]; then
        # 提取传输耗时
        local duration=$(grep 'transfer_duration_s' "$LOG_DIR/metrics.json" | grep -oP ': \K[0-9.]+')
        if [ -n "$duration" ] && [ "$duration" != "null" ]; then
            echo "传输耗时: ${duration}s" >> "$LOG_DIR/summary.txt"
        fi
        
        # 提取 Token 发送次数
        local tokens=$(grep 'tokens_sent' "$LOG_DIR/metrics.json" | grep -oP ': \K[0-9]+')
        if [ -n "$tokens" ]; then
            echo "Token 发送: $tokens" >> "$LOG_DIR/summary.txt"
        fi
        
        # 提取数据包发送次数
        local packets=$(grep 'packets_sent' "$LOG_DIR/metrics.json" | grep -oP ': \K[0-9]+')
        if [ -n "$packets" ]; then
            echo "数据包发送: $packets" >> "$LOG_DIR/summary.txt"
        fi
        
        # 提取 scheduler grant 次数
        local scheduler_grants=$(grep 'scheduler_grants' "$LOG_DIR/metrics.json" | grep -oP ': \K[0-9]+')
        if [ -n "$scheduler_grants" ]; then
            echo "Scheduler grant: $scheduler_grants" >> "$LOG_DIR/summary.txt"
        fi

        local client1_auth=$(grep 'client1_token_auth' "$LOG_DIR/metrics.json" | head -1 | sed 's/^.*: "//; s/",*$//')
        if [ -n "$client1_auth" ]; then
            echo "Client1 TOKEN_AUTH: $client1_auth" >> "$LOG_DIR/summary.txt"
        fi
    else
        echo "指标文件不存在" >> "$LOG_DIR/summary.txt"
    fi
    
    echo "" >> "$LOG_DIR/summary.txt"
    echo "--- 详细日志 ---" >> "$LOG_DIR/summary.txt"
    echo "日志目录: $LOG_DIR" >> "$LOG_DIR/summary.txt"
    echo "  - server.log: 服务端日志" >> "$LOG_DIR/summary.txt"
    echo "  - client.log: 客户端日志" >> "$LOG_DIR/summary.txt"
    echo "  - uftp_server.log: UFTP 服务端日志" >> "$LOG_DIR/summary.txt"
    echo "  - uftp_client.log: UFTP 客户端日志" >> "$LOG_DIR/summary.txt"
    echo "  - metrics.json: 性能指标" >> "$LOG_DIR/summary.txt"
    echo "  - token_results.md: Token 验收摘要" >> "$LOG_DIR/summary.txt"
    echo "  - token_results.tsv: Token 验收原始结果" >> "$LOG_DIR/summary.txt"
    echo "  - token_context.txt: Token 验收上下文与自动分析摘录" >> "$LOG_DIR/summary.txt"
    echo "  - dual_long_run_samples.tsv: 双客户端长稳主场景采样留痕" >> "$LOG_DIR/summary.txt"
    echo "  - capture.pcap: 网络抓包（如启用）" >> "$LOG_DIR/summary.txt"
    
    log_pass "摘要文件已生成: $LOG_DIR/summary.txt"
}

# ============================================
# 终端摘要打印
# ============================================
print_terminal_summary() {
    echo ""
    echo "========================================"
    echo "       测试结果汇总"
    echo "========================================"
    echo "日志目录: $LOG_DIR"
    echo "生成时间: $(date)"
    echo ""
    
    # 1. 应用层指标
    echo "--- 应用层指标 ---"
    if [ -f "$LOG_DIR/uftp_server.log" ]; then
        echo "UFTP 传输:"
        grep "传输完成\|Transfer complete\|传输耗时\|Transfer time\|File transfer complete" "$LOG_DIR/uftp_server.log" 2>/dev/null | tail -3 || echo "  未找到传输完成记录"
        echo ""
    else
        echo "UFTP 日志不存在"
        echo ""
    fi
    
    # 2. 操作系统层指标
    echo "--- 操作系统层指标 ---"
    if [ -f "$LOG_DIR/metrics.json" ]; then
        echo "TUN 设备统计:"
        local tx_dropped=$(grep 'tun_tx_dropped' "$LOG_DIR/metrics.json" | grep -oP ': \K[0-9]+')
        local tx_errors=$(grep 'tun_tx_errors' "$LOG_DIR/metrics.json" | grep -oP ': \K[0-9]+')
        local tx_packets=$(grep 'tun_tx_packets' "$LOG_DIR/metrics.json" | grep -oP ': \K[0-9]+')
        echo "  TX 丢包: ${tx_dropped:-N/A}"
        echo "  TX 错误: ${tx_errors:-N/A}"
        echo "  TX 总包: ${tx_packets:-N/A}"
        echo ""
    else
        echo "指标文件不存在"
        echo ""
    fi
    
    # 3. MAC 层指标
    echo "--- MAC 层指标 ---"
    if [ -f "$LOG_DIR/server.log" ]; then
        echo "服务端:"
        local tokens_sent=$(grep -c "Token 发送\|Token sent" "$LOG_DIR/server.log" 2>/dev/null || echo '0')
        local packets_sent=$(grep -c "发送数据包\|Packet sent" "$LOG_DIR/server.log" 2>/dev/null || echo '0')
        echo "  Token 发送: $tokens_sent"
        echo "  数据包发送: $packets_sent"
        echo ""
    elif [ -f "$LOG_DIR/server_transfer.log" ]; then
        echo "服务端:"
        local tokens_sent=$(grep -c "Token 发送\|Token sent" "$LOG_DIR/server_transfer.log" 2>/dev/null || echo '0')
        local packets_sent=$(grep -c "发送数据包\|Packet sent" "$LOG_DIR/server_transfer.log" 2>/dev/null || echo '0')
        echo "  Token 发送: $tokens_sent"
        echo "  数据包发送: $packets_sent"
        echo ""
    else
        echo "服务端日志不存在"
        echo ""
    fi
    
    if [ -f "$LOG_DIR/client.log" ]; then
        echo "客户端:"
        local tokens_received=$(grep -c "Token 接收\|Token received" "$LOG_DIR/client.log" 2>/dev/null || echo '0')
        local packets_received=$(grep -c "接收数据包\|Packet received" "$LOG_DIR/client.log" 2>/dev/null || echo '0')
        echo "  Token 接收: $tokens_received"
        echo "  数据包接收: $packets_received"
        echo ""
    elif [ -f "$LOG_DIR/client_transfer.log" ]; then
        echo "客户端:"
        local tokens_received=$(grep -c "Token 接收\|Token received" "$LOG_DIR/client_transfer.log" 2>/dev/null || echo '0')
        local packets_received=$(grep -c "接收数据包\|Packet received" "$LOG_DIR/client_transfer.log" 2>/dev/null || echo '0')
        echo "  Token 接收: $tokens_received"
        echo "  数据包接收: $packets_received"
        echo ""
    else
        echo "客户端日志不存在"
        echo ""
    fi
    
    # 4. 汇总结果
    echo "--- 测试结果汇总 ---"
    local PASS_COUNT=0
    local FAIL_COUNT=0
    
    # 检查服务器启动状态
    if grep -q "初始化成功\|启动成功" "$LOG_DIR/server.log" 2>/dev/null; then
        echo "✓ 服务器启动: PASS"
        ((PASS_COUNT++))
    elif grep -q "初始化成功\|启动成功" "$LOG_DIR/server_basic.log" 2>/dev/null; then
        echo "✓ 服务器启动: PASS"
        ((PASS_COUNT++))
    else
        echo "✗ 服务器启动: FAIL"
        ((FAIL_COUNT++))
    fi
    
    # 检查客户端启动状态
    if grep -q "初始化成功\|启动成功" "$LOG_DIR/client.log" 2>/dev/null; then
        echo "✓ 客户端启动: PASS"
        ((PASS_COUNT++))
    elif grep -q "初始化成功\|启动成功" "$LOG_DIR/client_basic.log" 2>/dev/null; then
        echo "✓ 客户端启动: PASS"
        ((PASS_COUNT++))
    else
        echo "✗ 客户端启动: FAIL"
        ((FAIL_COUNT++))
    fi
    
    # 检查文件传输状态
    if grep -q "传输完成\|Transfer complete\|File transfer complete" "$LOG_DIR/uftp_server.log" 2>/dev/null; then
        echo "✓ 文件传输: PASS"
        ((PASS_COUNT++))
    else
        echo "✗ 文件传输: FAIL"
        ((FAIL_COUNT++))
    fi
    
    echo ""
    echo "通过: $PASS_COUNT"
    echo "失败: $FAIL_COUNT"
    echo "========================================"
    
    if [ $FAIL_COUNT -eq 0 ]; then
        echo "✅ 所有测试通过！"
        return 0
    else
        echo "❌ 存在测试失败，请检查日志。"
        return 1
    fi
}

# ============================================
# 主流程
# ============================================
generate_report() {
    # 检查日志目录
    if [ ! -d "$LOG_DIR" ]; then
        log_fail "日志目录不存在: $LOG_DIR"
        return 1
    fi
    
    # 生成摘要文件
    generate_summary_file
    
    # 打印终端摘要
    print_terminal_summary
    
    return $?
}

# ============================================
# 如果直接运行脚本（而非 source）
# ============================================
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    generate_report
fi
