#!/bin/bash
# -*- coding: utf-8 -*-
# v6 新底座无 SSH 手动上行演示总控脚本

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
DEMO_FILE_SIZE="${DEMO_FILE_SIZE:-41943040}"
CHANNEL="${CHANNEL:-157}"
CHANNEL_WIDTH="${CHANNEL_WIDTH:-HT40+}"
LINK_ID="${LINK_ID:-406}"
UPLINK_STREAM="${UPLINK_STREAM:-32}"
DOWNLINK_STREAM="${DOWNLINK_STREAM:-33}"
RADIO_BANDWIDTH="${RADIO_BANDWIDTH:-40}"
RADIO_MCS_INDEX="${RADIO_MCS_INDEX:-1}"
RADIO_SHORT_GI="${RADIO_SHORT_GI:-1}"
FEC_K="${FEC_K:-8}"
FEC_N="${FEC_N:-12}"
UPLINK_PAUSE_THRESHOLD_BYTES="${UPLINK_PAUSE_THRESHOLD_BYTES:-131072}"
UPLINK_RESUME_THRESHOLD_BYTES="${UPLINK_RESUME_THRESHOLD_BYTES:-65536}"
UPLINK_QUEUE_PACKETS_LIMIT="${UPLINK_QUEUE_PACKETS_LIMIT:-64}"
CLIENT1_UPLINK_PAUSE_THRESHOLD_BYTES="${CLIENT1_UPLINK_PAUSE_THRESHOLD_BYTES:-$UPLINK_PAUSE_THRESHOLD_BYTES}"
CLIENT1_UPLINK_RESUME_THRESHOLD_BYTES="${CLIENT1_UPLINK_RESUME_THRESHOLD_BYTES:-$UPLINK_RESUME_THRESHOLD_BYTES}"
CLIENT1_UPLINK_QUEUE_PACKETS_LIMIT="${CLIENT1_UPLINK_QUEUE_PACKETS_LIMIT:-$UPLINK_QUEUE_PACKETS_LIMIT}"
CLIENT2_UPLINK_PAUSE_THRESHOLD_BYTES="${CLIENT2_UPLINK_PAUSE_THRESHOLD_BYTES:-$UPLINK_PAUSE_THRESHOLD_BYTES}"
CLIENT2_UPLINK_RESUME_THRESHOLD_BYTES="${CLIENT2_UPLINK_RESUME_THRESHOLD_BYTES:-$UPLINK_RESUME_THRESHOLD_BYTES}"
CLIENT2_UPLINK_QUEUE_PACKETS_LIMIT="${CLIENT2_UPLINK_QUEUE_PACKETS_LIMIT:-$UPLINK_QUEUE_PACKETS_LIMIT}"
SERVER_IFACE="${SERVER_IFACE:-wlxbcec23372588}"
CLIENT1_IFACE="${CLIENT1_IFACE:-wlxfc221c500a88}"
CLIENT2_IFACE="${CLIENT2_IFACE:-wlxfc221c300cbc}"
DASHBOARD_INTERVAL="${DASHBOARD_INTERVAL:-1}"
NO_CLEAR="${NO_CLEAR:-0}"
NO_ALT_SCREEN="${NO_ALT_SCREEN:-0}"
DASHBOARD_STTY_STATE=""

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    C_RESET=$'\033[0m'
    C_BOLD=$'\033[1m'
    C_RED=$'\033[31;1m'
    C_GREEN=$'\033[32;1m'
    C_YELLOW=$'\033[33;1m'
    C_BLUE=$'\033[34;1m'
    C_CYAN=$'\033[36;1m'
else
    C_RESET=""
    C_BOLD=""
    C_RED=""
    C_GREEN=""
    C_YELLOW=""
    C_BLUE=""
    C_CYAN=""
fi

usage() {
    cat <<'EOF'
用法：
  bash tests/real_hardware/v6_manual_uplink_demo.sh new-name
  DEMO_NAME=... bash tests/real_hardware/v6_manual_uplink_demo.sh server-prepare
  DEMO_NAME=... bash tests/real_hardware/v6_manual_uplink_demo.sh client1-prepare
  DEMO_NAME=... bash tests/real_hardware/v6_manual_uplink_demo.sh client2-prepare

启动链路窗口：
  DEMO_NAME=... bash tests/real_hardware/v6_manual_uplink_demo.sh server-wfb
  DEMO_NAME=... bash tests/real_hardware/v6_manual_uplink_demo.sh client1-wfb
  DEMO_NAME=... bash tests/real_hardware/v6_manual_uplink_demo.sh client2-wfb

执行上传窗口：
  DEMO_NAME=... bash tests/real_hardware/v6_manual_uplink_demo.sh server-recv-all        # 推荐：一个终端同时接收两个 client 文件
  DEMO_NAME=... bash tests/real_hardware/v6_manual_uplink_demo.sh server-recv-client1    # 排障：只接收 client1 应用层 TCP
  DEMO_NAME=... bash tests/real_hardware/v6_manual_uplink_demo.sh server-recv-client2    # 排障：只接收 client2 应用层 TCP
  DEMO_NAME=... bash tests/real_hardware/v6_manual_uplink_demo.sh client1-send
  DEMO_NAME=... bash tests/real_hardware/v6_manual_uplink_demo.sh client2-send

离线归档与汇总：
  DEMO_NAME=... bash tests/real_hardware/v6_manual_uplink_demo.sh client1-pack
  DEMO_NAME=... bash tests/real_hardware/v6_manual_uplink_demo.sh client2-pack
  DEMO_NAME=... bash tests/real_hardware/v6_manual_uplink_demo.sh server-unpack [/path/client1.tgz] [/path/client2.tgz]
  DEMO_NAME=... bash tests/real_hardware/v6_manual_uplink_demo.sh server-summary

常用环境变量：
  DEMO_NAME          三台机器必须完全一致
  DEMO_FILE_SIZE     默认 41943040；排障可设 1048576
  SERVER_IFACE       默认 wlxbcec23372588
  CLIENT1_IFACE      默认 wlxfc221c500a88
  CLIENT2_IFACE      默认 wlxfc221c300cbc
  CHANNEL            默认 157
  CHANNEL_WIDTH      默认 HT40+
  RADIO_BANDWIDTH    raw air HT 带宽，默认 40；应与 CHANNEL_WIDTH=HT40+ 匹配
  RADIO_MCS_INDEX    raw air HT MCS，默认 1；弱信号可降到 0
  RADIO_SHORT_GI     1 表示启用 short GI，默认 1；弱信号可设 0
  FEC_K/FEC_N       trusted_plaintext FEC 参数，默认 8/12；如需关闭冗余可设 1/1
  UPLINK_STREAM      上行方向 stream，默认 32
  DOWNLINK_STREAM    下行方向 stream，默认 33
  UPLINK_*           client 上行队列水位，默认 pause=131072 resume=65536 packets=64
  NO_CLEAR=1         不清屏，方便录屏或保存终端输出
  NO_ALT_SCREEN=1    不进入终端备用屏幕；默认使用备用屏幕原地刷新，避免滚动刷屏

说明：
  本脚本不使用 ssh/scp。client 日志请用 U 盘或现场文件共享人工拷贝到 server。
EOF
}

