#!/bin/bash
# scripts/deploy_cluster.sh
# WFB-FL ARMv8 集群一键批量部署与环境就绪验证编排器

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# 加载集群配置文件解析器（复用颜色定义、日志函数与配置解析器）
# shellcheck source=scripts/cluster_config.sh
source "$SCRIPT_DIR/cluster_config.sh"

log_step() { echo ""; echo "${C_BOLD}${C_CYAN}=== 步骤 $1: $2 ===${C_RESET}"; }

# 参数与默认配置
CONFIG_FILE="${CONFIG_FILE:-$PROJECT_ROOT/cluster_nodes.conf}"
REMOTE_DIR="${REMOTE_DIR:-projects/wfb-ng-fl}"
LOG_DIR="${LOG_DIR:-$PROJECT_ROOT/logs/deploy}"
SSH_PORT="22"
PARALLEL_MODE=1
SKIP_SYNC=0
CHECK_ONLY=0
CHECK_SERVER=0
DRY_RUN=0
SKIP_DRIVER=0
SKIP_APT=0

# SSH 统一选项
SSH_COMMON_OPTS=(
    -o "StrictHostKeyChecking=accept-new"
    -o "ConnectTimeout=3"
    -p "$SSH_PORT"
)
SSH_BATCH_OPTS=(
    "${SSH_COMMON_OPTS[@]}"
    -o "BatchMode=yes"
)

usage() {
    cat <<EOF
${C_BOLD}WFB-FL ARMv8 集群一键批量部署与环境就绪验证编排器${C_RESET}

用法:
  bash scripts/deploy_cluster.sh [选项]

选项:
  -c, --config <路径>       指定集群配置文件 (默认: cluster_nodes.conf)
  -r, --remote-dir <路径>   指定 Client 远端部署目录 (默认: projects/wfb-ng-fl)
  -l, --log-dir <路径>      指定部署执行日志输出目录 (默认: logs/deploy)
  -j, --parallel            并行并发同步与部署所有在线 Client (默认开启)
  -s, --serial              串行依次同步与部署各 Client
      --skip-sync           跳过代码库与 UFTP 源码包同步，直接执行远程部署
      --skip-driver         跳过 RTL8812AU 内核网卡驱动编译（透传给 deploy_node.sh）
      --skip-apt            跳过 APT 依赖安装（透传给 deploy_node.sh）
      --check-only          仅执行全集群就绪核验并输出状态报告，不触发安装
      --check-server        将 Server 本机纳入就绪强制阻断门禁
  -n, --dry-run             演练模式，仅打印将要执行的操作
  -h, --help                显示本帮助信息并退出

说明:
  本工具在 Server 端运行，读取 cluster_nodes.conf 中的 Client 节点列表；
  自动探测各 Client 在线状态，遇离线节点给出明显黄色告警并安全跳过；
  向在线 Client 批量分发当前代码库与 UFTP 源码包，远程触发 deploy_node.sh 执行并捕获日志；
  最后执行全集群安装后就绪核验，并在终端以表格形式输出软硬件环境就绪报告。
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
            -c|--config)
                CONFIG_FILE="$2"
                shift 2
                ;;
            -r|--remote-dir)
                REMOTE_DIR="$2"
                shift 2
                ;;
            -l|--log-dir)
                LOG_DIR="$2"
                shift 2
                ;;
            -j|--parallel)
                PARALLEL_MODE=1
                shift
                ;;
            -s|--serial)
                PARALLEL_MODE=0
                shift
                ;;
            --skip-sync)
                SKIP_SYNC=1
                shift
                ;;
            --skip-driver)
                SKIP_DRIVER=1
                shift
                ;;
            --skip-apt)
                SKIP_APT=1
                shift
                ;;
            --check-only)
                CHECK_ONLY=1
                shift
                ;;
            --check-server)
                CHECK_SERVER=1
                shift
                ;;
            -n|--dry-run)
                DRY_RUN=1
                shift
                ;;
            *)
                log_fail "未知参数: $1"
                usage >&2
                exit 1
                ;;
        esac
    done

    # 规范化配置文件路径
    if [ ! -f "$CONFIG_FILE" ]; then
        if [ -f "$PROJECT_ROOT/$CONFIG_FILE" ]; then
            CONFIG_FILE="$PROJECT_ROOT/$CONFIG_FILE"
        fi
    fi
}

