#!/bin/bash
# -*- coding: utf-8 -*-
# issue #41 SSH 编排真实硬件 FL Runtime 闭环验收脚本

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
export PYTHONPATH="$PROJECT_ROOT:${PYTHONPATH:-}"
BRANCH="${ISSUE41_BRANCH:-$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo feat/stage2-airgapped-core-engine-and-daemon)}"
ARCHIVE_ROOT="${ISSUE41_ARCHIVE_ROOT:-$PROJECT_ROOT/tests/logs}"
RUN_ID="${ISSUE41_RUN_ID:-v8_issue41_$(date +%Y%m%d_%H%M%S)}"
ARCHIVE_DIR="${ISSUE41_ARCHIVE_DIR:-$ARCHIVE_ROOT/$RUN_ID}"
REMOTE_REPO="${ISSUE41_REMOTE_REPO:-/home/virt/projects/wfb-ng-fl}"
read -r -a CLIENT_ROLES <<< "${ISSUE41_CLIENT_ROLES:-client1 client2 client3 client4 client5 client6 client7}"
export ISSUE41_CLIENT_ROLES="${CLIENT_ROLES[*]}"
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
FEC_N="${ISSUE41_FEC_N:-14}"
RADIO_BANDWIDTH="${ISSUE41_RADIO_BANDWIDTH:-40}"
RADIO_MCS_INDEX="${ISSUE41_RADIO_MCS_INDEX:-3}"
SERVER_RADIO_MCS_INDEX="${ISSUE41_SERVER_RADIO_MCS_INDEX:-$RADIO_MCS_INDEX}"
CLIENT_RADIO_MCS_INDEX="${ISSUE41_CLIENT_RADIO_MCS_INDEX:-$RADIO_MCS_INDEX}"
RADIO_SHORT_GI="${ISSUE41_RADIO_SHORT_GI:-1}"
RADIO_TXPOWER_DBM="${ISSUE41_RADIO_TXPOWER_DBM:-12}"
UFTP_RATE_KBPS="${ISSUE41_UFTP_RATE_KBPS:-15000}"
GRANT_DURATION_MS="${ISSUE41_GRANT_DURATION_MS:-120}"
GUARD_INTERVAL_MS="${ISSUE41_GUARD_INTERVAL_MS:-20}"
LINK_LOG_INTERVAL_MS="${ISSUE41_LINK_LOG_INTERVAL_MS:-1000}"
IO_TIMEOUT_SECONDS="${ISSUE41_IO_TIMEOUT_SECONDS:-120}"
FEEDBACK_WINDOW_PERIOD_MS="${ISSUE41_FEEDBACK_WINDOW_PERIOD_MS:-500}"
FEEDBACK_WINDOW_DURATION_MS="${ISSUE41_FEEDBACK_WINDOW_DURATION_MS:-15}"
AGGREGATION_DELAY_MS="${ISSUE41_AGGREGATION_DELAY_MS:-0}"
ROUNDS="${ISSUE41_ROUNDS:-2}"
INPUT_SIZE_BYTES="${ISSUE41_INPUT_SIZE_BYTES:-$((40 * 1024 * 1024))}"
if [ -n "${ISSUE41_INITIAL_MODEL_PATH:-}" ]; then
    INITIAL_MODEL_PATH="$ISSUE41_INITIAL_MODEL_PATH"
elif [ "$INPUT_SIZE_BYTES" -eq $((40 * 1024 * 1024)) ] && [ -f "/var/lib/wfb-ng/issue41-input/model-40mib.bin" ]; then
    INITIAL_MODEL_PATH="/var/lib/wfb-ng/issue41-input/model-40mib.bin"
elif [ -f "/var/lib/wfb-ng/issue41-input/model-${INPUT_SIZE_BYTES}b.bin" ]; then
    INITIAL_MODEL_PATH="/var/lib/wfb-ng/issue41-input/model-${INPUT_SIZE_BYTES}b.bin"
elif [ "$INPUT_SIZE_BYTES" -eq 4194304 ] && [ -f "/var/lib/wfb-ng/issue41-input/model-4mib.bin" ]; then
    INITIAL_MODEL_PATH="/var/lib/wfb-ng/issue41-input/model-4mib.bin"
else
    INITIAL_MODEL_PATH="/var/lib/wfb-ng/issue41-input/model-40mib.bin"
fi
CLIENT1_UPDATE_TEMPLATE_PATH="${ISSUE41_CLIENT1_UPDATE_TEMPLATE_PATH:-/var/lib/wfb-ng/issue41-input/update-client1-40mib.bin}"
CLIENT2_UPDATE_TEMPLATE_PATH="${ISSUE41_CLIENT2_UPDATE_TEMPLATE_PATH:-/var/lib/wfb-ng/issue41-input/update-client2-40mib.bin}"
CLIENT1_TRAINING_DELAY_MS="${ISSUE41_CLIENT1_TRAINING_DELAY_MS:-0}"
CLIENT2_TRAINING_DELAY_MS="${ISSUE41_CLIENT2_TRAINING_DELAY_MS:-0}"
KEEP_RUNNING_ON_FAIL="${ISSUE41_KEEP_RUNNING_ON_FAIL:-0}"
RESET_RUNTIME_STATE="${ISSUE41_RESET_RUNTIME_STATE:-0}"
SMOKE_CYCLE_COUNT="${ISSUE41_SMOKE_CYCLE_COUNT:-3}"
SMOKE_IO_TIMEOUT_SECONDS="${ISSUE41_SMOKE_IO_TIMEOUT_SECONDS:-120}"
SMOKE_CYCLE_DEADLINE_SECONDS="${ISSUE41_SMOKE_CYCLE_DEADLINE_SECONDS:-240}"
SMOKE_TIMEOUT_SECONDS="${ISSUE41_SMOKE_TIMEOUT_SECONDS:-180}"
RUNTIME_TIMEOUT_SECONDS="${ISSUE41_RUNTIME_TIMEOUT_SECONDS:-400}"
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
  verify-config-equivalence 角色服务与数据面 Gate 严格链路配置等价性比较
  run-runtime-loop         写 issue41 配置/drop-in 并启动 systemd Runtime loop
  lifecycle-stop-restart   stop/restart/no-overlap 生命周期验收
  collect                  采集本机和远端状态证据
  summary                  生成 issue41_summary.json、result.md 并运行归档校验器
  run-all                  唯一正式入口：严格串联 preflight/install/smoke-gate/verify-config-equivalence/runtime/lifecycle/collect/summary；不自动 push
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
    if [ -d "$ARCHIVE_DIR" ] && [ ! -f "$ARCHIVE_DIR/envelope.json" ]; then
        die "归档目录已存在，拒绝覆盖：$ARCHIVE_DIR"
    fi
    if [ "$RUNTIME_TIMEOUT_SECONDS" -gt 400 ]; then
        die "formal 验收严格要求 RUNTIME_TIMEOUT_SECONDS <= 400 秒，禁止通过增大超时掩盖停滞"
    fi
    if [ "$IO_TIMEOUT_SECONDS" -gt 120 ]; then
        die "formal 验收严格要求 IO_TIMEOUT_SECONDS <= 120 秒，禁止通过增大超时掩盖停滞"
    fi
    if [ "$SMOKE_IO_TIMEOUT_SECONDS" -gt 120 ]; then
        die "formal 验收严格要求 SMOKE_IO_TIMEOUT_SECONDS <= 120 秒，禁止通过增大超时掩盖停滞"
    fi
    local rate_min rate_max
    case "$SERVER_RADIO_MCS_INDEX:$RADIO_BANDWIDTH:$CHANNEL_WIDTH" in
        2:20:HT20) rate_min=5000; rate_max=8000 ;;
        2:40:HT40+) rate_min=8000; rate_max=14000 ;;
        3:40:HT40+) rate_min=12000; rate_max=18000 ;;
        4:40:HT40+) rate_min=18000; rate_max=26000 ;;
        5:40:HT40+) rate_min=24000; rate_max=35000 ;;
        *) die "未标定的射频配置，无法校验 UFTP 安全速率" ;;
    esac
    if ! [[ "$UFTP_RATE_KBPS" =~ ^[0-9]+$ ]] ||
        [ "$UFTP_RATE_KBPS" -lt "$rate_min" ] || [ "$UFTP_RATE_KBPS" -gt "$rate_max" ]; then
        die "UFTP 速率必须在当前射频配置的安全区间 ${rate_min}~${rate_max} Kbps 内"
    fi
    if ! [[ "$GRANT_DURATION_MS" =~ ^[1-9][0-9]*$ ]] ||
        ! [[ "$GUARD_INTERVAL_MS" =~ ^[0-9]+$ ]]; then
        die "GRANT_DURATION_MS 必须为正整数且 GUARD_INTERVAL_MS 必须为非负整数"
    fi
    if ! [[ "$RADIO_TXPOWER_DBM" =~ ^[1-9][0-9]*$ ]] || [ "$RADIO_TXPOWER_DBM" -gt 30 ]; then
        die "RADIO_TXPOWER_DBM 必须为 1~30 dBm 范围内的整数"
    fi
    if [ -f "$ARCHIVE_DIR/envelope.json" ]; then
        python3 - "$ARCHIVE_DIR/envelope.json" "$UFTP_RATE_KBPS" "$SERVER_RADIO_MCS_INDEX" "$CLIENT_RADIO_MCS_INDEX" "$RADIO_BANDWIDTH" "$CHANNEL_WIDTH" "$GRANT_DURATION_MS" "$GUARD_INTERVAL_MS" "$RADIO_TXPOWER_DBM" <<'PY' || die "本次配置与已有 envelope 不一致"
import json, sys
with open(sys.argv[1], encoding='utf-8') as fh:
    cfg = json.load(fh)['resolved_config']
for key, value in zip(('uftp_rate_kbps', 'server_radio_mcs_index', 'client_radio_mcs_index',
                       'radio_bandwidth', 'channel_width', 'grant_duration_ms', 'guard_interval_ms', 'radio_txpower_dbm'),
                      (int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]),
                       sys.argv[6], int(sys.argv[7]), int(sys.argv[8]), int(sys.argv[9]))):
    val = cfg.get(key)
    if val is None and key == 'server_radio_mcs_index':
        val = cfg.get('radio_mcs_index')
    if val is None and key == 'client_radio_mcs_index':
        val = cfg.get('radio_mcs_index')
    if val is None and key in ('grant_duration_ms', 'guard_interval_ms'):
        val = 120 if key == 'grant_duration_ms' else 20
    if val is None and key == 'radio_txpower_dbm':
        val = 12
    if val != value:
        raise SystemExit('已有 envelope 配置 %s 不匹配: %r != %r' % (key, val, value))
