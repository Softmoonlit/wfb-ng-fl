#!/bin/bash
# tests/real_hardware/run_fl_demo.sh
# WFB-FL ARMv8 现场演示总控脚本与多维指标看板

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# 加载集群全局配置解析器
# shellcheck source=scripts/cluster_config.sh
source "$PROJECT_ROOT/scripts/cluster_config.sh"

log_step() { echo ""; echo "${C_BOLD}${C_CYAN}=== 步骤 $1: $2 ===${C_RESET}"; }

# 演示环境默认参数
CONFIG_FILE="${CONFIG_FILE:-$PROJECT_ROOT/cluster_nodes.conf}"
SOURCE_FILE=""
REMOTE_REPO="${REMOTE_REPO:-projects/wfb-ng-fl}"
WORK_DIR="${WORK_DIR:-/var/lib/wfb-ng/fl_demo}"
REMOTE_RECEIVED_DIR="/var/lib/wfb-ng/fl_demo/received"
SERVER_RECEIVED_DIR="/var/lib/wfb-ng/fl_demo/server_received"
SERVER_RUN_DIR="$WORK_DIR/run"
DRY_RUN=0
COMMAND="all"

# 链路与传输固定协议参数 (严格继承基线成熟契约)
LINK_ID=406
UPLINK_STREAM=32
DOWNLINK_STREAM=33
FEC_K=8
FEC_N=12
MULTICAST_GROUP="239.80.41.1"
UFTP_PORT=1044
HTTP_PORT=8080
GRANT_DURATION_MS=120
GUARD_INTERVAL_MS=10
DOWNLINK_PAUSE_THRESHOLD_BYTES=131072
DOWNLINK_RESUME_THRESHOLD_BYTES=65536
DOWNLINK_QUEUE_PACKETS_LIMIT=64
FEEDBACK_WINDOW_PERIOD_MS=200
FEEDBACK_WINDOW_DURATION_MS=20

# TUN 设备名称定义
SERVER_TUN="wfb_tun_s0"

# SSH 统一选项
SSH_COMMON_OPTS=(
    -o "StrictHostKeyChecking=accept-new"
    -o "ConnectTimeout=2"
    -o "BatchMode=yes"
)

# 进程跟踪
SERVER_LINK_PID=""
SERVER_HTTP_PID=""

usage() {
    cat <<EOF
${C_BOLD}WFB-FL ARMv8 现场演示总控脚本与多维指标看板${C_RESET}

用法:
  bash tests/real_hardware/run_fl_demo.sh [子命令] [选项]

子命令:
  all                       执行完整演示流程：空口准备 -> 下行组播分发 -> 上行受控回传 (默认)
  downlink                  仅执行单步独立下行组播大文件分发
  uplink                    仅执行单步独立上行受控大文件回传
  clean                     停止残留后台进程并清理 TUN 虚拟网卡

选项:
  -f, --file <路径>         指定下行传输的大文件路径 (缺省时自动现场生成 40MB 测试文件)
  -c, --config <路径>       指定集群全局配置文件 (默认: cluster_nodes.conf)
  -w, --work-dir <路径>     指定本地工作与运行日志目录 (默认: /var/lib/wfb-ng/fl_demo)
  -r, --remote-dir <路径>   指定远端 Client 部署目录 (默认: projects/wfb-ng-fl)
  -n, --dry-run             演练模式 (模拟空口、多机交互与指标看板渲染)
  -h, --help                显示本帮助信息并退出

说明:
  本脚本面向现场汇报演示，采用 WFB-FL 高可靠通信底座；
  自动探测局域网在线 Client 集合，若有离线节点给出明显黄色告警并容错跳过；
  自动 down/up 切换网卡为 monitor 模式并锁定到指定信道与频宽；
  下行采用 UFTP 组播广播，各 Client 自动落盘并终端打印绝对路径；
  上行复用该文件发起受控 HTTP PUT 回传；
  最终在终端分别渲染多维下行与上行指标看板。
EOF
}

# 1. 命令行参数解析
parse_args() {
    while [ $# -gt 0 ]; do
        case "$1" in
            -h|--help)
                usage
                exit 0
                ;;
            all|downlink|uplink|clean)
                COMMAND="$1"
                shift
                ;;
            -f|--file)
                SOURCE_FILE="$2"
                shift 2
                ;;
            -c|--config)
                CONFIG_FILE="$2"
                shift 2
                ;;
            -w|--work-dir)
                WORK_DIR="$2"
                SERVER_RUN_DIR="$WORK_DIR/run"
                SERVER_RECEIVED_DIR="$WORK_DIR/server_received"
                shift 2
                ;;
            -r|--remote-dir)
                REMOTE_REPO="$2"
                shift 2
                ;;
            -n|--dry-run)
                DRY_RUN=1
                shift
                ;;
            *)
                log_fail "未知选项或参数: $1"
                usage
                exit 1
                ;;
        esac
    done
}

# 2. 读取并严格校验集群配置
step1_load_config() {
    log_step "1" "读取并解析集群配置文件: $CONFIG_FILE"
    load_cluster_config "$CONFIG_FILE"
    log_pass "配置解析完成，登记 Client 节点共 $CLIENT_COUNT 台 (信道: $WIRELESS_CHANNEL, 功率: ${WIRELESS_TXPOWER_DBM}dBm)"
}

# 3. 动态探测 Client 在线状态与容错跳过
declare -a ONLINE_INDICES=()
declare -a OFFLINE_INDICES=()

