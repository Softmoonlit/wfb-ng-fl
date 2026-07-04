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
SERVER_IFACE="${SERVER_IFACE:-wlxbcec23372588}"
CLIENT1_IFACE="${CLIENT1_IFACE:-wlxfca386b38672}"
CLIENT2_IFACE="${CLIENT2_IFACE:-wlxfc221c300cbc}"
DASHBOARD_INTERVAL="${DASHBOARD_INTERVAL:-1}"
NO_CLEAR="${NO_CLEAR:-0}"

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
  DEMO_NAME=... bash tests/real_hardware/v6_manual_uplink_demo.sh server-recv-client1
  DEMO_NAME=... bash tests/real_hardware/v6_manual_uplink_demo.sh server-recv-client2
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
  CLIENT1_IFACE      默认 wlxfca386b38672
  CLIENT2_IFACE      默认 wlxfc221c300cbc
  CHANNEL            默认 157
  CHANNEL_WIDTH      默认 HT40+
  NO_CLEAR=1         不清屏，方便录屏或保存终端输出

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

clear_screen() {
    if [ "$NO_CLEAR" != "1" ] && [ -t 1 ]; then
        printf '\033[H\033[2J'
    fi
}

banner() {
    printf '%s\n' "${C_BOLD}============================================================${C_RESET}"
    printf '%s\n' "${C_BOLD}$1${C_RESET}"
    printf '%s\n' "${C_BOLD}============================================================${C_RESET}"
}

status_item() {
    local ok="$1"
    local label="$2"
    local detail="${3:-}"
    if [ "$ok" = "yes" ]; then
        printf '%s%-8s%s %s %s\n' "$C_GREEN" "[通过]" "$C_RESET" "$label" "$detail"
    elif [ "$ok" = "warn" ]; then
        printf '%s%-8s%s %s %s\n' "$C_YELLOW" "[注意]" "$C_RESET" "$label" "$detail"
    else
        printf '%s%-8s%s %s %s\n' "$C_RED" "[等待]" "$C_RESET" "$label" "$detail"
    fi
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
    local lines
    lines="$(grep -Ei 'error|failed|pcap_activate|绑定 raw air socket|no such device|permission|READY_REJECT|GRANT_FILTER' "$file" 2>/dev/null | tail -n 4 || true)"
    if [ -n "$lines" ]; then
        printf '%s\n' "${C_YELLOW}最近需要关注的信息：${C_RESET}"
        printf '%s\n' "$lines"
    fi
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
    configure_monitor "$SERVER_IFACE"
    banner "server 准备完成"
    log_ok "DEMO_NAME=$DEMO_NAME"
    log_ok "日志目录=$(uplink_root)"
    log_info "下一步：另开终端执行 server-wfb、server-recv-client1、server-recv-client2。"
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
        client1) printf '8\n' ;;
        client2) printf '1\n' ;;
        *) die "未知 client：$1" ;;
    esac
}

client_pause_for() {
    case "$1" in
        client1) printf '64\n' ;;
        client2) printf '4096\n' ;;
        *) die "未知 client：$1" ;;
    esac
}

client_resume_for() {
    case "$1" in
        client1) printf '32\n' ;;
        client2) printf '2048\n' ;;
        *) die "未知 client：$1" ;;
    esac
}

prepare_client() {
    local role="$1"
    require_demo_name
    require_common_commands
    local iface ulog source sha_file
    iface="$(client_iface_for "$role")"
    ulog="$(client_log_dir "$role")"
    source="$ulog/${role}_uplink.bin"
    sha_file="$ulog/source_sha256.txt"
    mkdir -p "$ulog"
    build_binary
    require_sudo_session
    sudo pkill -x wfb_v6_uplink 2>/dev/null || true
    sudo pkill -f v6_manual_uplink_tcp_send_progress.py 2>/dev/null || true
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
    local trusted tun_ok c1_ready c2_ready c1_grants c2_grants ready_reject data_packets elapsed
    trusted="$(has_regex 'trusted_plaintext' "$log_file")"
    if ip addr show v6us0 >/dev/null 2>&1; then tun_ok=yes; else tun_ok=no; fi
    c1_ready="$(count_regex '^ready_accept node_id=1' "$log_file")"
    c2_ready="$(count_regex '^ready_accept node_id=2' "$log_file")"
    c1_grants="$(count_regex '^grant seq=.* node_id=1 ' "$log_file")"
    c2_grants="$(count_regex '^grant seq=.* node_id=2 ' "$log_file")"
    ready_reject="$(count_regex '^ready_reject ' "$log_file")"
    data_packets="$(pkt_sum "$log_file")"
    elapsed="$(ps -p "$pid" -o etime= 2>/dev/null | awk '{$1=$1; print}' || true)"

    clear_screen
    banner "server 上行链路面板：给老师看的实时状态"
    printf '演示名: %s\n' "$DEMO_NAME"
    printf '运行时间: %s    日志: %s\n\n' "${elapsed:-running}" "$log_file"
    status_item "$trusted" "链路安全口径" "trusted_plaintext 已出现才算正确"
    status_item "$tun_ok" "server TUN" "v6us0 = 10.80.0.1/24"
    if [ "$c1_ready" -gt 0 ]; then status_item yes "client1 已进入调度" "ready_accept=$c1_ready"; else status_item no "client1 已进入调度" "等待 client1"; fi
    if [ "$c2_ready" -gt 0 ]; then status_item yes "client2 已进入调度" "ready_accept=$c2_ready"; else status_item no "client2 已进入调度" "等待 client2"; fi
    if [ "$c1_grants" -gt 0 ]; then status_item yes "client1 获得空口发送机会" "grants=$c1_grants"; else status_item no "client1 获得空口发送机会" "grants=0"; fi
    if [ "$c2_grants" -gt 0 ]; then status_item yes "client2 获得空口发送机会" "grants=$c2_grants"; else status_item no "client2 获得空口发送机会" "grants=0"; fi
    if [ "$data_packets" -gt 0 ]; then status_item yes "server 收到无线数据" "累计数据包=$data_packets"; else status_item no "server 收到无线数据" "等待 TCP 上传开始"; fi
    if [ "$ready_reject" -eq 0 ]; then status_item yes "调度拒收" "ready_reject=0"; else status_item warn "调度拒收" "ready_reject=$ready_reject，需要解释"; fi
    printf '\n老师口径：两个 client 都出现“获得空口发送机会”，且 server 数据包增长，说明两台机器正在轮流通过真实无线链路上传。\n'
    printf '操作提示：保持本窗口不关；另开两个 server 接收窗口，再让两个 client 执行发送。按 Ctrl-C 停止本机 WFB。\n\n'
    show_recent_errors "$log_file"
}

