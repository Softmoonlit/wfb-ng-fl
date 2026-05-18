#pragma once

#include <stdint.h>

#include "token_event_ipc.hpp"

struct TokenAuthorizationCounters {
    uint32_t accepted_events = 0;
    uint32_t rejected_events = 0;
    uint32_t authorized_sends = 0;
    uint32_t denied_sends = 0;
};

class TokenAuthorizationState
{
public:
    void apply_event(const TokenAuthorizationEvent &event);
    bool is_authorized(uint64_t now_ms) const;
    TokenAuthorizationCounters &counters() { return counters_; }
    const TokenAuthorizationCounters &counters() const { return counters_; }

private:
    bool has_authorization_ = false;
    uint64_t expires_at_ms_ = 0;
    TokenAuthorizationCounters counters_ = {};
};