cmd="${1:-help}"
shift || true

if [ "$cmd" = "help" ] || [ "$cmd" = "--help" ] || [ "$cmd" = "-h" ]; then
    usage
    exit 0
fi

log_info() { printf '%s[信息]%s %s\n' "$C_BLUE" "$C_RESET" "$1"; }
log_ok() { printf '%s[通过]%s %s\n' "$C_GREEN" "$C_RESET" "$1"; }
log_warn() { printf '%s[注意]%s %s\n' "$C_YELLOW" "$C_RESET" "$1"; }
log_fail() { printf '%s[失败]%s %s\n' "$C_RED" "$C_RESET" "$1" >&2; }
die() { log_fail "$1"; exit 1; }

require_demo_name() {
    if [ -z "${DEMO_NAME:-}" ]; then
        die "缺少 DEMO_NAME。先在 server 执行：bash tests/real_hardware/v6_manual_uplink_demo.sh new-name"
    fi
}

demo_root() { printf '%s/tests/logs/%s\n' "$PROJECT_ROOT" "$DEMO_NAME"; }
uplink_root() { printf '%s/uplink\n' "$(demo_root)"; }
server_log_dir() { printf '%s/server\n' "$(uplink_root)"; }
client_log_dir() { printf '%s/%s\n' "$(uplink_root)" "$1"; }

require_command() {
    local name="$1"
    if ! command -v "$name" >/dev/null 2>&1; then
        die "缺少命令：$name"
    fi
}

require_common_commands() {
    require_command python3
    require_command ip
    require_command iw
    require_command sudo
}

require_sudo_session() {
    if [ "${EUID}" -ne 0 ]; then
        log_info "需要 sudo 权限；如有密码提示，请输入本机密码。"
        sudo -v
    fi
}

terminal_cols() {
    local cols
    cols="$(tput cols 2>/dev/null || printf '80')"
    if [ -z "$cols" ] || [ "$cols" -lt 50 ]; then
        cols=80
    fi
    printf '%s\n' "$cols"
}

line_fill() {
    local width="$1"
    printf '%*s\n' "$width" '' | tr ' ' '='
}

short_path() {
    local path="$1"
    local max_width="${2:-68}"
    if [[ "$path" == "$PROJECT_ROOT/"* ]]; then
        path="\$PROJECT_ROOT/${path#"$PROJECT_ROOT/"}"
    fi
    if [ "${#path}" -gt "$max_width" ]; then
        printf '...%s\n' "${path: -$((max_width - 3))}"
    else
        printf '%s\n' "$path"
    fi
}

dashboard_enter() {
    if [ -t 1 ] && command -v stty >/dev/null 2>&1; then
        DASHBOARD_STTY_STATE="$(stty -g 2>/dev/null || true)"
        # 面板逐行刷新依赖 LF 回到行首；现场终端可能被上一个程序留下 -opost/-onlcr。
        stty opost onlcr 2>/dev/null || true
    fi
    if [ "$NO_CLEAR" != "1" ] && [ "$NO_ALT_SCREEN" != "1" ] && [ -t 1 ]; then
        printf '\033[?1049h\033[?25l'
    elif [ "$NO_CLEAR" != "1" ] && [ -t 1 ]; then
        printf '\033[?25l'
    fi
}

dashboard_cleanup() {
    if [ -t 1 ]; then
        printf '\033[?25h'
        if [ "$NO_CLEAR" != "1" ] && [ "$NO_ALT_SCREEN" != "1" ]; then
            printf '\033[?1049l'
        fi
    fi
    if [ -n "$DASHBOARD_STTY_STATE" ] && command -v stty >/dev/null 2>&1; then
        stty "$DASHBOARD_STTY_STATE" 2>/dev/null || true
        DASHBOARD_STTY_STATE=""
    fi
}

clear_screen() {
    if [ "$NO_CLEAR" != "1" ] && [ -t 1 ]; then
        printf '\033[H\033[J'
    fi
}

banner() {
    local width title
    width="$(terminal_cols)"
    if [ "$width" -gt 88 ]; then
        width=88
    fi
    title="$1"
    line_fill "$width"
    printf '%s%s%s\n' "$C_BOLD" "$title" "$C_RESET"
    line_fill "$width"
}

