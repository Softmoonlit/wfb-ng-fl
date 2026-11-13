#!/usr/bin/env bash
# 本副本只负责本轮 capture 与唯一 model runner 的精确生命周期，不修改通用工具。
set -u

ROOT="$(cd "$(dirname "$0")" && pwd)"
STAMP="20261110T164928Z"
MODEL_ROOT="$ROOT/model-$STAMP"
CAPTURE_ROOT="$MODEL_ROOT/capture"
RUN_ID="after-fl08-fl09-power-$STAMP-model"
CAPTURE="$ROOT/capture-heartbeats-batch.py"
HOSTS=(fl01 fl02 fl03 fl05 fl06 fl07 fl08 fl09)
NATIVE_SHA="44d6bbf500f51df4d0b3ae8cd7a53c4a45cb55b09ca66cb5217e7ac75674feb2"
REVIEWED_SHA="1ff80d4683137c191328ef524b910143a2dae462b9c6854e615412da61d46496"
PAYLOAD="/home/fl/projects/wfb-ng-fl/.scratch/fl-testbed-magazine-draft/models/cifar10-resnet18-seed7.pt"
MANIFEST="$ROOT/model-manifest-input.json"
PHASES="$ROOT/phase-markers.jsonl"

mkdir -p "$MODEL_ROOT/runs" "$CAPTURE_ROOT"
: > "$PHASES"
printf 'wrapper_pid=%s\nstarted_wall=%s\n' "$$" "$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)" > "$ROOT/model-capture-wrapper.lifecycle"
printf '%s\n' "$$" > "$ROOT/model-capture-wrapper.pid"

marker() {
    local phase="$1"; local detail="${2:-}"
    /usr/bin/python3 - "$phase" "$detail" >> "$PHASES" <<'PY'
import json
import sys
import time
print(json.dumps({'phase': sys.argv[1], 'detail': sys.argv[2],
                  'wall_ns': time.time_ns(), 'monotonic_ns': time.monotonic_ns()}))
PY
}

CAPTURE_SCRIPT=$(<"$CAPTURE")
REMOTE_CAPTURE_CMD="sh -c 'sudo -n /usr/bin/python3 -I - --duration 1500 --clients all --ports 9001 & p=\$!; printf \"remote_capture_pid=%s\\\\n\" \"\$p\" >&2; wait \"\$p\"'"
CAPTURE_START_WALL="$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)"
marker capture_start "hosts=${HOSTS[*]} duration=1500"
declare -A CAP_PID
for host in "${HOSTS[@]}"; do
    printf 'host=%s start_wall=%s start_mono_ns=%s\n' "$host" "$CAPTURE_START_WALL" "$(date +%s%N)" > "$CAPTURE_ROOT/$host.lifecycle"
    (
        printf '%s\n' "$CAPTURE_SCRIPT" | ssh -T -oBatchMode=yes -oConnectTimeout=5 "$host" "$REMOTE_CAPTURE_CMD" \
            > "$CAPTURE_ROOT/$host.capture.json" 2> "$CAPTURE_ROOT/$host.capture.stderr"
        rc=$?
        printf 'stop_wall=%s\nssh_rc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)" "$rc" >> "$CAPTURE_ROOT/$host.lifecycle"
        printf '%s\n' "$rc" > "$CAPTURE_ROOT/$host.rc"
    ) &
    CAP_PID[$host]=$!
    printf '%s\n' "${CAP_PID[$host]}" > "$CAPTURE_ROOT/$host.ssh.pid"
done
printf 'capture_launcher_pid=%s\n' "$$" > "$CAPTURE_ROOT/launcher.pid"

# 给 SSH/sudo 启动窗口，随后仅检查本轮自有 PID，不以采集内容猜测 ready。
sleep 4
capture_ready=1
for host in "${HOSTS[@]}"; do
    if ! kill -0 "${CAP_PID[$host]}" 2>/dev/null; then
        capture_ready=0
    fi
done
marker capture_ready "local_capture_processes_alive=$capture_ready"
if [ "$capture_ready" -ne 1 ]; then
    printf 'capture_ready=0\n' >> "$ROOT/model-capture-wrapper.lifecycle"
    marker blocked "capture_process_not_alive"
    for host in "${HOSTS[@]}"; do kill -TERM "${CAP_PID[$host]}" 2>/dev/null || true; done
    exit 2
