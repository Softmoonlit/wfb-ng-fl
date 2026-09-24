#!/bin/bash
# scripts/cluster_config.sh
# WFB-FL ARMv8 多板载集群配置文件解析与严格校验器

set -euo pipefail

# 彩色输出定义
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

log_info()  { echo "${C_BLUE}[INFO]${C_RESET} $(date '+%H:%M:%S') $1"; }
log_pass()  { echo "${C_GREEN}[PASS]${C_RESET} $(date '+%H:%M:%S') $1"; }
log_warn()  { echo "${C_YELLOW}[WARN]${C_RESET} $(date '+%H:%M:%S') $1" >&2; }
log_fail()  { echo "${C_RED}[FAIL]${C_RESET} $(date '+%H:%M:%S') $1" >&2; }

# 全局变量定义
WIRELESS_CHANNEL=""
WIRELESS_CHANNEL_WIDTH=""
WIRELESS_TXPOWER_DBM=""
DOWNLINK_MCS=""
UPLINK_MCS=""
UFTP_RATE_KBPS=""
SERVER_NODE_ID=""
SERVER_TUN_IP=""
SERVER_HOST=""
SERVER_USER=""

# 客户端数组
CLIENT_ROLES=()
CLIENT_HOSTS=()
CLIENT_USERS=()
CLIENT_NODE_IDS=()
CLIENT_TUN_IPS=()
CLIENT_COUNT=0

# 重置解析状态
reset_cluster_config() {
    WIRELESS_CHANNEL=""
    WIRELESS_CHANNEL_WIDTH=""
    WIRELESS_TXPOWER_DBM=""
    DOWNLINK_MCS=""
    UPLINK_MCS=""
    UFTP_RATE_KBPS=""
    SERVER_NODE_ID=""
    SERVER_TUN_IP=""
    SERVER_HOST=""
    SERVER_USER=""

    CLIENT_ROLES=()
    CLIENT_HOSTS=()
    CLIENT_USERS=()
    CLIENT_NODE_IDS=()
    CLIENT_TUN_IPS=()
    CLIENT_COUNT=0
}

# 辅助函数: 检查 IPv4 地址格式与数值合法性 (0-255)
validate_ipv4() {
    local ip="$1"
    if ! [[ "$ip" =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]]; then
        return 1
    fi
    local o1="${BASH_REMATCH[1]}"
    local o2="${BASH_REMATCH[2]}"
    local o3="${BASH_REMATCH[3]}"
    local o4="${BASH_REMATCH[4]}"
    if [ "$o1" -gt 255 ] || [ "$o2" -gt 255 ] || [ "$o3" -gt 255 ] || [ "$o4" -gt 255 ]; then
        return 1
    fi
    return 0
}

# 辅助函数: 严格检查 Linux 用户名格式 (POSIX 标准用户名，防命令注入)
validate_username() {
    local user="$1"
    if [[ "$user" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]]; then
        return 0
    fi
    return 1
}

