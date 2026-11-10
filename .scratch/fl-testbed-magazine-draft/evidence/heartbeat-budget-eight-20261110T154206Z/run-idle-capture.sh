#!/usr/bin/env bash
set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"
OUT="$ROOT/idle-20261110T1602Z"
CAP="$ROOT/capture-heartbeats-batch.py"
HOSTS=(fl01 fl02 fl03 fl05 fl06 fl07 fl08 fl09)
mkdir -p "$OUT"
: > "$OUT/launcher.lifecycle"
printf 'launcher_start_wall=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)" >> "$OUT/launcher.lifecycle"
CAPTURE=$(<"$CAP")
for host in "${HOSTS[@]}"; do
    start=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)
    printf 'start_wall=%s\n' "$start" > "$OUT/$host.lifecycle"
    (
        printf '%s\n' "$CAPTURE" | timeout 145s ssh -T -oBatchMode=yes -oConnectTimeout=5 "$host" \
            "sudo -n timeout --signal=TERM --kill-after=5s 130s /usr/bin/python3 -I - --duration 120 --clients all --ports 9001" \
            > "$OUT/$host.capture.json" 2> "$OUT/$host.capture.stderr"
        ssh_rc=${PIPESTATUS[1]}
        stop=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)
        printf 'stop_wall=%s\nssh_rc=%s\n' "$stop" "$ssh_rc" >> "$OUT/$host.lifecycle"
        printf '%s\n' "$ssh_rc" > "$OUT/$host.rc"
    ) &
done
wait
for host in "${HOSTS[@]}"; do
    ssh -T -oBatchMode=yes -oConnectTimeout=5 "$host" \
        "journalctl -u wfb-fl-client-daemon.service --since=-150s --no-pager -o short-monotonic" \
        > "$OUT/$host.daemon-journal.log" 2> "$OUT/$host.daemon-journal.stderr" || true
done
printf 'launcher_stop_wall=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)" >> "$OUT/launcher.lifecycle"
printf 'complete\n' > "$OUT/launcher.complete"
