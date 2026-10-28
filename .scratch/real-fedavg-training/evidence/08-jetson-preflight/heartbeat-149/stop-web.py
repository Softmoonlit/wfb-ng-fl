#!/usr/bin/env python3
"""通过既有网页启动短作业，观察执行阶段后请求正常停止并记录恢复。"""
import argparse
import importlib.util
import json
from pathlib import Path
import time
import uuid

spec = importlib.util.spec_from_file_location('smoke_web', Path(__file__).with_name('smoke-web.py'))
web_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web_module)
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--web-url', required=True)
args = parser.parse_args()
journal = web_module.Journal(args.output)
web = web_module.Web(journal, args.web_url)
job_id = None
abort_sent = False
try:
    state = web.request('GET', '/api/v1/state')
    if not state or not web_module.admission(state):
        raise RuntimeError('当前未满足固定三个客户端的网页作业准入')
    payload = {'model_sha256': web_module.MODEL, 'target_nodes': [1, 2, 4], 'rounds': 2}
    journal.record('parameters', payload=payload, condition='waiting_updates且三个客户端RUNNING时请求一次正常停止',
                   deployed_commit='c62cde266ef509d605c8c923b0f4fa35e42fc4dd',
                   deployment_evidence='heartbeat-liveness/deployment-evidence.json',
                   stable_evidence='heartbeat-liveness/stable-ready.jsonl', timeout_seconds=600)
    result = web.request('POST', '/api/v1/jobs', payload,
                         key='stop-check-'+uuid.uuid4().hex, timeout=310)
    journal.record('start_result', result=result, retry_allowed=False)
    if not result or not isinstance(result.get('job_id'), str):
        raise RuntimeError('未能确认启动响应；不能猜测作业或重发')
    job_id = result['job_id']
    deadline = time.monotonic()+600
    while time.monotonic() < deadline:
        state = web.request('GET', '/api/v1/state')
        if state:
            current, recent = state.get('current_job'), state.get('recent_job')
            job = next((j for j in (current, recent) if isinstance(j, dict) and j.get('job_id')==job_id), None)
            if job:
                journal.record('job_observation', job_id=job_id, state=state)
                nodes = web_module.nodes_of(state)
                executing = (current is not None and job.get('server_phase')=='waiting_updates'
                             and all(nodes.get(n, {}).get('reported_state')=='RUNNING' for n in [1,2,4]))
                if not abort_sent and executing:
                    # 按真实阶段触发，不以固定延时猜测训练开始。
                    abort_sent = True
                    response = web.request('POST', '/api/v1/jobs/'+job_id+'/abort',
                                           {'reason':'issue08_normal_stop_during_running'}, timeout=30)
                    journal.record('abort_result', job_id=job_id, result=response, retry_allowed=False)
                if recent is job:
                    facts = web_module.report(state, job)
                    stopped = (abort_sent and job.get('execution_result')=='aborted'
                               and job.get('reason')=='issue08_normal_stop_during_running')
                    if job.get('recovery_state') in ('ready','blocked','unconfirmed'):
                        success = stopped and facts['recovery']['confirmed'] and facts['frozen_parameters_confirmed']
                        journal.terminal(status='terminal_observed', job_id=job_id, abort_sent=abort_sent,
                                         success=success, stopped=stopped, recovery=facts['recovery'], job=job)
                        break
        time.sleep(0.5)
    else:
        journal.terminal(status='observation_deadline', job_id=job_id, abort_sent=abort_sent, success=False)
except Exception as exc:
    journal.terminal(status='observer_error', job_id=job_id, abort_sent=abort_sent, error=repr(exc), success=False)
    raise
finally:
    journal.file.close()
