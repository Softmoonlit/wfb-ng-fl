"""Authoritative console state and operator history, independent of HTTP."""
from copy import deepcopy
from datetime import datetime, timezone
import math
import queue
import threading
import uuid
import time
from pathlib import Path
from typing import Any, BinaryIO, Callable, Dict, Optional

from .errors import FLRuntimeError
from .model_library import ModelLibrary, ModelLibraryError
from .mnist_deployment import get_mnist_deployment
from .radio import (ALLOWED_5GHZ_CHANNELS, ALLOWED_BANDWIDTH_MHZ, ALLOWED_MCS_VALUES,
                    ALLOWED_PATCH_KEYS, FIXED_FEC_K, FIXED_FEC_N, TXPOWER_MAX_DBM, TXPOWER_MIN_DBM,
                    get_downlink_rate_bounds, validate_radio_config)


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


def _finite_number(value: Any) -> bool:
    return _finite_float(value) is not None


def _finite_float(value: Any) -> Optional[float]:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _finite_metrics(value: Any) -> Optional[Dict[str, Any]]:
    """逐指标投影有限数值；缺失项不抹除其他已取得指标，合法零值保留。"""
    if not isinstance(value, dict) or not value:
        return None
    if any(not isinstance(key, str) for key in value):
        return None
    metrics = {key: deepcopy(item) for key, item in value.items() if _finite_number(item)}
    return metrics or None


def _sha256(value: Any) -> Optional[str]:
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in '0123456789abcdefABCDEF' for char in value)):
        return None
    return value.lower()


def _positive_int(value: Any) -> Optional[int]:
    if type(value) is not int or value <= 0:
        return None
    return value


_RADIO_RESULT_CONFIG_FIELDS = {
    'channel', 'bandwidth_mhz', 'radio_txpower_dbm', 'downlink_mcs',
    'uplink_mcs', 'uftp_rate_kbps', 'fec_k', 'fec_n',
    'channel_width', 'guard_interval',
}
_RADIO_RESULT_CHANNEL_WIDTHS = {'HT20', 'HT40+', 'HT40-'}
_RADIO_RESULT_GUARD_INTERVALS = {'long'}
_RADIO_RESULT_JS_SAFE_MAX = 2 ** 53 - 1
_RADIO_RESULT_TEXT_MAX = 128
_RADIO_RESULT_REASON_MAX_BYTES = 512


def _bounded_utf8(value: Any, max_bytes: int) -> Optional[str]:
    if not isinstance(value, str) or not value or len(value.encode('utf-8')) > max_bytes:
        return None
    return value


def _radio_safe_int(value: Any, minimum: int, maximum: int) -> Optional[int]:
    if (type(value) is not int or not -_RADIO_RESULT_JS_SAFE_MAX <= value <= _RADIO_RESULT_JS_SAFE_MAX
            or value < minimum or value > maximum):
        return None
    return value


def _project_radio_config(value: Any) -> Optional[Dict[str, Any]]:
    """只投影已知射频配置键，不把事务内部任意字典送入 Web。"""
    if not isinstance(value, dict):
        return None
    result: Dict[str, Any] = {}
    for key in _RADIO_RESULT_CONFIG_FIELDS:
        if key not in value:
            continue
        item = value[key]
        projected: Any = None
        if key == 'channel':
            projected = item if type(item) is int and item in ALLOWED_5GHZ_CHANNELS else None
        elif key == 'bandwidth_mhz':
            projected = item if type(item) is int and item in ALLOWED_BANDWIDTH_MHZ else None
        elif key == 'radio_txpower_dbm':
            projected = _radio_safe_int(item, TXPOWER_MIN_DBM, TXPOWER_MAX_DBM)
        elif key in ('downlink_mcs', 'uplink_mcs'):
            # 10 的结果允许 MCS 0；生产入口的组合校验仍由 radio.py 负责。
            projected = _radio_safe_int(item, 0, 6)
        elif key == 'uftp_rate_kbps':
            projected = _radio_safe_int(item, 1, _RADIO_RESULT_JS_SAFE_MAX)
        elif key == 'fec_k':
            projected = item if type(item) is int and item == FIXED_FEC_K else None
        elif key == 'fec_n':
            projected = item if type(item) is int and item == FIXED_FEC_N else None
        elif key == 'channel_width':
            projected = item if isinstance(item, str) and item in _RADIO_RESULT_CHANNEL_WIDTHS else None
        elif key == 'guard_interval':
            projected = item if isinstance(item, str) and item in _RADIO_RESULT_GUARD_INTERVALS else None
        else:
            projected = None
        if projected is None:
            return None
        result[key] = projected
    return result or None


