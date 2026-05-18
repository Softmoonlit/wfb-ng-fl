#!/bin/bash
# tests/real_hardware/collect_metrics.sh
# 指标收集脚本 - 细粒度全层级指标收集
#
# 用途：收集应用层、操作系统层和 MAC 层的多维度指标，用于性能分析和问题定位
#
# 使用方法:
#   source tests/real_hardware/collect_metrics.sh
#   collect_all_metrics
#
# 依赖:
#   - 测试配置文件: tests/config/test_config.sh
#   - 日志文件: $LOG_DIR/*.log
#   - 系统统计: /sys/class/net/wfb0/statistics/
#
# 输出:
#   - JSON 格式指标文件: $LOG_DIR/metrics.json
#   - 终端打印指标摘要

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
# 应用层指标收集
# ============================================
collect_app_metrics() {
    log_info "收集应用层指标..."
    
    # 从 UFTP 日志提取传输耗时
    # 格式：传输完成，耗时: 45.2s 或 Transfer complete, duration: 45.2s
    UFTP_DURATION=$(grep "传输完成\|Transfer complete\|File transfer complete" "$LOG_DIR/uftp_server.log" 2>/dev/null | \
                    tail -1 | grep -oP '耗时: \K[0-9.]+' || \
                    grep "传输完成\|Transfer complete\|File transfer complete" "$LOG_DIR/uftp_server.log" 2>/dev/null | \
                    tail -1 | grep -oP 'duration: \K[0-9.]+' || \
                    echo "N/A")
    
    # 从 UFTP 日志提取重传次数
    # 格式：重传次数: 5 或 retries: 5
    UFTP_RETRIES=$(grep "重传次数\|retries" "$LOG_DIR/uftp_server.log" 2>/dev/null | \
                    tail -1 | grep -oP '次数: \K[0-9]+' || \
                    grep "重传次数\|retries" "$LOG_DIR/uftp_server.log" 2>/dev/null | \
                    tail -1 | grep -oP 'retries: \K[0-9]+' || \
                    echo "0")
    
    # 检查传输状态
    if grep -q "传输完成\|Transfer complete\|File transfer complete" "$LOG_DIR/uftp_server.log" 2>/dev/null; then
        TRANSFER_STATUS="success"
    else
        TRANSFER_STATUS="failed"
    fi
    
    log_info "  传输状态: ${TRANSFER_STATUS}"
    log_info "  传输耗时: ${UFTP_DURATION}s"
    log_info "  重传次数: ${UFTP_RETRIES}"
}

# ============================================
# 操作系统层指标收集
# ============================================
collect_os_metrics() {
    log_info "收集操作系统层指标..."
    
    # TUN 设备名称
    local tun_dev="wfb0"
    
    # 检查 TUN 设备是否存在
    if [ ! -e "/sys/class/net/$tun_dev/statistics/tx_dropped" ]; then
        log_warn "TUN 设备 $tun_dev 不存在，跳过 OS 层指标"
        TX_DROPPED=0
        TX_ERRORS=0
        TX_PACKETS=0
        return 0
    fi
    
    # 从系统统计提取 TUN 队列指标
    TX_DROPPED=$(cat /sys/class/net/$tun_dev/statistics/tx_dropped 2>/dev/null || echo "0")
    TX_ERRORS=$(cat /sys/class/net/$tun_dev/statistics/tx_errors 2>/dev/null || echo "0")
    TX_PACKETS=$(cat /sys/class/net/$tun_dev/statistics/tx_packets 2>/dev/null || echo "0")
    
    log_info "  TUN TX 丢包: ${TX_DROPPED}"
    log_info "  TUN TX 错误: ${TX_ERRORS}"
    log_info "  TUN TX 总包: ${TX_PACKETS}"
    
    # 计算 TUN 层丢包率
    if [ "$TX_PACKETS" -gt 0 ]; then
        local drop_rate=$(echo "scale=2; $TX_DROPPED * 100 / $TX_PACKETS" | bc 2>/dev/null || echo "0")
        log_info "  TUN 丢包率: ${drop_rate}%"
    fi
}