status_item() {
    local ok="$1"
    local label="$2"
    local detail="${3:-}"
    local color tag
    if [ "$ok" = "yes" ]; then
        color="$C_GREEN"
        tag="[通过]"
    elif [ "$ok" = "warn" ]; then
        color="$C_YELLOW"
        tag="[注意]"
    else
        color="$C_RED"
        tag="[等待]"
    fi
    # 显式回车把状态列钉在第 1 列；即使终端换行模式异常，等待/通过也不会斜向错位。
    printf '\r%s%s%s  %s：%s\n' "$color" "$tag" "$C_RESET" "$label" "$detail"
}

count_regex() {
    local pattern="$1"
    local file="$2"
    if [ ! -f "$file" ]; then
        printf '0\n'
        return
    fi
    grep -Ec "$pattern" "$file" 2>/dev/null || true
}

has_regex() {
    local pattern="$1"
    local file="$2"
    if [ -f "$file" ] && grep -Eq "$pattern" "$file" 2>/dev/null; then
        printf 'yes\n'
    else
        printf 'no\n'
    fi
}

pkt_sum() {
    local file="$1"
    if [ ! -f "$file" ]; then
        printf '0\n'
        return
    fi
    awk -F'[:\t]' '/\tPKT\t/ {sum+=$7} END {print sum+0}' "$file"
}

last_token_auth() {
    local file="$1"
    if [ ! -f "$file" ]; then
        printf '0\n'
        return
    fi
    awk -F'[:\t]' '/TOKEN_AUTH/ {v=$5} END {print v+0}' "$file"
}

show_recent_errors() {
    local file="$1"
    if [ ! -f "$file" ]; then
        return
    fi
    local lines line
    lines="$(grep -Ei 'error|failed|pcap_activate|绑定 raw air socket|no such device|permission|READY_REJECT|GRANT_FILTER' "$file" 2>/dev/null | tail -n 4 || true)"
    if [ -n "$lines" ]; then
        printf '\r%s\n' "${C_YELLOW}最近需要关注的信息：${C_RESET}"
        while IFS= read -r line; do
            printf '\r%s\n' "$line"
        done <<< "$lines"
    fi
}

wfb_tun_error_line() {
    local file="$1"
    if [ ! -f "$file" ]; then
        return 0
    fi
    grep -Ei 'TUNSETIFF failed|Device or resource busy|tun.*busy|failed.*tun|tun.*failed' "$file" 2>/dev/null | tail -n 1 || true
}

cleanup_tun_device() {
    local tun_name="$1"
    if ! ip link show "$tun_name" >/dev/null 2>&1; then
        return 0
    fi

    log_warn "发现旧 TUN 设备 $tun_name，正在删除，避免 TUNSETIFF: Device or resource busy。"
    sudo ip link set "$tun_name" down 2>/dev/null || true
    sudo ip link delete "$tun_name" 2>/dev/null || true
    sleep 0.2

    if ip link show "$tun_name" >/dev/null 2>&1; then
        die "旧 TUN 设备 $tun_name 仍存在或被占用。请执行：sudo pkill -x wfb_v6_uplink || true; sudo ip link delete $tun_name 2>/dev/null || true"
    fi
    log_ok "旧 TUN 设备 $tun_name 已清理。"
}

build_binary() {
    log_info "构建 wfb_v6_uplink。"
    make -C "$PROJECT_ROOT" wfb_v6_uplink
    [ -x "$PROJECT_ROOT/wfb_v6_uplink" ] || die "wfb_v6_uplink 不存在或不可执行：$PROJECT_ROOT/wfb_v6_uplink"
    log_ok "二进制可执行：$PROJECT_ROOT/wfb_v6_uplink"
}

configure_monitor() {
    local iface="$1"
    require_sudo_session
    log_info "配置网卡为 monitor：$iface，信道 $CHANNEL / $CHANNEL_WIDTH。"
    sudo ip link set "$iface" down || true
    sudo iw dev "$iface" set type monitor
    sudo ip link set "$iface" up
    sudo iw dev "$iface" set channel "$CHANNEL" "$CHANNEL_WIDTH"

    ip -br link show "$iface" || true
    iw dev "$iface" info || true

    local mode state
    mode="$(iw dev "$iface" info 2>/dev/null | awk '/type / {print $2; exit}')"
    state="$(ip -br link show "$iface" 2>/dev/null | awk '{print $2}')"
    if [ "$mode" = "monitor" ] && [ "$state" = "UP" ]; then
        log_ok "$iface 已经是 monitor/UP。"
    else
        die "$iface 未达到 monitor/UP；请检查网卡名、驱动、rfkill。"
    fi
}

prepare_server() {
    require_demo_name
    require_common_commands
    mkdir -p "$(server_log_dir)" "$(client_log_dir client1)" "$(client_log_dir client2)"
    build_binary
    require_sudo_session
    sudo pkill -x wfb_v6_uplink 2>/dev/null || true
    sudo pkill -f v6_manual_uplink_tcp_recv_progress.py 2>/dev/null || true
    cleanup_tun_device v6us0
    configure_monitor "$SERVER_IFACE"
    banner "server 准备完成"
    log_ok "DEMO_NAME=$DEMO_NAME"
    log_ok "日志目录=$(uplink_root)"
    log_info "下一步：另开终端执行 server-wfb、server-recv-all。"
}

client_iface_for() {
    case "$1" in
        client1) printf '%s\n' "$CLIENT1_IFACE" ;;
        client2) printf '%s\n' "$CLIENT2_IFACE" ;;
        *) die "未知 client：$1" ;;
    esac
}

