# -*- coding: utf-8 -*-

import json
from typing import Any, Dict, Iterable, Mapping

RUN_KIND_NAMESPACE = 'namespace'
RUN_KIND_REAL_HARDWARE = 'real_hardware'
LINK_SECURITY_MODE_TRUSTED_PLAINTEXT = 'trusted_plaintext'
SUMMARY_FILENAME = 'formal_2a_summary.json'
CONCLUSION_FILENAME = 'formal_conclusion.md'

SCENARIO_V6_NAMESPACE_UPLINK = 'v6_namespace_uplink_backpressure_reassembly'
SCENARIO_V6_NAMESPACE_DOWNLINK = 'v6_namespace_downlink_shared_uftp_feedback'
SCENARIO_V6_REAL_HARDWARE_UPLINK = 'v6_real_hardware_uplink_dual_long_run'
SCENARIO_V6_REAL_HARDWARE_DOWNLINK_SINGLE = 'v6_real_hardware_downlink_single_uftp'
SCENARIO_V6_REAL_HARDWARE_DOWNLINK_SHARED = 'v6_real_hardware_downlink_shared_uftp_feedback'

READY_REJECTION_REASONS = (
    'invalid_source',
    'wrong_ingress_or_link_domain',
    'unknown_client',
)

PAUSE_REASONS = (
    'queued_bytes_threshold',
    'queued_packets_limit',
)

UPLINK_2A_FIELDS = (
    'run_kind',
    'link_security_mode',
    'scenario_id',
    'feedback_window_covered',
    'tun_read_pause_total',
    'tun_read_resume_total',
    'tun_read_pause_total_by_reason',
    'reassembly_overflow_evict',
    'unfinished_block_limit',
)

DOWNLINK_FEEDBACK_2A_FIELDS = (
    'run_kind',
    'link_security_mode',
    'scenario_id',
    'feedback_window_covered',
    'grant_sent_total',
    'ready_accepted_total',
    'ready_rejected_total_by_reason',
    'feedback_window_open_count',
    'feedback_window_close_count',
    'feedback_uplink_hit_total_by_node',
    'feedback_uplink_hit_total',
    'tun_read_pause_total',
    'tun_read_resume_total',
    'tun_read_pause_total_by_reason',
    'reassembly_overflow_evict',
    'unfinished_block_limit',
)

DOWNLINK_NO_FEEDBACK_2A_FIELDS = (
    'run_kind',
    'link_security_mode',
    'scenario_id',
    'feedback_window_covered',
    'grant_sent_total',
    'ready_accepted_total',
    'ready_rejected_total_by_reason',
    'tun_read_pause_total',
    'tun_read_resume_total',
    'tun_read_pause_total_by_reason',
    'reassembly_overflow_evict',
    'unfinished_block_limit',
)

SCENARIO_DEFINITIONS = {
    SCENARIO_V6_NAMESPACE_UPLINK: {
        'run_kind': RUN_KIND_NAMESPACE,
        'feedback_window_covered': False,
        'allowed_fields': UPLINK_2A_FIELDS,
    },
    SCENARIO_V6_NAMESPACE_DOWNLINK: {
        'run_kind': RUN_KIND_NAMESPACE,
        'feedback_window_covered': True,
        'allowed_fields': DOWNLINK_FEEDBACK_2A_FIELDS,
    },
    SCENARIO_V6_REAL_HARDWARE_UPLINK: {
        'run_kind': RUN_KIND_REAL_HARDWARE,
        'feedback_window_covered': False,
        'allowed_fields': UPLINK_2A_FIELDS,
    },
    SCENARIO_V6_REAL_HARDWARE_DOWNLINK_SINGLE: {
        'run_kind': RUN_KIND_REAL_HARDWARE,
        'feedback_window_covered': False,
        'allowed_fields': DOWNLINK_NO_FEEDBACK_2A_FIELDS,
    },
    SCENARIO_V6_REAL_HARDWARE_DOWNLINK_SHARED: {
        'run_kind': RUN_KIND_REAL_HARDWARE,
        'feedback_window_covered': True,
        'allowed_fields': DOWNLINK_FEEDBACK_2A_FIELDS,
    },
}


class SummaryValidationError(ValueError):
    pass



def _ensure(condition: bool, message: str) -> None:
    if not condition:
        raise SummaryValidationError(message)



