#!/bin/bash
# -*- coding: utf-8 -*-
# issue #41 SSH 编排真实硬件 FL Runtime 闭环验收脚本

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
BRANCH="${ISSUE41_BRANCH:-feat/41-real-hardware-fl-runtime-redo}"
ARCHIVE_ROOT="${ISSUE41_ARCHIVE_ROOT:-$PROJECT_ROOT/tests/logs}"
RUN_ID="${ISSUE41_RUN_ID:-v8_issue41_$(date +%Y%m%d_%H%M%S)}"
ARCHIVE_DIR="${ISSUE41_ARCHIVE_DIR:-$ARCHIVE_ROOT/$RUN_ID}"
REMOTE_REPO="${ISSUE41_REMOTE_REPO:-/home/virt/projects/wfb-ng-fl}"
CLIENT1_SSH="${ISSUE41_CLIENT1_SSH:-vm1}"
CLIENT2_SSH="${ISSUE41_CLIENT2_SSH:-vm2}"
SERVER_TUN="${ISSUE41_SERVER_TUN:-v8i41s0}"
CLIENT1_TUN="${ISSUE41_CLIENT1_TUN:-v8i41c1}"
CLIENT2_TUN="${ISSUE41_CLIENT2_TUN:-v8i41c2}"
SERVER_TUN_ADDR="${ISSUE41_SERVER_TUN_ADDR:-10.80.0.1/24}"
CLIENT1_TUN_ADDR="${ISSUE41_CLIENT1_TUN_ADDR:-10.80.0.11/24}"
CLIENT2_TUN_ADDR="${ISSUE41_CLIENT2_TUN_ADDR:-10.80.0.12/24}"
UFTP_GROUP="${ISSUE41_UFTP_GROUP:-239.80.41.1}"
UFTP_PRIVATE_GROUP="${ISSUE41_UFTP_PRIVATE_GROUP:-239.80.41.2}"
UFTP_PORT="${ISSUE41_UFTP_PORT:-1044}"
HTTP_HOST="${ISSUE41_HTTP_HOST:-10.80.0.1}"
HTTP_PORT="${ISSUE41_HTTP_PORT:-8080}"
CHANNEL="${ISSUE41_CHANNEL:-157}"
CHANNEL_WIDTH="${ISSUE41_CHANNEL_WIDTH:-HT40+}"
LINK_ID="${ISSUE41_LINK_ID:-406}"
UPLINK_STREAM="${ISSUE41_UPLINK_STREAM:-32}"
DOWNLINK_STREAM="${ISSUE41_DOWNLINK_STREAM:-33}"
FEC_K="${ISSUE41_FEC_K:-8}"
FEC_N="${ISSUE41_FEC_N:-12}"
RADIO_BANDWIDTH="${ISSUE41_RADIO_BANDWIDTH:-40}"
RADIO_MCS_INDEX="${ISSUE41_RADIO_MCS_INDEX:-3}"
RADIO_SHORT_GI="${ISSUE41_RADIO_SHORT_GI:-1}"
LINK_LOG_INTERVAL_MS="${ISSUE41_LINK_LOG_INTERVAL_MS:-1000}"
IO_TIMEOUT_SECONDS="${ISSUE41_IO_TIMEOUT_SECONDS:-120}"
FEEDBACK_WINDOW_PERIOD_MS="${ISSUE41_FEEDBACK_WINDOW_PERIOD_MS:-500}"
FEEDBACK_WINDOW_DURATION_MS="${ISSUE41_FEEDBACK_WINDOW_DURATION_MS:-15}"
AGGREGATION_DELAY_MS="${ISSUE41_AGGREGATION_DELAY_MS:-0}"
ROUNDS="${ISSUE41_ROUNDS:-1}"
INPUT_SIZE_BYTES=$((4 * 1024 * 1024))
INITIAL_MODEL_PATH="${ISSUE41_INITIAL_MODEL_PATH:-/var/lib/wfb-ng/issue41-input/model-4mib.bin}"
CLIENT1_UPDATE_TEMPLATE_PATH="${ISSUE41_CLIENT1_UPDATE_TEMPLATE_PATH:-/var/lib/wfb-ng/issue41-input/update-client1-4mib.bin}"
CLIENT2_UPDATE_TEMPLATE_PATH="${ISSUE41_CLIENT2_UPDATE_TEMPLATE_PATH:-/var/lib/wfb-ng/issue41-input/update-client2-4mib.bin}"
KEEP_RUNNING_ON_FAIL="${ISSUE41_KEEP_RUNNING_ON_FAIL:-0}"
RESET_RUNTIME_STATE="${ISSUE41_RESET_RUNTIME_STATE:-0}"
SMOKE_CYCLE_COUNT="${ISSUE41_SMOKE_CYCLE_COUNT:-3}"
SMOKE_IO_TIMEOUT_SECONDS="${ISSUE41_SMOKE_IO_TIMEOUT_SECONDS:-120}"
SMOKE_CYCLE_DEADLINE_SECONDS="${ISSUE41_SMOKE_CYCLE_DEADLINE_SECONDS:-240}"
SMOKE_TIMEOUT_SECONDS="${ISSUE41_SMOKE_TIMEOUT_SECONDS:-180}"
RUNTIME_TIMEOUT_SECONDS="${ISSUE41_RUNTIME_TIMEOUT_SECONDS:-180}"
STOP_CLEANUP_TIMEOUT_SECONDS="${ISSUE41_STOP_CLEANUP_TIMEOUT_SECONDS:-5}"
RADIO_MIN_USB_SPEED="${ISSUE41_RADIO_MIN_USB_SPEED:-480}"
STRICT_USB_SPEED="${ISSUE41_STRICT_USB_SPEED:-0}"

cmd="${1:-help}"
shift || true

usage() {
    cat <<'EOF'
用法：bash tests/real_hardware/issue41_fl_runtime_loop.sh <子命令>

子命令：
  push-branch              显式 push 当前干净分支到 origin
  sync-remotes             SSH 同步 client1/client2 到 origin/<branch>
  generate-fixtures        在三机生成确定性 4 MiB 验收模型与 update 模板
  preflight                检查三机依赖、sudo、仓库状态、wlx 网卡与端口
  install                  三机执行 make build_v6、sudo make install_v8、daemon-reload
  smoke-gate               连续三周期双向数据面 gate 验收 (别名: smoke)
  run-runtime-loop         写 issue41 配置/drop-in 并启动 systemd Runtime loop
  lifecycle-stop-restart   stop/restart/no-overlap 生命周期验收
  collect                  采集本机和远端状态证据
  summary                  生成 issue41_summary.json、result.md 并运行归档校验器
  run-all                  串联 preflight/install/smoke/runtime/lifecycle/collect/summary；不自动 push
  stop-all                 停止三机 issue41 相关服务和临时进程
  clean                    停止后清理 issue41 明确管理路径

说明：
  run-all 不执行 push-branch，也不执行 sync-remotes。push 是显式对外发布步骤。
  密码不得写入脚本、日志或归档；请使用 SSH key 或交互式 sudo/ssh。
  run-runtime-loop 默认拒绝复用旧 work_dir；重试前执行 clean，或显式设置：
    ISSUE41_RESET_RUNTIME_STATE=1 ... run-runtime-loop
EOF
}

log_info() { printf '[信息] %s\n' "$1"; }
log_ok() { printf '[通过] %s\n' "$1"; }
log_warn() { printf '[注意] %s\n' "$1"; }
die() { printf '[失败] %s\n' "$1" >&2; exit 1; }

case "$cmd" in
    help|--help|-h) usage; exit 0 ;;
esac

init_envelope() {
    if [ -d "$ARCHIVE_DIR" ]; then
        die "归档目录已存在，拒绝覆盖：$ARCHIVE_DIR"
    fi
    python3 "$SCRIPT_DIR/issue41_envelope.py" init \
        --archive-dir "$ARCHIVE_DIR" \
        --run-id "$RUN_ID" \
        --branch "$BRANCH" \
        --commit "$(git -C "$PROJECT_ROOT" rev-parse HEAD 2>/dev/null || echo '')" \
        --mode "${ISSUE41_MODE:-formal}" || die "运行包络初始化失败"
}

record_preflight_failure() {
    local layer="$1" category="$2" reason="$3" last_layer="${4:-}"
    python3 "$SCRIPT_DIR/issue41_envelope.py" record-failure \
        --archive-dir "$ARCHIVE_DIR" \
        --layer "$layer" \
        --category "$category" \
        --reason "$reason" \
        --last-successful-layer "$last_layer" >/dev/null 2>&1 || true
    cmd_stop_all 2>/dev/null || true
    die "preflight 失败 [$layer / $category]：$reason"
}

client_ssh() {
    case "$1" in
        client1) printf '%s\n' "$CLIENT1_SSH" ;;
        client2) printf '%s\n' "$CLIENT2_SSH" ;;
        *) die "未知 client：$1" ;;
    esac
}

client_tun() {
    case "$1" in client1) printf '%s\n' "$CLIENT1_TUN" ;; client2) printf '%s\n' "$CLIENT2_TUN" ;; esac
}

client_addr() {
    case "$1" in client1) printf '%s\n' "$CLIENT1_TUN_ADDR" ;; client2) printf '%s\n' "$CLIENT2_TUN_ADDR" ;; esac
}

client_ip() { client_addr "$1" | cut -d/ -f1; }

remote() {
    local role="$1"
    shift
    ssh -o BatchMode=yes "$(client_ssh "$role")" "$@"
}

run_local_capture() {
    local name="$1"
    shift
    "$@" > "$ARCHIVE_DIR/orchestration/${name}.log" 2>&1
}

require_clean_repo() {
    local status branch
    branch="$(git -C "$PROJECT_ROOT" rev-parse --abbrev-ref HEAD)"
    [ "$branch" = "$BRANCH" ] || die "当前分支 $branch 不是 $BRANCH"
    status="$(git -C "$PROJECT_ROOT" status --short)"
    [ -z "$status" ] || die "工作区不干净，拒绝继续：$status"
}

cmd_push_branch() {
    require_clean_repo
    git -C "$PROJECT_ROOT" push origin "HEAD:$BRANCH"
    log_ok "已显式 push：$BRANCH"
}

cmd_sync_remotes() {
    local head origin_head role dirty
    head="$(git -C "$PROJECT_ROOT" rev-parse HEAD)"
    git -C "$PROJECT_ROOT" fetch origin "$BRANCH"
    origin_head="$(git -C "$PROJECT_ROOT" rev-parse "origin/$BRANCH")"
    [ "$head" = "$origin_head" ] || die "本机 HEAD 与 origin/$BRANCH 不一致，拒绝同步远端"
    for role in client1 client2; do
        dirty="$(remote "$role" "cd '$REMOTE_REPO' && git status --short")"
        if [ -n "$dirty" ] && [ "${ISSUE41_FORCE_REMOTE_RESET:-0}" != "1" ]; then
            die "$role 工作区不干净；需 ISSUE41_FORCE_REMOTE_RESET=1 才允许 reset"
        fi
        remote "$role" "cd '$REMOTE_REPO' && git fetch origin '$BRANCH' && git checkout -B '$BRANCH' 'origin/$BRANCH' && git reset --hard 'origin/$BRANCH'"
        log_ok "$role 已同步到 $BRANCH@$head"
    done
}

find_wlx() {
    iw dev | awk '/Interface / {print $2}' | grep '^wlx' || true
}

radio_usb_speed_file() {
    local iface="$1" device_path
    device_path="$(readlink -f "/sys/class/net/$iface/device" 2>/dev/null || true)"
    [ -n "$device_path" ] || return 1
    printf '%s/speed\n' "$(dirname "$device_path")"
}

