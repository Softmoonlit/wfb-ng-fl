#!/usr/bin/env python
# -*- coding: utf-8 -*-

import hashlib
import json


class V6StartupConfigError(ValueError):
    pass


_VALID_ROLES = frozenset(('server', 'client'))
_SERVER_ROLE_FIELDS = frozenset((
    'known_clients',
    'grant_duration_ms',
    'guard_interval_ms',
    'feedback_window_period_ms',
    'feedback_window_duration_ms',
    'downlink_pause_threshold_bytes',
    'downlink_resume_threshold_bytes',
    'downlink_queue_packets_limit',
    'rx_reassembly_unfinished_block_limit',
))
_CLIENT_ROLE_FIELDS = frozenset((
    'uplink_token_gate_enabled',
    'uplink_pause_threshold_bytes',
    'uplink_resume_threshold_bytes',
    'uplink_queue_packets_limit',
    'ready_policy',
))
_ROLE_FIELDS = {
    'server': _SERVER_ROLE_FIELDS,
    'client': _CLIENT_ROLE_FIELDS,
}
_OTHER_ROLE = {
    'server': 'client',
    'client': 'server',
}


def hash_link_domain(link_domain):
    return int.from_bytes(hashlib.sha1(link_domain.encode('utf-8')).digest()[:3], 'big')


def _get_section(settings, name):
    return getattr(settings, name, None)


def _require_section(settings, name):
    section = _get_section(settings, name)
    if section is None:
        raise V6StartupConfigError('%s 缺少配置段 %s' % (name.split('_')[0], name))
    return section


def _section_values(section):
    return dict(getattr(section, '__dict__', {}))


def _require_attr(section, attr_name, context):
    if not hasattr(section, attr_name):
        raise V6StartupConfigError('%s 缺少必选参数 %s' % (context, attr_name))

    value = getattr(section, attr_name)
    if value is None:
        raise V6StartupConfigError('%s 缺少必选参数 %s' % (context, attr_name))
    if isinstance(value, str) and not value.strip():
        raise V6StartupConfigError('%s 缺少必选参数 %s' % (context, attr_name))
    return value


def _require_positive_int(section, attr_name, context, allow_zero=False):
    value = _require_attr(section, attr_name, context)
    if not isinstance(value, int):
        raise V6StartupConfigError('%s.%s 必须是整数' % (context, attr_name))
    if allow_zero:
        if value < 0:
            raise V6StartupConfigError('%s.%s 不能为负数' % (context, attr_name))
    elif value <= 0:
        raise V6StartupConfigError('%s.%s 必须大于 0' % (context, attr_name))
    return value


def _normalize_role(role, context):
    if role is None:
        raise V6StartupConfigError('%s 缺少显式 role' % context)
    if not isinstance(role, str):
        raise V6StartupConfigError('%s.role 必须是 server 或 client' % context)
    normalized = role.strip().lower()
    if normalized not in _VALID_ROLES:
        raise V6StartupConfigError('%s.role 必须是 server 或 client' % context)
    return normalized


def _validate_common_layer(profile_name, profile_section):
    for field_name in _SERVER_ROLE_FIELDS | _CLIENT_ROLE_FIELDS:
        if hasattr(profile_section, field_name):
            raise V6StartupConfigError('%s 不能在公共参数层混入 %s' % (profile_name, field_name))


def _resolve_role(profile_name, profile_section, role_override, env):
    env = env or {}

    candidates = []
    profile_role = getattr(profile_section, 'role', None)
    if profile_role is not None:
        candidates.append(('profile', _normalize_role(profile_role, profile_name)))
    env_role = env.get('WFB_ROLE')
    if env_role is not None:
        candidates.append(('env', _normalize_role(env_role, profile_name)))
    if role_override is not None:
        candidates.append(('cli', _normalize_role(role_override, profile_name)))

    if not candidates:
        raise V6StartupConfigError('%s 缺少显式 role' % profile_name)

    resolved_role = candidates[0][1]
    for source_name, candidate_role in candidates[1:]:
        if candidate_role != resolved_role:
            raise V6StartupConfigError('%s role 来源冲突: %s=%s' % (profile_name, source_name, candidate_role))

    return resolved_role


def _ensure_no_opposite_role_section(settings, profile_name, role):
    opposite_name = '%s_%s' % (profile_name, _OTHER_ROLE[role])
    opposite_section = _get_section(settings, opposite_name)
    if opposite_section is not None and _section_values(opposite_section):
        raise V6StartupConfigError('%s 在 role=%s 下混入了 %s 专属参数段' % (profile_name, role, opposite_name))


