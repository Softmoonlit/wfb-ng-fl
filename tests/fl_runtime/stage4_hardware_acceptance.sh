#!/usr/bin/env bash
# Stage 4 三机唯一正式入口：Web 上传与作业控制，作业窗口不使用 SSH。
set -Eeuo pipefail
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
export PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}"
archive_root="${STAGE4_HARDWARE_ARCHIVE_ROOT:-$repo_root/tests/logs}"
web_url="${STAGE4_WEB_URL:-http://127.0.0.1:8080}"
archive=""
if [[ $# -eq 2 && $1 == --archive ]]; then
    archive=$2
elif [[ $# -ne 0 ]]; then
    printf '%s\n' '用法: stage4_hardware_acceptance.sh [--archive DIRECTORY]' >&2
    exit 2
fi
args=(--archive-root "$archive_root" --web-url "$web_url")
if [[ -n "$archive" ]]; then args+=(--archive "$archive"); fi
set +e
output=$(python3 -m tests.fl_runtime.stage4_hardware_acceptance "${args[@]}" 2>&1)
status=$?
set -e
printf '%s\n' "$output"
if [[ -z "$archive" ]]; then
    archive=$(printf '%s\n' "$output" | awk '/^stage4 hardware archive: / {print $4; exit}')
fi
if [[ -n "$archive" ]]; then
    python3 -m tests.fl_runtime.stage4_hardware_archive "$archive" || status=1
else
    status=1
fi
exit "$status"
