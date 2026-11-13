#!/usr/bin/env bash
# 最终配置连续两次真实模型等量闭环验收脚本
# 固定拓扑：fl00 + [fl01, fl02, fl03, fl05, fl06, fl07, fl08, fl09]
# 调度：30/5, RS(8,12), 149/HT40/Long GI/功率索引18, DL MCS3/UL MCS4, UFTP 12000 Kbps
# 心跳：单次等待 1.8s, 持续失活 60.0s
# 调度优化：静默剔除 6 次 (~1.7s), 重申报等待 400ms
# 速率基准：下行 MCS 3 (12000 Kbps 稳健广播), 上行 MCS 4 (81.0 Mbps 16-QAM 3/4), 单向硬截止 900 秒
set -euo pipefail

EVIDENCE_ROOT="$(cd "$(dirname "$0")" && pwd)"
RELEASE_COMMIT="e81c4f69eaa25d70d97d5bea35e7e332e967d305"
RELEASE_ROOT="/srv/wfb-ng-fl/releases/heartbeat-e81c4f69eaa2-20261112T061928Z"
NATIVE_SHA="5315ce267446a02c2f124cd4676e686290dcf0352377bd346354f5fc210f9d5f"
REVIEWED_SHA="fbed98b4a7314d72d79be706146e6fbc2cf80a1d51caf3950b92b6072fc6f35c"
PAYLOAD="/home/fl/projects/wfb-ng-fl/.scratch/fl-testbed-magazine-draft/models/cifar10-resnet18-seed7.pt"
MANIFEST="$EVIDENCE_ROOT/model-manifest-input.json"

run_round() {
  local round_num="$1"
  local stamp
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  local run_id="final-closure-r${round_num}-${stamp}-model"
  local round_dir="$EVIDENCE_ROOT/round-${round_num}-${stamp}"
  mkdir -p "$round_dir/runs"

  echo "=========================================================="
  echo ">>> 开始最终闭环第 ${round_num} 轮验收: ${run_id} <<<"
  echo ">>> 参数: 下行 MCS 3 (12 Mbps 稳健广播), 上行 MCS 4 (81.0 Mbps 16-QAM 3/4), 900s 截止"
  echo ">>> 时间: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "=========================================================="

  # 1. 抓取传输前的初始日志行数快照
  echo ">>> 正在抓取传输前日志快照..."
  local snapshot_json
  snapshot_json=$(/usr/bin/python3 "$EVIDENCE_ROOT/collect_fec_telemetry.py" --snapshot)

  local rc=0
  /usr/bin/python3 "$EVIDENCE_ROOT/batchhardwareprobe.py" -- \
    --native-sha "$NATIVE_SHA" \
    --payload "$PAYLOAD" \
    --model-manifest "$MANIFEST" \
    --execute \
    --reviewed-sha "$REVIEWED_SHA" \
    --run-id "$run_id" \
    --downlink-mcs 3 \
    --uplink-mcs 4 \
    --uftp-rate-kbps 12000 \
    --release-commit "$RELEASE_COMMIT" \
    --release-root "$RELEASE_ROOT" \
    --output "$round_dir/runs" \
    > "$round_dir/runner.stdout" 2> "$round_dir/runner.stderr" || rc=$?

  # 2. 统计并输出本轮传输过程的丢包率与 FEC 纠错率
  echo ">>> 正在生成传输过程丢包率与 FEC 纠错率统计报告..."
  /usr/bin/python3 "$EVIDENCE_ROOT/collect_fec_telemetry.py" --report "$snapshot_json" "$round_dir/fec_telemetry.json" \
    | tee "$round_dir/fec_telemetry.md" || true

  echo ">>> 第 ${round_num} 轮结束，returncode=$rc"
  if [ $rc -ne 0 ]; then
    echo "ERROR: 第 ${round_num} 轮验收失败！"
    tail -n 30 "$round_dir/runner.stdout" || true
    tail -n 30 "$round_dir/runner.stderr" || true
    return $rc
  fi

  echo ">>> 第 ${round_num} 轮验收成功通过！"
  return 0
}

# 确保 fl00 USB 3.0 LPM 禁用状态维持
echo -1 | sudo tee /sys/bus/usb/devices/2-1.1/power/autosuspend_delay_ms >/dev/null
echo "on" | sudo tee /sys/bus/usb/devices/2-1.1/power/control >/dev/null
echo "on" | sudo tee /sys/bus/usb/devices/2-1/power/control >/dev/null
echo "disabled" | sudo tee /sys/bus/usb/devices/2-1.1/power/usb3_hardware_lpm_u1 >/dev/null 2>&1 || true
echo "disabled" | sudo tee /sys/bus/usb/devices/2-1.1/power/usb3_hardware_lpm_u2 >/dev/null 2>&1 || true

# 检查当前节点待命状态
python3 /home/fl/projects/wfb-ng-fl/.scratch/fl-testbed-magazine-draft/communication/check-all-ready.py

# 执行轮次
TARGET_ROUND="${1:-all}"

if [ "$TARGET_ROUND" = "1" ]; then
  run_round 1
elif [ "$TARGET_ROUND" = "2" ]; then
  run_round 2
else
  run_round 1
  sleep 5
  python3 /home/fl/projects/wfb-ng-fl/.scratch/fl-testbed-magazine-draft/communication/check-all-ready.py
  run_round 2
  echo "=========================================================="
  echo ">>> 恭喜！连续两轮真实模型等量闭环全部成功通过！ <<<"
  echo "=========================================================="
fi