def _project_node_list(value: Any) -> list:
    if not isinstance(value, list):
        return []
    return sorted({item for item in value
                   if type(item) is int and 1 <= item <= 255
                   and item <= _RADIO_RESULT_JS_SAFE_MAX})


def _project_radio_result(value: Any, job_id: Any) -> Optional[Dict[str, Any]]:
    """投影 10 的有限事务事实；effective_config=None 表示未知，不能替换。"""
    if not isinstance(value, dict) or not isinstance(job_id, str) or value.get('job_id') != job_id:
        return None
    request_id = _bounded_utf8(value.get('request_id'), _RADIO_RESULT_TEXT_MAX)
    if request_id is None:
        return None
    round_index = _radio_safe_int(value.get('round_index'), 0, _RADIO_RESULT_JS_SAFE_MAX)
    if round_index is None:
        return None
    phase = _bounded_utf8(value.get('phase'), _RADIO_RESULT_TEXT_MAX)
    transaction_state = _bounded_utf8(value.get('transaction_state'), _RADIO_RESULT_TEXT_MAX)
    recovery_state = _bounded_utf8(value.get('recovery_state'), _RADIO_RESULT_TEXT_MAX)
    error_code = _bounded_utf8(value.get('error_code'), _RADIO_RESULT_TEXT_MAX)
    reason = _bounded_utf8(value.get('reason'), _RADIO_RESULT_REASON_MAX_BYTES)
    result: Dict[str, Any] = {
        'request_id': request_id,
        'job_id': job_id,
        'round_index': round_index,
        'phase': phase,
        'transaction_state': transaction_state,
        'before_config': _project_radio_config(value.get('before_config')),
        'requested_patch': _project_radio_config(value.get('requested_patch')),
        'target_config': _project_radio_config(value.get('target_config')),
        'target_nodes': _project_node_list(value.get('target_nodes')),
        'confirmed_nodes': _project_node_list(value.get('confirmed_nodes')),
        'unconfirmed_nodes': _project_node_list(value.get('unconfirmed_nodes')),
        'effective_config': (_project_radio_config(value.get('effective_config'))
                             if value.get('effective_config') is not None else None),
        'recovery_state': recovery_state,
        'error_code': error_code,
        'reason': reason,
    }
    return result


def _project_radio_results(value: Any, job_id: Any) -> list:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        projected = _project_radio_result(item, job_id)
        if projected is not None:
            result.append(projected)
    return result


def _project_device(value: Any) -> Optional[Dict[str, str]]:
    if not isinstance(value, dict):
        return None
    result = {}
    device_type = value.get('type')
    device_name = value.get('name')
    if (not isinstance(device_type, str) or device_type not in ('cpu', 'cuda')
            or not isinstance(device_name, str) or not 0 < len(device_name) <= 128):
        return None
    result['type'] = device_type
    result['name'] = device_name
    for key in ('torch_version', 'cuda_version'):
        item = value.get(key)
        if isinstance(item, str) and 0 < len(item) <= 128:
            result[key] = item
    return result


def _project_preparation(value: Any) -> Optional[Dict[str, Any]]:
    """准备报告只保留页面需要的事实，不暴露本机路径或原始对象。"""
    if not isinstance(value, dict):
        return None
    result: Dict[str, Any] = {}
    if isinstance(value.get('model_format'), str):
        result['model_format'] = value['model_format']
    sample_count = _positive_int(value.get('sample_count'))
    if sample_count is not None:
        result['sample_count'] = sample_count
    data_seed = _positive_int(value.get('data_seed'))
    if data_seed is not None:
        result['data_seed'] = data_seed
    if type(value.get('ready')) is bool:
        result['ready'] = value['ready']
    device = _project_device(value.get('device'))
    if device is not None:
        result['device'] = device
    digests = value.get('data_sha256')
    if isinstance(digests, dict):
        projected = {key: digest for key, digest in digests.items()
                     if isinstance(key, str) and _sha256(digest) is not None}
        if projected:
            result['data_sha256'] = projected
    for key in ('error_code', 'reason', 'error'):
        if isinstance(value.get(key), str) and value[key]:
            result[key] = value[key]
    return result or None