capture_local_radio_health() {
    local role="$1" iface="$2" dir speed_file
    dir="${3:-$ARCHIVE_DIR/orchestration/radio-health/$role}"
    mkdir -p "$dir"
    printf '%s\n' "$iface" > "$dir/interface.txt"
    readlink -f "/sys/class/net/$iface/device" > "$dir/sysfs-device.txt" 2>&1 || true
    readlink -f "/sys/class/net/$iface/device/driver" > "$dir/driver.txt" 2>&1 || true
    speed_file="$(radio_usb_speed_file "$iface" || true)"
    if [ -n "$speed_file" ] && [ -r "$speed_file" ]; then
        cat "$speed_file" > "$dir/usb-speed.txt"
    else
        printf 'unknown\n' > "$dir/usb-speed.txt"
    fi
    iw dev "$iface" info > "$dir/iw-info.txt" 2>&1 || true
    ip -s link show "$iface" > "$dir/ip-link-stats.txt" 2>&1 || true
    rfkill list > "$dir/rfkill.txt" 2>&1 || true
    lsusb -t > "$dir/usb-topology.txt" 2>&1 || true
    sudo journalctl -k --no-pager 2>/dev/null | grep -E "$iface|not running at top speed|USB disconnect|new (full|high|super)-speed USB device" | tail -200 > "$dir/kernel-radio.log" || true
}

capture_remote_radio_health() {
    local role="$1" iface="$2" dir remote_dir archive
    archive="${3:-$ARCHIVE_DIR/orchestration/radio-health/$role}"
    remote_dir="/tmp/issue41-radio-health-$role"
    mkdir -p "$archive"
    remote "$role" "rm -rf '$remote_dir'; mkdir -p '$remote_dir'; printf '%s\\n' '$iface' > '$remote_dir/interface.txt'; device_path=\$(readlink -f '/sys/class/net/$iface/device' 2>/dev/null || true); printf '%s\\n' \"\$device_path\" > '$remote_dir/sysfs-device.txt'; readlink -f '/sys/class/net/$iface/device/driver' > '$remote_dir/driver.txt' 2>&1 || true; speed_file=\$(dirname \"\$device_path\")/speed; if [ -n \"\$device_path\" ] && [ -r \"\$speed_file\" ]; then cat \"\$speed_file\" > '$remote_dir/usb-speed.txt'; else printf 'unknown\\n' > '$remote_dir/usb-speed.txt'; fi; iw dev '$iface' info > '$remote_dir/iw-info.txt' 2>&1 || true; ip -s link show '$iface' > '$remote_dir/ip-link-stats.txt' 2>&1 || true; rfkill list > '$remote_dir/rfkill.txt' 2>&1 || true; lsusb -t > '$remote_dir/usb-topology.txt' 2>&1 || true; sudo journalctl -k --no-pager 2>/dev/null | grep -E '$iface|not running at top speed|USB disconnect|new (full|high|super)-speed USB device' | tail -200 > '$remote_dir/kernel-radio.log' || true; tar -C '$remote_dir' -czf '$remote_dir.tgz' ."
    scp -q "$(client_ssh "$role"):$remote_dir.tgz" "$archive/radio-health.tgz"
    tar -C "$archive" -xzf "$archive/radio-health.tgz"
}

check_radio_usb_speed() {
    local role="$1" speed_file speed
    speed_file="$ARCHIVE_DIR/orchestration/radio-health/$role/usb-speed.txt"
    speed="$(cat "$speed_file" 2>/dev/null || printf 'unknown')"
    if ! [[ "$speed" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
        if [ "$STRICT_USB_SPEED" = "1" ]; then
            die "$role 无法确认无线网卡 USB speed；严格模式拒绝继续"
        fi
        log_warn "$role 无法确认无线网卡 USB speed，已归档为 unknown"
        return
    fi
    if awk -v actual="$speed" -v minimum="$RADIO_MIN_USB_SPEED" 'BEGIN { exit !(actual < minimum) }'; then
        if [ "$STRICT_USB_SPEED" = "1" ]; then
            die "$role 无线网卡 USB speed=${speed}Mbit/s，低于严格阈值 ${RADIO_MIN_USB_SPEED}Mbit/s"
        fi
        log_warn "$role 无线网卡 USB speed=${speed}Mbit/s，低于建议值 ${RADIO_MIN_USB_SPEED}Mbit/s；继续执行但结果存在硬件不稳定风险"
    else
        log_ok "$role 无线网卡 USB speed=${speed}Mbit/s"
    fi
}

cmd_preflight() {
    init_envelope
    local last_successful="none"
    run_local_capture local-git git -C "$PROJECT_ROOT" status --short --branch

    # Layer 1: repo_and_version
    local head remote_head remote_status
    local branch
    branch="$(git -C "$PROJECT_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '')"
    [ "$branch" = "$BRANCH" ] || record_preflight_failure "repo_and_version" "implementation" "server 当前分支 $branch 不是 $BRANCH" "$last_successful"
    local local_dirty
    local_dirty="$(git -C "$PROJECT_ROOT" status --short)"
    [ -z "$local_dirty" ] || record_preflight_failure "repo_and_version" "implementation" "server 工作区不干净，拒绝继续：$local_dirty" "$last_successful"
    head="$(git -C "$PROJECT_ROOT" rev-parse HEAD)"
    for role in client1 client2; do
        remote_head="$(remote "$role" "cd '$REMOTE_REPO' && git rev-parse HEAD" 2>/dev/null || echo '')"
        [ "$remote_head" = "$head" ] || record_preflight_failure "repo_and_version" "implementation" "$role commit 与本机不一致：$remote_head != $head" "$last_successful"
        remote_status="$(remote "$role" "cd '$REMOTE_REPO' && git status --short" 2>/dev/null || echo '')"
        [ -z "$remote_status" ] || record_preflight_failure "repo_and_version" "implementation" "$role 工作区不干净：$remote_status" "$last_successful"
        remote "$role" "cd '$REMOTE_REPO' && test \"\$(git rev-parse --abbrev-ref HEAD)\" = '$BRANCH'" || record_preflight_failure "repo_and_version" "implementation" "$role 分支不是 $BRANCH" "$last_successful"
    done
    last_successful="repo_and_version"

    # Layer 2: ssh_and_sudo
    for role in client1 client2; do
        remote "$role" "true" >/dev/null 2>&1 || record_preflight_failure "ssh_and_sudo" "environment" "SSH 到 $role 失败" "$last_successful"
        remote "$role" "sudo -n true" >/dev/null 2>&1 || record_preflight_failure "ssh_and_sudo" "environment" "$role sudo -n true 失败" "$last_successful"
    done
    sudo -n true || record_preflight_failure "ssh_and_sudo" "environment" "本机 sudo -n true 失败" "$last_successful"
    last_successful="ssh_and_sudo"

    # Layer 3: dependencies
    for name in ip iw systemctl journalctl make python3 uftp uftpd; do
        command -v "$name" >/dev/null 2>&1 || record_preflight_failure "dependencies" "tooling" "本机缺少命令：$name" "$last_successful"
    done
    for role in client1 client2; do
        remote "$role" "command -v ip iw systemctl journalctl make python3 uftp uftpd >/dev/null" || record_preflight_failure "dependencies" "tooling" "$role 缺少命令依赖" "$last_successful"
    done
    [ -f "$INITIAL_MODEL_PATH" ] && [ -r "$INITIAL_MODEL_PATH" ] || \
        record_preflight_failure "dependencies" "tooling" "server 初始模型不是可读取普通文件：$INITIAL_MODEL_PATH" "$last_successful"
    [ "$(stat -c %s "$INITIAL_MODEL_PATH")" -eq "$INPUT_SIZE_BYTES" ] || \
        record_preflight_failure "dependencies" "tooling" "server 初始模型必须恰好为 4 MiB：$INITIAL_MODEL_PATH" "$last_successful"
    local update_template_path client1_template_sha256 client2_template_sha256
    for role in client1 client2; do
        case "$role" in
            client1) update_template_path="$CLIENT1_UPDATE_TEMPLATE_PATH" ;;
            client2) update_template_path="$CLIENT2_UPDATE_TEMPLATE_PATH" ;;
        esac
        remote "$role" "sudo test -f '$update_template_path' && sudo test -r '$update_template_path' && test \"\$(sudo stat -c %s '$update_template_path')\" -eq '$INPUT_SIZE_BYTES'" || record_preflight_failure "dependencies" "tooling" "$role update 模板必须是可读取的 4 MiB 普通文件：$update_template_path" "$last_successful"
        case "$role" in
            client1) client1_template_sha256="$(remote "$role" "sudo sha256sum '$update_template_path' | awk '{print \$1}'")" ;;
            client2) client2_template_sha256="$(remote "$role" "sudo sha256sum '$update_template_path' | awk '{print \$1}'")" ;;
        esac
        log_ok "$role preflight 基础检查通过"
    done
    [ "$client1_template_sha256" != "$client2_template_sha256" ] || record_preflight_failure "dependencies" "tooling" "两个 client 的 4 MiB update 模板 SHA-256 必须不同" "$last_successful"
    last_successful="dependencies"

    # Layer 4: wireless_usb & 重新发现拓扑
    python3 "$SCRIPT_DIR/issue41_envelope.py" discover-topology --archive-dir "$ARCHIVE_DIR" || record_preflight_failure "wireless_usb" "environment" "动态拓扑重新发现失败" "$last_successful"
    local ifaces
    ifaces="$(find_wlx)"
    [ "$(printf '%s\n' "$ifaces" | grep -c '^wlx' || true)" -eq 1 ] || record_preflight_failure "wireless_usb" "environment" "本机必须恰好发现一个 wlx* 网卡" "$last_successful"
    capture_local_radio_health server "$ifaces"
    check_radio_usb_speed server
    local remote_ifaces remote_iface_count
    for role in client1 client2; do
        remote_ifaces="$(remote "$role" "iw dev | awk '/Interface / {print \$2}' | grep '^wlx' || true")"
        remote_iface_count="$(printf '%s\n' "$remote_ifaces" | grep -c '^wlx' || true)"
        [ "$remote_iface_count" -eq 1 ] || record_preflight_failure "wireless_usb" "environment" "$role 必须恰好发现一个 wlx* 网卡" "$last_successful"
        capture_remote_radio_health "$role" "$remote_ifaces"
        check_radio_usb_speed "$role"
    done
    last_successful="wireless_usb"

    # Layer 5: radio_monitor_channel
    last_successful="radio_monitor_channel"

    # Layer 6: network_ports_and_tun
    for port in "$UFTP_PORT" "$HTTP_PORT"; do
        if ss -tuln 2>/dev/null | grep -qE ":${port}\b"; then
            record_preflight_failure "network_ports_and_tun" "environment" "server 端口 $port 已被占用" "$last_successful"
        fi
        for role in client1 client2; do
            if remote "$role" "ss -tuln 2>/dev/null" | grep -qE ":${port}\b"; then
                record_preflight_failure "network_ports_and_tun" "environment" "$role 端口 $port 已被占用" "$last_successful"
            fi
        done
    done
    if [ -e "/sys/class/net/$SERVER_TUN" ]; then
        record_preflight_failure "network_ports_and_tun" "environment" "server 上预期的 TUN 设备 $SERVER_TUN 已存在" "$last_successful"
    fi
    for role in client1 client2; do
        local tun
        tun="$(client_tun "$role")"
        if remote "$role" "[ -e '/sys/class/net/$tun' ]"; then
            record_preflight_failure "network_ports_and_tun" "environment" "$role 上预期的 TUN 设备 $tun 已存在" "$last_successful"
        fi
    done
    last_successful="network_ports_and_tun"

    # Layer 7: residual_processes
    if pgrep -x wfb-fl-server >/dev/null 2>&1 || pgrep -x wfb_v6_uplink >/dev/null 2>&1 || pgrep -x uftp >/dev/null 2>&1 || pgrep -x uftpd >/dev/null 2>&1; then
        record_preflight_failure "residual_processes" "environment" "server 发现残留 issue41 进程" "$last_successful"
    fi
    for role in client1 client2; do
        if remote "$role" "pgrep -x wfb-fl-client >/dev/null 2>&1 || pgrep -x wfb_v6_uplink >/dev/null 2>&1 || pgrep -x uftp >/dev/null 2>&1 || pgrep -x uftpd >/dev/null 2>&1"; then
            record_preflight_failure "residual_processes" "environment" "$role 发现残留 issue41 进程" "$last_successful"
        fi
    done
    last_successful="residual_processes"

    # Layer 8: managed_directory_boundary
    if runtime_state_exists_local || runtime_state_exists_remote client1 || runtime_state_exists_remote client2; then
        if [ "$RESET_RUNTIME_STATE" != "1" ]; then
            record_preflight_failure "managed_directory_boundary" "tooling" "检测到上次 Runtime 现场；默认拒绝复用，请先执行 clean，或设置 ISSUE41_RESET_RUNTIME_STATE=1" "$last_successful"
        fi
        sudo rm -rf /var/lib/wfb-ng/issue41/server
        for role in client1 client2; do
            remote "$role" "sudo rm -rf /var/lib/wfb-ng/issue41/client"
        done
        log_warn "已按 ISSUE41_RESET_RUNTIME_STATE=1 清理旧 Runtime work_dir"
    fi
    last_successful="managed_directory_boundary"

    cat > "$ARCHIVE_DIR/orchestration/preflight_result.json" <<EOF
{"status":"passed","last_successful_layer":"$last_successful","first_failing_layer":null,"failure_category":null,"failure_reason":null}
EOF
    log_ok "preflight 通过"
}