# ============================================
# MAC 层指标收集
# ============================================
collect_mac_metrics() {
    log_info "收集 MAC 层指标..."
    
    # 从 wfb_core 服务器日志提取 Token 发送次数
    # 格式：Token 发送 或 Token sent
    TOKENS_SENT=$(grep -c "Token 发送\|Token sent" "$LOG_DIR/server.log" 2>/dev/null || \
                  grep -c "Token 发送\|Token sent" "$LOG_DIR/server_transfer.log" 2>/dev/null || \
                  echo "0")
    
    # 从 wfb_core 客户端日志提取 Token 接收次数
    # 格式：Token 接收 或 Token received
    TOKENS_RECEIVED=$(grep -c "Token 接收\|Token received" "$LOG_DIR/client.log" 2>/dev/null || \
                      grep -c "Token 接收\|Token received" "$LOG_DIR/client_transfer.log" 2>/dev/null || \
                      echo "0")
    
    # 从 wfb_core 服务器日志提取数据包发送次数
    # 格式：发送数据包 或 Packet sent
    PACKETS_SENT=$(grep -c "发送数据包\|Packet sent" "$LOG_DIR/server.log" 2>/dev/null || \
                   grep -c "发送数据包\|Packet sent" "$LOG_DIR/server_transfer.log" 2>/dev/null || \
                   echo "0")
    
    # 从 wfb_core 客户端日志提取数据包接收次数
    # 格式：接收数据包 或 Packet received
    PACKETS_RECEIVED=$(grep -c "接收数据包\|Packet received" "$LOG_DIR/client.log" 2>/dev/null || \
                       grep -c "接收数据包\|Packet received" "$LOG_DIR/client_transfer.log" 2>/dev/null || \
                       echo "0")
    
    log_info "  Token 发送: ${TOKENS_SENT}"
    log_info "  Token 接收: ${TOKENS_RECEIVED}"
    log_info "  数据包发送: ${PACKETS_SENT}"
    log_info "  数据包接收: ${PACKETS_RECEIVED}"
    
    # 计算 Token 丢失率
    if [ "$TOKENS_SENT" -gt 0 ]; then
        local token_loss=$((TOKENS_SENT - TOKENS_RECEIVED))
        local token_loss_rate=$(echo "scale=2; $token_loss * 100 / $TOKENS_SENT" | bc 2>/dev/null || echo "0")
        log_info "  Token 丢失率: ${token_loss_rate}%"
    fi
}

collect_token_metrics() {
    log_info "收集 Token 验收指标..."

    local scheduler_log="$LOG_DIR/scheduler.log"
    local client1_log="$LOG_DIR/client1.log"
    local client2_log="$LOG_DIR/client2.log"

    TOKEN_GRANTS=$(grep -c '^grant seq=' "$scheduler_log" 2>/dev/null || echo "0")
    TOKEN_GUARDS=$(grep -c '^guard seq=' "$scheduler_log" 2>/dev/null || echo "0")
    CLIENT1_TOKEN_FILTER=$(grep 'TOKEN_FILTER' "$client1_log" 2>/dev/null | tail -1 | sed 's/"/\\"/g' || true)
    CLIENT1_TOKEN_AUTH=$(grep 'TOKEN_AUTH' "$client1_log" 2>/dev/null | tail -1 | sed 's/"/\\"/g' || true)
    CLIENT2_TOKEN_FILTER=$(grep 'TOKEN_FILTER' "$client2_log" 2>/dev/null | tail -1 | sed 's/"/\\"/g' || true)
    CLIENT2_TOKEN_AUTH=$(grep 'TOKEN_AUTH' "$client2_log" 2>/dev/null | tail -1 | sed 's/"/\\"/g' || true)

    log_info "  grant 次数: ${TOKEN_GRANTS}"
    log_info "  guard 次数: ${TOKEN_GUARDS}"
    [ -n "${CLIENT1_TOKEN_FILTER:-}" ] && log_info "  client1 TOKEN_FILTER: ${CLIENT1_TOKEN_FILTER}"
    [ -n "${CLIENT1_TOKEN_AUTH:-}" ] && log_info "  client1 TOKEN_AUTH: ${CLIENT1_TOKEN_AUTH}"
    [ -n "${CLIENT2_TOKEN_FILTER:-}" ] && log_info "  client2 TOKEN_FILTER: ${CLIENT2_TOKEN_FILTER}"
    [ -n "${CLIENT2_TOKEN_AUTH:-}" ] && log_info "  client2 TOKEN_AUTH: ${CLIENT2_TOKEN_AUTH}"
}

