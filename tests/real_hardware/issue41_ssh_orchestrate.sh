#!/bin/bash
# 本机 server 通过 SSH 编排两个独立远程 client 的 issue #41 验收阶段。
set -euo pipefail

ACTION="${1:-}"
ENV_FILE="${2:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

usage() {
    printf '%s\n' \
      '用法: issue41_ssh_orchestrate.sh ACTION ENV_FILE' \
      'ACTION: generate | deploy | preflight | start | collect | stop | restart | validate' \
      '当前机器固定为 server，CLIENT1_SSH/CLIENT2_SSH 必须是两台独立远程机器。'
}

[ -n "$ACTION" ] && [ -n "$ENV_FILE" ] || { usage >&2; exit 2; }
[ -r "$ENV_FILE" ] || { printf '参数文件不可读: %s\n' "$ENV_FILE" >&2; exit 2; }
# shellcheck disable=SC1090
source "$ENV_FILE"
: "${CLIENT1_SSH:?缺少 CLIENT1_SSH}"
: "${CLIENT2_SSH:?缺少 CLIENT2_SSH}"
: "${REMOTE_REPO:?缺少 REMOTE_REPO}"
: "${SERVER_IFACE:?缺少 SERVER_IFACE}"
: "${CLIENT1_IFACE:?缺少 CLIENT1_IFACE}"
: "${CLIENT2_IFACE:?缺少 CLIENT2_IFACE}"
: "${RUN_NAME:?缺少 RUN_NAME}"
: "${ARCHIVE_ROOT:?缺少 ARCHIVE_ROOT}"

if [ "$CLIENT1_SSH" = "$CLIENT2_SSH" ]; then
    printf '两个 SSH 目标不能相同\n' >&2
    exit 2
fi

CONFIG_ROOT="${CONFIG_ROOT:-/tmp/$RUN_NAME-config}"
read -r -a SSH_ARGS <<< "${SSH_OPTIONS:--o BatchMode=yes}"

[[ "$RUN_NAME" =~ ^[A-Za-z0-9._-]+$ ]] || {
    printf 'RUN_NAME 只能包含字母、数字、点、下划线和横线\n' >&2
    exit 2
}
[[ "$CLIENT1_SSH" =~ ^[A-Za-z0-9._@:-]+$ ]] &&
[[ "$CLIENT2_SSH" =~ ^[A-Za-z0-9._@:-]+$ ]] || {
    printf 'SSH target 包含不支持的字符\n' >&2
    exit 2
}
[[ "$REMOTE_REPO" =~ ^/[A-Za-z0-9._/-]+$ ]] || {
    printf 'REMOTE_REPO 必须是无空格和 shell 元字符的绝对路径\n' >&2
    exit 2
}
for path in "$CONFIG_ROOT" "$ARCHIVE_ROOT"; do
    [[ "$path" =~ ^/tmp/issue41[A-Za-z0-9._/-]*$ ]] && [ "$path" != /tmp/issue41 ] || {
        printf '可写目录必须位于 /tmp/ 且以 issue41 开头: %s\n' "$path" >&2
        exit 2
    }
done

remote() {
    local target="$1" command
    shift
    printf -v command '%q ' bash -s -- "$@"
    ssh "${SSH_ARGS[@]}" "$target" "$command"
}

copy_to() {
    local source="$1" target="$2" destination="$3"
    scp "${SSH_ARGS[@]}" -r "$source" "$target:$destination"
}

copy_from() {
    local target="$1" source="$2" destination="$3"
    mkdir -p "$destination"
    ssh "${SSH_ARGS[@]}" "$target" bash -s -- "$source" <<'REMOTE' | tar -C "$destination" -xf -
set -euo pipefail
cd "$1"
tar -cf - .
REMOTE
}

check_targets() {
    local local_host client1_host client2_host local_commit client1_commit client2_commit
    local_host="$(hostname)"
    client1_host="$(remote "$CLIENT1_SSH" <<'REMOTE'
hostname
REMOTE
)"
    client2_host="$(remote "$CLIENT2_SSH" <<'REMOTE'
hostname
REMOTE
)"
    local_commit="$(git -C "$PROJECT_ROOT" rev-parse HEAD)"
    client1_commit="$(remote "$CLIENT1_SSH" "$REMOTE_REPO" <<'REMOTE'
git -C "$1" rev-parse HEAD
REMOTE
)"
    client2_commit="$(remote "$CLIENT2_SSH" "$REMOTE_REPO" <<'REMOTE'
