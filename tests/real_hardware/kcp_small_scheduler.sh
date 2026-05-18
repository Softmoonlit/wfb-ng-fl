#!/bin/bash
# 三设备 KCP small 手动验收：server 端 Token 调度器脚本

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

NODES="${NODES:-1,2}"
DURATION_MS="${DURATION_MS:-1000}"
GUARD_MS="${GUARD_MS:-100}"
SOCKETS="${SOCKETS:-1:5602,2:5603}"
LOG_DIR="${LOG_DIR:-$ROOT/tests/logs/manual_kcp_scheduler_$(date +%Y%m%d_%H%M%S)}"

mkdir -p "$LOG_DIR"

echo "日志目录: $LOG_DIR"
echo "节点: $NODES"
echo "socket 映射: $SOCKETS"

echo "启动 wfb_token_scheduler"
sudo ./wfb_token_scheduler -n "$NODES" -d "$DURATION_MS" -g "$GUARD_MS" -s "$SOCKETS" \
    2>&1 | tee "$LOG_DIR/scheduler.log"