client_ip_for() {
    case "$1" in
        client1) printf '10.80.0.11\n' ;;
        client2) printf '10.80.0.12\n' ;;
        *) die "未知 client：$1" ;;
    esac
}

client_tun_for() {
    case "$1" in
        client1) printf 'v6uc1\n' ;;
        client2) printf 'v6uc2\n' ;;
        *) die "未知 client：$1" ;;
    esac
}

client_node_for() {
    case "$1" in
        client1) printf '1\n' ;;
        client2) printf '2\n' ;;
        *) die "未知 client：$1" ;;
    esac
}

client_port_for() {
    case "$1" in
        client1) printf '19111\n' ;;
        client2) printf '19112\n' ;;
        *) die "未知 client：$1" ;;
    esac
}

client_queue_limit_for() {
    case "$1" in
        client1) printf '%s\n' "$CLIENT1_UPLINK_QUEUE_PACKETS_LIMIT" ;;
        client2) printf '%s\n' "$CLIENT2_UPLINK_QUEUE_PACKETS_LIMIT" ;;
        *) die "未知 client：$1" ;;
    esac
}

client_pause_for() {
    case "$1" in
        client1) printf '%s\n' "$CLIENT1_UPLINK_PAUSE_THRESHOLD_BYTES" ;;
        client2) printf '%s\n' "$CLIENT2_UPLINK_PAUSE_THRESHOLD_BYTES" ;;
        *) die "未知 client：$1" ;;
    esac
}

client_resume_for() {
    case "$1" in
        client1) printf '%s\n' "$CLIENT1_UPLINK_RESUME_THRESHOLD_BYTES" ;;
        client2) printf '%s\n' "$CLIENT2_UPLINK_RESUME_THRESHOLD_BYTES" ;;
        *) die "未知 client：$1" ;;
    esac
}

prepare_client() {
    local role="$1"
    require_demo_name
    require_common_commands
    local iface tun_name ulog source sha_file
    iface="$(client_iface_for "$role")"
    tun_name="$(client_tun_for "$role")"
    ulog="$(client_log_dir "$role")"
    source="$ulog/${role}_uplink.bin"
    sha_file="$ulog/source_sha256.txt"
    mkdir -p "$ulog"
    build_binary
    require_sudo_session
    sudo pkill -x wfb_v6_uplink 2>/dev/null || true
    sudo pkill -f v6_manual_uplink_tcp_send_progress.py 2>/dev/null || true
    cleanup_tun_device "$tun_name"
    configure_monitor "$iface"
    python3 "$SCRIPT_DIR/v6_manual_uplink_make_source.py" \
        --client "$role" \
        --output "$source" \
        --size "$DEMO_FILE_SIZE" \
        --sha-output "$sha_file"
    banner "$role 准备完成"
    log_ok "DEMO_NAME=$DEMO_NAME"
    log_ok "日志目录=$ulog"
    log_ok "源文件 SHA：$(awk '{print $1}' "$sha_file")"
    log_info "下一步：另开终端执行 ${role}-wfb 和 ${role}-send。"
}

server_dashboard() {
    local pid="$1"
    local log_file="$2"
    local trusted tun_ok c1_ready c2_ready c1_grants c2_grants ready_reject data_packets elapsed log_display log_width tun_error
    trusted="$(has_regex 'trusted_plaintext' "$log_file")"
    if ip addr show v6us0 >/dev/null 2>&1; then tun_ok=yes; else tun_ok=no; fi
    c1_ready="$(count_regex '^ready_accept node_id=1' "$log_file")"
    c2_ready="$(count_regex '^ready_accept node_id=2' "$log_file")"
    c1_grants="$(count_regex '^grant seq=.* node_id=1 ' "$log_file")"
    c2_grants="$(count_regex '^grant seq=.* node_id=2 ' "$log_file")"
    ready_reject="$(count_regex '^ready_reject ' "$log_file")"
    data_packets="$(pkt_sum "$log_file")"
    elapsed="$(ps -p "$pid" -o etime= 2>/dev/null | awk '{$1=$1; print}' || true)"
    tun_error="$(wfb_tun_error_line "$log_file")"
    log_width=$(( $(terminal_cols) - 12 ))
    if [ "$log_width" -lt 36 ]; then
        log_width=36
    fi
    log_display="$(short_path "$log_file" "$log_width")"

    clear_screen
    banner "server 上行链路面板"
    printf '\r演示名：%s\n' "$DEMO_NAME"
    printf '\r运行时间：%s\n' "${elapsed:-running}"
    printf '\r日志文件：%s\n\r\n' "$log_display"
    status_item "$trusted" "链路安全口径" "trusted_plaintext 已出现才算正确"
    if [ -n "$tun_error" ]; then
        status_item warn "server TUN" "创建失败：$tun_error；请清理旧 TUN/旧进程"
    else
        status_item "$tun_ok" "server TUN" "v6us0 = 10.80.0.1/24"
    fi
    if [ "$c1_ready" -gt 0 ]; then status_item yes "client1 已进入调度" "ready_accept=$c1_ready"; else status_item no "client1 已进入调度" "等待 client1"; fi
    if [ "$c2_ready" -gt 0 ]; then status_item yes "client2 已进入调度" "ready_accept=$c2_ready"; else status_item no "client2 已进入调度" "等待 client2"; fi
    if [ "$c1_grants" -gt 0 ]; then status_item yes "client1 获得空口发送机会" "grants=$c1_grants"; else status_item no "client1 获得空口发送机会" "grants=0"; fi
    if [ "$c2_grants" -gt 0 ]; then status_item yes "client2 获得空口发送机会" "grants=$c2_grants"; else status_item no "client2 获得空口发送机会" "grants=0"; fi
    if [ "$data_packets" -gt 0 ]; then status_item yes "server 收到无线数据" "累计数据包=$data_packets"; else status_item no "server 收到无线数据" "等待 TCP 上传开始"; fi
    if [ "$ready_reject" -eq 0 ]; then status_item yes "调度拒收" "ready_reject=0"; else status_item warn "调度拒收" "ready_reject=$ready_reject，需要解释"; fi
    printf '\r\n\r口径：两个 client 都有 grant，且 server 数据包增长 = 共享无线链路上传。\n'
    printf '\r提示：保持本窗口；另开 server-recv-all，再启动两个 client-send。\n'
    printf '\r停止：Ctrl-C 停止 WFB。\n\r\n'
    show_recent_errors "$log_file"
}

