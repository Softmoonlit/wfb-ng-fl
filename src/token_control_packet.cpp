#include "token_control_packet.hpp"

#include <endian.h>
#include <limits>

#include "wifibroadcast.hpp"

namespace {

uint64_t saturating_add_u64(uint64_t lhs, uint32_t rhs)
{
    if (lhs > std::numeric_limits<uint64_t>::max() - rhs)
    {
        return std::numeric_limits<uint64_t>::max();
    }
    return lhs + rhs;
}

void increment_counter(uint32_t *counter)
{
    if (counter != nullptr && *counter < std::numeric_limits<uint32_t>::max())
    {
        *counter += 1;
    }
}

}

TokenControlParseStatus parse_token_control_packet(const uint8_t *buf,
                                                   size_t size,
                                                   uint64_t now_ms,
                                                   TokenControlPacketView *out)
{
    if (size == 0 || buf[0] != WFB_PACKET_TOKEN_CONTROL)
    {
        return TokenControlParseStatus::not_token_packet;
    }

    if (size < sizeof(wtoken_control_hdr_t))
    {
        return TokenControlParseStatus::invalid_length;
    }

    const wtoken_control_hdr_t *packet = reinterpret_cast<const wtoken_control_hdr_t *>(buf);
    if (be16toh(packet->magic) != WFB_TOKEN_CONTROL_MAGIC)
    {
        return TokenControlParseStatus::invalid_magic;
    }

    if (packet->version != WFB_TOKEN_CONTROL_VERSION)
    {
        return TokenControlParseStatus::invalid_version;
    }

    if (out != nullptr)
    {
        out->node_id = packet->node_id;
        out->sequence = be64toh(packet->sequence);
        out->duration_ms = be32toh(packet->duration_ms);
        out->expires_at_ms = saturating_add_u64(now_ms, out->duration_ms);
    }

    return TokenControlParseStatus::ok;
}

TokenControlDecision filter_token_control_packet(const TokenControlPacketView &packet,
                                                 uint8_t local_node_id,
                                                 uint64_t now_ms,
                                                 TokenControlFilterState *state,
                                                 TokenControlFilterCounters *counters)
{
    increment_counter(counters != nullptr ? &counters->received : nullptr);

    if (packet.node_id != local_node_id)
    {
        increment_counter(counters != nullptr ? &counters->ignored_wrong_node : nullptr);
        return TokenControlDecision::ignore_wrong_node;
    }

    if (packet.expires_at_ms <= now_ms)
    {
        increment_counter(counters != nullptr ? &counters->ignored_expired : nullptr);
        return TokenControlDecision::ignore_expired;
    }

    if (state != nullptr && state->has_last_sequence)
    {
        if (packet.sequence == state->last_sequence)
        {
            increment_counter(counters != nullptr ? &counters->ignored_duplicate_sequence : nullptr);
            return TokenControlDecision::ignore_duplicate_sequence;
        }

        if (packet.sequence < state->last_sequence)
        {
            increment_counter(counters != nullptr ? &counters->ignored_stale_sequence : nullptr);
            return TokenControlDecision::ignore_stale_sequence;
        }
    }

    if (state != nullptr)
    {
        state->has_last_sequence = true;
        state->last_sequence = packet.sequence;
    }

    increment_counter(counters != nullptr ? &counters->accepted : nullptr);
    return TokenControlDecision::accept;
}
