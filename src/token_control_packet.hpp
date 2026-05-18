#pragma once

#include <stddef.h>
#include <stdint.h>

enum class TokenControlParseStatus {
    ok,
    not_token_packet,
    invalid_length,
    invalid_magic,
    invalid_version,
    invalid_field,
};

enum class TokenControlDecision {
    accept,
    ignore_wrong_node,
    ignore_duplicate_sequence,
    ignore_stale_sequence,
    ignore_expired,
};

struct TokenControlPacketView {
    uint8_t node_id;
    uint64_t sequence;
    uint32_t duration_ms;
    uint64_t expires_at_ms;
};

struct TokenControlFilterState {
    bool has_last_sequence;
    uint64_t last_sequence;
};

struct TokenControlFilterCounters {
    uint32_t received;
    uint32_t accepted;
    uint32_t ignored_wrong_node;
    uint32_t ignored_duplicate_sequence;
    uint32_t ignored_stale_sequence;
    uint32_t ignored_expired;
};

TokenControlParseStatus parse_token_control_packet(const uint8_t *buf,
                                                   size_t size,
                                                   uint64_t now_ms,
                                                   TokenControlPacketView *out);

TokenControlDecision filter_token_control_packet(const TokenControlPacketView &packet,
                                                 uint8_t local_node_id,
                                                 uint64_t now_ms,
                                                 TokenControlFilterState *state,
                                                 TokenControlFilterCounters *counters);