client_dashboard() {
    local role="$1"
    local pid="$2"
    local log_file="$3"
    local send_log="$4"
    local tun_name trusted tun_ok grant_accept auth sent_line elapsed log_display log_width tun_error
    tun_name="$(client_tun_for "$role")"
    trusted="$(has_regex 'trusted_plaintext' "$log_file")"
    if ip addr show "$tun_name" >/dev/null 2>&1; then tun_ok=yes; else tun_ok=no; fi
    grant_accept="$(count_regex 'grant_accept' "$log_file")"
    auth="$(last_token_auth "$log_file")"
    sent_line=""
    if [ -f "$send_log" ]; then
        sent_line="$(grep '上传完成：sent_bytes=' "$send_log" 2>/dev/null | tail -n 1 || true)"
    fi
    elapsed="$(ps -p "$pid" -o etime= 2>/dev/null | awk '{$1=$1; print}' || true)"
    tun_error="$(wfb_tun_error_line "$log_file")"
    log_width=$(( $(terminal_cols) - 12 ))
    if [ "$log_width" -lt 36 ]; then
        log_width=36
    fi
    log_display="$(short_path "$log_file" "$log_width")"

    clear_screen
    banner "$role 上行链路面板"
    printf '\r演示名：%s\n' "$DEMO_NAME"
    printf '\r运行时间：%s\n' "${elapsed:-running}"
    printf '\r日志文件：%s\n\r\n' "$log_display"
    status_item "$trusted" "链路安全口径" "trusted_plaintext 已出现才算正确"
    if [ -n "$tun_error" ]; then
        status_item warn "$role TUN" "创建失败：$tun_error；请清理旧 TUN/旧进程"
    else
        status_item "$tun_ok" "$role TUN" "$tun_name = $(client_ip_for "$role")/24"
    fi
    if [ "$grant_accept" -gt 0 ]; then status_item yes "收到 server 授权" "grant_accept=$grant_accept"; else status_item no "收到 server 授权" "等待 server 调度"; fi
    if [ "$auth" -gt 0 ]; then status_item yes "允许发送数据" "authorized_sends=$auth"; else status_item no "允许发送数据" "等待 TCP 上传或授权"; fi
    if [ -n "$sent_line" ]; then status_item yes "TCP 文件上传" "$sent_line"; else status_item no "TCP 文件上传" "发送窗口完成后会显示"; fi
    printf '\r\n\r口径：出现 server 授权和允许发送数据 = 本机在授权窗口内上传。\n'
    printf '\r提示：保持本窗口；另开终端执行 %s-send。\n' "$role"
    printf '\r停止：Ctrl-C 停止 WFB。\n\r\n'
    show_recent_errors "$log_file"
}

run_server_wfb() {
    require_demo_name
    require_common_commands
    require_sudo_session
    mkdir -p "$(server_log_dir)"
    local log_file pid status
    log_file="$(server_log_dir)/wfb_v6_uplink.log"
    cleanup_tun_device v6us0
    : > "$log_file"
    log_info "启动 server WFB；屏幕只显示简化面板，完整日志写入 $log_file"
    local radio_args=(--radio-bandwidth "$RADIO_BANDWIDTH" --radio-mcs-index "$RADIO_MCS_INDEX")
    if [ "$RADIO_SHORT_GI" = "1" ]; then
        radio_args+=(--radio-short-gi)
    fi
    log_info "raw air 发送参数：HT${RADIO_BANDWIDTH} MCS${RADIO_MCS_INDEX} short_gi=${RADIO_SHORT_GI}；FEC=${FEC_K}/${FEC_N}。"
    sudo "$PROJECT_ROOT/wfb_v6_uplink" \
        --role server \
        --tun-name v6us0 \
        --tun-addr 10.80.0.1/24 \
        --node-id 9 \
        --link-id "$LINK_ID" \
        --uplink-stream "$UPLINK_STREAM" \
        --downlink-stream "$DOWNLINK_STREAM" \
        --fec-k "$FEC_K" \
        --fec-n "$FEC_N" \
        "${radio_args[@]}" \
        --air-interface "$SERVER_IFACE" \
        --known-clients 1,2 \
        --client-target 1:10.80.0.11:127.0.0.1:1 \
        --client-target 2:10.80.0.12:127.0.0.1:1 \
        --grant-duration-ms 120 \
        --guard-interval-ms 20 \
        --downlink-pause-threshold-bytes 131072 \
        --downlink-resume-threshold-bytes 65536 \
        --downlink-queue-packets-limit 64 \
        --queue-summary-file "$(server_log_dir)/server_queue_summary.json" \
        --log-interval 200 \
        > "$log_file" 2>&1 &
    pid=$!
    status=0
    dashboard_enter
    trap 'kill "$pid" 2>/dev/null || true; wait "$pid" 2>/dev/null || true; dashboard_cleanup' INT TERM EXIT
    while kill -0 "$pid" 2>/dev/null; do
        server_dashboard "$pid" "$log_file"
        sleep "$DASHBOARD_INTERVAL"
    done
    wait "$pid" || status=$?
    dashboard_cleanup
    trap - INT TERM EXIT
    if [ "$status" -ne 0 ]; then
        log_fail "server WFB 已退出，exit_status=$status。"
        show_recent_errors "$log_file"
        return "$status"
    fi
}

