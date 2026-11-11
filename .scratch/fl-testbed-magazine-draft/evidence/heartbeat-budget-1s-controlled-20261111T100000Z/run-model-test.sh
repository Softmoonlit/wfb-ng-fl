#!/usr/bin/env bash
# 待命心跳回执等待预算调整为1.0秒后的单变量受控大模型闭环测量
set -u

EVIDENCE_ROOT="$(cd "$(dirname "$0")" && pwd)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
MODEL_ROOT="$EVIDENCE_ROOT/model-$STAMP"
CAPTURE_ROOT="$MODEL_ROOT/capture"
REMOTE_ROOT="/var/tmp/wfb-hb-cap-$STAMP"
RUN_ID="hb-budget-1s-$STAMP-model"
HOSTS=(fl01 fl02 fl03 fl05 fl06 fl07 fl08 fl09)
CAPTURE_LOCAL="$EVIDENCE_ROOT/capture-heartbeats-batch.py"
PID_PROBE="$EVIDENCE_ROOT/collector-pid-probe.py"
JSON_PROBE="$EVIDENCE_ROOT/collector-json-probe.py"
NATIVE_SHA="44d6bbf500f51df4d0b3ae8cd7a53c4a45cb55b09ca66cb5217e7ac75674feb2"
REVIEWED_SHA="1ff80d4683137c191328ef524b910143a2dae462b9c6854e615412da61d46496"
PAYLOAD="/home/fl/projects/wfb-ng-fl/.scratch/fl-testbed-magazine-draft/models/cifar10-resnet18-seed7.pt"
MANIFEST="$EVIDENCE_ROOT/model-manifest-input.json"
PHASES="$MODEL_ROOT/phase-markers.jsonl"

mkdir -p "$MODEL_ROOT/runs" "$CAPTURE_ROOT"
: > "$PHASES"

marker() {
  /usr/bin/python3 - "$1" "${2:-}" >> "$PHASES" <<'PY'
import json,sys,time
print(json.dumps({'phase':sys.argv[1],'detail':sys.argv[2],'wall_ns':time.time_ns(),'monotonic_ns':time.monotonic_ns()}))
PY
}

printf 'wrapper_pid=%s\nstarted_wall=%s\n' "$$" "$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)" > "$MODEL_ROOT/model-capture-wrapper.lifecycle"
printf '%s\n' "$$" > "$MODEL_ROOT/model-capture-wrapper.pid"
marker capture_start "remote_root=$REMOTE_ROOT duration=1500"
sha256sum "$CAPTURE_LOCAL" > "$MODEL_ROOT/collector-local.sha256"

# 1. 部署采集脚本至各节点独立目录
echo ">>> 部署采集器到各节点..."
for host in "${HOSTS[@]}"; do
  remote="$REMOTE_ROOT/$host"
  ssh -T -oBatchMode=yes -oConnectTimeout=5 "$host" "sudo -n install -d -m 700 '$remote'"
  ssh -T -oBatchMode=yes -oConnectTimeout=5 "$host" "sudo -n tee '$remote/capture-heartbeats-batch.py' >/dev/null" < "$CAPTURE_LOCAL"
  ssh -T -oBatchMode=yes -oConnectTimeout=5 "$host" "sudo -n chmod 700 '$remote/capture-heartbeats-batch.py'; sudo -n sha256sum '$remote/capture-heartbeats-batch.py'; sudo -n stat -c '%n mode=%a' '$remote'" > "$CAPTURE_ROOT/$host.remote-copy.sha256-stat.txt"
done

# 2. 启动远端被动采集（后台运行并将PID及日志落盘）
echo ">>> 启动远端被动采集..."
for host in "${HOSTS[@]}"; do
  remote="$REMOTE_ROOT/$host"
  launch="sudo -n sh -c 'echo \$\$ > $remote/collector.pid; printf launch_wall=%s\\n \"\$(date -u +%Y-%m-%dT%H:%M:%SZ)\" > $remote/launch.lifecycle; exec /usr/bin/python3 -I $remote/capture-heartbeats-batch.py --duration 1500 --clients all --ports 9001 > $remote/raw.json 2> $remote/stderr' &"
  ssh -T -oBatchMode=yes -oConnectTimeout=5 "$host" "$launch" > "$CAPTURE_ROOT/$host.launch.stdout" 2> "$CAPTURE_ROOT/$host.launch.stderr" &
  printf '%s\n' "$!" > "$CAPTURE_ROOT/$host.launch-ssh.pid"
