#include <catch2/catch_session.hpp>
#include <catch2/catch_test_macros.hpp>

#include <sodium.h>

#include <sys/socket.h>
#include <sys/un.h>

#include <string>
#include <vector>

#include "rx.hpp"
#include "token_event_ipc.hpp"
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

std::string write_temp_keypair_file()
{
    char path[] = "/tmp/wfb-rx-test-key-XXXXXX";
    int fd = mkstemp(path);
    REQUIRE(fd >= 0);

    FILE *fp = fdopen(fd, "wb");
    REQUIRE(fp != nullptr);

    uint8_t rx_secretkey[crypto_box_SECRETKEYBYTES] = {};
    uint8_t tx_publickey[crypto_box_PUBLICKEYBYTES] = {};

    REQUIRE(fwrite(rx_secretkey, crypto_box_SECRETKEYBYTES, 1, fp) == 1);
    REQUIRE(fwrite(tx_publickey, crypto_box_PUBLICKEYBYTES, 1, fp) == 1);
    REQUIRE(fclose(fp) == 0);

    return std::string(path);
}

struct RecordingTokenControlListener : public TokenControlListener {
    std::vector<TokenControlPacketView> packets;

    void on_token_control(const TokenControlPacketView &packet) override
    {
        packets.push_back(packet);
    }
};

class ScopedUnixDatagramReceiver {
public:
    explicit ScopedUnixDatagramReceiver(const std::string &path) : fd_(-1), path_(path)
    {
        fd_ = socket(AF_UNIX, SOCK_DGRAM, 0);
        REQUIRE(fd_ >= 0);

        sockaddr_un addr = {};
        addr.sun_family = AF_UNIX;
        REQUIRE(path.size() < sizeof(addr.sun_path));
        std::strncpy(addr.sun_path, path.c_str(), sizeof(addr.sun_path) - 1);

        unlink(path.c_str());
        REQUIRE(bind(fd_, reinterpret_cast<const sockaddr *>(&addr), sizeof(addr)) == 0);
    }

    ~ScopedUnixDatagramReceiver()
    {
        if (fd_ >= 0)
        {
            close(fd_);
        }
        unlink(path_.c_str());
    }

    TokenAuthorizationEvent recv_event()
    {
        TokenAuthorizationEvent event = {};
        const ssize_t received = recv(fd_, &event, sizeof(event), 0);
        REQUIRE(received == static_cast<ssize_t>(sizeof(event)));
        return event;
    }

private:
    int fd_;
    std::string path_;
};

std::string make_socket_path()
{
    char path[] = "/tmp/wfb-rx-token-listener-XXXXXX";
    int fd = mkstemp(path);
    REQUIRE(fd >= 0);
    close(fd);
    unlink(path);
    return std::string(path);
}

}

