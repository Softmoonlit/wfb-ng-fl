#!/usr/bin/env python3
"""只读采样常驻服务；任何目标离线、配置漂移或不待命均直接失败。"""
import argparse
import json
from pathlib import Path
import time
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument('--seconds', type=float, default=60)
parser.add_argument('--out', type=Path, required=True)
args = parser.parse_args()
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
deadline = time.monotonic() + args.seconds
with args.out.open('x') as stream:
    while True:
        with opener.open('http://127.0.0.1:9090/api/v1/status', timeout=4) as response:
            state = json.load(response)
        record = {'mono': time.monotonic(), 'state': state}
        stream.write(json.dumps(record, ensure_ascii=False) + '\n')
        stream.flush()
        assert state['server_state'] == 'IDLE', state['server_state']
        radio = state['radio']
        assert (radio['channel'], radio['bandwidth_mhz'], radio['radio_txpower_dbm'], radio['downlink_mcs'], radio['uplink_mcs']) == (149, 40, 14, 3, 4), radio
        for node in ['1', '2', '4']:
            actual = state['nodes'][node]
            assert actual['readiness'] == 'READY', (node, actual)
        if time.monotonic() >= deadline:
            break
        time.sleep(min(1, max(0, deadline - time.monotonic())))
print('PASS: 整个观察窗口全员READY且149配置一致')