PY
        return 0
    fi
    local training_delays_json=""
    local client_templates_json=""
    local client_tuns_json=""
    local first=1
    for r in "${CLIENT_ROLES[@]}"; do
        local nid="${r#client}"
        local d_var="ISSUE41_${r^^}_TRAINING_DELAY_MS"
        local d_val="${!d_var:-0}"
        local t_var="ISSUE41_${r^^}_UPDATE_TEMPLATE_PATH"
        local t_val="${!t_var:-/var/lib/wfb-ng/issue41-input/update-${r}-${INPUT_SIZE_BYTES}b.bin}"
        local tun_name="$(client_tun "$r")"
        local tun_addr="$(client_addr "$r")"
        local m_var="ISSUE41_${r^^}_RADIO_MCS_INDEX"
        local m_val="${!m_var:-}"
        if [ "$first" -eq 1 ]; then
            first=0
        else
            training_delays_json+=", "
            client_templates_json+=", "
            client_tuns_json+=", "
        fi
        training_delays_json+="\"$nid\": $d_val"
        client_templates_json+="\"${r}_update_template_path\": \"$t_val\""
        client_tuns_json+="\"${r}_tun\": \"$tun_name\", \"${r}_tun_addr\": \"$tun_addr\""
        if [ -n "$m_val" ]; then
            client_tuns_json+=", \"${r}_radio_mcs_index\": $m_val"
        fi
    done
    local cfg_tmp
    cfg_tmp="$(mktemp)"
    cat > "$cfg_tmp" <<EOF
{
  "schema_version": 1,
  "channel": $CHANNEL,
  "channel_width": "$CHANNEL_WIDTH",
  "link_id": $LINK_ID,
  "uplink_stream": $UPLINK_STREAM,
  "downlink_stream": $DOWNLINK_STREAM,
  "fec_k": $FEC_K,
  "fec_n": $FEC_N,
  "radio_bandwidth": $RADIO_BANDWIDTH,
  "radio_mcs_index": $SERVER_RADIO_MCS_INDEX,
  "server_radio_mcs_index": $SERVER_RADIO_MCS_INDEX,
  "client_radio_mcs_index": $CLIENT_RADIO_MCS_INDEX,
  "radio_short_gi": $RADIO_SHORT_GI,
  "radio_txpower_dbm": $RADIO_TXPOWER_DBM,
  "server_tun": "$SERVER_TUN",
  "server_tun_addr": "$SERVER_TUN_ADDR",
  $client_tuns_json,
  "uftp_group": "$UFTP_GROUP",
  "uftp_private_group": "$UFTP_PRIVATE_GROUP",
  "uftp_port": $UFTP_PORT,
  "uftp_rate_kbps": $UFTP_RATE_KBPS,
  "grant_duration_ms": $GRANT_DURATION_MS,
  "guard_interval_ms": $GUARD_INTERVAL_MS,
  "http_host": "$HTTP_HOST",
  "http_port": $HTTP_PORT,
  "feedback_window_period_ms": $FEEDBACK_WINDOW_PERIOD_MS,
  "feedback_window_duration_ms": $FEEDBACK_WINDOW_DURATION_MS,
  "downlink_pause_threshold_bytes": 131072,
  "downlink_resume_threshold_bytes": 65536,
  "downlink_queue_packets_limit": 64,
  "uplink_pause_threshold_bytes": 131072,
  "uplink_resume_threshold_bytes": 65536,
  "uplink_queue_packets_limit": 64,
  "smoke_cycle_count": $SMOKE_CYCLE_COUNT,
  "smoke_io_timeout_seconds": $SMOKE_IO_TIMEOUT_SECONDS,
  "smoke_cycle_deadline_seconds": $SMOKE_CYCLE_DEADLINE_SECONDS,
  "smoke_timeout_seconds": $SMOKE_TIMEOUT_SECONDS,
  "runtime_timeout_seconds": $RUNTIME_TIMEOUT_SECONDS,
  "io_timeout_seconds": $IO_TIMEOUT_SECONDS,
  "stop_cleanup_timeout_seconds": $STOP_CLEANUP_TIMEOUT_SECONDS,
  "rounds": $ROUNDS,
  "artifact_size_bytes": $INPUT_SIZE_BYTES,
  "training_delay_ms_by_node": {
    $training_delays_json
  },
  "initial_model_path": "$INITIAL_MODEL_PATH",
  $client_templates_json
}
EOF
    python3 "$SCRIPT_DIR/issue41_envelope.py" init \
        --archive-dir "$ARCHIVE_DIR" \
        --run-id "$RUN_ID" \
        --branch "$BRANCH" \
        --commit "$(git -C "$PROJECT_ROOT" rev-parse HEAD 2>/dev/null || echo '')" \
        --mode "${ISSUE41_MODE:-formal}" \
        --config-json "$cfg_tmp" || { rm -f "$cfg_tmp"; die "运行包络初始化失败"; }
    rm -f "$cfg_tmp"
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
    cmd_collect 2>/dev/null || true
    cmd_summary 2>/dev/null || true
    die "preflight 失败 [$layer / $category]：$reason"
}

record_stage_failure() {
    local partition="$1" category="$2" reason="$3" last_layer="${4:-}"
    local fail_json="$ARCHIVE_DIR/$partition/failure_info.json"
    mkdir -p "$(dirname "$fail_json")"
    cat > "$fail_json" <<EOF
{"schema_version":1,"status":"failed","partition":"$partition","category":"$category","reason":"$reason","last_successful_layer":"$last_layer","run_id":"$RUN_ID"}
EOF
    python3 "$SCRIPT_DIR/issue41_envelope.py" record-stage-failure \
        --archive-dir "$ARCHIVE_DIR" \
        --partition-name "$partition" \
        --json-file "$fail_json" \
        --category "$category" \
        --reason "$reason" \
        --last-successful-layer "$last_layer" \
        --first-failing-layer "$partition" >/dev/null 2>&1 || true
    cmd_stop_all 2>/dev/null || true
    cmd_collect 2>/dev/null || true
    cmd_summary 2>/dev/null || true
}

client_ssh() {
    local role="$1"
    local num="${role#client}"
    local var_name="ISSUE41_CLIENT${num}_SSH"
    printf '%s\n' "${!var_name:-vm${num}}"
}

client_tun() {
    local role="$1"
    local num="${role#client}"
    local var_name="ISSUE41_CLIENT${num}_TUN"
    printf '%s\n' "${!var_name:-v8i41c${num}}"
}

client_addr() {
    local role="$1"
    local num="${role#client}"
    local var_name="ISSUE41_CLIENT${num}_TUN_ADDR"
    printf '%s\n' "${!var_name:-10.80.0.$((10 + num))/24}"
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
    for role in "${CLIENT_ROLES[@]}"; do
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
    for role in "${CLIENT_ROLES[@]}"; do
        remote_head="$(remote "$role" "cd '$REMOTE_REPO' && git rev-parse HEAD" 2>/dev/null || echo '')"
        [ "$remote_head" = "$head" ] || record_preflight_failure "repo_and_version" "implementation" "$role commit 与本机不一致：$remote_head != $head" "$last_successful"
        remote_status="$(remote "$role" "cd '$REMOTE_REPO' && git status --short" 2>/dev/null || echo '')"
        [ -z "$remote_status" ] || record_preflight_failure "repo_and_version" "implementation" "$role 工作区不干净：$remote_status" "$last_successful"
        remote "$role" "cd '$REMOTE_REPO' && test \"\$(git rev-parse --abbrev-ref HEAD)\" = '$BRANCH'" || record_preflight_failure "repo_and_version" "implementation" "$role 分支不是 $BRANCH" "$last_successful"
    done
    last_successful="repo_and_version"

    # Layer 2: ssh_and_sudo
    for role in "${CLIENT_ROLES[@]}"; do
        remote "$role" "true" >/dev/null 2>&1 || record_preflight_failure "ssh_and_sudo" "environment" "SSH 到 $role 失败" "$last_successful"
        remote "$role" "sudo -n true" >/dev/null 2>&1 || record_preflight_failure "ssh_and_sudo" "environment" "$role sudo -n true 失败" "$last_successful"
    done
    sudo -n true || record_preflight_failure "ssh_and_sudo" "environment" "本机 sudo -n true 失败" "$last_successful"
    last_successful="ssh_and_sudo"

    # Layer 3: dependencies
    for name in ip iw systemctl journalctl make python3 uftp uftpd; do
        command -v "$name" >/dev/null 2>&1 || record_preflight_failure "dependencies" "tooling" "本机缺少命令：$name" "$last_successful"
    done
    for role in "${CLIENT_ROLES[@]}"; do
        remote "$role" "command -v ip iw systemctl journalctl make python3 uftp uftpd >/dev/null" || record_preflight_failure "dependencies" "tooling" "$role 缺少命令依赖" "$last_successful"
    done
    [ -f "$INITIAL_MODEL_PATH" ] && [ -r "$INITIAL_MODEL_PATH" ] || \
        record_preflight_failure "dependencies" "tooling" "server 初始模型不是可读取普通文件：$INITIAL_MODEL_PATH" "$last_successful"
    [ "$(stat -L -c %s "$INITIAL_MODEL_PATH")" -eq "$INPUT_SIZE_BYTES" ] || \
        record_preflight_failure "dependencies" "tooling" "server 初始模型必须恰好为 ${INPUT_SIZE_BYTES} 字节：$INITIAL_MODEL_PATH" "$last_successful"
    local template_shas=()
    for role in "${CLIENT_ROLES[@]}"; do
        local nid="${role#client}"
        local t_var="ISSUE41_${role^^}_UPDATE_TEMPLATE_PATH"
        local update_template_path="${!t_var:-/var/lib/wfb-ng/issue41-input/update-${role}-${INPUT_SIZE_BYTES}b.bin}"
        remote "$role" "sudo test -f '$update_template_path' && sudo test -r '$update_template_path' && test \"\$(sudo stat -L -c %s '$update_template_path')\" -eq '$INPUT_SIZE_BYTES'" || record_preflight_failure "dependencies" "tooling" "$role update 模板必须是可读取的 ${INPUT_SIZE_BYTES} 字节普通文件：$update_template_path" "$last_successful"
        local t_sha
        t_sha="$(remote "$role" "sudo sha256sum '$update_template_path' | awk '{print \$1}'")"
        template_shas+=("$t_sha")
        log_ok "$role preflight 基础检查通过"
    done
    local unique_shas
    unique_shas=$(printf '%s\n' "${template_shas[@]}" | sort -u | wc -l)
    [ "$unique_shas" -eq "${#template_shas[@]}" ] || record_preflight_failure "dependencies" "tooling" "所有 client 的 4 MiB update 模板 SHA-256 必须两两不同" "$last_successful"
    last_successful="dependencies"

    # Layer 4: wireless_usb & 重新发现拓扑
    python3 "$SCRIPT_DIR/issue41_envelope.py" discover-topology --archive-dir "$ARCHIVE_DIR" || record_preflight_failure "wireless_usb" "environment" "动态拓扑重新发现失败" "$last_successful"
    local ifaces
    ifaces="$(find_wlx)"
    [ "$(printf '%s\n' "$ifaces" | grep -c '^wlx' || true)" -eq 1 ] || record_preflight_failure "wireless_usb" "environment" "本机必须恰好发现一个 wlx* 网卡" "$last_successful"
    capture_local_radio_health server "$ifaces"
    check_radio_usb_speed server
    local remote_ifaces remote_iface_count
    for role in "${CLIENT_ROLES[@]}"; do
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
        for role in "${CLIENT_ROLES[@]}"; do
            if remote "$role" "ss -tuln 2>/dev/null" | grep -qE ":${port}\b"; then
                record_preflight_failure "network_ports_and_tun" "environment" "$role 端口 $port 已被占用" "$last_successful"
            fi
        done
    done
    if [ -e "/sys/class/net/$SERVER_TUN" ]; then
        record_preflight_failure "network_ports_and_tun" "environment" "server 上预期的 TUN 设备 $SERVER_TUN 已存在" "$last_successful"
    fi
    for role in "${CLIENT_ROLES[@]}"; do
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
    for role in "${CLIENT_ROLES[@]}"; do
        if remote "$role" "pgrep -x wfb-fl-client >/dev/null 2>&1 || pgrep -x wfb_v6_uplink >/dev/null 2>&1 || pgrep -x uftp >/dev/null 2>&1 || pgrep -x uftpd >/dev/null 2>&1"; then
            record_preflight_failure "residual_processes" "environment" "$role 发现残留 issue41 进程" "$last_successful"
        fi
    done
    last_successful="residual_processes"

    # Layer 8: managed_directory_boundary
    local has_runtime_state=0
    runtime_state_exists_local && has_runtime_state=1
    for role in "${CLIENT_ROLES[@]}"; do
        runtime_state_exists_remote "$role" && has_runtime_state=1
    done
    if [ "$has_runtime_state" -eq 1 ]; then
        if [ "$RESET_RUNTIME_STATE" != "1" ]; then
            record_preflight_failure "managed_directory_boundary" "tooling" "检测到上次 Runtime 现场；默认拒绝复用，请先执行 clean，或设置 ISSUE41_RESET_RUNTIME_STATE=1" "$last_successful"
        fi
        sudo rm -rf /var/lib/wfb-ng/issue41/server
        for role in "${CLIENT_ROLES[@]}"; do
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
    log_info "生成确定性验收 fixture (${INPUT_SIZE_BYTES} 字节)..."
    sudo install -d -m 0777 "$(dirname "$INITIAL_MODEL_PATH")"
    python3 -m wfb_ng.fl.issue41_fixtures generate --dest-dir "$(dirname "$INITIAL_MODEL_PATH")" --role model --output "$INITIAL_MODEL_PATH" --size "$INPUT_SIZE_BYTES"
    log_ok "server 初始模型生成完成：$INITIAL_MODEL_PATH"

    for role in "${CLIENT_ROLES[@]}"; do
        local nid="${role#client}"
        local t_var="ISSUE41_${role^^}_UPDATE_TEMPLATE_PATH"
        local update_template_path="${!t_var:-/var/lib/wfb-ng/issue41-input/update-${role}-${INPUT_SIZE_BYTES}b.bin}"
        remote "$role" "sudo install -d -m 0777 '$(dirname "$update_template_path")' && cd '$REMOTE_REPO' && python3 -m wfb_ng.fl.issue41_fixtures generate --dest-dir '$(dirname "$update_template_path")' --role '$role' --output '$update_template_path' --size '$INPUT_SIZE_BYTES'"
        log_ok "$role update 模板生成完成：$update_template_path"
    done
}

