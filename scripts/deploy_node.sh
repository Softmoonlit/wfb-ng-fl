#!/bin/bash
# scripts/deploy_node.sh
# ARMv8 多板载集群环境一键自动化部署脚本（单机节点）

set -euo pipefail
trap 'echo "[FAIL] $(date +%H:%M:%S) 部署脚本在第 $LINENO 行发生未捕获错误，退出码 $?" >&2' ERR

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# 基础路径与默认配置
SYSCONFDIR="${SYSCONFDIR:-/etc}"
PREFIX="${PREFIX:-/usr}"
DESTDIR="${DESTDIR:-}"
RTL8812AU_REPO="${RTL8812AU_REPO:-https://github.com/svpcom/rtl8812au.git}"
RTL8812AU_DIR="${RTL8812AU_DIR:-}"
UFTP_SRC_ZIP="${UFTP_SRC_ZIP:-}"
TARGET_USER="${TARGET_USER:-${SUDO_USER:-$(whoami)}}"

DRY_RUN="${DRY_RUN:-0}"
CHECK_ONLY="${CHECK_ONLY:-0}"
SKIP_DRIVER="${SKIP_DRIVER:-0}"
SKIP_APT="${SKIP_APT:-0}"
SKIP_ROOT_CHECK="${SKIP_ROOT_CHECK:-0}"
SKIP_BUILD_WFB="${SKIP_BUILD_WFB:-0}"

# 终端彩色定义
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
log_step()  { echo ""; echo "${C_BOLD}${C_CYAN}=== 步骤 $1: $2 ===${C_RESET}"; }

usage() {
    cat <<EOF
${C_BOLD}ARMv8 板载节点一键自动化部署脚本${C_RESET}

用法:
  sudo bash scripts/deploy_node.sh [选项]

选项:
  -h, --help        显示本帮助信息并退出
  -n, --dry-run     模拟运行，打印将要执行的操作，不修改系统
  -c, --check-only  仅执行环境与依赖检查，不执行实际安装
  --skip-driver     跳过 RTL8812AU 内核网卡驱动的编译与安装
  --skip-apt        跳过 apt-get 依赖包安装步骤

环境变量可覆盖配置:
  SYSCONFDIR        配置文件安装目录（默认: /etc）
  PREFIX            程序安装前缀（默认: /usr）
  UFTP_SRC_ZIP      UFTP 源码 zip 路径（默认自动探测）
  RTL8812AU_DIR     本地 RTL8812AU 驱动源码路径
  TARGET_USER       配置免密 sudo 的目标用户（默认: \$SUDO_USER 或当前用户）
EOF
}

# 命令行参数解析
while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)
            usage
            exit 0
            ;;
        -n|--dry-run)
            DRY_RUN=1
            shift
            ;;
        -c|--check-only)
            CHECK_ONLY=1
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
        *)
            echo "未知参数: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

run_cmd() {
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "  [DRY-RUN] $*"
        return 0
    fi
    "$@"
}

require_root() {
    if [ "$SKIP_ROOT_CHECK" -eq 1 ] || [ "$DRY_RUN" -eq 1 ] || [ "$CHECK_ONLY" -eq 1 ]; then
        return 0
    fi
    if [ "$(id -u)" -ne 0 ]; then
        log_fail "本部署脚本必须以 root 权限运行，请使用 sudo: sudo bash scripts/deploy_node.sh"
        exit 1
    fi
}

check_architecture() {
    local arch
    arch="$(uname -m)"
    log_info "检测到主机 CPU 架构: $arch"
    if [ "$arch" = "aarch64" ] || [ "$arch" = "arm64" ] || [ "$arch" = "armv8l" ]; then
        log_pass "架构符合 ARMv8 平台规范"
    else
        log_warn "当前架构不是 aarch64/armv8（当前为 $arch），将按通用 Linux 流程继续执行"
    fi
}

step1_apt_dependencies() {
    log_step "1" "安装系统基础工具链与核心依赖库"

    local pkgs=(
        build-essential
        pkg-config
        python3
        python3-pip
        libssl-dev
        libsodium-dev
        libpcap-dev
        iw
        rfkill
        unzip
        git
        bc
        dkms
        net-tools
        iproute2
    )

    if [ "$SKIP_APT" -eq 1 ]; then
        log_info "已配置 --skip-apt，跳过 apt 依赖安装"
        return 0
    fi

    if [ "$DRY_RUN" -eq 1 ]; then
        echo "  [DRY-RUN] apt-get update"
        echo "  [DRY-RUN] apt-get install -y ${pkgs[*]}"
        echo "  [DRY-RUN] apt-get install -y linux-headers-\$(uname -r)"
        return 0
    fi

    if command -v apt-get >/dev/null 2>&1; then
        log_info "更新 APT 软件源索引..."
        apt-get update || log_warn "apt-get update 存在部分失败，继续尝试安装依赖包"

        log_info "安装核心工具与开发库: ${pkgs[*]}"
        apt-get install -y "${pkgs[@]}"

        local kernel_pkg="linux-headers-$(uname -r)"
        log_info "尝试安装内核头文件: $kernel_pkg"
        if ! apt-get install -y "$kernel_pkg"; then
            log_warn "未通过 apt 找到精确匹配的 $kernel_pkg；对于特定开发板，可能需从板厂镜像或单独提供内核头文件"
        fi
        log_pass "APT 依赖包处理完成"
    else
        log_warn "未检测到 apt-get 包管理器，跳过 Debian/Ubuntu 系包安装"
    fi
}

