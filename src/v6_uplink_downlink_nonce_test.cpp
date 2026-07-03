#include <catch2/catch_session.hpp>
#include <catch2/catch_test_macros.hpp>

#include <arpa/inet.h>

#include <memory>
#include <vector>
#include <cstdint>

#define __WFB_V6_UPLINK_TEST__
#include "v6_uplink.cpp"

namespace {


ClientTarget make_target(uint8_t node_id, const char *tun_ip)
{
    ClientTarget target;
    target.node_id = node_id;
    target.tun_ipv4 = parse_ipv4(tun_ip, "tun_ip");
    target.host = "127.0.0.1";
    target.host_ipv4 = parse_ipv4(target.host, "host");
    target.port = 10000 + node_id;
    return target;
}

std::vector<ClientTarget> make_two_targets()
{
    std::vector<ClientTarget> targets;
    targets.push_back(make_target(11, "10.6.0.11"));
    targets.push_back(make_target(12, "10.6.0.12"));
    return targets;
}

}

TEST_CASE("raw-air server 下多个 client target 共享同一个下行 nonce 序列")
{
    std::vector<ClientTarget> targets = make_two_targets();
    int raw_factory_calls = 0;
    int udp_factory_calls = 0;

    initialize_client_target_transmitters(
        targets,
        true,
        [&]() {
            raw_factory_calls += 1;
            std::shared_ptr<int> token(new int(raw_factory_calls));
            return std::shared_ptr<AirTransmitter>(token, reinterpret_cast<AirTransmitter *>(token.get()));
        },
        [&](const ClientTarget &) {
            udp_factory_calls += 1;
            std::shared_ptr<int> token(new int(udp_factory_calls));
            return std::shared_ptr<AirTransmitter>(token, reinterpret_cast<AirTransmitter *>(token.get()));
        });

    REQUIRE(raw_factory_calls == 1);
    REQUIRE(udp_factory_calls == 0);
    REQUIRE(targets[0].transmitter != NULL);

    // raw-air 是同一广播域：两个目标必须绑定同一个发送器实例，避免各自从 block_idx=0 开始。
    REQUIRE(targets[0].transmitter == targets[1].transmitter);
}

TEST_CASE("UDP server 下每个 client target 保持独立下行发送器")
{
    std::vector<ClientTarget> targets = make_two_targets();
    int raw_factory_calls = 0;
    std::vector<uint8_t> udp_factory_nodes;

    initialize_client_target_transmitters(
        targets,
        false,
        [&]() {
            raw_factory_calls += 1;
            std::shared_ptr<int> token(new int(raw_factory_calls));
            return std::shared_ptr<AirTransmitter>(token, reinterpret_cast<AirTransmitter *>(token.get()));
        },
        [&](const ClientTarget &target) {
            udp_factory_nodes.push_back(target.node_id);
            std::shared_ptr<int> token(new int(target.node_id));
            return std::shared_ptr<AirTransmitter>(token, reinterpret_cast<AirTransmitter *>(token.get()));
        });

    REQUIRE(raw_factory_calls == 0);
    REQUIRE(udp_factory_nodes == std::vector<uint8_t>{11, 12});
    REQUIRE(targets[0].transmitter != NULL);
    REQUIRE(targets[1].transmitter != NULL);
    REQUIRE(targets[0].transmitter != targets[1].transmitter);
}


int main(int argc, char **argv)
{
    Catch::Session session;
    return session.run(argc, argv);
}
