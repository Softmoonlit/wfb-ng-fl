#!/bin/bash

# 测试：静态 IP 分配与单播连通性 (ISSUE-1)
#
# 此测试验证服务端和客户端脚本是否能够正确配置基于 NODE_ID 的静态 IP
# - Server (NODE_ID=0): 10.0.0.1
# - Client N: 10.0.0.(N+1)

set -e

SCRIPT_DIR=$(dirname "$(readlink -f "$0")")
PROJECT_ROOT=$(dirname "$(dirname "$SCRIPT_DIR")")

# 这里使用 grep 分析脚本而不是执行它们，因为我们只是测试配置 IP 的能力
# 这是一种静态分析，不需要 mock 真实命令
echo "=== 测试: 静态 IP 分配与单播连通性 ==="

echo "检查 server_start.sh 是否有 IP 配置逻辑..."
if ! grep -q "10.0.0.1" "$PROJECT_ROOT/scripts/server_start.sh"; then
    if ! grep -q "\s*ifconfig.*10\.0\.0\.1" "$PROJECT_ROOT/scripts/server_start.sh" && \
       ! grep -q "\s*ip\s*addr\s*add.*10\.0\.0\.1" "$PROJECT_ROOT/scripts/server_start.sh"; then
        echo "❌ 失败: server_start.sh 未发现配置静态 IP (10.0.0.1) 的逻辑"
        exit 1
    fi
fi
echo "✅ server_start.sh 中找到了 10.0.0.1 静态 IP 配置"

echo "检查 client_start.sh 是否有动态 IP 配置逻辑..."
# 检查是否有类似 10.0.0.$((NODE_ID+1)) 或类似逻辑
if ! grep -q "10.0.0." "$PROJECT_ROOT/scripts/client_start.sh"; then
    echo "❌ 失败: client_start.sh 未发现基于 10.0.0.x 的 IP 配置逻辑"
    exit 1
fi
echo "✅ client_start.sh 中找到了基于 10.0.0.x 的 IP 配置"

# 更进一步的测试会在真实的测试脚本中执行
# test_basic_connectivity.sh 等

echo "✅ 成功: 静态 IP 配置逻辑检查通过"
exit 0