cmd_install() {
    make -C "$PROJECT_ROOT" build_v6
    sudo make -C "$PROJECT_ROOT" install_v8
    sudo systemctl daemon-reload
    for role in "${CLIENT_ROLES[@]}"; do
        remote "$role" "cd '$REMOTE_REPO' && make build_v6 && sudo make install_v8 && sudo systemctl daemon-reload"
        log_ok "$role 安装完成"
    done
    cmd_generate_fixtures
}

write_issue41_configs() {
    local role="$1" node_id tun addr work_dir algorithm result delay update_template_path ssh_target iface
    if [ "$role" = server ]; then
        node_id=255; tun="$SERVER_TUN"; addr="$SERVER_TUN_ADDR"; work_dir=/var/lib/wfb-ng/issue41/server
        algorithm=wfb_ng.fl.issue41_algorithm:server_main; result="$work_dir/issue41-server-result.json"
    else
        node_id="${role#client}"
        tun="$(client_tun "$role")"
        addr="$(client_addr "$role")"
        work_dir=/var/lib/wfb-ng/issue41/client
        algorithm=wfb_ng.fl.issue41_algorithm:client_main
        result="$work_dir/issue41-${role}-result.json"
        local d_var="ISSUE41_${role^^}_TRAINING_DELAY_MS"
        delay="${!d_var:-0}"
        local t_var="ISSUE41_${role^^}_UPDATE_TEMPLATE_PATH"
        update_template_path="${!t_var:-/var/lib/wfb-ng/issue41-input/update-${role}-${INPUT_SIZE_BYTES}b.bin}"
    fi
    local tmp short_gi_json=""
    if [ "$RADIO_SHORT_GI" = "1" ]; then
        short_gi_json=',"--radio-short-gi"'
    fi
    tmp="$(mktemp -d)"
    if [ "$role" = server ]; then
        local participant_ids=""
        local known_clients=""
        local client_targets_json=""
        local first=1
        for r in "${CLIENT_ROLES[@]}"; do
            local nid="${r#client}"
            if [ "$first" -eq 1 ]; then
                first=0
                participant_ids="$nid"
                known_clients="$nid"
            else
                participant_ids="$participant_ids,$nid"
                known_clients="$known_clients,$nid"
            fi
            client_targets_json+=',"--client-target","'"$nid:$(client_ip "$r"):127.0.0.1:1"'"'
        done
        cat > "$tmp/fl.json" <<EOF
{"schema_version":1,"role":"server","work_dir":"$work_dir","channel":$CHANNEL,"channel_width":"$CHANNEL_WIDTH","radio_txpower_dbm":$RADIO_TXPOWER_DBM,"node_id":255,"participant_node_ids":[$participant_ids],"participant_uftp_uids":[$participant_ids],"server_uftp_uid":255,"uftp_port":$UFTP_PORT,"uftp_rate_kbps":$UFTP_RATE_KBPS,"http_host":"$HTTP_HOST","http_port":$HTTP_PORT,"uftp_bind_host":"${SERVER_TUN_ADDR%/*}","uftp_multicast_host":"$UFTP_GROUP","uftp_private_multicast_host":"$UFTP_PRIVATE_GROUP","max_update_size_bytes":1073741824,"live_observation":true,"observation_path":"$work_dir/observation.jsonl","io_timeout_seconds":$IO_TIMEOUT_SECONDS,"link_args":["--tun-name","$tun","--tun-addr","$addr","--link-id","$LINK_ID","--uplink-stream","$UPLINK_STREAM","--downlink-stream","$DOWNLINK_STREAM","--fec-k","$FEC_K","--fec-n","$FEC_N","--radio-bandwidth","$RADIO_BANDWIDTH","--radio-mcs-index","$SERVER_RADIO_MCS_INDEX"$short_gi_json,"--log-interval","$LINK_LOG_INTERVAL_MS","--air-interface","$(find_wlx | head -n1)","--known-clients","$known_clients"$client_targets_json,"--grant-duration-ms","$GRANT_DURATION_MS","--guard-interval-ms","$GUARD_INTERVAL_MS","--downlink-pause-threshold-bytes","131072","--downlink-resume-threshold-bytes","65536","--downlink-queue-packets-limit","64","--feedback-window-period-ms","$FEEDBACK_WINDOW_PERIOD_MS","--feedback-window-duration-ms","$FEEDBACK_WINDOW_DURATION_MS","--queue-summary-file","$work_dir/server_queue_summary.json"]}
EOF
        cat > "$tmp/algorithm.json" <<EOF
{"rounds":$ROUNDS,"participant_node_ids":[$participant_ids],"initial_model_path":"$INITIAL_MODEL_PATH","required_artifact_size_bytes":$INPUT_SIZE_BYTES,"aggregation_delay_ms":$AGGREGATION_DELAY_MS,"result_path":"$result"}
EOF
        sudo install -d /etc/wfb-ng/issue41 /etc/systemd/system/wfb-fl-server.service.d
        sudo install -m 0644 "$tmp/fl.json" /etc/wfb-ng/issue41/fl-server.json
        sudo install -m 0644 "$tmp/algorithm.json" /etc/wfb-ng/issue41/server-algorithm.json
        printf '[Service]\nRestart=no\nLogRateLimitIntervalSec=0\nExecStart=\nExecStart=/usr/bin/wfb-fl-server --config /etc/wfb-ng/issue41/fl-server.json --algorithm %s --algorithm-config /etc/wfb-ng/issue41/server-algorithm.json\n' "$algorithm" | sudo tee /etc/systemd/system/wfb-fl-server.service.d/issue41.conf >/dev/null
    else
        iface="$(remote "$role" "iw dev | awk '/Interface / {print \$2}' | grep '^wlx' || true")"
        [ "$(printf '%s\n' "$iface" | grep -c '^wlx' || true)" -eq 1 ] || die "$role 必须恰好发现一个 wlx* 网卡"
        local client_mcs_var="ISSUE41_${role^^}_RADIO_MCS_INDEX"
        local client_mcs="${!client_mcs_var:-$CLIENT_RADIO_MCS_INDEX}"
        cat > "$tmp/fl.json" <<EOF
{"schema_version":1,"role":"client","work_dir":"$work_dir","node_id":$node_id,"uftp_uid":$node_id,"uftp_port":$UFTP_PORT,"server_http_host":"$HTTP_HOST","server_http_port":$HTTP_PORT,"uftp_bind_host":"${addr%/*}","uftp_multicast_host":"$UFTP_GROUP","uftp_private_multicast_host":"$UFTP_PRIVATE_GROUP","channel":$CHANNEL,"channel_width":"$CHANNEL_WIDTH","radio_txpower_dbm":$RADIO_TXPOWER_DBM,"max_update_size_bytes":1073741824,"live_observation":true,"observation_path":"$work_dir/observation.jsonl","io_timeout_seconds":$IO_TIMEOUT_SECONDS,"link_args":["--tun-name","$tun","--tun-addr","$addr","--link-id","$LINK_ID","--uplink-stream","$UPLINK_STREAM","--downlink-stream","$DOWNLINK_STREAM","--fec-k","$FEC_K","--fec-n","$FEC_N","--radio-bandwidth","$RADIO_BANDWIDTH","--radio-mcs-index","$client_mcs"$short_gi_json,"--log-interval","$LINK_LOG_INTERVAL_MS","--air-interface","$iface","--uplink-pause-threshold-bytes","131072","--uplink-resume-threshold-bytes","65536","--uplink-queue-packets-limit","64","--queue-summary-file","$work_dir/client${node_id}_queue_summary.json"]}
EOF
        cat > "$tmp/algorithm.json" <<EOF
{"rounds":$ROUNDS,"node_id":$node_id,"update_template_path":"$update_template_path","required_artifact_size_bytes":$INPUT_SIZE_BYTES,"training_delay_ms":$delay,"result_path":"$result"}
EOF
        cp "$tmp/fl.json" "/tmp/issue41-fl-$role.json"
        ssh_target="$(client_ssh "$role")"
        ssh -o BatchMode=yes "$ssh_target" "sudo install -d /etc/wfb-ng/issue41 /etc/systemd/system/wfb-fl-client.service.d"
        scp -q "$tmp/fl.json" "$ssh_target:/tmp/issue41-fl.json"
        scp -q "$tmp/algorithm.json" "$ssh_target:/tmp/issue41-algorithm.json"
        ssh -o BatchMode=yes "$ssh_target" "sudo install -m 0644 /tmp/issue41-fl.json /etc/wfb-ng/issue41/fl-client.json && sudo install -m 0644 /tmp/issue41-algorithm.json /etc/wfb-ng/issue41/client-algorithm.json && printf '[Service]\nRestart=no\nLogRateLimitIntervalSec=0\nExecStart=\nExecStart=/usr/bin/wfb-fl-client --config /etc/wfb-ng/issue41/fl-client.json --algorithm $algorithm --algorithm-config /etc/wfb-ng/issue41/client-algorithm.json\n' | sudo tee /etc/systemd/system/wfb-fl-client.service.d/issue41.conf >/dev/null"
    fi
    rm -rf "$tmp"
}

