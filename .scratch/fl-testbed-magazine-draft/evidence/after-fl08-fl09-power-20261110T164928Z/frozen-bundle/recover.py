#!/usr/bin/env python3
"""独立回收本轮临时 unit 和专有路由；仅在全端结束后按需恢复射频。

本通用回收器不硬编码具体实验网卡身份；批次硬件变化由外层 raw inventory gate 处理。"""
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import urllib.request


NODES = [1, 2, 3, 5, 6, 7, 8, 9]


def run(argv, check=True, timeout=10):
    return subprocess.run(argv, capture_output=True, text=True, check=check, timeout=timeout)


def main():
    run_id = sys.argv[1]
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', run_id):
        raise ValueError('非法批次')
    host = socket.gethostname()
    if host not in ['fl00'] + [f'fl{node:02d}' for node in NODES] or os.geteuid() != 0:
        raise ValueError('节点或权限不符')
    root = Path('/srv/wfb-ng-fl') / ('comm-' + run_id)
    state_path = root / 'cleanup-state.json'
    restore = len(sys.argv) == 3 and sys.argv[2] == '--restore'
    state = json.loads(state_path.read_text()) if state_path.exists() else None
    if state is not None and state['host'] != host:
        raise ValueError('回收记录节点不符')
    if restore:
        if host != 'fl00':
            raise ValueError('射频只能由服务端恢复')
        if state is None or not state['radio_attempted']:
            return dict(ok=True, attempted=False)
        pid = str(state['daemon_pid'])
        current = run(['systemctl', 'show', 'wfb-fl-server-daemon.service', '-p', 'MainPID', '--value']).stdout.strip()
        tick = Path('/proc', pid, 'stat').read_text().rsplit(')', 1)[1].split()[19]
        if current != pid or tick != state['resident'][pid]:
            raise ValueError('服务端身份已改变，拒绝沿用旧批次恢复')
        request = urllib.request.Request('http://127.0.0.1:9090/api/v1/radio/reconfigure',
            data=json.dumps(dict(confirmed=True, target_nodes=[1, 2, 3, 5, 6, 7, 8, 9], patch=state['original'])).encode(),
            headers={'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
        ok = result.get('status') == 'finalized' and result.get('calibration') is False
        ok = ok and all(result['effective_config'][k] == v for k, v in state['original'].items())
        # 独立恢复缺少原代理九端审计，RF 响应成功不能宣称最终恢复通过。
        output = dict(ok=False, rf_response_ok=ok, audited=False, attempted=True, response=result)
        (root / 'independent-restore.json').write_text(json.dumps(output))
        return output
    unit = 'comm-smoke-' + run_id + '-' + host + '.service'
    stopped = run(['systemctl', 'stop', unit], check=False, timeout=12)
    active = run(['systemctl', 'is-active', unit], check=False).stdout.strip()
    if active not in ['inactive', 'failed', 'unknown']:
        raise ValueError('测试 unit 尚未结束：' + active + stopped.stderr)
    errors = []
    if state is not None:
        for group, owned in state['routes'].items():
            try:
                if group not in ['239.80.41.1/32', '239.80.42.1/32']:
                    raise ValueError('非本轮组播路由')
                current = json.loads(run(['ip', '-j', 'route', 'show', 'exact', group]).stdout)
                if not current:
                    continue
                links = json.loads(run(['ip', '-j', 'link', 'show', 'dev', state['tun']]).stdout)
                if links[0]['ifindex'] != state['ifindex'] or current != owned:
                    raise ValueError('路由或接口身份改变，保留现场')
                run(['ip', 'route', 'del', group, 'dev', state['tun'], 'proto', '186'])
            except Exception as exc:
                errors.append(group + ': ' + repr(exc))
        for pid, tick in state['resident'].items():
            try:
                if Path('/proc', pid, 'stat').read_text().rsplit(')', 1)[1].split()[19] != tick:
                    raise ValueError('常驻进程身份变化')
            except Exception as exc:
                errors.append('resident ' + pid + ': ' + repr(exc))
    output = dict(ok=not errors, unit=unit, active=active, errors=errors)
    if root.is_dir():
        (root / 'independent-cleanup.json').write_text(json.dumps(output))
    return output


if __name__ == '__main__':
    try:
        result = main()
    except Exception as exc:
        result = dict(ok=False, error=repr(exc))
    print(json.dumps(result), flush=True)
    raise SystemExit(0 if result['ok'] else 1)