def _project_training(value: Any, node_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """投影参考算法已由宿主验证的单节点训练 metadata。"""
    if not isinstance(value, dict):
        return None
    result: Dict[str, Any] = {}
    actual_node = value.get('node_id', node_id)
    if type(actual_node) is int and actual_node > 0:
        result['node_id'] = actual_node
    if isinstance(value.get('model_format'), str) and value['model_format']:
        result['model_format'] = value['model_format']
    round_index = _positive_int(value.get('round_index'))
    if round_index is not None:
        result['round_index'] = round_index
    base_sha = _sha256(value.get('base_model_sha256'))
    if base_sha is not None:
        result['base_model_sha256'] = base_sha
    update_sha = _sha256(value.get('update_sha256'))
    if update_sha is not None:
        result['update_sha256'] = update_sha
    update_size = _positive_int(value.get('update_size_bytes'))
    if update_size is not None:
        result['update_size_bytes'] = update_size
    for key in ('sample_count', 'optimizer_steps'):
        number = _positive_int(value.get(key))
        if number is not None:
            result[key] = number
    loss = value.get('train_loss')
    loss_number = _finite_float(loss)
    if loss_number is not None and loss_number >= 0:
        result['train_loss'] = loss
    device = _project_device(value.get('device'))
    if device is not None:
        result['device'] = device
    if isinstance(value.get('metadata_status'), str):
        result['metadata_status'] = value['metadata_status']
    for key in ('error_code', 'reason', 'error'):
        if isinstance(value.get(key), str) and value[key]:
            result[key] = value[key]
    return result or None


def _project_training_entries(value: Any) -> list:
    entries: list = []
    if isinstance(value, dict):
        source: Any = value.items()
    elif isinstance(value, list):
        source = ((item.get('node_id') if isinstance(item, dict) else None, item)
                  for item in value)
    else:
        return entries
    for raw_node, fact in source:
        try:
            node_id = int(raw_node)
        except (TypeError, ValueError):
            node_id = None
        if node_id is None or node_id <= 0:
            continue
        projected = _project_training(fact, node_id)
        if projected is not None:
            projected['node_id'] = node_id
            entries.append(projected)
    return sorted(entries, key=lambda item: item['node_id'])


def _project_effect(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    result: Dict[str, Any] = {}
    round_index = _positive_int(value.get('round_index'))
    if round_index is not None:
        result['round_index'] = round_index
    metrics = _finite_metrics(value.get('metrics'))
    if metrics is not None:
        result['metrics'] = metrics
    if type(value.get('verdict')) is bool:
        result['verdict'] = value['verdict']
    else:
        result['verdict'] = None
    if isinstance(value.get('reason'), str) and value['reason']:
        result['reason'] = value['reason']
    else:
        result['reason'] = None
    return result


def _project_round(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    result: Dict[str, Any] = {}
    round_index = _positive_int(value.get('round_index'))
    if round_index is None:
        return None
    result['round_index'] = round_index
    for key in ('input_model_sha256', 'output_model_sha256'):
        digest = _sha256(value.get(key))
        if digest is not None:
            result[key] = digest
    for key in ('input_model_size_bytes', 'output_model_size_bytes'):
        size = _positive_int(value.get(key))
        if size is not None:
            result[key] = size
    metrics = _finite_metrics(value.get('metrics'))
    result['metrics'] = metrics
    if type(value.get('verdict')) is bool:
        result['verdict'] = value['verdict']
    else:
        result['verdict'] = None
    result['reason'] = value.get('reason') if isinstance(value.get('reason'), str) else None
    for key in ('committed_nodes', 'dropped_out_nodes'):
        values = value.get(key)
        result[key] = sorted(item for item in values if type(item) is int and item > 0) if isinstance(values, list) else []
    result['client_submission_returns_received'] = value.get('client_submission_returns_received') is True
    result['training'] = _project_training_entries(value.get('training'))
    return result


def _project_rounds(job: Dict[str, Any], summary: Dict[str, Any]) -> list:
    raw_rounds = summary.get('rounds') if isinstance(summary.get('rounds'), list) else job.get('round_results')
    if not isinstance(raw_rounds, list):
        return []
    result: list = []
    for raw_round in raw_rounds:
        projected = _project_round(raw_round)
        if projected is not None:
            result.append(projected)
    return sorted(result, key=lambda item: item['round_index'])


def _project_recovery_report(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    candidate = value.get('report')
    report: Dict[str, Any] = candidate if isinstance(candidate, dict) else value
    result: Dict[str, Any] = {}
    epoch = _positive_int(report.get('recovery_epoch'))
    if epoch is not None:
        result['recovery_epoch'] = epoch
    if isinstance(report.get('recovery_state'), str):
        result['recovery_state'] = report['recovery_state']
    for key in ('error_code', 'reason', 'error'):
        if isinstance(report.get(key), str) and report[key]:
            result[key] = report[key]
    return result or None


def _project_recovery_reports(value: Any) -> list:
    if not isinstance(value, dict):
        return []
    result: list = []
    for raw_node, envelope in value.items():
        try:
            node_id = int(raw_node)
        except (TypeError, ValueError):
            continue
        if node_id <= 0:
            continue
        report = _project_recovery_report(envelope)
        if report is None:
            continue
        item: Dict[str, Any] = {'node_id': node_id, 'report': report}
        if isinstance(envelope, dict) and isinstance(envelope.get('report_id'), str):
            item['report_id'] = envelope['report_id']
        result.append(item)
    return sorted(result, key=lambda item: item['node_id'])


def _project_diagnostic(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    result: Dict[str, Any] = {}
    for key in ('node_id', 'round_index'):
        number = value.get(key)
        if type(number) is int and number > 0:
            result[key] = number
    for key in ('phase', 'error_code', 'reason', 'error', 'message', 'state'):
        if isinstance(value.get(key), str) and value[key]:
            result[key] = value[key]
    return result or None


def _project_algorithm(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    result: Dict[str, Any] = {}
    client_entry = value.get('client_entry')
    for key in ('client_entry', 'aggregate_entry', 'evaluate_entry', 'radio_policy_entry'):
        if isinstance(value.get(key), str) and value[key]:
            result[key] = value[key]
    # 参考 MNIST 只允许页面看到已冻结的无路径 data_seed；未知算法不暴露 config。
    config = value.get('config')
    deployment = get_mnist_deployment(
        client_entry, value.get('aggregate_entry'), value.get('evaluate_entry'))
    if (deployment is not None
            and isinstance(config, dict)
            and type(config.get('data_seed')) is int
            and 0 < config['data_seed'] < 2 ** 63):
        result['config'] = {'data_seed': config['data_seed']}
    return result or None


def _job_view(job: Optional[Dict[str, Any]], nodes: Dict[int, Dict[str, Any]],
              disconnections: Dict[str, list]) -> Optional[Dict[str, Any]]:
    if job is None:
        return None
    fields = {"job_id", "run_id", "model_sha256", "model_size_bytes", "target_nodes", "rounds",
              "started_at", "preparation_timeout_seconds", "round_timeout_seconds", "io_timeout_seconds",
              "execution_result", "recovery_state", "error", "reason", "recovery_error",
              "current_round", "rounds_completed", "server_phase", "round_started_at", "phase_started_at"}
    view = {k: deepcopy(v) for k, v in job.items() if k in fields}
    target_nodes = view.get("target_nodes", [])
    view["target_nodes"] = target_nodes
    summary_value = job.get('summary')
    summary: Dict[str, Any] = summary_value if isinstance(summary_value, dict) else {}
    initial_metrics = _finite_metrics(summary.get('initial_metrics'))
    if initial_metrics is None:
        initial_metrics = _finite_metrics(job.get('initial_metrics'))
    rounds = _project_rounds(job, summary)
    effect = _project_effect(summary.get('effect_result'))
    if effect is None:
        effect = _project_effect(job.get('effect_result'))
    final_model_sha256 = _sha256(summary.get('final_model_sha256'))
    final_model_size = _positive_int(summary.get('final_model_size_bytes'))
    view['algorithm'] = _project_algorithm(job.get('algorithm'))
    view['server_preparation'] = _project_preparation(job.get('server_preparation'))
    view['initial_metrics'] = initial_metrics
    view['effect_result'] = effect
    view['evaluation'] = {
        'initial_metrics': initial_metrics,
        'rounds': rounds,
        'effect_result': effect,
        'final_model_sha256': final_model_sha256,
        'final_model_size_bytes': final_model_size,
    }
    view['radio_results'] = _project_radio_results(job.get('radio_results'), job.get('job_id'))
    prep_reports = job.get('client_preparation_reports')
    preparation_by_node: Dict[int, Any] = {}
    if isinstance(prep_reports, dict):
        for raw_node, report in prep_reports.items():
            try:
                node_id = int(raw_node)
            except (TypeError, ValueError):
                continue
            if node_id > 0:
                preparation_by_node[node_id] = _project_preparation(report)
    training_by_node: Dict[int, list] = {}
    for round_view in rounds:
        for training in round_view['training']:
            node_id = training['node_id']
            training_by_node.setdefault(node_id, []).append(deepcopy(training))
    participant_fields = ("node_id", "online", "state", "readiness", "reported_state",
                          "error_code", "can_select", "selection_blockers")
    view["participants"] = []
    job_id = job.get("job_id")
    job_disconnections = disconnections.get(job_id, []) if isinstance(job_id, str) else []
    for node_id in target_nodes:
        disconnection_events = [
            deepcopy(event) for event in job_disconnections
            if event.get("node_id") == node_id
        ]
        node = nodes.get(node_id)
        if node is None:
            participant = {
                "node_id": node_id,
                "online": False,
                "state": "offline",
                "readiness": "OFFLINE",
                "reported_state": None,
                "error_code": None,
                "can_select": False,
                "selection_blockers": [{"code": "NODE_NOT_IN_SNAPSHOT", "message": "节点不在当前状态快照"}],
                "disconnection_events": disconnection_events,
            }
        else:
            participant = {key: deepcopy(node[key]) for key in participant_fields}
            participant["disconnection_events"] = disconnection_events
        participant['preparation'] = deepcopy(preparation_by_node.get(node_id))
        participant['training'] = deepcopy(training_by_node.get(node_id, []))
        view["participants"].append(participant)
    recovery_state = job.get('recovery_state') if isinstance(job.get('recovery_state'), str) else 'not_required'
    recovery_missing_nodes = sorted(node for node in job.get('recovery_missing_nodes', [])
                                    if type(node) is int and node > 0) if isinstance(job.get('recovery_missing_nodes'), list) else []
    recovery_reports = _project_recovery_reports(job.get('recovery_reports'))
    additional_errors = [_project_diagnostic(item) for item in job.get('additional_errors', [])
                         if _project_diagnostic(item) is not None] if isinstance(job.get('additional_errors'), list) else []
    first_failure = _project_diagnostic(job.get('first_failure'))
    liveness_events = [_project_diagnostic(item) for item in job.get('liveness_events', [])
                       if _project_diagnostic(item) is not None] if isinstance(job.get('liveness_events'), list) else []
    server_recovery_complete = job.get('server_recovery_complete') if type(job.get('server_recovery_complete')) is bool else None
    view['recovery_missing_nodes'] = recovery_missing_nodes
    view['recovery_reports'] = recovery_reports
    view['additional_errors'] = additional_errors
    view['first_failure'] = first_failure
    view['liveness_events'] = liveness_events
    view['client_error'] = _project_diagnostic(job.get('client_error'))
    view['recovery_wait_finished'] = job.get('recovery_wait_finished') if type(job.get('recovery_wait_finished')) is bool else None
    view['server_recovery_complete'] = server_recovery_complete
    view['recovery'] = {
        'state': recovery_state,
        'recovery_state': recovery_state,
        'missing_nodes': recovery_missing_nodes,
        'reports': recovery_reports,
        'server_recovery_complete': server_recovery_complete,
        'additional_errors': additional_errors,
        'first_failure': first_failure,
        'liveness_events': liveness_events,
    }
    return view


class ConsoleApplicationService:
    """Project daemon facts into one recoverable, detached Web snapshot."""

    def __init__(self, read_status: Callable[[], Dict[str, Any]], source_lock: Any,
                 model_root: Optional[Path] = None,
                 apply_radio: Optional[Callable[[str, bool], Dict[str, Any]]] = None,
                 start_job: Optional[Callable[[Dict[str, Any], str], Dict[str, Any]]] = None,
                 abort_job: Optional[Callable[[str, str], Dict[str, Any]]] = None) -> None:
        self.instance_id = str(uuid.uuid4())
        self._read_status = read_status
        self._source_lock = source_lock
        self._lock = threading.RLock()
        self._version = 0
        self._facts: Any = None
        self._events: list = []
        self._job_disconnections: Dict[str, list] = {}
        self._event_sequence = 0
        self._node_online: Dict[int, bool] = {}
        self._web_status: Optional[tuple] = None
        self.models = ModelLibrary(model_root or Path('/var/lib/wfb-ng-fl/models'))
        self._apply_radio = apply_radio
        self._start_job = start_job
        self._abort_job = abort_job
        self._job_requests: Dict[str, tuple] = {}
        self._job_request_lock = threading.Lock()
        self._radio_confirmation: Optional[Dict[str, Any]] = None
        self._stream_subscribers: list = []
        self._initializing_stream = False

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
        # 幂等请求单独串行，不能让准备屏障占用守护进程状态锁。
        with self._job_request_lock:
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

    def abort_job(self, job_id: str, reason: str) -> Dict[str, Any]:
        if self._abort_job is None:
            raise FLRuntimeError('JOB_UNAVAILABLE', '作业服务不可用')
        if not isinstance(job_id, str) or not job_id:
            raise FLRuntimeError('INVALID_ABORT_REQUEST', '作业标识无效')
        if not isinstance(reason, str) or not 1 <= len(reason) <= 200:
            raise FLRuntimeError('INVALID_ABORT_REQUEST', '急停原因无效')
        state = self.snapshot()
        if not state['current_job'] or state['current_job']['job_id'] != job_id:
            raise FLRuntimeError('JOB_NOT_ACTIVE', '目标作业不处于运行中', details={})
        return self._abort_job(job_id, reason)

    def subscribe_events(self) -> queue.Queue:
        subscriber: queue.Queue = queue.Queue(maxsize=100)
        with self._lock:
            self._stream_subscribers.append(subscriber)
        return subscriber

    def unsubscribe_events(self, subscriber: queue.Queue) -> None:
        with self._lock:
            if subscriber in self._stream_subscribers:
                self._stream_subscribers.remove(subscriber)

    def _publish_stream_frame_locked(self, frame: Dict[str, Any]) -> None:
        for subscriber in list(self._stream_subscribers):
            try:
                subscriber.put_nowait(frame)
            except queue.Full:
                # A slow browser must not retain unbounded state or block the daemon.
                while True:
                    try:
                        subscriber.get_nowait()
                    except queue.Empty:
                        break
                subscriber.put_nowait({'kind': 'overflow'})
                self._stream_subscribers.remove(subscriber)

    def initial_stream_frame(self, last_event_id: Optional[int]) -> Dict[str, Any]:
        # Keep the daemon -> console lock order and hold both locks until the
        # initial frame receives its sequence number. No earlier frame can queue.
        with self._source_lock:
            with self._lock:
                self._initializing_stream = True
                try:
                    state = self._snapshot_locked()
                    missed = last_event_id is not None and last_event_id < self._event_sequence
                    self._event_sequence += 1
                    return {'id': self._event_sequence, 'kind': 'snapshot', 'snapshot': state,
                            'resync': missed}
                finally:
                    self._initializing_stream = False


    def radio_configuration(self) -> Dict[str, Any]:
        state = self.snapshot()
        # 缺少实测表的组合显式置空，页面不能借用其它带宽的数据。
        rate_bounds: Dict[str, Dict[str, Optional[Dict[str, object]]]] = {}
        for bandwidth in ALLOWED_BANDWIDTH_MHZ:
            rate_bounds[str(bandwidth)] = {}
            for mcs in ALLOWED_MCS_VALUES:
                try:
                    bounds = get_downlink_rate_bounds(mcs, bandwidth).as_dict()
                except ValueError:
                    bounds = None
                rate_bounds[str(bandwidth)][str(mcs)] = bounds
        return {'config': state['server']['radio'], 'snapshot': state,
                'rate_bounds': rate_bounds}


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
            raise RadioPreparationError('INVALID_RADIO_CONFIG', '必须提供完整的六项扁平射频参数')
        try:
            config = validate_radio_config(body['config'])
            bounds = get_downlink_rate_bounds(config.downlink_mcs, config.bandwidth_mhz)
        except (TypeError, ValueError) as exc:
            raise RadioPreparationError('INVALID_RADIO_CONFIG', str(exc)) from exc
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
                                        'patch': patch, 'targets': targets}
            return {'config': config.to_dict(), 'rate_bounds': bounds.as_dict(),
                    'confirmation': deepcopy(confirmation)}

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
            msg = f"{status}: {error}" if error else status
            self.record_event({"type": "MANAGEMENT_WEB_CHANGED", "message": msg, "error": error})

    def record_event(self, event: Dict[str, Any]) -> None:
        if event.get("type") == "NODE_HEARTBEAT":
            return
        with self._lock:
            # Daemon and coordinator can report the same start/terminal fact.
            identity = (event.get("type"), event.get("job_id") or event.get("job", {}).get("job_id"),
                        event.get("round_id"), event.get("node_id"),
                        event.get("message") or event.get("reason") or event.get("error"),
                        event.get("event_key"))
            if any(e["identity"] == identity for e in self._events):
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
            self._publish_stream_frame_locked({'id': self._event_sequence, 'kind': 'event',
                                               'event': deepcopy(self._events[-1]['event'])})

    def snapshot(self) -> Dict[str, Any]:
        # All publishers use daemon -> service order. Reading and publishing
        # remain atomic, including concurrent HTTP requests and heartbeats.
        with self._source_lock:
            return self._snapshot_locked()

    def _snapshot_locked(self) -> Dict[str, Any]:
        raw = deepcopy(self._read_status())
        with self._lock:
            active_id = (raw["active_job"] or {}).get("job_id")
            recent_id = (raw["recent_job"] or {}).get("job_id")
            self._job_disconnections = {
                job_id: events for job_id, events in self._job_disconnections.items()
                if job_id in {active_id, recent_id}
            }
            if self._web_status is not None:
                raw["management_web"]["status"], raw["management_web"]["error"] = self._web_status
            nodes = []
            for node in sorted(raw["nodes"].values(), key=lambda node: node["node_id"]):
                node["state"] = ("offline" if node["readiness"] == "OFFLINE" else
                                 "error" if node["error_code"] else
                                 (node["reported_state"] or "HUNTING").lower())
                node["online"] = node["readiness"] != "OFFLINE"
                if self._node_online.get(node["node_id"]) is True and not node["online"]:
                    observed_job = raw["active_job"]
                    if observed_job is None:
                        recent_job = raw["recent_job"]
                        observed_job = recent_job if recent_job and recent_job.get("recovery_state") != "ready" else {}
                    active_job_id = observed_job.get("job_id") if node["node_id"] in observed_job.get("target_nodes", []) else None
                    if active_job_id is not None:
                        disconnection = {
                            "type": "NODE_OFFLINE",
                            "job_id": active_job_id,
                            "node_id": node["node_id"],
                            "message": f"节点 {node['node_id']} 离线",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                        self._job_disconnections.setdefault(active_job_id, []).append(disconnection)
                    self.record_event({"type": "NODE_OFFLINE", "job_id": active_job_id,
                                       "event_key": f"{active_job_id}:{node['node_id']}:{time.monotonic_ns()}",
                                       "node_id": node["node_id"],
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
            participant_nodes = {node["node_id"]: node for node in nodes}
            current = _job_view(raw["active_job"], participant_nodes, self._job_disconnections)
            recent = _job_view(raw["recent_job"], participant_nodes, self._job_disconnections)
            if current:
                current["execution_result"] = "running"
                current["recovery_state"] = "not_required"
                blockers.append({"code": "JOB_RUNNING", "message": "作业正在运行"})
            if recent and recent["recovery_state"] != "ready":
                blockers.append({"code": "JOB_RESOURCE_NOT_READY", "message": "上一项作业资源尚未恢复"})
            if not any(n["can_select"] for n in nodes):
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
            state["state_version"] = self._version
            state["generated_at"] = datetime.now(timezone.utc).isoformat()
            if facts != self._facts:
                self._version += 1
                state["state_version"] = self._version
                self._facts = deepcopy(facts)
                if self._stream_subscribers and not self._initializing_stream:
                    self._event_sequence += 1
                    self._publish_stream_frame_locked({
                        'id': self._event_sequence, 'kind': 'snapshot',
                        'snapshot': deepcopy(state), 'resync': False})
            state["state_version"] = self._version
            return deepcopy(state)
