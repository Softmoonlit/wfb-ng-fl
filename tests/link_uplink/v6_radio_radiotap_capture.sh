#!/usr/bin/env bash
# 被动抓取 monitor 接口上的 Radiotap/802.11 帧；不执行 iw/ip，不切换网卡状态。
set -euo pipefail

usage() {
    printf '%s\n' \
        "用法: $0 IFACE PCAP_PATH [SECONDS]" \
        "示例: $0 wlx001122334455 /tmp/v6-radio-change.pcapng 20" >&2
}

if [[ $# -lt 2 || $# -gt 3 ]]; then
    usage
    exit 2
fi

iface=$1
pcap_path=$2
duration=${3:-20}

if ! [[ "$duration" =~ ^[1-9][0-9]*$ ]]; then
    printf 'SECONDS 必须是正整数: %s\n' "$duration" >&2
    exit 2
fi
if [[ -e "$pcap_path" ]]; then
    printf '拒绝覆盖已有抓包文件: %s\n' "$pcap_path" >&2
    exit 2
fi
if ! command -v tcpdump >/dev/null 2>&1; then
    printf '缺少 tcpdump\n' >&2
    exit 1
fi

if [[ $EUID -eq 0 ]]; then
    tcpdump_cmd=(tcpdump)
else
    command -v sudo >/dev/null 2>&1 || {
        printf '当前用户不是 root 且缺少 sudo\n' >&2
        exit 1
    }
    tcpdump_cmd=(sudo tcpdump)
fi

printf '被动抓包接口=%s 文件=%s 时长=%ss\n' "$iface" "$pcap_path" "$duration"
printf '%s\n' '请在抓包期间从另一个终端执行 GET_RADIO、GET_FEC 和 SET_RADIO；本脚本不改变信道、带宽或 monitor 状态。'

set +e
# -I 故意不使用，避免 tcpdump 为抓包而请求切换 monitor 模式。
timeout --foreground --signal=INT --kill-after=2s "${duration}s" \
    "${tcpdump_cmd[@]}" -i "$iface" -s 0 -U -w "$pcap_path"
status=$?
set -e
if [[ $status -ne 0 && $status -ne 124 && $status -ne 130 ]]; then
    printf 'tcpdump 失败，退出码=%s\n' "$status" >&2
    exit "$status"
fi

[[ -s "$pcap_path" ]] || {
    printf '抓包文件为空: %s\n' "$pcap_path" >&2
    exit 1
}

printf '\n-- pcap 摘要 --\n'
sha256sum "$pcap_path"
if command -v capinfos >/dev/null 2>&1; then
    capinfos "$pcap_path" | sed -n '1,18p'
fi

printf '\n-- tcpdump Radiotap/HT 摘要 --\n'
tcpdump -r "$pcap_path" -nn -e -vvv 2>&1 \
    | grep -Ei 'radiotap|MCS|bandwidth|short GI|GI|HT20|HT40' \
    | sed -n '1,160p' || true

if command -v tshark >/dev/null 2>&1; then
    printf '\n-- tshark Radiotap 字段 --\n'
    tshark -r "$pcap_path" -T fields -E header=y -E separator='\t' \
        -e frame.number \
        -e frame.time_relative \
        -e radiotap.mcs.index \
        -e radiotap.mcs.bandwidth \
        -e radiotap.mcs.gi \
        -e radiotap.mcs.flags 2>/dev/null | sed -n '1,160p' || true
fi

printf '\n抓包完成。应在更新前后分别看到对应的 Radiotap MCS/带宽记录；若 tcpdump 未解码字段，请保留 pcap，用 Wireshark/tshark 按 Radiotap MCS 字段复核。\n'
