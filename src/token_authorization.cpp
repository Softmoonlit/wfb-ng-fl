#include "token_authorization.hpp"

namespace {

bool is_valid_event(const TokenAuthorizationEvent &event)
{
    return event.expires_at_ms > 0;
}

}

bool TokenAuthorizationState::apply_event(const TokenAuthorizationEvent &event, uint64_t now_ms)
{
    if (!is_valid_event(event) || event.expires_at_ms <= now_ms || event.session_id == 0)
    {
        counters_.rejected_events += 1;
        return false;
    }

    if (!has_session_)
    {
        has_session_ = true;
        session_id_ = event.session_id;
        has_last_sequence_ = false;
    }
    else if (session_id_ != event.session_id)
    {
        if (retired_sessions_.count(event.session_id) != 0)
        {
            counters_.rejected_events += 1;
            counters_.rejected_retired_sessions += 1;
            return false;
        }

        retired_sessions_.insert(session_id_);
        session_id_ = event.session_id;
        has_last_sequence_ = false;
        has_authorization_ = false;
        expires_at_ms_ = 0;
    }

    if (has_last_sequence_ && event.sequence <= last_sequence_)
    {
        counters_.rejected_events += 1;
        counters_.rejected_stale_sequences += 1;
        return false;
    }

    has_last_sequence_ = true;
    last_sequence_ = event.sequence;
    expires_at_ms_ = event.expires_at_ms;
    has_authorization_ = true;
    counters_.accepted_events += 1;
    return true;
}

bool TokenAuthorizationState::is_authorized(uint64_t now_ms) const
{
    return has_authorization_ && now_ms < expires_at_ms_;
}
