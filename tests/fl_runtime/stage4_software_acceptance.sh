#!/usr/bin/env bash
set -u

# Stage 4 软件验收唯一入口：分层执行器负责保留失败前证据，校验器负责最终判定。
repo_root="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
archive_root="${STAGE4_ARCHIVE_ROOT:-$repo_root/tests/logs}"
output="$(python3 -m tests.fl_runtime.stage4_software_acceptance --archive-root "$archive_root")"
status=$?
printf '%s\n' "$output"
archive="$(printf '%s\n' "$output" | awk '/^stage4 archive: / {print $3; exit}')"
if [ -n "$archive" ]; then
    python3 -m tests.fl_runtime.stage4_archive validate "$archive" || status=1
else
    status=1
fi
exit "$status"