fi

MODEL_CMD=(/usr/bin/python3 "$ROOT/batchhardwareprobe.py" -- \
    --native-sha "$NATIVE_SHA" \
    --payload "$PAYLOAD" \
    --model-manifest "$MANIFEST" \
    --execute \
    --reviewed-sha "$REVIEWED_SHA" \
    --run-id "$RUN_ID" \
    --output "$MODEL_ROOT/runs")
printf '%q ' "${MODEL_CMD[@]}" > "$ROOT/model-command.txt"
printf '\n' >> "$ROOT/model-command.txt"
marker model_start "run_id=$RUN_ID payload_bytes=44770302 deadline_each_direction=600"
("${MODEL_CMD[@]}" > "$ROOT/model-executor.stdout" 2> "$ROOT/model-executor.stderr") &
MODEL_PID=$!
printf '%s\n' "$MODEL_PID" > "$ROOT/model-executor.pid"
wait "$MODEL_PID"
MODEL_RC=$?
marker model_end "rc=$MODEL_RC"
printf 'model_pid=%s\nmodel_rc=%s\n' "$MODEL_PID" "$MODEL_RC" >> "$ROOT/model-capture-wrapper.lifecycle"

# 模型 runner 完成后，按远端 capture PID 精确终止，保留 SIGTERM 收尾的 JSON。
marker capture_stop_requested "model_rc=$MODEL_RC"
for host in "${HOSTS[@]}"; do
    remote_pid=""
    for _ in 1 2 3 4 5; do
        remote_pid=$(sed -n 's/^remote_capture_pid=//p' "$CAPTURE_ROOT/$host.capture.stderr" | head -n 1)
        [ -n "$remote_pid" ] && break
        sleep 1
    done
    if [[ "$remote_pid" =~ ^[0-9]+$ ]]; then
        printf 'remote_pid=%s\n' "$remote_pid" > "$CAPTURE_ROOT/$host.remote.pid"
        ssh -T -oBatchMode=yes -oConnectTimeout=5 "$host" "sudo -n kill -TERM $remote_pid" \
            > "$CAPTURE_ROOT/$host.stop.stdout" 2> "$CAPTURE_ROOT/$host.stop.stderr"
        printf '%s\n' "$?" > "$CAPTURE_ROOT/$host.stop.rc"
    else
        printf 'remote_pid_unavailable\n' > "$CAPTURE_ROOT/$host.remote.pid"
        kill -TERM "${CAP_PID[$host]}" 2> "$CAPTURE_ROOT/$host.stop.stderr" || true
        printf 'local_ssh_fallback\n' > "$CAPTURE_ROOT/$host.stop.stdout"
        printf 'fallback\n' > "$CAPTURE_ROOT/$host.stop.rc"
    fi
done

for host in "${HOSTS[@]}"; do
    wait "${CAP_PID[$host]}" 2>/dev/null || true
done
CAPTURE_STOP_WALL="$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)"
marker capture_stopped "wall=$CAPTURE_STOP_WALL"
for host in "${HOSTS[@]}"; do
    ssh -T -oBatchMode=yes -oConnectTimeout=5 "$host" \
        "sudo -n journalctl -u wfb-fl-client-daemon.service --since='$CAPTURE_START_WALL' --until='$CAPTURE_STOP_WALL' --no-pager -o short-monotonic" \
        > "$CAPTURE_ROOT/$host.daemon-journal-root.log" 2> "$CAPTURE_ROOT/$host.daemon-journal-root.stderr" || true
done
printf 'capture_stop_wall=%s\ncomplete\n' "$CAPTURE_STOP_WALL" >> "$CAPTURE_ROOT/launcher.lifecycle"
printf 'finished_wall=%s\nmodel_rc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)" "$MODEL_RC" >> "$ROOT/model-capture-wrapper.lifecycle"
printf '%s\n' "$MODEL_RC" > "$ROOT/model-capture-wrapper.rc"
exit "$MODEL_RC"
