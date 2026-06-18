#include <catch2/catch_session.hpp>
#include <catch2/catch_test_macros.hpp>

#include <stdint.h>
#include <string.h>
#include "control_envelope.hpp"

#include "wifibroadcast.hpp"

namespace {

wcontrol_envelope_hdr_t make_ready_packet(uint8_t source_node)
{
    wcontrol_envelope_hdr_t packet = {};
    packet.packet_type = WFB_PACKET_CONTROL;
    packet.magic = htobe16(WFB_CONTROL_MAGIC);
    packet.version = WFB_CONTROL_VERSION;
    packet.control_type = WFB_CONTROL_TYPE_READY;
    packet.source_node = source_node;
    packet.target_node = 0; // Don't care
    packet.sequence = htobe64(0); // Don't care
    return packet;
}

struct TestGrantPacket {
    wcontrol_envelope_hdr_t hdr;
    wcontrol_grant_payload_t payload;
} __attribute__((packed));

TestGrantPacket make_grant_packet(uint8_t source_node,
                                  uint8_t target_node,
                                  uint64_t sequence,
                                  uint32_t duration_ms)
{
    TestGrantPacket packet = {};
    packet.hdr.packet_type = WFB_PACKET_CONTROL;
    packet.hdr.magic = htobe16(WFB_CONTROL_MAGIC);
    packet.hdr.version = WFB_CONTROL_VERSION;
    packet.hdr.control_type = WFB_CONTROL_TYPE_GRANT;
    packet.hdr.source_node = source_node;
    packet.hdr.target_node = target_node;
    packet.hdr.sequence = htobe64(sequence);
    packet.payload.duration_ms = htobe32(duration_ms);
    return packet;
}

TEST_CASE("parse_control_envelope returns ok for valid GRANT packet")
{
    TestGrantPacket packet = make_grant_packet(7, 3, 42, 1500);
    ControlEnvelopeView view = {};

    ControlEnvelopeParseStatus status = parse_control_envelope(
        reinterpret_cast<const uint8_t *>(&packet),
        sizeof(packet),
        1000, // now_ms
        &view);

    REQUIRE(status == ControlEnvelopeParseStatus::ok);
    REQUIRE(view.control_type == WFB_CONTROL_TYPE_GRANT);
    REQUIRE(view.source_node == 7);
    REQUIRE(view.target_node == 3);
    REQUIRE(view.sequence == 42);
    REQUIRE(view.grant_duration_ms == 1500);
    REQUIRE(view.grant_expires_at_ms == 2500);
}

TEST_CASE("parse_control_envelope returns ok for valid READY packet")
{
    wcontrol_envelope_hdr_t packet = make_ready_packet(7);
    ControlEnvelopeView view = {};

    ControlEnvelopeParseStatus status = parse_control_envelope(
        reinterpret_cast<const uint8_t *>(&packet),
        sizeof(packet),
        1000,
        &view);

    REQUIRE(status == ControlEnvelopeParseStatus::ok);
    REQUIRE(view.control_type == WFB_CONTROL_TYPE_READY);
    REQUIRE(view.source_node == 7);
}

TEST_CASE("filter_grant ignores wrong target")
{
    ControlEnvelopeView view = {};
    view.control_type = WFB_CONTROL_TYPE_GRANT;
    view.source_node = 1;
    view.target_node = 2; // Target is 2
    view.sequence = 1;
    view.grant_duration_ms = 100;
    view.grant_expires_at_ms = 1100;

    GrantFilterState state = {};
    GrantFilterCounters counters = {};

    GrantDecision decision = filter_grant(view, true, 3, 1000, &state, &counters);

    REQUIRE(decision == GrantDecision::ignore_wrong_target);
    REQUIRE(counters.ignored_wrong_target == 1);
}

TEST_CASE("filter_grant ignores expired")
{
    ControlEnvelopeView view = {};
    view.control_type = WFB_CONTROL_TYPE_GRANT;
    view.source_node = 1;
    view.target_node = 3;
    view.sequence = 1;
    view.grant_duration_ms = 100;
    view.grant_expires_at_ms = 1100; // Expired at 1100

    GrantFilterState state = {};
    GrantFilterCounters counters = {};

    // now_ms is 1200
    GrantDecision decision = filter_grant(view, true, 3, 1200, &state, &counters);

    REQUIRE(decision == GrantDecision::ignore_expired);
    REQUIRE(counters.ignored_expired == 1);
}

TEST_CASE("filter_grant ignores duplicate and stale sequences")
{
    ControlEnvelopeView view = {};
    view.control_type = WFB_CONTROL_TYPE_GRANT;
    view.source_node = 1;
    view.target_node = 3;
    view.sequence = 10;
    view.grant_duration_ms = 100;
    view.grant_expires_at_ms = 1100;

    GrantFilterState state = {};
    state.has_last_sequence = true;
    state.last_sequence = 10;
    GrantFilterCounters counters = {};

    GrantDecision decision = filter_grant(view, true, 3, 1000, &state, &counters);
    REQUIRE(decision == GrantDecision::ignore_duplicate_sequence);
    REQUIRE(counters.ignored_duplicate_sequence == 1);

    view.sequence = 5;
    decision = filter_grant(view, true, 3, 1000, &state, &counters);
    REQUIRE(decision == GrantDecision::ignore_stale_sequence);
    REQUIRE(counters.ignored_stale_sequence == 1);
}

TEST_CASE("filter_ready handles legitimate checks")
{
    ControlEnvelopeView view = {};
    view.control_type = WFB_CONTROL_TYPE_READY;
    view.source_node = 7;

    ReadyFilterCounters counters = {};

    // 1. Legitimate (all good)
    ReadyDecision decision = filter_ready(view, true, true, &counters);
    REQUIRE(decision == ReadyDecision::accept);
    REQUIRE(counters.accepted == 1);

    // 2. Invalid source (source_node == 0)
    view.source_node = 0;
    decision = filter_ready(view, true, true, &counters);
    REQUIRE(decision == ReadyDecision::ignore_invalid_source);
    REQUIRE(counters.rejected.invalid_source == 1);
    view.source_node = 7; // restore

    // 3. Wrong ingress
    decision = filter_ready(view, false, true, &counters);
    REQUIRE(decision == ReadyDecision::ignore_wrong_ingress_or_link_domain);
    REQUIRE(counters.rejected.wrong_ingress_or_link_domain == 1);

    // 4. Unknown client
    decision = filter_ready(view, true, false, &counters);
    REQUIRE(decision == ReadyDecision::ignore_unknown_client);
    REQUIRE(counters.rejected.unknown_client == 1);
}

}

