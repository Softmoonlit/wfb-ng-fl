#!/bin/sh
set -eu

prefix=${PREFIX:-/usr}
destdir=${DESTDIR:-}
python=${PYTHON:-python3}

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_root=$(CDPATH= cd -- "$script_dir/../.." && pwd)
cd "$project_root"

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
require_command make

# 直接部署也必须重新生成身份，不能依赖工作区遗留的构建文件。
make build_identity PYTHON="$python"

purelib=$($python -c 'import sysconfig; print(sysconfig.get_path("purelib", vars={"base": "'"$prefix"'", "platbase": "'"$prefix"'"}))')
site_dir=$destdir$purelib/wfb_ng
bin_dir=$destdir$prefix/bin
unit_dir=$destdir$prefix/lib/systemd/system
config_dir=$destdir/etc/wfb-ng

install -d "$site_dir/fl/static" "$bin_dir" "$unit_dir" "$config_dir"
install -m 0644 scripts/install/v8-wfb-ng-init.py "$site_dir/__init__.py"
install -m 0644 wfb_ng/fl/*.py "$site_dir/fl/"
install -m 0644 wfb_ng/fl/build_identity.json "$site_dir/fl/build_identity.json"
install -m 0644 wfb_ng/fl/static/* "$site_dir/fl/static/"
install -m 0755 wfb_v6_uplink "$bin_dir/wfb_v6_uplink"
install -m 0755 scripts/install/wfb-fl-server "$bin_dir/wfb-fl-server"
install -m 0755 scripts/install/wfb-fl-client "$bin_dir/wfb-fl-client"
install -m 0755 scripts/install/wfb-fl-server-daemon "$bin_dir/wfb-fl-server-daemon"
install -m 0755 scripts/install/wfb-fl-client-daemon "$bin_dir/wfb-fl-client-daemon"
install -m 0644 scripts/systemd/wfb-fl-server.service "$unit_dir/"
install -m 0644 scripts/systemd/wfb-fl-client.service "$unit_dir/"
install -m 0644 scripts/systemd/wfb-fl-server-daemon.service "$unit_dir/"
install -m 0644 scripts/systemd/wfb-fl-client-daemon.service "$unit_dir/"
install -m 0644 scripts/default/fl-server.json "$config_dir/"
install -m 0644 scripts/default/fl-client.json "$config_dir/"

identity_dir=$destdir/etc/wfb-ng-fl
install -d "$identity_dir"
if [ ! -e "$identity_dir/node.json" ]; then
    install -m 0644 scripts/default/node.json "$identity_dir/node.json"
fi
if [ ! -e "$identity_dir/server.json" ]; then
    install -m 0644 scripts/default/server.json "$identity_dir/server.json"
fi