step2_probe_clients() {
    log_step "2" "探测 Client 在线状态与容错集合发现"

    ONLINE_INDICES=()
    OFFLINE_INDICES=()

    for i in "${!CLIENT_ROLES[@]}"; do
        local r="${CLIENT_ROLES[$i]}"
        local h="${CLIENT_HOSTS[$i]}"
        local u="${CLIENT_USERS[$i]}"

        if [ "$DRY_RUN" -eq 1 ]; then
            ONLINE_INDICES+=("$i")
            log_info "  [DRY-RUN] 模拟发现在线节点: $r ($h)"
            continue
        fi

        if ssh "${SSH_COMMON_OPTS[@]}" "${u}@${h}" "true" >/dev/null 2>&1; then
            ONLINE_INDICES+=("$i")
            log_pass "  [ONLINE] 节点 $r ($h) 在线且就绪"
        else
            OFFLINE_INDICES+=("$i")
            log_warn "节点 $r ($h) 离线或不可达，已安全跳过该节点"
        fi
    done

    local total="${#CLIENT_ROLES[@]}"
    local online="${#ONLINE_INDICES[@]}"
    local offline="${#OFFLINE_INDICES[@]}"

    if [ "$online" -eq 0 ] && [ "$DRY_RUN" -eq 0 ]; then
        log_fail "所有 Client 节点均离线或不可达 (共 $total 台)，无法继续执行演示！"
        exit 1
    fi

    log_info "在线节点探测汇总: 登记共 $total 台，在线 $online 台，跳过离线 $offline 台"
}

# 4. 残留进程与 TUN 网卡安全清理
cleanup_processes() {
    log_info "正在清理残留后台进程与虚拟网卡..."

    if [ "$DRY_RUN" -eq 1 ]; then
        log_info "  [DRY-RUN] 模拟清理 server 与 client 残留进程"
        return 0
    fi

    # Server 本机清理
    sudo pkill -x wfb_v6_uplink 2>/dev/null || true
    sudo pkill -x uftp 2>/dev/null || true
    sudo pkill -x uftpd 2>/dev/null || true
    if [ -n "$SERVER_HTTP_PID" ] && kill -0 "$SERVER_HTTP_PID" 2>/dev/null; then
        kill "$SERVER_HTTP_PID" 2>/dev/null || true
    fi
    sudo ip link delete "$SERVER_TUN" 2>/dev/null || true

    # 各在线 Client 远程清理
    for i in "${ONLINE_INDICES[@]}"; do
        local r="${CLIENT_ROLES[$i]}"
        local h="${CLIENT_HOSTS[$i]}"
        local u="${CLIENT_USERS[$i]}"
        local nid="${CLIENT_NODE_IDS[$i]}"
        local c_tun="wfb_tun_c${nid}"

        ssh "${SSH_COMMON_OPTS[@]}" "${u}@${h}" "
            sudo pkill -x wfb_v6_uplink 2>/dev/null || true
            sudo pkill -x uftp 2>/dev/null || true
            sudo pkill -x uftpd 2>/dev/null || true
            sudo ip link delete '$c_tun' 2>/dev/null || true
        " || true
    done
}

# 5. 空口网卡准备：探测唯一 wlx* 网卡，切换 monitor 模式并锁定信道与功率
find_wlx_local() {
    iw dev 2>/dev/null | awk '/Interface / {print $2}' | grep '^wlx' || true
}

step3_configure_radio_interfaces() {
    log_step "3" "准备无线空口网卡 (探测 wlx*，设为 monitor 模式并锁定参数)"

    if [ "$DRY_RUN" -eq 1 ]; then
        log_info "  [DRY-RUN] 模拟在 Server 与各在线 Client 探测并配置 wlx* 网卡"
        log_info "  [DRY-RUN] 设定信道: $WIRELESS_CHANNEL $WIRELESS_CHANNEL_WIDTH, 功率: ${WIRELESS_TXPOWER_DBM}dBm"
        return 0
    fi

    # Server 本机网卡探测与配置
    local server_ifaces
    server_ifaces=($(find_wlx_local))
    if [ "${#server_ifaces[@]}" -ne 1 ]; then
        log_fail "Server 本机必须恰好存在一个 wlx* 无线网卡，当前发现 ${#server_ifaces[@]} 个 (${server_ifaces[*]:-无})"
        exit 1
    fi
    local s_iface="${server_ifaces[0]}"
    log_info "Server 本机探测到空口网卡: $s_iface，正在配置 monitor 模式..."

    sudo ip link set "$s_iface" down || true
    sudo iw dev "$s_iface" set type monitor
    sudo ip link set "$s_iface" up
    sudo iw dev "$s_iface" set channel "$WIRELESS_CHANNEL" "$WIRELESS_CHANNEL_WIDTH"
    if sudo test -w /sys/module/88XXau_wfb/parameters/rtw_tx_pwr_idx_override; then
        echo "$WIRELESS_TXPOWER_DBM" | sudo tee /sys/module/88XXau_wfb/parameters/rtw_tx_pwr_idx_override >/dev/null || true
    fi
    sudo iw dev "$s_iface" set txpower fixed "-$((WIRELESS_TXPOWER_DBM * 100))" 2>/dev/null || true

    if ! iw dev "$s_iface" info | grep -q 'type monitor'; then
        log_fail "Server 空口网卡 $s_iface 切换 monitor 模式失败！"
        exit 1
    fi
    log_pass "Server 空口网卡 $s_iface 已锁定至信道 $WIRELESS_CHANNEL $WIRELESS_CHANNEL_WIDTH (功率: ${WIRELESS_TXPOWER_DBM}dBm)"

    # 各在线 Client 远端网卡探测与配置
    for i in "${ONLINE_INDICES[@]}"; do
        local r="${CLIENT_ROLES[$i]}"
        local h="${CLIENT_HOSTS[$i]}"
        local u="${CLIENT_USERS[$i]}"

        log_info "正在配置节点 $r ($h) 空口网卡..."
        local c_ifaces
        c_ifaces=($(ssh "${SSH_COMMON_OPTS[@]}" "${u}@${h}" "iw dev 2>/dev/null | awk '/Interface / {print \$2}' | grep '^wlx' || true"))
        if [ "${#c_ifaces[@]}" -ne 1 ]; then
            log_fail "节点 $r 必须恰好存在一个 wlx* 无线网卡，当前发现 ${#c_ifaces[@]} 个 (${c_ifaces[*]:-无})"
            exit 1
        fi
        local c_iface="${c_ifaces[0]}"

        ssh "${SSH_COMMON_OPTS[@]}" "${u}@${h}" "
            sudo ip link set '$c_iface' down || true
            sudo iw dev '$c_iface' set type monitor
            sudo ip link set '$c_iface' up
            sudo iw dev '$c_iface' set channel '$WIRELESS_CHANNEL' '$WIRELESS_CHANNEL_WIDTH'
            if sudo test -w /sys/module/88XXau_wfb/parameters/rtw_tx_pwr_idx_override; then
                echo '$WIRELESS_TXPOWER_DBM' | sudo tee /sys/module/88XXau_wfb/parameters/rtw_tx_pwr_idx_override >/dev/null || true
            fi
            sudo iw dev '$c_iface' set txpower fixed '-$((WIRELESS_TXPOWER_DBM * 100))' 2>/dev/null || true
            iw dev '$c_iface' info | grep -q 'type monitor'
        "
        log_pass "节点 $r 空口网卡 $c_iface 已就绪"
    done
}