# 查找本地 UFTP 源码压缩包
resolve_local_uftp_zip() {
    if [ -n "${UFTP_SRC_ZIP:-}" ] && [ -f "$UFTP_SRC_ZIP" ]; then
        echo "$UFTP_SRC_ZIP"
        return 0
    fi
    for cand in \
        "$PROJECT_ROOT/../uftp_src-5.0.3.zip" \
        "$PROJECT_ROOT/uftp_src-5.0.3.zip" \
        "$HOME/uftp_src-5.0.3.zip"; do
        if [ -f "$cand" ]; then
            echo "$cand"
            return 0
        fi
    done
    return 1
}

# 2. 读取并校验集群配置
step1_load_config() {
    log_step "1" "读取并解析集群配置文件: $CONFIG_FILE"
    load_cluster_config "$CONFIG_FILE"
    log_pass "配置解析完成，登记 Client 节点共 $CLIENT_COUNT 台 (信道: $WIRELESS_CHANNEL, 功率: ${WIRELESS_TXPOWER_DBM}dBm)"
}

# 3. 探测 Client 在线状态（遇到离线节点给出黄色告警并安全跳过）
declare -a NODE_ONLINE=()
declare -a ONLINE_INDICES=()
declare -a OFFLINE_INDICES=()

step2_probe_clients() {
    log_step "2" "探测 Client 节点在线与 SSH 连通状态"

    NODE_ONLINE=()
    ONLINE_INDICES=()
    OFFLINE_INDICES=()

    for i in "${!CLIENT_ROLES[@]}"; do
        local r="${CLIENT_ROLES[$i]}"
        local h="${CLIENT_HOSTS[$i]}"
        local u="${CLIENT_USERS[$i]}"

        if [ "$DRY_RUN" -eq 1 ]; then
            log_info "  [DRY-RUN] 模拟探测 $r ($h) 在线状态..."
            NODE_ONLINE[$i]=1
            ONLINE_INDICES+=("$i")
            continue
        fi

        # 尝试快速 SSH 探活
        if ssh "${SSH_BATCH_OPTS[@]}" -o ConnectTimeout=2 "${u}@${h}" "true" >/dev/null 2>&1; then
            NODE_ONLINE[$i]=1
            ONLINE_INDICES+=("$i")
            log_pass "  [ONLINE] 节点 $r ($h) 在线且 SSH 免密正常就绪"
        else
            NODE_ONLINE[$i]=0
            OFFLINE_INDICES+=("$i")
            log_warn "节点 $r ($h) 离线或不可达，已安全跳过该节点"
        fi
    done

    local total="${#CLIENT_ROLES[@]}"
    local online="${#ONLINE_INDICES[@]}"
    local offline="${#OFFLINE_INDICES[@]}"

    if [ "$online" -eq 0 ] && [ "$DRY_RUN" -eq 0 ]; then
        log_fail "所有 Client 节点均离线或不可达 (共 $total 台)，无法继续执行集群部署！"
        exit 1
    fi

    log_info "节点在线探测汇总: 共 $total 台，在线 $online 台，离线 $offline 台"
}

# 单节点代码与 UFTP 资源同步操作
sync_single_node() {
    local r="$1"
    local h="$2"
    local u="$3"
    local local_uftp="$4"

    # 确保远端父目录与目标目录存在
    ssh "${SSH_BATCH_OPTS[@]}" "${u}@${h}" "mkdir -p '$REMOTE_DIR' \"\$(dirname '$REMOTE_DIR')\""

    # 使用 rsync 增量同步，排除非必要文件与编译缓存
    rsync -az --delete \
        --exclude='.git/' \
        --exclude='*.o' \
        --exclude='*.so' \
        --exclude='*.a' \
        --exclude='wfb_v6_uplink' \
        --exclude='__pycache__/' \
        --exclude='*.pyc' \
        --exclude='logs/' \
        -e "ssh ${SSH_BATCH_OPTS[*]}" \
        "$PROJECT_ROOT/" "${u}@${h}:${REMOTE_DIR}/"

    # deploy_node.sh 优先从上级目录查找 uftp_src-5.0.3.zip
    if [ -n "$local_uftp" ]; then
        rsync -az -e "ssh ${SSH_BATCH_OPTS[*]}" \
            "$local_uftp" "${u}@${h}:\$(dirname '$REMOTE_DIR')/"
    fi
}