export_rf_gate_env() {
    export ISSUE41_CHANNEL="$CHANNEL"
    export ISSUE41_CHANNEL_WIDTH="$CHANNEL_WIDTH"
    export ISSUE41_LINK_ID="$LINK_ID"
    export ISSUE41_FEC_K="$FEC_K"
    export ISSUE41_FEC_N="$FEC_N"
    export ISSUE41_RADIO_BANDWIDTH="$RADIO_BANDWIDTH"
    export ISSUE41_RADIO_MCS_INDEX="$RADIO_MCS_INDEX"
    export ISSUE41_SERVER_RADIO_MCS_INDEX="$SERVER_RADIO_MCS_INDEX"
    export ISSUE41_CLIENT_RADIO_MCS_INDEX="$CLIENT_RADIO_MCS_INDEX"
    export ISSUE41_RADIO_SHORT_GI="$RADIO_SHORT_GI"
    export ISSUE41_RADIO_TXPOWER_DBM="$RADIO_TXPOWER_DBM"
    export ISSUE41_UFTP_RATE_KBPS="$UFTP_RATE_KBPS"
    export ISSUE41_GRANT_DURATION_MS="$GRANT_DURATION_MS"
    export ISSUE41_GUARD_INTERVAL_MS="$GUARD_INTERVAL_MS"
    for role in "${CLIENT_ROLES[@]}"; do
        local client_mcs_var="ISSUE41_${role^^}_RADIO_MCS_INDEX"
        if [ -n "${!client_mcs_var:-}" ]; then
            export "$client_mcs_var"="${!client_mcs_var}"
        fi
    done
}

cmd_verify_config_equivalence() {
    init_envelope
    log_info "执行角色服务与数据面 Gate 严格链路配置等价性比较..."
    mkdir -p "$ARCHIVE_DIR/formal_runtime_loop"
    write_issue41_configs server
    local client_fl_args=()
    for role in "${CLIENT_ROLES[@]}"; do
        write_issue41_configs "$role"
        client_fl_args+=(--client-fls "$role:/tmp/issue41-fl-${role}.json")
    done

    export_rf_gate_env

    python3 "$SCRIPT_DIR/issue41_gate.py" verify-config-equivalence \
        --server-fl /etc/wfb-ng/issue41/fl-server.json \
        "${client_fl_args[@]}" \
        --out "$ARCHIVE_DIR/formal_runtime_loop/config_equivalence.json" || {
            record_stage_failure "formal_runtime_loop" "implementation" "角色服务解析后配置与数据面 Gate 不等价" "pre_runtime_smoke"
            die "角色服务解析后配置与数据面 Gate 不等价"
        }
    log_ok "角色服务解析后链路配置与数据面 Gate 严格等价。"
}

smoke_dir() {
    local name="${1:-}"
    [ -n "$name" ] || die "smoke_dir 必须指定名称参数"
    printf '/var/tmp/wfb-ng-issue41-smoke/%s' "$name"
}
smoke_archive_dir() {
    local name="${1:-}"
    [ -n "$name" ] || die "smoke_archive_dir 必须指定名称参数"
    if [ "$name" != "gate" ]; then
        printf '%s/pre_runtime_smoke/%s' "$ARCHIVE_DIR" "$name"
    else
        printf '%s/pre_runtime_smoke' "$ARCHIVE_DIR"
    fi
}

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
        for role in "${CLIENT_ROLES[@]}"; do
            tun="$(client_tun "$role")"
            source_ip="$(client_ip "$role")"
            assert_runtime_uftp_route "$role" "$tun" "$source_ip" "$group"
        done
    done
}

wait_runtime_results() {
    local start_time=$SECONDS
    local overall_deadline=$((start_time + RUNTIME_TIMEOUT_SECONDS))
    local s_path="/var/lib/wfb-ng/issue41/server/issue41-server-result.json"

    while [ "$SECONDS" -lt "$overall_deadline" ]; do
        local server_done=0
        if [ -f "$s_path" ]; then
            if sudo python3 -c "import json, sys; sys.exit(0 if json.load(open(sys.argv[1], encoding='utf-8')).get('conclusion') == 'succeeded' else 1)" "$s_path" 2>/dev/null; then
                server_done=1
            fi
        fi
        local all_clients_done=1
        for role in "${CLIENT_ROLES[@]}"; do
            local c_path="/var/lib/wfb-ng/issue41/client/issue41-${role}-result.json"
            if ! remote "$role" "[ -f '$c_path' ] && sudo python3 -c \"import json, sys; sys.exit(0 if json.load(open(sys.argv[1], encoding='utf-8')).get('conclusion') == 'succeeded' else 1)\" '$c_path'" 2>/dev/null; then
                all_clients_done=0
                break
            fi
        done
        if [ "$server_done" -eq 1 ] && [ "$all_clients_done" -eq 1 ]; then
            local duration=$((SECONDS - start_time))
            log_ok "Runtime 作业在 ${duration}s 内全部成功（固定 deadline: ${RUNTIME_TIMEOUT_SECONDS}s）"
            return 0
        fi
        sleep 0.5
    done
    die "Runtime 作业超过固定整体 deadline (${RUNTIME_TIMEOUT_SECONDS}s) 未完成"
}

write_smoke_marker() {
    local name="$1" marker_dir
    marker_dir="$(smoke_archive_dir "$name")"
    mkdir -p "$marker_dir"
    local client_tuns_json=""
    local first=1
    for r in "${CLIENT_ROLES[@]}"; do
        local ct="$(client_tun "$r")"
        if [ "$first" -eq 1 ]; then
            first=0
            client_tuns_json="\"$ct\""
        else
            client_tuns_json="$client_tuns_json,\"$ct\""
        fi
    done
    cat > "$marker_dir/passed.json" <<EOF
{"schema_version":1,"smoke":"$name","gate_type":"three_cycle_bidirectional","status":"passed","run_id":"$RUN_ID","data_plane":"10.80.0.0/24","server_tun":"$SERVER_TUN","client_tuns":[$client_tuns_json]}
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
    sudo iw dev "$iface" set txpower fixed "$((RADIO_TXPOWER_DBM * 100))" || true
    ip -br link show "$iface" > "$archive/ip-link.txt" 2>&1 || true
    iw dev "$iface" info > "$archive/iw-info.txt" 2>&1 || true
}

configure_remote_monitor() {
    local role="$1" iface="$2" dir="$3"
    remote "$role" "sudo ip link set '$iface' down || true; sudo iw dev '$iface' set type monitor; sudo ip link set '$iface' up; sudo iw dev '$iface' set channel '$CHANNEL' '$CHANNEL_WIDTH'; sudo iw dev '$iface' set txpower fixed '$((RADIO_TXPOWER_DBM * 100))' || true; ip -br link show '$iface' > '$dir/ip-link.txt' 2>&1 || true; iw dev '$iface' info > '$dir/iw-info.txt' 2>&1 || true"
}

configure_runtime_monitors() {
    local server_iface server_archive
    server_iface="$(find_wlx)"
    [ "$(printf '%s\n' "$server_iface" | grep -c '^wlx' || true)" -eq 1 ] || die "server 必须恰好发现一个 wlx* 网卡"
    server_archive="$ARCHIVE_DIR/formal_runtime_loop/server/radio-health"
    mkdir -p "$server_archive"
    configure_local_monitor "$server_iface" "$server_archive"
    iw dev "$server_iface" info | grep -q 'type monitor' || die "server 空口网卡未进入 monitor 模式"
    ip link show "$server_iface" | grep -q '<[^>]*UP' || die "server 空口网卡未启动"
    capture_local_radio_health server "$server_iface" "$server_archive"

    for role in "${CLIENT_ROLES[@]}"; do
        local client_iface client_archive
        client_iface="$(remote "$role" "iw dev | awk '/Interface / {print \$2}' | grep '^wlx' || true")"
        [ "$(printf '%s\n' "$client_iface" | grep -c '^wlx' || true)" -eq 1 ] || die "$role 必须恰好发现一个 wlx* 网卡"
        client_archive="$ARCHIVE_DIR/formal_runtime_loop/$role/radio-health"
        mkdir -p "$client_archive"
        remote "$role" "rm -rf /tmp/issue41-runtime-radio; mkdir -p /tmp/issue41-runtime-radio"
        configure_remote_monitor "$role" "$client_iface" /tmp/issue41-runtime-radio
        remote "$role" "iw dev '$client_iface' info | grep -q 'type monitor' && ip link show '$client_iface' | grep -q '<[^>]*UP'" || die "$role 空口网卡 monitor/UP 验证失败"
        capture_remote_radio_health "$role" "$client_iface" "$client_archive"
    done
}

