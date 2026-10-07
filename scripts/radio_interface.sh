#!/bin/bash
# 根据 RTL8812AU 内核驱动识别 WFB 空口网卡，避免误选手机热点管理网卡。

find_wfb_radio_interfaces() {
    local iface
    local driver

    while IFS= read -r iface; do
        [ -n "$iface" ] || continue
        driver="$(readlink -f "/sys/class/net/$iface/device/driver" 2>/dev/null || true)"
        case "$driver" in
            */rtl88xxau_wfb)
                printf '%s\n' "$iface"
                ;;
        esac
    done < <(iw dev 2>/dev/null | awk '/^[[:space:]]*Interface / {print $2}')
}