done

# 3. 探针核验远端采集进程存活
echo ">>> 核验远端采集进程状态..."
sleep 2
for host in "${HOSTS[@]}"; do
  (
    pid=""
    for attempt in 1 2 3 4 5 6 7 8 9 10; do
      pid=$(ssh -T -oBatchMode=yes -oConnectTimeout=5 "$host" "sudo -n cat '$REMOTE_ROOT/$host/collector.pid'" 2>/dev/null || true)
      [ -n "$pid" ] && break
      sleep .2
    done
    printf '%s\n' "$pid" > "$CAPTURE_ROOT/$host.collector.pid"
    if [ -n "$pid" ]; then
      ssh -T -oBatchMode=yes -oConnectTimeout=5 "$host" "sudo -n /usr/bin/python3 -I - '$REMOTE_ROOT' $pid" < "$PID_PROBE" > "$CAPTURE_ROOT/$host.start-probe.json" 2> "$CAPTURE_ROOT/$host.start-probe.stderr"
    else
      printf '{"status":"pid_file_missing"}\n' > "$CAPTURE_ROOT/$host.start-probe.json"
    fi
    printf '%s\n' "$?" > "$CAPTURE_ROOT/$host.start-probe.rc"
  ) &
  printf '%s\n' "$!" > "$CAPTURE_ROOT/$host.start-probe-job.pid"
done

for host in "${HOSTS[@]}"; do
  wait "$(cat "$CAPTURE_ROOT/$host.start-probe-job.pid")" 2>/dev/null || true
done

start_ok=1
for host in "${HOSTS[@]}"; do
  if ! grep -q '"status": "alive_verified"' "$CAPTURE_ROOT/$host.start-probe.json"; then
    echo "Host $host capture not alive!"
    start_ok=0
  fi
done
marker capture_ready "all_start_pid_cmdline_verified=$start_ok"

if [ "$start_ok" -ne 1 ]; then
  echo "ERROR: 采集器未能全部成功启动，终止测试！"
  marker blocked "collector_start_probe_failed"
  for host in "${HOSTS[@]}"; do
    pid=$(cat "$CAPTURE_ROOT/$host.collector.pid" 2>/dev/null || true)
    if [ -n "$pid" ]; then
      ssh -T -oBatchMode=yes -oConnectTimeout=5 "$host" "sudo -n /usr/bin/python3 -I - '$REMOTE_ROOT' $pid --post --term" < "$PID_PROBE" > "$CAPTURE_ROOT/$host.abort-probe.json" 2> "$CAPTURE_ROOT/$host.abort-probe.stderr" || true
    fi
  done
  exit 2
fi

# 4. 执行模型通信测试
echo ">>> 开始模型通信 (44,770,302 字节)..."
MODEL_CMD=(/usr/bin/python3 "$EVIDENCE_ROOT/batchhardwareprobe.py" -- \
  --native-sha "$NATIVE_SHA" \
  --payload "$PAYLOAD" \
  --model-manifest "$MANIFEST" \
  --execute \
  --reviewed-sha "$REVIEWED_SHA" \
  --run-id "$RUN_ID" \
  --release-commit "ae6e8b49b7576d6a1990c7f93bee87e5ff7121f8" \
  --release-root "/srv/wfb-ng-fl/releases/heartbeat-ae6e8b49b757-20261111T093949Z" \
  --output "$MODEL_ROOT/runs")
printf '%q ' "${MODEL_CMD[@]}" > "$MODEL_ROOT/model-command.txt"
printf '\n' >> "$MODEL_ROOT/model-command.txt"
marker model_start "run_id=$RUN_ID payload_bytes=44770302"