git -C "$1" rev-parse HEAD
REMOTE
)"
    [ "$client1_host" != "$client2_host" ] || {
        printf '两个 SSH 目标解析到同一 hostname: %s\n' "$client1_host" >&2
        return 1
    }
    [ "$local_host" != "$client1_host" ] && [ "$local_host" != "$client2_host" ] || {
        printf '远程目标不能解析到当前 server hostname: %s\n' "$local_host" >&2
        return 1
    }
    [ "$local_commit" = "$client1_commit" ] && [ "$local_commit" = "$client2_commit" ] || {
        printf '三机 commit 不一致: server=%s client1=%s client2=%s\n' \
          "$local_commit" "$client1_commit" "$client2_commit" >&2
        return 1
    }
    printf 'server=%s client1=%s client2=%s commit=%s\n' \
      "$local_host" "$client1_host" "$client2_host" "$local_commit"
}

generate() {
    check_targets
    rm -rf "$CONFIG_ROOT"
    python3 "$SCRIPT_DIR/issue41_generate_configs.py" \
      --output-dir "$CONFIG_ROOT" \
      --server-iface "$SERVER_IFACE" \
      --client1-iface "$CLIENT1_IFACE" \
      --client2-iface "$CLIENT2_IFACE"
}

deploy_local_server() {
    sudo install -m 0644 "$CONFIG_ROOT/server/fl-server.json" /etc/wfb-ng/fl-server.json
    sudo install -m 0644 "$CONFIG_ROOT/server/fl-server-algorithm.json" /etc/wfb-ng/fl-server-algorithm.json
    sudo install -m 0644 "$CONFIG_ROOT/server/wfb-fl-server" /etc/default/wfb-fl-server
}

deploy_remote_client() {
    local role="$1" target="$2"
    local remote_stage="/tmp/$RUN_NAME-$role-config"
    remote "$target" "$remote_stage" <<'REMOTE'
set -euo pipefail
rm -rf "$1"
REMOTE
    copy_to "$CONFIG_ROOT/$role" "$target" "$remote_stage"
    remote "$target" "$remote_stage" "$REMOTE_REPO" <<'REMOTE'
set -euo pipefail
stage="$1"
repo="$2"
cd "$repo"
make build_v6
sudo make install_v8
sudo install -m 0644 "$stage/fl-client.json" /etc/wfb-ng/fl-client.json
sudo install -m 0644 "$stage/fl-client-algorithm.json" /etc/wfb-ng/fl-client-algorithm.json
sudo install -m 0644 "$stage/wfb-fl-client" /etc/default/wfb-fl-client
sudo systemctl daemon-reload
REMOTE
}

deploy() {
    check_targets
    [ -d "$CONFIG_ROOT/server" ] || generate
    (cd "$PROJECT_ROOT" && make build_v6 && sudo make install_v8)
    deploy_local_server
    sudo systemctl daemon-reload
    deploy_remote_client client1 "$CLIENT1_SSH"
    deploy_remote_client client2 "$CLIENT2_SSH"
}

preflight() {
    check_targets
    bash "$SCRIPT_DIR/preflight_checklist.sh" \
      --role server --config /etc/wfb-ng/fl-server.json --phase clean
    remote "$CLIENT1_SSH" "$REMOTE_REPO" <<'REMOTE'
set -euo pipefail
cd "$1"
bash tests/real_hardware/preflight_checklist.sh --role client1 --config /etc/wfb-ng/fl-client.json --phase clean
REMOTE
    remote "$CLIENT2_SSH" "$REMOTE_REPO" <<'REMOTE'
set -euo pipefail
cd "$1"
bash tests/real_hardware/preflight_checklist.sh --role client2 --config /etc/wfb-ng/fl-client.json --phase clean
REMOTE
}

start_remote_client() {
    local role="$1" target="$2"
    local remote_output="/tmp/$RUN_NAME-evidence/$role"
    remote "$target" "$REMOTE_REPO" "$role" "$remote_output" <<'REMOTE'
set -euo pipefail
cd "$1"
sudo systemctl start wfb-fl-client.service
for _ in $(seq 1 100); do
    ip link show wfb-fl0 >/dev/null 2>&1 && break
    sleep 0.1
done
sudo ip route replace 230.4.4.1 dev wfb-fl0
bash tests/real_hardware/preflight_checklist.sh --role "$2" --config /etc/wfb-ng/fl-client.json --phase runtime
rm -rf "$3"
bash tests/real_hardware/issue41_collect.sh "$2" "$3" running
REMOTE
}

start() {
    check_targets
    sudo rm -f /run/wfb-ng/issue41-start.gate
    start_remote_client client1 "$CLIENT1_SSH"
    start_remote_client client2 "$CLIENT2_SSH"
    sudo systemctl start wfb-fl-server.service
    for _ in $(seq 1 100); do
        ip link show wfb-fl0 >/dev/null 2>&1 && break
        sleep 0.1
    done
    sudo ip route replace 230.4.4.1 dev wfb-fl0
    bash "$SCRIPT_DIR/preflight_checklist.sh" \
      --role server --config /etc/wfb-ng/fl-server.json --phase runtime
    rm -rf "$ARCHIVE_ROOT/server" "$ARCHIVE_ROOT/client1" "$ARCHIVE_ROOT/client2"
    mkdir -p "$ARCHIVE_ROOT/server"
    bash "$SCRIPT_DIR/issue41_collect.sh" \
      server "$ARCHIVE_ROOT/server" running
    copy_from "$CLIENT1_SSH" "/tmp/$RUN_NAME-evidence/client1" \
      "$ARCHIVE_ROOT/client1"
    copy_from "$CLIENT2_SSH" "/tmp/$RUN_NAME-evidence/client2" \
      "$ARCHIVE_ROOT/client2"
    sudo install -d -m 0755 /run/wfb-ng
    sudo touch /run/wfb-ng/issue41-start.gate
    printf '三端 ready，已打开 server 算法启动门。\n'
}