# 4. 向各在线 Client 分发当前代码库与 UFTP 源码包
step3_sync_code_and_uftp() {
    if [ "$CHECK_ONLY" -eq 1 ]; then
        log_info "已配置 --check-only，跳过代码同步步骤"
        return 0
    fi
    if [ "$SKIP_SYNC" -eq 1 ]; then
        log_info "已配置 --skip-sync，跳过代码同步步骤"
        return 0
    fi

    log_step "3" "向所有在线 Client 分发代码库与 UFTP 源码包"

    if [ "$DRY_RUN" -eq 0 ] && ! command -v rsync >/dev/null 2>&1; then
        log_fail "Server 本机未安装 rsync 命令，请先安装: sudo apt-get install -y rsync"
        return 1
    fi

    local local_uftp=""
    if local_uftp="$(resolve_local_uftp_zip)"; then
        log_info "找到本地 UFTP 源码包: $local_uftp"
    else
        log_warn "未在本地找到 uftp_src-5.0.3.zip（如远端尚未安装 UFTP，部署步骤可能受影响）"
    fi

    if [ "$DRY_RUN" -eq 1 ]; then
        for i in "${ONLINE_INDICES[@]}"; do
            local r="${CLIENT_ROLES[$i]}"
            local h="${CLIENT_HOSTS[$i]}"
            local u="${CLIENT_USERS[$i]}"
            echo "  [DRY-RUN] 创建远端目录: ssh $u@$h \"mkdir -p '$REMOTE_DIR'\""
            echo "  [DRY-RUN] 同步项目代码至: $u@$h:$REMOTE_DIR/"
            if [ -n "$local_uftp" ]; then
                echo "  [DRY-RUN] 推送 UFTP 源码包至: $u@$h:$(dirname "$REMOTE_DIR")/"
            fi
        done
        return 0
    fi

    if [ "$PARALLEL_MODE" -eq 1 ]; then
        log_info "以并行模式向 ${#ONLINE_INDICES[@]} 个在线节点分发代码库与资源..."
        local pids=()
        for i in "${ONLINE_INDICES[@]}"; do
            local r="${CLIENT_ROLES[$i]}"
            local h="${CLIENT_HOSTS[$i]}"
            local u="${CLIENT_USERS[$i]}"
            log_info "  [$r] 启动代码同步至 $h..."
            sync_single_node "$r" "$h" "$u" "$local_uftp" &
            pids[$i]=$!
        done

        local sync_failed=0
        for i in "${ONLINE_INDICES[@]}"; do
            local r="${CLIENT_ROLES[$i]}"
            local pid="${pids[$i]}"
            wait "$pid" && rc=0 || rc=$?
            if [ "$rc" -eq 0 ]; then
                log_pass "  [PASS] $r 代码与资源同步完成"
            else
                log_fail "  [FAIL] $r 代码同步失败 (退出码: $rc)"
                sync_failed=$((sync_failed + 1))
            fi
        done

        if [ "$sync_failed" -gt 0 ]; then
            log_fail "存在 $sync_failed 台节点代码同步失败，终止部署流程"
            return 1
        fi
    else
        log_info "以串行模式向在线节点逐一分发代码库与资源..."
        for i in "${ONLINE_INDICES[@]}"; do
            local r="${CLIENT_ROLES[$i]}"
            local h="${CLIENT_HOSTS[$i]}"
            local u="${CLIENT_USERS[$i]}"
            log_info "  [$r] 正在同步代码库至 $h..."
            if sync_single_node "$r" "$h" "$u" "$local_uftp"; then
                log_pass "  [PASS] $r 代码与资源同步完成"
            else
                log_fail "  [FAIL] $r 代码同步失败"
                return 1
            fi
        done
    fi
}

