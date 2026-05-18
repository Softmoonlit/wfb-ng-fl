#!/bin/bash
# ============================================================================
# 环境检查脚本 - 真实硬件测试环境验证
# ============================================================================
#
# 用途：验证测试环境是否满足真实硬件测试要求
# 用法：bash tests/real_hardware/check_environment.sh [接口名]
#
# 检查项：
# 1. Root 权限验证
# 2. WiFi 网卡存在性检查
# 3. Monitor 模式支持检查
# 4. TUN 模块检查
# 5. pcap 库检查
# 6. UFTP 工具检查
#
# 返回值：
#   0 - 所有检查通过
#   1 - 至少一项检查失败
#
# ============================================================================

set -e

# ============================================================================
# 配置
# ============================================================================
WIFI_IFACE="${1:-wlan0}"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# ============================================================================
# 辅助函数
# ============================================================================
log_pass() {
    echo -e "${GREEN}✓ 通过${NC}"
}

log_fail() {
    echo -e "${RED}✗ 失败${NC}"
}

log_warn() {
    echo -e "${YELLOW}⚠ 警告${NC}"
}

print_header() {
    echo ""
    echo "========================================"
    echo "  测试环境检查"
    echo "========================================"
    echo "检查接口: $WIFI_IFACE"
    echo ""
}

print_summary() {
    echo ""
    echo "========================================"
    echo -e "${GREEN}  环境检查通过${NC}"
    echo "========================================"
}

# ============================================================================
# 检查函数
# ============================================================================

# 检查 Root 权限
check_root() {
    echo "[1/6] Root 权限检查..."
    
    if [ "$EUID" -ne 0 ]; then
        log_fail
        echo "错误: 必须以 Root 权限运行"
        echo "解决: sudo $0 $@"
        return 1
    fi
    
    log_pass
    return 0
}

# 检查 WiFi 网卡存在性
check_wifi_interface() {
    echo "[2/6] WiFi 网卡存在性检查..."
    
    if ! ip link show "$WIFI_IFACE" &>/dev/null; then
        log_fail
        echo "错误: 网卡 $WIFI_IFACE 不存在"
        echo ""
        echo "可用网卡列表:"
        ip link show | grep -E "^[0-9]+:" | awk '{print "  " $2}' | tr -d ':'
        echo ""
        echo "提示: 检查网卡名称或使用参数指定接口"
        echo "用法: $0 <接口名>"
        return 1
    fi
    
    log_pass
    echo "  接口状态: $(ip link show "$WIFI_IFACE" | grep -oP 'state \K\w+')"
    return 0
}

# 检查 Monitor 模式支持
check_monitor_mode() {
    echo "[3/6] Monitor 模式支持检查..."
    
    # 检查 iw 命令是否存在
    if ! command -v iw &>/dev/null; then
        log_fail
        echo "错误: 未安装 iw 工具"
        echo "安装: sudo apt install iw"
        return 1
    fi
    
    # 检查网卡是否支持 Monitor 模式
    if ! iw list | grep -A 10 "Supported interface modes" | grep -q "monitor"; then
        # 尝试更精确的匹配
        if ! iw phy | grep -A 20 "$WIFI_IFACE" | grep -q "monitor"; then
            log_fail
            echo "错误: 网卡 $WIFI_IFACE 不支持 Monitor 模式"
            echo ""
            echo "支持的接口模式:"
            iw list | grep -A 10 "Supported interface modes" || echo "  无法获取"
            echo ""
            echo "建议: 更换支持 Monitor 模式的网卡（如 RTL8812AU）"
            return 1
        fi
    fi
    
    log_pass
    return 0
}

# 检查 TUN 模块
check_tun_module() {
    echo "[4/6] TUN 模块检查..."
    
    if [ ! -e /dev/net/tun ]; then
        echo "  尝试加载 TUN 模块..."
        modprobe tun 2>/dev/null || {
            log_fail
            echo "错误: 无法加载 TUN 模块"
            echo "解决: sudo modprobe tun"
            return 1
        }
        
        # 再次检查
        if [ ! -e /dev/net/tun ]; then
            log_fail
            echo "错误: TUN 设备不存在"
            return 1
        fi
    fi
    
    log_pass
    echo "  设备路径: /dev/net/tun"
    return 0
}