run_client_wfb() {
    local role="$1"
    require_demo_name
    require_common_commands
    require_sudo_session
    local ulog log_file send_log tun_name node_id iface pause resume queue_limit status
    ulog="$(client_log_dir "$role")"
    mkdir -p "$ulog"
    log_file="$ulog/wfb_v6_uplink.log"
    send_log="$ulog/tcp_send.log"
    tun_name="$(client_tun_for "$role")"
    node_id="$(client_node_for "$role")"
    iface="$(client_iface_for "$role")"
    pause="$(client_pause_for "$role")"
    resume="$(client_resume_for "$role")"
    queue_limit="$(client_queue_limit_for "$role")"
    cleanup_tun_device "$tun_name"
    : > "$log_file"
    log_info "启动 $role WFB；屏幕只显示简化面板，完整日志写入 $log_file"
    local radio_args=(--radio-bandwidth "$RADIO_BANDWIDTH" --radio-mcs-index "$RADIO_MCS_INDEX")
    if [ "$RADIO_SHORT_GI" = "1" ]; then
        radio_args+=(--radio-short-gi)
    fi
    log_info "raw air 发送参数：HT${RADIO_BANDWIDTH} MCS${RADIO_MCS_INDEX} short_gi=${RADIO_SHORT_GI}；FEC=${FEC_K}/${FEC_N}；上行队列 pause=${pause} resume=${resume} packets=${queue_limit}。"
    sudo "$PROJECT_ROOT/wfb_v6_uplink" \
        --role client \
        --tun-name "$tun_name" \
        --tun-addr "$(client_ip_for "$role")/24" \
        --node-id "$node_id" \
        --link-id "$LINK_ID" \
        --uplink-stream "$UPLINK_STREAM" \
        --downlink-stream "$DOWNLINK_STREAM" \
        --fec-k "$FEC_K" \
        --fec-n "$FEC_N" \
        "${radio_args[@]}" \
        --air-interface "$iface" \
        --uplink-pause-threshold-bytes "$pause" \
        --uplink-resume-threshold-bytes "$resume" \
        --uplink-queue-packets-limit "$queue_limit" \
        --queue-summary-file "$ulog/${role}_queue_summary.json" \
        --log-interval 200 \
        > "$log_file" 2>&1 &
    local pid=$!
    status=0
    dashboard_enter
    trap 'kill "$pid" 2>/dev/null || true; wait "$pid" 2>/dev/null || true; dashboard_cleanup' INT TERM EXIT
    while kill -0 "$pid" 2>/dev/null; do
        client_dashboard "$role" "$pid" "$log_file" "$send_log"
        sleep "$DASHBOARD_INTERVAL"
    done
    wait "$pid" || status=$?
    dashboard_cleanup
    trap - INT TERM EXIT
    if [ "$status" -ne 0 ]; then
        log_fail "$role WFB 已退出，exit_status=$status。"
        show_recent_errors "$log_file"
        return "$status"
    fi
}

# 在 server 的 TUN 口上启动一个应用层 TCP 文件接收器。
# 注意：这不是 WFB/RF 接收进程；唯一的 server WFB 接收进程是 server-wfb。
server_app_tcp_recv_one() {
    local role="$1"
    require_demo_name
    require_command python3
    local ulog port output log_file
    ulog="$(server_log_dir)"
    mkdir -p "$ulog"
    port="$(client_port_for "$role")"
    output="$ulog/${role}_uplink_received.bin"
    log_file="$ulog/${role}_tcp_recv.log"
    python3 "$SCRIPT_DIR/v6_manual_uplink_tcp_recv_progress.py" \
        --label "server 应用层接收 $role TCP 文件" \
        --bind-ip 10.80.0.1 \
        --port "$port" \
        --output "$output" \
        --expected-bytes "$DEMO_FILE_SIZE" \
        --timeout 300 \
        --log-file "$log_file"
}

server_app_tcp_recv_all() {
    require_demo_name
    require_command python3
    mkdir -p "$(server_log_dir)"
    banner "server 应用层接收：一个窗口看两个 client 文件上传"
    log_info "无线/WFB 接收进程只有 server-wfb 一个；这里启动的是两个应用层 TCP 接收器。"
    log_info "两个 TCP 接收器共享同一个 TUN: 10.80.0.1，只是端口不同：client1=19111，client2=19112。"
    log_info "等本窗口显示两个“接收器已就绪”后，再启动 client1-send 和 client2-send。"

    server_app_tcp_recv_one client1 &
    local pid1=$!
    server_app_tcp_recv_one client2 &
    local pid2=$!
    local status1=0
    local status2=0

    trap 'kill "$pid1" "$pid2" 2>/dev/null || true; wait "$pid1" 2>/dev/null || true; wait "$pid2" 2>/dev/null || true' INT TERM
    wait "$pid1" || status1=$?
    wait "$pid2" || status2=$?
    trap - INT TERM

    if [ "$status1" -eq 0 ] && [ "$status2" -eq 0 ]; then
        log_ok "两个 client 的应用层文件接收都完成。"
        return 0
    fi
    log_fail "应用层接收未全部完成：client1_status=$status1 client2_status=$status2"
    return 1
}

