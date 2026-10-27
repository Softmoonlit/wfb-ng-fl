"""算法宿主提供的只读作业上下文与显式函数装配。"""
import copy
import importlib
import inspect
import json
import math
import os
import threading
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional, Tuple

from .errors import FLRuntimeError


RADIO_POLICY_PATCH_FIELDS = frozenset({
    'radio_txpower_dbm',
    'downlink_mcs',
    'uplink_mcs',
    'bandwidth_mhz',
    'uftp_rate_kbps',
})


def _jsonable(value: Any, *, label: str) -> Any:
    if isinstance(value, Mapping):
        converted = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f'{label} object keys must be strings')
            converted[key] = _jsonable(item, label=label)
        return converted
    if isinstance(value, (list, tuple)):
        return [_jsonable(item, label=label) for item in value]
    return value


def _finite_json_snapshot(value: Any, *, label: str, max_bytes: int) -> Any:
    try:
        encoded_value = _jsonable(value, label=label)
        encoded = json.dumps(encoded_value, ensure_ascii=False, allow_nan=False).encode('utf-8')
        if len(encoded) > max_bytes:
            raise ValueError(f'{label} exceeds the finite control result budget')
        return json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise FLRuntimeError(f'invalid_{label}', f'{label} must be a finite JSON value') from exc


def validate_radio_policy_patch(patch: Mapping[str, Any]) -> dict:
    """Validate only the algorithm-facing radio PATCH shape.

    Physical ranges, combination tables, target membership, and transaction
    state remain host/daemon concerns. In particular, this helper never fills
    a rate or manufactures an effective configuration.
    """
    if not isinstance(patch, Mapping) or not patch:
        raise FLRuntimeError('invalid_radio_patch', 'radio patch must be a non-empty object')
    keys = tuple(patch.keys())
    if any(not isinstance(key, str) for key in keys):
        raise FLRuntimeError(
            'invalid_radio_patch',
            'radio patch object keys must be strings',
        )
    unknown = set(keys) - RADIO_POLICY_PATCH_FIELDS
    if unknown:
        raise FLRuntimeError(
            'invalid_radio_patch',
            'radio patch contains unsupported fields',
        )
    for key, value in patch.items():
        if type(value) is not int:
            raise FLRuntimeError(
                'invalid_radio_patch',
                f'radio patch field {key!r} must be an integer',
            )
    try:
        return _finite_json_snapshot(dict(patch), label='radio_patch', max_bytes=1024)
    except FLRuntimeError:
        raise


def validate_radio_policy_result(result: Any) -> Mapping[str, Any]:
    """Return an opaque, finite host report without asserting radio authority.

    The result is deliberately not interpreted as ``effective_config``. Only
    the daemon/transaction owner can establish what became effective.
    """
    if not isinstance(result, Mapping):
        raise FLRuntimeError('invalid_radio_result', 'radio configure result must be an object')
    snapshot = _finite_json_snapshot(result, label='radio_result', max_bytes=4096)
    if not isinstance(snapshot, dict):
        raise FLRuntimeError('invalid_radio_result', 'radio configure result must be an object')
    return _freeze(snapshot)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    return copy.deepcopy(value)


def load_algorithm(spec: str, *, arity: Optional[int] = None) -> Callable[..., Any]:
    """按部署给定的模块路径加载函数，不探测旧入口或默认模拟实现。"""
    if not isinstance(spec, str) or spec.count(':') != 1:
        raise FLRuntimeError('invalid_configuration', '算法入口须为 package.module:callable')
    module_name, name = spec.split(':')
    if not module_name or not name:
        raise FLRuntimeError('invalid_configuration', '算法入口不能为空')
    try:
        target = importlib.import_module(module_name)
        for part in name.split('.'):
            target = getattr(target, part)
        if not callable(target):
            raise TypeError('算法入口不可调用')
        if arity is not None:
            inspect.signature(target).bind(*([None] * arity))
    except (ImportError, AttributeError, TypeError, ValueError) as exc:
        raise FLRuntimeError('invalid_configuration', f'算法入口不可用: {spec}') from exc
    return target


