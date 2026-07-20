#!/bin/bash
# 由 SSH 三机编排器分两阶段记录 restart/active/cgroup/stop/no-orphan。
set -euo pipefail
ROLE="${1:-}"
OUTPUT="${2:-}"
PHASE="${3:-}"
case "$ROLE" in
    server)
        UNIT=wfb-fl-server.service
        WORK_DIR=/var/lib/wfb-ng/fl-server
        ;;
    client1|client2)
        UNIT=wfb-fl-client.service
        WORK_DIR=/var/lib/wfb-ng/fl-client
        ;;
    *) exit 2 ;;
esac
case "$PHASE" in start|stop) ;; *) exit 2 ;; esac
[ -d "$OUTPUT" ] || { printf '证据目录不存在: %s\n' "$OUTPUT" >&2; exit 2; }
[ -f "$OUTPUT/algorithm-result.json" ] && [ -d "$OUTPUT/round" ] || {
    printf '正式作业尚未完整归档: %s\n' "$OUTPUT" >&2
    exit 1
}
[ ! -e "$WORK_DIR/current-round.json" ] || {
    printf '旧 work dir 仍有 current-round.json，拒绝 restart\n' >&2
    exit 1
}

running_record="$OUTPUT/restart-running.txt"
if [ "$PHASE" = start ]; then
    systemctl start "$UNIT"
    active="$(systemctl is-active "$UNIT" 2>/dev/null || true)"
    main_pid="$(systemctl show "$UNIT" -p MainPID --value)"
    cgroup="$(systemctl show "$UNIT" -p ControlGroup --value)"
    [ "$active" = active ] && [ "${main_pid:-0}" -gt 0 ] && [ -n "$cgroup" ] || {
        printf 'restart 后 unit 未进入 active: %s pid=%s cgroup=%s\n' \
          "$active" "$main_pid" "$cgroup" >&2
        exit 1
    }
    {
        printf 'restart_active_state=%s\n' "$active"
        printf 'restart_main_pid=%s\n' "$main_pid"
        printf 'restart_cgroup=%s\n' "$cgroup"
        systemd-cgls "$cgroup" 2>&1
    } > "$running_record"
    exit 0
fi

[ -f "$running_record" ] || {
    printf '缺少 restart active 阶段记录\n' >&2
    exit 1
}
systemctl stop "$UNIT"
stopped="$(systemctl is-active "$UNIT" 2>/dev/null || true)"
stopped_pid="$(systemctl show "$UNIT" -p MainPID --value)"
orphans="$(pgrep -a -f '(^|/)(wfb_v6_uplink|wfb-fl-server|wfb-fl-client|uftp|uftpd)( |$)' 2>/dev/null || true)"
failed=0
[ "$stopped" = inactive ] || failed=1
[ "$stopped_pid" = 0 ] || failed=1
[ -z "$orphans" ] || failed=1
{
    printf 'unit=%s\n' "$UNIT"
    cat "$running_record"
    printf 'stopped_active_state=%s\n' "$stopped"
    printf 'stopped_main_pid=%s\n' "$stopped_pid"
    printf 'orphan_process_count=%s\n' "$([ -z "$orphans" ] && printf 0 || printf 1)"
    printf 'result=%s\n' "$([ "$failed" -eq 0 ] && printf PASS || printf FAIL)"
} > "$OUTPUT/restart-lifecycle.txt"
rm -f "$running_record"
(
    cd "$OUTPUT"
    find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
)
exit "$failed"