# ============================================
# 指标汇总与 JSON 输出
# ============================================
generate_metrics_json() {
    log_info "生成指标文件..."
    
    # 处理 N/A 值
    local duration="${UFTP_DURATION}"
    if [ "$duration" = "N/A" ]; then
        duration="null"
    fi
    
    cat > "$LOG_DIR/metrics.json" << EOF
{
    "timestamp": "$(date -Iseconds)",
    "test_config": {
        "wifi_iface": "$WIFI_IFACE",
        "channel": $CHANNEL,
        "mcs": $MCS,
        "node_id": $NODE_ID,
        "uftp_rate_kbps": $UFTP_RATE
    },
    "application": {
        "transfer_duration_s": $duration,
        "retries": ${UFTP_RETRIES:-0},
        "status": "$TRANSFER_STATUS"
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

# ============================================
# 打印指标摘要
# ============================================
print_metrics_summary() {
    echo ""
    echo "========================================"
    echo "  指标收集摘要"
    echo "========================================"
    echo ""
    
    echo "--- 应用层指标 ---"
    echo "传输状态: ${TRANSFER_STATUS}"
    echo "传输耗时: ${UFTP_DURATION}s"
    echo "重传次数: ${UFTP_RETRIES}"
    echo ""
    
    echo "--- 操作系统层指标 ---"
    echo "TUN TX 丢包: ${TX_DROPPED:-0}"
    echo "TUN TX 错误: ${TX_ERRORS:-0}"
    echo "TUN TX 总包: ${TX_PACKETS:-0}"
    echo ""
    
    echo "--- Token 验收指标 ---"
    echo "Scheduler grant: ${TOKEN_GRANTS:-0}"
    echo "Scheduler guard: ${TOKEN_GUARDS:-0}"
    echo "Client1 TOKEN_FILTER: ${CLIENT1_TOKEN_FILTER:-N/A}"
    echo "Client1 TOKEN_AUTH: ${CLIENT1_TOKEN_AUTH:-N/A}"
    echo "Client2 TOKEN_FILTER: ${CLIENT2_TOKEN_FILTER:-N/A}"
    echo "Client2 TOKEN_AUTH: ${CLIENT2_TOKEN_AUTH:-N/A}"
    echo ""

    echo "========================================"
}

# ============================================
# 主流程：收集所有指标
# ============================================
collect_all_metrics() {
    echo "========================================"
    echo "  开始收集测试指标"
    echo "========================================"
    echo ""
    
    # 检查日志目录
    if [ ! -d "$LOG_DIR" ]; then
        log_fail "日志目录不存在: $LOG_DIR"
        return 1
    fi
    
    # 依次收集三层指标
    collect_app_metrics
    collect_os_metrics
    collect_mac_metrics
    
    # 生成 JSON 文件
    generate_metrics_json
    
    # 打印摘要
    print_metrics_summary
    
    log_pass "指标收集完成"
    return 0
}

# ============================================
# 如果直接运行脚本（而非 source）
# ============================================
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    collect_all_metrics
fi
