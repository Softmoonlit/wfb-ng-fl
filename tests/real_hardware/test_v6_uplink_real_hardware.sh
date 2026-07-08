#!/bin/bash
# tests/real_hardware/test_v6_uplink_real_hardware.sh
# 旧的同机三网卡自动脚本已退役；v6 正式 real-hardware 入口改为三机手动手册。

set -euo pipefail

usage() {
    cat <<'EOF'
用法:
  bash tests/real_hardware/test_v6_uplink_real_hardware.sh

说明:
  该脚本对应的 same-host / netns 自动跑数方案已经过时，不再作为 v6 正式 real-hardware 验收入口。

当前正式入口:
  tests/real_hardware/v6新底座无SSH手动上行演示手册.md

配套执行脚本:
  tests/real_hardware/v6_manual_uplink_demo.sh

建议执行顺序:
  1. 阅读无 SSH 手动上行演示手册。
  2. 在 server / client1 / client2 三台机器分别按手册执行短命令。
  3. 使用手册中定义的 2A 摘要、日志与 SHA256 作为正式证据归档。
EOF
}

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    usage
    exit 0
fi

usage >&2
exit 2