client_send() {
    local role="$1"
    require_demo_name
    require_command python3
    local ulog source port log_file
    ulog="$(client_log_dir "$role")"
    source="$ulog/${role}_uplink.bin"
    port="$(client_port_for "$role")"
    log_file="$ulog/tcp_send.log"
    python3 "$SCRIPT_DIR/v6_manual_uplink_tcp_send_progress.py" \
        --label "$role 上传到 server" \
        --source-ip "$(client_ip_for "$role")" \
        --host 10.80.0.1 \
        --port "$port" \
        --input "$source" \
        --timeout 300 \
        --log-file "$log_file"
}

pack_client() {
    local role="$1"
    require_demo_name
    local ulog archive queue_file
    ulog="$(client_log_dir "$role")"
    archive="/tmp/${DEMO_NAME}_${role}_uplink_logs.tgz"
    queue_file="${role}_queue_summary.json"
    [ -f "$ulog/wfb_v6_uplink.log" ] || die "缺少 $ulog/wfb_v6_uplink.log"
    [ -f "$ulog/tcp_send.log" ] || die "缺少 $ulog/tcp_send.log"
    [ -f "$ulog/source_sha256.txt" ] || die "缺少 $ulog/source_sha256.txt"
    [ -f "$ulog/$queue_file" ] || die "缺少 $ulog/$queue_file；请先在 WFB 面板按 Ctrl-C 让程序退出并刷新 summary"
    tar -C "$ulog" -czf "$archive" wfb_v6_uplink.log tcp_send.log source_sha256.txt "$queue_file"
    banner "$role 日志包已生成"
    sha256sum "$archive"
    log_ok "请人工拷贝到 server：$archive"
}

server_unpack() {
    require_demo_name
    local c1_archive c2_archive
    c1_archive="${1:-/tmp/${DEMO_NAME}_client1_uplink_logs.tgz}"
    c2_archive="${2:-/tmp/${DEMO_NAME}_client2_uplink_logs.tgz}"
    [ -f "$c1_archive" ] || die "缺少 client1 日志包：$c1_archive"
    [ -f "$c2_archive" ] || die "缺少 client2 日志包：$c2_archive"
    mkdir -p "$(client_log_dir client1)" "$(client_log_dir client2)"
    tar -C "$(client_log_dir client1)" -xzf "$c1_archive"
    tar -C "$(client_log_dir client2)" -xzf "$c2_archive"
    banner "client 日志已解包到 server"
    find "$(uplink_root)" -maxdepth 2 -type f | sort
}

value_or_empty() {
    local file="$1"
    if [ -f "$file" ]; then
        awk '{print $1; exit}' "$file"
    fi
    return 0
}

sha_file() {
    local file="$1"
    if [ -f "$file" ]; then
        sha256sum "$file" | awk '{print $1}'
    fi
    return 0
}

make_formal_summary() {
    local out_file
    out_file="$(uplink_root)/formal_2a_summary.json"
    PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
    python3 "$PROJECT_ROOT/tests/real_hardware/v6_formal_2a_summary.py" \
        --mode uplink \
        --scenario dual-long-run \
        --output "$out_file" \
        --queue-summary "$(server_log_dir)/server_queue_summary.json" \
        --queue-summary "$(client_log_dir client1)/client1_queue_summary.json" \
        --queue-summary "$(client_log_dir client2)/client2_queue_summary.json" \
        --reassembly-log "$(server_log_dir)/wfb_v6_uplink.log" \
        --reassembly-log "$(client_log_dir client1)/wfb_v6_uplink.log" \
        --reassembly-log "$(client_log_dir client2)/wfb_v6_uplink.log"
    python3 -m json.tool "$out_file" > "$(uplink_root)/formal_2a_summary.pretty.json"
}

pass_if() {
    local condition="$1"
    local label="$2"
    local detail="$3"
    if [ "$condition" = "yes" ]; then
        status_item yes "$label" "$detail"
    else
        status_item no "$label" "$detail"
    fi
}

