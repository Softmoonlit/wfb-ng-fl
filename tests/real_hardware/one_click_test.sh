#!/bin/bash
# tests/real_hardware/one_click_test.sh
# Token-gated split-process 测试入口

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# 兼容原有的 config 位置
if [ -f "$PROJECT_ROOT/tests/config/test_config.sh" ]; then
    source "$PROJECT_ROOT/tests/config/test_config.sh"
elif [ -f "$PROJECT_ROOT/../tests/config/test_config.sh" ]; then
    source "$PROJECT_ROOT/../tests/config/test_config.sh"
else
    echo "错误: 找不到测试配置文件 tests/config/test_config.sh"
    exit 1
fi

RUN_TOKEN=false
TOKEN_SCENARIO="single"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --token)
            RUN_TOKEN=true
            shift
            ;;
        --token-scenario)
            TOKEN_SCENARIO="$2"
            shift 2
            ;;
        --help|-h)
            echo "用法: sudo bash tests/real_hardware/one_click_test.sh [--token [--token-scenario single|dual|expiry|all]]"
            exit 0
            ;;
        *)
            echo "未知参数: $1" >&2
            exit 1
            ;;
    esac
done

if [ "$RUN_TOKEN" = true ]; then
    echo "========================================="
    echo "运行 Token-gated split-process 验收 ($TOKEN_SCENARIO)"
    echo "========================================="
    bash "$SCRIPT_DIR/test_token_gated_uplink.sh" --scenario "$TOKEN_SCENARIO"
    exit $?
fi

echo "未指定 --token，退出。"
exit 0
