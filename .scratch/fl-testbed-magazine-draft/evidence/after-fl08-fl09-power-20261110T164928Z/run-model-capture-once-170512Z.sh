#!/usr/bin/env bash
# 本副本只负责本轮 capture 与唯一 model runner 的精确生命周期，不修改通用工具。
set -u

ROOT="$(cd "$(dirname "$0")" && pwd)"
STAMP="20261110T170512Z"
MODEL_ROOT="$ROOT/model-$STAMP"
CAPTURE_ROOT="$MODEL_ROOT/capture"
RUN_ID="after-fl08-fl09-power-$STAMP-model"
CAPTURE="$ROOT/capture-heartbeats-batch.py"
HOSTS=(fl01 fl02 fl03 fl05 fl06 fl07 fl08 fl09)
NATIVE_SHA="44d6bbf500f51df4d0b3ae8cd7a53c4a45cb55b09ca66cb5217e7ac75674feb2"
REVIEWED_SHA="1ff80d4683137c191328ef524b910143a2dae462b9c6854e615412da61d46496"
PAYLOAD="/home/fl/projects/wfb-ng-fl/.scratch/fl-testbed-magazine-draft/models/cifar10-resnet18-seed7.pt"
MANIFEST="$ROOT/model-manifest-input.json"
PHASES="$MODEL_ROOT/phase-markers.jsonl"

mkdir -p "$MODEL_ROOT/runs" "$CAPTURE_ROOT"
: > "$PHASES"
printf 'wrapper_pid=%s\nstarted_wall=%s\n' "$$" "$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)" > "$MODEL_ROOT/model-capture-wrapper.lifecycle"
printf '%s\n' "$$" > "$MODEL_ROOT/model-capture-wrapper.pid"

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
CAPTURE_START_WALL="$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)"
marker capture_start "hosts=${HOSTS[*]} duration=1500"
declare -A CAP_PID
for host in "${HOSTS[@]}"; do
    printf 'host=%s start_wall=%s start_mono_ns=%s\n' "$host" "$CAPTURE_START_WALL" "$(date +%s%N)" > "$CAPTURE_ROOT/$host.lifecycle"
    (
        printf '%s\n' "$CAPTURE_SCRIPT" | timeout --foreground --signal=TERM --kill-after=5s 1525s ssh -T -oBatchMode=yes -oConnectTimeout=5 "$host" \
            "sudo -n timeout --signal=TERM --kill-after=5s 1510s /usr/bin/python3 -I - --duration 1500 --clients all --ports 9001" \
            > "$CAPTURE_ROOT/$host.capture.json" 2> "$CAPTURE_ROOT/$host.capture.stderr" &
        timeout_pid=$!
        printf '%s\n' "$timeout_pid" > "$CAPTURE_ROOT/$host.timeout.pid"
        wait "$timeout_pid"
        rc=$?
        printf 'stop_wall=%s\ntimeout_rc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)" "$rc" >> "$CAPTURE_ROOT/$host.lifecycle"
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
    if ! test -s "$CAPTURE_ROOT/$host.timeout.pid" || ! kill -0 "$(cat "$CAPTURE_ROOT/$host.timeout.pid")" 2>/dev/null; then
        capture_ready=0
    fi
done
marker capture_ready "local_capture_processes_alive=$capture_ready"
if [ "$capture_ready" -ne 1 ]; then
    printf 'capture_ready=0\n' >> "$MODEL_ROOT/model-capture-wrapper.lifecycle"
    marker blocked "capture_process_not_alive"
    for host in "${HOSTS[@]}"; do
        if test -s "$CAPTURE_ROOT/$host.timeout.pid"; then
            kill -TERM "$(cat "$CAPTURE_ROOT/$host.timeout.pid")" 2>/dev/null || true
        fi
    done
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
printf '%q ' "${MODEL_CMD[@]}" > "$MODEL_ROOT/model-command.txt"
printf '\n' >> "$MODEL_ROOT/model-command.txt"
marker model_start "run_id=$RUN_ID payload_bytes=44770302 deadline_each_direction=600"
("${MODEL_CMD[@]}" > "$MODEL_ROOT/model-executor.stdout" 2> "$MODEL_ROOT/model-executor.stderr") &
MODEL_PID=$!
printf '%s\n' "$MODEL_PID" > "$MODEL_ROOT/model-executor.pid"
wait "$MODEL_PID"
MODEL_RC=$?
marker model_end "rc=$MODEL_RC"
printf 'model_pid=%s\nmodel_rc=%s\n' "$MODEL_PID" "$MODEL_RC" >> "$MODEL_ROOT/model-capture-wrapper.lifecycle"

# 模型 runner 完成后，只终止本轮各节点的本地 timeout PID。
marker capture_stop_requested "model_rc=$MODEL_RC"
for host in "${HOSTS[@]}"; do
    timeout_pid=$(cat "$CAPTURE_ROOT/$host.timeout.pid" 2>/dev/null || true)
    if [[ "$timeout_pid" =~ ^[0-9]+$ ]]; then
        printf 'timeout_pid=%s\n' "$timeout_pid" > "$CAPTURE_ROOT/$host.stop.pid"
        kill -TERM "$timeout_pid" 2> "$CAPTURE_ROOT/$host.stop.stderr" || true
        printf 'local_timeout_sigterm\n' > "$CAPTURE_ROOT/$host.stop.stdout"
        printf '%s\n' "$?" > "$CAPTURE_ROOT/$host.stop.rc"
    else
        printf 'timeout_pid_unavailable\n' > "$CAPTURE_ROOT/$host.stop.pid"
        printf 'no_timeout_pid\n' > "$CAPTURE_ROOT/$host.stop.stderr"
        printf 'no_timeout_pid\n' > "$CAPTURE_ROOT/$host.stop.stdout"
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
printf 'finished_wall=%s\nmodel_rc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)" "$MODEL_RC" >> "$MODEL_ROOT/model-capture-wrapper.lifecycle"
printf '%s\n' "$MODEL_RC" > "$MODEL_ROOT/model-capture-wrapper.rc"
exit "$MODEL_RC"
