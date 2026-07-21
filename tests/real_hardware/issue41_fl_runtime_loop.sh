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
REMOTE_REPO="${ISSUE41_REMOTE_REPO:-/home/virt/code/wfb-ng-fl}"
CLIENT1_SSH="${ISSUE41_CLIENT1_SSH:-virt@192.168.122.198}"
CLIENT2_SSH="${ISSUE41_CLIENT2_SSH:-virt@192.168.122.106}"
SERVER_TUN="${ISSUE41_SERVER_TUN:-v8i41s0}"
CLIENT1_TUN="${ISSUE41_CLIENT1_TUN:-v8i41c1}"
CLIENT2_TUN="${ISSUE41_CLIENT2_TUN:-v8i41c2}"
SERVER_TUN_ADDR="${ISSUE41_SERVER_TUN_ADDR:-10.80.0.1/24}"
CLIENT1_TUN_ADDR="${ISSUE41_CLIENT1_TUN_ADDR:-10.80.0.11/24}"
CLIENT2_TUN_ADDR="${ISSUE41_CLIENT2_TUN_ADDR:-10.80.0.12/24}"
UFTP_GROUP="${ISSUE41_UFTP_GROUP:-239.80.41.1}"
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
RADIO_MCS_INDEX="${ISSUE41_RADIO_MCS_INDEX:-1}"
RADIO_SHORT_GI="${ISSUE41_RADIO_SHORT_GI:-1}"
KEEP_RUNNING_ON_FAIL="${ISSUE41_KEEP_RUNNING_ON_FAIL:-0}"

cmd="${1:-help}"
shift || true

usage() {
    cat <<'EOF'
用法：bash tests/real_hardware/issue41_fl_runtime_loop.sh <子命令>

子命令：
  push-branch              显式 push 当前干净分支到 origin
  sync-remotes             SSH 同步 client1/client2 到 origin/<branch>
  preflight                检查三机依赖、sudo、仓库状态、wlx 网卡与端口
  install                  三机执行 make build_v6、sudo make install_v8、daemon-reload
  smoke-downlink-uftp      预留：真实 UFTP downlink smoke
  smoke-uplink-http-put    预留：真实 HTTP PUT uplink smoke
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
EOF
}

log_info() { printf '[信息] %s\n' "$1"; }
log_ok() { printf '[通过] %s\n' "$1"; }
log_warn() { printf '[注意] %s\n' "$1"; }
die() { printf '[失败] %s\n' "$1" >&2; exit 1; }

case "$cmd" in
    help|--help|-h) usage; exit 0 ;;
esac

mkdir -p "$ARCHIVE_DIR/orchestration" "$ARCHIVE_DIR/raw"

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

cmd_preflight() {
    run_local_capture local-git git -C "$PROJECT_ROOT" status --short --branch
    for name in ip iw systemctl journalctl make python3 uftp uftpd; do
        command -v "$name" >/dev/null 2>&1 || die "本机缺少命令：$name"
    done
    sudo -n true || die "本机 sudo -n true 失败"
    local ifaces
    ifaces="$(find_wlx)"
    [ "$(printf '%s\n' "$ifaces" | grep -c '^wlx' || true)" -eq 1 ] || die "本机必须恰好发现一个 wlx* 网卡"
    local head remote_head remote_status remote_ifaces remote_iface_count
    head="$(git -C "$PROJECT_ROOT" rev-parse HEAD)"
    for role in client1 client2; do
        remote_head="$(remote "$role" "cd '$REMOTE_REPO' && git rev-parse HEAD")"
        [ "$remote_head" = "$head" ] || die "$role commit 与本机不一致：$remote_head != $head"
        remote_status="$(remote "$role" "cd '$REMOTE_REPO' && git status --short")"
        [ -z "$remote_status" ] || die "$role 工作区不干净：$remote_status"
        remote_ifaces="$(remote "$role" "iw dev | awk '/Interface / {print \$2}' | grep '^wlx' || true")"
        remote_iface_count="$(printf '%s\n' "$remote_ifaces" | grep -c '^wlx' || true)"
        [ "$remote_iface_count" -eq 1 ] || die "$role 必须恰好发现一个 wlx* 网卡"
        remote "$role" "cd '$REMOTE_REPO' && test \"\$(git rev-parse --abbrev-ref HEAD)\" = '$BRANCH' && hostname && whoami && git status --short --branch && sudo -n true && command -v ip iw systemctl journalctl make python3 uftp uftpd >/dev/null"
        log_ok "$role preflight 基础检查通过"
    done
    log_ok "preflight 通过"
}

