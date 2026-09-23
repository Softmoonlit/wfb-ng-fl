#!/bin/bash
# scripts/setup_cluster_auth.sh
# WFB-FL ARMv8 集群全局 SSH 与 Sudo 免密互信一键分发工具

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# 加载集群配置文件解析器（复用颜色定义、日志函数与配置解析器）
# shellcheck source=scripts/cluster_config.sh
source "$SCRIPT_DIR/cluster_config.sh"

log_step() { echo ""; echo "${C_BOLD}${C_CYAN}=== 步骤 $1: $2 ===${C_RESET}"; }

# 参数与配置
CONFIG_FILE="${CONFIG_FILE:-$PROJECT_ROOT/cluster_nodes.conf}"
SSH_KEY_PATH="${SSH_KEY_PATH:-$HOME/.ssh/id_rsa}"
SSH_PORT="22"
SSH_PASSWORD="${SSH_PASSWORD:-}"
DRY_RUN="${DRY_RUN:-0}"
SKIP_NETWORK_CHECK="${SKIP_NETWORK_CHECK:-0}"

# 统一 SSH 连接参数
SSH_COMMON_OPTS=(
    -o "StrictHostKeyChecking=accept-new"
    -o "ConnectTimeout=3"
    -p "$SSH_PORT"
)
SSH_BATCH_OPTS=(
    "${SSH_COMMON_OPTS[@]}"
    -o "BatchMode=yes"
)

# 临时资源清理钩子
TEMP_ASKPASS=""
TEMP_PASSFILE=""
cleanup() {
    if [ -n "$TEMP_ASKPASS" ] && [ -f "$TEMP_ASKPASS" ]; then
        rm -f "$TEMP_ASKPASS"
    fi
    if [ -n "$TEMP_PASSFILE" ] && [ -f "$TEMP_PASSFILE" ]; then
        rm -f "$TEMP_PASSFILE"
    fi
}
trap cleanup EXIT INT TERM

usage() {
    cat <<EOF
${C_BOLD}WFB-FL ARMv8 集群全局 SSH 与 Sudo 免密互信分发工具${C_RESET}

用法:
  bash scripts/setup_cluster_auth.sh [选项]

选项:
  -c, --config <路径>   指定集群配置文件 (默认: cluster_nodes.conf)
  -p, --password <密码> 提供统一 Client SSH 登录与 Sudo 密码 (非交互自动化模式)
  -k, --key <私钥路径>  指定 Server 本机 SSH 私钥路径 (默认: ~/.ssh/id_rsa)
      --dry-run         演练模式，仅打印将要执行的操作
  -h, --help            显示本帮助信息并退出

说明:
  本工具在 Server 端运行，读取集群配置，自动确保 Server 本机具备 SSH 密钥对；
  在 Server 端提示输入一次密码（或通过 -p 传入），通过原生 SSH_ASKPASS 向所有 Client 分发公钥，
  并在 Client 端写入 /etc/sudoers.d/99-wfb-nopasswd，最后执行严格免密连通性验证。
EOF
}

# 1. 解析命令行参数
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
            -p|--password)
                SSH_PASSWORD="$2"
                shift 2
                ;;
            -k|--key)
                SSH_KEY_PATH="$2"
                shift 2
                ;;
            --dry-run)
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
}