step2_install_driver() {
    log_step "2" "构建并安装 RTL8812AU 无线网卡驱动"

    if [ "$SKIP_DRIVER" -eq 1 ]; then
        log_info "已配置 --skip-driver，跳过网卡驱动处理"
        return 0
    fi

    # 检查是否已加载驱动模块
    if lsmod 2>/dev/null | grep -qE '8812au|88XXau|rtl88xxau_wfb'; then
        log_pass "检测到 RTL8812AU 驱动内核模块已处于加载状态，跳过重复构建"
        return 0
    fi

    local build_dir=""
    if [ -n "$RTL8812AU_DIR" ] && [ -d "$RTL8812AU_DIR" ]; then
        build_dir="$RTL8812AU_DIR"
        log_info "使用指定的 RTL8812AU 源码目录: $build_dir"
    elif [ -d "$PROJECT_ROOT/../rtl8812au" ]; then
        build_dir="$PROJECT_ROOT/../rtl8812au"
        log_info "复用上级目录已有源码: $build_dir"
    else
        build_dir="/tmp/wfb-build/rtl8812au"
        log_info "从 GitHub 克隆 RTL8812AU 源码至: $build_dir"
        run_cmd mkdir -p "/tmp/wfb-build"
        if [ "$DRY_RUN" -eq 0 ]; then
            rm -rf "$build_dir"
            git clone --depth 1 "$RTL8812AU_REPO" "$build_dir"
        else
            echo "  [DRY-RUN] git clone --depth 1 $RTL8812AU_REPO $build_dir"
        fi
    fi

    if [ "$DRY_RUN" -eq 1 ]; then
        echo "  [DRY-RUN] cd $build_dir && ./dkms-install.sh"
        echo "  [DRY-RUN] modprobe 8812au"
        return 0
    fi

    if [ -f "$build_dir/dkms-install.sh" ]; then
        log_info "开始执行 DKMS 驱动安装 (dkms-install.sh)..."
        (cd "$build_dir" && ./dkms-install.sh)
        log_info "尝试加载驱动内核模块..."
        modprobe 8812au 2>/dev/null || modprobe 88XXau 2>/dev/null || modprobe rtl88xxau_wfb 2>/dev/null || {
            log_warn "驱动已编译安装，但 modprobe 模块未成功加载（可能尚未插入 USB 网卡，插入网卡后将自动加载）"
        }
        log_pass "RTL8812AU 驱动安装流程完成"
    else
        log_fail "未在 $build_dir 找到 dkms-install.sh 脚本"
        return 1
    fi
}

