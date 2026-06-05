#!/bin/bash
# tests/real_hardware/preflight_checklist.sh
# v5 real-hardware 正式跑数前检查与环境校正脚本

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [ -f "$PROJECT_ROOT/tests/config/test_config.sh" ]; then
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/tests/config/test_config.sh"
fi

MODE="check"
KILL_UFTPD=false
EXIT_CODE=0

CHANNEL="${CHANNEL:-157}"
CHANNEL_WIDTH="${CHANNEL_WIDTH:-HT40+}"
SERVER_IFACE="${SERVER_IFACE:-${WIFI_IFACE:-wlxbcec23372588}}"
CLIENT1_IFACE="${CLIENT1_IFACE:-${WIFI_IFACE_CLIENT:-wlxfca386b38672}}"
CLIENT2_IFACE="${CLIENT2_IFACE:-${WIFI_IFACE_CLIENT2:-wlxfc221c500a88}}"
IFACES=("$SERVER_IFACE" "$CLIENT1_IFACE" "$CLIENT2_IFACE")
TUN_DEVICES=("${SERVER_TUN_NAME:-rhsrvtun}" "${CLIENT1_TUN_NAME:-rhc1tun}" "${CLIENT2_TUN_NAME:-rhc2tun}")
WFB_PORTS_REGEX='5600|5602|5603|5800|5801|5802|5803|5804|5805|1044|1045'

log_info()  { echo "[INFO] $(date '+%H:%M:%S') $1"; }
log_pass()  { echo "[PASS] $(date '+%H:%M:%S') $1"; }
log_warn()  { echo "[WARN] $(date '+%H:%M:%S') $1" >&2; }
log_fail()  { echo "[FAIL] $(date '+%H:%M:%S') $1" >&2; EXIT_CODE=1; }

usage() {
    cat <<'EOF'
用法:
  bash tests/real_hardware/preflight_checklist.sh [--check-only]
  sudo bash tests/real_hardware/preflight_checklist.sh --apply [--kill-uftpd]

说明:
  --check-only   只做检查，不修改系统状态（默认）
  --apply        执行推荐的环境校正动作，再做检查
  --kill-uftpd   仅在 --apply 时生效；额外结束 uftpd 进程
  --help, -h     显示帮助

默认接口与参数可通过环境变量覆盖:
  SERVER_IFACE / CLIENT1_IFACE / CLIENT2_IFACE
  CHANNEL / CHANNEL_WIDTH
  SERVER_TUN_NAME / CLIENT1_TUN_NAME / CLIENT2_TUN_NAME

脚本覆盖的动作:
  1. 检查测试网卡是否存在
  2. 检查 rfkill 是否仍有软阻塞
  3. 检查网卡模式、状态、信道是否符合 v5 real-hardware 要求
  4. 检查遗留 wfb_* / uftp* / TUN 设备与关键监听端口
  5. 在 --apply 模式下执行推荐校正动作
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --check-only)
            MODE="check"
            shift
            ;;
        --apply)
            MODE="apply"
            shift
            ;;
        --kill-uftpd)
            KILL_UFTPD=true
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo "未知参数: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

require_command() {
    local name="$1"
    if ! command -v "$name" >/dev/null 2>&1; then
        log_fail "缺少命令: $name"
        return 1
    fi
    return 0
}

require_root_if_needed() {
    if [ "$MODE" = "apply" ] && [ "${EUID}" -ne 0 ]; then
        echo "错误: --apply 模式必须以 root 运行" >&2
        exit 1
    fi
}

print_header() {
    echo "========================================"
    echo " v5 real-hardware 跑数前检查清单"
    echo "========================================"
    echo "模式: $MODE"
    echo "server iface : $SERVER_IFACE"
    echo "client1 iface: $CLIENT1_IFACE"
    echo "client2 iface: $CLIENT2_IFACE"
    echo "目标信道: $CHANNEL / $CHANNEL_WIDTH"
    echo "TUN 设备: ${TUN_DEVICES[*]}"
    echo "========================================"
}

normalize_ifaces() {
    local unique=()
    local iface seen
    for iface in "${IFACES[@]}"; do
        [ -n "$iface" ] || continue
        seen=false
        for existing in "${unique[@]:-}"; do
            if [ "$existing" = "$iface" ]; then
                seen=true
                break
            fi
        done
        if [ "$seen" = false ]; then
            unique+=("$iface")
        fi
    done
    IFACES=("${unique[@]}")
}

check_interfaces_exist() {
    local iface
    for iface in "${IFACES[@]}"; do
        if ip link show "$iface" >/dev/null 2>&1; then
            log_pass "接口存在: $iface"
        else
            log_fail "接口不存在: $iface"
        fi
    done
}

rfkill_soft_blocked() {
    rfkill list 2>/dev/null | awk '/Soft blocked:/ { if ($3 == "yes") found=1 } END { exit(found ? 0 : 1) }'
}

check_rfkill() {
    if rfkill_soft_blocked; then
        log_fail "检测到 Wi-Fi 仍有 Soft blocked=yes"
    else
        log_pass "rfkill 未发现 Wi-Fi 软阻塞"
    fi
}

interface_mode() {
    local iface="$1"
    iw dev "$iface" info 2>/dev/null | awk '/type / { print $2; exit }'
}

interface_channel_line() {
    local iface="$1"
    iw dev "$iface" info 2>/dev/null | awk '/channel / { print; exit }'
}

