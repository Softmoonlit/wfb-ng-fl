"""统一按IDLE请求配对两端ACK，避免把握手探测算成丢包。"""
import json
import pathlib
import statistics
import sys

root = pathlib.Path(sys.argv[1])
summary = {}
for host in ['fl01', 'fl05']:
    events = json.loads((root / (host + '-capture.json')).read_text())['events']
    requests = {}
    replies = {}
    for event in events:
        body = event['body']
        if event['direction'] == 'out' and body.get('type') == 'NODE_HEARTBEAT' and body['state'] == 'IDLE':
            key = (body['boot_id'], body['session'], body['timestamp_ms'])
            requests.setdefault(key, event['mono'])
        elif event['direction'] == 'in' and body.get('confirmed') is True:
            key = (body['boot_id'], body['session'], body['heartbeat_timestamp_ms'])
            if key in requests and event['mono'] >= requests[key]:
                replies.setdefault(key, (event['mono'] - requests[key]) * 1000)
    delays = list(replies.values())
    summary[host] = {
        'idle_requests': len(requests), 'matched_confirmed_ack': len(delays),
        'within_230ms': sum(d <= 230 for d in delays),
        'median_ms': statistics.median(delays) if delays else None,
        'max_ms': max(delays) if delays else None,
    }
(root / 'idle-heartbeat-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(summary, ensure_ascii=False, indent=2))
