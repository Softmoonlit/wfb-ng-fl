#!/bin/sh
set -eu

prefix=${PREFIX:-/usr}
destdir=${DESTDIR:-}
python=${PYTHON:-python3}

require_command() {
    command -v "$1" >/dev/null 2>&1 || {
        printf '%s\n' "缺少运行依赖: $1" >&2
        exit 1
    }
}

[ -x ./wfb_v6_uplink ] || {
    printf '%s\n' '缺少已构建的 ./wfb_v6_uplink，请先执行 make build_v6' >&2
    exit 1
}
require_command uftp
require_command uftpd

purelib=$($python -c 'import sysconfig; print(sysconfig.get_path("purelib", vars={"base": "'"$prefix"'", "platbase": "'"$prefix"'"}))')
site_dir=$destdir$purelib/wfb_ng
bin_dir=$destdir$prefix/bin
unit_dir=$destdir$prefix/lib/systemd/system
config_dir=$destdir/etc/wfb-ng
default_dir=$destdir/etc/default
share_dir=$destdir$prefix/share/wfb-ng

install -d "$site_dir/fl" "$bin_dir" "$unit_dir" "$config_dir" "$default_dir" "$share_dir"
install -m 0644 scripts/v8-wfb-ng-init.py "$site_dir/__init__.py"
install -m 0644 wfb_ng/fl/*.py "$site_dir/fl/"
install -m 0755 wfb_v6_uplink "$bin_dir/wfb_v6_uplink"
install -m 0755 scripts/wfb-fl-server "$bin_dir/wfb-fl-server"
install -m 0755 scripts/wfb-fl-client "$bin_dir/wfb-fl-client"
install -m 0644 scripts/systemd/wfb-fl-server.service "$unit_dir/"
install -m 0644 scripts/systemd/wfb-fl-client.service "$unit_dir/"
install -m 0644 scripts/default/wfb-fl-server "$default_dir/"
install -m 0644 scripts/default/wfb-fl-client "$default_dir/"
install -m 0644 scripts/default/fl-server.json "$config_dir/"
install -m 0644 scripts/default/fl-client.json "$config_dir/"
install -m 0644 scripts/default/fl-server-algorithm.json "$config_dir/"
install -m 0644 scripts/default/fl-client-algorithm.json "$config_dir/"
install -m 0644 scripts/fixtures/fl-fixture-model.bin "$share_dir/"