("${MODEL_CMD[@]}" > "$MODEL_ROOT/model-executor.stdout" 2> "$MODEL_ROOT/model-executor.stderr") &
MODEL_PID=$!
printf '%s\n' "$MODEL_PID" > "$MODEL_ROOT/model-executor.pid"
wait "$MODEL_PID"
MODEL_RC=$?
marker model_end "rc=$MODEL_RC"
printf 'model_pid=%s\nmodel_rc=%s\n' "$MODEL_PID" "$MODEL_RC" >> "$MODEL_ROOT/model-capture-wrapper.lifecycle"
echo ">>> 模型通信结束，returncode=$MODEL_RC"

# 5. 优雅终止采集器并拉取数据及系统日志
echo ">>> 优雅终止采集器并收集原始数据与系统日志..."
marker capture_stop_requested "model_rc=$MODEL_RC"
for host in "${HOSTS[@]}"; do
  pid=$(cat "$CAPTURE_ROOT/$host.collector.pid" 2>/dev/null || true)
  if [ -n "$pid" ]; then
    ssh -T -oBatchMode=yes -oConnectTimeout=5 "$host" "sudo -n /usr/bin/python3 -I - '$REMOTE_ROOT' $pid --post --term" < "$PID_PROBE" > "$CAPTURE_ROOT/$host.stop-probe.json" 2> "$CAPTURE_ROOT/$host.stop-probe.stderr" || true
    for attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
      if ssh -T -oBatchMode=yes -oConnectTimeout=5 "$host" "sudo -n test ! -e /proc/$pid" >/dev/null 2>&1; then break; fi
      sleep 2
    done
    ssh -T -oBatchMode=yes -oConnectTimeout=5 "$host" "sudo -n /usr/bin/python3 -I - '$REMOTE_ROOT'" < "$JSON_PROBE" > "$CAPTURE_ROOT/$host.remote-json-probe.json" 2> "$CAPTURE_ROOT/$host.remote-json-probe.stderr"
    printf '%s\n' "$?" > "$CAPTURE_ROOT/$host.remote-json-probe.rc"
    ssh -T -oBatchMode=yes -oConnectTimeout=5 "$host" "sudo -n cat '$REMOTE_ROOT/$host/raw.json'" > "$CAPTURE_ROOT/$host.raw.json" 2> "$CAPTURE_ROOT/$host.raw-read.stderr"
    ssh -T -oBatchMode=yes -oConnectTimeout=5 "$host" "sudo -n cat '$REMOTE_ROOT/$host/stderr'" > "$CAPTURE_ROOT/$host.remote-stderr" 2> "$CAPTURE_ROOT/$host.stderr-read.stderr"
    ssh -T -oBatchMode=yes -oConnectTimeout=5 "$host" "sudo -n stat -c '%n size=%s inode=%i mode=%a mtime=%y' '$REMOTE_ROOT/$host/raw.json' '$REMOTE_ROOT/$host/stderr' '$REMOTE_ROOT/$host/collector.pid'" > "$CAPTURE_ROOT/$host.remote-stat.txt" 2> "$CAPTURE_ROOT/$host.remote-stat.stderr" || true
    ssh -T -oBatchMode=yes -oConnectTimeout=5 "$host" "sudo -n journalctl -u wfb-fl-client-daemon.service --since=-1800s --no-pager -o short-monotonic" > "$CAPTURE_ROOT/$host.daemon-journal.log" 2> "$CAPTURE_ROOT/$host.daemon-journal.stderr" || true
  fi
done

for host in "${HOSTS[@]}"; do
  wait "$(cat "$CAPTURE_ROOT/$host.launch-ssh.pid")" 2>/dev/null || true
done
marker capture_stopped "model_rc=$MODEL_RC"

printf 'finished_wall=%s\nmodel_rc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)" "$MODEL_RC" >> "$MODEL_ROOT/model-capture-wrapper.lifecycle"
printf '%s\n' "$MODEL_RC" > "$MODEL_ROOT/model-capture-wrapper.rc"
echo ">>> 测试完成，退出码=$MODEL_RC"
exit "$MODEL_RC"
