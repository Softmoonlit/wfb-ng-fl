#!/bin/sh
# 仅用于有界调度对照：复用同一native制品，覆盖服务端授权窗口。
# 对照结束后删除systemd实验PATH覆盖，不能作为正式发布入口。
exec /usr/bin/wfb_v6_uplink "$@" --grant-duration-ms 10
