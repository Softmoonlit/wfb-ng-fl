#!/bin/bash
# -*- coding: utf-8 -*-
# Issue #55-#57 临时终端过程模拟器；控制通道只传同步信号，不传输 FL 数据。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROLE="${1:-}"
TRANSFER_MBPS="${2:-}"
FILE_SIZE_MB="${3:-}"

if [ "$#" -gt 3 ]; then
    printf '用法：%s {server|client1|client2} [Mbps] [MB]\n' "${0##*/}" >&2
    exit 2
fi

case "$ROLE" in
    server|client1|client2)
        if [ -n "$FILE_SIZE_MB" ]; then
            exec python3 "$SCRIPT_DIR/issue55_57_demo_control.py" \
                "$ROLE" "$TRANSFER_MBPS" "$FILE_SIZE_MB"
        fi
        if [ -n "$TRANSFER_MBPS" ]; then
            exec python3 "$SCRIPT_DIR/issue55_57_demo_control.py" "$ROLE" "$TRANSFER_MBPS"
        fi
        exec python3 "$SCRIPT_DIR/issue55_57_demo_control.py" "$ROLE"
        ;;
    *)
        printf '用法：%s {server|client1|client2} [Mbps] [MB]\n' "${0##*/}" >&2
        exit 2
        ;;
esac