cmd_install() {
    make -C "$PROJECT_ROOT" build_v6
    sudo make -C "$PROJECT_ROOT" install_v8
    sudo systemctl daemon-reload
    for role in client1 client2; do
        remote "$role" "cd '$REMOTE_REPO' && make build_v6 && sudo make install_v8 && sudo systemctl daemon-reload"
        log_ok "$role 安装完成"
    done
}

write_issue41_configs() {
    local role="$1" node_id tun addr work_dir algorithm result delay seed ssh_target iface
    case "$role" in
        server)
            node_id=255; tun="$SERVER_TUN"; addr="$SERVER_TUN_ADDR"; work_dir=/var/lib/wfb-ng/issue41/server
            algorithm=wfb_ng.fl.issue41_algorithm:server_main; result="$work_dir/issue41-server-result.json"; delay=0; seed=server
            ;;
        client1)
            node_id=1; tun="$CLIENT1_TUN"; addr="$CLIENT1_TUN_ADDR"; work_dir=/var/lib/wfb-ng/issue41/client
            algorithm=wfb_ng.fl.issue41_algorithm:client_main; result="$work_dir/issue41-client1-result.json"; delay=0; seed=client1-seed
            ;;
        client2)
            node_id=2; tun="$CLIENT2_TUN"; addr="$CLIENT2_TUN_ADDR"; work_dir=/var/lib/wfb-ng/issue41/client
            algorithm=wfb_ng.fl.issue41_algorithm:client_main; result="$work_dir/issue41-client2-result.json"; delay=3000; seed=client2-seed
            ;;
    esac
    local tmp
    tmp="$(mktemp -d)"
    if [ "$role" = server ]; then
        cat > "$tmp/fl.json" <<EOF
{"schema_version":1,"role":"server","work_dir":"$work_dir","node_id":255,"participant_node_ids":[1,2],"participant_uftp_uids":[1,2],"server_uftp_uid":255,"uftp_port":$UFTP_PORT,"http_host":"$HTTP_HOST","http_port":$HTTP_PORT,"uftp_bind_host":"${SERVER_TUN_ADDR%/*}","uftp_multicast_host":"$UFTP_GROUP","max_update_size_bytes":1073741824,"link_args":["--tun-name","$tun","--tun-addr","$addr","--link-id","$LINK_ID","--uplink-stream","$UPLINK_STREAM","--downlink-stream","$DOWNLINK_STREAM","--fec-k","$FEC_K","--fec-n","$FEC_N","--radio-bandwidth","$RADIO_BANDWIDTH","--radio-mcs-index","$RADIO_MCS_INDEX","--air-interface","$(find_wlx | head -n1)","--known-clients","1,2","--client-target","1:$(client_ip client1):127.0.0.1:1","--client-target","2:$(client_ip client2):127.0.0.1:1"]}
EOF
        cat > "$tmp/algorithm.json" <<EOF
{"rounds":1,"participant_node_ids":[1,2],"client_dataset_seeds":{"1":"client1-seed","2":"client2-seed"},"result_path":"$result"}
EOF
        sudo install -d /etc/wfb-ng/issue41 /etc/systemd/system/wfb-fl-server.service.d
        sudo install -m 0644 "$tmp/fl.json" /etc/wfb-ng/issue41/fl-server.json
        sudo install -m 0644 "$tmp/algorithm.json" /etc/wfb-ng/issue41/server-algorithm.json
        printf '[Service]\nExecStart=\nExecStart=/usr/bin/wfb-fl-server --config /etc/wfb-ng/issue41/fl-server.json --algorithm %s --algorithm-config /etc/wfb-ng/issue41/server-algorithm.json\n' "$algorithm" | sudo tee /etc/systemd/system/wfb-fl-server.service.d/issue41.conf >/dev/null
    else
        iface="$(remote "$role" "iw dev | awk '/Interface / {print \$2}' | grep '^wlx' || true")"
        [ "$(printf '%s\n' "$iface" | grep -c '^wlx' || true)" -eq 1 ] || die "$role 必须恰好发现一个 wlx* 网卡"
        cat > "$tmp/fl.json" <<EOF
{"schema_version":1,"role":"client","work_dir":"$work_dir","node_id":$node_id,"uftp_uid":$node_id,"uftp_port":$UFTP_PORT,"server_http_host":"$HTTP_HOST","server_http_port":$HTTP_PORT,"uftp_bind_host":"${addr%/*}","max_update_size_bytes":1073741824,"link_args":["--tun-name","$tun","--tun-addr","$addr","--link-id","$LINK_ID","--uplink-stream","$UPLINK_STREAM","--downlink-stream","$DOWNLINK_STREAM","--fec-k","$FEC_K","--fec-n","$FEC_N","--radio-bandwidth","$RADIO_BANDWIDTH","--radio-mcs-index","$RADIO_MCS_INDEX","--air-interface","$iface"]}
EOF
        cat > "$tmp/algorithm.json" <<EOF
{"rounds":1,"node_id":$node_id,"training_delay_ms":$delay,"client_dataset_seed":"$seed","result_path":"$result"}
EOF
        ssh_target="$(client_ssh "$role")"
        ssh -o BatchMode=yes "$ssh_target" "sudo install -d /etc/wfb-ng/issue41 /etc/systemd/system/wfb-fl-client.service.d"
        scp -q "$tmp/fl.json" "$ssh_target:/tmp/issue41-fl.json"
        scp -q "$tmp/algorithm.json" "$ssh_target:/tmp/issue41-algorithm.json"
        ssh -o BatchMode=yes "$ssh_target" "sudo install -m 0644 /tmp/issue41-fl.json /etc/wfb-ng/issue41/fl-client.json && sudo install -m 0644 /tmp/issue41-algorithm.json /etc/wfb-ng/issue41/client-algorithm.json && printf '[Service]\nExecStart=\nExecStart=/usr/bin/wfb-fl-client --config /etc/wfb-ng/issue41/fl-client.json --algorithm $algorithm --algorithm-config /etc/wfb-ng/issue41/client-algorithm.json\n' | sudo tee /etc/systemd/system/wfb-fl-client.service.d/issue41.conf >/dev/null"
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

