"""对固定在线集合采样，抓取fl01/fl05本机单调时间，区分收发完成与状态投影。"""
import concurrent.futures
import datetime
import json
import pathlib
import subprocess
import sys
import time
import urllib.request

phase, evidence = sys.argv[1:]
root = pathlib.Path(evidence) / phase
root.mkdir()
capture = pathlib.Path(__file__).with_name('capture-heartbeats.py').read_text()


def capture_node(host):
    cmd = ['sudo', '-n', '/usr/bin/python3', '-'] if host == 'fl00' else [
        'ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=6', host, 'sudo -n /usr/bin/python3 -']
    try:
        p = subprocess.run(cmd, input=capture, capture_output=True, text=True, timeout=50)
        output, error, code = p.stdout, p.stderr, p.returncode
    except subprocess.TimeoutExpired as exc:
        # 捕获结束后的SSH中断不抹除其他节点已落盘的报文和状态事实。
        output = exc.stdout or ''
        error = exc.stderr or ''
        if isinstance(output, bytes):
            output = output.decode(errors='replace')
        if isinstance(error, bytes):
            error = error.decode(errors='replace')
        error += '\nSSH抓包进程超过50秒截止时间，客户端证据不完整。\n'
        code = 124
    (root / (host + '-capture.json')).write_text(output)
    (root / (host + '-capture.err')).write_text(error)
    return {'host': host, 'exit': code}


with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
    captures = [pool.submit(capture_node, h) for h in ['fl00', 'fl01', 'fl05']]
    start = time.monotonic()
    with (root / 'samples.jsonl').open('w') as output:
        for i in range(24):
            sample = {'mono': time.monotonic(), 'utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}
            try:
                with urllib.request.urlopen('http://127.0.0.1:9090/api/v1/status', timeout=3) as response:
                    sample['status'] = json.load(response)
            except Exception as exc:
                sample['error'] = repr(exc)
            output.write(json.dumps(sample) + '\n')
            output.flush()
            time.sleep(max(0, start + (i + 1) * 5 - time.monotonic()))
    outcomes = [f.result() for f in captures]
    (root / 'capture-outcomes.json').write_text(json.dumps(outcomes, indent=2) + '\n')
    assert all(row['exit'] == 0 for row in outcomes), outcomes
print(root)