def _resolve_tun_summary(settings, profile_name):
    profile_section = _require_section(settings, profile_name)
    streams = _require_attr(profile_section, 'streams', profile_name)
    if not isinstance(streams, list):
        raise V6StartupConfigError('%s.streams 必须是列表' % profile_name)

    for stream in streams:
        if stream.get('service_type') != 'tunnel':
            continue

        merged = {}
        for base_profile in stream.get('profiles', []):
            merged.update(_section_values(_require_section(settings, base_profile)))
        merged.update(stream)

        for field_name in ('ifname', 'ifaddr', 'default_route'):
            if field_name not in merged:
                raise V6StartupConfigError('%s 缺少 TUN 必选参数 %s' % (profile_name, field_name))

        return {
            'ifname': merged['ifname'],
            'ifaddr': merged['ifaddr'],
            'default_route': merged['default_route'],
        }

    raise V6StartupConfigError('%s 缺少 tunnel 服务配置' % profile_name)

def _has_legacy_keypair_material(settings, profile_name):
    profile_section = _require_section(settings, profile_name)
    streams = _require_attr(profile_section, 'streams', profile_name)
    if not isinstance(streams, list):
        raise V6StartupConfigError('%s.streams 必须是列表' % profile_name)

    for stream in streams:
        merged = {}
        for base_profile in stream.get('profiles', []):
            merged.update(_section_values(_require_section(settings, base_profile)))
        merged.update(stream)

        keypair = merged.get('keypair')
        if isinstance(keypair, str) and keypair.strip():
            return True

    return False


def _validate_link_security_mode(settings, profile_name, link_security_mode):
    if link_security_mode == 'legacy_encrypted':
        if not _has_legacy_keypair_material(settings, profile_name):
            raise V6StartupConfigError('%s 在 legacy_encrypted 缺少旧 keypair' % profile_name)
        return

    if link_security_mode == 'trusted_plaintext':
        if _has_legacy_keypair_material(settings, profile_name):
            raise V6StartupConfigError('%s 在 trusted_plaintext 下禁止旧 keypair' % profile_name)
        return

    raise V6StartupConfigError('%s.link_security_mode 必须是 trusted_plaintext 或 legacy_encrypted' % profile_name)


def _resolve_known_clients(section, context):
    value = _require_attr(section, 'known_clients', context)
    if not isinstance(value, list) or not value:
        raise V6StartupConfigError('%s.known_clients 必须是非空 NODE_ID 集合' % context)

    normalized = []
    seen = set()
    for node_id in value:
        if not isinstance(node_id, int) or node_id <= 0 or node_id > 255:
            raise V6StartupConfigError('%s.known_clients 只能包含 1-255 的 NODE_ID' % context)
        if node_id in seen:
            raise V6StartupConfigError('%s.known_clients 不能包含重复 NODE_ID' % context)
        seen.add(node_id)
        normalized.append(node_id)
    return normalized


def _resolve_server_layer(settings, profile_name):
    section_name = '%s_server' % profile_name
    section = _require_section(settings, section_name)
    context = section_name

    pause_threshold = _require_positive_int(section, 'downlink_pause_threshold_bytes', context)
    resume_threshold = _require_positive_int(section, 'downlink_resume_threshold_bytes', context)
    if pause_threshold < resume_threshold:
        raise V6StartupConfigError('%s 的 downlink pause 阈值不能小于 resume 阈值' % profile_name)

    return {
        'tun': _resolve_tun_summary(settings, profile_name),
        'known_clients': _resolve_known_clients(section, context),
        'grant': {
            'duration_ms': _require_positive_int(section, 'grant_duration_ms', context),
            'guard_interval_ms': _require_positive_int(section, 'guard_interval_ms', context, allow_zero=True),
        },
        'feedback_window': {
            'period_ms': _require_positive_int(section, 'feedback_window_period_ms', context),
            'duration_ms': _require_positive_int(section, 'feedback_window_duration_ms', context),
        },
        'downlink_queue': {
            'pause_threshold_bytes': pause_threshold,
            'resume_threshold_bytes': resume_threshold,
            'queued_packets_limit': _require_positive_int(section, 'downlink_queue_packets_limit', context),
        },
        'rx_reassembly': {
            'unfinished_block_limit': _require_positive_int(section, 'rx_reassembly_unfinished_block_limit', context),
        },
    }