check_tun_device() {
    local dev="$1"
    [ -e "/sys/class/net/$dev" ] || ip link show "$dev" >/dev/null 2>&1
}

# 6. 启动底层 WFB Mesh 空口信道服务
step4_start_wfb_mesh() {
    log_step "4" "启动底层 WFB Mesh 组播与调度信道"

    if [ "$DRY_RUN" -eq 1 ]; then
        log_info "  [DRY-RUN] 模拟拉起 Server 端与 Client 端 wfb_v6_uplink 底座进程"
        log_info "  [DRY-RUN] 模拟绑定组播路由: $MULTICAST_GROUP dev $SERVER_TUN"
        return 0
    fi

    mkdir -p "$SERVER_RUN_DIR"
    local s_iface
    s_iface="$(find_wlx_local | head -n1)"

    # 计算带宽参数
    local bw=40
    if [[ "$WIRELESS_CHANNEL_WIDTH" == *"20"* ]]; then
        bw=20
    fi

    # 构造客户端调度参数
    local known_clients=""
    local client_target_args=()
    local first=1
    for i in "${ONLINE_INDICES[@]}"; do
        local nid="${CLIENT_NODE_IDS[$i]}"
        local tip="${CLIENT_TUN_IPS[$i]}"
        if [ "$first" -eq 1 ]; then
            first=0
            known_clients="$nid"
        else
            known_clients="$known_clients,$nid"
        fi
        client_target_args+=(--client-target "$nid:$tip:127.0.0.1:1")
    done

    local wfb_bin="wfb_v6_uplink"

    log_info "启动 Server 端 wfb_v6_uplink 守护进程..."
    sudo bash -c "nohup '$wfb_bin' \
        --role server \
        --tun-name '$SERVER_TUN' \
        --tun-addr '$SERVER_TUN_IP' \
        --node-id '$SERVER_NODE_ID' \
        --link-id '$LINK_ID' \
        --uplink-stream '$UPLINK_STREAM' \
        --downlink-stream '$DOWNLINK_STREAM' \
        --fec-k '$FEC_K' \
        --fec-n '$FEC_N' \
        --radio-bandwidth '$bw' \
        --radio-mcs-index '$DOWNLINK_MCS' \
        --radio-short-gi \
        --air-interface '$s_iface' \
        --known-clients '$known_clients' \
        ${client_target_args[*]} \
        --grant-duration-ms '$GRANT_DURATION_MS' \
        --guard-interval-ms '$GUARD_INTERVAL_MS' \
        --downlink-pause-threshold-bytes '$DOWNLINK_PAUSE_THRESHOLD_BYTES' \
        --downlink-resume-threshold-bytes '$DOWNLINK_RESUME_THRESHOLD_BYTES' \
        --downlink-queue-packets-limit '$DOWNLINK_QUEUE_PACKETS_LIMIT' \
        --feedback-window-period-ms '$FEEDBACK_WINDOW_PERIOD_MS' \
        --feedback-window-duration-ms '$FEEDBACK_WINDOW_DURATION_MS' \
        --queue-summary-file '$SERVER_RUN_DIR/server_queue_summary.json' \
        --log-interval 200 \
        < /dev/null > '$SERVER_RUN_DIR/wfb.log' 2>&1 & echo \$! > '$SERVER_RUN_DIR/wfb.pid'"

    SERVER_LINK_PID="$(cat "$SERVER_RUN_DIR/wfb.pid")"

    # 启动各 Client 端 wfb_v6_uplink
    for i in "${ONLINE_INDICES[@]}"; do
        local r="${CLIENT_ROLES[$i]}"
        local h="${CLIENT_HOSTS[$i]}"
        local u="${CLIENT_USERS[$i]}"
        local nid="${CLIENT_NODE_IDS[$i]}"
        local tip="${CLIENT_TUN_IPS[$i]}"
        local c_tun="wfb_tun_c${nid}"

        log_info "启动节点 $r 端 wfb_v6_uplink 守护进程..."
        ssh "${SSH_COMMON_OPTS[@]}" "${u}@${h}" "
            mkdir -p '$WORK_DIR/run'
            c_iface=\$(iw dev 2>/dev/null | awk '/Interface / {print \$2}' | grep '^wlx' | head -n1)
            sudo bash -c \"nohup wfb_v6_uplink \
                --role client \
                --tun-name '$c_tun' \
                --tun-addr '$tip/24' \
                --node-id '$nid' \
                --link-id '$LINK_ID' \
                --uplink-stream '$UPLINK_STREAM' \
                --downlink-stream '$DOWNLINK_STREAM' \
                --fec-k '$FEC_K' \
                --fec-n '$FEC_N' \
                --radio-bandwidth '$bw' \
                --radio-mcs-index '$UPLINK_MCS' \
                --radio-short-gi \
                --air-interface \\\"\$c_iface\\\" \
                --uplink-pause-threshold-bytes '$DOWNLINK_PAUSE_THRESHOLD_BYTES' \
                --uplink-resume-threshold-bytes '$DOWNLINK_RESUME_THRESHOLD_BYTES' \
                --uplink-queue-packets-limit '$DOWNLINK_QUEUE_PACKETS_LIMIT' \
                --queue-summary-file '$WORK_DIR/run/client_queue_summary.json' \
                --log-interval 200 \
                < /dev/null > '$WORK_DIR/run/wfb.log' 2>&1 & echo \\\$! > '$WORK_DIR/run/wfb.pid'\"
        "
    done

    # 等待 Server TUN 就绪
    local ready=0
    for _ in $(seq 1 50); do
        if check_tun_device "$SERVER_TUN"; then
            ready=1
            break
        fi
        sleep 0.1
    done
    [ "$ready" -eq 1 ] || { log_fail "Server TUN 接口 $SERVER_TUN 未按时出现！"; exit 1; }

    # 绑定组播路由
    sudo ip route replace "$MULTICAST_GROUP/32" dev "$SERVER_TUN"
    for i in "${ONLINE_INDICES[@]}"; do
        local r="${CLIENT_ROLES[$i]}"
        local h="${CLIENT_HOSTS[$i]}"
        local u="${CLIENT_USERS[$i]}"
        local nid="${CLIENT_NODE_IDS[$i]}"
        local c_tun="wfb_tun_c${nid}"
        ssh "${SSH_COMMON_OPTS[@]}" "${u}@${h}" "
            for _ in \$(seq 1 50); do
                if [ -e '/sys/class/net/$c_tun' ] || ip link show '$c_tun' >/dev/null 2>&1; then exit 0; fi
                sleep 0.1
            done
            exit 1
        " || { log_fail "Client $r TUN 接口 $c_tun 未出现！"; exit 1; }
        ssh "${SSH_COMMON_OPTS[@]}" "${u}@${h}" "sudo ip route replace '$MULTICAST_GROUP/32' dev '$c_tun'"
    done

    log_pass "WFB Mesh 底座与组播网络路由配置完成"
}