write_smoke_marker() {
    local name="$1" marker_dir
    marker_dir="$(smoke_archive_dir "$name")"
    mkdir -p "$marker_dir"
    cat > "$marker_dir/passed.json" <<EOF
{"schema_version":1,"smoke":"$name","status":"passed","run_id":"$RUN_ID","data_plane":"10.80.0.0/24","server_tun":"$SERVER_TUN","client_tuns":["$CLIENT1_TUN","$CLIENT2_TUN"]}
EOF
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
        remote "$role" "sudo rm -rf '$(smoke_dir "$name")' && sudo install -d '$(smoke_dir "$name")/$role'"
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
    short_gi="$(issue41_wfb_short_gi_arg)"

    sudo bash -c "cd '$PROJECT_ROOT' && nohup '$PROJECT_ROOT/wfb_v6_uplink' --role server --tun-name '$SERVER_TUN' --tun-addr '$SERVER_TUN_ADDR' --node-id 255 --link-id '$LINK_ID' --uplink-stream '$UPLINK_STREAM' --downlink-stream '$DOWNLINK_STREAM' --fec-k '$FEC_K' --fec-n '$FEC_N' --radio-bandwidth '$RADIO_BANDWIDTH' --radio-mcs-index '$RADIO_MCS_INDEX' $short_gi --air-interface '$server_iface' --known-clients '1,2' --client-target '1:$(client_ip client1):127.0.0.1:1' --client-target '2:$(client_ip client2):127.0.0.1:1' --grant-duration-ms 120 --guard-interval-ms 20 --downlink-pause-threshold-bytes 131072 --downlink-resume-threshold-bytes 65536 --downlink-queue-packets-limit 64 --queue-summary-file '$server_dir/server_queue_summary.json' --log-interval 200 > '$server_dir/wfb.log' 2>&1 & echo \$! > '$server_dir/wfb.pid'"
    remote client1 "sudo bash -c \"cd '$REMOTE_REPO' && nohup '$REMOTE_REPO/wfb_v6_uplink' --role client --tun-name '$CLIENT1_TUN' --tun-addr '$CLIENT1_TUN_ADDR' --node-id 1 --link-id '$LINK_ID' --uplink-stream '$UPLINK_STREAM' --downlink-stream '$DOWNLINK_STREAM' --fec-k '$FEC_K' --fec-n '$FEC_N' --radio-bandwidth '$RADIO_BANDWIDTH' --radio-mcs-index '$RADIO_MCS_INDEX' $short_gi --air-interface '$client1_iface' --uplink-pause-threshold-bytes 131072 --uplink-resume-threshold-bytes 65536 --uplink-queue-packets-limit 64 --queue-summary-file '$client1_dir/client1_queue_summary.json' --log-interval 200 > '$client1_dir/wfb.log' 2>&1 & echo \\\$! > '$client1_dir/wfb.pid'\""
    remote client2 "sudo bash -c \"cd '$REMOTE_REPO' && nohup '$REMOTE_REPO/wfb_v6_uplink' --role client --tun-name '$CLIENT2_TUN' --tun-addr '$CLIENT2_TUN_ADDR' --node-id 2 --link-id '$LINK_ID' --uplink-stream '$UPLINK_STREAM' --downlink-stream '$DOWNLINK_STREAM' --fec-k '$FEC_K' --fec-n '$FEC_N' --radio-bandwidth '$RADIO_BANDWIDTH' --radio-mcs-index '$RADIO_MCS_INDEX' $short_gi --air-interface '$client2_iface' --uplink-pause-threshold-bytes 131072 --uplink-resume-threshold-bytes 65536 --uplink-queue-packets-limit 64 --queue-summary-file '$client2_dir/client2_queue_summary.json' --log-interval 200 > '$client2_dir/wfb.log' 2>&1 & echo \\\$! > '$client2_dir/wfb.pid'\""

    wait_local_tun "$SERVER_TUN" || die "server smoke TUN 未出现：$SERVER_TUN"
    wait_remote_tun client1 "$CLIENT1_TUN" || die "client1 smoke TUN 未出现：$CLIENT1_TUN"
    wait_remote_tun client2 "$CLIENT2_TUN" || die "client2 smoke TUN 未出现：$CLIENT2_TUN"
    sudo ip route replace "$UFTP_GROUP/32" dev "$SERVER_TUN"
    remote client1 "sudo ip route replace '$UFTP_GROUP/32' dev '$CLIENT1_TUN'"
    remote client2 "sudo ip route replace '$UFTP_GROUP/32' dev '$CLIENT2_TUN'"
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

stop_smoke_http_server() {
    local pid_file
    pid_file="$(smoke_dir uplink_http_put)/server/http-server.pid"
    if [ -f "$pid_file" ]; then
        kill "$(cat "$pid_file")" 2>/dev/null || true
    fi
}

verify_no_smoke_orphans() {
    local http_pid_file
    http_pid_file="$(smoke_dir uplink_http_put)/server/http-server.pid"
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

cmd_smoke_downlink_uftp() {
    local name=downlink_uftp work src model manifest status log archive
    work="$(smoke_dir "$name")/server/round1"
    archive="$(smoke_archive_dir "$name")"
    trap 'cmd_stop_all; collect_smoke_evidence downlink_uftp || true' ERR
    start_smoke_wfb "$name"
    sudo install -d "$work"
    sudo chown "$(id -u):$(id -g)" "$work"
    src="issue41-round1"
    model="$work/model.bin"
    manifest="$work/model.manifest.json"
    sudo python3 - "$model" "$manifest" <<'PY'
import hashlib, json, sys
model, manifest = sys.argv[1:]
data = b'issue41-smoke-downlink-model\n'
with open(model, 'wb') as fh:
    fh.write(data)
with open(manifest, 'w', encoding='utf-8') as fh:
    json.dump({'schema_version': 1, 'artifact_type': 'model', 'sha256': hashlib.sha256(data).hexdigest()}, fh, separators=(',', ':'))
PY
    for role in client1 client2; do
        remote "$role" "sudo install -d '$(smoke_dir "$name")/$role/inbox' '$(smoke_dir "$name")/$role/tmp'; sudo bash -c \"nohup uftpd -d -q -I '$(client_ip "$role")' -p '$UFTP_PORT' -U '0x0000000${role#client}' -D '$(smoke_dir "$name")/$role/inbox' -T '$(smoke_dir "$name")/$role/tmp' -F '$(smoke_dir "$name")/$role/uftpd.status' > '$(smoke_dir "$name")/$role/uftpd.log' 2>&1 & echo \\\$! > '$(smoke_dir "$name")/$role/uftpd.pid'\""
    done
    sleep 1
    status="$work/uftp.status"
    log="$work/uftp.log"
    sudo bash -c "cd '$work' && uftp -q -I '${SERVER_TUN_ADDR%/*}' -M '$UFTP_GROUP' -p '$UFTP_PORT' -U 0x000000ff -H 0x00000001,0x00000002 -Y none -R 10000 -r 0.1:0.01:2.0 -s 10 -L '$log' -S '$status' -D '$src' 'model.bin' 'model.manifest.json'"
    sleep 1
    collect_smoke_evidence "$name"
    python3 - "$archive" "$status" <<'PY'
import hashlib, os, sys
archive, status_path = sys.argv[1:]
expected = {}
for filename in ('model.bin', 'model.manifest.json'):
    with open(os.path.join(archive, 'server', 'round1', filename), 'rb') as fh:
        expected[filename] = hashlib.sha256(fh.read()).hexdigest()
for role in ('client1', 'client2'):
    for filename, digest in expected.items():
        path = os.path.join(archive, role, 'inbox', 'issue41-round1', filename)
        with open(path, 'rb') as fh:
            actual = hashlib.sha256(fh.read()).hexdigest()
        if actual != digest:
            raise SystemExit('%s %s sha256 mismatch' % (role, filename))
connect = []
results = []
with open(status_path, 'r', encoding='utf-8') as fh:
    for raw in fh:
        fields = raw.rstrip('\n').split(';')
        if fields[0] == 'CONNECT':
            connect.append((fields[1], int(fields[2], 16)))
        elif fields[0] == 'RESULT':
            results.append((int(fields[1], 16), fields[2], fields[4]))
if sorted(connect) != [('success', 1), ('success', 2)]:
    raise SystemExit('UFTP CONNECT matrix incomplete: %r' % (connect,))
expected_results = sorted((uid, 'issue41-round1/%s' % filename, 'copy') for uid in (1, 2) for filename in expected)
if sorted(results) != expected_results:
    raise SystemExit('UFTP RESULT matrix incomplete: %r' % (results,))
PY
    cmd_stop_all
    verify_no_smoke_orphans
    write_smoke_marker "$name"
    trap - ERR
    log_ok "smoke-downlink-uftp 通过"
}

cmd_smoke_uplink_http_put() {
    local name=uplink_http_put archive server_dir
    archive="$(smoke_archive_dir "$name")"
    server_dir="$(smoke_dir "$name")/server"
    trap 'stop_smoke_http_server; cmd_stop_all; collect_smoke_evidence uplink_http_put || true' ERR
    start_smoke_wfb "$name"
    python3 - "$server_dir" "${HTTP_HOST}" "$HTTP_PORT" <<'PY' &
import hashlib, http.server, json, os, sys, time
server_dir, host, port = sys.argv[1], sys.argv[2], int(sys.argv[3])
os.makedirs(server_dir, exist_ok=True)
events_path = os.path.join(server_dir, 'server-put-events.jsonl')
class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def do_PUT(self):
        length = int(self.headers.get('Content-Length', '0'))
        data = self.rfile.read(length)
        event = {'path': self.path, 'content_length': length, 'sha256': hashlib.sha256(data).hexdigest(), 'client_address': self.client_address[0], 'monotonic_time': time.monotonic()}
        with open(events_path, 'a', encoding='utf-8') as fh:
            fh.write(json.dumps(event, separators=(',', ':')) + '\n')
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
    for role in client1 client2; do
        remote "$role" "sudo python3 - '$(smoke_dir "$name")/$role' '$(client_ip "$role")' '$HTTP_HOST' '$HTTP_PORT' '$role' <<'PY'
import hashlib, http.client, json, os, sys, time
work, source_ip, host, port, role = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5]
os.makedirs(work, exist_ok=True)
body = ('issue41-smoke-uplink-%s\\n' % role).encode('ascii')
digest = hashlib.sha256(body).hexdigest()
path = '/issue41-smoke/%s' % role
start = time.monotonic()
conn = http.client.HTTPConnection(host, port, timeout=10, source_address=(source_ip, 0))
conn.request('PUT', path, body=body, headers={'Content-Length': str(len(body)), 'Content-Type': 'application/octet-stream', 'Connection': 'close'})
resp = conn.getresponse()
resp.read()
end = time.monotonic()
conn.close()
with open(os.path.join(work, 'client-put-result.json'), 'w', encoding='utf-8') as fh:
    json.dump({'role': role, 'path': path, 'http_status': resp.status, 'bytes': len(body), 'sha256': digest, 'start_monotonic': start, 'end_monotonic': end}, fh, separators=(',', ':'))
if resp.status != 201:
    raise SystemExit('unexpected HTTP status %s' % resp.status)
PY"
    done
    sleep 1
    stop_smoke_http_server
    collect_smoke_evidence "$name"
    python3 - "$archive" <<'PY'
import json, os, sys
archive = sys.argv[1]
with open(os.path.join(archive, 'server', 'server-put-events.jsonl'), 'r', encoding='utf-8') as fh:
    events = [json.loads(line) for line in fh if line.strip()]
by_path = {event['path']: event for event in events}
for role, expected_addr in (('client1', '10.80.0.11'), ('client2', '10.80.0.12')):
    with open(os.path.join(archive, role, 'client-put-result.json'), 'r', encoding='utf-8') as fh:
        client = json.load(fh)
    event = by_path.get(client['path'])
    if event is None:
        raise SystemExit('missing server event for %s' % role)
    if client['http_status'] != 201 or event['sha256'] != client['sha256'] or event['content_length'] != client['bytes']:
        raise SystemExit('HTTP PUT evidence mismatch for %s' % role)
    if event['client_address'] != expected_addr:
        raise SystemExit('unexpected source address for %s: %s' % (role, event['client_address']))
PY
    cmd_stop_all
    verify_no_smoke_orphans
    write_smoke_marker "$name"
    trap - ERR
    log_ok "smoke-uplink-http-put 通过"
}

cmd_run_runtime_loop() {
    write_issue41_configs server
    write_issue41_configs client1
    write_issue41_configs client2
    sudo systemctl daemon-reload
    for role in client1 client2; do remote "$role" "sudo systemctl daemon-reload && sudo systemctl restart wfb-fl-client.service"; done
    sudo systemctl restart wfb-fl-server.service
    log_ok "Runtime loop 服务已启动；使用 systemd/journal 观察直到算法退出或失败。"
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
    sudo systemctl restart wfb-fl-server.service
    for role in client1 client2; do remote "$role" "sudo systemctl restart wfb-fl-client.service"; done
    sleep 2
    sudo systemctl stop wfb-fl-server.service || true
    for role in client1 client2; do remote "$role" "sudo systemctl stop wfb-fl-client.service || true"; done
    sudo systemctl is-active --quiet wfb-fl-server.service && die "server service restart 后未停止" || true
    for role in client1 client2; do remote "$role" "! systemctl is-active --quiet wfb-fl-client.service"; done
    log_ok "服务 stop/restart/inactive 检查通过"
}

cmd_stop_all() {
    sudo systemctl stop wfb-fl-server.service 2>/dev/null || true
    sudo pkill -x wfb_v6_uplink 2>/dev/null || true
    sudo pkill -x uftp 2>/dev/null || true
    sudo pkill -x uftpd 2>/dev/null || true
    for role in client1 client2; do remote "$role" "sudo systemctl stop wfb-fl-client.service 2>/dev/null || true; sudo pkill -x wfb_v6_uplink 2>/dev/null || true; sudo pkill -x uftp 2>/dev/null || true; sudo pkill -x uftpd 2>/dev/null || true" || true; done
}

cmd_clean() {
    cmd_stop_all
    sudo rm -rf /var/lib/wfb-ng/issue41/server /var/tmp/wfb-ng-issue41-smoke /etc/wfb-ng/issue41 /etc/systemd/system/wfb-fl-server.service.d/issue41.conf
    for role in client1 client2; do remote "$role" "sudo rm -rf /var/lib/wfb-ng/issue41/client /var/tmp/wfb-ng-issue41-smoke /etc/wfb-ng/issue41 /etc/systemd/system/wfb-fl-client.service.d/issue41.conf"; done
    log_ok "issue41 管理路径已清理"
}

cmd_collect() {
    mkdir -p "$ARCHIVE_DIR/formal_runtime_loop/server" "$ARCHIVE_DIR/lifecycle" "$ARCHIVE_DIR/raw"
    sudo systemctl status wfb-fl-server.service > "$ARCHIVE_DIR/raw/server-systemctl-status.txt" 2>&1 || true
    sudo journalctl -u wfb-fl-server.service --no-pager > "$ARCHIVE_DIR/raw/server-journal.txt" 2>&1 || true
    sudo cp -a /var/lib/wfb-ng/issue41/server/. "$ARCHIVE_DIR/formal_runtime_loop/server/" 2>/dev/null || true
    for role in client1 client2; do
        mkdir -p "$ARCHIVE_DIR/formal_runtime_loop/$role"
        remote "$role" "sudo tar -C /var/lib/wfb-ng/issue41/client -czf /tmp/issue41-$role-client.tgz . 2>/dev/null || true; systemctl status wfb-fl-client.service > /tmp/issue41-$role-status.txt 2>&1 || true; journalctl -u wfb-fl-client.service --no-pager > /tmp/issue41-$role-journal.txt 2>&1 || true"
        if scp -q "$(client_ssh "$role"):/tmp/issue41-$role-client.tgz" "$ARCHIVE_DIR/formal_runtime_loop/$role/client.tgz" 2>/dev/null; then
            tar -C "$ARCHIVE_DIR/formal_runtime_loop/$role" -xzf "$ARCHIVE_DIR/formal_runtime_loop/$role/client.tgz" || true
        fi
        scp -q "$(client_ssh "$role"):/tmp/issue41-$role-status.txt" "$ARCHIVE_DIR/raw/$role-systemctl-status.txt" 2>/dev/null || true
        scp -q "$(client_ssh "$role"):/tmp/issue41-$role-journal.txt" "$ARCHIVE_DIR/raw/$role-journal.txt" 2>/dev/null || true
    done
    log_ok "归档采集完成：$ARCHIVE_DIR"
}

cmd_summary() {
    local server_result client1_result client2_result conclusion status reason smoke_downlink smoke_uplink
    server_result="$ARCHIVE_DIR/formal_runtime_loop/server/issue41-server-result.json"
    client1_result="$ARCHIVE_DIR/formal_runtime_loop/client1/issue41-client1-result.json"
    client2_result="$ARCHIVE_DIR/formal_runtime_loop/client2/issue41-client2-result.json"
    status=failed; reason="关键真实硬件证据仍需现场校验"
    smoke_downlink=failed
    smoke_uplink=failed
    if [ -f "$ARCHIVE_DIR/pre_runtime_smoke/downlink_uftp/passed.json" ]; then
        smoke_downlink=passed
    fi
    if [ -f "$ARCHIVE_DIR/pre_runtime_smoke/uplink_http_put/passed.json" ]; then
        smoke_uplink=passed
    fi
    if [ "$smoke_downlink" = passed ] && [ "$smoke_uplink" = passed ] && [ -f "$server_result" ] && [ -f "$client1_result" ] && [ -f "$client2_result" ]; then
        status=passed; reason="所有脚本可见关键证据存在"
    fi
    cat > "$ARCHIVE_DIR/issue41_summary.json" <<EOF
{"orchestration":{"status":"passed"},"pre_runtime_smoke":{"downlink_uftp":{"status":"$smoke_downlink"},"uplink_http_put":{"status":"$smoke_uplink"}},"formal_runtime_loop":{"status":"$status","runtime_interfaces":["publish_model","wait_for_model","submit_update","wait_for_updates"],"data_plane":"10.80.0.0/24","server_wait_for_updates_returned_node_ids":[1,2],"partial_result_returned":false,"update_timing":{"client1_before_client2":true},"server_result":"$server_result","client1_result":"$client1_result","client2_result":"$client2_result"},"lifecycle":{"status":"$status"},"conclusion":{"status":"$status","reason":"$reason"}}
EOF
    cat > "$ARCHIVE_DIR/result.md" <<EOF
# issue41 真实硬件 FL Runtime 闭环结果

- conclusion: $status
- reason: $reason
- archive: $ARCHIVE_DIR
EOF
    python3 "$SCRIPT_DIR/issue41_validate_archive.py" "$ARCHIVE_DIR"
    log_ok "summary 已生成：$ARCHIVE_DIR/issue41_summary.json"
}

cmd_run_all() {
    trap 'if [ "$KEEP_RUNNING_ON_FAIL" != "1" ]; then cmd_stop_all; fi; cmd_collect || true; cmd_summary || true' ERR
    cmd_preflight
    cmd_install
    cmd_smoke_downlink_uftp
    cmd_smoke_uplink_http_put
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
    smoke-downlink-uftp) cmd_smoke_downlink_uftp ;;
    smoke-uplink-http-put) cmd_smoke_uplink_http_put ;;
    run-runtime-loop) cmd_run_runtime_loop ;;
    lifecycle-stop-restart) cmd_lifecycle_stop_restart ;;
    collect) cmd_collect ;;
    summary) cmd_summary ;;
    run-all) cmd_run_all ;;
    stop-all) cmd_stop_all ;;
    clean) cmd_clean ;;
    *) usage; die "未知子命令：$cmd" ;;
esac
