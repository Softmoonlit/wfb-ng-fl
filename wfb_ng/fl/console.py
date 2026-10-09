"""Authoritative console state and operator history, independent of HTTP."""
from copy import deepcopy
from datetime import datetime, timezone
import threading
import uuid
from typing import Any, Callable, Dict, Optional


SERVER_STATES = {
    "INITIALIZING": "starting", "IDLE": "idle", "PREPARING": "preparing",
    "RUNNING": "running", "ABORTING": "aborting", "RADIO_ERROR": "radio_error",
    "STOPPED": "unavailable", "SWITCHING_RADIO": "preparing", "SURVEYING": "preparing",
}
TIME_FIELDS = {"generated_at", "elapsed_ms", "last_heartbeat_time",
               "last_heartbeat_timestamp_ms", "last_heartbeat_ago_seconds"}


def _state_facts(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _state_facts(v) for k, v in value.items() if k not in TIME_FIELDS}
    if isinstance(value, list):
        return [_state_facts(v) for v in value]
    return value


def _job_view(job: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if job is None:
        return None
    fields = {"job_id", "run_id", "model_sha256", "model_size_bytes", "target_nodes", "rounds",
              "started_at", "execution_result", "recovery_state", "error", "reason", "recovery_error"}
    return {k: deepcopy(v) for k, v in job.items() if k in fields}


class ConsoleApplicationService:
    """Project daemon facts into one recoverable, detached Web snapshot."""

    def __init__(self, read_status: Callable[[], Dict[str, Any]], source_lock: Any) -> None:
        self.instance_id = str(uuid.uuid4())
        self._read_status = read_status
        self._source_lock = source_lock
        self._lock = threading.RLock()
        self._version = 0
        self._facts: Any = None
        self._events: list = []
        self._event_sequence = 0
        self._node_online: Dict[int, bool] = {}
        self._web_status: Optional[tuple] = None

    def record_web_status(self, status: str, error: Optional[str]) -> None:
        # Web stop cannot wait on the daemon lock. Keep its facts and event
        # together under the service lock so a concurrent raw read cannot
        # combine an old Web status with the newly recorded change event.
        with self._lock:
            if self._web_status == (status, error):
                return
            self._web_status = (status, error)
            self.record_event({"type": "MANAGEMENT_WEB_CHANGED", "message": status})

    def record_event(self, event: Dict[str, Any]) -> None:
        if event.get("type") == "NODE_HEARTBEAT":
            return
        with self._lock:
            # Daemon and coordinator can report the same start/terminal fact.
            identity = (event.get("type"), event.get("job_id") or event.get("job", {}).get("job_id"),
                        event.get("round_id"))
            if (identity[0] in ("JOB_STARTED", "JOB_COMPLETED", "JOB_FAILED", "JOB_ABORTED")
                    and identity[1] and any(e["identity"] == identity for e in self._events)):
                return
            self._event_sequence += 1
            self._events.append({"identity": identity, "event": {
                "sequence": self._event_sequence,
                "type": event.get("type", "STATE_CHANGED"),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "job_id": identity[1], "round_id": identity[2], "node_id": event.get("node_id"),
                "message": event.get("message") or event.get("reason") or event.get("error") or event.get("type"),
            }})
            del self._events[:-100]

    def snapshot(self) -> Dict[str, Any]:
        # All publishers use daemon -> service order. Reading and publishing
        # remain atomic, including concurrent HTTP requests and heartbeats.
        with self._source_lock:
            return self._snapshot_locked()

    def _snapshot_locked(self) -> Dict[str, Any]:
        raw = deepcopy(self._read_status())
        with self._lock:
            if self._web_status is not None:
                raw["management_web"]["status"], raw["management_web"]["error"] = self._web_status
            nodes = []
            for node in raw["nodes"].values():
                node["state"] = ("offline" if node["readiness"] == "OFFLINE" else
                                 "error" if node["error_code"] else
                                 (node["reported_state"] or "HUNTING").lower())
                node["online"] = node["readiness"] != "OFFLINE"
                if self._node_online.get(node["node_id"]) is True and not node["online"]:
                    self.record_event({"type": "NODE_OFFLINE", "node_id": node["node_id"],
                                       "message": f"节点 {node['node_id']} 离线"})
                self._node_online[node["node_id"]] = node["online"]
                nodes.append(node)
            blockers = []
            if raw["server_state"] != "IDLE":
                blockers.append({"code": "SERVER_NOT_IDLE", "message": "Server 尚未待命"})
            if raw["radio"] is None:
                blockers.append({"code": "RADIO_NOT_READY", "message": "射频配置尚未确认"})
            if raw["link_required"]:
                if not raw["tun"]["is_active"]:
                    blockers.append({"code": "TUN_NOT_READY", "message": "TUN 尚未就绪"})
                if not raw["link_process"]["running"]:
                    blockers.append({"code": "LINK_NOT_READY", "message": "无线链路进程尚未就绪"})
            current = _job_view(raw["active_job"])
            recent = _job_view(raw["recent_job"])
            if current:
                current["execution_result"] = "running"
                current["recovery_state"] = "not_required"
                blockers.append({"code": "JOB_RUNNING", "message": "作业正在运行"})
            if recent and recent["recovery_state"] != "ready":
                blockers.append({"code": "JOB_RESOURCE_NOT_READY", "message": "上一项作业资源尚未恢复"})
            if not any(n["state"] == "idle" and n["readiness"] == "READY" for n in nodes):
                blockers.append({"code": "NODES_NOT_READY", "message": "没有待命且就绪的节点"})
            state = {
                "schema_version": 1, "instance_id": self.instance_id,
                "server": {"state": SERVER_STATES[raw["server_state"]],
                           "management_web": raw["management_web"], "radio": raw["radio"],
                           "can_start_job": not blockers, "start_blockers": blockers,
                           "tun": raw["tun"], "link_process": raw["link_process"],
                           "air_interface": raw["air_interface"]},
                "nodes": nodes, "job": current or recent,
                "current_job": current, "recent_job": recent,
                "execution_result": (current or recent or {}).get("execution_result", "none"),
                "recovery_state": (current or recent or {}).get("recovery_state", "not_required"),
                "events": [entry["event"] for entry in self._events],
            }
            facts = (_state_facts(state), raw["server_state"])
            if facts != self._facts:
                self._version += 1
                self._facts = deepcopy(facts)
            state["state_version"] = self._version
            state["generated_at"] = datetime.now(timezone.utc).isoformat()
            return deepcopy(state)
