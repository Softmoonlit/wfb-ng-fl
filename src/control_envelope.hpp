#pragma once

#include <stddef.h>
#include <stdint.h>

enum class ControlEnvelopeParseStatus {
    ok,
    not_control_packet,
    invalid_length,
    invalid_magic,
    invalid_version,
    invalid_control_type,
    invalid_field,
};

enum class GrantDecision {
    accept,
    ignore_invalid_source,
    ignore_wrong_target,
    ignore_expired,
    ignore_duplicate_sequence,
    ignore_stale_sequence,
};

enum class ReadyDecision {
    accept,
    ignore_invalid_source,
    ignore_wrong_ingress_or_link_domain,
    ignore_unknown_client,
};

struct ControlEnvelopeView {
    uint8_t control_type;
    uint8_t source_node;
    uint8_t target_node;
    uint64_t sequence;
    uint32_t grant_duration_ms;
    uint64_t grant_expires_at_ms;
};

struct GrantFilterState {
    bool has_last_sequence;
    uint64_t last_sequence;
};

struct ReadyRejectedCounters {
    uint32_t invalid_source;
    uint32_t wrong_ingress_or_link_domain;
    uint32_t unknown_client;
};

struct ReadyFilterCounters {
    uint32_t received;
    uint32_t accepted;
    ReadyRejectedCounters rejected;
};

struct GrantFilterCounters {
    uint32_t received;
    uint32_t accepted;
    uint32_t ignored_invalid_source;
    uint32_t ignored_wrong_target;
    uint32_t ignored_expired;
    uint32_t ignored_duplicate_sequence;
    uint32_t ignored_stale_sequence;
};

ControlEnvelopeParseStatus parse_control_envelope(const uint8_t *buf,
                                                  size_t size,
                                                  uint64_t now_ms,
                                                  ControlEnvelopeView *out);

GrantDecision filter_grant(const ControlEnvelopeView &envelope,
                           bool is_valid_source,
                           uint8_t local_node_id,
                           uint64_t now_ms,
                           GrantFilterState *state,
                           GrantFilterCounters *counters);

ReadyDecision filter_ready(const ControlEnvelopeView &envelope,
                           bool is_valid_ingress,
                           bool is_known_client,
                           ReadyFilterCounters *counters);