cmd_generate_fixtures() {
    log_info "生成确定性验收 fixture (4 MiB)..."
    sudo install -d -m 0777 "$(dirname "$INITIAL_MODEL_PATH")"
    python3 -m wfb_ng.fl.issue41_fixtures generate --dest-dir "$(dirname "$INITIAL_MODEL_PATH")" --role model --output "$INITIAL_MODEL_PATH" --size "$INPUT_SIZE_BYTES"
    log_ok "server 初始模型生成完成：$INITIAL_MODEL_PATH"

    remote client1 "sudo install -d -m 0777 '$(dirname "$CLIENT1_UPDATE_TEMPLATE_PATH")' && cd '$REMOTE_REPO' && python3 -m wfb_ng.fl.issue41_fixtures generate --dest-dir '$(dirname "$CLIENT1_UPDATE_TEMPLATE_PATH")' --role client1 --output '$CLIENT1_UPDATE_TEMPLATE_PATH' --size '$INPUT_SIZE_BYTES'"
    log_ok "client1 update 模板生成完成：$CLIENT1_UPDATE_TEMPLATE_PATH"

    remote client2 "sudo install -d -m 0777 '$(dirname "$CLIENT2_UPDATE_TEMPLATE_PATH")' && cd '$REMOTE_REPO' && python3 -m wfb_ng.fl.issue41_fixtures generate --dest-dir '$(dirname "$CLIENT2_UPDATE_TEMPLATE_PATH")' --role client2 --output '$CLIENT2_UPDATE_TEMPLATE_PATH' --size '$INPUT_SIZE_BYTES'"
    log_ok "client2 update 模板生成完成：$CLIENT2_UPDATE_TEMPLATE_PATH"
}

cmd_install() {
    make -C "$PROJECT_ROOT" build_v6
    sudo make -C "$PROJECT_ROOT" install_v8
    sudo systemctl daemon-reload
    for role in client1 client2; do
        remote "$role" "cd '$REMOTE_REPO' && make build_v6 && sudo make install_v8 && sudo systemctl daemon-reload"
        log_ok "$role 安装完成"
    done
    cmd_generate_fixtures
}

write_issue41_configs() {
    local role="$1" node_id tun addr work_dir algorithm result delay update_template_path ssh_target iface
    case "$role" in
        server)
            node_id=255; tun="$SERVER_TUN"; addr="$SERVER_TUN_ADDR"; work_dir=/var/lib/wfb-ng/issue41/server
            algorithm=wfb_ng.fl.issue41_algorithm:server_main; result="$work_dir/issue41-server-result.json"
            ;;
        client1)
            node_id=1; tun="$CLIENT1_TUN"; addr="$CLIENT1_TUN_ADDR"; work_dir=/var/lib/wfb-ng/issue41/client
            algorithm=wfb_ng.fl.issue41_algorithm:client_main; result="$work_dir/issue41-client1-result.json"; delay=0; update_template_path="$CLIENT1_UPDATE_TEMPLATE_PATH"
            ;;
        client2)
            node_id=2; tun="$CLIENT2_TUN"; addr="$CLIENT2_TUN_ADDR"; work_dir=/var/lib/wfb-ng/issue41/client
            algorithm=wfb_ng.fl.issue41_algorithm:client_main; result="$work_dir/issue41-client2-result.json"; delay=3000; update_template_path="$CLIENT2_UPDATE_TEMPLATE_PATH"
            ;;
    esac
    local tmp
    tmp="$(mktemp -d)"
    if [ "$role" = server ]; then
        cat > "$tmp/fl.json" <<EOF
{"schema_version":1,"role":"server","work_dir":"$work_dir","node_id":255,"participant_node_ids":[1,2],"participant_uftp_uids":[1,2],"server_uftp_uid":255,"uftp_port":$UFTP_PORT,"http_host":"$HTTP_HOST","http_port":$HTTP_PORT,"uftp_bind_host":"${SERVER_TUN_ADDR%/*}","uftp_multicast_host":"$UFTP_GROUP","uftp_private_multicast_host":"$UFTP_PRIVATE_GROUP","max_update_size_bytes":1073741824,"live_observation":true,"observation_path":"$work_dir/observation.jsonl","io_timeout_seconds":$IO_TIMEOUT_SECONDS,"link_args":["--tun-name","$tun","--tun-addr","$addr","--link-id","$LINK_ID","--uplink-stream","$UPLINK_STREAM","--downlink-stream","$DOWNLINK_STREAM","--fec-k","$FEC_K","--fec-n","$FEC_N","--radio-bandwidth","$RADIO_BANDWIDTH","--radio-mcs-index","$RADIO_MCS_INDEX","--log-interval","$LINK_LOG_INTERVAL_MS","--air-interface","$(find_wlx | head -n1)","--known-clients","1,2","--client-target","1:$(client_ip client1):127.0.0.1:1","--client-target","2:$(client_ip client2):127.0.0.1:1","--feedback-window-period-ms","$FEEDBACK_WINDOW_PERIOD_MS","--feedback-window-duration-ms","$FEEDBACK_WINDOW_DURATION_MS",]}
EOF
        cat > "$tmp/algorithm.json" <<EOF
{"rounds":$ROUNDS,"participant_node_ids":[1,2],"initial_model_path":"$INITIAL_MODEL_PATH","required_artifact_size_bytes":$INPUT_SIZE_BYTES,"aggregation_delay_ms":$AGGREGATION_DELAY_MS,"result_path":"$result"}
EOF
        sudo install -d /etc/wfb-ng/issue41 /etc/systemd/system/wfb-fl-server.service.d
        sudo install -m 0644 "$tmp/fl.json" /etc/wfb-ng/issue41/fl-server.json
        sudo install -m 0644 "$tmp/algorithm.json" /etc/wfb-ng/issue41/server-algorithm.json
        printf '[Service]\nRestart=no\nLogRateLimitIntervalSec=0\nExecStart=\nExecStart=/usr/bin/wfb-fl-server --config /etc/wfb-ng/issue41/fl-server.json --algorithm %s --algorithm-config /etc/wfb-ng/issue41/server-algorithm.json\n' "$algorithm" | sudo tee /etc/systemd/system/wfb-fl-server.service.d/issue41.conf >/dev/null
    else
        iface="$(remote "$role" "iw dev | awk '/Interface / {print \$2}' | grep '^wlx' || true")"
        [ "$(printf '%s\n' "$iface" | grep -c '^wlx' || true)" -eq 1 ] || die "$role 必须恰好发现一个 wlx* 网卡"
        cat > "$tmp/fl.json" <<EOF
{"schema_version":1,"role":"client","work_dir":"$work_dir","node_id":$node_id,"uftp_uid":$node_id,"uftp_port":$UFTP_PORT,"server_http_host":"$HTTP_HOST","server_http_port":$HTTP_PORT,"uftp_bind_host":"${addr%/*}","uftp_multicast_host":"$UFTP_GROUP","uftp_private_multicast_host":"$UFTP_PRIVATE_GROUP","max_update_size_bytes":1073741824,"live_observation":true,"observation_path":"$work_dir/observation.jsonl","io_timeout_seconds":$IO_TIMEOUT_SECONDS,"link_args":["--tun-name","$tun","--tun-addr","$addr","--link-id","$LINK_ID","--uplink-stream","$UPLINK_STREAM","--downlink-stream","$DOWNLINK_STREAM","--fec-k","$FEC_K","--fec-n","$FEC_N","--radio-bandwidth","$RADIO_BANDWIDTH","--radio-mcs-index","$RADIO_MCS_INDEX","--log-interval","$LINK_LOG_INTERVAL_MS","--air-interface","$iface"]}
EOF
        cat > "$tmp/algorithm.json" <<EOF
{"rounds":$ROUNDS,"node_id":$node_id,"update_template_path":"$update_template_path","required_artifact_size_bytes":$INPUT_SIZE_BYTES,"training_delay_ms":$delay,"result_path":"$result"}
EOF
        ssh_target="$(client_ssh "$role")"
        ssh -o BatchMode=yes "$ssh_target" "sudo install -d /etc/wfb-ng/issue41 /etc/systemd/system/wfb-fl-client.service.d"
        scp -q "$tmp/fl.json" "$ssh_target:/tmp/issue41-fl.json"
        scp -q "$tmp/algorithm.json" "$ssh_target:/tmp/issue41-algorithm.json"
        ssh -o BatchMode=yes "$ssh_target" "sudo install -m 0644 /tmp/issue41-fl.json /etc/wfb-ng/issue41/fl-client.json && sudo install -m 0644 /tmp/issue41-algorithm.json /etc/wfb-ng/issue41/client-algorithm.json && printf '[Service]\nRestart=no\nLogRateLimitIntervalSec=0\nExecStart=\nExecStart=/usr/bin/wfb-fl-client --config /etc/wfb-ng/issue41/fl-client.json --algorithm $algorithm --algorithm-config /etc/wfb-ng/issue41/client-algorithm.json\n' | sudo tee /etc/systemd/system/wfb-fl-client.service.d/issue41.conf >/dev/null"
    fi
    rm -rf "$tmp"
}

smoke_dir() { printf '/var/tmp/wfb-ng-issue41-smoke/%s' "$1"; }
smoke_archive_dir() { printf '%s/pre_runtime_smoke/%s' "$ARCHIVE_DIR" "$1"; }

wait_local_tun() {
    local tun="$1" deadline=$((SECONDS + 15))
    while [ "$SECONDS" -lt "$deadline" ]; do
        [ -d "/sys/class/net/$tun" ] && return 0
        sleep 0.2
    done
    return 1
}

wait_remote_tun() {
    local role="$1" tun="$2"
    remote "$role" "deadline=\$((SECONDS + 15)); while [ \$SECONDS -lt \$deadline ]; do [ -d '/sys/class/net/$tun' ] && exit 0; sleep 0.2; done; exit 1"
}

