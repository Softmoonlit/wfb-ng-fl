#!/usr/bin/env bash
set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"
OUT="$ROOT/model-20261110T1606Z/capture"
CAP="$ROOT/capture-heartbeats-batch.py"
HOSTS=(fl01 fl02 fl03 fl05 fl06 fl07 fl08 fl09)
mkdir -p "$OUT"
CAPTURE=$(<"$CAP")
printf 'launcher_start_wall=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)" > "$OUT/launcher.lifecycle"
for host in "${HOSTS[@]}"; do
    start=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)
    printf 'start_wall=%s\n' "$start" > "$OUT/$host.lifecycle"
    (
        printf '%s\n' "$CAPTURE" | timeout 1525s ssh -T -oBatchMode=yes -oConnectTimeout=5 "$host" \
            "sudo -n timeout --signal=TERM --kill-after=5s 1510s /usr/bin/python3 -I - --duration 1500 --clients all --ports 9001" \
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
        "sudo -n journalctl -u wfb-fl-client-daemon.service --since=-1530s --no-pager -o short-monotonic" \
        > "$OUT/$host.daemon-journal-root.log" 2> "$OUT/$host.daemon-journal-root.stderr" || true
done
printf 'launcher_stop_wall=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)" >> "$OUT/launcher.lifecycle"
printf 'complete\n' > "$OUT/launcher.complete"
