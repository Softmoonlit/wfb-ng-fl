// 通过真实 run_client 与 UDP 观察重武装，TUN 系统边界由 socketpair 代替。
#include <fcntl.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <unistd.h>
#include <cstdlib>
#include <stdlib.h>
#include <cstring>
#include <cassert>
#include <iostream>
#include <algorithm>
#include <functional>
#include <map>
#include <memory>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>
static int rearm_tun_fd;
static int rearm_test_open(const char *, int) { return dup(rearm_tun_fd); }
static int rearm_test_ioctl(int, unsigned long, void *) { return 0; }
static int rearm_test_system(const char *) { return 0; }
#define open rearm_test_open
#define ioctl rearm_test_ioctl
#define system rearm_test_system
#define __WFB_V6_UPLINK_TEST__
#include "../../src/v6_uplink.cpp"
#undef open
#undef ioctl
#undef system

static int rearm_udp(uint16_t *port) {
    int fd = socket(AF_INET, SOCK_DGRAM, 0); assert(fd >= 0);
    sockaddr_in addr = {}; addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    assert(bind(fd, reinterpret_cast<sockaddr *>(&addr), sizeof(addr)) == 0);
    socklen_t n = sizeof(addr); assert(getsockname(fd, reinterpret_cast<sockaddr *>(&addr), &n) == 0);
    *port = ntohs(addr.sin_port); return fd;
}
static void rearm_send(int fd, uint16_t port, const void *data, size_t size) {
    sockaddr_in addr = {}; addr.sin_family = AF_INET; addr.sin_port = htons(port);
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    assert(sendto(fd, data, size, 0, reinterpret_cast<sockaddr *>(&addr), sizeof(addr)) == static_cast<ssize_t>(size));
}
static bool rearm_receive(int fd, int timeout_ms, int expected_type) {
    pollfd p = {fd, POLLIN, 0};
    if (poll(&p, 1, timeout_ms) != 1) return false;
    uint8_t packet[4096] = {}; ssize_t n = recv(fd, packet, sizeof(packet), 0);
    assert(n > static_cast<ssize_t>(sizeof(wrxfwd_t)));
    auto *h = reinterpret_cast<wcontrol_envelope_hdr_t *>(packet + sizeof(wrxfwd_t));
    return expected_type == WFB_PACKET_DATA ? h->packet_type == WFB_PACKET_DATA :
        h->packet_type == WFB_PACKET_CONTROL && h->control_type == expected_type;
}
static uint32_t rearm_command(int fd, uint16_t port, uint64_t epoch, size_t size = 13) {
    // 独立 CMD_REARM_UPLINK=5，reqid 与 epoch 均为网络字节序。
    uint8_t request[14] = {}; uint32_t reqid = htobe32(71);
    memcpy(request, &reqid, 4); request[4] = 5;
    uint64_t wire_epoch = htobe64(epoch); memcpy(request+5, &wire_epoch, 8);
    rearm_send(fd, port, request, size);
    pollfd p = {fd, POLLIN, 0}; assert(poll(&p, 1, 500) == 1);
    uint32_t response[3] = {}; assert(recv(fd, response, sizeof(response), 0) == 8);
    assert(response[0] == reqid); return ntohl(response[1]);
}
static void rearm_apply_radio(int fd, uint16_t port, uint8_t mcs) {
    cmd_req_t request = {}; request.req_id = htobe32(72); request.cmd_id = CMD_SET_RADIO;
    request.u.cmd_set_radio.bandwidth = 20; request.u.cmd_set_radio.mcs_index = mcs;
    request.u.cmd_set_radio.vht_nss = 1;
    rearm_send(fd, port, &request, offsetof(cmd_req_t, u) + sizeof(request.u.cmd_set_radio));
    pollfd p = {fd, POLLIN, 0}; assert(poll(&p, 1, 500) == 1);
    uint32_t response[2] = {}; assert(recv(fd, response, sizeof(response), 0) == 8);
    assert(response[0] == request.req_id && response[1] == 0);
}
static void rearm_grant(int fd, uint16_t port, uint64_t seq, uint64_t session = 100, uint32_t duration_ms = 3000) {
    std::vector<uint8_t> packet(sizeof(wrxfwd_t)+sizeof(wcontrol_envelope_hdr_t)+sizeof(wcontrol_grant_payload_t), 0);
    auto *h = reinterpret_cast<wcontrol_envelope_hdr_t *>(packet.data()+sizeof(wrxfwd_t));
    h->packet_type = WFB_PACKET_CONTROL; h->magic = htobe16(WFB_CONTROL_MAGIC);
    h->version = WFB_CONTROL_VERSION; h->control_type = WFB_CONTROL_TYPE_GRANT;
    h->source_node = 1; h->target_node = 9; h->session_id = htobe64(session); h->sequence = htobe64(seq);
    reinterpret_cast<wcontrol_grant_payload_t *>(packet.data()+sizeof(wrxfwd_t)+sizeof(*h))->duration_ms = htobe32(duration_ms);
    rearm_send(fd, port, packet.data(), packet.size());
}
static bool rearm_receive_data_frame(int fd, int timeout_ms, uint64_t *nonce, uint8_t *flags) {
    pollfd p = {fd, POLLIN, 0};
    if (poll(&p, 1, timeout_ms) != 1) return false;
    uint8_t packet[4096] = {};
    ssize_t n = recv(fd, packet, sizeof(packet), 0);
    if (n <= static_cast<ssize_t>(sizeof(wrxfwd_t) + sizeof(wblock_hdr_t))) return false;
    const auto *block = reinterpret_cast<const wblock_hdr_t *>(packet + sizeof(wrxfwd_t));
    if (block->packet_type != WFB_PACKET_DATA || be16toh(block->magic) != WFB_DATA_MAGIC) return false;
    const auto *fragment = reinterpret_cast<const wpacket_hdr_t *>(packet + sizeof(wrxfwd_t) + sizeof(*block));
    *nonce = be64toh(block->data_nonce);
    *flags = fragment->flags;
    return true;
}