def _resolve_client_layer(settings, profile_name):
    section_name = '%s_client' % profile_name
    section = _require_section(settings, section_name)
    context = section_name

    pause_threshold = _require_positive_int(section, 'uplink_pause_threshold_bytes', context)
    resume_threshold = _require_positive_int(section, 'uplink_resume_threshold_bytes', context)
    if pause_threshold < resume_threshold:
        raise V6StartupConfigError('%s 的 uplink pause 阈值不能小于 resume 阈值' % profile_name)

    token_gate_enabled = _require_attr(section, 'uplink_token_gate_enabled', context)
    if not isinstance(token_gate_enabled, bool):
        raise V6StartupConfigError('%s.uplink_token_gate_enabled 必须是布尔值' % context)

    summary = {
        'tun': _resolve_tun_summary(settings, profile_name),
        'uplink_token_gate': {
            'enabled': token_gate_enabled,
        },
        'uplink_queue': {
            'pause_threshold_bytes': pause_threshold,
            'resume_threshold_bytes': resume_threshold,
            'queued_packets_limit': _require_positive_int(section, 'uplink_queue_packets_limit', context),
        },
    }
    if hasattr(section, 'ready_policy'):
        summary['ready_policy'] = getattr(section, 'ready_policy')
    return summary


def should_use_v6_startup_path(settings, profile_names, role_override=None, env=None):
    env = env or {}
    if role_override is not None:
        return True
    if env.get('WFB_ROLE'):
        return True

    for profile_name in profile_names:
        profile_section = _get_section(settings, profile_name)
        if profile_section is not None and hasattr(profile_section, 'role'):
            return True
    return False


def resolve_v6_startup_config(settings, profile_name, radio_interfaces=None, role_override=None, env=None):
    profile_section = _require_section(settings, profile_name)
    _validate_common_layer(profile_name, profile_section)
    role = _resolve_role(profile_name, profile_section, role_override, env)
    _ensure_no_opposite_role_section(settings, profile_name, role)

    node_id = _require_positive_int(profile_section, 'node_id', profile_name)
    if node_id > 255:
        raise V6StartupConfigError('%s.node_id 必须在 1-255 范围内' % profile_name)

    link_domain = _require_attr(profile_section, 'link_domain', profile_name)
    uplink_stream = _require_positive_int(profile_section, 'uplink_stream', profile_name, allow_zero=True)
    downlink_stream = _require_positive_int(profile_section, 'downlink_stream', profile_name, allow_zero=True)
    link_security_mode = _require_attr(profile_section, 'link_security_mode', profile_name)
    _validate_link_security_mode(settings, profile_name, link_security_mode)

    summary = {
        'profile': profile_name,
        'common': {
            'role': role,
            'node_id': node_id,
            'link_domain': link_domain,
            'link_id': hash_link_domain(link_domain),
            'uplink_stream': uplink_stream,
            'downlink_stream': downlink_stream,
            'radio_interfaces': None if radio_interfaces is None else list(radio_interfaces),
            'link_security_mode': link_security_mode,
            'log_interval_ms': _require_positive_int(getattr(settings, 'common', object()), 'log_interval', 'common'),
        },
    }

    if role == 'server':
        summary['server'] = _resolve_server_layer(settings, profile_name)
    else:
        summary['client'] = _resolve_client_layer(settings, profile_name)

    return summary


def resolve_v6_startup_configs(settings, profile_names, radio_interfaces=None, role_override=None, env=None):
    summaries = [resolve_v6_startup_config(settings, profile_name, radio_interfaces, role_override, env)
                 for profile_name in profile_names]
    if not summaries:
        raise V6StartupConfigError('至少需要一个 profile')

    role = summaries[0]['common']['role']
    for summary in summaries[1:]:
        if summary['common']['role'] != role:
            raise V6StartupConfigError('同一实例的所有 profile 必须解析为同一 role')

    link_security_mode = summaries[0]['common']['link_security_mode']
    for summary in summaries[1:]:
        if summary['common']['link_security_mode'] != link_security_mode:
            raise V6StartupConfigError('同一实例的所有 profile 必须解析为同一 link_security_mode')

    return {
        'role': role,
        'profiles': summaries,
    }


def format_v6_startup_summary(summary):
    return json.dumps(summary, ensure_ascii=False, sort_keys=True)
