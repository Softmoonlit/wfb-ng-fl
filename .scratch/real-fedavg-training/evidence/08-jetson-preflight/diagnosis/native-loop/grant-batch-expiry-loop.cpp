#include <cassert>
#include <endian.h>
#include <iostream>
#include <vector>

#include "control_envelope.hpp"
#include "wifibroadcast.hpp"

static std::vector<uint8_t> grant_frame(uint64_t session, uint64_t sequence,
                                        uint32_t duration_ms) {
    std::vector<uint8_t> frame(
        sizeof(wcontrol_envelope_hdr_t) + sizeof(wcontrol_grant_payload_t), 0);
    auto *header = reinterpret_cast<wcontrol_envelope_hdr_t *>(frame.data());
    header->packet_type = WFB_PACKET_CONTROL;
    header->magic = htobe16(WFB_CONTROL_MAGIC);
    header->version = WFB_CONTROL_VERSION;
    header->control_type = WFB_CONTROL_TYPE_GRANT;
    header->source_node = 255;
    header->target_node = 2;
    header->sequence = htobe64(sequence);
    header->session_id = htobe64(session);
    auto *payload = reinterpret_cast<wcontrol_grant_payload_t *>(
        frame.data() + sizeof(wcontrol_envelope_hdr_t));
    payload->duration_ms = htobe32(duration_ms);
    return frame;
}

int main() {
    const uint64_t arrival_ms = 1000;
    const uint64_t processing_ms = arrival_ms + 121;  // > 120ms duration.
    auto frame = grant_frame(77, 1, 120);
    ControlEnvelopeView view = {};
    const auto status = parse_control_envelope(
        frame.data(), frame.size(), processing_ms, &view);
    assert(status == ControlEnvelopeParseStatus::ok);
    assert(view.grant_expires_at_ms == processing_ms + 120);

    GrantFilterState filter_state = {};
    GrantFilterCounters counters = {};
    const auto decision = filter_grant(
        view, true, 2, processing_ms, &filter_state, &counters);
    assert(decision == GrantDecision::accept);
    assert(counters.accepted == 1);
    assert(counters.ignored_expired == 0);

    std::cout << "arrival_ms=" << arrival_ms
              << " processing_ms=" << processing_ms
              << "batch_delay_ms=" << (processing_ms - arrival_ms)
              << "grant_expires_at_ms=" << view.grant_expires_at_ms
              << "decision=accept\n";
    std::cout << "H5_BATCH_DELAY_EXPIRES_GRANT=falsified_by_current_parser\n";
}
