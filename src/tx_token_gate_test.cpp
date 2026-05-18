#include <catch2/catch_session.hpp>
#include <catch2/catch_test_macros.hpp>

#include <stdint.h>

#include "token_authorization.hpp"
#include "tx_token_gate.hpp"

TEST_CASE("run_when_authorized 在未授权时不执行发送动作")
{
    TokenAuthorizationState state;
    bool called = false;

    const bool sent = run_when_authorized(&state, 1000, [&]() {
        called = true;
        return true;
    });

    REQUIRE_FALSE(sent);
    REQUIRE_FALSE(called);
    REQUIRE(state.counters().authorized_sends == 0);
    REQUIRE(state.counters().denied_sends == 1);
}

TEST_CASE("run_when_authorized 在已授权时执行发送动作")
{
    TokenAuthorizationState state;
    TokenAuthorizationEvent event = {};
    event.node_id = 7;
    event.sequence = 42;
    event.duration_ms = 150;
    event.expires_at_ms = 1200;
    state.apply_event(event);

    bool called = false;
    const bool sent = run_when_authorized(&state, 1000, [&]() {
        called = true;
        return true;
    });

    REQUIRE(sent);
    REQUIRE(called);
    REQUIRE(state.counters().authorized_sends == 1);
}

TEST_CASE("run_when_authorized 在授权过期后停止执行发送动作")
{
    TokenAuthorizationState state;
    TokenAuthorizationEvent event = {};
    event.node_id = 7;
    event.sequence = 42;
    event.duration_ms = 150;
    event.expires_at_ms = 1200;
    state.apply_event(event);

    bool called = false;
    const bool sent = run_when_authorized(&state, 1200, [&]() {
        called = true;
        return true;
    });

    REQUIRE_FALSE(sent);
    REQUIRE_FALSE(called);
}

int main(int argc, char *argv[])
{
    Catch::Session session;
    return session.run(argc, argv);
}