start_smoke_wfb() {
    local name="$1" server_dir server_iface short_gi
    server_dir="$(smoke_dir "$name")/server"
    cmd_stop_all
    sudo rm -rf "$(smoke_dir "$name")"
    sudo install -d "$server_dir"
    sudo chown "$(id -u):$(id -g)" "$server_dir"
    for role in "${CLIENT_ROLES[@]}"; do
        remote "$role" "sudo rm -rf '$(smoke_dir "$name")' && sudo install -d '$(smoke_dir "$name")/$role' && sudo chown \$(id -u):\$(id -g) '$(smoke_dir "$name")/$role'"
    done
    server_iface="$(find_wlx)"
    [ "$(printf '%s\n' "$server_iface" | grep -c '^wlx' || true)" -eq 1 ] || die "本机必须恰好发现一个 wlx* 网卡"
    configure_local_monitor "$server_iface" "$server_dir"

    local participant_ids=""
    local known_clients=""
    local client_targets=()
    local first=1
    for role in "${CLIENT_ROLES[@]}"; do
        local client_iface client_dir
        client_dir="$(smoke_dir "$name")/$role"
        client_iface="$(remote "$role" "iw dev | awk '/Interface / {print \$2}' | grep '^wlx' || true")"
        [ "$(printf '%s\n' "$client_iface" | grep -c '^wlx' || true)" -eq 1 ] || die "$role 必须恰好发现一个 wlx* 网卡"
        configure_remote_monitor "$role" "$client_iface" "$client_dir"
        capture_remote_radio_health "$role" "$client_iface" "$(smoke_archive_dir "$name")/$role/radio-health"

        local nid="${role#client}"
        if [ "$first" -eq 1 ]; then
            first=0
            participant_ids="$nid"
            known_clients="$nid"
        else
            participant_ids="$participant_ids,$nid"
            known_clients="$known_clients,$nid"
        fi
        client_targets+=(--client-target "$nid:$(client_ip "$role"):127.0.0.1:1")
    done
    capture_local_radio_health server "$server_iface" "$(smoke_archive_dir "$name")/server/radio-health"
    short_gi="$(issue41_wfb_short_gi_arg)"

    local ct_args=""
    for ct in "${client_targets[@]}"; do
        ct_args="$ct_args '$ct'"
    done

    sudo bash -c "cd '$PROJECT_ROOT' || exit 1; nohup '$PROJECT_ROOT/wfb_v6_uplink' --role server --tun-name '$SERVER_TUN' --tun-addr '$SERVER_TUN_ADDR' --node-id 255 --link-id '$LINK_ID' --uplink-stream '$UPLINK_STREAM' --downlink-stream '$DOWNLINK_STREAM' --fec-k '$FEC_K' --fec-n '$FEC_N' --radio-bandwidth '$RADIO_BANDWIDTH' --radio-mcs-index '$SERVER_RADIO_MCS_INDEX' $short_gi --air-interface '$server_iface' --known-clients '$known_clients' $ct_args --grant-duration-ms '$GRANT_DURATION_MS' --guard-interval-ms '$GUARD_INTERVAL_MS' --downlink-pause-threshold-bytes 131072 --downlink-resume-threshold-bytes 65536 --downlink-queue-packets-limit 64 --feedback-window-period-ms '$FEEDBACK_WINDOW_PERIOD_MS' --feedback-window-duration-ms '$FEEDBACK_WINDOW_DURATION_MS' --queue-summary-file '$server_dir/server_queue_summary.json' --log-interval 200 < /dev/null > '$server_dir/wfb.log' 2>&1 & echo \$! > '$server_dir/wfb.pid'; exit 0"

    for role in "${CLIENT_ROLES[@]}"; do
        local nid="${role#client}"
        local tun="$(client_tun "$role")"
        local addr="$(client_addr "$role")"
        local client_dir="$(smoke_dir "$name")/$role"
        local client_iface
        local client_mcs_var="ISSUE41_${role^^}_RADIO_MCS_INDEX"
        local client_mcs="${!client_mcs_var:-$CLIENT_RADIO_MCS_INDEX}"
        client_iface="$(remote "$role" "iw dev | awk '/Interface / {print \$2}' | grep '^wlx' || true")"
        remote "$role" "sudo bash -c \"cd '$REMOTE_REPO' || exit 1; nohup '$REMOTE_REPO/wfb_v6_uplink' --role client --tun-name '$tun' --tun-addr '$addr' --node-id $nid --link-id '$LINK_ID' --uplink-stream '$UPLINK_STREAM' --downlink-stream '$DOWNLINK_STREAM' --fec-k '$FEC_K' --fec-n '$FEC_N' --radio-bandwidth '$RADIO_BANDWIDTH' --radio-mcs-index '$client_mcs' $short_gi --air-interface '$client_iface' --uplink-pause-threshold-bytes 131072 --uplink-resume-threshold-bytes 65536 --uplink-queue-packets-limit 64 --queue-summary-file '$client_dir/${role}_queue_summary.json' --log-interval 200 < /dev/null > '$client_dir/wfb.log' 2>&1 & echo \\\$! > '$client_dir/wfb.pid'; exit 0\""
    done

    wait_local_tun "$SERVER_TUN" || die "server smoke TUN 未出现：$SERVER_TUN"
    for role in "${CLIENT_ROLES[@]}"; do
        wait_remote_tun "$role" "$(client_tun "$role")" || die "$role smoke TUN 未出现：$(client_tun "$role")"
    done

    local group
    for group in "$UFTP_GROUP" "$UFTP_PRIVATE_GROUP"; do
        sudo ip route replace "$group/32" dev "$SERVER_TUN"
        for role in "${CLIENT_ROLES[@]}"; do
            remote "$role" "sudo ip route replace '$group/32' dev '$(client_tun "$role")'"
        done
    done
}

collect_smoke_evidence() {
    local name="$1" archive smoke_root
    archive="$(smoke_archive_dir "$name")"
    smoke_root="$(smoke_dir "$name")"
    mkdir -p "$archive/server"
    sudo tar -C "$smoke_root/server" -czf /tmp/issue41-smoke-server.tgz . 2>/dev/null || true
    if [ -f /tmp/issue41-smoke-server.tgz ]; then
        tar -C "$archive/server" -xzf /tmp/issue41-smoke-server.tgz || true
    fi
    for role in "${CLIENT_ROLES[@]}"; do
        mkdir -p "$archive/$role"
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
    python3 - "$archive" "${CLIENT_ROLES[@]}" <<'PY'
import json
import os
import re
import sys

archive = sys.argv[1]
client_roles = sys.argv[2:] if len(sys.argv) > 2 else ['client1', 'client2']

def read_text(path):
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as fh:
            return fh.read()
    except OSError:
        return ''

server_log = read_text(os.path.join(archive, 'server', 'wfb.log'))
client_logs = {
    role: read_text(os.path.join(archive, role, 'wfb.log'))
    for role in client_roles
}
client_declared = {}
server_accepted = {}
for role in client_roles:
    m = re.search(r'\d+', role)
    node_id = m.group() if m else '1'
    client_declared[role] = ('first_declare node_id=%s' % node_id in client_logs[role])
    server_accepted[role] = ('ready_accept node_id=%s' % node_id in server_log)
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
    for role in "${CLIENT_ROLES[@]}"; do
        remote "$role" "! pgrep -x wfb_v6_uplink >/dev/null && ! pgrep -x uftp >/dev/null && ! pgrep -x uftpd >/dev/null"
    done
}

start_smoke_gate_environment() {
    local name=gate
    start_smoke_wfb "$name"

    # 启动各 client 的 uftpd 接收端
    for role in "${CLIENT_ROLES[@]}"; do
        local nid="${role#client}"
        local hex_uid=$(printf '0x%08x' "$nid")
        remote "$role" "sudo install -d '$(smoke_dir "$name")/$role/inbox' '$(smoke_dir "$name")/$role/tmp'; sudo bash -c \"nohup uftpd -d -q -I '$(client_ip "$role")' -M '$UFTP_GROUP' -p '$UFTP_PORT' -U '$hex_uid' -D '$(smoke_dir "$name")/$role/inbox' -T '$(smoke_dir "$name")/$role/tmp' -F '$(smoke_dir "$name")/$role/uftpd.status' > '$(smoke_dir "$name")/$role/uftpd.log' 2>&1 & echo \\\$! > '$(smoke_dir "$name")/$role/uftpd.pid'\""
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
        parts = client_ip.split('.')
        node_id = int(parts[3]) - 10 if (len(parts) == 4 and parts[0] == '10' and parts[1] == '80' and parts[2] == '0' and 11 <= int(parts[3]) <= 20) else 1
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

        complete = read_bytes == length
        status = 201 if complete else 400
        outcome = 'committed' if complete else 'incomplete_body'
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
            'status': status,
            'outcome': outcome,
            'start_time': t0,
            'end_time': t1,
        })
        self.send_response(status)
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

    # 记录复用进程 PID 矩阵
    local s_link_pid s_http_pid
    s_link_pid="$(cat "$server_dir/wfb.pid")"
    s_http_pid="$(cat "$server_dir/http-server.pid")"

    local pids_args=()
    for role in "${CLIENT_ROLES[@]}"; do
        local c_link c_uftpd
        c_link="$(remote "$role" "cat '$(smoke_dir "$name")/$role/wfb.pid'")"
        c_uftpd="$(remote "$role" "cat '$(smoke_dir "$name")/$role/uftpd.pid'")"
        pids_args+=("$role" "$c_link" "$c_uftpd")
    done

    python3 - "$server_dir/reused_processes.json" "$s_link_pid" "$s_http_pid" "${pids_args[@]}" <<'PY'
import json, sys
out, s_link, s_http = sys.argv[1:4]
rest = sys.argv[4:]
data = {
    'server_link_pid': int(s_link),
    'server_http_pid': int(s_http),
}
for i in range(0, len(rest), 3):
    role = rest[i]
    c_link = rest[i+1]
    c_uftpd = rest[i+2]
    data[f'{role}_link_pid'] = int(c_link)
    data[f'{role}_uftpd_pid'] = int(c_uftpd)
with open(out, 'w', encoding='utf-8') as fh:
    json.dump(data, fh, indent=2)
PY
}