def validate_algorithm_config(config: Mapping[str, Any]) -> dict:
    """冻结前检查可下发的算法值；本机文件位置只从宿主上下文提供。"""
    def check(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if not isinstance(key, str) or key.endswith(('_path', '_dir')):
                    raise ValueError('算法配置不得包含本机路径字段')
                check(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                check(item)
        elif isinstance(value, str) and os.path.isabs(value):
            raise ValueError('算法配置不得包含本机绝对路径')

    try:
        if not isinstance(config, Mapping):
            raise ValueError('算法配置须为对象')
        check(config)
        encoded = json.dumps(dict(config), ensure_ascii=False, allow_nan=False).encode('utf-8')
        if len(encoded) > 2048:
            raise ValueError('算法配置超出任务控制消息预算')
        return json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise FLRuntimeError('invalid_algorithm_config', str(exc)) from exc


def freeze_algorithm_config(config: Mapping[str, Any]) -> Mapping[str, Any]:
    """供算法调用使用的递归只读参数快照，不暴露宿主的可变冻结记录。"""
    return _freeze(validate_algorithm_config(config))


def validate_evaluation_result(result: Any) -> dict:
    """校验算法公共结果信封；指标含义和参考数据检查仍由算法拥有。"""
    if (not isinstance(result, Mapping)
            or set(result) != {'metrics', 'preparation', 'verdict', 'reason'}
            or not isinstance(result['metrics'], Mapping) or not result['metrics']
            or (result['preparation'] is not None and not isinstance(result['preparation'], Mapping))
            or (result['verdict'] is not None and type(result['verdict']) is not bool)
            or (result['reason'] is not None and
                (not isinstance(result['reason'], str) or not result['reason'].strip()
                 or len(result['reason'].encode('utf-8')) > 512))):
        raise FLRuntimeError('invalid_evaluation_result', '评估须返回有限指标、准备信息和效果判定')
    if any(not isinstance(key, str) or type(value) not in (int, float) or not math.isfinite(value)
           for key, value in result['metrics'].items()):
        raise FLRuntimeError('invalid_evaluation_result', '评估指标须为有限数值')
    try:
        return json.loads(json.dumps(dict(result), allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise FLRuntimeError('invalid_evaluation_result', '评估结果不是有限JSON对象') from exc


@dataclass(frozen=True)
class AlgorithmContext:
    job_id: str
    node_id: int
    target_nodes: Tuple[int, ...]
    rounds: int
    artifact_dir: str
    local_config: Mapping[str, Any]
    stop_event: threading.Event = field(repr=False)
    phase: str = 'preparing'
    round_index: int = 0
    initial_metrics: Optional[Mapping[str, Any]] = None
    prepared_callback: Optional[Callable[[dict], None]] = field(default=None, repr=False)
    round_callback: Optional[Callable[[int], None]] = field(default=None, repr=False)
    radio_configure_callback: Optional[Callable[[Mapping[str, Any]], Mapping[str, Any]]] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, 'target_nodes', tuple(self.target_nodes))
        object.__setattr__(self, 'local_config', _freeze(self.local_config))
        if self.initial_metrics is not None:
            object.__setattr__(self, 'initial_metrics', _freeze(self.initial_metrics))

    def for_phase(self, phase: str, *, round_index: Optional[int] = None,
                  initial_metrics: Optional[Mapping[str, Any]] = None) -> 'AlgorithmContext':
        return replace(
            self,
            phase=phase,
            round_index=self.round_index if round_index is None else round_index,
            initial_metrics=self.initial_metrics if initial_metrics is None else initial_metrics,
            radio_configure_callback=(
                self.radio_configure_callback if phase == 'radio_policy' else None
            ),
        )

    def check_stop(self) -> None:
        if self.stop_event.is_set():
            raise FLRuntimeError('job_aborted', '作业已请求停止')

    def configure_radio(self, patch: Mapping[str, Any]) -> Mapping[str, Any]:
        """Synchronously ask the host to apply an algorithm radio PATCH.

        The callable is a job-bound host closure. The algorithm sees neither
        ControlPlane, Runtime, radio adapter, nor target membership. Its report
        remains an opaque finite snapshot and is never promoted to an
        effective configuration by this context.
        """
        self.check_stop()
        if self.phase != 'radio_policy':
            raise FLRuntimeError(
                'radio_policy_phase_required',
                'configure_radio 仅可在 radio_policy 阶段调用',
            )
        if self.radio_configure_callback is None:
            raise FLRuntimeError(
                'radio_configuration_unavailable',
                '宿主未提供作业绑定的无线配置通路',
            )
        normalized = validate_radio_policy_patch(patch)
        try:
            result = self.radio_configure_callback(_freeze(normalized))
        except FLRuntimeError:
            raise
        except Exception as exc:
            raise FLRuntimeError('radio_configuration_failed', '无线配置事务执行失败') from exc
        self.check_stop()
        return validate_radio_policy_result(result)

    def report_prepared(self, report: Mapping[str, Any]) -> None:
        self.check_stop()
        if self.prepared_callback is None:
            raise FLRuntimeError('algorithm_report_unavailable', '宿主未提供准备报告通路')
        try:
            encoded = json.dumps(dict(report), ensure_ascii=False, allow_nan=False).encode('utf-8')
            if len(encoded) > 3072 or not report:
                raise ValueError('准备报告为空或超过有限控制消息预算')
            snapshot = json.loads(encoded)
        except (TypeError, ValueError) as exc:
            raise FLRuntimeError('invalid_preparation_report', str(exc)) from exc
        self.prepared_callback(snapshot)
        self.check_stop()

    def report_round_complete(self, index: int) -> None:
        self.check_stop()
        if self.round_callback is None:
            raise FLRuntimeError('algorithm_report_unavailable', '宿主未提供轮次报告通路')
        if type(index) is not int or not 1 <= index <= self.rounds:
            raise FLRuntimeError('invalid_round_report', '本地完成轮次超出作业范围')
        self.round_callback(index)
        self.check_stop()