# 2. 检查或生成 Server 本机 SSH 密钥对
ensure_ssh_key() {
    log_step "1" "检查 Server 本机 SSH 密钥对"
    local pub_key="${SSH_KEY_PATH}.pub"

    if [ -f "$pub_key" ] && [ -f "$SSH_KEY_PATH" ]; then
        log_pass "检测到现有完整 SSH 密钥对: $SSH_KEY_PATH ($pub_key)"
        return 0
    fi

    # 如果私钥存在但公钥缺失，直接从私钥导出公钥，防止覆盖或交互确认卡死
    if [ -f "$SSH_KEY_PATH" ] && [ ! -f "$pub_key" ]; then
        log_info "检测到私钥存在但公钥缺失，正在由私钥导出对应公钥..."
        if [ "$DRY_RUN" -eq 1 ]; then
            log_info "[DRY-RUN] 将执行: ssh-keygen -y -f '$SSH_KEY_PATH' > '$pub_key'"
            return 0
        fi
        ssh-keygen -y -f "$SSH_KEY_PATH" > "$pub_key"
        chmod 644 "$pub_key"
        log_pass "成功导出 SSH 公钥: $pub_key"
        return 0
    fi

    log_info "未检测到 SSH 密钥对，正在自动生成 4096 位 RSA 密钥..."
    if [ "$DRY_RUN" -eq 1 ]; then
        log_info "[DRY-RUN] 将执行: ssh-keygen -t rsa -b 4096 -N '' -f '$SSH_KEY_PATH' -q"
        return 0
    fi

    mkdir -p "$(dirname "$SSH_KEY_PATH")"
    chmod 700 "$(dirname "$SSH_KEY_PATH")"
    ssh-keygen -t rsa -b 4096 -N "" -f "$SSH_KEY_PATH" -q -C "wfb-server@$(hostname)"
    log_pass "成功生成 SSH 密钥对: $pub_key"
}

# 3. 连通性测试辅助函数
test_node_nopasswd() {
    local host_ip="$1"
    local ssh_user="$2"

    if [ "$DRY_RUN" -eq 1 ]; then
        return 1
    fi

    ssh "${SSH_BATCH_OPTS[@]}" \
        -i "$SSH_KEY_PATH" \
        "${ssh_user}@${host_ip}" \
        "sudo -n true" >/dev/null 2>&1
}

# 4. 获取统一登录密码
prompt_password_if_needed() {
    if [ -n "$SSH_PASSWORD" ]; then
        return 0
    fi

    if [ "$DRY_RUN" -eq 1 ]; then
        SSH_PASSWORD="dry_run_password"
        log_info "[DRY-RUN] 使用演练占位密码: ******"
        return 0
    fi

    echo ""
    log_info "集群中存在尚未建立免密互信的节点，请在 Server 上输入一次统一登录密码。"
    read -r -s -p "请输入 Client SSH/Sudo 登录密码: " SSH_PASSWORD
    echo ""

    if [ -z "$SSH_PASSWORD" ]; then
        log_fail "登录密码不能为空！"
        return 1
    fi
}

# 创建临时 askpass 辅助脚本供 ssh-copy-id 使用
prepare_askpass_helper() {
    if [ -n "$TEMP_ASKPASS" ] && [ -f "$TEMP_ASKPASS" ]; then
        return 0
    fi

    TEMP_PASSFILE="$(mktemp /tmp/wfb_pass.XXXXXX)"
    chmod 600 "$TEMP_PASSFILE"
    printf "%s\n" "$SSH_PASSWORD" > "$TEMP_PASSFILE"

    TEMP_ASKPASS="$(mktemp /tmp/wfb_askpass.XXXXXX)"
    cat <<EOF > "$TEMP_ASKPASS"
#!/bin/sh
cat "$TEMP_PASSFILE"
EOF
    chmod 700 "$TEMP_ASKPASS"
}

