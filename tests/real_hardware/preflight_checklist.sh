#!/bin/bash
# issue #41 三机真实硬件验收预检。默认只读且 fail-closed。
set -euo pipefail

ROLE="${ROLE:-}"
PHASE="${PHASE:-clean}"
CONFIG="${CONFIG:-}"
CHANNEL="${CHANNEL:-157}"
CHANNEL_WIDTH="${CHANNEL_WIDTH:-HT40+}"
EXIT_CODE=0

usage() {
    printf '%s\n' \
      '用法: preflight_checklist.sh --role server|client1|client2 --config PATH [--phase clean|runtime]' \
      'clean: 要求无角色进程、TUN、端口和组播路由残留。' \
      'runtime: 要求 unit active、实际 TUN/IP、UFTP/HTTP 端口和组播路由存在。'
}
while [[ $# -gt 0 ]]; do
    case "$1" in
        --role) ROLE="${2:-}"; shift 2 ;;
        --config) CONFIG="${2:-}"; shift 2 ;;
        --phase) PHASE="${2:-}"; shift 2 ;;
        --check-only) shift ;;
        --help|-h) usage; exit 0 ;;
        *) printf '未知参数: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
done
case "$ROLE" in
    server) UNIT=wfb-fl-server.service; CONFIG="${CONFIG:-/etc/wfb-ng/fl-server.json}" ;;
    client1|client2) UNIT=wfb-fl-client.service; CONFIG="${CONFIG:-/etc/wfb-ng/fl-client.json}" ;;
    *) printf '必须指定 --role server|client1|client2\n' >&2; exit 2 ;;
esac
case "$PHASE" in clean|runtime) ;; *) printf '无效 --phase: %s\n' "$PHASE" >&2; exit 2 ;; esac

pass() { printf '[PASS] %s\n' "$1"; }
fail() { printf '[FAIL] %s\n' "$1" >&2; EXIT_CODE=1; }
for command in python3 ip iw ss pgrep systemctl sha256sum; do
    command -v "$command" >/dev/null 2>&1 || fail "缺少命令: $command"
done
for binary in /usr/bin/wfb_v6_uplink /usr/bin/wfb-fl-server /usr/bin/wfb-fl-client; do
    [ -x "$binary" ] && pass "安装后二进制可执行: $binary" || fail "安装后二进制缺失或不可执行: $binary"
done
for binary in uftp uftpd; do
    command -v "$binary" >/dev/null 2>&1 && pass "依赖可执行: $(command -v "$binary")" || fail "缺少安装后依赖: $binary"
done
for unit in wfb-fl-server.service wfb-fl-client.service; do
    unit_path="$(systemctl show -p FragmentPath --value "$unit" 2>/dev/null || true)"
    [ -n "$unit_path" ] && [ -f "$unit_path" ] && pass "unit 已安装: $unit -> $unit_path" || fail "unit 未安装: $unit"
done
[ -r "$CONFIG" ] || { fail "角色配置不可读: $CONFIG"; exit "$EXIT_CODE"; }

eval "$(python3 - "$CONFIG" <<'PY'
import json, shlex, sys
try:
    with open(sys.argv[1], encoding='utf-8') as fh:
        data = json.load(fh)
    args = data['link_args']
    def option(name):
        return args[args.index(name) + 1]
    values = {
        'CFG_ROLE': data['role'], 'TUN_NAME': option('--tun-name'),
        'TUN_ADDR': option('--tun-addr'), 'AIR_IFACE': option('--air-interface'),
        'UFTP_PORT': str(data['uftp_port']),
        'MCAST': data.get('uftp_multicast_address', data.get('server_uftp_multicast_address', '')),
        'HTTP_PORT': str(data.get('http_port', data.get('server_http_port', ''))),
    }
except Exception as exc:
    print('CONFIG_ERROR=' + shlex.quote(str(exc)))
else:
    for key, value in values.items():
        print('%s=%s' % (key, shlex.quote(value)))
PY
)"
if [ -n "${CONFIG_ERROR:-}" ]; then fail "角色配置解析失败: $CONFIG_ERROR"; exit "$EXIT_CODE"; fi
expected_role=client; [ "$ROLE" = server ] && expected_role=server
[ "$CFG_ROLE" = "$expected_role" ] || fail "配置 role=$CFG_ROLE 与现场角色 $ROLE 不符"