collect_remote() {
    local role="$1" target="$2"
    local remote_output="/tmp/$RUN_NAME-evidence/$role"
    remote "$target" "$REMOTE_REPO" "$role" "$remote_output" <<'REMOTE'
set -euo pipefail
cd "$1"
mkdir -p "$3"
bash tests/real_hardware/issue41_collect.sh "$2" "$3" final
REMOTE
    copy_from "$target" "$remote_output" "$ARCHIVE_ROOT/$role"
}

collect() {
    mkdir -p "$ARCHIVE_ROOT/server"
    bash "$SCRIPT_DIR/issue41_collect.sh" server "$ARCHIVE_ROOT/server" final
    collect_remote client1 "$CLIENT1_SSH"
    collect_remote client2 "$CLIENT2_SSH"
    python3 "$SCRIPT_DIR/v6_formal_2a_summary.py" \
      --mode downlink --scenario shared \
      --server-log "$ARCHIVE_ROOT/server/journal.log" \
      --queue-summary "$ARCHIVE_ROOT/server/queue-summary.json" \
      --queue-summary "$ARCHIVE_ROOT/client1/queue-summary.json" \
      --queue-summary "$ARCHIVE_ROOT/client2/queue-summary.json" \
      --reassembly-log "$ARCHIVE_ROOT/server/journal.log" \
      --reassembly-log "$ARCHIVE_ROOT/client1/journal.log" \
      --reassembly-log "$ARCHIVE_ROOT/client2/journal.log" \
      --output "$ARCHIVE_ROOT/server/downlink-formal-2a-summary.json"
    (cd "$ARCHIVE_ROOT/server" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)
}

stop_remote() {
    local role="$1" target="$2"
    remote "$target" "$REMOTE_REPO" "$role" "/tmp/$RUN_NAME-evidence/$role" <<'REMOTE'
set -euo pipefail
cd "$1"
sudo systemctl stop wfb-fl-client.service
bash tests/real_hardware/issue41_collect_stopped.sh "$2" "$3"
REMOTE
    copy_from "$target" "/tmp/$RUN_NAME-evidence/$role" "$ARCHIVE_ROOT/$role"
}

stop_all() {
    sudo systemctl stop wfb-fl-server.service
    bash "$SCRIPT_DIR/issue41_collect_stopped.sh" server "$ARCHIVE_ROOT/server"
    stop_remote client1 "$CLIENT1_SSH"
    stop_remote client2 "$CLIENT2_SSH"
}

restart_remote_phase() {
    local phase="$1" role="$2" target="$3"
    remote "$target" "$REMOTE_REPO" "$role" "/tmp/$RUN_NAME-evidence/$role" "$phase" <<'REMOTE'
set -euo pipefail
cd "$1"
sudo bash tests/real_hardware/issue41_restart_lifecycle.sh "$2" "$3" "$4"
REMOTE
}

restart_all() {
    sudo rm -f /run/wfb-ng/issue41-start.gate
    restart_remote_phase start client1 "$CLIENT1_SSH"
    restart_remote_phase start client2 "$CLIENT2_SSH"
    sudo bash "$SCRIPT_DIR/issue41_restart_lifecycle.sh" \
      server "$ARCHIVE_ROOT/server" start
    sudo bash "$SCRIPT_DIR/issue41_restart_lifecycle.sh" \
      server "$ARCHIVE_ROOT/server" stop
    restart_remote_phase stop client1 "$CLIENT1_SSH"
    restart_remote_phase stop client2 "$CLIENT2_SSH"
    copy_from "$CLIENT1_SSH" "/tmp/$RUN_NAME-evidence/client1" \
      "$ARCHIVE_ROOT/client1"
    copy_from "$CLIENT2_SSH" "/tmp/$RUN_NAME-evidence/client2" \
      "$ARCHIVE_ROOT/client2"
}

validate() {
    python3 "$SCRIPT_DIR/issue41_validate_archive.py" "$ARCHIVE_ROOT"
}

case "$ACTION" in
    generate) generate ;;
    deploy) deploy ;;
    preflight) preflight ;;
    start) start ;;
    collect) collect ;;
    stop) stop_all ;;
    restart) restart_all ;;
    validate) validate ;;
    *) usage >&2; exit 2 ;;
esac