client_dashboard() {
    local role="$1"
    local pid="$2"
    local log_file="$3"
    local send_log="$4"
    local tun_name trusted tun_ok grant_accept auth sent_line elapsed
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

    clear_screen
    banner "$role 上行链路面板：给老师看的实时状态"
    printf '演示名: %s\n' "$DEMO_NAME"
    printf '运行时间: %s    日志: %s\n\n' "${elapsed:-running}" "$log_file"
    status_item "$trusted" "链路安全口径" "trusted_plaintext 已出现才算正确"
    status_item "$tun_ok" "$role TUN" "$tun_name = $(client_ip_for "$role")/24"
    if [ "$grant_accept" -gt 0 ]; then status_item yes "收到 server 授权" "grant_accept=$grant_accept"; else status_item no "收到 server 授权" "等待 server 调度"; fi
    if [ "$auth" -gt 0 ]; then status_item yes "允许发送数据" "authorized_sends=$auth"; else status_item no "允许发送数据" "等待 TCP 上传或授权"; fi
    if [ -n "$sent_line" ]; then status_item yes "TCP 文件上传" "$sent_line"; else status_item no "TCP 文件上传" "发送窗口完成后会显示"; fi
    printf '\n老师口径：本机出现“收到 server 授权”和“允许发送数据”，说明它不是自己乱发，而是在 server 授权窗口内上传。\n'
    printf '操作提示：保持本窗口不关；另开终端执行 %s-send。按 Ctrl-C 停止本机 WFB。\n\n' "$role"
    show_recent_errors "$log_file"
}

run_server_wfb() {
    require_demo_name
    require_common_commands
    require_sudo_session
    mkdir -p "$(server_log_dir)"
    local log_file pid
    log_file="$(server_log_dir)/wfb_v6_uplink.log"
    : > "$log_file"
    log_info "启动 server WFB；屏幕只显示简化面板，完整日志写入 $log_file"
    sudo "$PROJECT_ROOT/wfb_v6_uplink" \
        --role server \
        --tun-name v6us0 \
        --tun-addr 10.80.0.1/24 \
        --node-id 9 \
        --link-id "$LINK_ID" \
        --stream "$UPLINK_STREAM" \
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
    trap 'kill "$pid" 2>/dev/null || true; wait "$pid" 2>/dev/null || true' INT TERM EXIT
    while kill -0 "$pid" 2>/dev/null; do
        server_dashboard "$pid" "$log_file"
        sleep "$DASHBOARD_INTERVAL"
    done
    wait "$pid"
}

run_client_wfb() {
    local role="$1"
    require_demo_name
    require_common_commands
    require_sudo_session
    local ulog log_file send_log tun_name node_id iface pause resume queue_limit
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
    : > "$log_file"
    log_info "启动 $role WFB；屏幕只显示简化面板，完整日志写入 $log_file"
    sudo "$PROJECT_ROOT/wfb_v6_uplink" \
        --role client \
        --tun-name "$tun_name" \
        --tun-addr "$(client_ip_for "$role")/24" \
        --node-id "$node_id" \
        --link-id "$LINK_ID" \
        --stream "$UPLINK_STREAM" \
        --air-interface "$iface" \
        --uplink-pause-threshold-bytes "$pause" \
        --uplink-resume-threshold-bytes "$resume" \
        --uplink-queue-packets-limit "$queue_limit" \
        --queue-summary-file "$ulog/${role}_queue_summary.json" \
        --log-interval 200 \
        > "$log_file" 2>&1 &
    local pid=$!
    trap 'kill "$pid" 2>/dev/null || true; wait "$pid" 2>/dev/null || true' INT TERM EXIT
    while kill -0 "$pid" 2>/dev/null; do
        client_dashboard "$role" "$pid" "$log_file" "$send_log"
        sleep "$DASHBOARD_INTERVAL"
    done
    wait "$pid"
}

server_recv() {
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
        --label "server 接收 $role 上传" \
        --bind-ip 10.80.0.1 \
        --port "$port" \
        --output "$output" \
        --expected-bytes "$DEMO_FILE_SIZE" \
        --timeout 300 \
        --log-file "$log_file"
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
    server-recv-client1)
        server_recv client1
        ;;
    server-recv-client2)
        server_recv client2
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
