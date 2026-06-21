#include <catch2/catch_session.hpp>
#include <catch2/catch_test_macros.hpp>

#include <sodium.h>

#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <string>
#include <vector>
#include <set>
#include <functional>

#include "rx.hpp"
#include "token_event_ipc.hpp"
#include "wifibroadcast.hpp"
#include "control_envelope.hpp"

namespace {

struct TestGrantPacket {
    wcontrol_envelope_hdr_t hdr;
    wcontrol_grant_payload_t payload;
} __attribute__((packed));

TestGrantPacket make_token_packet(uint8_t node_id,
                                  uint64_t sequence,
                                  uint32_t duration_ms)
{
    TestGrantPacket packet = {};
    packet.hdr.packet_type = WFB_PACKET_CONTROL;
    packet.hdr.magic = htobe16(WFB_CONTROL_MAGIC);
    packet.hdr.version = WFB_CONTROL_VERSION;
    packet.hdr.control_type = WFB_CONTROL_TYPE_GRANT;
    packet.hdr.source_node = 1;
    packet.hdr.target_node = node_id;
    packet.hdr.sequence = htobe64(sequence);
    packet.payload.duration_ms = htobe32(duration_ms);
    return packet;
}
wcontrol_envelope_hdr_t make_ready_packet(uint8_t source_node)
{
    wcontrol_envelope_hdr_t packet = {};
    packet.packet_type = WFB_PACKET_CONTROL;
    packet.magic = htobe16(WFB_CONTROL_MAGIC);
    packet.version = WFB_CONTROL_VERSION;
    packet.control_type = WFB_CONTROL_TYPE_READY;
    packet.source_node = source_node;
    packet.target_node = 0;
    packet.sequence = htobe64(0);
    return packet;
}
std::vector<uint8_t> make_data_packet(uint64_t block_idx, uint8_t fragment_idx, uint8_t payload_byte = 0x42)
{
    std::vector<uint8_t> packet(sizeof(wblock_hdr_t) + sizeof(wpacket_hdr_t) + 1, 0);
    wblock_hdr_t *block_hdr = reinterpret_cast<wblock_hdr_t *>(packet.data());
    block_hdr->packet_type = WFB_PACKET_DATA;
    block_hdr->data_nonce = htobe64((block_idx << 8) | fragment_idx);

    wpacket_hdr_t *packet_hdr = reinterpret_cast<wpacket_hdr_t *>(packet.data() + sizeof(wblock_hdr_t));
    packet_hdr->flags = 0;
    packet_hdr->packet_size = htobe16(1);
    packet[sizeof(wblock_hdr_t) + sizeof(wpacket_hdr_t)] = payload_byte;
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
    std::vector<ControlEnvelopeView> packets;

    void on_token_control(const ControlEnvelopeView &packet) override
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

std::string capture_fd_for_test(const std::function<void()> &fn, int target_fd)
{
    int pipe_fds[2] = {-1, -1};
    REQUIRE(pipe(pipe_fds) == 0);

    ScopedFd read_end(pipe_fds[0]);
    ScopedFd write_end(pipe_fds[1]);
    ScopedFd target_guard(dup(target_fd));
    REQUIRE(target_guard.get() >= 0);

    fflush(target_fd == STDERR_FILENO ? stderr : stdout);
    REQUIRE(dup2(write_end.get(), target_fd) >= 0);

    fn();

    fflush(target_fd == STDERR_FILENO ? stderr : stdout);
    REQUIRE(dup2(target_guard.get(), target_fd) >= 0);
    close(pipe_fds[1]);

    std::string captured;
    char buffer[256] = {};
    ssize_t received = 0;
    while ((received = read(read_end.get(), buffer, sizeof(buffer))) > 0)
    {
        captured.append(buffer, static_cast<size_t>(received));
    }

    return captured;
}

std::string capture_stderr_for_test(const std::function<void()> &fn)
{
    return capture_fd_for_test(fn, STDERR_FILENO);
}

std::string capture_stdout_for_test(const std::function<void()> &fn)
{
    return capture_fd_for_test(fn, STDOUT_FILENO);
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
        TestGrantPacket packet = make_token_packet(7, 42, 100);
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
        REQUIRE(listener.packets[0].target_node == 7);
        REQUIRE(listener.packets[0].sequence == 42);
        REQUIRE(agg.grant_filter_counters_.received == 1);
        REQUIRE(agg.grant_filter_counters_.accepted == 1);
    }

    SECTION("合法 Token 通过 Unix datagram 发送授权事件")
    {
        const std::string base_socket_path = make_socket_path();
        const std::string auth_socket_path = make_token_authorization_socket_name(base_socket_path);
        ScopedUnixDatagramReceiver receiver(auth_socket_path);
        TokenEventDatagramListener ipc_listener(base_socket_path);
        agg.set_token_control_listener(&ipc_listener);

        TestGrantPacket packet = make_token_packet(7, 52, 180);
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
        REQUIRE(agg.grant_filter_counters_.received == 1);
        REQUIRE(agg.grant_filter_counters_.accepted == 1);
    }

    SECTION("静态已知 client 的 READY 转发到 ready socket")
    {
        const std::string base_socket_path = make_socket_path();
        const std::string ready_socket_base = make_socket_path();
        const std::string ready_socket_path = make_token_ready_socket_name(ready_socket_base);
        TokenAuthorizationDatagramReceiver receiver(ready_socket_path);
        TokenEventDatagramListener ipc_listener(base_socket_path, ready_socket_base);
        agg.set_token_control_listener(&ipc_listener);
        agg.set_known_client_node_ids(std::set<uint8_t>{7});

        wcontrol_envelope_hdr_t packet = make_ready_packet(7);
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

        TokenAuthorizationEvent event = {};
        REQUIRE(receiver.recv_event(&event));
        REQUIRE(event.node_id == 7);
        REQUIRE(event.sequence == 0);
        REQUIRE(event.duration_ms == 0);
        REQUIRE(event.expires_at_ms == 0);
        REQUIRE(agg.ready_filter_counters_.received == 1);
        REQUIRE(agg.ready_filter_counters_.accepted == 1);
    }

    SECTION("未知 client 的 READY 被拒收并记录最小排障日志")
    {
        const std::string base_socket_path = make_socket_path();
        const std::string ready_socket_base = make_socket_path();
        const std::string ready_socket_path = make_token_ready_socket_name(ready_socket_base);
        TokenAuthorizationDatagramReceiver receiver(ready_socket_path);
        TokenEventDatagramListener ipc_listener(base_socket_path, ready_socket_base);
        agg.set_token_control_listener(&ipc_listener);
        agg.set_known_client_node_ids(std::set<uint8_t>{7});

        wcontrol_envelope_hdr_t packet = make_ready_packet(8);
        const std::string captured = capture_stderr_for_test([&]() {
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
        });

        TokenAuthorizationEvent event = {};
        REQUIRE_FALSE(receiver.recv_event(&event));
        REQUIRE(agg.ready_filter_counters_.received == 1);
        REQUIRE(agg.ready_filter_counters_.rejected.unknown_client == 1);
        REQUIRE(captured.find("READY_REJECT reason=unknown_client") != std::string::npos);
        REQUIRE(captured.find("source_node=8") != std::string::npos);
        REQUIRE(captured.find("server_node_id=7") != std::string::npos);
    }

    SECTION("合法 Token 发送到派生的授权 socket")
    {
        const std::string base_socket_path = make_socket_path();
        const std::string auth_socket_path = make_token_authorization_socket_name(base_socket_path);
        ScopedUnixDatagramReceiver receiver(auth_socket_path);
        TokenEventDatagramListener ipc_listener(base_socket_path);
        agg.set_token_control_listener(&ipc_listener);

        TestGrantPacket packet = make_token_packet(7, 60, 200);
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
        REQUIRE(agg.grant_filter_counters_.received == 1);
        REQUIRE(agg.grant_filter_counters_.accepted == 1);
    }

    SECTION("其他节点 Token 不触发 listener")
    {
        TestGrantPacket packet = make_token_packet(8, 42, 100);
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
        REQUIRE(agg.grant_filter_counters_.received == 1);
        REQUIRE(agg.grant_filter_counters_.ignored_wrong_target == 1);
    }

    SECTION("重复 sequence Token 不触发 listener")
    {
        TestGrantPacket first = make_token_packet(7, 42, 100);
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

        TestGrantPacket duplicate = make_token_packet(7, 42, 100);
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
        REQUIRE(agg.grant_filter_counters_.received == 2);
        REQUIRE(agg.grant_filter_counters_.accepted == 1);
        REQUIRE(agg.grant_filter_counters_.ignored_duplicate_sequence == 1);
    }

    unlink(keypair_path.c_str());
}

TEST_CASE("RX 重组溢出不会阻塞控制面优先通路")
{
    REQUIRE(sodium_init() >= 0);

    const std::string socket_path = make_socket_path();
    AggregatorUNIX agg(socket_path, WFB_TRUSTED_PLAINTEXT_KEYPAIR, 0, 0, 0, 7, true, 2, 2);
    agg.set_known_client_node_ids(std::set<uint8_t>{7});

    const std::string base_socket_path = make_socket_path();
    const std::string ready_socket_base = make_socket_path();
    const std::string ready_socket_path = make_token_ready_socket_name(ready_socket_base);
    TokenAuthorizationDatagramReceiver receiver(ready_socket_path);
    TokenEventDatagramListener ipc_listener(base_socket_path, ready_socket_base);
    agg.set_token_control_listener(&ipc_listener);

    const uint8_t antenna[RX_ANT_MAX] = {0xff, 0xff, 0xff, 0xff};
    const int8_t rssi[RX_ANT_MAX] = {0, 0, 0, 0};
    const int8_t noise[RX_ANT_MAX] = {0, 0, 0, 0};

    const std::string captured = capture_stderr_for_test([&]() {
        for (uint64_t block_idx = 1; block_idx <= RX_RING_SIZE + 1; ++block_idx)
        {
            const std::vector<uint8_t> packet = make_data_packet(block_idx, 1, static_cast<uint8_t>(block_idx));
            agg.process_packet(packet.data(),
                               packet.size(),
                               0,
                               antenna,
                               rssi,
                               noise,
                               0,
                               0,
                               20,
                               nullptr);
        }
    });

    REQUIRE(agg.count_p_override == 1);
    REQUIRE(agg.reassembly_overflow_evict_total() == 1);
    REQUIRE(agg.unfinished_block_limit() == RX_RING_SIZE);
    REQUIRE(captured.find("REASSEMBLY_OVERFLOW_EVICT") != std::string::npos);
    REQUIRE(captured.find("unfinished_block_limit=40") != std::string::npos);

    const std::string stats = capture_stdout_for_test([&]() {
        agg.dump_stats();
    });
    REQUIRE(stats.find("\tREASSEMBLY\t1:40\n") != std::string::npos);

    const wcontrol_envelope_hdr_t ready_packet = make_ready_packet(7);
    agg.process_packet(reinterpret_cast<const uint8_t *>(&ready_packet),
                       sizeof(ready_packet),
                       0,
                       antenna,
                       rssi,
                       noise,
                       0,
                       0,
                       20,
                       nullptr);

    TokenAuthorizationEvent event = {};
    REQUIRE(receiver.recv_event(&event));
    REQUIRE(event.node_id == 7);
    REQUIRE(agg.ready_filter_counters_.accepted == 1);
    REQUIRE(agg.reassembly_overflow_evict_total() == 1);
}

int main(int argc, char *argv[])
{
    Catch::Session session;
    return session.run(argc, argv);
}