TEST_CASE("Aggregator 只向 listener 转发合法 Token")
{
    REQUIRE(sodium_init() >= 0);

    std::string keypair_path = write_temp_keypair_file();
    AggregatorUNIX agg("rx-test-socket", keypair_path, 0, 0, 0, 7);
    RecordingTokenControlListener listener;
    agg.set_token_control_listener(&listener);

    const uint8_t antenna[RX_ANT_MAX] = {0xff, 0xff, 0xff, 0xff};
    const int8_t rssi[RX_ANT_MAX] = {0, 0, 0, 0};
    const int8_t noise[RX_ANT_MAX] = {0, 0, 0, 0};

    SECTION("本节点新 Token 触发 listener")
    {
        wtoken_control_hdr_t packet = make_token_packet(7, 42, 100);
        agg.process_packet(reinterpret_cast<const uint8_t *>(&packet),
                           sizeof(packet),
                           0,
                           antenna,
                           rssi,
                           noise,
                           0,
                           0,
                           20,
                           nullptr);

        REQUIRE(listener.packets.size() == 1);
        REQUIRE(listener.packets[0].node_id == 7);
        REQUIRE(listener.packets[0].sequence == 42);
        REQUIRE(agg.token_filter_counters.received == 1);
        REQUIRE(agg.token_filter_counters.accepted == 1);
    }

    SECTION("合法 Token 通过 Unix datagram 发送授权事件")
    {
        const std::string base_socket_path = make_socket_path();
        const std::string auth_socket_path = make_token_authorization_socket_name(base_socket_path);
        ScopedUnixDatagramReceiver receiver(auth_socket_path);
        TokenEventDatagramListener listener(base_socket_path);
        agg.set_token_control_listener(&listener);

        wtoken_control_hdr_t packet = make_token_packet(7, 52, 180);
        agg.process_packet(reinterpret_cast<const uint8_t *>(&packet),
                           sizeof(packet),
                           0,
                           antenna,
                           rssi,
                           noise,
                           0,
                           0,
                           20,
                           nullptr);

        const TokenAuthorizationEvent event = receiver.recv_event();
        REQUIRE(event.node_id == 7);
        REQUIRE(event.sequence == 52);
        REQUIRE(event.duration_ms == 180);
        REQUIRE(event.expires_at_ms >= 180);
        REQUIRE(agg.token_filter_counters.received == 1);
        REQUIRE(agg.token_filter_counters.accepted == 1);
    }

    SECTION("合法 Token 发送到派生的授权 socket")
    {
        const std::string base_socket_path = make_socket_path();
        const std::string auth_socket_path = make_token_authorization_socket_name(base_socket_path);
        ScopedUnixDatagramReceiver receiver(auth_socket_path);
        TokenEventDatagramListener listener(base_socket_path);
        agg.set_token_control_listener(&listener);

        wtoken_control_hdr_t packet = make_token_packet(7, 60, 200);
        agg.process_packet(reinterpret_cast<const uint8_t *>(&packet),
                           sizeof(packet),
                           0,
                           antenna,
                           rssi,
                           noise,
                           0,
                           0,
                           20,
                           nullptr);

        const TokenAuthorizationEvent event = receiver.recv_event();
        REQUIRE(event.node_id == 7);
        REQUIRE(event.sequence == 60);
        REQUIRE(event.duration_ms == 200);
        REQUIRE(agg.token_filter_counters.received == 1);
        REQUIRE(agg.token_filter_counters.accepted == 1);
    }

    SECTION("其他节点 Token 不触发 listener")
    {
        wtoken_control_hdr_t packet = make_token_packet(8, 42, 100);
        agg.process_packet(reinterpret_cast<const uint8_t *>(&packet),
                           sizeof(packet),
                           0,
                           antenna,
                           rssi,
                           noise,
                           0,
                           0,
                           20,
                           nullptr);

        REQUIRE(listener.packets.empty());
        REQUIRE(agg.token_filter_counters.received == 1);
        REQUIRE(agg.token_filter_counters.ignored_wrong_node == 1);
    }

    SECTION("重复 sequence Token 不触发 listener")
    {
        wtoken_control_hdr_t first = make_token_packet(7, 42, 100);
        agg.process_packet(reinterpret_cast<const uint8_t *>(&first),
                           sizeof(first),
                           0,
                           antenna,
                           rssi,
                           noise,
                           0,
                           0,
                           20,
                           nullptr);

        wtoken_control_hdr_t duplicate = make_token_packet(7, 42, 100);
        agg.process_packet(reinterpret_cast<const uint8_t *>(&duplicate),
                           sizeof(duplicate),
                           0,
                           antenna,
                           rssi,
                           noise,
                           0,
                           0,
                           20,
                           nullptr);

        REQUIRE(listener.packets.size() == 1);
        REQUIRE(agg.token_filter_counters.received == 2);
        REQUIRE(agg.token_filter_counters.accepted == 1);
        REQUIRE(agg.token_filter_counters.ignored_duplicate_sequence == 1);
    }

    unlink(keypair_path.c_str());
}

int main(int argc, char *argv[])
{
    Catch::Session session;
    return session.run(argc, argv);
}