# 加载并严格解析配置文件
# 用法: load_cluster_config <config_file_path>
load_cluster_config() {
    local conf_file="$1"
    if [ ! -f "$conf_file" ]; then
        log_fail "配置文件不存在或不是普通文件: $conf_file"
        return 1
    fi

    reset_cluster_config

    local in_clients_block=0
    local raw_client_lines=()
    local line_num=0

    while IFS= read -r line || [ -n "$line" ]; do
        line_num=$((line_num + 1))
        # 去除行首尾空白
        line="$(echo "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"

        # 忽略空行或纯注释行
        if [ -z "$line" ] || [[ "$line" =~ ^# ]]; then
            continue
        fi

        # 处理 CLIENTS 数组块
        if [ "$in_clients_block" -eq 1 ]; then
            if [[ "$line" =~ ^\) ]]; then
                in_clients_block=0
                continue
            fi
            # 提取引号内的内容
            local item="$line"
            item="${item#\"}"
            item="${item%\"}"
            item="${item#\'}"
            item="${item%\'}"
            item="$(echo "$item" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
            if [ -n "$item" ] && ! [[ "$item" =~ ^# ]]; then
                raw_client_lines+=("$item")
            fi
            continue
        fi

        # 处理 CLIENTS=( 开头
        if [[ "$line" =~ ^CLIENTS=\( ]]; then
            in_clients_block=1
            local rem="${line#CLIENTS=(}"
            rem="$(echo "$rem" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
            if [[ "$rem" =~ \)$ ]]; then
                in_clients_block=0
                rem="${rem%\)}"
                rem="${rem#\"}"
                rem="${rem%\"}"
                rem="${rem#\'}"
                rem="${rem%\'}"
                rem="$(echo "$rem" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
                if [ -n "$rem" ]; then
                    raw_client_lines+=("$rem")
                fi
            fi
            continue
        fi

        # 处理键值对赋值 (例如 WIRELESS_CHANNEL=157)
        if [[ "$line" =~ ^([A-Za-z0-9_]+)=(.*)$ ]]; then
            local key="${BASH_REMATCH[1]}"
            local val="${BASH_REMATCH[2]}"
            val="$(echo "$val" | sed -e 's/[[:space:]]*#.*$//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
            val="${val#\"}"
            val="${val%\"}"
            val="${val#\'}"
            val="${val%\'}"

            case "$key" in
                WIRELESS_CHANNEL)       WIRELESS_CHANNEL="$val" ;;
                WIRELESS_CHANNEL_WIDTH) WIRELESS_CHANNEL_WIDTH="$val" ;;
                WIRELESS_TXPOWER_DBM)   WIRELESS_TXPOWER_DBM="$val" ;;
                DOWNLINK_MCS)           DOWNLINK_MCS="$val" ;;
                UPLINK_MCS)             UPLINK_MCS="$val" ;;
                UFTP_RATE_KBPS)         UFTP_RATE_KBPS="$val" ;;
                SERVER_NODE_ID)         SERVER_NODE_ID="$val" ;;
                SERVER_TUN_IP)          SERVER_TUN_IP="$val" ;;
                SERVER_HOST)            SERVER_HOST="$val" ;;
                SERVER_USER)            SERVER_USER="$val" ;;
                *)
                    log_fail "配置文件第 $line_num 行包含未知键: $key"
                    return 1
                    ;;
            esac
            continue
        fi

        # 无法识别的行，严格报错拒止（拒绝容错垫片）
        log_fail "配置文件第 $line_num 行存在无法识别的内容: $line"
        return 1
    done < "$conf_file"

    if [ "$in_clients_block" -eq 1 ]; then
        log_fail "配置文件中 CLIENTS=( 数组未正常闭合"
        return 1
    fi

    # 解析所有收集到的 Client 行
    for c_line in "${raw_client_lines[@]}"; do
        read -r c_role c_ip c_user c_nid c_tip extra <<< "$c_line"
        if [ -n "${extra:-}" ]; then
            log_fail "客户端配置行存在多余字段: $c_line"
            return 1
        fi
        if [ -z "${c_role:-}" ] || [ -z "${c_ip:-}" ] || [ -z "${c_user:-}" ] || [ -z "${c_nid:-}" ] || [ -z "${c_tip:-}" ]; then
            log_fail "客户端配置行字段不全 (格式需为: role host_ip user node_id tun_ip): $c_line"
            return 1
        fi
        CLIENT_ROLES+=("$c_role")
        CLIENT_HOSTS+=("$c_ip")
        CLIENT_USERS+=("$c_user")
        CLIENT_NODE_IDS+=("$c_nid")
        CLIENT_TUN_IPS+=("$c_tip")
    done
    CLIENT_COUNT="${#CLIENT_ROLES[@]}"

    # 执行严格校验
    validate_cluster_config
}