# 单节点远程部署命令执行
deploy_single_node() {
    local r="$1"
    local h="$2"
    local u="$3"
    local log_file="$4"
    local deploy_args_str="$5"

    (
        echo "=========================================================="
        echo " WFB-FL 远程部署日志 - 节点: $r ($h) 用户: $u"
        echo " 开始时间: $(date '+%Y-%m-%d %H:%M:%S')"
        echo " 执行命令: cd '$REMOTE_DIR' && sudo bash ./scripts/deploy_node.sh $deploy_args_str"
        echo "=========================================================="
        ssh "${SSH_BATCH_OPTS[@]}" "${u}@${h}" \
            "cd '$REMOTE_DIR' && sudo bash ./scripts/deploy_node.sh $deploy_args_str"
    ) > "$log_file" 2>&1
}

# 5. 远程触发各 Client 执行 deploy_node.sh 并捕获日志
declare -a NODE_DEPLOY_STATUS=()

step4_remote_deploy() {
    if [ "$CHECK_ONLY" -eq 1 ]; then
        log_info "已配置 --check-only，跳过远程安装步骤"
        for i in "${!CLIENT_ROLES[@]}"; do
            NODE_DEPLOY_STATUS[$i]="SKIPPED"
        done
        return 0
    fi

    log_step "4" "远程触发各 Client 执行 deploy_node.sh 自动化部署"

    mkdir -p "$LOG_DIR"

    # 构建透传参数
    local deploy_args=()
    if [ "$SKIP_DRIVER" -eq 1 ]; then
        deploy_args+=(--skip-driver)
    fi
    if [ "$SKIP_APT" -eq 1 ]; then
        deploy_args+=(--skip-apt)
    fi
    if [ "$DRY_RUN" -eq 1 ]; then
        deploy_args+=(--dry-run)
    fi
    local deploy_args_str="${deploy_args[*]:-}"

    for i in "${!CLIENT_ROLES[@]}"; do
        NODE_DEPLOY_STATUS[$i]="SKIPPED"
    done

    if [ "$DRY_RUN" -eq 1 ]; then
        for i in "${ONLINE_INDICES[@]}"; do
            local r="${CLIENT_ROLES[$i]}"
            local h="${CLIENT_HOSTS[$i]}"
            local u="${CLIENT_USERS[$i]}"
            echo "  [DRY-RUN] ssh $u@$h \"cd '$REMOTE_DIR' && sudo ./scripts/deploy_node.sh $deploy_args_str\""
            NODE_DEPLOY_STATUS[$i]="SUCCESS"
        done
        return 0
    fi

    if [ "$PARALLEL_MODE" -eq 1 ]; then
        log_info "以并行模式同时启动 ${#ONLINE_INDICES[@]} 个在线节点的远程部署..."
        local pids=()
        local log_files=()

        for i in "${ONLINE_INDICES[@]}"; do
            local r="${CLIENT_ROLES[$i]}"
            local h="${CLIENT_HOSTS[$i]}"
            local u="${CLIENT_USERS[$i]}"
            local log_file="$LOG_DIR/${r}_${h}.log"
            log_files[$i]="$log_file"

            log_info "  [$r] 启动部署，日志记录至: $log_file"
            deploy_single_node "$r" "$h" "$u" "$log_file" "$deploy_args_str" &
            pids[$i]=$!
        done

        # 等待所有并行任务完成并收集退出状态
        for i in "${ONLINE_INDICES[@]}"; do
            local r="${CLIENT_ROLES[$i]}"
            local pid="${pids[$i]}"
            local log_file="${log_files[$i]}"

            wait "$pid" && rc=0 || rc=$?
            if [ "$rc" -eq 0 ]; then
                NODE_DEPLOY_STATUS[$i]="SUCCESS"
                log_pass "  [PASS] 节点 $r 部署成功"
            else
                NODE_DEPLOY_STATUS[$i]="FAILED"
                log_fail "  [FAIL] 节点 $r 部署失败 (退出码: $rc，日志: $log_file)"
            fi
        done
    else
        log_info "以串行模式逐一执行在线节点的远程部署..."
        for i in "${ONLINE_INDICES[@]}"; do
            local r="${CLIENT_ROLES[$i]}"
            local h="${CLIENT_HOSTS[$i]}"
            local u="${CLIENT_USERS[$i]}"
            local log_file="$LOG_DIR/${r}_${h}.log"

            log_info "  [$r] 开始部署，日志记录至: $log_file"
            deploy_single_node "$r" "$h" "$u" "$log_file" "$deploy_args_str" && rc=0 || rc=$?

            if [ "$rc" -eq 0 ]; then
                NODE_DEPLOY_STATUS[$i]="SUCCESS"
                log_pass "  [PASS] 节点 $r 部署成功"
            else
                NODE_DEPLOY_STATUS[$i]="FAILED"
                log_fail "  [FAIL] 节点 $r 部署失败 (退出码: $rc，日志: $log_file)"
            fi
        done
    fi
}