cmd_smoke_gate() {
    local name=gate archive server_dir
    archive="$(smoke_archive_dir "$name")"
    server_dir="$(smoke_dir "$name")/server"

    init_envelope
    trap 'stop_smoke_gate_processes; collect_smoke_evidence "$name" || true; handle_smoke_gate_failure' ERR

    export ISSUE41_INPUT_SIZE_BYTES="$INPUT_SIZE_BYTES"
    export ISSUE41_SMOKE_CYCLE_COUNT="$SMOKE_CYCLE_COUNT"
    export ISSUE41_SMOKE_IO_TIMEOUT_SECONDS="$SMOKE_IO_TIMEOUT_SECONDS"
    export ISSUE41_SMOKE_CYCLE_DEADLINE_SECONDS="$SMOKE_CYCLE_DEADLINE_SECONDS"
    export_rf_gate_env

    log_info "开始数据面 Gate 验收 (共 $SMOKE_CYCLE_COUNT 周期，单载荷 $((INPUT_SIZE_BYTES / 1024 / 1024)) MiB)..."
    start_smoke_gate_environment

    local cycle t_cycle_start t_cycle_end cycle_dur
    local t_dl_start t_dl_end dl_dur
    local t_ul_start t_ul_end ul_dur
    local work src model manifest status log
    local cycle_files=()
    local s_lines_before=0
    local -A c_lines_before

    for cycle in $(seq 1 "$SMOKE_CYCLE_COUNT"); do
        log_info "=== 运行数据面 Gate 周期 $cycle / $SMOKE_CYCLE_COUNT ==="
        t_cycle_start="$(python3 -c 'import time; print(time.monotonic())')"

        # 记录本周期启动前各节点日志行数，用于准确截取单周期遥测 (fail-closed，拒绝容错回退)
        [ -f "$server_dir/wfb.log" ] || die "缺少 server wfb.log"
        s_lines_before=$(wc -l < "$server_dir/wfb.log")
        for role in "${CLIENT_ROLES[@]}"; do
            c_lines_before["$role"]=$(remote "$role" "test -f '$(smoke_dir "$name")/$role/wfb.log' && wc -l < '$(smoke_dir "$name")/$role/wfb.log'") || die "读取 $role wfb.log 行数失败"
        done

        # 1. 确定性生成交付物与 update 文件
        work="$server_dir/cycle$cycle"
        sudo install -d "$work"
        sudo chown "$(id -u):$(id -g)" "$work"
        src="issue41-cycle$cycle"
        model="$work/model.bin"
        manifest="$work/model.manifest.json"

        # 生成 server 端下行 model 和 manifest
        sudo env PYTHONPATH="$PROJECT_ROOT" python3 - "$model" "$manifest" "$INPUT_SIZE_BYTES" "$cycle" <<'PY'
import json, sys
from wfb_ng.fl.issue41_fixtures import cycle_model_pattern, generate_deterministic_file
model, manifest, size_str, cycle_str = sys.argv[1:]
size = int(size_str)
meta = generate_deterministic_file(model, size_bytes=size, pattern=cycle_model_pattern(cycle_str))
with open(manifest, 'w', encoding='utf-8') as fh:
    json.dump({'schema_version': 1, 'artifact_type': 'model', 'size_bytes': size, 'sha256': meta['sha256']}, fh, separators=(',', ':'))
PY

        # 生成各 client 不同的 update 文件
        for role in "${CLIENT_ROLES[@]}"; do
            local nid="${role#client}"
            remote "$role" "sudo install -d '$(smoke_dir "$name")/$role/cycle$cycle' && sudo env PYTHONPATH='$REMOTE_REPO' python3 - '$(smoke_dir "$name")/$role/cycle$cycle/update.bin' '$INPUT_SIZE_BYTES' '$cycle' '$nid' <<'PY'
import sys
from wfb_ng.fl.issue41_fixtures import cycle_client_pattern, generate_deterministic_file
path, size_str, cycle_str, nid = sys.argv[1:]
size = int(size_str)
meta = generate_deterministic_file(path, size_bytes=size, pattern=cycle_client_pattern(cycle_str, nid))
with open(path + '.sha256', 'w', encoding='utf-8') as fh:
    fh.write(meta['sha256'])
PY"
        done

        # 2. Shared UFTP 下行
        t_dl_start="$(python3 -c 'import time; print(time.monotonic())')"
        status="$work/uftp.status"
        log="$work/uftp.log"
        local uftp_hosts=""
        local first=1
        for role in "${CLIENT_ROLES[@]}"; do
            local nid="${role#client}"
            local hex_uid=$(printf '0x%08x' "$nid")
            if [ "$first" -eq 1 ]; then
                first=0
                uftp_hosts="$hex_uid"
            else
                uftp_hosts="$uftp_hosts,$hex_uid"
            fi
        done
        sudo timeout "$SMOKE_IO_TIMEOUT_SECONDS" bash -c "cd '$work' && uftp -q -I '${SERVER_TUN_ADDR%/*}' -M '$UFTP_GROUP' -P '$UFTP_PRIVATE_GROUP' -p '$UFTP_PORT' -U 0x000000ff -H '$uftp_hosts' -Y none -R '$UFTP_RATE_KBPS' -r 0.1:0.01:2.0 -s 20 -L '$log' -S '$status' -D '$src' 'model.bin' 'model.manifest.json'" || die "周期 $cycle shared UFTP 下行超时或失败"
        t_dl_end="$(python3 -c 'import time; print(time.monotonic())')"
        dl_dur="$(python3 -c "print($t_dl_end - $t_dl_start)")"

        # 下行结果验证
        for role in "${CLIENT_ROLES[@]}"; do
            remote "$role" "for i in \$(seq 1 30); do [ -d '$(smoke_dir "$name")/$role/inbox/$src' ] && break; sleep 0.5; done"
            remote "$role" "sudo chown -R \$(id -u):\$(id -g) '$(smoke_dir "$name")/$role/inbox'"
            scp -rq "$(client_ssh "$role"):$(smoke_dir "$name")/$role/inbox/$src" "$server_dir/$role-inbox-$src"
        done

        python3 - "$work" "$status" "$dl_dur" "$server_dir/cycle${cycle}_downlink.json" "$server_dir" "$src" "${CLIENT_ROLES[@]}" <<'PY'
import json, os, sys
server_cycle_dir, status_path, duration_str, out_path, server_dir, src = sys.argv[1:7]
client_roles = sys.argv[7:]
from tests.real_hardware.issue41_gate import verify_downlink_artifacts, GateConfig
cfg = GateConfig.from_env()
client_inboxes = {
    role.replace('client', ''): os.path.join(server_dir, f"{role}-inbox-{src}")
    for role in client_roles
}
res = verify_downlink_artifacts(
    server_cycle_dir=server_cycle_dir,
    client_inboxes=client_inboxes,
    status_file=status_path,
    config=cfg,
    duration_seconds=float(duration_str),
)
with open(out_path, 'w', encoding='utf-8') as fh:
    json.dump(res, fh, indent=2)
if res['status'] != 'passed':
    raise SystemExit('UFTP 下行校验失败: %r' % res)
PY
        [ $? -eq 0 ] || die "Gate 周期 $cycle UFTP 下行校验未通过"

        # 3. 各 client 并发 HTTP PUT 上行
        > "$server_dir/server-put-events.jsonl"
        t_ul_start="$(python3 -c 'import time; print(time.monotonic())')"
        local put_pids=()
        for role in "${CLIENT_ROLES[@]}"; do
            local nid="${role#client}"
            remote "$role" "sudo env PYTHONPATH='$REMOTE_REPO' timeout '$SMOKE_IO_TIMEOUT_SECONDS' python3 - '$(smoke_dir "$name")/$role/cycle$cycle' '$(client_ip "$role")' '$HTTP_HOST' '$HTTP_PORT' '$role' '$nid' <<'PY'
import http.client, json, os, sys, time
from wfb_ng.fl.issue41_fixtures import file_sha256
work, source_ip, host, port, role, nid = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5], int(sys.argv[6])
update_file = os.path.join(work, 'update.bin')
with open(update_file, 'rb') as fh:
    body = fh.read()
digest = file_sha256(update_file)
path = '/client%d' % nid
start = time.monotonic()
conn = http.client.HTTPConnection(host, port, timeout=120, source_address=(source_ip, 0))
conn.request('PUT', path, body=body, headers={'Content-Length': str(len(body)), 'Content-Type': 'application/octet-stream', 'Connection': 'close'})
resp = conn.getresponse()
resp.read()
end = time.monotonic()
conn.close()
with open(os.path.join(work, 'client-put-result.json'), 'w', encoding='utf-8') as fh:
    json.dump({'role': role, 'node_id': nid, 'path': path, 'http_status': resp.status, 'bytes': len(body), 'sha256': digest, 'start_monotonic': start, 'end_monotonic': end}, fh, separators=(',', ':'))
if resp.status != 201:
    raise SystemExit('unexpected HTTP status %s' % resp.status)
PY" &
            put_pids+=($!)
        done
        local put_err=0
        for pid in "${put_pids[@]}"; do
            wait "$pid" || put_err=$((put_err + 1))
        done
        [ $put_err -eq 0 ] || die "Gate 周期 $cycle HTTP PUT 上行进程失败"
        for role in "${CLIENT_ROLES[@]}"; do
            scp -q "$(client_ssh "$role"):$(smoke_dir "$name")/$role/cycle$cycle/client-put-result.json" "$server_dir/$role-put-result-cycle$cycle.json"
        done
        t_ul_end="$(python3 -c 'import time; print(time.monotonic())')"
        ul_dur="$(python3 -c "print($t_ul_end - $t_ul_start)")"
        cp "$server_dir/server-put-events.jsonl" "$server_dir/server-put-events-cycle$cycle.jsonl"

        # 上行结果验证
        python3 - "$server_dir/server-put-events-cycle$cycle.jsonl" "$ul_dur" "$server_dir/cycle${cycle}_uplink.json" "$server_dir" "$cycle" "${CLIENT_ROLES[@]}" <<'PY'
import json, os, sys
events_file, duration_str, out_path, server_dir, cycle_str = sys.argv[1:6]
client_roles = sys.argv[6:]
from tests.real_hardware.issue41_gate import verify_uplink_cycle, GateConfig
events = []
with open(events_file, 'r', encoding='utf-8') as fh:
    for line in fh:
        line = line.strip()
        if line:
            events.append(json.loads(line))
c_res = {}
for role in client_roles:
    nid_str = role.replace('client', '')
    res_path = os.path.join(server_dir, f"{role}-put-result-cycle{cycle_str}.json")
    with open(res_path, 'r', encoding='utf-8') as fh:
        c = json.load(fh)
    c_res[nid_str] = {
        'node_id': int(nid_str),
        'status': c['http_status'],
        'size_bytes': c['bytes'],
        'sha256': c['sha256'],
        'start_time': c['start_monotonic'],
        'end_time': c['end_monotonic'],
    }
cfg = GateConfig.from_env()
res = verify_uplink_cycle(events, c_res, cfg, float(duration_str))
with open(out_path, 'w', encoding='utf-8') as fh:
    json.dump(res, fh, indent=2)
if res['status'] != 'passed':
    raise SystemExit('HTTP PUT 上行校验失败: %r' % res)
PY
        [ $? -eq 0 ] || die "Gate 周期 $cycle HTTP PUT 校验未通过"

        # 4. 采集遥测并校验周期契约
        for role in "${CLIENT_ROLES[@]}"; do
            scp -q "$(client_ssh "$role"):$(smoke_dir "$name")/$role/wfb.log" "$server_dir/$role-wfb.log" || die "scp 采集 $role wfb.log 失败"
            scp -q "$(client_ssh "$role"):$(smoke_dir "$name")/$role/${role}_queue_summary.json" "$server_dir/$role-queue.json" 2>/dev/null || true
            tail -n +"$((c_lines_before["$role"] + 1))" "$server_dir/$role-wfb.log" > "$server_dir/cycle${cycle}_${role}-wfb.log"
        done

        # 截取本周期的日志切片（fail-closed，无容错回退）
        [ -f "$server_dir/wfb.log" ] || die "缺少 server wfb.log"
        tail -n +"$((s_lines_before + 1))" "$server_dir/wfb.log" > "$server_dir/cycle${cycle}_server-wfb.log"

        python3 - "$cycle" "$server_dir/cycle${cycle}_downlink.json" "$server_dir/cycle${cycle}_uplink.json" "$server_dir/cycle${cycle}_server-wfb.log" "$server_dir/server_queue_summary.json" "$dl_dur" "$ul_dur" "$server_dir/cycle$cycle.json" "$server_dir" "${CLIENT_ROLES[@]}" <<'PY'
import json, os, sys
cycle_idx, dl_path, ul_path, s_log_p, sq_p, dl_dur, ul_dur, out_path, server_dir = sys.argv[1:10]
client_roles = sys.argv[10:]
from tests.real_hardware.issue41_gate import parse_telemetry, build_cycle_evidence, validate_cycle_evidence, GateConfig
with open(dl_path, 'r', encoding='utf-8') as fh:
    dl = json.load(fh)
with open(ul_path, 'r', encoding='utf-8') as fh:
    ul = json.load(fh)

s_log = open(s_log_p, 'r', encoding='utf-8', errors='replace').read() if os.path.isfile(s_log_p) else ''
sq = json.load(open(sq_p, 'r', encoding='utf-8')) if os.path.isfile(sq_p) else {}

