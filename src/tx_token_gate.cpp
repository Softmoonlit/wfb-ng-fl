#include "tx_token_gate.hpp"

bool run_when_authorized(const TokenAuthorizationState *state,
                         uint64_t now_ms,
                         const std::function<bool(void)> &action)
{
    if (state != nullptr && !state->is_authorized(now_ms))
    {
        const_cast<TokenAuthorizationState *>(state)->counters().denied_sends += 1;
        return false;
    }

    const bool sent = action();
    if (state != nullptr && sent)
    {
        const_cast<TokenAuthorizationState *>(state)->counters().authorized_sends += 1;
    }
    return sent;
}