static void test_native_unauthorized_fec_deadline() {
    uint16_t air_port, input_port, unused;
    int air = rearm_udp(&air_port), input_reservation = rearm_udp(&input_port);
    int command = rearm_udp(&unused), output_pipe[2];
    assert(pipe(output_pipe) == 0);
    int tun[2]; assert(socketpair(AF_UNIX, SOCK_DGRAM, 0, tun) == 0);
    Config config; config.node_id = 9; config.link_id = 1;
    config.uplink_stream = 1; config.downlink_stream = 2;
    config.air_listen_port = input_port; config.radio_control_port = 0;
    config.air_target_host = "127.0.0.1"; config.air_target_port = air_port;
    config.plaintext_fec_k = 2; config.plaintext_fec_n = 3;
    config.log_interval = 50;
    close(input_reservation);
    pid_t child = fork(); assert(child >= 0);
    if (child == 0) {
        prctl(PR_SET_PDEATHSIG, SIGKILL); close(tun[0]); close(output_pipe[0]);
        assert(dup2(output_pipe[1], STDOUT_FILENO) >= 0); close(output_pipe[1]);
        rearm_tun_fd = tun[1]; run_client(config, 42); _exit(0);
    }
    close(tun[1]); close(output_pipe[1]);
    const uint8_t data = 0x45;
    assert(send(tun[0], &data, 1, 0) == 1);
    assert(rearm_receive(air, 500, WFB_CONTROL_TYPE_READY));
    rearm_grant(command, input_port, 1, 100, 100);

    uint64_t first_nonce = 0; uint8_t first_flags = 0;
    assert(rearm_receive_data_frame(air, 500, &first_nonce, &first_flags));
    assert((first_nonce & 0xff) == 0 && first_flags == 0);
    const uint64_t first_block = first_nonce >> 8;
    // 首 block 的合法 idle close 在授权有效期间完成并被观察到。
    uint64_t close_nonce[2] = {}; uint8_t close_flags[2] = {};
    assert(rearm_receive_data_frame(air, 100, &close_nonce[0], &close_flags[0]));
    assert(rearm_receive_data_frame(air, 100, &close_nonce[1], &close_flags[1]));
    assert((close_nonce[0] >> 8) == first_block && (close_nonce[0] & 0xff) == 1);
    assert((close_nonce[1] >> 8) == first_block && (close_nonce[1] & 0xff) == 2);
    assert(close_flags[0] == WFB_PACKET_FEC_ONLY);

    usleep(120000);
    assert(send(tun[0], &data, 1, 0) == 1);
    assert(!rearm_receive_data_frame(air, 100, &first_nonce, &first_flags));

    rearm_grant(command, input_port, 2, 100, 3000);
    uint64_t pending_nonce = 0; uint8_t pending_flags = 0;
    assert(rearm_receive_data_frame(air, 500, &pending_nonce, &pending_flags));
    assert((pending_nonce >> 8) == first_block + 1);
    assert((pending_nonce & 0xff) == 0 && pending_flags == 0);

    close(output_pipe[0]); close(tun[0]); close(air); close(command);
    std::cout << "native run_client unauthorized pending FEC deadline/order: PASS\n";
}