if ip link show "$AIR_IFACE" >/dev/null 2>&1; then
    mode="$(iw dev "$AIR_IFACE" info 2>/dev/null | awk '/type / {print $2; exit}')"
    channel_line="$(iw dev "$AIR_IFACE" info 2>/dev/null | awk '/channel / {print; exit}')"
    state="$(ip -br link show "$AIR_IFACE" 2>/dev/null | awk '{print $2}')"
    channel_width_ok=false
    case "$CHANNEL_WIDTH" in
        HT40+|HT40-)
            if [[ "$channel_line" == *"$CHANNEL_WIDTH"* ]] || [[ "$channel_line" == *'width: 40 MHz'* ]]; then
                channel_width_ok=true
            fi
            ;;
        *)
            [[ "$channel_line" == *"$CHANNEL_WIDTH"* ]] && channel_width_ok=true
            ;;
    esac
    if [ "$mode" = monitor ] && [ "$state" = UP ] && [[ "$channel_line" == *"channel $CHANNEL"* ]] && [ "$channel_width_ok" = true ]; then
        pass "实际无线接口: $AIR_IFACE monitor/UP $CHANNEL/$CHANNEL_WIDTH"
    else
        fail "无线接口状态不符: iface=$AIR_IFACE mode=${mode:-?} state=${state:-?} channel=${channel_line:-?}"
    fi
else
    fail "实际无线接口不存在: $AIR_IFACE"
fi

processes="$(pgrep -a -f '(^|/)(wfb_v6_uplink|wfb-fl-server|wfb-fl-client|uftp|uftpd)( |$)' 2>/dev/null || true)"
tun_exists=false; ip link show "$TUN_NAME" >/dev/null 2>&1 && tun_exists=true
udp="$(ss -H -lunp 2>/dev/null | awk -v p=":$UFTP_PORT" '$5 ~ p"$" {print}' || true)"
tcp="$(ss -H -ltnp 2>/dev/null | awk -v p=":$HTTP_PORT" '$4 ~ p"$" {print}' || true)"
route="$(ip route show table all 2>/dev/null | awk -v m="$MCAST" -v d="$TUN_NAME" '$1 == m && $0 ~ ("dev " d "( |$)") {print}' || true)"
if [ "$PHASE" = clean ]; then
    [ -z "$processes" ] && pass '无 wfb_v6_uplink/wfb-fl/uftp/uftpd 残留' || fail "发现角色进程残留: $processes"
    [ "$tun_exists" = false ] && pass "无 TUN 残留: $TUN_NAME" || fail "发现 TUN 残留: $TUN_NAME"
    [ -z "$udp$tcp" ] && pass "实际端口空闲: UDP/$UFTP_PORT TCP/$HTTP_PORT" || fail "发现端口残留: $udp $tcp"
    [ -z "$route" ] && pass "无组播路由残留: $MCAST dev $TUN_NAME" || fail "发现组播路由残留: $route"
else
    systemctl is-active --quiet "$UNIT" && pass "unit active: $UNIT" || fail "unit 未 active: $UNIT"
    if [ "$tun_exists" = true ]; then
        ip -4 addr show dev "$TUN_NAME" | grep -Fq "inet $TUN_ADDR" && pass "实际 TUN/IP: $TUN_NAME $TUN_ADDR" || fail "TUN IP 不符: $TUN_NAME 期望 $TUN_ADDR"
    else
        fail "实际 TUN 不存在: $TUN_NAME"
    fi
    [ -n "$processes" ] && pass '角色进程存在' || fail '角色进程不存在'
    [ -n "$udp" ] && pass "实际 UFTP UDP/$UFTP_PORT 在监听" || fail "UFTP UDP/$UFTP_PORT 未监听"
    if [ "$ROLE" = server ]; then
        [ -n "$tcp" ] && pass "实际 HTTP TCP/$HTTP_PORT 在监听" || fail "HTTP TCP/$HTTP_PORT 未监听"
    fi
    [ -n "$route" ] && pass "实际组播路由: $route" || fail "缺少组播路由: $MCAST dev $TUN_NAME"
fi
exit "$EXIT_CODE"