client_logs = {}
queue_summaries = {'server': sq}
for role in client_roles:
    nid_str = role.replace('client', '')
    c_log_p = os.path.join(server_dir, f"cycle{cycle_idx}_{role}-wfb.log")
    cq_p = os.path.join(server_dir, f"{role}-queue.json")
    client_logs[nid_str] = open(c_log_p, 'r', encoding='utf-8', errors='replace').read() if os.path.isfile(c_log_p) else ''
    queue_summaries[role] = json.load(open(cq_p, 'r', encoding='utf-8')) if os.path.isfile(cq_p) else {}

durations = {
    'downlink_seconds': float(dl_dur),
    'uplink_seconds': float(ul_dur),
    'cycle_total_seconds': float(dl_dur) + float(ul_dur),
}
telem = parse_telemetry(
    server_log=s_log,
    client_logs=client_logs,
    queue_summaries=queue_summaries,
    phase_durations=durations,
)
cfg = GateConfig.from_env()
cycle_ev = build_cycle_evidence(
    cycle_index=int(cycle_idx),
    status='passed',
    downlink=dl,
    uplink=ul,
    telemetry=telem,
    total_duration_seconds=float(dl_dur) + float(ul_dur),
    config=cfg,
)
errors = validate_cycle_evidence(cycle_ev, cfg)
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
        for role in "${CLIENT_ROLES[@]}"; do
            remote "$role" "sudo rm -rf '$(smoke_dir "$name")/$role/inbox'/* '$(smoke_dir "$name")/$role/cycle$cycle'"
        done
        cycle_files+=("$server_dir/cycle$cycle.json")
        log_ok "Gate 周期 $cycle 验收通过 (耗时: ${cycle_dur}s)"
        python3 "$SCRIPT_DIR/issue41_gate.py" format-cycle-telemetry --cycle-json "$server_dir/cycle$cycle.json"
    done

    # 停止进程并清理收尾
    stop_smoke_gate_processes
    verify_no_smoke_orphans
    collect_smoke_evidence "$name"

    mkdir -p "$archive"
    python3 - "$archive/gate_summary.json" "$RUN_ID" "$server_dir/reused_processes.json" "${cycle_files[@]}" <<'PY'
import json, sys
out_path, run_id, pids_path = sys.argv[1:4]
cycle_paths = sys.argv[4:]
from tests.real_hardware.issue41_gate import build_gate_summary, validate_gate_summary, GateConfig
with open(pids_path, 'r', encoding='utf-8') as fh:
    pids = json.load(fh)
cycles = [json.load(open(cp, 'r', encoding='utf-8')) for cp in cycle_paths]
cfg = GateConfig.from_env()
summary = build_gate_summary(
    run_id=run_id,
    status='passed',
    cycles=cycles,
    reused_processes=pids,
    config=cfg,
)
errors = validate_gate_summary(summary, cfg)
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

    python3 "$SCRIPT_DIR/issue41_gate.py" format-summary-telemetry --summary-json "$archive/gate_summary.json"

    trap - ERR
    log_ok "数据面 Gate 全部通过 (共 $SMOKE_CYCLE_COUNT 周期)！"
}

handle_smoke_gate_failure() {
    log_warn "Gate 运行发生失败，正在进行受控停止与现场诊断归档..."
    local name=gate archive server_dir
    archive="$(smoke_archive_dir "$name")"
    server_dir="$(smoke_dir "$name")/server"
    stop_smoke_gate_processes || true
    collect_smoke_evidence "$name" || true

    mkdir -p "$archive"
    python3 - "$archive" "$RUN_ID" "${CLIENT_ROLES[@]}" <<'PY'
import json, os, sys
archive_dir, run_id = sys.argv[1:3]
client_roles = sys.argv[3:]
from tests.real_hardware.issue41_gate import classify_gate_failure

s_log_p = os.path.join(archive_dir, 'server', 'wfb.log')
s_log = open(s_log_p, 'r', encoding='utf-8', errors='replace').read() if os.path.isfile(s_log_p) else ''
ant_samples = s_log.count('\tRX_ANT\t')

c_decl = {}
s_acc = {}
for role in client_roles:
    nid = role.replace('client', '')
    c_log_p = os.path.join(archive_dir, role, 'wfb.log')
    c_log = open(c_log_p, 'r', encoding='utf-8', errors='replace').read() if os.path.isfile(c_log_p) else ''
    c_decl[role] = f'first_declare node_id={nid}' in c_log
    s_acc[role] = f'ready_accept node_id={nid}' in s_log

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

    local fail_cat fail_reason last_layer first_layer
    fail_cat="$(python3 -c "import json; print(json.load(open('$archive/gate_summary.json'))['failure_category'])" 2>/dev/null || echo 'link_capability')"
    fail_reason="$(python3 -c "import json; print(json.load(open('$archive/gate_summary.json'))['failure_reason'])" 2>/dev/null || echo '数据面 Gate 验收失败')"
    last_layer="$(python3 -c "import json; print(json.load(open('$archive/gate_summary.json'))['last_successful_layer'])" 2>/dev/null || echo 'orchestration')"
    first_layer="$(python3 -c "import json; print(json.load(open('$archive/gate_summary.json'))['first_failing_layer'])" 2>/dev/null || echo 'pre_runtime_smoke')"

    python3 "$SCRIPT_DIR/issue41_envelope.py" record-stage-failure \
        --archive-dir "$ARCHIVE_DIR" \
        --partition-name pre_runtime_smoke \
        --json-file "$archive/gate_summary.json" \
        --category "$fail_cat" \
        --reason "$fail_reason" \
        --last-successful-layer "$last_layer" \
        --first-failing-layer "$first_layer" >/dev/null 2>&1 || true

    cmd_stop_all 2>/dev/null || true
    cmd_collect 2>/dev/null || true
    cmd_summary 2>/dev/null || true

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
    for role in "${CLIENT_ROLES[@]}"; do
        remote "$role" "! pgrep -x wfb-fl-client >/dev/null && ! pgrep -x wfb_v6_uplink >/dev/null && ! pgrep -x uftp >/dev/null && ! pgrep -x uftpd >/dev/null" || die "$role 启动 Runtime 前仍有 issue41 相关进程"
    done
}

prepare_runtime_state() {
    cmd_stop_all
    assert_runtime_processes_stopped
    local has_runtime_state=0
    runtime_state_exists_local && has_runtime_state=1
    for role in "${CLIENT_ROLES[@]}"; do
        runtime_state_exists_remote "$role" && has_runtime_state=1
    done
    if [ "$has_runtime_state" -eq 1 ]; then
        if [ "$RESET_RUNTIME_STATE" != "1" ]; then
            die "检测到上次 Runtime 现场；默认拒绝复用，请先执行 clean，或设置 ISSUE41_RESET_RUNTIME_STATE=1"
        fi
        sudo rm -rf /var/lib/wfb-ng/issue41/server
        for role in "${CLIENT_ROLES[@]}"; do
            remote "$role" "sudo rm -rf /var/lib/wfb-ng/issue41/client"
        done
        log_warn "已按 ISSUE41_RESET_RUNTIME_STATE=1 清理旧 Runtime work_dir"
    fi
}

cmd_run_runtime_loop() {
    prepare_runtime_state
    configure_runtime_monitors
    write_issue41_configs server
    for role in "${CLIENT_ROLES[@]}"; do
        write_issue41_configs "$role"
    done
    if [ ! -f "$ARCHIVE_DIR/formal_runtime_loop/config_equivalence.json" ]; then
        cmd_verify_config_equivalence
    fi

    sudo systemctl daemon-reload
    for role in "${CLIENT_ROLES[@]}"; do
        remote "$role" "sudo systemctl daemon-reload"
        wait_remote_service_ready "$role" wfb-fl-client.service || {
            record_stage_failure "formal_runtime_loop" "implementation" "$role 角色服务就绪超时" "pre_runtime_smoke"
            die "$role 角色服务就绪超时"
        }
        assert_remote_service_active "$role" wfb-fl-client.service || {
            record_stage_failure "formal_runtime_loop" "implementation" "$role 角色服务未激活" "pre_runtime_smoke"
            die "$role 角色服务未激活"
        }
    done
    sudo systemctl restart wfb-fl-server.service || {
        record_stage_failure "formal_runtime_loop" "implementation" "server 角色服务启动失败" "pre_runtime_smoke"
        die "server 角色服务启动失败"
    }

    log_info "记录各角色服务首次运行 MainPID 以备生命周期 no-overlap 验证..."
    mkdir -p "$ARCHIVE_DIR/lifecycle"
    server_main_pid="$(sudo systemctl show -p MainPID --value wfb-fl-server.service 2>/dev/null || echo 0)"
    local initial_pids_json="\"server\": $server_main_pid"
    for role in "${CLIENT_ROLES[@]}"; do
        local c_pid
        c_pid="$(remote "$role" "sudo systemctl show -p MainPID --value wfb-fl-client.service 2>/dev/null || echo 0")"
        initial_pids_json+=", \"$role\": $c_pid"
    done
    cat > "$ARCHIVE_DIR/lifecycle/initial_pids.json" <<EOF
{$initial_pids_json}
EOF

    assert_runtime_uftp_routes
    capture_runtime_routes
    wait_runtime_results || {
        record_stage_failure "formal_runtime_loop" "implementation" "Runtime 结果超时或未完成" "pre_runtime_smoke"
        die "Runtime 结果超时或未完成"
    }

    log_info "正式 Runtime 作业已成功返回，执行各角色服务受控停止..."
    sudo systemctl stop wfb-fl-server.service || true
    for role in "${CLIENT_ROLES[@]}"; do
        remote "$role" "sudo systemctl stop wfb-fl-client.service || true"
    done
    wait_local_issue41_cleanup || {
        record_stage_failure "formal_runtime_loop" "implementation" "server 角色服务受控停止后清理超时" "pre_runtime_smoke"
        die "server 角色服务受控停止后清理超时"
    }
    for role in "${CLIENT_ROLES[@]}"; do
        wait_remote_issue41_cleanup "$role" "$(client_tun "$role")" || {
            record_stage_failure "formal_runtime_loop" "implementation" "$role 角色服务受控停止后清理超时" "pre_runtime_smoke"
            die "$role 角色服务受控停止后清理超时"
        }
    done
    local client_stops_json=""
    for role in "${CLIENT_ROLES[@]}"; do
        client_stops_json+="\"${role}_stopped\":true,"
    done
    cat > "$ARCHIVE_DIR/formal_runtime_loop/controlled_stop.json" <<EOF
{"schema_version":1,"status":"passed","server_stopped":true,$client_stops_json"cleaned":true}
EOF
    log_ok "各角色服务受控停止完成。"
    log_ok "Runtime loop 作业与 UFTP 组播路由验收通过。"
}