init_work_dirs() {
    if [ "$DRY_RUN" -eq 1 ]; then
        mkdir -p "$WORK_DIR/source" "$WORK_DIR/run" "$SERVER_RECEIVED_DIR"
    else
        sudo mkdir -p "$WORK_DIR/source" "$WORK_DIR/run" "$SERVER_RECEIVED_DIR"
        sudo chown -R "$(id -u):$(id -g)" "$WORK_DIR"
    fi
}

# 7. 下行传输实现 (Downlink Transmission)
run_downlink() {
    log_step "5" "执行下行大文件组播广播分发 (Downlink UFTP)"

    init_work_dirs

    # 准备下行源文件 (如用户未指定，自动现场确定性生成 40MB 测试文件)
    if [ -z "$SOURCE_FILE" ]; then
        SOURCE_FILE="$WORK_DIR/source/model_40mb.bin"
        if [ ! -f "$SOURCE_FILE" ] || [ "$(stat -c%s "$SOURCE_FILE" 2>/dev/null || echo 0)" -ne 41943040 ]; then
            log_info "未指定 --file，现场自动生成 40MB 确定性测试文件: $SOURCE_FILE..."
            python3 "$PROJECT_ROOT/tests/real_hardware/fl_demo_metrics.py" generate-file \
                --output "$SOURCE_FILE" \
                --size 41943040 \
                --seed "wfb_fl_demo_source" >/dev/null
        fi
    fi

    if [ ! -f "$SOURCE_FILE" ]; then
        log_fail "源文件不存在: $SOURCE_FILE"
        exit 1
    fi

    local src_name
    src_name="$(basename "$SOURCE_FILE")"
    local file_meta
    file_meta="$(python3 "$PROJECT_ROOT/tests/real_hardware/fl_demo_metrics.py" sha256 --file "$SOURCE_FILE")"
    local source_sha
    source_sha="$(echo "$file_meta" | awk '{print $1}')"
    local source_size
    source_size="$(echo "$file_meta" | awk '{print $2}')"

    log_info "下行源文件: $SOURCE_FILE (大小: $source_size 字节, SHA-256: $source_sha)"

    if [ "$DRY_RUN" -eq 1 ]; then
        log_info "  [DRY-RUN] 模拟在各 Client 拉起 uftpd 监听组播组: $MULTICAST_GROUP:$UFTP_PORT"
        log_info "  [DRY-RUN] 模拟 Server 调用 uftp 广播下发文件..."
        local mock_dl_summary="$WORK_DIR/run/downlink_summary.json"
        cat > "$mock_dl_summary" <<EOF
{
  "source_file": "$SOURCE_FILE",
  "size_bytes": $source_size,
  "source_sha256": "$source_sha",
  "channel": "$WIRELESS_CHANNEL",
  "channel_width": "$WIRELESS_CHANNEL_WIDTH",
  "txpower_dbm": $WIRELESS_TXPOWER_DBM,
  "multicast_group": "$MULTICAST_GROUP",
  "multicast_port": $UFTP_PORT,
  "mcs": $DOWNLINK_MCS,
  "rate_kbps": $UFTP_RATE_KBPS,
  "duration_seconds": 22.35,
  "clients": [
EOF
        local first_c=1
        for i in "${ONLINE_INDICES[@]}"; do
            local r="${CLIENT_ROLES[$i]}"
            local h="${CLIENT_HOSTS[$i]}"
            [ "$first_c" -eq 1 ] || echo "," >> "$mock_dl_summary"
            first_c=0
            cat >> "$mock_dl_summary" <<EOF
    {
      "role": "$r",
      "host_ip": "$h",
      "received_path": "$REMOTE_RECEIVED_DIR/$src_name",
      "size_bytes": $source_size,
      "duration_seconds": 22.30,
      "sha256": "$source_sha"
    }
EOF
        done
        cat >> "$mock_dl_summary" <<EOF
  ]
}
EOF
        echo ""
        python3 "$PROJECT_ROOT/tests/real_hardware/fl_demo_metrics.py" render-downlink --json "$mock_dl_summary"
        return 0
    fi

    # 1. 在各在线 Client 远端创建目录并启动 uftpd 接收端
    local uftp_hosts=""
    local first=1
    for i in "${ONLINE_INDICES[@]}"; do
        local r="${CLIENT_ROLES[$i]}"
        local h="${CLIENT_HOSTS[$i]}"
        local u="${CLIENT_USERS[$i]}"
        local nid="${CLIENT_NODE_IDS[$i]}"
        local tip="${CLIENT_TUN_IPS[$i]}"
        local hex_uid
        hex_uid=$(printf '0x%08x' "$nid")

        if [ "$first" -eq 1 ]; then
            first=0
            uftp_hosts="$hex_uid"
        else
            uftp_hosts="$uftp_hosts,$hex_uid"
        fi

        log_info "在节点 $r ($h) 拉起 uftpd 接收端 (UID: $hex_uid, 目录: $REMOTE_RECEIVED_DIR)..."
        ssh "${SSH_COMMON_OPTS[@]}" "${u}@${h}" "
            sudo mkdir -p '$REMOTE_RECEIVED_DIR' '$WORK_DIR/tmp'
            sudo rm -f '$REMOTE_RECEIVED_DIR/$src_name'
            sudo chown -R \$(id -u):\$(id -g) '$WORK_DIR'
            nohup uftpd -d -q -I '$tip' -M '$MULTICAST_GROUP' -p '$UFTP_PORT' -U '$hex_uid' \
                -D '$REMOTE_RECEIVED_DIR/' -T '$WORK_DIR/tmp/' -F '$WORK_DIR/uftpd.status' \
                > '$WORK_DIR/uftpd.log' 2>&1 & echo \$! > '$WORK_DIR/uftpd.pid'
        "
    done

    # 2. Server 端调用 uftp 发起组播广播
    log_info "Server 启动 UFTP 组播发送 (速率: ${UFTP_RATE_KBPS} Kbps, 目标 UIDs: $uftp_hosts)..."
    local s_tun_ip_clean="${SERVER_TUN_IP%/*}"
    local t_start
    t_start="$(python3 -c 'import time; print(time.monotonic())')"

    sudo uftp -q -I "$s_tun_ip_clean" -M "$MULTICAST_GROUP" -p "$UFTP_PORT" -U 0x000000ff \
        -H "$uftp_hosts" -Y none -R "$UFTP_RATE_KBPS" -r 0.1:0.01:2.0 -s 20 \
        -L "$SERVER_RUN_DIR/uftp.log" -S "$SERVER_RUN_DIR/uftp.status" \
        "$SOURCE_FILE"

    local t_end
    t_end="$(python3 -c 'import time; print(time.monotonic())')"
    local duration
    duration="$(python3 -c "print(round($t_end - $t_start, 3))")"
    log_pass "UFTP 组播发送阶段结束，用时 $duration 秒"

    # 3. 等待各 Client 接收落盘并核对 SHA-256
    log_info "核验各在线 Client 落盘文件与 SHA-256 完整性..."
    local dl_clients_json=()

    for i in "${ONLINE_INDICES[@]}"; do
        local r="${CLIENT_ROLES[$i]}"
        local h="${CLIENT_HOSTS[$i]}"
        local u="${CLIENT_USERS[$i]}"

        # 等待文件落盘
        ssh "${SSH_COMMON_OPTS[@]}" "${u}@${h}" "
            for _ in \$(seq 1 100); do
                if [ -f '$REMOTE_RECEIVED_DIR/$src_name' ] && [ \$(stat -c%s '$REMOTE_RECEIVED_DIR/$src_name' 2>/dev/null || echo 0) -eq $source_size ]; then
                    exit 0
                fi
                sleep 0.2
            done
            exit 1
        " || { log_fail "节点 $r 接收超时或大小不匹配！"; exit 1; }

        local c_sha
        c_sha="$(ssh "${SSH_COMMON_OPTS[@]}" "${u}@${h}" "sha256sum '$REMOTE_RECEIVED_DIR/$src_name'" | awk '{print $1}')"

        dl_clients_json+=(
            "{\"role\": \"$r\", \"host_ip\": \"$h\", \"received_path\": \"$REMOTE_RECEIVED_DIR/$src_name\", \"size_bytes\": $source_size, \"duration_seconds\": $duration, \"sha256\": \"$c_sha\"}"
        )
    done

    # 停止各 Client 端 uftpd
    for i in "${ONLINE_INDICES[@]}"; do
        local h="${CLIENT_HOSTS[$i]}"
        local u="${CLIENT_USERS[$i]}"
        ssh "${SSH_COMMON_OPTS[@]}" "${u}@${h}" "sudo pkill -x uftpd 2>/dev/null || true" || true
    done

    # 生成下行指标汇总 JSON
    local dl_summary_file="$SERVER_RUN_DIR/downlink_summary.json"
    cat > "$dl_summary_file" <<EOF
{
  "source_file": "$SOURCE_FILE",
  "size_bytes": $source_size,
  "source_sha256": "$source_sha",
  "channel": "$WIRELESS_CHANNEL",
  "channel_width": "$WIRELESS_CHANNEL_WIDTH",
  "txpower_dbm": $WIRELESS_TXPOWER_DBM,
  "multicast_group": "$MULTICAST_GROUP",
  "multicast_port": $UFTP_PORT,
  "mcs": $DOWNLINK_MCS,
  "rate_kbps": $UFTP_RATE_KBPS,
  "duration_seconds": $duration,
  "clients": [
    $(IFS=,; echo "${dl_clients_json[*]}")
  ]
}
EOF

    echo ""
    if ! python3 "$PROJECT_ROOT/tests/real_hardware/fl_demo_metrics.py" render-downlink --json "$dl_summary_file"; then
        log_fail "下行传输校验失败，存在未通过节点！"
        exit 1
    fi
}

