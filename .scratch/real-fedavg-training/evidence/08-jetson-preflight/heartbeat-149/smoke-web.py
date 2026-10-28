#!/usr/bin/env python3
"""通过既有管理网页执行两轮四机真实训练并持久记录事实。

本文件只供父流程在 release 已验证、三客户端稳定心跳至少一百二十秒后运行。
部署证据必须来自实际部署进程和发布产物，工作树提交不能代替实际构建核验。
本脚本不运行部署命令，不访问远程终端，不复制模型，不修改配置，不探测射频。
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

MODEL = "b1a1c1734bec69acd26df1ed6c6aaafecd0b7a6b5b3c2f9b594043d7fca453d1"
MODEL_SOURCE = "/srv/wfb-ng-fl/server/mnist-initial-seed7.pt"
TARGETS = [1, 2, 4]
ROUNDS = 2
POLL = 5
DEADLINE = 300 + ROUNDS * 1800 + 120


def arguments():
    """要求父流程提供可审阅的实际部署与前置条件证据。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web-url", required=True, help="实际管理网页地址，例如 http://192.168.108.100:8080；不做地址回退")
    parser.add_argument("--output", required=True, help="新的证据目录，必须不存在")
    parser.add_argument("--deployed-commit", required=True, help="实际发布产物的完整提交哈希")
    parser.add_argument("--deployed-argv-json", required=True,
                        help="实际四机进程参数的本地 JSON 文件，键为 server、1、2、4，值为参数数组")
    parser.add_argument("--deployment-evidence", required=True, help="实际部署构建核验的本地证据文件")
    parser.add_argument("--release-evidence", required=True, help="父流程确认 release 已验证版的本地证据文件")
    parser.add_argument("--heartbeat-evidence", required=True, help="三客户端稳定心跳的本地证据文件")
    parser.add_argument("--stable-heartbeat-seconds", required=True, type=float,
                        help="父流程从心跳证据确认的连续稳定秒数，至少一百二十秒")
    args = parser.parse_args()
    parsed = urllib.parse.urlsplit(args.web_url)
    if (parsed.scheme != "http" or not parsed.hostname or parsed.port is None
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        parser.error("必须明确指定含主机与端口的 HTTP 网页地址，不允许凭据、路径、查询或片段")
    args.web_url = args.web_url.rstrip("/")
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", args.deployed_commit):
        parser.error("实际部署提交必须为完整的小写提交哈希")
    if not 120 <= args.stable_heartbeat_seconds < float("inf"):
        parser.error("必须先有至少一百二十秒连续稳定心跳证据")
    return args


def evidence(path):
    """保存父流程提供的证据原文与摘要，不将其误作在线构建探测。"""
    source = Path(path).resolve()
    raw = source.read_bytes()
    if not raw.strip():
        raise ValueError("前置证据文件不能为空：" + str(source))
    return {"path": str(source), "sha256": hashlib.sha256(raw).hexdigest(),
            "text": raw.decode("utf-8")}


class Journal:
    """每条记录均刷入操作系统并同步到存储。"""

    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.file = (self.directory / "events.jsonl").open("x", encoding="utf-8")

    def record(self, kind, **facts):
        entry = {"time": datetime.now(timezone.utc).isoformat(),
                 "monotonic": time.monotonic(), "kind": kind, **facts}
        self.file.write(json.dumps(entry, ensure_ascii=False, allow_nan=False) + "\n")
        self.file.flush()
        os.fsync(self.file.fileno())

    def terminal(self, **facts):
        self.record("terminal_report", **facts)
        with (self.directory / "terminal.json").open("x", encoding="utf-8") as output:
            json.dump(facts, output, ensure_ascii=False, indent=2, allow_nan=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())


class Web:
    """仅访问明确指定的管理网页端点，禁止自动重定向或重发写请求。"""

    def __init__(self, journal, base_url):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.journal = journal
        self.base_url = base_url

    def request(self, method, path, body=None, key=None, timeout=10):
        headers = {"Accept": "application/json", "Cache-Control": "no-store"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        if key:
            headers["Idempotency-Key"] = key
        self.journal.record("http_request", method=method, path=path, body=body, headers=headers)
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
                status = response.status
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            status = exc.code
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            self.journal.record("http_transport_error", method=method, path=path, error=str(exc))
            return None
        self.journal.record("http_response", method=method, path=path, status=status, raw=raw)
        try:
            value = json.loads(raw)
        except ValueError:
            self.journal.record("http_json_error", method=method, path=path)
            return None
        return value if 200 <= status < 300 and isinstance(value, dict) else None


def nodes_of(state):
    """使用网页契约的节点列表，不推断缺失节点状态。"""
    return {node.get("node_id"): node for node in state.get("nodes", []) if isinstance(node, dict)}


def ready(node):
    """同时核对基础就绪、运行状态及网页选择资格。"""
    return (node.get("readiness") == "READY" and node.get("reported_state") == "IDLE"
            and node.get("can_select") is True and node.get("online") is True
            and node.get("error_code") is None)


def admission(state):
    """只依据网页真实字段准入三个固定客户端。"""
    server = state.get("server", {})
    recent = state.get("recent_job")
    nodes = nodes_of(state)
    return (server.get("state") == "idle" and server.get("can_start_job") is True
            and server.get("start_blockers") == [] and state.get("current_job") is None
            and (recent is None or recent.get("recovery_state") == "ready")
            and all(ready(nodes.get(node, {})) for node in TARGETS))


def report(state, job):
    """分开保留执行、效果和恢复事实，成功严格使用服务端 succeeded 枚举。"""
    nodes = nodes_of(state or {})
    job = job or {}
    recovered = (job.get("recovery_state") == "ready"
                 and job.get("server_recovery_complete") is True
                 and job.get("recovery_wait_finished") is True
                 and job.get("recovery_missing_nodes") == []
                 and all(ready(nodes.get(node, {})) for node in TARGETS)
                 and (state or {}).get("server", {}).get("state") == "idle"
                 and (state or {}).get("current_job") is None)
    execution_ok = job.get("execution_result") == "succeeded" and job.get("rounds_completed") == ROUNDS
    frozen_ok = (job.get("target_nodes") == TARGETS and job.get("rounds") == ROUNDS
                 and job.get("model_sha256") == MODEL)
    return {"execution": {"result": job.get("execution_result"),
                          "rounds_completed": job.get("rounds_completed"), "confirmed": execution_ok},
            "effect": job.get("effect_result"),
            "recovery": {"confirmed": recovered, "state": job.get("recovery_state"),
                         "details": job.get("recovery"),
                         "nodes": {str(node): nodes.get(node) for node in TARGETS}},
            "frozen_parameters_confirmed": frozen_ok,
            "success": execution_ok and recovered and frozen_ok,
            "job": job}


def main():
    """先核对父流程证据，再进行一次启动和只读观测。"""
    args = arguments()
    prerequisites = {name: evidence(getattr(args, name)) for name in
                     ("deployment_evidence", "release_evidence", "heartbeat_evidence", "deployed_argv_json")}
    argv = json.loads(prerequisites["deployed_argv_json"]["text"])
    if (not isinstance(argv, dict) or set(argv) != {"server", "1", "2", "4"}
            or any(not isinstance(value, list) or not value
                   or any(not isinstance(item, str) for item in value) for value in argv.values())):
        raise ValueError("实际部署参数证据必须逐台给出 server、1、2、4 的非空参数数组")
    journal = Journal(args.output)
    web = Web(journal, args.web_url)
    key = "smoke-two-rounds-" + uuid.uuid4().hex
    payload = {"model_sha256": MODEL, "target_nodes": TARGETS, "rounds": ROUNDS}
    journal.record("parameters", arguments=vars(args), invocation=sys.argv,
                   deployed_commit=args.deployed_commit, deployed_argv=argv, prerequisites=prerequisites,
                   prerequisite_authority="父流程证据；脚本不将工作树当实际部署核验",
                   base_url=args.web_url, payload=payload, idempotency_key=key,
                   model_reference={"source": MODEL_SOURCE, "seed": 7, "reference": "官方参考"},
                   preparation_seconds=300, round_seconds=1800, recovery_seconds=120,
                   deadline_seconds=DEADLINE, poll_seconds=POLL)
    started = time.monotonic()
    deadline = started + DEADLINE
    job_id = None
    last_state = None
    last_job = None
    try:
        models = web.request("GET", "/api/v1/models")
        if not models or not any(model.get("sha256") == MODEL for model in models.get("models", [])
                                 if isinstance(model, dict)):
            journal.terminal(status="model_unconfirmed", success=False, parent_decision_required=True)
            return 2
        last_state = web.request("GET", "/api/v1/state")
        if not last_state or not admission(last_state):
            journal.terminal(status="admission_rejected", success=False, state=last_state,
                             parent_decision_required=True)
            return 2
        # 写请求仅一次；响应丢失不能重发，也不能凭猜测认领其他作业。
        result = web.request("POST", "/api/v1/jobs", payload, key=key, timeout=310)
        if result and isinstance(result.get("job_id"), str) and result["job_id"]:
            job_id = result["job_id"]
        journal.record("start_result", result=result, job_id=job_id, retry_allowed=False)
        while time.monotonic() < deadline:
            state = web.request("GET", "/api/v1/state", timeout=min(10, max(0.1, deadline - time.monotonic())))
            if state is not None:
                last_state = state
                # 网络错误只记录管理通道事实，不生成任何客户端离线结论。
                for location in ("current_job", "recent_job"):
                    candidate = state.get(location)
                    if job_id and isinstance(candidate, dict) and candidate.get("job_id") == job_id:
                        last_job = candidate
                        facts = report(state, candidate)
                        journal.record("job_observation", job_id=job_id, location=location, **facts)
                        if (location == "recent_job" and candidate.get("execution_result") in
                                ("succeeded", "failed", "aborted") and candidate.get("recovery_state") in
                                ("ready", "blocked", "unconfirmed")):
                            journal.terminal(status="terminal_observed", job_id=job_id,
                                             parent_decision_required=not facts["success"], **facts)
                            return 0 if facts["success"] else 2
            time.sleep(min(POLL, max(0, deadline - time.monotonic())))
        # 到期仅向已收到启动响应的本作业发一次正常网页终止请求，服务端再次核对活动标识。
        journal.record("deadline_reached", job_id=job_id, deadline_seconds=DEADLINE)
        abort_result = None
        if job_id:
            abort_result = web.request("POST", "/api/v1/jobs/" + urllib.parse.quote(job_id, safe="") + "/abort",
                                       {"reason": "smoke_observation_deadline_4020_seconds"})
            final_state = web.request("GET", "/api/v1/state")
            if final_state is not None:
                last_state = final_state
                for location in ("current_job", "recent_job"):
                    candidate = final_state.get(location)
                    if isinstance(candidate, dict) and candidate.get("job_id") == job_id:
                        last_job = candidate
        facts = report(last_state, last_job)
        facts["success"] = False
        journal.terminal(status="deadline", job_id=job_id, abort_result=abort_result,
                         parent_decision_required=True, **facts)
        return 2
    except (Exception, KeyboardInterrupt) as exc:
        # 异常和人工中断不触发重发或猜测性终止，交由父流程按事实处理。
        journal.terminal(status="observer_interrupted", job_id=job_id, error=repr(exc),
                         parent_decision_required=True, **report(last_state, last_job))
        return 2
    finally:
        journal.file.close()


if __name__ == "__main__":
    sys.exit(main())