def _ensure_non_negative_int(name: str, value: Any) -> None:
    _ensure(isinstance(value, int) and not isinstance(value, bool), '%s 必须是非负整数' % name)
    _ensure(value >= 0, '%s 必须是非负整数' % name)



def _validate_reason_counter(name: str, value: Any, reasons: Iterable[str]) -> None:
    _ensure(isinstance(value, dict), '%s 必须是对象' % name)
    keys = set(value.keys())
    expected = set(reasons)
    _ensure(keys == expected, '%s 字段必须精确等于 %s' % (name, sorted(expected)))
    for reason in reasons:
        _ensure_non_negative_int('%s.%s' % (name, reason), value[reason])



def _validate_feedback_hits(value: Any) -> None:
    _ensure(isinstance(value, dict), 'feedback_uplink_hit_total_by_node 必须是对象')
    for key, hit_total in value.items():
        _ensure(str(key).strip() != '', 'feedback_uplink_hit_total_by_node 键不能为空')
        _ensure_non_negative_int('feedback_uplink_hit_total_by_node.%s' % key, hit_total)



def allowed_fields_for(scenario_id: str):
    _ensure(scenario_id in SCENARIO_DEFINITIONS, 'scenario_id 不在受控枚举内')
    return tuple(SCENARIO_DEFINITIONS[scenario_id]['allowed_fields'])



def _validate_summary(summary: Mapping[str, Any]) -> None:
    scenario_id = summary.get('scenario_id')
    _ensure(isinstance(scenario_id, str) and scenario_id in SCENARIO_DEFINITIONS, 'scenario_id 不在受控枚举内')
    scenario_definition = SCENARIO_DEFINITIONS[scenario_id]

    _ensure(summary.get('run_kind') == scenario_definition['run_kind'], 'run_kind 与 scenario_id 不匹配')
    _ensure(
        summary.get('link_security_mode') == LINK_SECURITY_MODE_TRUSTED_PLAINTEXT,
        'link_security_mode 必须为 trusted_plaintext',
    )

    _ensure(
        summary.get('feedback_window_covered') == scenario_definition['feedback_window_covered'],
        'feedback_window_covered 与 scenario_id 不匹配',
    )

    keys = set(summary.keys())
    allowed_fields = set(scenario_definition['allowed_fields'])
    _ensure(keys == allowed_fields, '统一 2A 摘要字段必须精确等于 %s，实际为 %s' % (sorted(allowed_fields), sorted(keys)))

    if 'ready_rejected_total_by_reason' in summary:
        _validate_reason_counter('ready_rejected_total_by_reason', summary['ready_rejected_total_by_reason'], READY_REJECTION_REASONS)

    if 'tun_read_pause_total_by_reason' in summary:
        _validate_reason_counter('tun_read_pause_total_by_reason', summary['tun_read_pause_total_by_reason'], PAUSE_REASONS)

    if 'feedback_uplink_hit_total_by_node' in summary:
        _validate_feedback_hits(summary['feedback_uplink_hit_total_by_node'])

    for field_name in (
        'grant_sent_total',
        'ready_accepted_total',
        'feedback_window_open_count',
        'feedback_window_close_count',
        'feedback_uplink_hit_total',
        'tun_read_pause_total',
        'tun_read_resume_total',
        'reassembly_overflow_evict',
        'unfinished_block_limit',
    ):
        if field_name in summary:
            _ensure_non_negative_int(field_name, summary[field_name])



def build_summary(scenario_id: str, **fields: Any) -> Dict[str, Any]:
    _ensure(scenario_id in SCENARIO_DEFINITIONS, 'scenario_id 不在受控枚举内')
    scenario_definition = SCENARIO_DEFINITIONS[scenario_id]
    summary = {
        'run_kind': scenario_definition['run_kind'],
        'link_security_mode': LINK_SECURITY_MODE_TRUSTED_PLAINTEXT,
        'scenario_id': scenario_id,
        'feedback_window_covered': scenario_definition['feedback_window_covered'],
    }
    summary.update(fields)
    _validate_summary(summary)
    return summary



def write_summary(path: str, summary: Mapping[str, Any]) -> None:
    _validate_summary(summary)
    with open(path, 'w') as fh:
        json.dump(dict(summary), fh, indent=2, sort_keys=True)
        fh.write('\n')



def load_summary(path: str) -> Dict[str, Any]:
    with open(path, 'r') as fh:
        summary = json.load(fh)
    _validate_summary(summary)
    return summary