# 严格校验配置逻辑
validate_cluster_config() {
    # 0. 必填字段缺失检查
    local required_fields=(
        "WIRELESS_CHANNEL"
        "WIRELESS_CHANNEL_WIDTH"
        "WIRELESS_TXPOWER_DBM"
        "DOWNLINK_MCS"
        "UPLINK_MCS"
        "UFTP_RATE_KBPS"
        "SERVER_NODE_ID"
        "SERVER_TUN_IP"
    )
    for field in "${required_fields[@]}"; do
        if [ -z "${!field:-}" ]; then
            log_fail "配置文件缺失必填参数: $field"
            return 1
        fi
    done

    # 1. WIRELESS_CHANNEL 校验 (硬性拒绝 161；支持合法 5GHz 频段信道)
    if [ "$WIRELESS_CHANNEL" = "161" ]; then
        log_fail "信道 161 处于系统硬黑名单中！(RTL8812AU 驱动内核缺失 163 频点定义，配置 161 将触发 UBSAN 越界崩溃导致网卡瘫痪)"
        return 1
    fi
    case "$WIRELESS_CHANNEL" in
        36|40|44|48|52|56|60|64|100|104|108|112|116|120|124|128|132|136|140|144|149|153|157|165) ;;
        *)
            log_fail "无线信道 WIRELESS_CHANNEL ($WIRELESS_CHANNEL) 不合法，必须为合法的 5GHz Wi-Fi 信道 (如 149, 153, 157, 165 等，161 硬性屏蔽)"
            return 1
            ;;
    esac

    # 2. WIRELESS_CHANNEL_WIDTH 校验 (HT40+, HT40-, HT20)
    case "$WIRELESS_CHANNEL_WIDTH" in
        HT40+|HT40-|HT20) ;;
        *)
            log_fail "信道频宽 WIRELESS_CHANNEL_WIDTH ($WIRELESS_CHANNEL_WIDTH) 不合法，支持: HT40+, HT40-, HT20"
            return 1
            ;;
    esac

    # 信道与频宽组合合法性校验 (信道 165 为 20MHz 单载波，不支持 HT40)
    if [ "$WIRELESS_CHANNEL" = "165" ] && [ "$WIRELESS_CHANNEL_WIDTH" != "HT20" ]; then
        log_fail "信道 165 为 5GHz 上边界单频点，仅支持 20MHz 频宽 (HT20)，不能配置为 $WIRELESS_CHANNEL_WIDTH"
        return 1
    fi

    # 3. WIRELESS_TXPOWER_DBM 校验 (10 ~ 20 dBm)
    if ! [[ "$WIRELESS_TXPOWER_DBM" =~ ^[0-9]+$ ]] || [ "$WIRELESS_TXPOWER_DBM" -lt 10 ] || [ "$WIRELESS_TXPOWER_DBM" -gt 20 ]; then
        log_fail "发射功率 WIRELESS_TXPOWER_DBM ($WIRELESS_TXPOWER_DBM) 超出合法范围 [10, 20] dBm (默认推荐 12 dBm)"
        return 1
    fi

    # 4. DOWNLINK_MCS 校验 (3 ~ 6)
    if ! [[ "$DOWNLINK_MCS" =~ ^[0-9]+$ ]] || [ "$DOWNLINK_MCS" -lt 3 ] || [ "$DOWNLINK_MCS" -gt 6 ]; then
        log_fail "下行组播调制阶数 DOWNLINK_MCS ($DOWNLINK_MCS) 超出合法范围 [3, 6]"
        return 1
    fi

    # 5. UPLINK_MCS 校验 (3 ~ 6)
    if ! [[ "$UPLINK_MCS" =~ ^[0-9]+$ ]] || [ "$UPLINK_MCS" -lt 3 ] || [ "$UPLINK_MCS" -gt 6 ]; then
        log_fail "上行单播调制阶数 UPLINK_MCS ($UPLINK_MCS) 超出合法范围 [3, 6]"
        return 1
    fi

    # 6. UFTP_RATE_KBPS 根据 DOWNLINK_MCS 校验合理性
    local rate_min rate_max rec_rate
    case "$DOWNLINK_MCS" in
        3) rate_min=12000; rate_max=18000; rec_rate=15000 ;;
        4) rate_min=18000; rate_max=26000; rec_rate=22000 ;;
        5) rate_min=24000; rate_max=35000; rec_rate=28000 ;;
        6) rate_min=32000; rate_max=45000; rec_rate=38000 ;;
    esac

    if ! [[ "$UFTP_RATE_KBPS" =~ ^[0-9]+$ ]] || [ "$UFTP_RATE_KBPS" -lt "$rate_min" ] || [ "$UFTP_RATE_KBPS" -gt "$rate_max" ]; then
        log_fail "下行 UFTP 速率 UFTP_RATE_KBPS ($UFTP_RATE_KBPS) 超出 DOWNLINK_MCS=$DOWNLINK_MCS 的允许安全区间 [${rate_min}, ${rate_max}] Kbps (推荐值: ${rec_rate} Kbps)"
        return 1
    fi

    # 7. SERVER_NODE_ID 校验 (规格固定为 255)
    if [ "$SERVER_NODE_ID" != "255" ]; then
        log_fail "服务端节点 ID SERVER_NODE_ID ($SERVER_NODE_ID) 不合法，系统架构契约规定必须固定为 255"
        return 1
    fi

    # 8. SERVER_TUN_IP 校验 (规格固定为 10.80.0.1/24)
    if [ "$SERVER_TUN_IP" != "10.80.0.1/24" ]; then
        log_fail "服务端 TUN 地址 SERVER_TUN_IP ($SERVER_TUN_IP) 不合法，系统架构契约规定必须严格为 10.80.0.1/24"
        return 1
    fi

    # 9. 客户端列表校验 (3 ~ 7 台 Client)
    if [ "$CLIENT_COUNT" -lt 3 ] || [ "$CLIENT_COUNT" -gt 7 ]; then
        log_fail "集群 Client 节点数量必须在 3~7 台之间（当前配置了 $CLIENT_COUNT 台）"
        return 1
    fi

    local seen_roles=()
    local seen_node_ids=()
    local seen_tun_ips=()
    local srv_ip_clean="10.80.0.1"

    for i in "${!CLIENT_ROLES[@]}"; do
        local r="${CLIENT_ROLES[$i]}"
        local h="${CLIENT_HOSTS[$i]}"
        local u="${CLIENT_USERS[$i]}"
        local nid="${CLIENT_NODE_IDS[$i]}"
        local tip="${CLIENT_TUN_IPS[$i]}"
        local tip_clean="${tip%/*}"

        # role_name 格式与唯一性
        if [[ " ${seen_roles[*]} " =~ " ${r} " ]]; then
            log_fail "Client 角色标识 (role_name) 重复: $r"
            return 1
        fi
        seen_roles+=("$r")

        # host_ip 格式
        if ! validate_ipv4 "$h"; then
            log_fail "Client $r 的 host_ip ($h) 不是合法的 IPv4 地址"
            return 1
        fi

        # ssh_user 严格格式检查 (POSIX 用户名，防注入)
        if ! validate_username "$u"; then
            log_fail "Client $r 的 ssh_user ($u) 不是合法的 Linux 用户名格式"
            return 1
        fi

        # node_id 范围与唯一性
        if ! [[ "$nid" =~ ^[0-9]+$ ]] || [ "$nid" -le 0 ] || [ "$nid" -ge 255 ]; then
            log_fail "Client $r 的 node_id ($nid) 不合法，必须为 1~254 之间的整数"
            return 1
        fi
        if [[ " ${seen_node_ids[*]} " =~ " ${nid} " ]]; then
            log_fail "Client $r 的 node_id ($nid) 与其他 Client 重复"
            return 1
        fi
        seen_node_ids+=("$nid")

        # tun_ip 格式与唯一性
        if ! validate_ipv4 "$tip_clean"; then
            log_fail "Client $r 的 tun_ip ($tip) 不是合法的 IPv4 地址"
            return 1
        fi
        if [ "$tip_clean" = "$srv_ip_clean" ]; then
            log_fail "Client $r 的 tun_ip ($tip) 不能与服务端 TUN IP ($SERVER_TUN_IP) 冲突"
            return 1
        fi
        if [[ " ${seen_tun_ips[*]} " =~ " ${tip_clean} " ]]; then
            log_fail "Client $r 的 tun_ip ($tip) 与其他 Client 重复"
            return 1
        fi
        seen_tun_ips+=("$tip_clean")
    done

    return 0
}