wait_local_issue41_cleanup() {
    local deadline=$((SECONDS + STOP_CLEANUP_TIMEOUT_SECONDS))
    while [ "$SECONDS" -lt "$deadline" ]; do
        if ! pgrep -x wfb-fl-server >/dev/null && \
           ! pgrep -x wfb_v6_uplink >/dev/null && \
           ! pgrep -x uftp >/dev/null && \
           ! pgrep -x uftpd >/dev/null && \
           [ ! -e "/sys/class/net/$SERVER_TUN" ]; then
            return 0
        fi
        sleep 0.2
    done
    printf '本机 issue41 停止后仍有残留：\n' >&2
    for process in wfb-fl-server wfb_v6_uplink uftp uftpd; do
        pgrep -a -x "$process" >&2 || true
    done
    ip link show "$SERVER_TUN" >&2 2>&1 || true
    return 1
}

wait_remote_issue41_cleanup() {
    local role="$1" tun="$2"
    remote "$role" "deadline=\$((SECONDS + $STOP_CLEANUP_TIMEOUT_SECONDS)); while [ \$SECONDS -lt \$deadline ]; do if ! pgrep -x wfb-fl-client >/dev/null && ! pgrep -x wfb_v6_uplink >/dev/null && ! pgrep -x uftp >/dev/null && ! pgrep -x uftpd >/dev/null && [ ! -e '/sys/class/net/$tun' ]; then exit 0; fi; sleep 0.2; done; printf '%s\n' 'issue41 停止后仍有残留：' >&2; for process in wfb-fl-client wfb_v6_uplink uftp uftpd; do pgrep -a -x \"\$process\" >&2 || true; done; ip link show '$tun' >&2 2>&1 || true; exit 1"
}

wait_remote_service_ready() {
    local role="$1" unit="$2"
    remote "$role" "sudo systemctl restart '$unit' && sudo systemctl is-active --quiet '$unit'"
}

assert_remote_service_active() {
    local role="$1" unit="$2"
    remote "$role" "sudo systemctl is-active --quiet '$unit'"
}

assert_runtime_uftp_route() {
    local role="$1" tun="$2" source_ip="$3" group="$4" route
    if [ "$role" = server ]; then
        route="$(ip route show "$group/32" 2>&1)" || die "server 无法查询 UFTP 组播路由：$route"
        case " $route " in
            *"$group dev $tun"*) ;;
            *) die "server UFTP 精确组播路由未指向 $tun：$route" ;;
        esac
        route="$(ip route get "$group" from "$source_ip" 2>&1)" || die "server 无法查询 UFTP 源地址路由：$route"
        case " $route " in
            *" dev $tun "*) ;;
            *) die "server UFTP 源地址路由未指向 $tun：$route" ;;
        esac
    else
        remote "$role" "route=\$(ip route show '$group/32' 2>&1) || { printf '%s\\n' \"$role 无法查询 UFTP 组播路由：\$route\" >&2; exit 1; }; case \" \$route \" in *\"$group dev $tun\"*) ;; *) printf '%s\\n' \"$role UFTP 精确组播路由未指向 $tun：\$route\" >&2; exit 1 ;; esac; route=\$(ip route get '$group' from '$source_ip' 2>&1) || { printf '%s\\n' \"$role 无法查询 UFTP 源地址路由：\$route\" >&2; exit 1; }; case \" \$route \" in *\" dev $tun \"*) ;; *) printf '%s\\n' \"$role UFTP 源地址路由未指向 $tun：\$route\" >&2; exit 1 ;; esac"
    fi
}

assert_runtime_uftp_routes() {
    local role tun source_ip group
    for group in "$UFTP_GROUP" "$UFTP_PRIVATE_GROUP"; do
        assert_runtime_uftp_route server "$SERVER_TUN" "${SERVER_TUN_ADDR%/*}" "$group"
        for role in client1 client2; do
            tun="$(client_tun "$role")"
            source_ip="$(client_ip "$role")"
            assert_runtime_uftp_route "$role" "$tun" "$source_ip" "$group"
        done
    done
}

wait_runtime_result() {
    local role="$1" path="$2" deadline
    if [ "$role" = server ]; then
        deadline=$((SECONDS + RUNTIME_TIMEOUT_SECONDS))
        while [ "$SECONDS" -lt "$deadline" ]; do
            if [ -f "$path" ]; then
                sudo python3 -c "import json, sys; sys.exit(0 if json.load(open(sys.argv[1], encoding='utf-8')).get('conclusion') == 'succeeded' else 1)" "$path" || die "server Runtime 作业未成功"
                return
            fi
            sleep 0.2
        done
    else
        remote "$role" "deadline=\$((SECONDS + $RUNTIME_TIMEOUT_SECONDS)); while [ \$SECONDS -lt \$deadline ]; do if [ -f '$path' ]; then sudo python3 -c \"import json, sys; sys.exit(0 if json.load(open(sys.argv[1], encoding='utf-8')).get('conclusion') == 'succeeded' else 1)\" '$path'; exit \$?; fi; sleep 0.2; done; exit 1" || die "$role Runtime 作业未在期限内成功"
        return
    fi
    die "server Runtime 作业未在期限内成功"
}

wait_runtime_results() {
    wait_runtime_result server /var/lib/wfb-ng/issue41/server/issue41-server-result.json
    wait_runtime_result client1 /var/lib/wfb-ng/issue41/client/issue41-client1-result.json
    wait_runtime_result client2 /var/lib/wfb-ng/issue41/client/issue41-client2-result.json
}

write_smoke_marker() {
    local name="$1" marker_dir
    marker_dir="$(smoke_archive_dir "$name")"
    mkdir -p "$marker_dir"
    cat > "$marker_dir/passed.json" <<EOF
{"schema_version":1,"smoke":"$name","status":"passed","run_id":"$RUN_ID","data_plane":"10.80.0.0/24","server_tun":"$SERVER_TUN","client_tuns":["$CLIENT1_TUN","$CLIENT2_TUN"]}
EOF
    cp -f "$marker_dir/passed.json" "$ARCHIVE_DIR/pre_runtime_smoke/passed.json" 2>/dev/null || true
}

issue41_wfb_short_gi_arg() {
    if [ "$RADIO_SHORT_GI" = "1" ]; then
        printf '%s\n' '--radio-short-gi'
    fi
}

configure_local_monitor() {
    local iface="$1" archive="$2"
    sudo ip link set "$iface" down || true
    sudo iw dev "$iface" set type monitor
    sudo ip link set "$iface" up
    sudo iw dev "$iface" set channel "$CHANNEL" "$CHANNEL_WIDTH"
    ip -br link show "$iface" > "$archive/ip-link.txt" 2>&1 || true
    iw dev "$iface" info > "$archive/iw-info.txt" 2>&1 || true
}

configure_remote_monitor() {
    local role="$1" iface="$2" dir="$3"
    remote "$role" "sudo ip link set '$iface' down || true; sudo iw dev '$iface' set type monitor; sudo ip link set '$iface' up; sudo iw dev '$iface' set channel '$CHANNEL' '$CHANNEL_WIDTH'; ip -br link show '$iface' > '$dir/ip-link.txt' 2>&1 || true; iw dev '$iface' info > '$dir/iw-info.txt' 2>&1 || true"
}

configure_runtime_monitors() {
    local server_iface client1_iface client2_iface server_archive client1_archive client2_archive
    server_iface="$(find_wlx)"
    client1_iface="$(remote client1 "iw dev | awk '/Interface / {print \$2}' | grep '^wlx' || true")"
    client2_iface="$(remote client2 "iw dev | awk '/Interface / {print \$2}' | grep '^wlx' || true")"
    [ "$(printf '%s\n' "$server_iface" | grep -c '^wlx' || true)" -eq 1 ] || die "server 必须恰好发现一个 wlx* 网卡"
    [ "$(printf '%s\n' "$client1_iface" | grep -c '^wlx' || true)" -eq 1 ] || die "client1 必须恰好发现一个 wlx* 网卡"
    [ "$(printf '%s\n' "$client2_iface" | grep -c '^wlx' || true)" -eq 1 ] || die "client2 必须恰好发现一个 wlx* 网卡"

    server_archive="$ARCHIVE_DIR/formal_runtime_loop/server/radio-health"
    client1_archive="$ARCHIVE_DIR/formal_runtime_loop/client1/radio-health"
    client2_archive="$ARCHIVE_DIR/formal_runtime_loop/client2/radio-health"
    mkdir -p "$server_archive" "$client1_archive" "$client2_archive"
    remote client1 "rm -rf /tmp/issue41-runtime-radio; mkdir -p /tmp/issue41-runtime-radio"
    remote client2 "rm -rf /tmp/issue41-runtime-radio; mkdir -p /tmp/issue41-runtime-radio"

    configure_local_monitor "$server_iface" "$server_archive"
    configure_remote_monitor client1 "$client1_iface" /tmp/issue41-runtime-radio
    configure_remote_monitor client2 "$client2_iface" /tmp/issue41-runtime-radio

    iw dev "$server_iface" info | grep -q 'type monitor' || die "server 空口网卡未进入 monitor 模式"
    ip link show "$server_iface" | grep -q '<[^>]*UP' || die "server 空口网卡未启动"
    remote client1 "iw dev '$client1_iface' info | grep -q 'type monitor' && ip link show '$client1_iface' | grep -q '<[^>]*UP'" || die "client1 空口网卡 monitor/UP 验证失败"
    remote client2 "iw dev '$client2_iface' info | grep -q 'type monitor' && ip link show '$client2_iface' | grep -q '<[^>]*UP'" || die "client2 空口网卡 monitor/UP 验证失败"

    capture_local_radio_health server "$server_iface" "$server_archive"
    capture_remote_radio_health client1 "$client1_iface" "$client1_archive"
    capture_remote_radio_health client2 "$client2_iface" "$client2_archive"
}

