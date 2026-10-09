"""Authoritative console state and operator history, independent of HTTP."""
from copy import deepcopy
from datetime import datetime, timezone
import threading
import uuid
import time
from pathlib import Path
from typing import Any, BinaryIO, Callable, Dict, Optional

from .errors import FLRuntimeError
from .model_library import ModelLibrary, ModelLibraryError
from .radio import ALLOWED_PATCH_KEYS, ALLOWED_MCS_VALUES, get_downlink_rate_bounds, validate_radio_config


class RadioPreparationError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


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
              "started_at", "execution_result", "recovery_state", "error", "reason", "recovery_error",
              "current_round", "rounds_completed", "server_phase", "round_started_at"}
    return {k: deepcopy(v) for k, v in job.items() if k in fields}


class ConsoleApplicationService:
    """Project daemon facts into one recoverable, detached Web snapshot."""

    def __init__(self, read_status: Callable[[], Dict[str, Any]], source_lock: Any,
                 model_root: Optional[Path] = None,
                 apply_radio: Optional[Callable[[str, bool], Dict[str, Any]]] = None,
                 start_job: Optional[Callable[[Dict[str, Any], str], Dict[str, Any]]] = None) -> None:
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
        self.models = ModelLibrary(model_root or Path('/var/lib/wfb-ng-fl/models'))
        self._apply_radio = apply_radio
        self._start_job = start_job
        self._job_requests: Dict[str, tuple] = {}
        self._radio_confirmation: Optional[Dict[str, Any]] = None

    def start_job(self, body: Any, idempotency_key: Optional[str]) -> Dict[str, Any]:
        if self._start_job is None:
            raise FLRuntimeError('JOB_UNAVAILABLE', '作业服务不可用')
        if not isinstance(idempotency_key, str) or not (1 <= len(idempotency_key) <= 200):
            raise FLRuntimeError('INVALID_JOB_REQUEST', '必须提供有效的 Idempotency-Key')
        if not isinstance(body, dict) or set(body) != {'model_sha256', 'target_nodes', 'rounds'}:
            raise FLRuntimeError('INVALID_JOB_REQUEST', '只允许 model_sha256、target_nodes、rounds')
        digest = body['model_sha256']
        try:
            ModelLibrary.validate_digest(digest)
        except (ModelLibraryError, TypeError) as exc:
            raise FLRuntimeError('INVALID_JOB_REQUEST', str(exc)) from exc
        nodes = body['target_nodes']
        rounds = body['rounds']
        if (not isinstance(nodes, list) or not nodes
                or any(type(node) is not int or not 1 <= node <= 10 for node in nodes)
                or len(set(nodes)) != len(nodes)
                or type(rounds) is not int or rounds <= 0):
            raise FLRuntimeError('INVALID_JOB_REQUEST', 'target_nodes 必须为固定不重复节点集合，rounds 必须为正整数')
        canonical = (digest, tuple(nodes), rounds)
        with self._source_lock:
            previous = self._job_requests.get(idempotency_key)
            if previous is not None:
                if previous[0] != canonical:
                    raise FLRuntimeError('IDEMPOTENCY_KEY_REUSED', 'Idempotency-Key 已用于不同请求', details={})
                return deepcopy(previous[1])
            try:
                artifact = self.models.artifact_path(digest)
                size_bytes = artifact.stat().st_size
            except ModelLibraryError:
                raise
            except OSError as exc:
                raise ModelLibraryError('MODEL_NOT_FOUND', '模型不存在', 404) from exc
            payload = {'model_sha256': digest, 'target_nodes': list(nodes), 'rounds': rounds,
                       'model_path': str(artifact), 'model_size_bytes': size_bytes}
            result = self._start_job(payload, idempotency_key)
            self._job_requests[idempotency_key] = (canonical, deepcopy(result))
            return result

    def radio_configuration(self) -> Dict[str, Any]:
        state = self.snapshot()
        return {'config': state['server']['radio'], 'snapshot': state,
                'rate_bounds': {str(mcs): get_downlink_rate_bounds(mcs).as_dict()
                                for mcs in ALLOWED_MCS_VALUES}}


    def _radio_targets(self, state: Dict[str, Any]) -> list:
        blockers = state['server']['start_blockers']
        targets = [node for node in state['nodes'] if node['reported_state'] is not None]
        if blockers or not targets or any(
                node['state'] != 'idle' or node['readiness'] != 'READY'
                or node['error_code'] or node['last_heartbeat_ago_seconds'] is None
                or node['last_heartbeat_ago_seconds'] > 10 for node in targets):
            raise RadioPreparationError('RADIO_NOT_READY', '集群必须空闲，节点与链路资源必须就绪', 409)
        return sorted(node['node_id'] for node in targets)

    def validate_radio_configuration(self, body: Any) -> Dict[str, Any]:
        if (not isinstance(body, dict) or set(body) != {'config'}
                or not isinstance(body['config'], dict) or set(body['config']) != ALLOWED_PATCH_KEYS
                or any(type(value) is not int for value in body['config'].values())):
            raise RadioPreparationError('INVALID_RADIO_CONFIG', '必须提供完整的五项扁平射频参数')
        try:
            config = validate_radio_config(body['config'])
        except (TypeError, ValueError) as exc:
            raise RadioPreparationError('INVALID_RADIO_CONFIG', str(exc)) from exc
        bounds = get_downlink_rate_bounds(config.downlink_mcs)
        warning = (None if bounds.min_rate_kbps <= config.uftp_rate_kbps <= bounds.max_rate_kbps
                   else 'UFTP 速率超出安全范围，可能导致队列丢包或传输停滞；必须明确确认风险')
        with self._source_lock:
            state = self.snapshot()
            targets = self._radio_targets(state)
            patch = {key: value for key, value in config.to_dict().items()
                     if key in ALLOWED_PATCH_KEYS and value != state['server']['radio'][key]}
            if not patch:
                raise RadioPreparationError('RADIO_CONFIG_UNCHANGED', '配置未变化，无需应用')
            confirmation = {'token': str(uuid.uuid4()), 'instance_id': self.instance_id,
                            'state_version': state['state_version'], 'expires_in_seconds': 60}
            self._radio_confirmation = {'confirmation': confirmation, 'expires_at': time.monotonic() + 60,
                                        'patch': patch, 'targets': targets, 'warning': warning}
            return {'config': config.to_dict(), 'rate_bounds': bounds.as_dict(),
                    'warning': warning, 'confirmation': deepcopy(confirmation)}

    def consume_radio_confirmation(self, token: str, confirm_risk: bool) -> Dict[str, Any]:
        """Daemon calls under its admission lock, immediately before reserving radio ownership."""
        with self._source_lock:
            context = self._radio_confirmation
            state = self.snapshot()
            if (context is None or context['confirmation']['token'] != token
                    or context['confirmation']['instance_id'] != self.instance_id
                    or context['confirmation']['state_version'] != state['state_version']
                    or time.monotonic() >= context['expires_at']):
                raise RadioPreparationError('RADIO_CONFIRMATION_EXPIRED', '确认已失效，请重新校验配置', 409)
            targets = self._radio_targets(state)
            if targets != context['targets']:
                raise RadioPreparationError('RADIO_CONFIRMATION_EXPIRED', '目标节点已变化，请重新校验配置', 409)
            if context['warning'] and not confirm_risk:
                raise RadioPreparationError('RADIO_RISK_CONFIRMATION_REQUIRED', context['warning'], 409)
            self._radio_confirmation = None
            return {'confirmed': True, 'patch': deepcopy(context['patch']), 'target_nodes': targets}

    def apply_radio_configuration(self, body: Any) -> Dict[str, Any]:
        if (not isinstance(body, dict) or set(body) != {'confirmation_token', 'confirm_risk'}
                or not isinstance(body['confirmation_token'], str) or type(body['confirm_risk']) is not bool):
            raise RadioPreparationError('INVALID_RADIO_REQUEST', '必须提供确认令牌和布尔风险确认')
        if self._apply_radio is None:
            raise RadioPreparationError('RADIO_UNAVAILABLE', '射频服务不可用', 503)
        return self._apply_radio(body['confirmation_token'], body['confirm_risk'])


    def _referenced_models(self) -> set:
        raw = self._read_status()
        jobs = [raw.get('active_job')]
        recent = raw.get('recent_job')
        if recent and recent.get('recovery_state') != 'ready':
            jobs.append(recent)
        return {job['model_sha256'] for job in jobs if job and job.get('model_sha256')}

    def list_models(self) -> Dict[str, Any]:
        # Startup integrity checking can read a large library. Do it before
        # taking the daemon lock so heartbeats and lifecycle work continue.
        models = self.models.list_models()
        with self._source_lock:
            references = self._referenced_models()
            for model in models:
                model['referenced'] = model['referenced'] or model['sha256'] in references
            return {'models': models}

    def upload_model(self, stream: BinaryIO, size: int, filename: str) -> Dict[str, Any]:
        result = self.models.upload(stream, size, filename)
        with self._source_lock:
            result['model']['referenced'] |= result['model']['sha256'] in self._referenced_models()
        return result

    def delete_model(self, sha256: str) -> Dict[str, bool]:
        self.models.validate_digest(sha256)
        self.models.list_models()
        with self._source_lock:
            if sha256 in self._referenced_models():
                raise ModelLibraryError('MODEL_REFERENCED', '模型正在被作业引用，不能删除', 409)
            self.models.delete(sha256)
            return {'deleted': True}

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
