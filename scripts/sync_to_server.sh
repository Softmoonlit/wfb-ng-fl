#!/usr/bin/env bash
# scripts/sync_to_server.sh
# WSL / Linux: 一键向 Server 开发板传输项目代码与 UFTP 离线源码包 (优先 rsync 增量同步)

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
    C_GRAY=$'\033[90m'
else
    C_RESET=""
    C_BOLD=""
    C_RED=""
    C_GREEN=""
    C_YELLOW=""
    C_BLUE=""
    C_CYAN=""
    C_GRAY=""
fi

log_info()  { echo "${C_CYAN}[INFO]${C_RESET} $1"; }
log_pass()  { echo "${C_GREEN}[PASS]${C_RESET} $1"; }
log_warn()  { echo "${C_YELLOW}[WARN]${C_RESET} $1" >&2; }
log_fail()  { echo "${C_RED}[FAIL]${C_RESET} $1" >&2; }
log_dry()   { echo "${C_GRAY}[INFO] [DRY-RUN]${C_RESET} $1"; }

show_usage() {
    cat <<EOF
${C_CYAN}WFB-FL WSL/Linux 电脑端向 Server 开发板一键增量同步工具${C_RESET}

${C_YELLOW}用法:${C_RESET}
  $0 [选项]

${C_YELLOW}选项:${C_RESET}
  -s, --server <IP/别名>     指定 Server 开发板局域网 IP 或 SSH 别名 (如 192.168.1.100 或 vm0)
  -u, --user <用户名>        指定 SSH 登录用户名 (默认优先读取配置，缺省为 ubuntu)
  -c, --config <路径>        指定集群全局配置文件路径 (默认: ./cluster_nodes.conf)
  -r, --remote-dir <路径>    指定 Server 端存放项目的目标路径 (默认: projects/wfb-ng-fl)
  -z, --uftp-zip <路径>      指定本地 uftp_src-5.0.3.zip 离线源码包路径 (默认自动寻源)
  -m, --method <模式>        指定传输模式: auto (默认，优先 rsync 自动回退 tar) | rsync | tar
      --no-delete            rsync 传输时不删除远端多余文件 (默认启用 --delete 保持镜像干净)
  -n, --dry-run              演练模式 (仅打印即将执行的步骤与命令，不产生实际网络传输)
  -h, --help                 显示本帮助信息并退出

${C_YELLOW}说明:${C_RESET}
  本脚本运行于 WSL / Linux 环境，提供业界标准的开发板代码同步体验：
  1. 默认优先采用 ${C_BOLD}rsync 增量传输${C_RESET}，仅同步修改文件（毫秒级响应），自动排除 .git/、编译缓存及日志；
  2. 智能探测远端环境：若远端尚未安装 rsync，自动平滑降级为 ${C_BOLD}tar 管道流传输${C_RESET}，确保 100% 成功率；
  3. 自动定位上级目录中的 uftp_src-5.0.3.zip 离线源码包并同步到 Server 端上级目录；
  4. 同步完成后，Server 端目录结构与本地完全对齐，可直接开始一键集群部署与演示。
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

SERVER=""
USER=""
CONFIG_FILE="./cluster_nodes.conf"
REMOTE_DIR="projects/wfb-ng-fl"
UFTP_ZIP=""
METHOD="auto"
DELETE_EXTRA=1
DRY_RUN=0
USER_EXPLICIT=0

# 参数解析
while [[ $# -gt 0 ]]; do
    case "$1" in
        -s|--server)
            SERVER="$2"
            shift 2
            ;;
        -u|--user)
            USER="$2"
            USER_EXPLICIT=1
            shift 2
            ;;
        -c|--config|--config-file)
            CONFIG_FILE="$2"
            shift 2
            ;;
        -r|--remote-dir)
            REMOTE_DIR="$2"
            shift 2
            ;;
        -z|--uftp-zip)
            UFTP_ZIP="$2"
            shift 2
            ;;
        -m|--method)
            METHOD="$2"
            shift 2
            ;;
        --no-delete)
            DELETE_EXTRA=0
            shift
            ;;
        -n|--dry-run)
            DRY_RUN=1
            shift
            ;;
        -h|--help)
            show_usage
            exit 0
            ;;
        *)
            log_fail "未知选项: $1"
            show_usage
            exit 1
            ;;
    esac
done

if [[ "$METHOD" != "auto" && "$METHOD" != "rsync" && "$METHOD" != "tar" ]]; then
    log_fail "不支持的传输模式: $METHOD (仅支持 auto, rsync, tar)"
    exit 1