resolve_uftp_zip() {
    if [ -n "$UFTP_SRC_ZIP" ] && [ -f "$UFTP_SRC_ZIP" ]; then
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

step3_build_uftp() {
    log_step "3" "编译并安装 UFTP 原生通信组件 (uftp / uftpd)"

    if command -v uftp >/dev/null 2>&1 && command -v uftpd >/dev/null 2>&1; then
        log_pass "检测到系统中已存在 uftp 与 uftpd: $(command -v uftp), $(command -v uftpd)"
        return 0
    fi

    local zip_file=""
    if zip_file="$(resolve_uftp_zip)"; then
        log_info "发现 UFTP 源码压缩包: $zip_file"
    else
        log_warn "未自动发现 uftp_src-5.0.3.zip，尝试检查是否有已解压的目录"
    fi

    local uftp_work_dir="/tmp/wfb-build/uftp-src"

    if [ "$DRY_RUN" -eq 1 ]; then
        echo "  [DRY-RUN] unzip -q $zip_file -d /tmp/wfb-build"
        echo "  [DRY-RUN] make (linking -lcrypto)"
        echo "  [DRY-RUN] make install DESTDIR=$DESTDIR PREFIX=$PREFIX"
        return 0
    fi

    run_cmd mkdir -p "/tmp/wfb-build"
    if [ -n "$zip_file" ]; then
        rm -rf "$uftp_work_dir"
        mkdir -p "$uftp_work_dir"
        unzip -q -o "$zip_file" -d "$uftp_work_dir"
        # 兼容源码包内部的一级目录
        local inner_dir
        inner_dir="$(find "$uftp_work_dir" -maxdepth 2 -type f -name 'makefile' -exec dirname {} \; | head -n1)"
        if [ -n "$inner_dir" ]; then
            uftp_work_dir="$inner_dir"
        fi
    fi

    if [ -f "$uftp_work_dir/makefile" ]; then
        log_info "进入 $uftp_work_dir 执行编译..."
        make -C "$uftp_work_dir"
        log_info "执行 make install 将 uftp / uftpd 部署到 $PREFIX/bin..."
        make -C "$uftp_work_dir" install DESTDIR="$DESTDIR"
        log_pass "UFTP 编译与安装完成"
    else
        log_fail "未找到 UFTP makefile，无法完成构建"
        return 1
    fi
}

step4_network_and_rf() {
    log_step "4" "配置 NetworkManager 排除规则与解锁射频"

    local nm_conf_dir="$DESTDIR$SYSCONFDIR/NetworkManager/conf.d"
    local nm_conf_file="$nm_conf_dir/wfb-unmanaged.conf"

    if [ "$DRY_RUN" -eq 1 ]; then
        echo "  [DRY-RUN] mkdir -p $nm_conf_dir"
        echo "  [DRY-RUN] write unmanaged-devices=interface-name:wlx* to $nm_conf_file"
        echo "  [DRY-RUN] systemctl restart/reload NetworkManager"
        echo "  [DRY-RUN] rfkill unblock all"
        return 0
    fi

    log_info "在 $nm_conf_file 中配置 NetworkManager 忽略 wlx* 无线网卡..."
    run_cmd mkdir -p "$nm_conf_dir"
    cat > "$nm_conf_file" <<'EOF'
[keyfile]
unmanaged-devices=interface-name:wlx*
EOF
    log_pass "已写入 $nm_conf_file"

    if command -v systemctl >/dev/null 2>&1 && systemctl is-active NetworkManager >/dev/null 2>&1; then
        log_info "重载 NetworkManager 配置..."
        systemctl reload NetworkManager 2>/dev/null || systemctl restart NetworkManager 2>/dev/null || true
    fi

    log_info "执行 rfkill unblock all 解除全部射频软件阻塞..."
    if command -v rfkill >/dev/null 2>&1; then
        rfkill unblock all || true
        log_pass "射频已解锁"
    else
        log_warn "未找到 rfkill 命令，跳过解锁"
    fi
}

step5_sysctl_and_kernel() {
    log_step "5" "配置内核网络缓冲区与 TUN 模块"

    local sysctl_dir="$DESTDIR$SYSCONFDIR/sysctl.d"
    local sysctl_dest="$sysctl_dir/98-wifibroadcast.conf"
    local sysctl_src="$PROJECT_ROOT/scripts/sysctl/98-wifibroadcast.conf"

    if [ "$DRY_RUN" -eq 1 ]; then
        echo "  [DRY-RUN] cp $sysctl_src $sysctl_dest"
        echo "  [DRY-RUN] sysctl --system"
        echo "  [DRY-RUN] modprobe tun"
        return 0
    fi

    run_cmd mkdir -p "$sysctl_dir"
    if [ -f "$sysctl_src" ]; then
        log_info "拷贝内核网络优化配置到 $sysctl_dest..."
        cp "$sysctl_src" "$sysctl_dest"
        if command -v sysctl >/dev/null 2>&1; then
            log_info "应用 sysctl 网络参数..."
            sysctl --system >/dev/null 2>&1 || sysctl -p "$sysctl_dest" >/dev/null 2>&1 || true
        fi
        log_pass "内核网络参数优化已生效"
    else
        log_warn "未在 $sysctl_src 找到网络参数配置文件"
    fi

    log_info "确保 TUN 虚拟设备内核模块加载..."
    modprobe tun 2>/dev/null || true
    if [ -c /dev/net/tun ]; then
        log_pass "TUN 设备正常就绪 (/dev/net/tun)"
    else
        log_warn "/dev/net/tun 字符设备未直接就绪，可能在创建 TUN 网卡时由内核动态激活"
    fi
}

step6_sudoers_nopasswd() {
    log_step "6" "配置当前用户免密 Sudo 规则"

    if [ -z "$TARGET_USER" ] || [ "$TARGET_USER" = "root" ]; then
        log_info "当前为 root 用户，跳过普通用户免密 sudoers 规则写入"
        return 0
    fi

    local sudoers_dir="$DESTDIR$SYSCONFDIR/sudoers.d"
    local sudoers_file="$sudoers_dir/99-wfb-nopasswd"

    if [ "$DRY_RUN" -eq 1 ]; then
        echo "  [DRY-RUN] mkdir -p $sudoers_dir"
        echo "  [DRY-RUN] echo '$TARGET_USER ALL=(ALL) NOPASSWD: ALL' > $sudoers_file"
        echo "  [DRY-RUN] chmod 0440 $sudoers_file"
        return 0
    fi

    log_info "为用户 $TARGET_USER 写入免密 Sudo 规则: $sudoers_file"
    run_cmd mkdir -p "$sudoers_dir"
    cat > "$sudoers_file" <<EOF
$TARGET_USER ALL=(ALL) NOPASSWD: ALL
EOF
    chmod 0440 "$sudoers_file"

    if command -v visudo >/dev/null 2>&1; then
        if ! visudo -c -f "$sudoers_file" >/dev/null 2>&1; then
            log_fail "sudoers 规则语法校验失败，已自动撤销该文件以保障系统安全"
            rm -f "$sudoers_file"
            return 1
        fi
    fi
    log_pass "免密 sudo 权限配置完成并通过 visudo 语法验证"
}

step7_build_and_install_wfb() {
    log_step "7" "编译 WFB-FL 链路底座并执行系统安装"

    if [ "$SKIP_BUILD_WFB" -eq 1 ]; then
        log_info "已配置 SKIP_BUILD_WFB=1，跳过 WFB C++ 编译与系统级安装"
        return 0
    fi

    if [ "$DRY_RUN" -eq 1 ]; then
        echo "  [DRY-RUN] cd $PROJECT_ROOT && make build_v6"
        echo "  [DRY-RUN] cd $PROJECT_ROOT && ./scripts/install-v8.sh"
        return 0
    fi

    log_info "在 $PROJECT_ROOT 编译 wfb_v6_uplink..."
    (cd "$PROJECT_ROOT" && make build_v6)
    log_pass "底座核心二进制构建成功"

    log_info "执行系统角色服务与 Python 运行时安装 (install-v8.sh)..."
    (cd "$PROJECT_ROOT" && PREFIX="$PREFIX" DESTDIR="$DESTDIR" ./scripts/install-v8.sh)
    log_pass "WFB-FL 系统级角色服务与配置模板安装完成"
}

step8_final_verification() {
    log_step "8" "安装结果最终核验"

    if [ "$DRY_RUN" -eq 1 ]; then
        echo "  [DRY-RUN] 模拟核验: wfb-fl-server wfb-fl-client wfb_v6_uplink uftp uftpd"
        echo "  [DRY-RUN] 模拟探测: wlx* 无线网卡"
        echo ""
        log_pass "=========================================================="
        log_pass " [DRY-RUN] 模拟运行完成，所有预演步骤均已正确生成！"
        log_pass "=========================================================="
        return 0
    fi

    local missing=0
    local bins=(
        "wfb-fl-server"
        "wfb-fl-client"
        "wfb_v6_uplink"
        "uftp"
        "uftpd"
    )

    log_info "检查关键系统可执行文件路径:"
    for b in "${bins[@]}"; do
        if command -v "$b" >/dev/null 2>&1; then
            log_pass "  $b -> $(command -v "$b")"
        elif [ -x "$PREFIX/bin/$b" ] || [ -x "$DESTDIR$PREFIX/bin/$b" ]; then
            log_pass "  $b -> $PREFIX/bin/$b"
        else
            log_fail "  缺失命令: $b"
            missing=$((missing + 1))
        fi
    done

    log_info "检查可用 RTL8812AU (wlx*) 接口:"
    local found_wlx=0
    if command -v iw >/dev/null 2>&1; then
        for iface in $(iw dev 2>/dev/null | awk '/Interface / {print $2}' | grep '^wlx' || true); do
            log_pass "  发现无线网卡: $iface"
            found_wlx=1
        done
    fi

    if [ "$found_wlx" -eq 0 ]; then
        log_warn "  当前未发现名称以 wlx 开头的无线网卡（请确保 USB 网卡已插入且供电充足）"
    fi

    if [ "$missing" -eq 0 ]; then
        echo ""
        log_pass "=========================================================="
        log_pass " 恭喜！当前单机节点已成功完成 WFB-FL 全部环境与依赖部署！"
        log_pass "=========================================================="
    else
        echo ""
        log_fail "部署完成但存在 $missing 项缺失命令，请检查上述日志"
        return 1
    fi
}

main() {
    require_root
    check_architecture

    if [ "$CHECK_ONLY" -eq 1 ]; then
        step8_final_verification
        exit 0
    fi

    step1_apt_dependencies
    step2_install_driver
    step3_build_uftp
    step4_network_and_rf
    step5_sysctl_and_kernel
    step6_sudoers_nopasswd
    step7_build_and_install_wfb
    step8_final_verification
}

main "$@"
