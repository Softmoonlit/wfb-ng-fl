"""换卡后四客户端的有界待命观察；不修改服务、射频或注册表。"""
import datetime
import json
import pathlib
import sys
import time
import urllib.request

root = pathlib.Path(sys.argv[1]) / 'donor-10min'
root.mkdir()
participants = ['1', '2', '4', '5']
start = time.monotonic()
misses = {n: 0 for n in participants}
result = {'participants': participants, 'duration_seconds': 600, 'completed': False}
with (root / 'samples.jsonl').open('w') as output:
    for index in range(120):
        row = {'mono': time.monotonic(), 'utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}
        try:
            row['status'] = json.load(urllib.request.urlopen('http://127.0.0.1:9090/api/v1/status', timeout=3))
        except Exception as exc:
            row['error'] = repr(exc)
        states = {n: row.get('status', {}).get('nodes', {}).get(n, {}).get('readiness', 'ERROR') for n in participants}
        for node, state in states.items():
            misses[node] = 0 if state == 'READY' else misses[node] + 1
        output.write(json.dumps(row) + '\n')
        output.flush()
        if any(count >= 2 for count in misses.values()):
            result.update({'stop_reason': '连续两个采样点存在非READY节点', 'states': states, 'samples': index + 1})
            break
        time.sleep(max(0, start + (index + 1) * 5 - time.monotonic()))
    else:
        result.update({'completed': True, 'samples': 120})
result['elapsed_seconds'] = time.monotonic() - start
(root / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(result, ensure_ascii=False))