static void test_native_rearm() {
    uint16_t air_port, input_port, control_port, unused;
    int air = rearm_udp(&air_port), input_reservation = rearm_udp(&input_port);
    int control_reservation = rearm_udp(&control_port), command = rearm_udp(&unused);
    int tun[2]; assert(socketpair(AF_UNIX, SOCK_DGRAM, 0, tun) == 0);
    Config config; config.node_id = 9; config.link_id = 1;
    config.uplink_stream = 1; config.downlink_stream = 2;
    config.air_listen_port = input_port; config.radio_control_port = control_port;
    config.air_target_host = "127.0.0.1"; config.air_target_port = air_port;
    close(input_reservation); close(control_reservation);
    pid_t child = fork(); assert(child >= 0);
    if (child == 0) { prctl(PR_SET_PDEATHSIG, SIGKILL); close(tun[0]); rearm_tun_fd = tun[1]; run_client(config, 42); _exit(0); }
    struct ChildCleanup { pid_t pid; ~ChildCleanup() { kill(pid, SIGKILL); waitpid(pid, nullptr, 0); } } cleanup{child};
    close(tun[1]);
    const uint8_t data = 0x45;
    assert(send(tun[0], &data, 1, 0) == 1);
    assert(rearm_receive(air, 500, WFB_CONTROL_TYPE_READY));
    // 宿主连续成功切信道后，各自的新 epoch 都应在 4 秒前立即声明。
    for (uint64_t epoch : {1000ULL, 2000ULL}) {
        rearm_apply_radio(command, control_port, epoch == 1000 ? 3 : 4);
        assert(!rearm_receive(air, 80, WFB_CONTROL_TYPE_READY));
        assert(rearm_command(command, control_port, epoch) == 0);
        assert(rearm_receive(air, 500, WFB_CONTROL_TYPE_READY));
        assert(rearm_command(command, control_port, epoch) == 0);
        assert(!rearm_receive(air, 80, WFB_CONTROL_TYPE_READY));
    }
    assert(rearm_command(command, control_port, 1000) == ESTALE);
    assert(!rearm_receive(air, 80, WFB_CONTROL_TYPE_READY));
    assert(rearm_command(command, control_port, 0) == EINVAL);
    assert(rearm_command(command, control_port, 3000, 14) == EINVAL);
    rearm_grant(command, input_port, 10);
    assert(rearm_receive(air, 500, WFB_PACKET_DATA));
    // 空队列不声明；重武装必须撤销仍有 3 秒有效期的授权。
    assert(rearm_command(command, control_port, 3000) == 0);
    assert(!rearm_receive(air, 80, WFB_CONTROL_TYPE_READY));
    assert(send(tun[0], &data, 1, 0) == 1);
    assert(rearm_receive(air, 500, WFB_CONTROL_TYPE_READY));
    rearm_grant(command, input_port, 10);
    rearm_grant(command, input_port, 9);
    assert(!rearm_receive(air, 80, WFB_PACKET_DATA));
    rearm_grant(command, input_port, 11);
    assert(rearm_receive(air, 500, WFB_PACKET_DATA));
    // 会话切换后再重武装，退役会话仍不能重新授权。
    rearm_grant(command, input_port, 0, 200);
    // 观察真实数据发送，确认新会话已经生效后才通知再次换信道。
    assert(send(tun[0], &data, 1, 0) == 1);
    assert(rearm_receive(air, 500, WFB_PACKET_DATA));
    rearm_apply_radio(command, control_port, 5);
    assert(rearm_command(command, control_port, 4000) == 0);
    assert(send(tun[0], &data, 1, 0) == 1);
    assert(rearm_receive(air, 500, WFB_CONTROL_TYPE_READY));
    rearm_grant(command, input_port, 12, 100);
    rearm_grant(command, input_port, 0, 200);
    assert(!rearm_receive(air, 80, WFB_PACKET_DATA));
    rearm_grant(command, input_port, 1, 200);
    assert(rearm_receive(air, 500, WFB_PACKET_DATA));
    close(tun[0]); close(air); close(command);
    std::cout << "native run_client UDP rearm, READY, authorization and sequence: PASS\n";
}

