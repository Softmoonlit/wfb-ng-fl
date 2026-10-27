#include <cassert>
#include <cstdint>
#include <iostream>

#include "token_authorization.hpp"
#include "token_event_ipc.hpp"
#include "v6_uplink_queue.hpp"

// Semantic seam for run_client's pending-packet branch. It uses the real
// authorization state and real TUN queue, but does not open TUN/radio/sockets.
struct FecProbe {
    uint64_t deadline_ms = 0;
    unsigned flush_count = 0;
    unsigned send_count = 0;

    void send_data(uint64_t now_ms) {
        ++send_count;
        deadline_ms = now_ms + 2;
    }

    bool flush_data_block_if_due(uint64_t now_ms) {
        if (deadline_ms == 0 || now_ms < deadline_ms) return false;
        ++flush_count;
        deadline_ms = 0;
        return true;
    }
};

struct LoopStats {
    unsigned denied = 0;
    unsigned authorized = 0;
    unsigned zero_timeout_polls = 0;
};

static void run_client_branch_once(FixedCapacityTunReadQueue &queue,
                                   FecProbe &fec,
                                   const TokenAuthorizationState &authorization,
                                   uint64_t now_ms,
                                   LoopStats *stats) {
    // Mirrors v6_uplink.cpp:1825-1847. The production code computes poll()
    // before this branch; a due deadline therefore produces timeout=0.
    const uint64_t next_wakeup = fec.deadline_ms;
    const int timeout_ms = next_wakeup > now_ms
        ? static_cast<int>(next_wakeup - now_ms) : 0;
    if (timeout_ms == 0) ++stats->zero_timeout_polls;

    const QueuedTunPacket *pending_packet = queue.front();
    if (pending_packet == NULL) {
        fec.flush_data_block_if_due(now_ms);
        return;
    }

    if (!authorization.is_authorized(now_ms)) {
        ++stats->denied;
        // Production calls maybe_send_ready() and then continues.
        return;
    }

    fec.send_data(now_ms);
    ++stats->authorized;
    assert(queue.pop_front());
}

int main() {
    FixedCapacityTunReadQueue queue(4096, 1024, 8);
    const uint8_t payload[] = {0x45, 0, 0, 1};
    assert(queue.push(payload, sizeof(payload)));

    TokenAuthorizationState authorization;
    TokenAuthorizationEvent grant = {};
    grant.node_id = 2;
    grant.session_id = 77;
    grant.sequence = 1;
    grant.duration_ms = 5;
    grant.expires_at_ms = 5;
    assert(authorization.apply_event(grant, 0));
    assert(!authorization.is_authorized(10));

    FecProbe fec;
    // A prior authorized packet opened a FEC block. The next packet remains
    // queued after authorization expires, matching the suspected transition.
    fec.send_data(0);
    assert(fec.deadline_ms == 2);

    LoopStats stats;
    for (uint64_t now = 10; now < 110; ++now)
        run_client_branch_once(queue, fec, authorization, now, &stats);

    std::cout << "pending=" << queue.queued_packets()
              << " denied=" << stats.denied
              << " authorized=" << stats.authorized
              << " zero_timeout_polls=" << stats.zero_timeout_polls
              << " fec_flushes=" << fec.flush_count << '\n';

    // This is intentionally red against the current branch. It asserts the
    // desired invariant for a later fix: a due block cannot be starved by a
    // later unauthorized pending packet.
    assert(fec.flush_count > 0 &&
           "RED: pending unauthorized packet starves due FEC flush");
}