check_interface_state() {
    local iface="$1"
    local mode channel_line

    if ! ip link show "$iface" >/dev/null 2>&1; then
        return
    fi

    mode="$(interface_mode "$iface")"
    channel_line="$(interface_channel_line "$iface")"

    if ip -br link show "$iface" | awk '{ print $2 }' | grep -qx 'UP'; then
        log_pass "$iface 状态为 UP"
    else
        log_fail "$iface 未处于 UP 状态"
    fi

    if [ "$mode" = "monitor" ]; then
        log_pass "$iface 处于 monitor 模式"
    else
        log_fail "$iface 当前模式不是 monitor（当前: ${mode:-未知}）"
    fi

    if printf '%s\n' "$channel_line" | grep -q "channel $CHANNEL" && printf '%s\n' "$channel_line" | grep -q "$CHANNEL_WIDTH"; then
        log_pass "$iface 已固定在信道 $CHANNEL / $CHANNEL_WIDTH"
    else
        log_fail "$iface 信道不匹配（当前: ${channel_line:-未知}）"
    fi

    if command -v nmcli >/dev/null 2>&1; then
        local managed_state
        managed_state="$(nmcli -t -f GENERAL.MANAGED device show "$iface" 2>/dev/null | awk -F: 'NR==1 { print $2 }' || true)"
        if [ "$managed_state" = "no" ]; then
            log_pass "$iface 未被 NetworkManager 管理"
        else
            log_warn "$iface 可能仍被 NetworkManager 管理（GENERAL.MANAGED=${managed_state:-未知}）"
        fi
    else
        log_warn "未安装 nmcli，跳过 NetworkManager 接管检查"
    fi
}

check_all_interface_state() {
    local iface
    for iface in "${IFACES[@]}"; do
        check_interface_state "$iface"
    done
}

check_residual_processes() {
    local any=false
    local name
    for name in wfb_tx wfb_rx wfb_tun wfb_token_scheduler uftp uftpd; do
        if pgrep -x "$name" >/dev/null 2>&1; then
            if [ "$name" = "uftpd" ]; then
                log_warn "发现遗留进程: $name（请先确认是否系统常驻服务）"
            else
                log_warn "发现遗留进程: $name"
            fi
            any=true
        fi
    done

    if [ "$any" = false ]; then
        log_pass "未发现遗留 wfb_* / uftp* 进程"
    fi
}

check_tun_devices() {
    local any=false
    local dev
    for dev in "${TUN_DEVICES[@]}"; do
        if ip link show "$dev" >/dev/null 2>&1; then
            log_warn "发现遗留 TUN 设备: $dev"
            any=true
        fi
    done

    if [ "$any" = false ]; then
        log_pass "未发现遗留 TUN 设备"
    fi
}

check_ports() {
    if ! command -v ss >/dev/null 2>&1; then
        log_warn "缺少 ss，跳过端口检查"
        return
    fi

    local matches
    matches="$(ss -lunp 2>/dev/null | grep -E "$WFB_PORTS_REGEX" || true)"
    if [ -n "$matches" ]; then
        log_warn "发现关键 UDP 端口已有监听:"
        printf '%s\n' "$matches" >&2
    else
        log_pass "关键 UDP 端口未发现遗留监听"
    fi
}

apply_recommended_actions() {
    local iface dev

    log_info "执行 rfkill unblock wifi"
    rfkill unblock wifi || true

    if command -v nmcli >/dev/null 2>&1; then
        for iface in "${IFACES[@]}"; do
            log_info "取消 NetworkManager 接管: $iface"
            nmcli device set "$iface" managed no || true
        done
    fi

    for iface in "${IFACES[@]}"; do
        if ! ip link show "$iface" >/dev/null 2>&1; then
            log_warn "跳过不存在的接口: $iface"
            continue
        fi
        log_info "校正网卡到 monitor + channel: $iface"
        ip link set "$iface" down || true
        iw dev "$iface" set type monitor
        ip link set "$iface" up
        iw dev "$iface" set channel "$CHANNEL" "$CHANNEL_WIDTH"
    done

    for name in wfb_tx wfb_rx wfb_tun wfb_token_scheduler uftp; do
        if pgrep -x "$name" >/dev/null 2>&1; then
            log_info "结束遗留进程: $name"
            pkill -x "$name" || true
        fi
    done

    if [ "$KILL_UFTPD" = true ] && pgrep -x uftpd >/dev/null 2>&1; then
        log_info "按请求结束 uftpd"
        pkill -x uftpd || true
    fi

    for dev in "${TUN_DEVICES[@]}"; do
        if ip link show "$dev" >/dev/null 2>&1; then
            log_info "删除遗留 TUN 设备: $dev"
            ip link del "$dev" || true
        fi
    done
}

print_next_steps() {
    echo ""
    echo "建议："
    echo "1. 通过后先做一次短预跑，再做 180 秒正式上行。"
    echo "2. 下行若默认 UFTP 再次出现 ANNOUNCE timed out，优先按 same-host multicast local-delivery 断点处理。"
    echo "3. 正式批次完成后，立即固定日志目录与 issue 回填结论。"
}

main() {
    normalize_ifaces
    require_root_if_needed
    print_header

    require_command ip
    require_command iw
    require_command rfkill

    if [ "$MODE" = "apply" ]; then
        apply_recommended_actions
        echo ""
        log_info "开始复检"
    fi

    check_interfaces_exist
    check_rfkill
    check_all_interface_state
    check_residual_processes
    check_tun_devices
    check_ports
    print_next_steps

    if [ "$EXIT_CODE" -eq 0 ]; then
        log_pass "跑数前检查完成"
    else
        log_fail "跑数前检查存在未解决项"
    fi

    exit "$EXIT_CODE"
}

main "$@"