# 检查 pcap 库
check_pcap() {
    echo "[5/6] pcap 库检查..."
    
    if ! ldconfig -p 2>/dev/null | grep -q libpcap; then
        log_fail
        echo "错误: 未安装 libpcap 库"
        echo "安装: sudo apt install libpcap-dev"
        return 1
    fi
    
    log_pass
    
    # 显示版本信息
    PCAP_VERSION=$(ldconfig -p | grep libpcap | head -1 | grep -oP 'libpcap\.so\.\K[0-9.]+' || echo "未知")
    echo "  库版本: $PCAP_VERSION"
    return 0
}

# 检查 UFTP 工具
check_uftp() {
    echo "[6/6] UFTP 工具检查..."
    
    local has_uftp=false
    local has_uftp_recv=false
    
    if command -v uftp &>/dev/null; then
        has_uftp=true
        UFTP_VERSION=$(uftp -V 2>&1 | head -1 || echo "未知")
    fi
    
    if command -v uftp_recv &>/dev/null; then
        has_uftp_recv=true
    fi
    
    if [ "$has_uftp" = true ] && [ "$has_uftp_recv" = true ]; then
        log_pass
        echo "  UFTP 版本: $UFTP_VERSION"
        return 0
    fi
    
    # 只警告，不阻止测试
    log_warn
    
    if [ "$has_uftp" = false ]; then
        echo "  缺少: uftp（服务器端工具）"
    fi
    
    if [ "$has_uftp_recv" = false ]; then
        echo "  缺少: uftp_recv（客户端工具）"
    fi
    
    echo ""
    echo "安装: sudo apt install uftp"
    echo ""
    echo "注意: UFTP 未安装将跳过应用层集成测试"
    return 0
}

# ============================================================================
# 扩展检查（可选）
# ============================================================================

# 检查网卡当前模式
check_current_mode() {
    echo ""
    echo "--- 网卡当前模式 ---"
    
    if command -v iw &>/dev/null; then
        local mode=$(iw dev "$WIFI_IFACE" info 2>/dev/null | grep -oP 'type \K\w+' || echo "未知")
        echo "当前模式: $mode"
        
        if [ "$mode" != "monitor" ]; then
            echo ""
            echo "提示: 网卡当前不在 Monitor 模式"
            echo "设置命令:"
            echo "  sudo ip link set $WIFI_IFACE down"
            echo "  sudo iw dev $WIFI_IFACE set type monitor"
            echo "  sudo ip link set $WIFI_IFACE up"
        fi
    fi
}

# 检查其他依赖
check_other_dependencies() {
    echo ""
    echo "--- 其他依赖检查 ---"
    
    local deps=("tcpdump" "iw" "ip" "timeout")
    local missing=()
    
    for dep in "${deps[@]}"; do
        if ! command -v "$dep" &>/dev/null; then
            missing+=("$dep")
        fi
    done
    
    if [ ${#missing[@]} -eq 0 ]; then
        echo "✓ 所有依赖工具已安装"
    else
        echo "缺失工具: ${missing[*]}"
        echo "安装: sudo apt install ${missing[*]}"
    fi
}

# ============================================================================
# 主函数
# ============================================================================
main() {
    print_header
    
    local failed=0
    
    # 执行检查
    check_root || ((failed++))
    check_wifi_interface || ((failed++))
    check_monitor_mode || ((failed++))
    check_tun_module || ((failed++))
    check_pcap || ((failed++))
    check_uftp || true  # UFTP 缺失不阻止测试
    
    # 扩展检查
    check_current_mode
    check_other_dependencies
    
    # 总结
    if [ $failed -eq 0 ]; then
        print_summary
        exit 0
    else
        echo ""
        echo "========================================"
        echo -e "${RED}  环境检查失败${NC}"
        echo "========================================"
        echo "失败项数: $failed"
        echo ""
        echo "请解决上述问题后重新运行检查"
        exit 1
    fi
}

# ============================================================================
# 脚本入口
# ============================================================================
main "$@"