# 共享就绪检测脚本（Server 与 Client 执行完全相同的检测逻辑）
VERIFY_SCRIPT='
missing_cmds=0
for cmd in wfb-fl-server wfb-fl-client wfb_v6_uplink uftp uftpd; do
    if ! command -v "$cmd" >/dev/null 2>&1 && [ ! -x "/usr/bin/$cmd" ] && [ ! -x "/usr/local/bin/$cmd" ]; then
        missing_cmds=$((missing_cmds + 1))
    fi
done

driver_status="NOT_LOADED"
if lsmod 2>/dev/null | grep -qE "8812au|88XXau|rtl88xxau_wfb"; then
    driver_status="LOADED"
fi

nic_status="NONE"
if command -v iw >/dev/null 2>&1; then
    wlx_list=($(iw dev 2>/dev/null | awk "/Interface / {print \$2}" | grep "^wlx" || true))
    if [ "${#wlx_list[@]}" -eq 1 ]; then
        nic_status="${wlx_list[0]}"
    elif [ "${#wlx_list[@]}" -gt 1 ]; then
        nic_status="CONFLICT"
    else
        nic_status="NONE"
    fi
fi

echo "VERIFY_RESULT: cmds_missing=$missing_cmds driver=$driver_status nic=$nic_status"
'

# 解析并判定核验输出
# 输出格式使用竖线分隔: cmds_str|driver_str|nic_str|overall_str
parse_and_judge_readiness() {
    local raw_output="$1"
    local prior_deploy_status="$2"

    if [[ "$raw_output" =~ VERIFY_RESULT:[[:space:]]*cmds_missing=([0-9]+)[[:space:]]*driver=([A-Za-z0-9_]+)[[:space:]]*nic=([A-Za-z0-9_]+) ]]; then
        local missing="${BASH_REMATCH[1]}"
        local driver="${BASH_REMATCH[2]}"
        local nic="${BASH_REMATCH[3]}"

        local cmds_display="5/5 OK"
        if [ "$missing" -gt 0 ]; then
            cmds_display="${missing} 缺失"
        fi

        local overall=""
        if [ "$prior_deploy_status" = "FAILED" ]; then
            overall="DEPLOY_FAIL"
        elif [ "$missing" -gt 0 ]; then
            overall="CMD_MISSING"
        elif [ "$driver" != "LOADED" ]; then
            overall="NO_DRIVER"
        elif [ "$nic" = "NONE" ]; then
            overall="NO_NIC"
        elif [ "$nic" = "CONFLICT" ]; then
            overall="NIC_CONFLICT"
        else
            overall="READY"
        fi

        echo "${cmds_display}|${driver}|${nic}|${overall}"
    else
        echo "CHECK_FAIL|UNKNOWN|UNKNOWN|CHECK_FAIL"
    fi
}

# 6. 执行全集群安装后就绪核验
declare -a NODE_CMDS=()
declare -a NODE_DRIVER=()
declare -a NODE_NIC=()
declare -a NODE_OVERALL=()

SERVER_CMDS=""
SERVER_DRIVER=""
SERVER_NIC=""
SERVER_OVERALL=""

