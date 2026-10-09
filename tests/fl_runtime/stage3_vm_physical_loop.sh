#!/usr/bin/env bash
# 唯一正式 Stage 3 入口；仅管理 daemon，不通过 SSH 编排作业。
set -Eeuo pipefail
REPO_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"
STAGE=${1:-run-all}
if (($#)); then shift; fi
case "$STAGE" in
    preflight|install|start-services|run-sync|run-radio-recovery|collect|stop-services|validate|run-all) ;;
    -h|--help)
        printf '%s\n' 'Usage: stage3_vm_physical_loop.sh [run-all|preflight|install|start-services|run-sync|run-radio-recovery|collect|stop-services|validate] [--archive DIRECTORY]' \
          'STAGE3_ARCHIVE_ROOT overrides tests/logs; every attempt gets fresh run/job IDs.'
        exit 0 ;;
    *) printf 'Unknown stage: %s\n' "$STAGE" >&2; exit 2 ;;
esac
ARCHIVE=''
if (($#)); then
    if [[ $# != 2 || $1 != --archive ]]; then
        printf '%s\n' 'Only --archive DIRECTORY is accepted.' >&2
        exit 2
    fi
    ARCHIVE=$2
elif [[ $STAGE == run-all || $STAGE == preflight ]]; then
    ARCHIVE=$(python3 - "$REPO_ROOT" <<'PY'
import os, sys
from pathlib import Path
from tests.fl_runtime.stage3_runner import initialize
repo = Path(sys.argv[1])
print(initialize(Path(os.environ.get('STAGE3_ARCHIVE_ROOT', str(repo / 'tests/logs'))), repo))
PY
)
else
    printf '%s\n' '--archive is required to continue an existing run.' >&2
    exit 2
fi
CHILD_PID=''
finish_child() {
    if [[ -n "$CHILD_PID" ]]; then
        if kill -0 "$CHILD_PID" 2>/dev/null; then
            kill -TERM "$CHILD_PID" 2>/dev/null || true
        fi
        wait "$CHILD_PID" 2>/dev/null || true
        CHILD_PID=''
    fi
}
on_signal() {
    trap '' INT TERM HUP
    finish_child
    exit "$1"
}
cleanup() {
    result=$?
    trap - EXIT INT TERM HUP
    finish_child
    if ((result != 0)) && [[ -f "$ARCHIVE/envelope.json" && ! -f "$ARCHIVE/archive_manifest.json" ]]; then
        # Python also has try/finally. Fallback retries are serialized after
        # child exit and preserve the original failure + its first snapshot.
        python3 -m tests.fl_runtime.stage3_runner cleanup --archive "$ARCHIVE" --repo "$REPO_ROOT" || true
    fi
    exit "$result"
}
trap cleanup EXIT
trap 'on_signal 130' INT
trap 'on_signal 143' TERM HUP
python3 -m tests.fl_runtime.stage3_runner "$STAGE" --archive "$ARCHIVE" --repo "$REPO_ROOT" &
CHILD_PID=$!
wait "$CHILD_PID"
CHILD_PID=''