# 8. 上行传输实现 (Uplink Transmission)
run_uplink() {
    log_step "6" "执行上行受控大文件回传 (Uplink HTTP PUT via WFB TUN)"

    init_work_dirs
    mkdir -p "$SERVER_RECEIVED_DIR" "$SERVER_RUN_DIR/clients"

    local src_name="model_40mb.bin"
    if [ -n "$SOURCE_FILE" ]; then
        src_name="$(basename "$SOURCE_FILE")"
    fi

    # 查找原始参考模型文件，用于严格哈希比对
    local ref_file=""
    if [ -n "$SOURCE_FILE" ] && [ -f "$SOURCE_FILE" ]; then
        ref_file="$SOURCE_FILE"
    elif [ -f "$WORK_DIR/source/$src_name" ]; then
        ref_file="$WORK_DIR/source/$src_name"
    fi

    if [ -z "$ref_file" ] || [ ! -f "$ref_file" ]; then
        log_fail "未找到原始参考模型文件 ($src_name)，无法进行上行一致性校验！请先执行 downlink 或通过 -f 传入参考文件"
        exit 1
    fi

    local expected_sha
    expected_sha="$(python3 "$PROJECT_ROOT/tests/real_hardware/fl_demo_metrics.py" sha256 --file "$ref_file" | awk '{print $1}')"

    if [ "$DRY_RUN" -eq 1 ]; then
        log_info "  [DRY-RUN] 模拟 Server 启动 HTTP PUT 接收端: ${SERVER_TUN_IP%/*}:$HTTP_PORT"
        log_info "  [DRY-RUN] 模拟各在线 Client 并发执行 HTTP PUT 回传..."
        local mock_ul_summary="$WORK_DIR/run/uplink_summary.json"
        local mock_size
        mock_size="$(stat -c%s "$ref_file" 2>/dev/null || echo 41943040)"
        cat > "$mock_ul_summary" <<EOF
{
  "server_address": "${SERVER_TUN_IP%/*}:$HTTP_PORT",
  "total_bytes": $((mock_size * ${#ONLINE_INDICES[@]})),
  "duration_seconds": 38.45,
  "expected_sha256": "$expected_sha",
  "mcs": $UPLINK_MCS,
  "slot_duration_ms": $GRANT_DURATION_MS,
  "guard_interval_ms": $GUARD_INTERVAL_MS,
  "telemetry": {
    "rx_packets": 88421,
    "rx_bytes": 132488192,
    "packets_lost": 1204,
    "loss_rate": 0.0134,
    "packets_fec_recovered": 1189,
    "fec_recovery_rate": 0.9875,
    "tun_pause_count": 12,
    "tun_resume_count": 12,
    "tcp_retransmits": 3,
    "authorized_sends_by_node": {
      "1": 116,
      "2": 116,
      "3": 116
    },
    "loss_and_fec_by_node": {
      "1": {"rx_packets": 29400, "packets_lost": 401, "packets_fec_recovered": 396},
      "2": {"rx_packets": 29510, "packets_lost": 402, "packets_fec_recovered": 397},
      "3": {"rx_packets": 29511, "packets_lost": 401, "packets_fec_recovered": 396}
    }
  },
  "clients": [
EOF
        local first_c=1
        for i in "${ONLINE_INDICES[@]}"; do
            local r="${CLIENT_ROLES[$i]}"
            local nid="${CLIENT_NODE_IDS[$i]}"
            local tip="${CLIENT_TUN_IPS[$i]}"
            [ "$first_c" -eq 1 ] || echo "," >> "$mock_ul_summary"
            first_c=0
            cat >> "$mock_ul_summary" <<EOF
    {
      "role": "$r",
      "tun_ip": "$tip",
      "server_path": "$SERVER_RECEIVED_DIR/client_${nid}_update.bin",
      "size_bytes": $mock_size,
      "duration_seconds": 37.50,
      "status_code": 201,
      "sha256": "$expected_sha"
    }
EOF
        done
        cat >> "$mock_ul_summary" <<EOF
  ]
}
EOF
        echo ""
        python3 "$PROJECT_ROOT/tests/real_hardware/fl_demo_metrics.py" render-uplink --json "$mock_ul_summary"
        return 0
    fi

    # 1. 严格确认各在线 Client 远端存在待回传模型文件 (坚决不进行 fallback 生成)
    for i in "${ONLINE_INDICES[@]}"; do
        local r="${CLIENT_ROLES[$i]}"
        local h="${CLIENT_HOSTS[$i]}"
        local u="${CLIENT_USERS[$i]}"

        local client_has_file
        client_has_file="$(ssh "${SSH_COMMON_OPTS[@]}" "${u}@${h}" "[ -f '$REMOTE_RECEIVED_DIR/$src_name' ] && [ \$(stat -c%s '$REMOTE_RECEIVED_DIR/$src_name' 2>/dev/null || echo 0) -gt 0 ] && echo 1 || echo 0")"
        if [ "$client_has_file" -ne 1 ]; then
            log_fail "节点 $r 缺少待回传模型文件 ($REMOTE_RECEIVED_DIR/$src_name)！请先运行 downlink 分发模型文件"
            exit 1
        fi
    done

    # 2. Server 端拉起受控 HTTP PUT 接收端
    local s_tun_ip_clean="${SERVER_TUN_IP%/*}"
    local ready_file="$SERVER_RUN_DIR/http_receiver_ready"
    rm -f "$ready_file"

    log_info "在 Server 端启动 HTTP PUT 接收端 ($s_tun_ip_clean:$HTTP_PORT)..."
    python3 "$PROJECT_ROOT/tests/real_hardware/fl_demo_metrics.py" http-receiver \
        --host "$s_tun_ip_clean" \
        --port "$HTTP_PORT" \
        --output-dir "$SERVER_RECEIVED_DIR" \
        --ready-file "$ready_file" \
        > "$SERVER_RUN_DIR/http_receiver.log" 2>&1 &
    SERVER_HTTP_PID=$!

    for _ in $(seq 1 50); do
        [ -f "$ready_file" ] && break
        sleep 0.1
    done
    [ -f "$ready_file" ] || { log_fail "Server HTTP PUT 接收端启动超时！"; exit 1; }

    # 3. 记录基线指标与时间戳
    local tcp_before
    tcp_before="$(python3 "$PROJECT_ROOT/tests/real_hardware/fl_demo_metrics.py" tcp-retrans)"
    local t_start
    t_start="$(python3 -c 'import time; print(time.monotonic())')"

    # 4. 各在线 Client 并发发起 HTTP PUT 回传
    log_info "各在线 Client 发起 HTTP PUT 并发回传..."
    local put_pids=()
    for i in "${ONLINE_INDICES[@]}"; do
        local r="${CLIENT_ROLES[$i]}"
        local h="${CLIENT_HOSTS[$i]}"
        local u="${CLIENT_USERS[$i]}"
        local nid="${CLIENT_NODE_IDS[$i]}"
        local tip="${CLIENT_TUN_IPS[$i]}"

        ssh "${SSH_COMMON_OPTS[@]}" "${u}@${h}" "
            python3 '$REMOTE_REPO/tests/real_hardware/fl_demo_metrics.py' http-sender \
                --host '$s_tun_ip_clean' \
                --port '$HTTP_PORT' \
                --file '$REMOTE_RECEIVED_DIR/$src_name' \
                --node-id '$nid' \
                --source-ip '$tip' \
                > '$WORK_DIR/run/put_result.json' 2>&1
        " &
        put_pids+=($!)
    done

    # 等待所有上传进程完成
    local upload_errors=0
    for pid in "${put_pids[@]}"; do
        wait "$pid" || upload_errors=$((upload_errors + 1))
    done
    [ "$upload_errors" -eq 0 ] || { log_fail "存在 Client HTTP PUT 回传进程异常失败！"; exit 1; }

    local t_end
    t_end="$(python3 -c 'import time; print(time.monotonic())')"
    local duration
    duration="$(python3 -c "print(round($t_end - $t_start, 3))")"
    local tcp_after
    tcp_after="$(python3 "$PROJECT_ROOT/tests/real_hardware/fl_demo_metrics.py" tcp-retrans)"
    local tcp_delta=$((tcp_after - tcp_before))
    if [ "$tcp_delta" -lt 0 ]; then tcp_delta=0; fi

    log_pass "所有 Client 回传完毕，用时 $duration 秒 (TCP 重传增量: $tcp_delta)"

    # 5. 采集各 Client 端日志与结果
    local ul_clients_json=()
    local total_bytes=0

    for i in "${ONLINE_INDICES[@]}"; do
        local r="${CLIENT_ROLES[$i]}"
        local h="${CLIENT_HOSTS[$i]}"
        local u="${CLIENT_USERS[$i]}"
        local nid="${CLIENT_NODE_IDS[$i]}"
        local tip="${CLIENT_TUN_IPS[$i]}"

        # 拉取 Client 上传结果与日志
        mkdir -p "$SERVER_RUN_DIR/clients/$r"
        ssh "${SSH_COMMON_OPTS[@]}" "${u}@${h}" "test -f '$WORK_DIR/run/put_result.json'" || { log_fail "节点 $r 缺少回传结果文件 $WORK_DIR/run/put_result.json"; exit 1; }
        scp -q "${SSH_COMMON_OPTS[@]}" "${u}@${h}:$WORK_DIR/run/put_result.json" "$SERVER_RUN_DIR/clients/$r/put_result.json"
        scp -q "${SSH_COMMON_OPTS[@]}" "${u}@${h}:$WORK_DIR/run/wfb.log" "$SERVER_RUN_DIR/clients/$r/wfb.log"
        scp -q "${SSH_COMMON_OPTS[@]}" "${u}@${h}:$WORK_DIR/run/client_queue_summary.json" "$SERVER_RUN_DIR/clients/$r/client_queue_summary.json"

        local server_land_file="$SERVER_RECEIVED_DIR/client_${nid}_update.bin"
        [ -f "$server_land_file" ] || { log_fail "Server 未收到节点 $r 的回传落盘文件: $server_land_file"; exit 1; }

        local file_meta
        file_meta="$(python3 "$PROJECT_ROOT/tests/real_hardware/fl_demo_metrics.py" sha256 --file "$server_land_file")"
        local c_sha
        c_sha="$(echo "$file_meta" | awk '{print $1}')"
        local c_bytes
        c_bytes="$(echo "$file_meta" | awk '{print $2}')"
        total_bytes=$((total_bytes + c_bytes))

        # 读取客户端测得的真实耗时与队列反压
        local c_dur
        c_dur="$(python3 -c "import json; print(json.load(open('$SERVER_RUN_DIR/clients/$r/put_result.json'))['duration_seconds'])")"
        local c_pause=0
        local c_resume=0
        if [ -f "$SERVER_RUN_DIR/clients/$r/client_queue_summary.json" ]; then
            c_pause="$(python3 -c "import json; print(json.load(open('$SERVER_RUN_DIR/clients/$r/client_queue_summary.json')).get('tun_read_pause_total', 0))")"
            c_resume="$(python3 -c "import json; print(json.load(open('$SERVER_RUN_DIR/clients/$r/client_queue_summary.json')).get('tun_read_resume_total', 0))")"
        fi

        ul_clients_json+=(
            "{\"role\": \"$r\", \"tun_ip\": \"$tip\", \"server_path\": \"$server_land_file\", \"size_bytes\": $c_bytes, \"duration_seconds\": $c_dur, \"status_code\": 201, \"sha256\": \"$c_sha\", \"tun_pause_count\": $c_pause, \"tun_resume_count\": $c_resume}"
        )
    done

    # 停止 Server HTTP 接收端
    if [ -n "$SERVER_HTTP_PID" ] && kill -0 "$SERVER_HTTP_PID" 2>/dev/null; then
        kill "$SERVER_HTTP_PID" 2>/dev/null || true
        SERVER_HTTP_PID=""
    fi

    # 6. 解析底座遥测并渲染看板
    log_info "正在解析 WFB 底座空口丢包、FEC 恢复与反压流控遥测数据..."
    local telemetry_file="$SERVER_RUN_DIR/telemetry.json"
    python3 "$PROJECT_ROOT/tests/real_hardware/fl_demo_metrics.py" parse-telemetry \
        --server-log "$SERVER_RUN_DIR/wfb.log" \
        --clients-dir "$SERVER_RUN_DIR/clients" \
        --tcp-retrans-delta "$tcp_delta" \
        --output "$telemetry_file"

    local ul_summary_file="$SERVER_RUN_DIR/uplink_summary.json"
    python3 - "$telemetry_file" "$ul_summary_file" "$duration" "$total_bytes" "$s_tun_ip_clean:$HTTP_PORT" "$expected_sha" "$UPLINK_MCS" "$GRANT_DURATION_MS" "$GUARD_INTERVAL_MS" <<'PY'
import json, sys
t_path, out_path, dur, tot_b, s_addr, exp_sha, mcs, slot, guard = sys.argv[1:10]
with open(t_path, 'r', encoding='utf-8') as f:
    telem = json.load(f)
summary = {
    "server_address": s_addr,
    "total_bytes": int(tot_b),
    "duration_seconds": float(dur),
    "expected_sha256": exp_sha,
    "mcs": int(mcs),
    "slot_duration_ms": int(slot),
    "guard_interval_ms": int(guard),
    "telemetry": telem,
}
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(summary, f, indent=2, ensure_ascii=False)
PY

    # 合并 clients 列表
    python3 - "$ul_summary_file" "${ul_clients_json[@]}" <<'PY'
import json, sys
out_path = sys.argv[1]
clients = [json.loads(c) for c in sys.argv[2:]]
with open(out_path, 'r', encoding='utf-8') as f:
    data = json.load(f)
data["clients"] = clients
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
PY

    echo ""
    if ! python3 "$PROJECT_ROOT/tests/real_hardware/fl_demo_metrics.py" render-uplink --json "$ul_summary_file"; then
        log_fail "上行传输校验失败，存在未通过节点！"
        exit 1
    fi
}

# 退出清理陷阱
cleanup_on_exit() {
    local exit_code=$?
    if [ "$COMMAND" = "clean" ]; then
        return 0
    fi
    if [ "$exit_code" -ne 0 ]; then
        log_warn "演示执行异常中断 (退出码 $exit_code)，正在触发紧急清理..."
    fi
    cleanup_processes
    exit "$exit_code"
}

# 主执行流
main() {
    parse_args "$@"

    trap cleanup_on_exit EXIT INT TERM

    step1_load_config
    step2_probe_clients

    if [ "$COMMAND" = "clean" ]; then
        cleanup_processes
        log_pass "集群残留进程与 TUN 接口已彻底清理！"
        exit 0
    fi

    cleanup_processes
    step3_configure_radio_interfaces
    step4_start_wfb_mesh

    case "$COMMAND" in
        downlink)
            run_downlink
            ;;
        uplink)
            run_uplink
            ;;
        all)
            run_downlink
            echo ""
            run_uplink
            ;;
        *)
            log_fail "未支持的命令: $COMMAND"
            usage
            exit 1
            ;;
    esac

    log_step "7" "WFB-FL 现场演示全流程执行完毕"
}

main "$@"