step5_verify_readiness() {
    log_step "5" "执行全集群安装后环境与网卡就绪核验"

    NODE_CMDS=()
    NODE_DRIVER=()
    NODE_NIC=()
    NODE_OVERALL=()

    # 1. 核验 Server 本机
    log_info "核验 Server 端本机软硬件环境..."
    if [ "$DRY_RUN" -eq 1 ]; then
        SERVER_CMDS="5/5 OK"
        SERVER_DRIVER="LOADED"
        SERVER_NIC="wlx0013ef123450"
        SERVER_OVERALL="READY"
    else
        local s_raw
        s_raw="$(bash -c "$VERIFY_SCRIPT" 2>/dev/null || true)"
        IFS='|' read -r SERVER_CMDS SERVER_DRIVER SERVER_NIC SERVER_OVERALL <<< "$(parse_and_judge_readiness "$s_raw" "SUCCESS")"
    fi

    # 2. 核验各 Client 节点
    for i in "${!CLIENT_ROLES[@]}"; do
        local r="${CLIENT_ROLES[$i]}"
        local h="${CLIENT_HOSTS[$i]}"
        local u="${CLIENT_USERS[$i]}"

        # 离线节点直接标记
        if [ "${NODE_ONLINE[$i]:-0}" -eq 0 ]; then
            NODE_CMDS[$i]="-"
            NODE_DRIVER[$i]="-"
            NODE_NIC[$i]="-"
            NODE_OVERALL[$i]="OFFLINE"
            continue
        fi

        if [ "$DRY_RUN" -eq 1 ]; then
            NODE_CMDS[$i]="5/5 OK"
            NODE_DRIVER[$i]="LOADED"
            NODE_NIC[$i]="wlx0013ef12345$i"
            NODE_OVERALL[$i]="READY"
            continue
        fi

        log_info "正在核验 $r ($h) 依赖与网卡状态..."
        local verify_raw
        verify_raw="$(ssh "${SSH_BATCH_OPTS[@]}" "${u}@${h}" "bash -s" <<< "$VERIFY_SCRIPT" 2>/dev/null || true)"

        local c_cmds c_driver c_nic c_overall
        IFS='|' read -r c_cmds c_driver c_nic c_overall <<< "$(parse_and_judge_readiness "$verify_raw" "${NODE_DEPLOY_STATUS[$i]:-SKIPPED}")"

        NODE_CMDS[$i]="$c_cmds"
        NODE_DRIVER[$i]="$c_driver"
        NODE_NIC[$i]="$c_nic"
        NODE_OVERALL[$i]="$c_overall"
    done
}

