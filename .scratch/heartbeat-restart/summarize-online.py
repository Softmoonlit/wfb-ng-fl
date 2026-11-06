"""依据客户端单调时钟配对请求与回执，不跨主机比较墙上时钟。"""
import collections
import json
import pathlib
import statistics
import sys

root = pathlib.Path(sys.argv[1])
rows = [json.loads(line) for line in (root / 'samples.jsonl').read_text().splitlines()]
summary = {'samples': len(rows), 'nodes': {}, 'heartbeat': {}}
for node in ['1', '2', '4', '5', '6']:
    summary['nodes'][node] = dict(collections.Counter(
        row.get('status', {}).get('nodes', {}).get(node, {}).get('readiness', 'ERROR') for row in rows))
for host in ['fl01', 'fl05']:
    p = root / (host + '-capture.json')
    if not p.exists() or not p.stat().st_size:
        continue
    events = json.loads(p.read_text())['events']
    requests = {}
    matches = {}
    boots = set()
    states = collections.Counter()
    for event in events:
        body = event['body']
        if event['direction'] == 'out' and body.get('type') == 'NODE_HEARTBEAT':
            key = (body.get('boot_id'), body.get('session'), body['timestamp_ms'])
            requests.setdefault(key, event['mono'])
            boots.add(body.get('boot_id'))
            states[body['state']] += 1
        elif event['direction'] == 'in' and body.get('confirmed') is True:
            key = (body.get('boot_id'), body.get('session'), body['heartbeat_timestamp_ms'])
            if key in requests and event['mono'] >= requests[key]:
                matches.setdefault(key, (event['mono'] - requests[key]) * 1000)
    delays = list(matches.values())
    summary['heartbeat'][host] = {
        'requests': len(requests), 'matched_confirmed_ack': len(delays),
        'within_230ms': sum(d <= 230 for d in delays),
        'median_ms': statistics.median(delays) if delays else None,
        'max_ms': max(delays) if delays else None, 'boot_ids': sorted(boots),
        'request_states': dict(states)}
(root / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(summary, ensure_ascii=False, indent=2))
