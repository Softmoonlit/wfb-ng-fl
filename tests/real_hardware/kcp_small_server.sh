#!/bin/bash
# 三设备 KCP small 手动验收：server 端脚本

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

SERVER_IFACE="${SERVER_IFACE:-wlxbcec23372588}"
SERVER_PORT="${SERVER_PORT:-5600}"
RADIO_PORT="${RADIO_PORT:-0}"
LINK_ID="${LINK_ID:-0}"
LOG_DIR="${LOG_DIR:-$ROOT/tests/logs/manual_kcp_server_$(date +%Y%m%d_%H%M%S)}"
RECEIVED_FILE="${RECEIVED_FILE:-$LOG_DIR/kcp_small_received.bin}"
KCP_TIMEOUT_MS="${KCP_TIMEOUT_MS:-60000}"

mkdir -p "$LOG_DIR"

echo "日志目录: $LOG_DIR"
echo "接收文件: $RECEIVED_FILE"

cleanup() {
    for pid in ${WFB_RX_PID:-} ${KCP_RX_PID:-}; do
        if [ -n "$pid" ]; then
            kill "$pid" 2>/dev/null || true
            wait "$pid" 2>/dev/null || true
        fi
    done
}
trap cleanup EXIT

echo "[1/2] 启动 wfb_rx"
sudo ./wfb_rx -p "$RADIO_PORT" -u "$SERVER_PORT" -K gs.key -R 16777216 -l 1000 -i "$LINK_ID" "$SERVER_IFACE" \
    > "$LOG_DIR/server.log" 2>&1 &
WFB_RX_PID=$!
sleep 1
if ! kill -0 "$WFB_RX_PID" 2>/dev/null; then
    echo "wfb_rx 启动失败，查看 $LOG_DIR/server.log" >&2
    exit 1
fi

echo "[2/2] 启动 kcp_small_receiver"
./kcp_small_receiver --port "$SERVER_PORT" --output "$RECEIVED_FILE" --timeout-ms "$KCP_TIMEOUT_MS" \
    > "$LOG_DIR/kcp_receiver.log" 2>&1 &
KCP_RX_PID=$!

wait "$KCP_RX_PID"
KCP_RC=$?

if [ -f "$RECEIVED_FILE" ]; then
    sha256sum "$RECEIVED_FILE" | tee "$LOG_DIR/received.sha256"
fi

echo "KCP receiver 退出码: $KCP_RC"
echo "日志目录: $LOG_DIR"
exit "$KCP_RC"
