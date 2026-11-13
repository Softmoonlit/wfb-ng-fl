"""按用户指令并行关闭客户端，保留逐端命令接受结果；fl00由父最后安排。"""
import concurrent.futures
import datetime
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent

def shutdown(host):
    args = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', '-o',
            'ConnectionAttempts=1', host, 'sudo -n shutdown -h now']
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=12)
        return dict(host=host, returncode=p.returncode, command_accepted=p.returncode == 0,
                    stdout=p.stdout, stderr=p.stderr)
    except subprocess.TimeoutExpired as exc:
        return dict(host=host, command_accepted=False, error='管理SSH在12秒内未完成',
                    stdout=str(exc.stdout or ''), stderr=str(exc.stderr or ''))

if __name__ == '__main__':
    hosts = ['fl%02d' % n for n in range(1, 11)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        rows = list(pool.map(shutdown, hosts))
    result = dict(reason='用户要求暂停充电，全部关机',
                  utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  clients=rows,
                  note='command_accepted只证明关机命令返回成功，不等于独立物理断电确认；未接受者需现场确认')
    (ROOT / 'clients-shutdown.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))
