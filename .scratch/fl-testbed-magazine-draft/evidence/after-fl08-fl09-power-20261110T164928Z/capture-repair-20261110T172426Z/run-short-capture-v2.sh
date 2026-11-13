#!/usr/bin/env bash
# 采集修复验证：远端持久化 raw，绝不把 SSH stdout 当唯一证据。
set -u
ROOT_BASE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$ROOT_BASE/short-capture-v2"
mkdir -p "$ROOT"
REMOTE_ROOT=/var/tmp/wfb-heartbeat-capture-repair-20261110T172426Z-v2
CAPTURE_LOCAL="$ROOT_BASE/capture-heartbeats-batch-reviewed.py"
PROBE_LOCAL="$ROOT_BASE/collector-pid-probe.py"
HOSTS=(fl01 fl02 fl03 fl05 fl06 fl07 fl08 fl09)
DURATION=5
printf 'repair_start_wall=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)" > "$ROOT/short-capture.lifecycle"
sha256sum "$CAPTURE_LOCAL" > "$ROOT/collector-copy.sha256"
for host in "${HOSTS[@]}"; do
    remote="$REMOTE_ROOT/$host"
    ssh -T -oBatchMode=yes -oConnectTimeout=5 "$host" "sudo -n install -d -m 700 '$remote'"
    ssh -T -oBatchMode=yes -oConnectTimeout=5 "$host" "sudo -n tee '$remote/capture-heartbeats-batch.py' >/dev/null" < "$CAPTURE_LOCAL"
    ssh -T -oBatchMode=yes -oConnectTimeout=5 "$host" "sudo -n chmod 700 '$remote/capture-heartbeats-batch.py'; sudo -n sha256sum '$remote/capture-heartbeats-batch.py'" > "$ROOT/$host.remote-collector-sha256.txt"
done
for host in "${HOSTS[@]}"; do
    remote="$REMOTE_ROOT/$host"
    launch="sudo -n sh -c 'echo \$\$ > $remote/collector.pid; printf launch_wall=%s\\n \"\$(date -u +%Y-%m-%dT%H:%M:%SZ)\" > $remote/launch.lifecycle; exec /usr/bin/python3 -I $remote/capture-heartbeats-batch.py --duration $DURATION --clients all --ports 9001 > $remote/raw.json 2> $remote/stderr' &"
    ssh -T -oBatchMode=yes -oConnectTimeout=5 "$host" "$launch" > "$ROOT/$host.launch.stdout" 2> "$ROOT/$host.launch.stderr" &
    printf '%s\n' "$!" > "$ROOT/$host.launch-ssh.pid"
done
for host in "${HOSTS[@]}"; do wait "$(cat "$ROOT/$host.launch-ssh.pid")" || true; done
printf 'launch_ssh_done_wall=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)" >> "$ROOT/short-capture.lifecycle"
sleep 2
for host in "${HOSTS[@]}"; do
    (
        pid=""
        for attempt in 1 2 3 4 5 6 7 8 9 10; do
            pid=$(ssh -T -oBatchMode=yes -oConnectTimeout=5 "$host" "sudo -n cat '$REMOTE_ROOT/$host/collector.pid'" 2>/dev/null || true)
            [ -n "$pid" ] && break
            sleep .2
        done
        printf '%s\n' "$pid" > "$ROOT/$host.collector.pid"
        if [ -n "$pid" ]; then
            ssh -T -oBatchMode=yes -oConnectTimeout=5 "$host" "sudo -n /usr/bin/python3 -I - $pid" < "$PROBE_LOCAL" > "$ROOT/$host.running-probe.json" 2> "$ROOT/$host.running-probe.stderr"
        else
            printf '{"status":"pid_file_missing"}\n' > "$ROOT/$host.running-probe.json"
            printf 'pid file missing\n' > "$ROOT/$host.running-probe.stderr"
        fi
        printf '%s\n' "$?" > "$ROOT/$host.running-probe.rc"
    ) &
done
for host in "${HOSTS[@]}"; do
    wait || true
done
printf 'running_probe_done_wall=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)" >> "$ROOT/short-capture.lifecycle"
sleep 6
for host in "${HOSTS[@]}"; do
    remote="$REMOTE_ROOT/$host"
    ssh -T -oBatchMode=yes -oConnectTimeout=5 "$host" "sudo -n cat '$remote/raw.json'" > "$ROOT/$host.raw.json" 2> "$ROOT/$host.raw-read.stderr"
    printf '%s\n' "$?" > "$ROOT/$host.raw-read.rc"
    ssh -T -oBatchMode=yes -oConnectTimeout=5 "$host" "sudo -n cat '$remote/stderr'" > "$ROOT/$host.remote-stderr" 2> "$ROOT/$host.stderr-read.stderr"
    ssh -T -oBatchMode=yes -oConnectTimeout=5 "$host" "sudo -n stat -c '%n size=%s inode=%i mode=%a mtime=%y' '$remote/raw.json' '$remote/stderr' '$remote/collector.pid'" > "$ROOT/$host.remote-stat.txt" 2> "$ROOT/$host.remote-stat.stderr" || true
    ssh -T -oBatchMode=yes -oConnectTimeout=5 "$host" "sudo -n journalctl -u wfb-fl-client-daemon.service --since=-30s --no-pager -o short-monotonic" > "$ROOT/$host.daemon-journal.log" 2> "$ROOT/$host.daemon-journal.stderr" || true
done
for host in "${HOSTS[@]}"; do
    pid=$(cat "$ROOT/$host.collector.pid")
    if [ -n "$pid" ]; then
        ssh -T -oBatchMode=yes -oConnectTimeout=5 "$host" "sudo -n /usr/bin/python3 -I - $pid --post --term" < "$PROBE_LOCAL" > "$ROOT/$host.post-probe.json" 2> "$ROOT/$host.post-probe.stderr" || true
    fi
done
printf 'repair_stop_wall=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)" >> "$ROOT/short-capture.lifecycle"
