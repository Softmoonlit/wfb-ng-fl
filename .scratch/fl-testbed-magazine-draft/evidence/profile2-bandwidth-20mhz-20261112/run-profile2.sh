#!/usr/bin/env bash
# Profile 2 (可用带宽受限：40MHz -> 20MHz HT20) 验收脚本
# 固定拓扑：fl00 + [fl01, fl02, fl03, fl05, fl06, fl07, fl08, fl09]
# 射频参数：HT20, 信道 157 (基线待命 149), 驱动功率索引 18, 下行 MCS 3, 上行 MCS 4, 注入 6000 Kbps
# 调度与FEC：30/5, RS(8,12), 400ms重申报, 6次静默剔除, 1.8s ACK超时, 60s失活与看门狗
set -euo pipefail

EVIDENCE_ROOT="$(cd "$(dirname "$0")" && pwd)"
RELEASE_COMMIT="e81c4f69eaa25d70d97d5bea35e7e332e967d305"
RELEASE_ROOT="/srv/wfb-ng-fl/releases/heartbeat-e81c4f69eaa2-20261112T061928Z"
NATIVE_SHA="5315ce267446a02c2f124cd4676e686290dcf0352377bd346354f5fc210f9d5f"
REVIEWED_SHA="fbed98b4a7314d72d79be706146e6fbc2cf80a1d51caf3950b92b6072fc6f35c"
PAYLOAD="/home/fl/projects/wfb-ng-fl/.scratch/fl-testbed-magazine-draft/models/cifar10-resnet18-seed7.pt"
MANIFEST="$EVIDENCE_ROOT/model-manifest-input.json"

run_smoke() {
  local stamp
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  local run_id="profile2-smoke-${stamp}"
  local round_dir="$EVIDENCE_ROOT/smoke-${stamp}"
  mkdir -p "$round_dir/runs"

  echo "=========================================================="
  echo ">>> 开始 Profile 2 (HT20) 1 MiB 通信冒烟: ${run_id} <<<"
  echo ">>> 参数: 20 MHz (HT20), 信道 157, DL MCS 3 / UL MCS 4, 注入 6000 Kbps"
  echo ">>> 时间: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "=========================================================="

  local rc=0
  /usr/bin/python3 "$EVIDENCE_ROOT/batchhardwareprobe.py" -- \
    --native-sha "$NATIVE_SHA" \
    --smoke \
    --execute \
    --reviewed-sha "$REVIEWED_SHA" \
    --run-id "$run_id" \
    --bandwidth-mhz 20 \
    --downlink-mcs 3 \
    --uplink-mcs 4 \
    --uftp-rate-kbps 6000 \
    --release-commit "$RELEASE_COMMIT" \
    --release-root "$RELEASE_ROOT" \
    --output "$round_dir/runs" \
    > "$round_dir/runner.stdout" 2> "$round_dir/runner.stderr" || rc=$?

  echo ">>> 冒烟结束，returncode=$rc"
  if [ $rc -ne 0 ]; then
    echo "ERROR: Profile 2 冒烟失败！"
    tail -n 30 "$round_dir/runner.stdout" || true
    tail -n 30 "$round_dir/runner.stderr" || true
    return $rc
  fi
  echo ">>> Profile 2 冒烟成功通过！"
  return 0
}

run_round() {
  local round_num="$1"
  local stamp
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  local run_id="profile2-model-r${round_num}-${stamp}"
  local round_dir="$EVIDENCE_ROOT/round-${round_num}-${stamp}"
  mkdir -p "$round_dir/runs"

  echo "=========================================================="
  echo ">>> 开始 Profile 2 (HT20) 第 ${round_num} 轮模型闭环: ${run_id} <<<"
  echo ">>> 参数: 20 MHz (HT20), 信道 157, DL MCS 3 / UL MCS 4, 注入 6000 Kbps"
  echo ">>> 时间: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "=========================================================="

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
    --bandwidth-mhz 20 \
    --downlink-mcs 3 \
    --uplink-mcs 4 \
    --uftp-rate-kbps 6000 \
    --release-commit "$RELEASE_COMMIT" \
    --release-root "$RELEASE_ROOT" \
    --output "$round_dir/runs" \
    > "$round_dir/runner.stdout" 2> "$round_dir/runner.stderr" || rc=$?

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

TARGET="${1:-smoke}"

if [ "$TARGET" = "smoke" ]; then
  run_smoke
elif [ "$TARGET" = "1" ]; then
  run_round 1
elif [ "$TARGET" = "2" ]; then
  run_round 2
elif [ "$TARGET" = "all" ]; then
  run_smoke
  sleep 5
  python3 /home/fl/projects/wfb-ng-fl/.scratch/fl-testbed-magazine-draft/communication/check-all-ready.py
  run_round 1
  sleep 5
  python3 /home/fl/projects/wfb-ng-fl/.scratch/fl-testbed-magazine-draft/communication/check-all-ready.py
  run_round 2
  echo "=========================================================="
  echo ">>> 恭喜！Profile 2 (HT20) 冒烟与连续两轮模型闭环全部成功！ <<<"
  echo "=========================================================="
fi