fi

# 1. 尝试从配置文件中解析未显式指定的 SERVER_HOST 与 SERVER_USER
if [ -f "$CONFIG_FILE" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        clean_line="$(echo "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
        if [[ "$clean_line" =~ ^# ]] || [ -z "$clean_line" ]; then
            continue
        fi
        if [[ "$clean_line" =~ ^SERVER_HOST=(.*) ]]; then
            val="${BASH_REMATCH[1]}"
            val="${val%%#*}"
            val="$(echo "$val" | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^["'\''"]//' -e 's/["'\''"]$//')"
            if [ -z "$SERVER" ]; then
                SERVER="$val"
            fi
        fi
        if [[ "$clean_line" =~ ^SERVER_USER=(.*) ]]; then
            val="${BASH_REMATCH[1]}"
            val="${val%%#*}"
            val="$(echo "$val" | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^["'\''"]//' -e 's/["'\''"]$//')"
            if [ -z "$USER" ]; then
                USER="$val"
            fi
        fi
    done < "$CONFIG_FILE"
fi

if [ -z "$USER" ]; then
    USER="ubuntu"
fi

if [ -z "$SERVER" ]; then
    log_fail "未指定 Server 主机 IP 或 SSH 别名！"
    echo "       请通过参数传入: $0 -s <IP/别名>" >&2
    echo "       或者在 $CONFIG_FILE 中配置 SERVER_HOST=<IP>" >&2
    exit 1
fi

# 2. 格式化 SSH 连接目标 Target
if [[ "$SERVER" == *"@"* ]]; then
    TARGET="$SERVER"
elif [ "$USER_EXPLICIT" -eq 1 ]; then
    TARGET="${USER}@${SERVER}"
elif [[ "$SERVER" =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]]; then
    TARGET="${USER}@${SERVER}"
else
    # 针对 SSH Config 中的别名 (如 vm0)，直接保留别名以使用别名内指定的 User 与 IdentityFile
    TARGET="$SERVER"
fi

log_info "连接目标 Server: $TARGET (远端目标目录: $REMOTE_DIR)"

# 3. 自动定位本地 UFTP 源码包
RESOLVED_UFTP_ZIP=""
search_candidates=(
    "$UFTP_ZIP"
    "$SCRIPT_DIR/../../uftp_src-5.0.3.zip"
    "$SCRIPT_DIR/../uftp_src-5.0.3.zip"
    "../uftp_src-5.0.3.zip"
    "./uftp_src-5.0.3.zip"
    "${HOME}/uftp_src-5.0.3.zip"
)

for cand in "${search_candidates[@]}"; do
    if [ -n "$cand" ] && [ -f "$cand" ]; then
        RESOLVED_UFTP_ZIP="$(cd "$(dirname "$cand")" && pwd)/$(basename "$cand")"
        break
    fi
done

if [ -n "$RESOLVED_UFTP_ZIP" ]; then
    log_pass "定位到本地 UFTP 源码包: $RESOLVED_UFTP_ZIP"
else
    log_warn "未在本地找到 uftp_src-5.0.3.zip，仅同步代码库"
fi

# 4. 计算远端父目录 (例如 projects/wfb-ng-fl -> projects)
REMOTE_PARENT="$(dirname "$REMOTE_DIR")"
if [ "$REMOTE_PARENT" = "." ] || [ -z "$REMOTE_PARENT" ]; then
    REMOTE_PARENT="projects"
fi

# 5. 检查演练模式 (Dry-Run)
if [ "$DRY_RUN" -eq 1 ]; then
    log_dry "创建远端目录: ssh $TARGET \"mkdir -p '$REMOTE_DIR' '$REMOTE_PARENT'\""
    if [ "$METHOD" = "tar" ]; then
        log_dry "模式 [tar 管道]: tar --exclude=\".git\" --exclude=\"*.o\" --exclude=\"*.so\" --exclude=\"*.a\" --exclude=\"wfb_v6_uplink\" --exclude=\"__pycache__\" --exclude=\"logs\" -czf - -C \"$REPO_ROOT\" . | ssh $TARGET \"tar -xzf - -C '$REMOTE_DIR'\""
    else
        del_opt=""
        [ "$DELETE_EXTRA" -eq 1 ] && del_opt="--delete "
        log_dry "模式 [rsync 增量]: rsync -avz ${del_opt}--exclude=\".git/\" --exclude=\"*.o\" --exclude=\"*.so\" --exclude=\"*.a\" --exclude=\"wfb_v6_uplink\" --exclude=\"__pycache__/\" --exclude=\"*.pyc\" --exclude=\"logs/\" \"$REPO_ROOT/\" \"${TARGET}:${REMOTE_DIR}/\""
    fi
    if [ -n "$RESOLVED_UFTP_ZIP" ]; then
        log_dry "上传 UFTP 离线包: scp \"$RESOLVED_UFTP_ZIP\" \"${TARGET}:${REMOTE_PARENT}/\""
    fi
    log_pass "演练完成 (DRY-RUN 模式未产生实际网络传输)"
    exit 0
fi

# 6. 正式执行传输
log_info "正在连接 Server 并创建远端目录..."
if ! ssh "$TARGET" "mkdir -p '$REMOTE_DIR' '$REMOTE_PARENT'"; then
    log_fail "无法通过 SSH 连接至 Server ($TARGET)，请检查网络或 SSH 密钥配置。"
    exit 1
fi

if [ -n "$RESOLVED_UFTP_ZIP" ]; then
    log_info "正在上传 UFTP 源码包至 ${TARGET}:${REMOTE_PARENT}/ ..."
    if command -v rsync >/dev/null 2>&1; then
        rsync -az "$RESOLVED_UFTP_ZIP" "${TARGET}:${REMOTE_PARENT}/" || scp "$RESOLVED_UFTP_ZIP" "${TARGET}:${REMOTE_PARENT}/"
    else
        scp "$RESOLVED_UFTP_ZIP" "${TARGET}:${REMOTE_PARENT}/"
    fi
    log_pass "UFTP 源码包上传完成"
fi

# 决定传输策略 (rsync 或 tar)
USE_RSYNC=0
if [ "$METHOD" = "rsync" ]; then
    if ! command -v rsync >/dev/null 2>&1; then
        log_fail "本地未安装 rsync，请运行 sudo apt-get install -y rsync 安装"
        exit 1
    fi
    if ! ssh "$TARGET" "command -v rsync >/dev/null 2>&1"; then
        log_fail "远端 Server ($TARGET) 未安装 rsync 命令"
        exit 1
    fi
    USE_RSYNC=1
elif [ "$METHOD" = "auto" ]; then
    if command -v rsync >/dev/null 2>&1 && ssh "$TARGET" "command -v rsync >/dev/null 2>&1"; then
        USE_RSYNC=1
    else
        log_warn "远端或本地未检测到 rsync，将自动降级使用 Linux tar 管道流传输"
        USE_RSYNC=0
    fi
fi

if [ "$USE_RSYNC" -eq 1 ]; then
    log_info "正在使用 rsync 极速增量同步项目代码 (排除 .git 与编译缓存)..."
    RSYNC_OPTS=("-avz")
    if [ "$DELETE_EXTRA" -eq 1 ]; then
        RSYNC_OPTS+=("--delete")
    fi
    RSYNC_OPTS+=(
        "--exclude=.git/"
        "--exclude=*.o"
        "--exclude=*.so"
        "--exclude=*.a"
        "--exclude=wfb_v6_uplink"
        "--exclude=__pycache__/"
        "--exclude=*.pyc"
        "--exclude=logs/"
    )
    rsync "${RSYNC_OPTS[@]}" "$REPO_ROOT/" "${TARGET}:${REMOTE_DIR}/"
else
    log_info "正在使用 tar 管道流传输项目代码..."
    tar --exclude=".git" \
        --exclude="*.o" \
        --exclude="*.so" \
        --exclude="*.a" \
        --exclude="wfb_v6_uplink" \
        --exclude="__pycache__" \
        --exclude="logs" \
        -czf - -C "$REPO_ROOT" . | ssh "$TARGET" "tar -xzf - -C '$REMOTE_DIR'"
fi

echo ""
log_pass "项目代码与 UFTP 源码包已成功同步至 Server 开发板 (${TARGET}:${REMOTE_DIR})！"
echo ""
echo "${C_YELLOW}后续操作指引 (在 WSL 终端中执行):${C_RESET}"
echo "  1. 登录 Server 开发板:  ssh $TARGET"
echo "  2. 进入项目目录:        cd $REMOTE_DIR"
echo "  3. 首次部署 Server 本机: sudo ./scripts/deploy_node.sh"
echo "  4. 建立集群免密互信:     ./scripts/setup_cluster_auth.sh"
echo "  5. 批量部署所有 Client:  ./scripts/deploy_cluster.sh"
echo "  6. 启动现场演示:        bash tests/real_hardware/run_fl_demo.sh all"