# 5. 向单个 Client 分发公钥并配置 sudo 免密
configure_single_client() {
    local role_name="$1"
    local host_ip="$2"
    local ssh_user="$3"
    local pub_key="${SSH_KEY_PATH}.pub"

    log_info "正在处理节点 $role_name ($host_ip)..."

    # 在 DRY_RUN 模式下直接模拟
    if [ "$DRY_RUN" -eq 1 ]; then
        log_info "  [DRY-RUN] 模拟网络连通性探测: $host_ip -> 在线"
        log_info "  [DRY-RUN] 拷贝公钥 $pub_key 到 ${ssh_user}@${host_ip}"
        log_info "  [DRY-RUN] 远程配置 /etc/sudoers.d/99-wfb-nopasswd"
        return 0
    fi

    # 网络连通性快速探测 (ping 或端口探测)
    if [ "$SKIP_NETWORK_CHECK" -ne 1 ]; then
        if ! ping -c 1 -W 2 "$host_ip" >/dev/null 2>&1; then
            if ! timeout 2 bash -c "</dev/tcp/${host_ip}/${SSH_PORT}" >/dev/null 2>&1; then
                log_warn "  节点 $role_name ($host_ip) 网络未连通或离线，跳过配置"
                return 2
            fi
        fi
    fi

    # 步骤 A: 分发公钥 (通过 OpenSSH 原生 SSH_ASKPASS)
    log_info "  向 ${ssh_user}@${host_ip} 分发 SSH 公钥..."
    if ssh "${SSH_BATCH_OPTS[@]}" -i "$SSH_KEY_PATH" "${ssh_user}@${host_ip}" "true" >/dev/null 2>&1; then
        log_info "  节点 $role_name 的 SSH 公钥已存在且有效，跳过拷贝"
    else
        prepare_askpass_helper
        if SSH_ASKPASS="$TEMP_ASKPASS" SSH_ASKPASS_REQUIRE=force DISPLAY=dummy:0 ssh-copy-id \
            -o "StrictHostKeyChecking=accept-new" \
            -p "$SSH_PORT" \
            -i "$pub_key" \
            "${ssh_user}@${host_ip}" >/dev/null 2>&1; then
            log_pass "  SSH 公钥分发成功"
        else
            log_fail "  SSH 公钥分发失败（请核对密码是否正确）"
            return 1
        fi
    fi

    # 步骤 B: 远程配置 sudo 免密 (/etc/sudoers.d/99-wfb-nopasswd)
    log_info "  配置远程 sudo 免密权限 (/etc/sudoers.d/99-wfb-nopasswd)..."
    local sudo_cmd="echo \"${ssh_user} ALL=(ALL) NOPASSWD: ALL\" > /etc/sudoers.d/99-wfb-nopasswd && chmod 0440 /etc/sudoers.d/99-wfb-nopasswd"

    # 先测试是否已经免密 sudo
    if ssh "${SSH_BATCH_OPTS[@]}" -i "$SSH_KEY_PATH" "${ssh_user}@${host_ip}" "sudo -n true" >/dev/null 2>&1; then
        log_pass "  远程 Sudo 免密已就绪"
        return 0
    fi

    # 通过 stdin 将密码安全注入远程 sudo -S
    if printf "%s\n" "$SSH_PASSWORD" | ssh "${SSH_COMMON_OPTS[@]}" -i "$SSH_KEY_PATH" "${ssh_user}@${host_ip}" \
        "sudo -S sh -c '$sudo_cmd'" >/dev/null 2>&1; then
        log_pass "  远程 Sudo 免密规则写入成功"
    else
        log_fail "  远程 Sudo 免密规则配置失败"
        return 1
    fi

    return 0
}