# 7. 终端表格输出与最终报告
step6_report_summary() {
    echo ""
    echo "${C_BOLD}${C_CYAN}========================================================================================================${C_RESET}"
    echo "${C_BOLD}${C_CYAN}                                 WFB-FL 集群部署与环境就绪报告                                          ${C_RESET}"
    echo "${C_BOLD}${C_CYAN}========================================================================================================${C_RESET}"
    printf "  %-9s %-16s %-9s %-10s %-11s %-11s %-18s %-12s\n" \
        "ROLE" "HOST_IP" "ONLINE" "DEPLOY" "COMMANDS" "DRIVER" "WIRELESS_NIC" "OVERALL"
    printf "  %-9s %-16s %-9s %-10s %-11s %-11s %-18s %-12s\n" \
        "---------" "----------------" "---------" "----------" "-----------" "-----------" "------------------" "------------"

    # 输出 Server 行
    local s_ov_color="${C_GREEN}"
    if [ "$SERVER_OVERALL" = "NO_NIC" ]; then
        s_ov_color="${C_YELLOW}"
    elif [ "$SERVER_OVERALL" != "READY" ]; then
        s_ov_color="${C_RED}"
    fi
    printf "  %-9s %-16s %-9s %-10s %-11s %-11s %-18s ${s_ov_color}%-12s${C_RESET}\n" \
        "server" "127.0.0.1" "ONLINE" "LOCAL" "$SERVER_CMDS" "$SERVER_DRIVER" "$SERVER_NIC" "$SERVER_OVERALL"

    # 输出各 Client 行
    local total_clients="${#CLIENT_ROLES[@]}"
    local online_clients=0
    local deploy_success=0
    local deploy_failed=0
    local fully_ready=0
    local hard_failures=0

    # 检查 Server 是否存在严重就绪失败 (若开启 --check-server 则纳入硬性阻断)
    if [ "$CHECK_SERVER" -eq 1 ]; then
        if [ "$SERVER_OVERALL" = "CMD_MISSING" ] || [ "$SERVER_OVERALL" = "NO_DRIVER" ] || [ "$SERVER_OVERALL" = "NIC_CONFLICT" ] || [ "$SERVER_OVERALL" = "CHECK_FAIL" ]; then
            hard_failures=$((hard_failures + 1))
        fi
    fi

    for i in "${!CLIENT_ROLES[@]}"; do
        local r="${CLIENT_ROLES[$i]}"
        local h="${CLIENT_HOSTS[$i]}"
        local on_st="OFFLINE"
        local on_color="${C_YELLOW}"
        if [ "${NODE_ONLINE[$i]:-0}" -eq 1 ]; then
            on_st="ONLINE"
            on_color="${C_GREEN}"
            online_clients=$((online_clients + 1))
        fi

        local dep_st="${NODE_DEPLOY_STATUS[$i]:-SKIPPED}"
        local dep_color="${C_RESET}"
        if [ "$dep_st" = "SUCCESS" ]; then
            dep_color="${C_GREEN}"
            deploy_success=$((deploy_success + 1))
        elif [ "$dep_st" = "FAILED" ]; then
            dep_color="${C_RED}"
            deploy_failed=$((deploy_failed + 1))
        fi

        local ov_st="${NODE_OVERALL[$i]}"
        local ov_color="${C_RESET}"
        if [ "$ov_st" = "READY" ]; then
            ov_color="${C_GREEN}"
            fully_ready=$((fully_ready + 1))
        elif [ "$ov_st" = "NO_NIC" ]; then
            ov_color="${C_YELLOW}"
        elif [ "$ov_st" = "OFFLINE" ]; then
            ov_color="${C_YELLOW}"
        else
            ov_color="${C_RED}"
            hard_failures=$((hard_failures + 1))
        fi

        printf "  %-9s %-16s ${on_color}%-9s${C_RESET} ${dep_color}%-10s${C_RESET} %-11s %-11s %-18s ${ov_color}%-12s${C_RESET}\n" \
            "$r" "$h" "$on_st" "$dep_st" "${NODE_CMDS[$i]}" "${NODE_DRIVER[$i]}" "${NODE_NIC[$i]}" "$ov_st"
    done

    echo "${C_BOLD}${C_CYAN}========================================================================================================${C_RESET}"
    echo "  汇总: 登记 Client $total_clients 台 | 在线 $online_clients 台 | 部署成功 $deploy_success 台 | 失败 $deploy_failed 台 | 完全就绪 $fully_ready 台"
    echo ""

    # 判定最终返回状态
    if [ "$deploy_failed" -gt 0 ]; then
        log_fail "存在 $deploy_failed 台节点部署脚本执行失败，详情见 $LOG_DIR/ 下的日志文件！"
        return 1
    fi

    if [ "$hard_failures" -gt 0 ]; then
        log_fail "存在 $hard_failures 项客户端硬性就绪故障（依赖缺失、驱动未加载或网卡冲突），请检查上方报表！"
        return 1
    fi

    if [ "$CHECK_SERVER" -eq 1 ] && [ "$SERVER_OVERALL" != "READY" ]; then
        log_fail "Server 端处于未完全就绪状态 ($SERVER_OVERALL)，请先在 Server 端执行 deploy_node.sh！"
        return 1
    fi

    if [ "$fully_ready" -eq "$online_clients" ] && [ "$online_clients" -gt 0 ]; then
        log_pass "=========================================================="
        log_pass " 恭喜！全集群在线 Client 节点已全部就绪，具备无线演示条件！"
        log_pass "=========================================================="
        return 0
    else
        log_info "部署任务已执行完毕。部分节点未插入 USB 无线网卡 (NO_NIC)，网卡插入后将自动就绪。"
        return 0
    fi
}

main() {
    parse_args "$@"
    step1_load_config
    step2_probe_clients
    step3_sync_code_and_uftp
    step4_remote_deploy
    step5_verify_readiness
    step6_report_summary
}

main "$@"
