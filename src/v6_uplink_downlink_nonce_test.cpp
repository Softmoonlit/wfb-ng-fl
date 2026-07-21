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

Config parse_server_config(const std::vector<std::string> &extra_args)
{
    std::vector<std::string> arguments = {
        "wfb_v6_uplink",
        "--role", "server",
        "--tun-name", "test0",
        "--tun-addr", "10.6.0.1/24",
        "--node-id", "255",
        "--link-id", "1",
        "--uplink-stream", "1",
        "--downlink-stream", "2",
        "--air-listen-port", "10000",
        "--known-clients", "11,12",
        "--client-target", "11:10.6.0.11:127.0.0.1:10011",
        "--client-target", "12:10.6.0.12:127.0.0.1:10012",
    };
    arguments.insert(arguments.end(), extra_args.begin(), extra_args.end());

    std::vector<char *> argv;
    for (std::string &argument : arguments)
    {
        argv.push_back(const_cast<char *>(argument.c_str()));
    }
    optind = 1;
    return parse_args(static_cast<int>(argv.size()), argv.data());
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
class ScopedPipe {
public:
    ScopedPipe()
    {
        int fds[2] = {-1, -1};
        REQUIRE(pipe(fds) == 0);
        read_fd_ = fds[0];
        write_fd_ = fds[1];
    }

    ~ScopedPipe()
    {
        if (read_fd_ >= 0)
        {
            close(read_fd_);
        }
        if (write_fd_ >= 0)
        {
            close(write_fd_);
        }
    }

    int reader() const { return read_fd_; }
    int writer() const { return write_fd_; }

private:
    int read_fd_ = -1;
    int write_fd_ = -1;
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
uint8_t packet_source_node(const std::vector<uint8_t> &packet)
{
    return data_nonce_source_node(packet_data_nonce(packet));
}

uint64_t packet_block_idx(const std::vector<uint8_t> &packet)
{
    return data_nonce_source_local_block_idx(packet_data_nonce(packet));
}

uint8_t packet_fragment_idx(const std::vector<uint8_t> &packet)
{
    return data_nonce_fragment_idx(packet_data_nonce(packet));
}

uint8_t packet_flags(const std::vector<uint8_t> &packet)
{
    const uint8_t *air_packet = udp_forwarded_air_packet_data(packet);
    const wpacket_hdr_t *packet_hdr = reinterpret_cast<const wpacket_hdr_t *>(air_packet + sizeof(wblock_hdr_t));
    return packet_hdr->flags;
}

void process_forwarded_packet(TunWriterAggregator &aggregator, const std::vector<uint8_t> &packet)
{
    REQUIRE(packet.size() >= sizeof(wrxfwd_t) + sizeof(wblock_hdr_t));
    const wrxfwd_t *header = reinterpret_cast<const wrxfwd_t *>(packet.data());
    aggregator.process_packet(packet.data() + sizeof(wrxfwd_t),
                              packet.size() - sizeof(wrxfwd_t),
                              header->wlan_idx,
                              header->antenna,
                              header->rssi,
                              header->noise,
                              header->freq,
                              header->mcs_index,
                              header->bandwidth,
                              nullptr);
}

std::vector<uint8_t> make_ipv4_packet(const char *source, const char *destination)
{
    std::vector<uint8_t> packet(20, 0);
    packet[0] = 0x45;
    in_addr source_address = {};
    in_addr destination_address = {};
    REQUIRE(inet_aton(source, &source_address) == 1);
    REQUIRE(inet_aton(destination, &destination_address) == 1);
    memcpy(packet.data() + 12, &source_address, sizeof(source_address));
    memcpy(packet.data() + 16, &destination_address, sizeof(destination_address));
    return packet;
}

} // namespace

TEST_CASE("反馈窗口可选择立即开始首轮")
{
    Config delayed = parse_server_config({
        "--feedback-window-period-ms", "500",
        "--feedback-window-duration-ms", "15",
    });
    FeedbackWindowState delayed_state = make_feedback_window_state(delayed, 1000);
    REQUIRE(delayed_state.enabled);
    REQUIRE(delayed_state.next_open_at_ms == 1500);

    Config immediate = parse_server_config({
        "--feedback-window-period-ms", "500",
        "--feedback-window-duration-ms", "15",
        "--feedback-window-start-immediately",
    });
    FeedbackWindowState immediate_state = make_feedback_window_state(immediate, 1000);
    REQUIRE(immediate_state.enabled);
    REQUIRE(immediate_state.next_open_at_ms == 1000);

    REQUIRE_THROWS(parse_server_config({
        "--feedback-window-start-immediately",
    }));
}

TEST_CASE("反馈窗口无需 READY 即按 known_clients 发放短 GRANT")
{
    Config config = {};
    config.node_id = 255;
    config.known_clients = {11, 12};
    config.guard_interval_ms = 10;
    config.feedback_window_period_ms = 500;
    config.feedback_window_duration_ms = 15;
    std::vector<ClientTarget> targets = make_two_targets();

    TokenSchedulerConfig scheduler_config = {};
    scheduler_config.node_ids = config.known_clients;
    scheduler_config.duration_ms = 100;
    scheduler_config.guard_interval_ms = config.guard_interval_ms;
    TokenScheduler scheduler(scheduler_config);
    FeedbackWindowState state = make_feedback_window_state(config, 1000);
    open_feedback_window(&state, 1000, config.known_clients.size());

    REQUIRE(maybe_send_feedback_grant(&state, &scheduler, config, targets, 1000));
    REQUIRE(state.current_node_id == 11);
    state.current_slot_expires_at_ms = UINT64_MAX;
    const std::vector<uint8_t> response = make_ipv4_packet("10.6.0.11", "10.6.0.1");
    maybe_record_feedback_uplink_hit(&state, &scheduler, targets, response.data(), response.size());
    REQUIRE(state.slot_hit_total_by_node[11] == 1);

    REQUIRE(maybe_send_feedback_grant(&state, &scheduler, config, targets, 1025));
    REQUIRE(state.current_node_id == 12);
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
TEST_CASE("trusted_plaintext FEC 2/3 按同一 block 发出 primary-primary-parity")
{
    ScopedUdpReceiver receiver;
    std::unique_ptr<AirTransmitter> transmitter(new AirTransmitter("127.0.0.1", receiver.port(), 0, 23, 2, 3));
    const std::vector<uint8_t> first_payload = {0x10, 0x11, 0x12};
    const std::vector<uint8_t> second_payload = {0x20, 0x21};

    transmitter->send_data(first_payload.data(), first_payload.size());
    transmitter->send_data(second_payload.data(), second_payload.size());

    const std::vector<uint8_t> first_packet = receiver.recv_packet();
    const std::vector<uint8_t> second_packet = receiver.recv_packet();
    const std::vector<uint8_t> third_packet = receiver.recv_packet();

    REQUIRE(packet_source_node(first_packet) == 23);
    REQUIRE(packet_source_node(second_packet) == 23);
    REQUIRE(packet_source_node(third_packet) == 23);

    REQUIRE(packet_block_idx(first_packet) == 0);
    REQUIRE(packet_block_idx(second_packet) == 0);
    REQUIRE(packet_block_idx(third_packet) == 0);

    REQUIRE(packet_fragment_idx(first_packet) == 0);
    REQUIRE(packet_fragment_idx(second_packet) == 1);
    REQUIRE(packet_fragment_idx(third_packet) == 2);

    REQUIRE(packet_flags(first_packet) == 0);
    REQUIRE(packet_flags(second_packet) == 0);
    REQUIRE(packet_payload(first_packet) == first_payload);
    REQUIRE(packet_payload(second_packet) == second_payload);
}

TEST_CASE("TunWriterAggregator 可用 surviving primary 加 parity 恢复缺失的首个 primary")
{
    ScopedUdpReceiver receiver;
    std::unique_ptr<AirTransmitter> transmitter(new AirTransmitter("127.0.0.1", receiver.port(), 0, 37, 2, 3));
    const std::vector<uint8_t> first_payload = {0x31, 0x32, 0x33};
    const std::vector<uint8_t> second_payload = {0x41, 0x42};
    ScopedPipe pipe;
    TunWriterAggregator aggregator(pipe.writer(),
                                   WFB_TRUSTED_PLAINTEXT_KEYPAIR,
                                   0,
                                   0,
                                   0,
                                   true,
                                   2,
                                   3);
    std::vector<std::vector<uint8_t>> delivered_payloads;
    aggregator.set_payload_observer([&](const uint8_t *payload, uint16_t packet_size) {
        delivered_payloads.emplace_back(payload, payload + packet_size);
    });

    transmitter->send_data(first_payload.data(), first_payload.size());
    transmitter->send_data(second_payload.data(), second_payload.size());

    const std::vector<uint8_t> first_packet = receiver.recv_packet();
    const std::vector<uint8_t> second_packet = receiver.recv_packet();
    const std::vector<uint8_t> third_packet = receiver.recv_packet();

    REQUIRE(packet_fragment_idx(first_packet) == 0);
    REQUIRE(packet_fragment_idx(second_packet) == 1);
    REQUIRE(packet_fragment_idx(third_packet) == 2);

    process_forwarded_packet(aggregator, second_packet);
    process_forwarded_packet(aggregator, third_packet);

    REQUIRE(delivered_payloads == std::vector<std::vector<uint8_t>>{first_payload, second_payload});
}

TEST_CASE("flush_data_block 会封口半块且不会把 FEC_ONLY 写成业务 payload")
{
    ScopedUdpReceiver receiver;
    std::unique_ptr<AirTransmitter> transmitter(new AirTransmitter("127.0.0.1", receiver.port(), 0, 41, 2, 3));
    const std::vector<uint8_t> payload = {0xaa, 0xbb, 0xcc};
    ScopedPipe pipe;
    TunWriterAggregator aggregator(pipe.writer(),
                                   WFB_TRUSTED_PLAINTEXT_KEYPAIR,
                                   0,
                                   0,
                                   0,
                                   true,
                                   2,
                                   3);
    std::vector<std::vector<uint8_t>> delivered_payloads;
    aggregator.set_payload_observer([&](const uint8_t *written_payload, uint16_t packet_size) {
        delivered_payloads.emplace_back(written_payload, written_payload + packet_size);
    });

    transmitter->send_data(payload.data(), payload.size());
    const std::vector<uint8_t> first_packet = receiver.recv_packet();

    REQUIRE(packet_fragment_idx(first_packet) == 0);
    REQUIRE(packet_payload(first_packet) == payload);
    REQUIRE(transmitter->next_flush_deadline_ms() > 0);

    REQUIRE(transmitter->flush_data_block());
    REQUIRE_FALSE(transmitter->flush_data_block());
    REQUIRE(transmitter->next_flush_deadline_ms() == 0);

    const std::vector<uint8_t> second_packet = receiver.recv_packet();
    const std::vector<uint8_t> third_packet = receiver.recv_packet();

    REQUIRE(packet_block_idx(second_packet) == 0);
    REQUIRE(packet_block_idx(third_packet) == 0);
    REQUIRE(packet_fragment_idx(second_packet) == 1);
    REQUIRE(packet_fragment_idx(third_packet) == 2);
    REQUIRE((packet_flags(second_packet) & WFB_PACKET_FEC_ONLY) != 0);
    REQUIRE(packet_payload(second_packet).empty());

    process_forwarded_packet(aggregator, first_packet);
    process_forwarded_packet(aggregator, second_packet);
    process_forwarded_packet(aggregator, third_packet);

    REQUIRE(delivered_payloads == std::vector<std::vector<uint8_t>>{payload});
}

int main(int argc, char **argv)
{
    Catch::Session session;
    return session.run(argc, argv);
}
