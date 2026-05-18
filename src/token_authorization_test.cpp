#include <catch2/catch_session.hpp>
#include <catch2/catch_test_macros.hpp>

#include "token_authorization.hpp"
#include "token_event_ipc.hpp"

TEST_CASE("TokenAuthorizationState 在无 Token 时默认禁止发送")
{
    TokenAuthorizationState state;

    REQUIRE_FALSE(state.is_authorized(1000));
    REQUIRE(state.counters().accepted_events == 0);
    REQUIRE(state.counters().rejected_events == 0);
    REQUIRE(state.counters().authorized_sends == 0);
    REQUIRE(state.counters().denied_sends == 0);
}

TEST_CASE("TokenAuthorizationState 收到合法授权事件后允许发送")
{
    TokenAuthorizationState state;
    TokenAuthorizationEvent event = {};
    event.node_id = 7;
    event.sequence = 42;
    event.duration_ms = 150;
    event.expires_at_ms = 1200;

    state.apply_event(event);

    REQUIRE(state.is_authorized(1000));
}

TEST_CASE("TokenAuthorizationState 在授权到期后自动失效")
{
    TokenAuthorizationState state;
    TokenAuthorizationEvent event = {};
    event.node_id = 7;
    event.sequence = 42;
    event.duration_ms = 150;
    event.expires_at_ms = 1200;

    state.apply_event(event);

    REQUIRE(state.is_authorized(1199));
    REQUIRE_FALSE(state.is_authorized(1200));
    REQUIRE_FALSE(state.is_authorized(1300));
}

TEST_CASE("TokenAuthorizationState 使用新 Token 替换授权窗口")
{
    TokenAuthorizationState state;

    TokenAuthorizationEvent first = {};
    first.node_id = 7;
    first.sequence = 42;
    first.duration_ms = 150;
    first.expires_at_ms = 1200;
    state.apply_event(first);

    TokenAuthorizationEvent second = {};
    second.node_id = 7;
    second.sequence = 43;
    second.duration_ms = 300;
    second.expires_at_ms = 1500;
    state.apply_event(second);

    REQUIRE(state.is_authorized(1400));
    REQUIRE_FALSE(state.is_authorized(1500));
}

TEST_CASE("TokenAuthorizationState 忽略非法事件并保持 fail-closed")
{
    TokenAuthorizationState state;

    TokenAuthorizationEvent invalid = {};
    invalid.node_id = 7;
    invalid.sequence = 42;
    invalid.duration_ms = 150;
    invalid.expires_at_ms = 0;
    state.apply_event(invalid);

    REQUIRE_FALSE(state.is_authorized(1000));
    REQUIRE(state.counters().accepted_events == 0);
    REQUIRE(state.counters().rejected_events == 1);
    REQUIRE(state.counters().authorized_sends == 0);
    REQUIRE(state.counters().denied_sends == 0);
}

int main(int argc, char *argv[])
{
    Catch::Session session;
    return session.run(argc, argv);
}
