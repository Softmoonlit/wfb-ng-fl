#include "token_authorization.hpp"

namespace {

bool is_valid_event(const TokenAuthorizationEvent &event)
{
    return event.expires_at_ms > 0;
}

}

void TokenAuthorizationState::apply_event(const TokenAuthorizationEvent &event)
{
    if (!is_valid_event(event))
    {
        counters_.rejected_events += 1;
        return;
    }

    expires_at_ms_ = event.expires_at_ms;
    has_authorization_ = true;
    counters_.accepted_events += 1;
}

bool TokenAuthorizationState::is_authorized(uint64_t now_ms) const
{
    return has_authorization_ && now_ms < expires_at_ms_;
}