start_smoke_wfb() {
    local name="$1" server_dir client1_dir client2_dir server_iface client1_iface client2_iface short_gi
    server_dir="$(smoke_dir "$name")/server"
    client1_dir="$(smoke_dir "$name")/client1"
    client2_dir="$(smoke_dir "$name")/client2"
    cmd_stop_all
    sudo rm -rf "$(smoke_dir "$name")"
    sudo install -d "$server_dir"
    sudo chown "$(id -u):$(id -g)" "$server_dir"
    for role in client1 client2; do
        remote "$role" "sudo rm -rf '$(smoke_dir "$name")' && sudo install -d '$(smoke_dir "$name")/$role' && sudo chown \$(id -u):\$(id -g) '$(smoke_dir "$name")/$role'"
    done
    server_iface="$(find_wlx)"
    [ "$(printf '%s\n' "$server_iface" | grep -c '^wlx' || true)" -eq 1 ] || die "本机必须恰好发现一个 wlx* 网卡"
    client1_iface="$(remote client1 "iw dev | awk '/Interface / {print \$2}' | grep '^wlx' || true")"
    client2_iface="$(remote client2 "iw dev | awk '/Interface / {print \$2}' | grep '^wlx' || true")"
    [ "$(printf '%s\n' "$client1_iface" | grep -c '^wlx' || true)" -eq 1 ] || die "client1 必须恰好发现一个 wlx* 网卡"
    [ "$(printf '%s\n' "$client2_iface" | grep -c '^wlx' || true)" -eq 1 ] || die "client2 必须恰好发现一个 wlx* 网卡"
    configure_local_monitor "$server_iface" "$server_dir"
    configure_remote_monitor client1 "$client1_iface" "$client1_dir"
    configure_remote_monitor client2 "$client2_iface" "$client2_dir"
    capture_local_radio_health server "$server_iface" "$(smoke_archive_dir "$name")/server/radio-health"
    capture_remote_radio_health client1 "$client1_iface" "$(smoke_archive_dir "$name")/client1/radio-health"
    capture_remote_radio_health client2 "$client2_iface" "$(smoke_archive_dir "$name")/client2/radio-health"
    short_gi="$(issue41_wfb_short_gi_arg)"

    sudo bash -c "cd '$PROJECT_ROOT' || exit 1; nohup '$PROJECT_ROOT/wfb_v6_uplink' --role server --tun-name '$SERVER_TUN' --tun-addr '$SERVER_TUN_ADDR' --node-id 255 --link-id '$LINK_ID' --uplink-stream '$UPLINK_STREAM' --downlink-stream '$DOWNLINK_STREAM' --fec-k '$FEC_K' --fec-n '$FEC_N' --radio-bandwidth '$RADIO_BANDWIDTH' --radio-mcs-index '$RADIO_MCS_INDEX' $short_gi --air-interface '$server_iface' --known-clients '1,2' --client-target '1:$(client_ip client1):127.0.0.1:1' --client-target '2:$(client_ip client2):127.0.0.1:1' --grant-duration-ms 120 --guard-interval-ms 20 --downlink-pause-threshold-bytes 131072 --downlink-resume-threshold-bytes 65536 --downlink-queue-packets-limit 64 --queue-summary-file '$server_dir/server_queue_summary.json' --log-interval 200 < /dev/null > '$server_dir/wfb.log' 2>&1 & echo \$! > '$server_dir/wfb.pid'; exit 0"
    remote client1 "sudo bash -c \"cd '$REMOTE_REPO' || exit 1; nohup '$REMOTE_REPO/wfb_v6_uplink' --role client --tun-name '$CLIENT1_TUN' --tun-addr '$CLIENT1_TUN_ADDR' --node-id 1 --link-id '$LINK_ID' --uplink-stream '$UPLINK_STREAM' --downlink-stream '$DOWNLINK_STREAM' --fec-k '$FEC_K' --fec-n '$FEC_N' --radio-bandwidth '$RADIO_BANDWIDTH' --radio-mcs-index '$RADIO_MCS_INDEX' $short_gi --air-interface '$client1_iface' --uplink-pause-threshold-bytes 131072 --uplink-resume-threshold-bytes 65536 --uplink-queue-packets-limit 64 --queue-summary-file '$client1_dir/client1_queue_summary.json' --log-interval 200 < /dev/null > '$client1_dir/wfb.log' 2>&1 & echo \\\$! > '$client1_dir/wfb.pid'; exit 0\""
    remote client2 "sudo bash -c \"cd '$REMOTE_REPO' || exit 1; nohup '$REMOTE_REPO/wfb_v6_uplink' --role client --tun-name '$CLIENT2_TUN' --tun-addr '$CLIENT2_TUN_ADDR' --node-id 2 --link-id '$LINK_ID' --uplink-stream '$UPLINK_STREAM' --downlink-stream '$DOWNLINK_STREAM' --fec-k '$FEC_K' --fec-n '$FEC_N' --radio-bandwidth '$RADIO_BANDWIDTH' --radio-mcs-index '$RADIO_MCS_INDEX' $short_gi --air-interface '$client2_iface' --uplink-pause-threshold-bytes 131072 --uplink-resume-threshold-bytes 65536 --uplink-queue-packets-limit 64 --queue-summary-file '$client2_dir/client2_queue_summary.json' --log-interval 200 < /dev/null > '$client2_dir/wfb.log' 2>&1 & echo \\\$! > '$client2_dir/wfb.pid'; exit 0\""

    wait_local_tun "$SERVER_TUN" || die "server smoke TUN 未出现：$SERVER_TUN"
    wait_remote_tun client1 "$CLIENT1_TUN" || die "client1 smoke TUN 未出现：$CLIENT1_TUN"
    wait_remote_tun client2 "$CLIENT2_TUN" || die "client2 smoke TUN 未出现：$CLIENT2_TUN"
    local group
    for group in "$UFTP_GROUP" "$UFTP_PRIVATE_GROUP"; do
        sudo ip route replace "$group/32" dev "$SERVER_TUN"
        remote client1 "sudo ip route replace '$group/32' dev '$CLIENT1_TUN'"
        remote client2 "sudo ip route replace '$group/32' dev '$CLIENT2_TUN'"
    done
}

collect_smoke_evidence() {
    local name="$1" archive smoke_root
    archive="$(smoke_archive_dir "$name")"
    smoke_root="$(smoke_dir "$name")"
    mkdir -p "$archive/server" "$archive/client1" "$archive/client2"
    sudo tar -C "$smoke_root/server" -czf /tmp/issue41-smoke-server.tgz . 2>/dev/null || true
    if [ -f /tmp/issue41-smoke-server.tgz ]; then
        tar -C "$archive/server" -xzf /tmp/issue41-smoke-server.tgz || true
    fi
    for role in client1 client2; do
        remote "$role" "sudo tar -C '$smoke_root/$role' -czf /tmp/issue41-smoke-$role.tgz . 2>/dev/null || true"
        if scp -q "$(client_ssh "$role"):/tmp/issue41-smoke-$role.tgz" "$archive/$role/$role.tgz" 2>/dev/null; then
            tar -C "$archive/$role" -xzf "$archive/$role/$role.tgz" || true
        fi
    done
}

write_downlink_failure_diagnosis() {
    local archive
    archive="$(smoke_archive_dir downlink_uftp)"
    mkdir -p "$archive"
    python3 - "$archive" <<'PY'
import json
import os
import sys

archive = sys.argv[1]

def read_text(path):
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as fh:
            return fh.read()
    except OSError:
        return ''

server_log = read_text(os.path.join(archive, 'server', 'wfb.log'))
client_logs = {
    role: read_text(os.path.join(archive, role, 'wfb.log'))
    for role in ('client1', 'client2')
}
client_declared = {
    role: 'first_declare node_id=%s' % node_id in client_logs[role]
    for role, node_id in (('client1', 1), ('client2', 2))
}
server_accepted = {
    role: 'ready_accept node_id=%s' % node_id in server_log
    for role, node_id in (('client1', 1), ('client2', 2))
}
server_rx_ant_samples = server_log.count('\tRX_ANT\t')
classification = 'insufficient_evidence'
if all(client_declared.values()) and not any(server_accepted.values()) and server_rx_ant_samples == 0:
    classification = 'server_radio_receive_path_unhealthy_or_disconnected'
elif all(client_declared.values()) and not all(server_accepted.values()):
    classification = 'ready_delivery_incomplete'
result = {
    'schema_version': 1,
    'classification': classification,
    'client_declared_locally': client_declared,
    'server_ready_accepted': server_accepted,
    'server_rx_ant_samples': server_rx_ant_samples,
    'semantics': {
        'local_declare_is_not_server_accept': True,
        'missing_server_accept_is_not_a_sleep_transition': True,
        'strict_runtime_participants_may_not_be_downgraded': True,
    },
}
with open(os.path.join(archive, 'link-health-diagnosis.json'), 'w', encoding='utf-8') as fh:
    json.dump(result, fh, ensure_ascii=False, indent=2, sort_keys=True)
    fh.write('\n')
PY
}

stop_smoke_http_server() {
    local pid_file
    for pid_file in "$(smoke_dir gate)/server/http-server.pid" "$(smoke_dir uplink_http_put)/server/http-server.pid"; do
        if [ -f "$pid_file" ]; then
            kill "$(cat "$pid_file")" 2>/dev/null || true
        fi
    done
}

stop_smoke_gate_processes() {
    stop_smoke_http_server
    cmd_stop_all
}

verify_no_smoke_orphans() {
    local http_pid_file
    http_pid_file="$(smoke_dir gate)/server/http-server.pid"
    if pgrep -x wfb_v6_uplink >/dev/null || pgrep -x uftp >/dev/null || pgrep -x uftpd >/dev/null; then
        die "本机发现 smoke 孤儿进程"
    fi
    if [ -f "$http_pid_file" ] && kill -0 "$(cat "$http_pid_file")" 2>/dev/null; then
        die "本机发现 HTTP smoke receiver 孤儿进程"
    fi
    for role in client1 client2; do
        remote "$role" "! pgrep -x wfb_v6_uplink >/dev/null && ! pgrep -x uftp >/dev/null && ! pgrep -x uftpd >/dev/null"
    done
}

start_smoke_gate_environment() {
    local name=gate
    start_smoke_wfb "$name"

    # 启动 client1 和 client2 的 uftpd 接收端
    for role in client1 client2; do
        remote "$role" "sudo install -d '$(smoke_dir "$name")/$role/inbox' '$(smoke_dir "$name")/$role/tmp'; sudo bash -c \"nohup uftpd -d -q -I '$(client_ip "$role")' -M '$UFTP_GROUP' -p '$UFTP_PORT' -U '0x0000000${role#client}' -D '$(smoke_dir "$name")/$role/inbox' -T '$(smoke_dir "$name")/$role/tmp' -F '$(smoke_dir "$name")/$role/uftpd.status' > '$(smoke_dir "$name")/$role/uftpd.log' 2>&1 & echo \\\$! > '$(smoke_dir "$name")/$role/uftpd.pid'\""
    done

    # 启动 server 端 HTTP PUT receiver
    local server_dir
    server_dir="$(smoke_dir "$name")/server"
    python3 - "$server_dir" "${HTTP_HOST}" "$HTTP_PORT" <<'PY' &
import hashlib, http.server, json, os, sys, threading, time

server_dir, host, port = sys.argv[1], sys.argv[2], int(sys.argv[3])
os.makedirs(server_dir, exist_ok=True)
events_path = os.path.join(server_dir, 'server-put-events.jsonl')
lock = threading.Lock()
active_uploads = set()

def append_event(event):
    with open(events_path, 'a', encoding='utf-8') as fh:
        fh.write(json.dumps(event, separators=(',', ':')) + '\n')

class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def do_PUT(self):
        t0 = time.monotonic()
        client_ip = self.client_address[0]
        node_id = 1 if client_ip.endswith('.11') else (2 if client_ip.endswith('.12') else 1)
        with lock:
            active_uploads.add(node_id)
            append_event({'type': 'active_set', 'active_uploads': sorted(list(active_uploads))})

        length = int(self.headers.get('Content-Length', '0'))
        hasher = hashlib.sha256()
        read_bytes = 0
        while read_bytes < length:
            chunk = self.rfile.read(min(65536, length - read_bytes))
            if not chunk:
                break
            hasher.update(chunk)
            read_bytes += len(chunk)

        t1 = time.monotonic()
        digest = hasher.hexdigest()
        with lock:
            active_uploads.discard(node_id)
            append_event({'type': 'active_set', 'active_uploads': sorted(list(active_uploads))})

        append_event({
            'type': 'upload',
            'node_id': node_id,
            'client_address': self.client_address[0],
            'path': self.path,
            'size_bytes': read_bytes,
            'sha256': digest,
            'status': 201,
            'outcome': 'committed',
            'start_time': t0,
            'end_time': t1,
        })
        self.send_response(201)
        self.send_header('Content-Length', '0')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True

    def log_message(self, fmt, *args):
        return

class Server(http.server.ThreadingHTTPServer):
    allow_reuse_address = True

with Server((host, port), Handler) as server:
    with open(os.path.join(server_dir, 'http-server-ready'), 'w', encoding='utf-8') as fh:
        fh.write('ready\n')
    server.serve_forever()
PY
    echo $! > "$server_dir/http-server.pid"
    for _ in $(seq 1 50); do [ -f "$server_dir/http-server-ready" ] && break; sleep 0.1; done
    [ -f "$server_dir/http-server-ready" ] || die "HTTP PUT receiver 未 ready"

    # 记录三机复用进程 PID 矩阵
    local s_link_pid c1_link_pid c2_link_pid c1_uftpd_pid c2_uftpd_pid s_http_pid
    s_link_pid="$(cat "$server_dir/wfb.pid")"
    c1_link_pid="$(remote client1 "cat '$(smoke_dir "$name")/client1/wfb.pid'")"
    c2_link_pid="$(remote client2 "cat '$(smoke_dir "$name")/client2/wfb.pid'")"
    c1_uftpd_pid="$(remote client1 "cat '$(smoke_dir "$name")/client1/uftpd.pid'")"
    c2_uftpd_pid="$(remote client2 "cat '$(smoke_dir "$name")/client2/uftpd.pid'")"
    s_http_pid="$(cat "$server_dir/http-server.pid")"

    python3 - "$server_dir/reused_processes.json" "$s_link_pid" "$c1_link_pid" "$c2_link_pid" "$c1_uftpd_pid" "$c2_uftpd_pid" "$s_http_pid" <<'PY'
import json, sys
out, s_link, c1_link, c2_link, c1_uftpd, c2_uftpd, s_http = sys.argv[1:]
data = {
    'server_link_pid': int(s_link),
    'client1_link_pid': int(c1_link),
    'client2_link_pid': int(c2_link),
    'client1_uftpd_pid': int(c1_uftpd),
    'client2_uftpd_pid': int(c2_uftpd),
    'server_http_pid': int(s_http),
}
with open(out, 'w', encoding='utf-8') as fh:
    json.dump(data, fh, indent=2)
PY
}

