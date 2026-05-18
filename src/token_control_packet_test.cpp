#include <catch2/catch_session.hpp>
#include <catch2/catch_test_macros.hpp>

#include <stdint.h>
#include <string.h>

#include "token_control_packet.hpp"
#include "wifibroadcast.hpp"

namespace {

wtoken_control_hdr_t make_token_packet(uint8_t node_id,
                                       uint64_t sequence,
                                       uint32_t duration_ms)
{
    wtoken_control_hdr_t packet = {};
    packet.packet_type = WFB_PACKET_TOKEN_CONTROL;
    packet.magic = htobe16(WFB_TOKEN_CONTROL_MAGIC);
    packet.version = WFB_TOKEN_CONTROL_VERSION;
    packet.flags = 0;
    packet.node_id = node_id;
    packet.reserved = 0;
    packet.sequence = htobe64(sequence);
    packet.duration_ms = htobe32(duration_ms);
    return packet;
}

}

TEST_CASE("parse_token_control_packet 解析合法控制包")
{
    wtoken_control_hdr_t packet = make_token_packet(7, 42, 1500);
    TokenControlPacketView view = {};

    TokenControlParseStatus status = parse_token_control_packet(
        reinterpret_cast<const uint8_t *>(&packet),
        sizeof(packet),
        10000,
        &view);

    REQUIRE(status == TokenControlParseStatus::ok);
    REQUIRE(view.node_id == 7);
    REQUIRE(view.sequence == 42);
    REQUIRE(view.duration_ms == 1500);
    REQUIRE(view.expires_at_ms == 11500);
}

TEST_CASE("parse_token_control_packet 不误识别普通数据包")
{
    uint8_t packet[sizeof(wblock_hdr_t)] = {};
    packet[0] = WFB_PACKET_DATA;
    TokenControlPacketView view = {};

    TokenControlParseStatus status = parse_token_control_packet(
        packet,
        sizeof(packet),
        1,
        &view);

    REQUIRE(status == TokenControlParseStatus::not_token_packet);
}

TEST_CASE("parse_token_control_packet 拒绝非法长度")
{
    wtoken_control_hdr_t packet = make_token_packet(3, 9, 25);
    TokenControlPacketView view = {};

    TokenControlParseStatus status = parse_token_control_packet(
        reinterpret_cast<const uint8_t *>(&packet),
        sizeof(packet) - 1,
        1,
        &view);

    REQUIRE(status == TokenControlParseStatus::invalid_length);
}

TEST_CASE("parse_token_control_packet 拒绝非法 magic")
{
    wtoken_control_hdr_t packet = make_token_packet(3, 9, 25);
    packet.magic = htobe16(0x1234);
    TokenControlPacketView view = {};

    TokenControlParseStatus status = parse_token_control_packet(
        reinterpret_cast<const uint8_t *>(&packet),
        sizeof(packet),
        1,
        &view);

    REQUIRE(status == TokenControlParseStatus::invalid_magic);
}

TEST_CASE("parse_token_control_packet 拒绝非法 version")
{
    wtoken_control_hdr_t packet = make_token_packet(3, 9, 25);
    packet.version = WFB_TOKEN_CONTROL_VERSION + 1;
    TokenControlPacketView view = {};

    TokenControlParseStatus status = parse_token_control_packet(
        reinterpret_cast<const uint8_t *>(&packet),
        sizeof(packet),
        1,
        &view);

    REQUIRE(status == TokenControlParseStatus::invalid_version);
}

TEST_CASE("parse_token_control_packet 覆盖边界值与过期时间饱和")
{
    SECTION("允许最小与最大 node_id 和 sequence")
    {
        wtoken_control_hdr_t packet = make_token_packet(255, UINT64_MAX, 0);
        TokenControlPacketView view = {};

        TokenControlParseStatus status = parse_token_control_packet(
            reinterpret_cast<const uint8_t *>(&packet),
            sizeof(packet),
            9,
            &view);

        REQUIRE(status == TokenControlParseStatus::ok);
        REQUIRE(view.node_id == 255);
        REQUIRE(view.sequence == UINT64_MAX);
        REQUIRE(view.duration_ms == 0);
        REQUIRE(view.expires_at_ms == 9);
    }

    SECTION("过期时间溢出时饱和到 UINT64_MAX")
    {
        wtoken_control_hdr_t packet = make_token_packet(0, 0, UINT32_MAX);
        TokenControlPacketView view = {};

        TokenControlParseStatus status = parse_token_control_packet(
            reinterpret_cast<const uint8_t *>(&packet),
            sizeof(packet),
            UINT64_MAX - 10,
            &view);

        REQUIRE(status == TokenControlParseStatus::ok);
        REQUIRE(view.node_id == 0);
        REQUIRE(view.sequence == 0);
        REQUIRE(view.duration_ms == UINT32_MAX);
        REQUIRE(view.expires_at_ms == UINT64_MAX);
    }
}

int main(int argc, char *argv[])
{
    Catch::Session session;
    return session.run(argc, argv);
}