// 只观察真实客户端的 UDP 输出；不读取 READY 私有状态或重试常量。
static void test_native_ready_retry_loss() {
    uint16_t air_port, input_port, unused;
    int air = rearm_udp(&air_port), input_reservation = rearm_udp(&input_port);
    int command = rearm_udp(&unused);
    int tun[2]; assert(socketpair(AF_UNIX, SOCK_DGRAM, 0, tun) == 0);
    Config config; config.node_id = 9; config.link_id = 1;
    config.uplink_stream = 1; config.downlink_stream = 2;
    config.air_listen_port = input_port; config.radio_control_port = 0;
    config.air_target_host = "127.0.0.1"; config.air_target_port = air_port;
    // READY 的重试不能依赖日志定时器提供唤醒。
    config.log_interval = 5000;
    close(input_reservation);
    pid_t child = fork(); assert(child >= 0);
    if (child == 0) { prctl(PR_SET_PDEATHSIG, SIGKILL); close(tun[0]); rearm_tun_fd = tun[1]; run_client(config, 42); _exit(0); }
    struct ChildCleanup { pid_t pid; ~ChildCleanup() { kill(pid, SIGKILL); waitpid(pid, nullptr, 0); } } cleanup{child};
    close(tun[1]);
    assert(!rearm_receive(air, 1200, WFB_CONTROL_TYPE_READY));
    const uint8_t data = 0x45;
    assert(send(tun[0], &data, 1, 0) == 1);
    assert(rearm_receive(air, 500, WFB_CONTROL_TYPE_READY));
    const uint64_t first_ready_ms = get_time_ms();
    // 丢弃首个 READY；第二个 READY 到达后模拟其 GRANT 丢失（不交给客户端）。
    for (int loss = 0; loss < 2; ++loss) {
        const uint64_t before_ms = get_time_ms();
        const bool received = rearm_receive(air, 1200, WFB_CONTROL_TYPE_READY);
        const uint64_t elapsed_ms = get_time_ms() - before_ms;
        std::cerr << "READY loss retry " << loss + 1 << ": received=" << received
                  << " elapsed_ms=" << elapsed_ms << std::endl;
        assert(received);
        assert(elapsed_ms >= 800 && elapsed_ms <= 1800);
    }
    const uint64_t recovery_ms = get_time_ms() - first_ready_ms;
    assert(recovery_ms <= 2400);
    rearm_grant(command, input_port, 1);
    assert(rearm_receive(air, 500, WFB_PACKET_DATA));
    // 仍有有效的三秒授权时，新 pending 数据应直接发送，不重新声明 READY。
    assert(!rearm_receive(air, 1100, WFB_CONTROL_TYPE_READY));
    assert(send(tun[0], &data, 1, 0) == 1);
    assert(rearm_receive(air, 500, WFB_PACKET_DATA));
    // 跨过授权到期与重试周期，空队列仍不发送 READY。
    assert(!rearm_receive(air, 2200, WFB_CONTROL_TYPE_READY));
    assert(send(tun[0], &data, 1, 0) == 1);
    assert(rearm_receive(air, 500, WFB_CONTROL_TYPE_READY));
    rearm_grant(command, input_port, 2);
    assert(rearm_receive(air, 500, WFB_PACKET_DATA));
    close(tun[0]); close(air); close(command);
    std::cout << "native run_client READY/GRANT loss recovery_ms=" << recovery_ms
              << ", idle and authorized pending: PASS" << std::endl;
}
