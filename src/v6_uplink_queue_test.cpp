#include <catch2/catch_session.hpp>
#include <catch2/catch_test_macros.hpp>

#include <string.h>

#include <vector>

#include "v6_uplink_queue.hpp"

namespace {

std::vector<uint8_t> make_packet(size_t size, uint8_t seed)
{
    std::vector<uint8_t> packet(size, seed);
    return packet;
}

}

TEST_CASE("queued_bytes 双阈值触发停读与恢复")
{
    FixedCapacityTunReadQueue queue(100, 40, 8);
    const std::vector<uint8_t> first = make_packet(60, 0x11);
    const std::vector<uint8_t> second = make_packet(50, 0x22);

    REQUIRE(queue.push(first.data(), first.size()));
    REQUIRE(queue.tun_read_enabled());
    REQUIRE(queue.push(second.data(), second.size()));

    REQUIRE_FALSE(queue.tun_read_enabled());
    REQUIRE(queue.current_pause_reason() == TUN_READ_PAUSE_QUEUED_BYTES_THRESHOLD);
    REQUIRE(queue.counters().tun_read_pause_total == 1);
    REQUIRE(queue.counters().tun_read_pause_total_by_queued_bytes_threshold == 1);
    REQUIRE(queue.counters().tun_read_pause_total_by_queued_packets_limit == 0);

    REQUIRE(queue.pop_front());
    REQUIRE_FALSE(queue.tun_read_enabled());
    REQUIRE(queue.queued_bytes() == 50);

    REQUIRE(queue.pop_front());
    REQUIRE(queue.tun_read_enabled());
    REQUIRE(queue.counters().tun_read_resume_total == 1);
}

TEST_CASE("queued_packets 硬上限独立触发停读")
{
    FixedCapacityTunReadQueue queue(4096, 2048, 2);
    const std::vector<uint8_t> first = make_packet(100, 0x33);
    const std::vector<uint8_t> second = make_packet(100, 0x44);

    REQUIRE(queue.push(first.data(), first.size()));
    REQUIRE(queue.push(second.data(), second.size()));

    REQUIRE(queue.full());
    REQUIRE_FALSE(queue.tun_read_enabled());
    REQUIRE(queue.current_pause_reason() == TUN_READ_PAUSE_QUEUED_PACKETS_LIMIT);
    REQUIRE(queue.counters().tun_read_pause_total == 1);
    REQUIRE(queue.counters().tun_read_pause_total_by_queued_bytes_threshold == 0);
    REQUIRE(queue.counters().tun_read_pause_total_by_queued_packets_limit == 1);

    REQUIRE(queue.pop_front());
    REQUIRE(queue.tun_read_enabled());
    REQUIRE(queue.counters().tun_read_resume_total == 1);
}

TEST_CASE("固定容量队列保持 FIFO 顺序")
{
    FixedCapacityTunReadQueue queue(4096, 2048, 4);
    const std::vector<uint8_t> first = make_packet(3, 0x51);
    const std::vector<uint8_t> second = make_packet(2, 0x62);

    REQUIRE(queue.push(first.data(), first.size()));
    REQUIRE(queue.push(second.data(), second.size()));

    const QueuedTunPacket *front = queue.front();
    REQUIRE(front != NULL);
    REQUIRE(front->size == first.size());
    REQUIRE(memcmp(front->bytes, first.data(), first.size()) == 0);

    REQUIRE(queue.pop_front());
    front = queue.front();
    REQUIRE(front != NULL);
    REQUIRE(front->size == second.size());
    REQUIRE(memcmp(front->bytes, second.data(), second.size()) == 0);
}

int main(int argc, char **argv)
{
    Catch::Session session;
    return session.run(argc, argv);
}