server_summary() {
    require_demo_name
    require_command python3
    require_command sha256sum
    local uroot result c1_src c2_src c1_recv c2_recv c1_sha c2_sha c1_recv_sha c2_recv_sha
    local c1_grants c2_grants ready_reject data_packets c1_auth c2_auth c1_bytes c2_bytes summary_ok all_ok
    uroot="$(uplink_root)"
    result="$(demo_root)/result.md"
    mkdir -p "$uroot"

    c1_src="$(client_log_dir client1)/source_sha256.txt"
    c2_src="$(client_log_dir client2)/source_sha256.txt"
    c1_recv="$(server_log_dir)/client1_uplink_received.bin"
    c2_recv="$(server_log_dir)/client2_uplink_received.bin"
    c1_sha="$(value_or_empty "$c1_src")"
    c2_sha="$(value_or_empty "$c2_src")"
    c1_recv_sha="$(sha_file "$c1_recv")"
    c2_recv_sha="$(sha_file "$c2_recv")"
    c1_grants="$(count_regex '^grant seq=.* node_id=1 ' "$(server_log_dir)/wfb_v6_uplink.log")"
    c2_grants="$(count_regex '^grant seq=.* node_id=2 ' "$(server_log_dir)/wfb_v6_uplink.log")"
    ready_reject="$(count_regex '^ready_reject ' "$(server_log_dir)/wfb_v6_uplink.log")"
    data_packets="$(pkt_sum "$(server_log_dir)/wfb_v6_uplink.log")"
    c1_auth="$(last_token_auth "$(client_log_dir client1)/wfb_v6_uplink.log")"
    c2_auth="$(last_token_auth "$(client_log_dir client2)/wfb_v6_uplink.log")"
    c1_bytes="$(grep '接收完成：received_bytes=' "$(server_log_dir)/client1_tcp_recv.log" 2>/dev/null | tail -n 1 || true)"
    c2_bytes="$(grep '接收完成：received_bytes=' "$(server_log_dir)/client2_tcp_recv.log" 2>/dev/null | tail -n 1 || true)"

    summary_ok=no
    if make_formal_summary; then
        summary_ok=yes
    else
        log_warn "formal_2a_summary.json 生成失败；请检查 client 日志包和 queue summary。"
    fi

    clear_screen
    banner "最终结果面板：给老师看的通过条件"
    pass_if "$([ -n "$c1_sha" ] && [ "$c1_sha" = "$c1_recv_sha" ] && printf yes || printf no)" "client1 文件完整" "source=$c1_sha received=$c1_recv_sha"
    pass_if "$([ -n "$c2_sha" ] && [ "$c2_sha" = "$c2_recv_sha" ] && printf yes || printf no)" "client2 文件完整" "source=$c2_sha received=$c2_recv_sha"
    pass_if "$([ "$c1_grants" -gt 0 ] && printf yes || printf no)" "client1 获得无线发送机会" "grants=$c1_grants"
    pass_if "$([ "$c2_grants" -gt 0 ] && printf yes || printf no)" "client2 获得无线发送机会" "grants=$c2_grants"
    pass_if "$([ "$c1_auth" -gt 0 ] && printf yes || printf no)" "client1 被授权发送" "authorized_sends=$c1_auth"
    pass_if "$([ "$c2_auth" -gt 0 ] && printf yes || printf no)" "client2 被授权发送" "authorized_sends=$c2_auth"
    pass_if "$([ "$data_packets" -gt 0 ] && printf yes || printf no)" "server 收到无线数据" "server_data_packets_sum=$data_packets"
    pass_if "$([ "$ready_reject" -eq 0 ] && printf yes || printf no)" "无调度拒收" "ready_reject=$ready_reject"
    pass_if "$summary_ok" "2A 摘要生成" "$(uplink_root)/formal_2a_summary.json"

    all_ok=yes
    [ -n "$c1_sha" ] && [ "$c1_sha" = "$c1_recv_sha" ] || all_ok=no
    [ -n "$c2_sha" ] && [ "$c2_sha" = "$c2_recv_sha" ] || all_ok=no
    [ "$c1_grants" -gt 0 ] || all_ok=no
    [ "$c2_grants" -gt 0 ] || all_ok=no
    [ "$c1_auth" -gt 0 ] || all_ok=no
    [ "$c2_auth" -gt 0 ] || all_ok=no
    [ "$data_packets" -gt 0 ] || all_ok=no
    [ "$ready_reject" -eq 0 ] || all_ok=no
    [ "$summary_ok" = yes ] || all_ok=no

    printf '\n'
    if [ "$all_ok" = yes ]; then
        log_ok "结论：本次无 SSH 手动双 client 上行演示通过。"
    else
        log_fail "结论：本次无 SSH 手动双 client 上行演示未通过；按上面的 [等待] 项排查。"
    fi

    cat > "$result" <<EOF
# v6 新底座无 SSH 手动上行演示结果

- 日志目录: $(demo_root)
- 文件大小: $DEMO_FILE_SIZE bytes
- 启动路径: wfb_v6_uplink --role server/client
- link_security_mode: trusted_plaintext
- 禁止路径: wfb_rx / wfb_tx / wfb_token_scheduler / 旧 -K key
- 拓扑: server 本机，client1 独立机器，client2 独立机器；无 SSH 自动控制

## 文件结果

- client1 source SHA: $c1_sha
- server received client1 SHA: $c1_recv_sha
- client1 recv log: $c1_bytes
- client2 source SHA: $c2_sha
- server received client2 SHA: $c2_recv_sha
- client2 recv log: $c2_bytes

## 链路指标

- client1_grants: $c1_grants
- client2_grants: $c2_grants
- client1_authorized_sends: $c1_auth
- client2_authorized_sends: $c2_auth
- ready_reject_count: $ready_reject
- server_data_packets_sum: $data_packets
- 2A 摘要生成: $summary_ok
- 2A 摘要路径: $(uplink_root)/formal_2a_summary.json

## 现场结论

- 自动判定: $all_ok
- 人工签字: 通过 / 不通过
EOF
    log_ok "结果摘要已写入：$result"

    if [ "$all_ok" = yes ]; then
        return 0
    fi
    return 1
}

new_name() {
    local name
    name="v6手动上行_$(date +%Y%m%d_%H%M%S)"
    banner "生成本轮演示名"
    printf 'export DEMO_NAME=%q\n' "$name"
    printf '\n请把上一行复制到 server、client1、client2 三台机器。\n'
}

case "$cmd" in
    new-name)
        new_name
        ;;
    server-prepare)
        prepare_server
        ;;
    client1-prepare)
        prepare_client client1
        ;;
    client2-prepare)
        prepare_client client2
        ;;
    server-wfb)
        run_server_wfb
        ;;
    client1-wfb)
        run_client_wfb client1
        ;;
    client2-wfb)
        run_client_wfb client2
        ;;
    server-recv-all)
        server_app_tcp_recv_all
        ;;
    server-recv-client1)
        server_app_tcp_recv_one client1
        ;;
    server-recv-client2)
        server_app_tcp_recv_one client2
        ;;
    client1-send)
        client_send client1
        ;;
    client2-send)
        client_send client2
        ;;
    client1-pack)
        pack_client client1
        ;;
    client2-pack)
        pack_client client2
        ;;
    server-unpack)
        server_unpack "$@"
        ;;
    server-summary)
        server_summary
        ;;
    *)
        log_fail "未知命令：$cmd"
        usage >&2
        exit 2
        ;;
esac
