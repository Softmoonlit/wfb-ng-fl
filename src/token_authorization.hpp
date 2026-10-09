#pragma once

#include <stdint.h>
#include <set>

#include "token_event_ipc.hpp"

struct TokenAuthorizationCounters {
    uint32_t accepted_events = 0;
    uint32_t rejected_events = 0;
    uint32_t rejected_retired_sessions = 0;
    uint32_t rejected_stale_sequences = 0;
    uint32_t authorized_sends = 0;
    uint32_t denied_sends = 0;
};

class TokenAuthorizationState
{
public:
    bool apply_event(const TokenAuthorizationEvent &event, uint64_t now_ms);
    bool is_authorized(uint64_t now_ms) const;
    TokenAuthorizationCounters &counters() { return counters_; }
    const TokenAuthorizationCounters &counters() const { return counters_; }

private:
    bool has_session_ = false;
    uint64_t session_id_ = 0;
    bool has_last_sequence_ = false;
    uint64_t last_sequence_ = 0;
    std::set<uint64_t> retired_sessions_;
    bool has_authorization_ = false;
    uint64_t expires_at_ms_ = 0;
    TokenAuthorizationCounters counters_ = {};
};
