#include <catch2/catch_session.hpp>
#include <catch2/catch_test_macros.hpp>

#include <stdint.h>

#include "token_control_packet.hpp"

TEST_CASE("filter_token_control_packet 接受发给本节点的新 Token")
{
    TokenControlPacketView packet = {};
    packet.node_id = 7;
    packet.sequence = 42;
    packet.duration_ms = 100;
    packet.expires_at_ms = 5100;

    TokenControlFilterState state = {};
    TokenControlFilterCounters counters = {};

    TokenControlDecision decision = filter_token_control_packet(packet, 7, 5000, &state, &counters);

    REQUIRE(decision == TokenControlDecision::accept);
    REQUIRE(state.has_last_sequence);
    REQUIRE(state.last_sequence == 42);
    REQUIRE(counters.received == 1);
    REQUIRE(counters.accepted == 1);
    REQUIRE(counters.ignored_wrong_node == 0);
    REQUIRE(counters.ignored_duplicate_sequence == 0);
    REQUIRE(counters.ignored_stale_sequence == 0);
    REQUIRE(counters.ignored_expired == 0);
}

TEST_CASE("filter_token_control_packet 忽略其他节点 Token")
{
    TokenControlPacketView packet = {};
    packet.node_id = 8;
    packet.sequence = 42;
    packet.duration_ms = 100;
    packet.expires_at_ms = 5100;

    TokenControlFilterState state = {};
    TokenControlFilterCounters counters = {};

    TokenControlDecision decision = filter_token_control_packet(packet, 7, 5000, &state, &counters);

    REQUIRE(decision == TokenControlDecision::ignore_wrong_node);
    REQUIRE_FALSE(state.has_last_sequence);
    REQUIRE(counters.received == 1);
    REQUIRE(counters.accepted == 0);
    REQUIRE(counters.ignored_wrong_node == 1);
    REQUIRE(counters.ignored_duplicate_sequence == 0);
    REQUIRE(counters.ignored_stale_sequence == 0);
    REQUIRE(counters.ignored_expired == 0);
}

TEST_CASE("filter_token_control_packet 忽略重复 sequence Token")
{
    TokenControlPacketView packet = {};
    packet.node_id = 7;
    packet.sequence = 42;
    packet.duration_ms = 100;
    packet.expires_at_ms = 5100;

    TokenControlFilterState state = {};
    state.has_last_sequence = true;
    state.last_sequence = 42;
    TokenControlFilterCounters counters = {};

    TokenControlDecision decision = filter_token_control_packet(packet, 7, 5000, &state, &counters);

    REQUIRE(decision == TokenControlDecision::ignore_duplicate_sequence);
    REQUIRE(state.has_last_sequence);
    REQUIRE(state.last_sequence == 42);
    REQUIRE(counters.received == 1);
    REQUIRE(counters.accepted == 0);
    REQUIRE(counters.ignored_wrong_node == 0);
    REQUIRE(counters.ignored_duplicate_sequence == 1);
    REQUIRE(counters.ignored_stale_sequence == 0);
    REQUIRE(counters.ignored_expired == 0);
}

TEST_CASE("filter_token_control_packet 忽略旧 sequence Token")
{
    TokenControlPacketView packet = {};
    packet.node_id = 7;
    packet.sequence = 41;
    packet.duration_ms = 100;
    packet.expires_at_ms = 5100;

    TokenControlFilterState state = {};
    state.has_last_sequence = true;
    state.last_sequence = 42;
    TokenControlFilterCounters counters = {};

    TokenControlDecision decision = filter_token_control_packet(packet, 7, 5000, &state, &counters);

    REQUIRE(decision == TokenControlDecision::ignore_stale_sequence);
    REQUIRE(state.has_last_sequence);
    REQUIRE(state.last_sequence == 42);
    REQUIRE(counters.received == 1);
    REQUIRE(counters.accepted == 0);
    REQUIRE(counters.ignored_wrong_node == 0);
    REQUIRE(counters.ignored_duplicate_sequence == 0);
    REQUIRE(counters.ignored_stale_sequence == 1);
    REQUIRE(counters.ignored_expired == 0);
}

TEST_CASE("filter_token_control_packet 忽略过期 Token")
{
    TokenControlPacketView packet = {};
    packet.node_id = 7;
    packet.sequence = 42;
    packet.duration_ms = 100;
    packet.expires_at_ms = 5000;

    TokenControlFilterState state = {};
    TokenControlFilterCounters counters = {};

    TokenControlDecision decision = filter_token_control_packet(packet, 7, 5000, &state, &counters);

    REQUIRE(decision == TokenControlDecision::ignore_expired);
    REQUIRE_FALSE(state.has_last_sequence);
    REQUIRE(counters.received == 1);
    REQUIRE(counters.accepted == 0);
    REQUIRE(counters.ignored_wrong_node == 0);
    REQUIRE(counters.ignored_duplicate_sequence == 0);
    REQUIRE(counters.ignored_stale_sequence == 0);
    REQUIRE(counters.ignored_expired == 1);
}

int main(int argc, char *argv[])
{
    Catch::Session session;
    return session.run(argc, argv);
}