cmd_lifecycle_stop_restart() {
    log_info "执行角色服务 stop/restart/no-overlap 及资源生命周期审计..."
    cmd_collect
    local lc_args=()
    for role in "${CLIENT_ROLES[@]}"; do
        lc_args+=(--client-tuns "$role:$(client_tun "$role")")
        lc_args+=(--client-sshs "$role:$(client_ssh "$role")")
    done
    python3 "$SCRIPT_DIR/issue41_lifecycle.py" run "$ARCHIVE_DIR" \
        --server-tun "$SERVER_TUN" \
        "${lc_args[@]}" \
        --initial-pids "$ARCHIVE_DIR/lifecycle/initial_pids.json" \
        --timeout "$STOP_CLEANUP_TIMEOUT_SECONDS" || {
            record_stage_failure "lifecycle" "implementation" "角色服务 stop/restart 及资源生命周期审计未通过" "formal_runtime_loop"
            die "角色服务 stop/restart 及资源生命周期审计未通过"
        }
    if [ -f "$ARCHIVE_DIR/envelope.json" ]; then
        python3 "$SCRIPT_DIR/issue41_envelope.py" append-partition \
            --archive-dir "$ARCHIVE_DIR" \
            --name lifecycle \
            --json-file "$ARCHIVE_DIR/lifecycle/lifecycle_summary.json" >/dev/null 2>&1 || true
    fi
    log_ok "角色服务 stop/restart 及资源清理审计通过。"
}

cmd_stop_all() {
    sudo systemctl stop wfb-fl-server.service 2>/dev/null || true
    sudo pkill -x wfb_v6_uplink 2>/dev/null || true
    sudo pkill -x uftp 2>/dev/null || true
    sudo pkill -x uftpd 2>/dev/null || true
    sudo pkill -f /var/tmp/wfb-ng-issue41-smoke 2>/dev/null || true
    for role in "${CLIENT_ROLES[@]}"; do
        remote "$role" "sudo systemctl stop wfb-fl-client.service 2>/dev/null || true; sudo pkill -x wfb_v6_uplink 2>/dev/null || true; sudo pkill -x uftp 2>/dev/null || true; sudo pkill -x uftpd 2>/dev/null || true; sudo pkill -f /var/tmp/wfb-ng-issue41-smoke 2>/dev/null || true" || true
    done
    wait_local_issue41_cleanup || die "本机 issue41 停止后清理超时"
    for role in "${CLIENT_ROLES[@]}"; do
        wait_remote_issue41_cleanup "$role" "$(client_tun "$role")" || die "$role issue41 停止后清理超时"
    done
}

cmd_clean() {
    cmd_stop_all
    sudo rm -rf /var/lib/wfb-ng/issue41/server /var/tmp/wfb-ng-issue41-smoke /etc/wfb-ng/issue41 /etc/systemd/system/wfb-fl-server.service.d/issue41.conf
    for role in "${CLIENT_ROLES[@]}"; do
        remote "$role" "sudo rm -rf /var/lib/wfb-ng/issue41/client /var/tmp/wfb-ng-issue41-smoke /etc/wfb-ng/issue41 /etc/systemd/system/wfb-fl-client.service.d/issue41.conf"
    done
    log_ok "issue41 管理路径已清理"
}

capture_runtime_routes() {
    local role group tun source_ip prefix
    local all_roles=("server" "${CLIENT_ROLES[@]}")
    for role in "${all_roles[@]}"; do
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
    for role in "${CLIENT_ROLES[@]}"; do
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
    local expected_route_files=$(( (${#CLIENT_ROLES[@]} + 1) * 4 ))
    if [ "$(find "$ARCHIVE_DIR/raw" -maxdepth 1 -name '*-route-*.txt' -type f | wc -l)" -lt "$expected_route_files" ]; then
        capture_runtime_routes
    fi
    log_ok "归档采集完成：$ARCHIVE_DIR"
}

cmd_summary() {
    local server_result conclusion status reason route_evidence role group suffix
    server_result="$ARCHIVE_DIR/formal_runtime_loop/server/issue41-server-result.json"
    route_evidence=
    local all_roles=("server" "${CLIENT_ROLES[@]}")
    for role in "${all_roles[@]}"; do
        for group in "$UFTP_GROUP" "$UFTP_PRIVATE_GROUP"; do
            suffix="${group//./-}"
            for direction in show get; do
                [ -z "$route_evidence" ] || route_evidence="$route_evidence,"
                route_evidence="$route_evidence\"$ARCHIVE_DIR/raw/$role-route-$suffix-$direction.txt\""
            done
        done
    done
    python3 "$SCRIPT_DIR/issue41_build_summary.py" "$ARCHIVE_DIR" > "$ARCHIVE_DIR/formal-runtime-summary.json"
    python3 - "$ARCHIVE_DIR" "$route_evidence" "${CLIENT_ROLES[@]}" <<'PY'
import json
import os
import sys

archive_dir, route_evidence = sys.argv[1:3]
client_roles = sys.argv[3:]
with open(os.path.join(archive_dir, 'formal-runtime-summary.json'), encoding='utf-8') as fh:
    formal = json.load(fh)

preflight_res_path = os.path.join(archive_dir, 'orchestration', 'preflight_result.json')
orch_status = 'passed'
orch_reason = None
orch_category = None
if os.path.isfile(preflight_res_path):
    with open(preflight_res_path, 'r', encoding='utf-8') as pf:
        p_res = json.load(pf)
        orch_status = p_res.get('status', 'passed')
        orch_reason = p_res.get('failure_reason')
        orch_category = p_res.get('failure_category')

smoke_gate_path = os.path.join(archive_dir, 'pre_runtime_smoke', 'gate_summary.json')
passed_marker_path = os.path.join(archive_dir, 'pre_runtime_smoke', 'passed.json')
if os.path.isfile(smoke_gate_path):
    with open(smoke_gate_path, 'r', encoding='utf-8') as sf:
        smoke_gate = json.load(sf)
elif os.path.isfile(passed_marker_path):
    with open(passed_marker_path, 'r', encoding='utf-8') as sf:
        smoke_gate = json.load(sf)
else:
    if orch_status != 'passed':
        smoke_gate = {'status': 'skipped', 'reason': 'preflight 检查未通过，跳过数据面 Gate'}
    else:
        smoke_gate = {'status': 'failed', 'reason': 'gate_summary.json 缺失'}

if smoke_gate.get('status') != 'passed' and not os.path.isfile(os.path.join(archive_dir, 'formal_runtime_loop', 'server', 'issue41-server-result.json')):
    formal['status'] = 'skipped'
    formal['reason'] = '前序阶段未通过，formal_runtime_loop 跳过'

lifecycle_path = os.path.join(archive_dir, 'lifecycle', 'lifecycle_summary.json')
if os.path.isfile(lifecycle_path):
    with open(lifecycle_path, 'r', encoding='utf-8') as lf:
        lifecycle_data = json.load(lf)
else:
    if formal.get('status') != 'passed' or smoke_gate.get('status') != 'passed' or orch_status != 'passed':
        lifecycle_data = {'status': 'skipped', 'reason': '前序阶段未通过，lifecycle 跳过'}
    else:
        lifecycle_data = {'status': 'failed', 'reason': 'lifecycle_summary.json 缺失'}

status = 'passed' if (orch_status == 'passed' and
                     smoke_gate.get('status') == 'passed' and
                     formal.get('status') == 'passed' and
                     lifecycle_data.get('status') == 'passed') else 'failed'
category = None
reason = formal.get('reason', '')
if status != 'passed':
    if orch_status != 'passed':
        reason = orch_reason or 'preflight 检查未通过'
        category = orch_category or 'environment'
    elif smoke_gate.get('status') != 'passed':
        reason = smoke_gate.get('reason', '数据面 Gate 前置条件未通过')
        category = smoke_gate.get('failure_category', 'link_capability')
    elif formal.get('status') != 'passed':
        reason = formal.get('reason', '正式一轮 Runtime 闭环未通过')
        category = formal.get('failure_category', 'implementation')
    elif lifecycle_data.get('status') != 'passed':
        reason = lifecycle_data.get('reason', '生命周期 stop/restart 审计未通过')
        category = lifecycle_data.get('failure_category', 'implementation')
    else:
        reason = '存在未通过的阶段'
        category = 'implementation'

conclusion = {'status': status, 'reason': reason}
if category:
    conclusion['category'] = category

envelope_path = os.path.join(archive_dir, 'envelope.json')
run_id = os.path.basename(archive_dir)
if os.path.isfile(envelope_path):
    with open(envelope_path, 'r', encoding='utf-8') as ef:
        run_id = json.load(ef).get('run_id', run_id)

formal_rt = {
    'status': formal['status'],
    'runtime_interfaces': ['publish_model', 'wait_for_model', 'submit_update', 'wait_for_updates'],
    'data_plane': '10.80.0.0/24',
    'server_wait_for_updates_returned_node_ids': formal['server_wait_for_updates_returned_node_ids'],
    'partial_result_returned': formal['partial_result_returned'],
    'scenario': formal['scenario'],
    'rounds': formal['rounds'],
    'server_result': os.path.join(archive_dir, 'formal_runtime_loop', 'server', 'issue41-server-result.json'),
    'server_journal': os.path.join(archive_dir, 'raw', 'server-journal.txt'),
    'route_evidence': json.loads('[%s]' % route_evidence),
    'config_equivalence': formal.get('config_equivalence', {'status': 'passed'}),
    'controlled_stop': formal.get('controlled_stop', {'status': 'passed'}),
}
for role in client_roles:
    formal_rt[f'{role}_result'] = os.path.join(archive_dir, 'formal_runtime_loop', role, f'issue41-{role}-result.json')
    formal_rt[f'{role}_journal'] = os.path.join(archive_dir, 'raw', f'{role}-journal.txt')

summary = {
    'schema_version': 1,
    'run_id': run_id,
    'orchestration': {'status': orch_status, 'radio_health_dir': os.path.join(archive_dir, 'orchestration', 'radio-health')},
    'pre_runtime_smoke': smoke_gate,
    'formal_runtime_loop': formal_rt,
    'lifecycle': lifecycle_data,
    'conclusion': conclusion,
}
with open(os.path.join(archive_dir, 'issue41_summary.json'), 'w', encoding='utf-8') as fh:
    json.dump(summary, fh, ensure_ascii=True, separators=(',', ':'))
PY
    status="$(python3 -c "import json, sys; print(json.load(open(sys.argv[1], encoding='utf-8'))['conclusion']['status'])" "$ARCHIVE_DIR/issue41_summary.json")"
    reason="$(python3 -c "import json, sys; print(json.load(open(sys.argv[1], encoding='utf-8'))['conclusion']['reason'])" "$ARCHIVE_DIR/issue41_summary.json")"
    category="$(python3 -c "import json, sys; print(json.load(open(sys.argv[1], encoding='utf-8'))['conclusion'].get('category', 'none'))" "$ARCHIVE_DIR/issue41_summary.json")"
    cat > "$ARCHIVE_DIR/result.md" <<EOF
# issue41 真实硬件 FL Runtime 闭环结果

- conclusion: $status
- reason: $reason
- failure_category: $category
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
    cmd_verify_config_equivalence
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
    verify-config-equivalence) cmd_verify_config_equivalence ;;
    run-runtime-loop) cmd_run_runtime_loop ;;
    lifecycle-stop-restart) cmd_lifecycle_stop_restart ;;
    collect) cmd_collect ;;
    summary) cmd_summary ;;
    run-all) cmd_run_all ;;
    stop-all) cmd_stop_all ;;
    clean) cmd_clean ;;
    *) usage; die "未知子命令：$cmd" ;;
esac