# 格式化打印当前配置信息
print_cluster_config() {
    echo ""
    echo "${C_BOLD}${C_CYAN}=== WFB-FL 集群配置参数概要 ===${C_RESET}"
    echo "  ${C_BOLD}无线信道 (Channel):${C_RESET}       $WIRELESS_CHANNEL (频宽: $WIRELESS_CHANNEL_WIDTH)"
    echo "  ${C_BOLD}发射功率 (TxPower):${C_RESET}       $WIRELESS_TXPOWER_DBM dBm"
    echo "  ${C_BOLD}下行组播调制 (Downlink):${C_RESET}  MCS $DOWNLINK_MCS (UFTP 速率: $UFTP_RATE_KBPS Kbps)"
    echo "  ${C_BOLD}上行单播调制 (Uplink):${C_RESET}    MCS $UPLINK_MCS"
    echo "  ${C_BOLD}服务端节点 (Server):${C_RESET}      Node ID: $SERVER_NODE_ID, TUN IP: $SERVER_TUN_IP"
    echo ""
    echo "${C_BOLD}${C_CYAN}=== 客户端拓扑列表 (共 $CLIENT_COUNT 台) ===${C_RESET}"
    printf "  %-10s %-16s %-10s %-9s %-16s\n" "ROLE" "HOST_IP" "USER" "NODE_ID" "TUN_IP"
    printf "  %-10s %-16s %-10s %-9s %-16s\n" "----------" "----------------" "----------" "---------" "----------------"
    for i in "${!CLIENT_ROLES[@]}"; do
        printf "  %-10s %-16s %-10s %-9s %-16s\n" \
            "${CLIENT_ROLES[$i]}" "${CLIENT_HOSTS[$i]}" "${CLIENT_USERS[$i]}" "${CLIENT_NODE_IDS[$i]}" "${CLIENT_TUN_IPS[$i]}"
    done
    echo ""
}

usage() {
    cat <<EOF
${C_BOLD}WFB-FL 集群配置文件解析与严格校验工具${C_RESET}

用法:
  bash scripts/cluster_config.sh [配置文件路径]

选项:
  -h, --help        显示本帮助信息并退出

示例:
  bash scripts/cluster_config.sh cluster_nodes.conf
EOF
}

# 作为独立脚本运行时的入口处理
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    conf_path="cluster_nodes.conf"

    while [ $# -gt 0 ]; do
        case "$1" in
            -h|--help)
                usage
                exit 0
                ;;
            -*)
                echo "${C_RED}[FAIL] 未知选项: $1${C_RESET}" >&2
                usage >&2
                exit 1
                ;;
            *)
                conf_path="$1"
                shift
                ;;
        esac
    done

    if load_cluster_config "$conf_path"; then
        log_pass "配置文件校验通过: $conf_path"
        print_cluster_config
        exit 0
    else
        log_fail "配置文件校验失败: $conf_path"
        exit 1
    fi
fi
