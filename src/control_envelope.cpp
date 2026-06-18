#include "control_envelope.hpp"

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

ControlEnvelopeParseStatus parse_control_envelope(const uint8_t *buf,
                                                  size_t size,
                                                  uint64_t now_ms,
                                                  ControlEnvelopeView *out)
{
    if (size == 0 || buf[0] != WFB_PACKET_CONTROL)
    {
        return ControlEnvelopeParseStatus::not_control_packet;
    }

    if (size < sizeof(wcontrol_envelope_hdr_t))
    {
        return ControlEnvelopeParseStatus::invalid_length;
    }

    const wcontrol_envelope_hdr_t *envelope = reinterpret_cast<const wcontrol_envelope_hdr_t *>(buf);
    if (be16toh(envelope->magic) != WFB_CONTROL_MAGIC)
    {
        return ControlEnvelopeParseStatus::invalid_magic;
    }

    if (envelope->version != WFB_CONTROL_VERSION)
    {
        return ControlEnvelopeParseStatus::invalid_version;
    }

    if (envelope->control_type != WFB_CONTROL_TYPE_GRANT && envelope->control_type != WFB_CONTROL_TYPE_READY)
    {
        return ControlEnvelopeParseStatus::invalid_control_type;
    }

    if (envelope->control_type == WFB_CONTROL_TYPE_GRANT)
    {
        if (size != sizeof(wcontrol_envelope_hdr_t) + sizeof(wcontrol_grant_payload_t))
        {
            return ControlEnvelopeParseStatus::invalid_length;
        }
    }
    else if (envelope->control_type == WFB_CONTROL_TYPE_READY)
    {
        if (size != sizeof(wcontrol_envelope_hdr_t))
        {
            return ControlEnvelopeParseStatus::invalid_length;
        }
    }

    if (out != nullptr)
    {
        out->control_type = envelope->control_type;
        out->source_node = envelope->source_node;
        out->target_node = envelope->target_node;
        out->sequence = be64toh(envelope->sequence);

        if (envelope->control_type == WFB_CONTROL_TYPE_GRANT)
        {
            const wcontrol_grant_payload_t *payload = reinterpret_cast<const wcontrol_grant_payload_t *>(buf + sizeof(wcontrol_envelope_hdr_t));
            out->grant_duration_ms = be32toh(payload->duration_ms);
            out->grant_expires_at_ms = saturating_add_u64(now_ms, out->grant_duration_ms);
        }
    }

    return ControlEnvelopeParseStatus::ok;
}

GrantDecision filter_grant(const ControlEnvelopeView &envelope,
                           bool is_valid_source,
                           uint8_t local_node_id,
                           uint64_t now_ms,
                           GrantFilterState *state,
                           GrantFilterCounters *counters)
{
    if (counters) increment_counter(&counters->received);

    if (!is_valid_source || envelope.source_node == 0 || envelope.source_node == local_node_id)
    {
        if (counters) increment_counter(&counters->ignored_invalid_source);
        return GrantDecision::ignore_invalid_source;
    }

    if (envelope.target_node != local_node_id)
    {
        if (counters) increment_counter(&counters->ignored_wrong_target);
        return GrantDecision::ignore_wrong_target;
    }

    if (envelope.grant_expires_at_ms <= now_ms)
    {
        if (counters) increment_counter(&counters->ignored_expired);
        return GrantDecision::ignore_expired;
    }

    if (state->has_last_sequence)
    {
        if (envelope.sequence == state->last_sequence)
        {
            if (counters) increment_counter(&counters->ignored_duplicate_sequence);
            return GrantDecision::ignore_duplicate_sequence;
        }
        if (envelope.sequence < state->last_sequence)
        {
            if (counters) increment_counter(&counters->ignored_stale_sequence);
            return GrantDecision::ignore_stale_sequence;
        }
    }

    state->has_last_sequence = true;
    state->last_sequence = envelope.sequence;

    if (counters) increment_counter(&counters->accepted);
    return GrantDecision::accept;
}

ReadyDecision filter_ready(const ControlEnvelopeView &envelope,
                           bool is_valid_ingress,
                           bool is_known_client,
                           ReadyFilterCounters *counters)
{
    if (counters) increment_counter(&counters->received);

    if (envelope.source_node == 0)
    {
        if (counters) increment_counter(&counters->rejected.invalid_source);
        return ReadyDecision::ignore_invalid_source;
    }

    if (!is_valid_ingress)
    {
        if (counters) increment_counter(&counters->rejected.wrong_ingress_or_link_domain);
        return ReadyDecision::ignore_wrong_ingress_or_link_domain;
    }

    if (!is_known_client)
    {
        if (counters) increment_counter(&counters->rejected.unknown_client);
        return ReadyDecision::ignore_unknown_client;
    }

    if (counters) increment_counter(&counters->accepted);
    return ReadyDecision::accept;
}

