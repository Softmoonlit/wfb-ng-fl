#!/bin/bash
# Capture one host's issue #41 evidence. This script does not control other hosts.
set -euo pipefail

ROLE="${1:-}"
OUTPUT="${2:-}"
MODE="${3:-final}"
case "$ROLE" in server|client1|client2) ;; *) printf '用法: %s server|client1|client2 OUTPUT_DIR [running|final]\n' "$0" >&2; exit 2 ;; esac
case "$MODE" in running|final) ;; *) printf 'MODE 必须是 running 或 final\n' >&2; exit 2 ;; esac
[ -n "$OUTPUT" ] || { printf '缺少 OUTPUT_DIR\n' >&2; exit 2; }
CONFIG=/etc/wfb-ng/fl-client.json
UNIT=wfb-fl-client.service
WORK_DIR=/var/lib/wfb-ng/fl-client
if [ "$ROLE" = server ]; then CONFIG=/etc/wfb-ng/fl-server.json; UNIT=wfb-fl-server.service; WORK_DIR=/var/lib/wfb-ng/fl-server; fi
mkdir -p "$OUTPUT"

capture() { local name="$1"; shift; "$@" > "$OUTPUT/$name" 2>&1 || return $?; }
cp "$CONFIG" "$OUTPUT/role-config.json"
if [ "$ROLE" = server ]; then
    cp /etc/wfb-ng/fl-server-algorithm.json "$OUTPUT/algorithm-config.json"
    cp /etc/default/wfb-fl-server "$OUTPUT/algorithm-environment.txt"
else
    cp /etc/wfb-ng/fl-client-algorithm.json "$OUTPUT/algorithm-config.json"
    cp /etc/default/wfb-fl-client "$OUTPUT/algorithm-environment.txt"
fi
capture hostname.txt hostname
capture installed-sha256.txt sha256sum /usr/bin/wfb_v6_uplink /usr/bin/wfb-fl-server /usr/bin/wfb-fl-client
capture version.txt bash -c 'printf "commit="; git -C "$1" rev-parse HEAD; printf "describe="; git -C "$1" describe --always --dirty' _ "$(cd "$(dirname "$0")/../.." && pwd)"
if [ "$MODE" = running ]; then
    capture unit-status.txt systemctl status "$UNIT" --no-pager
    capture unit-show.txt systemctl show "$UNIT" -p ActiveState -p SubState -p MainPID -p ControlGroup -p FragmentPath -p ExecMainStatus
    capture unit-cgroup.txt systemd-cgls "$(systemctl show "$UNIT" -p ControlGroup --value)"
    capture processes-running.txt ps -eo pid,ppid,unit,cgroup,args
    capture sockets-running.txt ss -lntup
    capture links-running.txt ip -details link show
    capture wireless-running.txt iw dev
    capture addresses-running.txt ip -4 address show
    capture routes-running.txt ip -4 route show table all
    capture journal.log journalctl -u "$UNIT" --no-pager --output=cat --since=-30min
    (
        cd "$OUTPUT"
        find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
    )
    printf '运行中快照完成: %s\n' "$OUTPUT"
    exit 0
fi
capture journal.log journalctl -u "$UNIT" --no-pager --output=cat --since=-30min

current="$(python3 - "$WORK_DIR" <<'PY'
import json, os, sys
path = os.path.join(sys.argv[1], 'current-round.json')
with open(path, encoding='utf-8') as fh:
    print(json.load(fh)['round_id'])
PY
)"
[ -f "$WORK_DIR/algorithm-result.json" ] || { printf '缺少算法结果 JSON\n' >&2; exit 1; }
[ -f "$WORK_DIR/queue-summary.json" ] || { printf '缺少 queue summary\n' >&2; exit 1; }
cp "$WORK_DIR/algorithm-result.json" "$OUTPUT/algorithm-result.json"
cp "$WORK_DIR/queue-summary.json" "$OUTPUT/queue-summary.json"
cp -a "$WORK_DIR/rounds/$current" "$OUTPUT/round"
[ -f "$WORK_DIR/uftpd.log" ] && cp "$WORK_DIR/uftpd.log" "$OUTPUT/uftpd.log"
[ -f "$WORK_DIR/uftpd.status" ] && cp "$WORK_DIR/uftpd.status" "$OUTPUT/uftpd.status"
find "$WORK_DIR/rounds/$current" -maxdepth 1 -name 'uftp-*.log' -exec cp {} "$OUTPUT/" \;
find "$WORK_DIR/rounds/$current" -maxdepth 1 -name 'uftp-*.status' -exec cp {} "$OUTPUT/" \;
printf '%s\n' "$current" > "$OUTPUT/ROUND_ID"
(
    cd "$OUTPUT"
    find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
)
printf '采集完成: %s\n' "$OUTPUT"
printf '停止服务后还必须执行 issue41_collect_stopped.sh，证明 cgroup 和孤儿进程为空。\n'
