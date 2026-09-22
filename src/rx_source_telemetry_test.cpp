#include <catch2/catch_session.hpp>
#include <catch2/catch_test_macros.hpp>

#include <sodium.h>
#include <unistd.h>
#include <vector>
#include <set>
#include <string>
#include <functional>
#include <sstream>

#include "rx.hpp"
#include "wifibroadcast.hpp"
#include "control_envelope.hpp"
#include "token_event_ipc.hpp"
#include "token_authorization_ipc.hpp"

namespace {

class ScopedFd {
public:
    explicit ScopedFd(int fd) : fd_(fd) {}
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

std::string capture_stdout_for_test(const std::function<void()> &fn)
{
    int pipe_fds[2] = {-1, -1};
    REQUIRE(pipe(pipe_fds) == 0);

    ScopedFd read_end(pipe_fds[0]);
    ScopedFd write_end(pipe_fds[1]);
    ScopedFd target_guard(dup(STDOUT_FILENO));
    REQUIRE(target_guard.get() >= 0);

    fflush(stdout);
    REQUIRE(dup2(write_end.get(), STDOUT_FILENO) >= 0);

    fn();

    fflush(stdout);
    REQUIRE(dup2(target_guard.get(), STDOUT_FILENO) >= 0);
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

std::vector<uint8_t> make_data_packet(uint8_t source_node,
                                      uint64_t source_local_block_idx,
                                      uint8_t fragment_idx,
                                      uint8_t payload_byte = 0x42,
                                      size_t payload_size = 1,
                                      uint8_t flags = 0)
{
    std::vector<uint8_t> packet(sizeof(wblock_hdr_t) + sizeof(wpacket_hdr_t) + payload_size, 0);
    wblock_hdr_t *block_hdr = reinterpret_cast<wblock_hdr_t *>(packet.data());
    block_hdr->packet_type = WFB_PACKET_DATA;
    const uint64_t data_nonce = make_data_nonce(source_node, source_local_block_idx, fragment_idx);
    block_hdr->data_nonce = htobe64(data_nonce);

    wpacket_hdr_t *packet_hdr = reinterpret_cast<wpacket_hdr_t *>(packet.data() + sizeof(wblock_hdr_t));
    packet_hdr->flags = flags;
    packet_hdr->packet_size = htobe16(static_cast<uint16_t>(payload_size));
    if (payload_size > 0)
    {
        memset(packet.data() + sizeof(wblock_hdr_t) + sizeof(wpacket_hdr_t), payload_byte, payload_size);
    }
    return packet;
}

class RecordingPayloadAggregator : public Aggregator {
public:
    RecordingPayloadAggregator(uint8_t local_node_id, const std::set<uint8_t> &known_clients, int k = 1, int n = 1)
        : Aggregator(WFB_TRUSTED_PLAINTEXT_KEYPAIR, 0, 0, local_node_id, true, k, n)
    {
        set_known_client_node_ids(known_clients);
    }

    std::vector<std::vector<uint8_t>> delivered_payloads;

protected:
    void send_to_socket(const uint8_t *payload, uint16_t packet_size) override
    {
        delivered_payloads.emplace_back(payload, payload + packet_size);
    }
};

} // namespace

TEST_CASE("分源聚合器按 source_node 独立维护收包与交付统计")
{
    REQUIRE(sodium_init() >= 0);

    RecordingPayloadAggregator agg(0, std::set<uint8_t>{1, 2}, 1, 1);
    const uint8_t antenna[RX_ANT_MAX] = {0xff, 0xff, 0xff, 0xff};
    const int8_t rssi[RX_ANT_MAX] = {0, 0, 0, 0};
    const int8_t noise[RX_ANT_MAX] = {0, 0, 0, 0};

    const size_t c1_payload_len = 10;
    const size_t c2_payload_len = 20;

    // Client 1 和 Client 2 交替发送数据包
    const std::vector<uint8_t> c1_p1 = make_data_packet(1, 0, 0, 0x11, c1_payload_len);
    const std::vector<uint8_t> c2_p1 = make_data_packet(2, 0, 0, 0x21, c2_payload_len);
    const std::vector<uint8_t> c1_p2 = make_data_packet(1, 1, 0, 0x12, c1_payload_len);
    const std::vector<uint8_t> c2_p2 = make_data_packet(2, 1, 0, 0x22, c2_payload_len);

    agg.process_packet(c1_p1.data(), c1_p1.size(), 0, antenna, rssi, noise, 0, 0, 20, nullptr);
    agg.process_packet(c2_p1.data(), c2_p1.size(), 0, antenna, rssi, noise, 0, 0, 20, nullptr);
    agg.process_packet(c1_p2.data(), c1_p2.size(), 0, antenna, rssi, noise, 0, 0, 20, nullptr);
    agg.process_packet(c2_p2.data(), c2_p2.size(), 0, antenna, rssi, noise, 0, 0, 20, nullptr);

    REQUIRE(agg.delivered_payloads.size() == 4);

    RxSourceStats stats1 = {};
    REQUIRE(agg.get_source_stats(1, &stats1));
    CHECK(stats1.count_p_raw == 2);
    CHECK(stats1.count_b_raw == c1_p1.size() + c1_p2.size());
    CHECK(stats1.count_p_outgoing == 2);
    CHECK(stats1.count_b_outgoing == c1_payload_len * 2);
    CHECK(stats1.count_p_fec_recovered == 0);
    CHECK(stats1.count_p_lost == 0);

    RxSourceStats stats2 = {};
    REQUIRE(agg.get_source_stats(2, &stats2));
    CHECK(stats2.count_p_raw == 2);
    CHECK(stats2.count_b_raw == c2_p1.size() + c2_p2.size());
    CHECK(stats2.count_p_outgoing == 2);
    CHECK(stats2.count_b_outgoing == c2_payload_len * 2);
    CHECK(stats2.count_p_fec_recovered == 0);
    CHECK(stats2.count_p_lost == 0);

    // 全局统计也保持一致
    CHECK(agg.count_p_all == 4);
    CHECK(agg.count_p_outgoing == 4);
    CHECK(agg.count_b_outgoing == c1_payload_len * 2 + c2_payload_len * 2);
}

TEST_CASE("分源聚合器丢包精准归因于出现跳号的客户端")
{
    REQUIRE(sodium_init() >= 0);

    RecordingPayloadAggregator agg(0, std::set<uint8_t>{1, 2}, 1, 1);
    const uint8_t antenna[RX_ANT_MAX] = {0xff, 0xff, 0xff, 0xff};
    const int8_t rssi[RX_ANT_MAX] = {0, 0, 0, 0};
    const int8_t noise[RX_ANT_MAX] = {0, 0, 0, 0};

    // Client 1 发送 block 1，跳过 block 2，直接发送 block 3 -> 产生 1 个丢包
    const std::vector<uint8_t> c1_p1 = make_data_packet(1, 1, 0, 0x11, 8);
    const std::vector<uint8_t> c1_p3 = make_data_packet(1, 3, 0, 0x13, 8);

    // Client 2 连续发送 block 1 和 block 2 -> 无丢包
    const std::vector<uint8_t> c2_p1 = make_data_packet(2, 1, 0, 0x21, 8);
    const std::vector<uint8_t> c2_p2 = make_data_packet(2, 2, 0, 0x22, 8);

    agg.process_packet(c1_p1.data(), c1_p1.size(), 0, antenna, rssi, noise, 0, 0, 20, nullptr);
    agg.process_packet(c2_p1.data(), c2_p1.size(), 0, antenna, rssi, noise, 0, 0, 20, nullptr);
    agg.process_packet(c2_p2.data(), c2_p2.size(), 0, antenna, rssi, noise, 0, 0, 20, nullptr);
    agg.process_packet(c1_p3.data(), c1_p3.size(), 0, antenna, rssi, noise, 0, 0, 20, nullptr);

    RxSourceStats stats1 = {};
    REQUIRE(agg.get_source_stats(1, &stats1));
    CHECK(stats1.count_p_lost == 1);
    CHECK(stats1.count_p_raw == 2);
    CHECK(stats1.count_p_outgoing == 2);

    RxSourceStats stats2 = {};
    REQUIRE(agg.get_source_stats(2, &stats2));
    CHECK(stats2.count_p_lost == 0);
    CHECK(stats2.count_p_raw == 2);
    CHECK(stats2.count_p_outgoing == 2);

    // 全局丢包为 1
    CHECK(agg.count_p_lost == 1);
}

TEST_CASE("分源聚合器 FEC 恢复精准归因于执行重组的客户端")
{
    REQUIRE(sodium_init() >= 0);

    // k=1, n=2 (1 primary, 1 parity)
    RecordingPayloadAggregator agg(0, std::set<uint8_t>{1, 2}, 1, 2);
    const uint8_t antenna[RX_ANT_MAX] = {0xff, 0xff, 0xff, 0xff};
    const int8_t rssi[RX_ANT_MAX] = {0, 0, 0, 0};
    const int8_t noise[RX_ANT_MAX] = {0, 0, 0, 0};

    // Client 1: 仅收到 parity 帧 (fragment 1)，未收到 primary 帧 (fragment 0)
    // 对于 k=1, n=2，zfex 奇偶校验帧即 primary 帧的拷贝（nonce 中 fragment_idx=1）
    const std::vector<uint8_t> c1_parity = make_data_packet(1, 0, 1, 0x55, 16);

    // Client 2: 收到正常的 primary 帧 (fragment 0)
    const std::vector<uint8_t> c2_primary = make_data_packet(2, 0, 0, 0x66, 16);

    agg.process_packet(c1_parity.data(), c1_parity.size(), 0, antenna, rssi, noise, 0, 0, 20, nullptr);
    agg.process_packet(c2_primary.data(), c2_primary.size(), 0, antenna, rssi, noise, 0, 0, 20, nullptr);

    RxSourceStats stats1 = {};
    REQUIRE(agg.get_source_stats(1, &stats1));
    CHECK(stats1.count_p_fec_recovered == 1);
    CHECK(stats1.count_p_raw == 1);
    CHECK(stats1.count_p_outgoing == 1);

    RxSourceStats stats2 = {};
    REQUIRE(agg.get_source_stats(2, &stats2));
    CHECK(stats2.count_p_fec_recovered == 0);
    CHECK(stats2.count_p_raw == 1);
    CHECK(stats2.count_p_outgoing == 1);

    CHECK(agg.count_p_fec_recovered == 1);
    CHECK(agg.delivered_payloads.size() == 2);
}

TEST_CASE("dump_stats 输出符合格式的 PKT_SRC 行且 clear_stats 重置周期计数")
{
    REQUIRE(sodium_init() >= 0);

    // 已知节点 1, 2, 3
    RecordingPayloadAggregator agg(0, std::set<uint8_t>{1, 2, 3}, 1, 1);
    const uint8_t antenna[RX_ANT_MAX] = {0xff, 0xff, 0xff, 0xff};
    const int8_t rssi[RX_ANT_MAX] = {0, 0, 0, 0};
    const int8_t noise[RX_ANT_MAX] = {0, 0, 0, 0};

    // Client 1 发送 2 包
    const std::vector<uint8_t> c1_p1 = make_data_packet(1, 0, 0, 0x11, 10);
    const std::vector<uint8_t> c1_p2 = make_data_packet(1, 1, 0, 0x12, 10);
    agg.process_packet(c1_p1.data(), c1_p1.size(), 0, antenna, rssi, noise, 0, 0, 20, nullptr);
    agg.process_packet(c1_p2.data(), c1_p2.size(), 0, antenna, rssi, noise, 0, 0, 20, nullptr);

    // Client 2 发送 1 包，有跳号丢包
    const std::vector<uint8_t> c2_p1 = make_data_packet(2, 1, 0, 0x21, 15);
    const std::vector<uint8_t> c2_p3 = make_data_packet(2, 3, 0, 0x23, 15);
    agg.process_packet(c2_p1.data(), c2_p1.size(), 0, antenna, rssi, noise, 0, 0, 20, nullptr);
    agg.process_packet(c2_p3.data(), c2_p3.size(), 0, antenna, rssi, noise, 0, 0, 20, nullptr);

    // Client 3 本周期未发送任何包 (0 pkts)

    std::string captured = capture_stdout_for_test([&]() {
        agg.dump_stats();
    });

    // 检查是否包含全局 PKT 行
    CHECK(captured.find("\tPKT\t") != std::string::npos);

    // 检查 Client 1 的 PKT_SRC 行: source_node=1, raw=2, bytes=c1_p1.size()*2, fec_recovered=0, lost=0, out_packets=2, out_bytes=20
    std::string expected_c1 = "\tPKT_SRC\t1:2:" + std::to_string(c1_p1.size() * 2) + ":0:0:2:20\n";
    CHECK(captured.find(expected_c1) != std::string::npos);

    // 检查 Client 2 的 PKT_SRC 行: source_node=2, raw=2, bytes=c2_p1.size()*2, fec_recovered=0, lost=1, out_packets=2, out_bytes=30
    std::string expected_c2 = "\tPKT_SRC\t2:2:" + std::to_string(c2_p1.size() * 2) + ":0:1:2:30\n";
    CHECK(captured.find(expected_c2) != std::string::npos);

    // 检查 Client 3 的 PKT_SRC 行: source_node=3, 全 0
    std::string expected_c3 = "\tPKT_SRC\t3:0:0:0:0:0:0\n";
    CHECK(captured.find(expected_c3) != std::string::npos);

    // dump_stats 之后 clear_stats 应已被调用，所有计数重置为 0
    RxSourceStats post_stats1 = {};
    REQUIRE(agg.get_source_stats(1, &post_stats1));
    CHECK(post_stats1.count_p_raw == 0);
    CHECK(post_stats1.count_b_raw == 0);
    CHECK(post_stats1.count_p_lost == 0);
    CHECK(post_stats1.count_p_outgoing == 0);
    CHECK(post_stats1.count_b_outgoing == 0);

    CHECK(agg.count_p_all == 0);
    CHECK(agg.count_p_outgoing == 0);
}