cmd_smoke_gate() {
    local name=gate archive server_dir
    archive="$(smoke_archive_dir)"
    server_dir="$(smoke_dir "$name")/server"

    trap 'stop_smoke_gate_processes; collect_smoke_evidence "$name" || true; handle_smoke_gate_failure' ERR

    log_info "开始连续三周期双向数据面 Gate 验收..."
    start_smoke_gate_environment

    local cycle t_cycle_start t_cycle_end cycle_dur
    local t_dl_start t_dl_end dl_dur
    local t_ul_start t_ul_end ul_dur
    local work src model manifest status log
    local cycle_files=()

    for cycle in 1 2 3; do
        log_info "=== 运行数据面 Gate 周期 $cycle / 3 ==="
        t_cycle_start="$(python3 -c 'import time; print(time.monotonic())')"

        # 1. 确定性生成 4 MiB 交付物与 update 文件
        work="$server_dir/cycle$cycle"
        sudo install -d "$work"
        sudo chown "$(id -u):$(id -g)" "$work"
        src="issue41-cycle$cycle"
        model="$work/model.bin"
        manifest="$work/model.manifest.json"

        # 生成 server 端下行 model 和 manifest
        sudo python3 - "$model" "$manifest" "$INPUT_SIZE_BYTES" "$cycle" <<'PY'
import hashlib, json, sys
model, manifest, size_str, cycle_str = sys.argv[1:]
size = int(size_str)
pat = ('wfb-ng-issue41-cycle%s-model-4mib\n' % cycle_str).encode('utf-8')
data = (pat * (size // len(pat) + 1))[:size]
with open(model, 'wb') as fh:
    fh.write(data)
with open(manifest, 'w', encoding='utf-8') as fh:
    json.dump({'schema_version': 1, 'artifact_type': 'model', 'size_bytes': size, 'sha256': hashlib.sha256(data).hexdigest()}, fh, separators=(',', ':'))
PY

        # 生成两 client 不同的 4 MiB update 文件
        for role in client1 client2; do
            local nid="${role#client}"
            remote "$role" "sudo install -d '$(smoke_dir "$name")/$role/cycle$cycle' && sudo python3 - '$(smoke_dir "$name")/$role/cycle$cycle/update.bin' '$INPUT_SIZE_BYTES' '$cycle' '$nid' <<'PY'
import hashlib, json, sys
path, size_str, cycle_str, nid = sys.argv[1:]
size = int(size_str)
pat = ('wfb-ng-issue41-cycle%s-client%s-update-4mib\n' % (cycle_str, nid)).encode('utf-8')
data = (pat * (size // len(pat) + 1))[:size]
with open(path, 'wb') as fh:
    fh.write(data)
with open(path + '.sha256', 'w', encoding='utf-8') as fh:
    fh.write(hashlib.sha256(data).hexdigest())
PY"
        done

        # 2. Shared UFTP 下行 (4 MiB)
        t_dl_start="$(python3 -c 'import time; print(time.monotonic())')"
        status="$work/uftp.status"
        log="$work/uftp.log"
        sudo timeout "$SMOKE_IO_TIMEOUT_SECONDS" bash -c "cd '$work' && uftp -q -I '${SERVER_TUN_ADDR%/*}' -M '$UFTP_GROUP' -P '$UFTP_PRIVATE_GROUP' -p '$UFTP_PORT' -U 0x000000ff -H 0x00000001,0x00000002 -Y none -R 15000 -r 0.1:0.01:2.0 -s 20 -L '$log' -S '$status' -D '$src' 'model.bin' 'model.manifest.json'" || die "周期 $cycle shared UFTP 下行超时或失败"
        t_dl_end="$(python3 -c 'import time; print(time.monotonic())')"
        dl_dur="$(python3 -c "print($t_dl_end - $t_dl_start)")"

        # 下行结果验证
        for role in client1 client2; do
            remote "$role" "sudo chown -R \$(id -u):\$(id -g) '$(smoke_dir "$name")/$role/inbox'"
            scp -rq "$(client_ssh "$role"):$(smoke_dir "$name")/$role/inbox/$src" "$server_dir/$role-inbox-$src"
        done

        python3 - "$work" "$server_dir/client1-inbox-$src" "$server_dir/client2-inbox-$src" "$status" "$dl_dur" "$server_dir/cycle${cycle}_downlink.json" <<'PY'
import hashlib, json, os, sys
server_cycle_dir, c1_inbox, c2_inbox, status_path, duration_str, out_path = sys.argv[1:]
from tests.real_hardware.issue41_gate import verify_downlink_artifacts, GateConfig
res = verify_downlink_artifacts(
    server_cycle_dir=server_cycle_dir,
    client_inboxes={'1': c1_inbox, '2': c2_inbox},
    status_file=status_path,
    config=GateConfig(),
    duration_seconds=float(duration_str),
)
with open(out_path, 'w', encoding='utf-8') as fh:
    json.dump(res, fh, indent=2)
if res['status'] != 'passed':
    raise SystemExit('UFTP 下行校验失败: %r' % res)
PY
        [ $? -eq 0 ] || die "Gate 周期 $cycle UFTP 下行校验未通过"

        # 3. 两个 client 各一次 4 MiB HTTP PUT 上行
        t_ul_start="$(python3 -c 'import time; print(time.monotonic())')"
        for role in client1 client2; do
            local nid="${role#client}"
            remote "$role" "sudo timeout '$SMOKE_IO_TIMEOUT_SECONDS' python3 - '$(smoke_dir "$name")/$role/cycle$cycle' '$(client_ip "$role")' '$HTTP_HOST' '$HTTP_PORT' '$role' '$nid' <<'PY'
import hashlib, http.client, json, os, sys, time
work, source_ip, host, port, role, nid = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5], int(sys.argv[6])
update_file = os.path.join(work, 'update.bin')
with open(update_file, 'rb') as fh:
    body = fh.read()
digest = hashlib.sha256(body).hexdigest()
path = '/client%d' % nid
start = time.monotonic()
conn = http.client.HTTPConnection(host, port, timeout=10, source_address=(source_ip, 0))
conn.request('PUT', path, body=body, headers={'Content-Length': str(len(body)), 'Content-Type': 'application/octet-stream', 'Connection': 'close'})
resp = conn.getresponse()
resp.read()
end = time.monotonic()
conn.close()
with open(os.path.join(work, 'client-put-result.json'), 'w', encoding='utf-8') as fh:
    json.dump({'role': role, 'node_id': nid, 'path': path, 'http_status': resp.status, 'bytes': len(body), 'sha256': digest, 'start_monotonic': start, 'end_monotonic': end}, fh, separators=(',', ':'))
if resp.status != 201:
    raise SystemExit('unexpected HTTP status %s' % resp.status)
PY"
            scp -q "$(client_ssh "$role"):$(smoke_dir "$name")/$role/cycle$cycle/client-put-result.json" "$server_dir/$role-put-result-cycle$cycle.json"
        done
        t_ul_end="$(python3 -c 'import time; print(time.monotonic())')"
        ul_dur="$(python3 -c "print($t_ul_end - $t_ul_start)")"

        # 上行结果验证
        python3 - "$server_dir/server-put-events.jsonl" "$server_dir/client1-put-result-cycle$cycle.json" "$server_dir/client2-put-result-cycle$cycle.json" "$ul_dur" "$server_dir/cycle${cycle}_uplink.json" <<'PY'
import json, sys
events_file, c1_res_path, c2_res_path, duration_str, out_path = sys.argv[1:]
from tests.real_hardware.issue41_gate import verify_uplink_cycle, GateConfig
events = []
with open(events_file, 'r', encoding='utf-8') as fh:
    for line in fh:
        line = line.strip()
        if line:
            events.append(json.loads(line))
with open(c1_res_path, 'r', encoding='utf-8') as fh:
    c1 = json.load(fh)
with open(c2_res_path, 'r', encoding='utf-8') as fh:
    c2 = json.load(fh)
c_res = {
    '1': {'node_id': 1, 'status': c1['http_status'], 'size_bytes': c1['bytes'], 'sha256': c1['sha256'], 'start_time': c1['start_monotonic'], 'end_time': c1['end_monotonic']},
    '2': {'node_id': 2, 'status': c2['http_status'], 'size_bytes': c2['bytes'], 'sha256': c2['sha256'], 'start_time': c2['start_monotonic'], 'end_time': c2['end_monotonic']},
}
for role, expected_addr in (('1', '10.80.0.11'), ('2', '10.80.0.12')):
    ev = next((e for e in events if e.get('type') == 'upload' and str(e.get('node_id')) == role), None)
    if ev and ev.get('client_address') != expected_addr:
        if event['client_address'] != expected_addr:
            raise SystemExit('client address mismatch')
res = verify_uplink_cycle(events, c_res, GateConfig(), float(duration_str))
with open(out_path, 'w', encoding='utf-8') as fh:
    json.dump(res, fh, indent=2)
if res['status'] != 'passed':
    raise SystemExit('HTTP PUT 上行校验失败: %r' % res)
PY
        [ $? -eq 0 ] || die "Gate 周期 $cycle HTTP PUT 校验未通过"

        # 4. 采集遥测并校验周期契约
        for role in client1 client2; do
            scp -q "$(client_ssh "$role"):$(smoke_dir "$name")/$role/wfb.log" "$server_dir/$role-wfb.log" 2>/dev/null || true
            scp -q "$(client_ssh "$role"):$(smoke_dir "$name")/$role/${role}_queue_summary.json" "$server_dir/$role-queue.json" 2>/dev/null || true
        done

        python3 - "$cycle" "$server_dir/cycle${cycle}_downlink.json" "$server_dir/cycle${cycle}_uplink.json" "$server_dir/wfb.log" "$server_dir/$role-wfb.log" "$server_dir/client1-wfb.log" "$server_dir/client2-wfb.log" "$server_dir/server_queue_summary.json" "$server_dir/client1-queue.json" "$server_dir/client2-queue.json" "$dl_dur" "$ul_dur" "$server_dir/cycle$cycle.json" <<'PY'
import json, os, sys
cycle_idx, dl_path, ul_path, s_log_p, _, c1_log_p, c2_log_p, sq_p, c1q_p, c2q_p, dl_dur, ul_dur, out_path = sys.argv[1:]
from tests.real_hardware.issue41_gate import parse_telemetry, build_cycle_evidence, validate_cycle_evidence, GateConfig
with open(dl_path, 'r', encoding='utf-8') as fh:
    dl = json.load(fh)
with open(ul_path, 'r', encoding='utf-8') as fh:
    ul = json.load(fh)

s_log = open(s_log_p, 'r', encoding='utf-8', errors='replace').read() if os.path.isfile(s_log_p) else ''
c1_log = open(c1_log_p, 'r', encoding='utf-8', errors='replace').read() if os.path.isfile(c1_log_p) else ''
c2_log = open(c2_log_p, 'r', encoding='utf-8', errors='replace').read() if os.path.isfile(c2_log_p) else ''
sq = json.load(open(sq_p, 'r', encoding='utf-8')) if os.path.isfile(sq_p) else {}
c1q = json.load(open(c1q_p, 'r', encoding='utf-8')) if os.path.isfile(c1q_p) else {}
c2q = json.load(open(c2q_p, 'r', encoding='utf-8')) if os.path.isfile(c2q_p) else {}

durations = {
    'downlink_seconds': float(dl_dur),
    'uplink_seconds': float(ul_dur),
    'cycle_total_seconds': float(dl_dur) + float(ul_dur),
}
telem = parse_telemetry(
    server_log=s_log,
    client_logs={'1': c1_log, '2': c2_log},
    queue_summaries={'server': sq, 'client1': c1q, 'client2': c2q},
    phase_durations=durations,
)
cycle_ev = build_cycle_evidence(
    cycle_index=int(cycle_idx),
    downlink=dl,
    uplink=ul,
    telemetry=telem,
    config=GateConfig(),
)
errors = validate_cycle_evidence(cycle_ev, GateConfig())
if errors:
    raise SystemExit('周期 %s 遥测或契约校验失败: %r' % (cycle_idx, errors))
with open(out_path, 'w', encoding='utf-8') as fh:
    json.dump(cycle_ev, fh, indent=2)
PY
        [ $? -eq 0 ] || die "Gate 周期 $cycle 遥测契约校验未通过"

        t_cycle_end="$(python3 -c 'import time; print(time.monotonic())')"
        cycle_dur="$(python3 -c "print($t_cycle_end - $t_cycle_start)")"
        python3 -c "import sys; sys.exit(0 if float(sys.argv[1]) <= float(sys.argv[2]) else 1)" "$cycle_dur" "$SMOKE_CYCLE_DEADLINE_SECONDS" || die "Gate 周期 $cycle 总耗时 ${cycle_dur}s 超过固定 deadline ${SMOKE_CYCLE_DEADLINE_SECONDS}s"

        # 5. 清理本周期临时状态（不杀进程）
        for role in client1 client2; do
            remote "$role" "sudo rm -rf '$(smoke_dir "$name")/$role/inbox'/* '$(smoke_dir "$name")/$role/cycle$cycle'"
        done
        cycle_files+=("$server_dir/cycle$cycle.json")
        log_ok "Gate 周期 $cycle 验收通过 (耗时: ${cycle_dur}s)"
    done

    # 停止进程并清理收尾
    stop_smoke_gate_processes
    verify_no_smoke_orphans
    collect_smoke_evidence "$name"

    mkdir -p "$archive"
    python3 - "$archive/gate_summary.json" "$RUN_ID" "$server_dir/reused_processes.json" "${cycle_files[@]}" <<'PY'
import json, sys
out_path, run_id, pids_path, c1_p, c2_p, c3_p = sys.argv[1:]
from tests.real_hardware.issue41_gate import build_gate_summary, validate_gate_summary, GateConfig
with open(pids_path, 'r', encoding='utf-8') as fh:
    pids = json.load(fh)
cycles = [
    json.load(open(c1_p, 'r', encoding='utf-8')),
    json.load(open(c2_p, 'r', encoding='utf-8')),
    json.load(open(c3_p, 'r', encoding='utf-8')),
]
summary = build_gate_summary(
    run_id=run_id,
    status='passed',
    cycles=cycles,
    reused_processes=pids,
    config=GateConfig(),
)
errors = validate_gate_summary(summary, GateConfig())
if errors:
    raise SystemExit('Gate summary 校验失败: %r' % errors)
with open(out_path, 'w', encoding='utf-8') as fh:
    json.dump(summary, fh, indent=2)
PY
    [ $? -eq 0 ] || die "Gate summary 归档校验未通过"

    write_smoke_marker "$name"
    python3 "$SCRIPT_DIR/issue41_envelope.py" append-partition \
        --archive-dir "$ARCHIVE_DIR" \
        --name pre_runtime_smoke \
        --json-file "$archive/gate_summary.json"

    trap - ERR
    log_ok "连续三周期双向数据面 Gate 全部通过！"
}

handle_smoke_gate_failure() {
    log_warn "Gate 运行发生失败，正在进行受控停止与现场诊断归档..."
    local name=gate archive server_dir
    archive="$(smoke_archive_dir)"
    server_dir="$(smoke_dir "$name")/server"
    stop_smoke_gate_processes || true
    collect_smoke_evidence "$name" || true

    mkdir -p "$archive"
    python3 - "$archive" "$RUN_ID" <<'PY'
import json, os, sys
archive_dir, run_id = sys.argv[1], sys.argv[2]
from tests.real_hardware.issue41_gate import classify_gate_failure

s_log_p = os.path.join(archive_dir, 'server', 'wfb.log')
c1_log_p = os.path.join(archive_dir, 'client1', 'wfb.log')
c2_log_p = os.path.join(archive_dir, 'client2', 'wfb.log')

s_log = open(s_log_p, 'r', encoding='utf-8', errors='replace').read() if os.path.isfile(s_log_p) else ''
c1_log = open(c1_log_p, 'r', encoding='utf-8', errors='replace').read() if os.path.isfile(c1_log_p) else ''
c2_log = open(c2_log_p, 'r', encoding='utf-8', errors='replace').read() if os.path.isfile(c2_log_p) else ''

ant_samples = s_log.count('\tRX_ANT\t')
c_decl = {
    'client1': 'first_declare node_id=1' in c1_log,
    'client2': 'first_declare node_id=2' in c2_log,
}
s_acc = {
    'client1': 'ready_accept node_id=1' in s_log,
    'client2': 'ready_accept node_id=2' in s_log,
}

diag = classify_gate_failure(
    server_rx_ant_samples=ant_samples,
    client_declared=c_decl,
    server_accepted=s_acc,
    tun_routes_ok=True,
    uftp_status_ok=False,
    http_put_ok=False,
)

failed_summary = {
    'schema_version': 1,
    'run_id': run_id,
    'status': 'failed',
    'gate_type': 'three_cycle_bidirectional',
    'last_successful_layer': diag['last_successful_layer'],
    'first_failing_layer': diag['first_failing_layer'],
    'failure_category': diag['category'],
    'failure_reason': diag['reason'],
}

with open(os.path.join(archive_dir, 'gate_summary.json'), 'w', encoding='utf-8') as fh:
    json.dump(failed_summary, fh, indent=2)

with open(os.path.join(archive_dir, 'failed.json'), 'w', encoding='utf-8') as fh:
    json.dump(failed_summary, fh, indent=2)
PY

    python3 "$SCRIPT_DIR/issue41_envelope.py" append-partition \
        --archive-dir "$ARCHIVE_DIR" \
        --name pre_runtime_smoke \
        --json-file "$archive/gate_summary.json" 2>/dev/null || true

    die "数据面 Gate 验收失败，已归档失败诊断，禁止正式 Runtime 启动！"
}

runtime_state_exists_local() {
    [ -d /var/lib/wfb-ng/issue41/server ] && find /var/lib/wfb-ng/issue41/server -mindepth 1 -print -quit | grep -q . && return 0
    return 1
}

runtime_state_exists_remote() {
    local role="$1"
    remote "$role" "[ -d /var/lib/wfb-ng/issue41/client ] && find /var/lib/wfb-ng/issue41/client -mindepth 1 -print -quit | grep -q ."
}

assert_runtime_processes_stopped() {
    if pgrep -x wfb-fl-server >/dev/null || pgrep -x wfb_v6_uplink >/dev/null || pgrep -x uftp >/dev/null || pgrep -x uftpd >/dev/null; then
        die "启动 Runtime 前本机仍有 issue41 相关进程"
    fi
    for role in client1 client2; do
        remote "$role" "! pgrep -x wfb-fl-client >/dev/null && ! pgrep -x wfb_v6_uplink >/dev/null && ! pgrep -x uftp >/dev/null && ! pgrep -x uftpd >/dev/null" || die "$role 启动 Runtime 前仍有 issue41 相关进程"
    done
}

prepare_runtime_state() {
    cmd_stop_all
    assert_runtime_processes_stopped
    if runtime_state_exists_local || runtime_state_exists_remote client1 || runtime_state_exists_remote client2; then
        if [ "$RESET_RUNTIME_STATE" != "1" ]; then
            die "检测到上次 Runtime 现场；默认拒绝复用，请先执行 clean，或设置 ISSUE41_RESET_RUNTIME_STATE=1"
        fi
        sudo rm -rf /var/lib/wfb-ng/issue41/server
        for role in client1 client2; do
            remote "$role" "sudo rm -rf /var/lib/wfb-ng/issue41/client"
        done
        log_warn "已按 ISSUE41_RESET_RUNTIME_STATE=1 清理旧 Runtime work_dir"
    fi
}

cmd_run_runtime_loop() {
    prepare_runtime_state
    configure_runtime_monitors
    write_issue41_configs server
    write_issue41_configs client1
    write_issue41_configs client2
    sudo systemctl daemon-reload
    for role in client1 client2; do
        remote "$role" "sudo systemctl daemon-reload"
        wait_remote_service_ready "$role" wfb-fl-client.service
        assert_remote_service_active "$role" wfb-fl-client.service
    done
    sudo systemctl restart wfb-fl-server.service
    assert_runtime_uftp_routes
    capture_runtime_routes
    wait_runtime_results
    log_ok "Runtime loop 作业与 UFTP 组播路由验收通过。"
}

cmd_lifecycle_stop_restart() {
    sudo systemctl stop wfb-fl-server.service || true
    for role in client1 client2; do remote "$role" "sudo systemctl stop wfb-fl-client.service || true"; done
    sudo systemctl is-active --quiet wfb-fl-server.service && die "server service 未停止" || true
    for role in client1 client2; do remote "$role" "! systemctl is-active --quiet wfb-fl-client.service"; done
    if pgrep -x wfb-fl-server >/dev/null || pgrep -x wfb_v6_uplink >/dev/null || pgrep -x uftp >/dev/null || pgrep -x uftpd >/dev/null; then
        die "本机发现 issue41 相关孤儿进程"
    fi
    for role in client1 client2; do
        remote "$role" "! pgrep -x wfb-fl-client >/dev/null && ! pgrep -x wfb_v6_uplink >/dev/null && ! pgrep -x uftp >/dev/null && ! pgrep -x uftpd >/dev/null"
    done
    wait_local_issue41_cleanup || die "本机 issue41 停止后清理超时"
    for role in client1 client2; do
        wait_remote_issue41_cleanup "$role" "$(client_tun "$role")" || die "$role issue41 停止后清理超时"
    done
    cmd_collect
    sudo rm -rf /var/lib/wfb-ng/issue41/server
    for role in client1 client2; do
        remote "$role" "sudo rm -rf /var/lib/wfb-ng/issue41/client"
    done
    for role in client1 client2; do
        wait_remote_service_ready "$role" wfb-fl-client.service
        assert_remote_service_active "$role" wfb-fl-client.service
    done
    sudo systemctl restart wfb-fl-server.service
    sleep 2
    sudo systemctl stop wfb-fl-server.service || true
    for role in client1 client2; do remote "$role" "sudo systemctl stop wfb-fl-client.service || true"; done
    sudo systemctl is-active --quiet wfb-fl-server.service && die "server service restart 后未停止" || true
    for role in client1 client2; do remote "$role" "! systemctl is-active --quiet wfb-fl-client.service"; done
    wait_local_issue41_cleanup || die "本机 restart 后清理超时"
    for role in client1 client2; do
        wait_remote_issue41_cleanup "$role" "$(client_tun "$role")" || die "$role restart 后清理超时"
    done
    log_ok "服务 stop/restart/inactive 检查通过"
}

cmd_stop_all() {
    sudo systemctl stop wfb-fl-server.service 2>/dev/null || true
    sudo pkill -x wfb_v6_uplink 2>/dev/null || true
    sudo pkill -x uftp 2>/dev/null || true
    sudo pkill -x uftpd 2>/dev/null || true
    sudo pkill -f /var/tmp/wfb-ng-issue41-smoke 2>/dev/null || true
    for role in client1 client2; do remote "$role" "sudo systemctl stop wfb-fl-client.service 2>/dev/null || true; sudo pkill -x wfb_v6_uplink 2>/dev/null || true; sudo pkill -x uftp 2>/dev/null || true; sudo pkill -x uftpd 2>/dev/null || true; sudo pkill -f /var/tmp/wfb-ng-issue41-smoke 2>/dev/null || true" || true; done
    wait_local_issue41_cleanup || die "本机 issue41 停止后清理超时"
    for role in client1 client2; do
        wait_remote_issue41_cleanup "$role" "$(client_tun "$role")" || die "$role issue41 停止后清理超时"
    done
}

cmd_clean() {
    cmd_stop_all
    sudo rm -rf /var/lib/wfb-ng/issue41/server /var/tmp/wfb-ng-issue41-smoke /etc/wfb-ng/issue41 /etc/systemd/system/wfb-fl-server.service.d/issue41.conf
    for role in client1 client2; do remote "$role" "sudo rm -rf /var/lib/wfb-ng/issue41/client /var/tmp/wfb-ng-issue41-smoke /etc/wfb-ng/issue41 /etc/systemd/system/wfb-fl-client.service.d/issue41.conf"; done
    log_ok "issue41 管理路径已清理"
}

capture_runtime_routes() {
    local role group tun source_ip prefix
    for role in server client1 client2; do
        if [ "$role" = server ]; then
            tun="$SERVER_TUN"
            source_ip="${SERVER_TUN_ADDR%/*}"
        else
            tun="$(client_tun "$role")"
            source_ip="$(client_ip "$role")"
        fi
        for group in "$UFTP_GROUP" "$UFTP_PRIVATE_GROUP"; do
            prefix="$ARCHIVE_DIR/raw/$role-route-${group//./-}"
            if [ "$role" = server ]; then
                ip route show "$group/32" > "$prefix-show.txt" 2>&1 || true
                ip route get "$group" from "$source_ip" > "$prefix-get.txt" 2>&1 || true
            else
                remote "$role" "ip route show '$group/32'" > "$prefix-show.txt" 2>&1 || true
                remote "$role" "ip route get '$group' from '$source_ip'" > "$prefix-get.txt" 2>&1 || true
            fi
        done
    done
}

cmd_collect() {
    local server_result role client_result
    mkdir -p "$ARCHIVE_DIR/formal_runtime_loop/server" "$ARCHIVE_DIR/lifecycle" "$ARCHIVE_DIR/raw"
    sudo systemctl status wfb-fl-server.service > "$ARCHIVE_DIR/raw/server-systemctl-status.txt" 2>&1 || true
    sudo journalctl -u wfb-fl-server.service --output=short-precise --no-pager > "$ARCHIVE_DIR/raw/server-journal.txt" 2>&1 || true
    server_result="$ARCHIVE_DIR/formal_runtime_loop/server/issue41-server-result.json"
    if [ ! -f "$server_result" ]; then
        sudo cp -a /var/lib/wfb-ng/issue41/server/. "$ARCHIVE_DIR/formal_runtime_loop/server/" 2>/dev/null || true
    fi
    sudo chown -R "$(id -u):$(id -g)" "$ARCHIVE_DIR/formal_runtime_loop/server"
    for role in client1 client2; do
        mkdir -p "$ARCHIVE_DIR/formal_runtime_loop/$role"
        remote "$role" "sudo tar -C /var/lib/wfb-ng/issue41/client -czf /tmp/issue41-$role-client.tgz . 2>/dev/null || true; systemctl status wfb-fl-client.service > /tmp/issue41-$role-status.txt 2>&1 || true; journalctl -u wfb-fl-client.service --output=short-precise --no-pager > /tmp/issue41-$role-journal.txt 2>&1 || true"
        client_result="$ARCHIVE_DIR/formal_runtime_loop/$role/issue41-$role-result.json"
        if [ ! -f "$client_result" ]; then
            if scp -q "$(client_ssh "$role"):/tmp/issue41-$role-client.tgz" "$ARCHIVE_DIR/formal_runtime_loop/$role/client.tgz" 2>/dev/null; then
                tar -C "$ARCHIVE_DIR/formal_runtime_loop/$role" -xzf "$ARCHIVE_DIR/formal_runtime_loop/$role/client.tgz" || true
            fi
        fi
        scp -q "$(client_ssh "$role"):/tmp/issue41-$role-status.txt" "$ARCHIVE_DIR/raw/$role-systemctl-status.txt" 2>/dev/null || true
        scp -q "$(client_ssh "$role"):/tmp/issue41-$role-journal.txt" "$ARCHIVE_DIR/raw/$role-journal.txt" 2>/dev/null || true
    done
    if [ "$(find "$ARCHIVE_DIR/raw" -maxdepth 1 -name '*-route-*.txt' -type f | wc -l)" -lt 12 ]; then
        capture_runtime_routes
    fi
    log_ok "归档采集完成：$ARCHIVE_DIR"
}

cmd_summary() {
    local server_result client1_result client2_result conclusion status reason route_evidence role group suffix
    server_result="$ARCHIVE_DIR/formal_runtime_loop/server/issue41-server-result.json"
    client1_result="$ARCHIVE_DIR/formal_runtime_loop/client1/issue41-client1-result.json"
    client2_result="$ARCHIVE_DIR/formal_runtime_loop/client2/issue41-client2-result.json"
    route_evidence=
    for role in server client1 client2; do
        for group in "$UFTP_GROUP" "$UFTP_PRIVATE_GROUP"; do
            suffix="${group//./-}"
            for direction in show get; do
                [ -z "$route_evidence" ] || route_evidence="$route_evidence,"
                route_evidence="$route_evidence\"$ARCHIVE_DIR/raw/$role-route-$suffix-$direction.txt\""
            done
        done
    done
    python3 "$SCRIPT_DIR/issue41_build_summary.py" "$ARCHIVE_DIR" > "$ARCHIVE_DIR/formal-runtime-summary.json"
    python3 - "$ARCHIVE_DIR" "$route_evidence" <<'PY'
import json
import os
import sys

archive_dir, route_evidence = sys.argv[1:]
with open(os.path.join(archive_dir, 'formal-runtime-summary.json'), encoding='utf-8') as fh:
    formal = json.load(fh)

smoke_gate_path = os.path.join(archive_dir, 'pre_runtime_smoke', 'gate_summary.json')
passed_marker_path = os.path.join(archive_dir, 'pre_runtime_smoke', 'passed.json')
if os.path.isfile(smoke_gate_path):
    with open(smoke_gate_path, 'r', encoding='utf-8') as sf:
        smoke_gate = json.load(sf)
elif os.path.isfile(passed_marker_path):
    with open(passed_marker_path, 'r', encoding='utf-8') as sf:
        smoke_gate = json.load(sf)
else:
    smoke_gate = {'status': 'failed', 'reason': 'gate_summary.json 缺失'}

status = 'passed' if (smoke_gate.get('status') == 'passed' and formal['status'] == 'passed') else 'failed'
reason = formal['reason']
if status != 'passed' and formal['status'] == 'passed':
    reason = '数据面 Gate 前置条件未通过'

envelope_path = os.path.join(archive_dir, 'envelope.json')
run_id = os.path.basename(archive_dir)
if os.path.isfile(envelope_path):
    with open(envelope_path, 'r', encoding='utf-8') as ef:
        run_id = json.load(ef).get('run_id', run_id)

summary = {
    'schema_version': 1,
    'run_id': run_id,
    'orchestration': {'status': 'passed', 'radio_health_dir': os.path.join(archive_dir, 'orchestration', 'radio-health')},
    'pre_runtime_smoke': smoke_gate,
    'formal_runtime_loop': {
        'status': formal['status'],
        'runtime_interfaces': ['publish_model', 'wait_for_model', 'submit_update', 'wait_for_updates'],
        'data_plane': '10.80.0.0/24',
        'server_wait_for_updates_returned_node_ids': formal['server_wait_for_updates_returned_node_ids'],
        'partial_result_returned': formal['partial_result_returned'],
        'scenario': formal['scenario'],
        'rounds': formal['rounds'],
        'server_result': os.path.join(archive_dir, 'formal_runtime_loop', 'server', 'issue41-server-result.json'),
        'client1_result': os.path.join(archive_dir, 'formal_runtime_loop', 'client1', 'issue41-client1-result.json'),
        'client2_result': os.path.join(archive_dir, 'formal_runtime_loop', 'client2', 'issue41-client2-result.json'),
        'server_journal': os.path.join(archive_dir, 'raw', 'server-journal.txt'),
        'client1_journal': os.path.join(archive_dir, 'raw', 'client1-journal.txt'),
        'client2_journal': os.path.join(archive_dir, 'raw', 'client2-journal.txt'),
        'route_evidence': json.loads('[%s]' % route_evidence),
    },
    'lifecycle': {'status': formal['status']},
    'conclusion': {'status': status, 'reason': reason},
}
with open(os.path.join(archive_dir, 'issue41_summary.json'), 'w', encoding='utf-8') as fh:
    json.dump(summary, fh, ensure_ascii=True, separators=(',', ':'))
PY
    status="$(python3 -c "import json, sys; print(json.load(open(sys.argv[1], encoding='utf-8'))['conclusion']['status'])" "$ARCHIVE_DIR/issue41_summary.json")"
    reason="$(python3 -c "import json, sys; print(json.load(open(sys.argv[1], encoding='utf-8'))['conclusion']['reason'])" "$ARCHIVE_DIR/issue41_summary.json")"
    cat > "$ARCHIVE_DIR/result.md" <<EOF
# issue41 真实硬件 FL Runtime 闭环结果

- conclusion: $status
- reason: $reason
- archive: $ARCHIVE_DIR
EOF
    python3 "$SCRIPT_DIR/issue41_validate_archive.py" "$ARCHIVE_DIR"
    log_ok "summary 已生成：$ARCHIVE_DIR/issue41_summary.json"
    [ "$status" = passed ] || die "归档结论为 failed：$reason"
}

cmd_run_all() {
    trap 'if [ "$KEEP_RUNNING_ON_FAIL" != "1" ]; then cmd_stop_all || true; fi; cmd_collect || true; cmd_summary || true' ERR
    cmd_preflight
    cmd_install
    cmd_smoke_gate
    cmd_run_runtime_loop
    cmd_lifecycle_stop_restart
    cmd_collect
    cmd_summary
}

case "$cmd" in
    push-branch) cmd_push_branch ;;
    sync-remotes) cmd_sync_remotes ;;
    preflight) cmd_preflight ;;
    install) cmd_install ;;
    generate-fixtures) cmd_generate_fixtures ;;
    smoke-gate|smoke) cmd_smoke_gate ;;
    run-runtime-loop) cmd_run_runtime_loop ;;
    lifecycle-stop-restart) cmd_lifecycle_stop_restart ;;
    collect) cmd_collect ;;
    summary) cmd_summary ;;
    run-all) cmd_run_all ;;
    stop-all) cmd_stop_all ;;
    clean) cmd_clean ;;
    *) usage; die "未知子命令：$cmd" ;;
esac