# 6. 最终免密连通性严格验证并汇总
verify_and_report() {
    log_step "3" "执行集群 SSH 与 Sudo 免密连通性最终严谨核验"

    local total="${#CLIENT_ROLES[@]}"
    local passed=0
    local failed=0
    local results=()

    echo ""
    log_info "逐一执行: ssh -o BatchMode=yes <user>@<ip> \"sudo -n true\""
    echo ""

    for i in "${!CLIENT_ROLES[@]}"; do
        local r="${CLIENT_ROLES[$i]}"
        local h="${CLIENT_HOSTS[$i]}"
        local u="${CLIENT_USERS[$i]}"
        local nid="${CLIENT_NODE_IDS[$i]}"
        local tip="${CLIENT_TUN_IPS[$i]}"

        if [ "$DRY_RUN" -eq 1 ]; then
            log_info "  [DRY-RUN] 模拟验证: $r ($h) -> PASS"
            results+=("PASS")
            passed=$((passed + 1))
            continue
        fi

        if ssh "${SSH_BATCH_OPTS[@]}" \
            -o ConnectTimeout=5 \
            -i "$SSH_KEY_PATH" \
            "${u}@${h}" \
            "sudo -n true" >/dev/null 2>&1; then
            log_pass "  [PASS] $r ($h, node_id=$nid) SSH 与 Sudo 免密验证通过！"
            results+=("PASS")
            passed=$((passed + 1))
        else
            log_fail "  [FAIL] $r ($h, node_id=$nid) 免密验证失败！"
            results+=("FAIL")
            failed=$((failed + 1))
        fi
    done

    echo ""
    echo "${C_BOLD}${C_CYAN}===================================================================${C_RESET}"
    echo "${C_BOLD}${C_CYAN}                  WFB-FL 集群认证互信状态看板                      ${C_RESET}"
    echo "${C_BOLD}${C_CYAN}===================================================================${C_RESET}"
    printf "  %-10s %-16s %-10s %-9s %-16s %-10s\n" "ROLE" "HOST_IP" "USER" "NODE_ID" "TUN_IP" "AUTH_STATUS"
    printf "  %-10s %-16s %-10s %-9s %-16s %-10s\n" "----------" "----------------" "----------" "---------" "----------------" "-----------"
    for i in "${!CLIENT_ROLES[@]}"; do
        local st="${results[$i]}"
        local st_color="${C_GREEN}"
        if [ "$st" != "PASS" ]; then
            st_color="${C_RED}"
        fi
        printf "  %-10s %-16s %-10s %-9s %-16s ${st_color}%-10s${C_RESET}\n" \
            "${CLIENT_ROLES[$i]}" "${CLIENT_HOSTS[$i]}" "${CLIENT_USERS[$i]}" "${CLIENT_NODE_IDS[$i]}" "${CLIENT_TUN_IPS[$i]}" "$st"
    done
    echo "${C_BOLD}${C_CYAN}===================================================================${C_RESET}"
    echo "  汇总: 检查 $total 台，通过 ${C_GREEN}$passed${C_RESET} 台，失败 ${C_RED}$failed${C_RESET} 台"
    echo ""

    if [ "$failed" -eq 0 ]; then
        log_pass "=========================================================="
        log_pass " 恭喜！集群全部 Client 节点 SSH 与 Sudo 免密互信均已成功就绪！"
        log_pass "=========================================================="
        return 0
    else
        log_fail "存在 $failed 台节点未通过免密验证，请检查网络连接、用户名与密码配置"
        return 1
    fi
}

main() {
    parse_args "$@"

    echo "${C_BOLD}===================================================================${C_RESET}"
    echo "${C_BOLD}          WFB-FL ARMv8 集群 SSH 与 Sudo 免密互信配置工具           ${C_RESET}"
    echo "${C_BOLD}===================================================================${C_RESET}"

    # 1. 加载并严格校验集群配置
    log_info "正在读取并校验集群配置文件: $CONFIG_FILE"
    if ! load_cluster_config "$CONFIG_FILE"; then
        log_fail "集群配置文件校验失败，终止运行"
        exit 1
    fi
    print_cluster_config

    # 2. 检查或生成 Server 本机 SSH 密钥对
    ensure_ssh_key

    # 3. 预检哪些节点需要配置
    log_step "2" "探测各 Client 免密状态并按需分发公钥与授权"
    local need_config_indices=()
    for i in "${!CLIENT_ROLES[@]}"; do
        if test_node_nopasswd "${CLIENT_HOSTS[$i]}" "${CLIENT_USERS[$i]}"; then
            log_pass "节点 ${CLIENT_ROLES[$i]} (${CLIENT_HOSTS[$i]}) 已具备免密权限，无需重复配置"
        else
            need_config_indices+=("$i")
        fi
    done

    # 若所有节点都已免密
    if [ "${#need_config_indices[@]}" -eq 0 ]; then
        log_pass "所有 Client 节点均已具备免密互信，无需重新分发！"
    else
        # 4. 提示输入一次密码
        prompt_password_if_needed

        # 5. 逐一分发公钥并配置远端 Sudo
        for idx in "${need_config_indices[@]}"; do
            configure_single_client \
                "${CLIENT_ROLES[$idx]}" \
                "${CLIENT_HOSTS[$idx]}" \
                "${CLIENT_USERS[$idx]}" || true
        done
    fi

    # 6. 最终验证与报告
    verify_and_report
}

main "$@"
