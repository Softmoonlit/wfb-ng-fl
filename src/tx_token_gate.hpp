#pragma once

#include <functional>

#include "token_authorization.hpp"

bool run_when_authorized(const TokenAuthorizationState *state,
                         uint64_t now_ms,
                         const std::function<bool(void)> &action);
