#!/bin/bash
# 三设备 KCP small 手动验收：client2 端脚本

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

CLIENT_IFACE="${CLIENT_IFACE:-wlx08107a083815}"
CLIENT_PORT="${CLIENT_PORT:-5603}"
NODE_ID="${NODE_ID:-2}"
RADIO_PORT="${RADIO_PORT:-0}"
LINK_ID="${LINK_ID:-0}"
BANDWIDTH="${BANDWIDTH:-40}"
FEC_K="${FEC_K:-12}"
FEC_N="${FEC_N:-12}"
LOG_DIR="${LOG_DIR:-$ROOT/tests/logs/manual_kcp_client2_$(date +%Y%m%d_%H%M%S)}"
SOURCE_FILE="${SOURCE_FILE:-$LOG_DIR/kcp_small_client2.bin}"
KCP_SIZE="${KCP_SIZE:-65536}"
KCP_TIMEOUT_MS="${KCP_TIMEOUT_MS:-60000}"
START_SENDER="${START_SENDER:-1}"

mkdir -p "$LOG_DIR"

if [ ! -f "$SOURCE_FILE" ]; then
    dd if=/dev/urandom of="$SOURCE_FILE" bs="$KCP_SIZE" count=1 status=none
fi
sha256sum "$SOURCE_FILE" | tee "$LOG_DIR/source.sha256"

echo "日志目录: $LOG_DIR"
echo "源文件: $SOURCE_FILE"
echo "本地 wfb_tx UDP 端口: $CLIENT_PORT"

cleanup() {
    for pid in ${WFB_TX_PID:-}; do
        if [ -n "$pid" ]; then
            kill "$pid" 2>/dev/null || true
            wait "$pid" 2>/dev/null || true
        fi
    done
}
trap cleanup EXIT

echo "[1/2] 启动 client${NODE_ID} wfb_tx"
sudo ./wfb_tx -g -p "$RADIO_PORT" -u "$CLIENT_PORT" -K drone.key -B "$BANDWIDTH" -k "$FEC_K" -n "$FEC_N" -i "$LINK_ID" "$CLIENT_IFACE" \
    > "$LOG_DIR/client${NODE_ID}.log" 2>&1 &
WFB_TX_PID=$!
sleep 1
if ! kill -0 "$WFB_TX_PID" 2>/dev/null; then
    echo "wfb_tx 启动失败，查看 $LOG_DIR/client${NODE_ID}.log" >&2
    exit 1
fi

if [ "$START_SENDER" != "1" ]; then
    echo "START_SENDER=$START_SENDER，仅启动 wfb_tx，不执行 KCP sender。"
    wait "$WFB_TX_PID"
    exit $?
fi

echo "[2/2] 等待 Token 窗口后执行 kcp_small_sender"
sleep "${SENDER_DELAY_SEC:-2}"
./kcp_small_sender --host 127.0.0.1 --port "$CLIENT_PORT" --input "$SOURCE_FILE" --timeout-ms "$KCP_TIMEOUT_MS" \
    > "$LOG_DIR/kcp_sender.log" 2>&1
SENDER_RC=$?

echo "KCP sender 退出码: $SENDER_RC"
echo "日志目录: $LOG_DIR"
exit "$SENDER_RC"
