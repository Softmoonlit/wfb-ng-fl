#include <catch2/catch_session.hpp>
#include <catch2/catch_test_macros.hpp>

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

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

class ScopedFd {
public:
    explicit ScopedFd(int fd = -1) : fd_(fd) {}

    ~ScopedFd()
    {
        if (fd_ >= 0)
        {
            close(fd_);
        }
    }

    int get() const { return fd_; }

private:
    int fd_;
};

class ScopedUdpReceiver {
public:
    ScopedUdpReceiver() : fd_(socket(AF_INET, SOCK_DGRAM, 0))
    {
        REQUIRE(fd_.get() >= 0);

        sockaddr_in addr = {};
        addr.sin_family = AF_INET;
        addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        addr.sin_port = 0;
        REQUIRE(bind(fd_.get(), reinterpret_cast<const sockaddr *>(&addr), sizeof(addr)) == 0);

        socklen_t addr_len = sizeof(addr);
        REQUIRE(getsockname(fd_.get(), reinterpret_cast<sockaddr *>(&addr), &addr_len) == 0);
        port_ = ntohs(addr.sin_port);

        timeval timeout = {};
        timeout.tv_sec = 1;
        REQUIRE(setsockopt(fd_.get(), SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)) == 0);
    }

    int port() const { return port_; }

    std::vector<uint8_t> recv_packet() const
    {
        std::vector<uint8_t> packet(sizeof(wrxfwd_t) + MAX_FORWARDER_PACKET_SIZE, 0);
        const ssize_t received = recv(fd_.get(), packet.data(), packet.size(), 0);
        REQUIRE(received > 0);
        packet.resize(static_cast<size_t>(received));
        return packet;
    }

private:
    ScopedFd fd_;
    int port_ = 0;
};

const uint8_t *udp_forwarded_air_packet_data(const std::vector<uint8_t> &packet)
{
    REQUIRE(packet.size() >= sizeof(wrxfwd_t) + sizeof(wblock_hdr_t) + sizeof(wpacket_hdr_t));
    return packet.data() + sizeof(wrxfwd_t);
}

uint64_t packet_data_nonce(const std::vector<uint8_t> &packet)
{
    const wblock_hdr_t *block_hdr = reinterpret_cast<const wblock_hdr_t *>(udp_forwarded_air_packet_data(packet));
    return be64toh(block_hdr->data_nonce);
}

std::vector<uint8_t> packet_payload(const std::vector<uint8_t> &packet)
{
    const uint8_t *air_packet = udp_forwarded_air_packet_data(packet);
    const wpacket_hdr_t *packet_hdr = reinterpret_cast<const wpacket_hdr_t *>(air_packet + sizeof(wblock_hdr_t));
    const uint16_t payload_size = be16toh(packet_hdr->packet_size);
    REQUIRE(packet.size() == sizeof(wrxfwd_t) + sizeof(wblock_hdr_t) + sizeof(wpacket_hdr_t) + payload_size);
    return std::vector<uint8_t>(air_packet + sizeof(wblock_hdr_t) + sizeof(wpacket_hdr_t),
                                air_packet + sizeof(wblock_hdr_t) + sizeof(wpacket_hdr_t) + payload_size);
}

} // namespace

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

TEST_CASE("v6 client uplink data_nonce 编码包含 source_node 与本地 block 索引")
{
    ScopedUdpReceiver receiver;
    std::unique_ptr<AirTransmitter> transmitter(new AirTransmitter("127.0.0.1", receiver.port(), 0, 23));
    const std::vector<uint8_t> first_payload = {0x10, 0x11, 0x12};
    const std::vector<uint8_t> second_payload = {0x20, 0x21};

    transmitter->send_data(first_payload.data(), first_payload.size());
    transmitter->send_data(second_payload.data(), second_payload.size());

    const std::vector<uint8_t> first_packet = receiver.recv_packet();
    const std::vector<uint8_t> second_packet = receiver.recv_packet();
    const uint64_t first_nonce = packet_data_nonce(first_packet);
    const uint64_t second_nonce = packet_data_nonce(second_packet);

    REQUIRE(packet_payload(first_packet) == first_payload);
    REQUIRE(packet_payload(second_packet) == second_payload);
    REQUIRE(data_nonce_source_node(first_nonce) == 23);
    REQUIRE(data_nonce_source_local_block_idx(first_nonce) == 0);
    REQUIRE(data_nonce_fragment_idx(first_nonce) == 0);
    REQUIRE(data_nonce_source_node(second_nonce) == 23);
    REQUIRE(data_nonce_source_local_block_idx(second_nonce) == 1);
    REQUIRE(data_nonce_fragment_idx(second_nonce) == 0);
}

int main(int argc, char **argv)
{
    Catch::Session session;
    return session.run(argc, argv);
}
