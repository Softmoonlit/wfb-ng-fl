#!/bin/bash
# Capture fail-closed service stop/orphan evidence after systemctl stop.
set -euo pipefail
ROLE="${1:-}"
OUTPUT="${2:-}"
case "$ROLE" in server) UNIT=wfb-fl-server.service ;; client1|client2) UNIT=wfb-fl-client.service ;; *) exit 2 ;; esac
[ -d "$OUTPUT" ] || { printf '证据目录不存在: %s\n' "$OUTPUT" >&2; exit 2; }
failed=0
active="$(systemctl is-active "$UNIT" 2>/dev/null || true)"
[ "$active" = inactive ] || { printf '[FAIL] unit state=%s\n' "$active" >&2; failed=1; }
main_pid="$(systemctl show "$UNIT" -p MainPID --value)"
[ "$main_pid" = 0 ] || { printf '[FAIL] MainPID=%s\n' "$main_pid" >&2; failed=1; }
orphans="$(pgrep -a -f '(^|/)(wfb_v6_uplink|wfb-fl-server|wfb-fl-client|uftp|uftpd)( |$)' 2>/dev/null || true)"
[ -z "$orphans" ] || { printf '[FAIL] orphan processes: %s\n' "$orphans" >&2; failed=1; }
{
    printf 'unit=%s\nactive_state=%s\nmain_pid=%s\n' "$UNIT" "$active" "$main_pid"
    printf 'orphan_process_count=%s\n' "$([ -z "$orphans" ] && printf 0 || printf 1)"
    printf 'result=%s\n' "$([ "$failed" -eq 0 ] && printf PASS || printf FAIL)"
} > "$OUTPUT/stopped-lifecycle.txt"
(
    cd "$OUTPUT"
    find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
)
exit "$failed"
