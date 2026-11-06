#include "v6_uplink_rearm_test.hpp"
#include <cassert>
#include <arpa/inet.h>
#include <cerrno>
#include <cstring>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>
#include <cstdint>
#include <iostream>

#include "v6_radio_control.hpp"
#include "v6_plaintext_fec_tx.hpp"

static cmd_req_t make_radio_request(uint8_t short_gi,
                                    uint8_t bandwidth,
                                    uint8_t mcs_index)
{
    cmd_req_t request = {};
    request.req_id = htobe32(7);
    request.cmd_id = CMD_SET_RADIO;
    request.u.cmd_set_radio.stbc = 0;
    request.u.cmd_set_radio.ldpc = false;
    request.u.cmd_set_radio.short_gi = short_gi != 0;
    request.u.cmd_set_radio.bandwidth = bandwidth;
    request.u.cmd_set_radio.mcs_index = mcs_index;
    request.u.cmd_set_radio.vht_mode = false;
    request.u.cmd_set_radio.vht_nss = 1;
    return request;
}

static void test_udp_debug_radio_update()
{
    const int receive_fd = socket(AF_INET, SOCK_DGRAM, 0);
    assert(receive_fd >= 0);
    sockaddr_in receive_address = {};
    receive_address.sin_family = AF_INET;
    receive_address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    receive_address.sin_port = htons(0);
    assert(bind(receive_fd,
                reinterpret_cast<const sockaddr *>(&receive_address),
                sizeof(receive_address)) == 0);
    socklen_t address_size = sizeof(receive_address);
    assert(getsockname(receive_fd,
                       reinterpret_cast<sockaddr *>(&receive_address),
                       &address_size) == 0);

    V6PlaintextFecRadioConfig radio = {};
    radio.bandwidth = 40;
    radio.mcs_index = 3;
    radio.short_gi = true;
    V6PlaintextFecTransmitter transmitter("127.0.0.1",
                                          ntohs(receive_address.sin_port),
                                          0,
                                          1,
                                          radio,
                                          1,
                                          1,
                                          42);
    const uint8_t payload = 0x5a;
    assert(transmitter.send_payload(&payload, sizeof(payload)));

    uint8_t packet[sizeof(wrxfwd_t) + MAX_FORWARDER_PACKET_SIZE] = {};
    assert(recv(receive_fd, packet, sizeof(packet), 0) >= static_cast<ssize_t>(sizeof(wrxfwd_t)));
    const wrxfwd_t *first_header = reinterpret_cast<const wrxfwd_t *>(packet);
    assert(first_header->bandwidth == 40);
    assert(first_header->mcs_index == 3);

    radio.bandwidth = 20;
    radio.mcs_index = 5;
    transmitter.update_radio_config(radio);
    assert(transmitter.send_payload(&payload, sizeof(payload)));
    memset(packet, 0, sizeof(packet));
    assert(recv(receive_fd, packet, sizeof(packet), 0) >= static_cast<ssize_t>(sizeof(wrxfwd_t)));
    const wrxfwd_t *second_header = reinterpret_cast<const wrxfwd_t *>(packet);
    assert(second_header->bandwidth == 20);
    assert(second_header->mcs_index == 5);
    close(receive_fd);
}

static void test_udp_control_and_data_mcs()
{
    uint16_t port;
    const int receive_fd = rearm_udp(&port);
    V6RadioConfig radio = {};
    radio.bandwidth = 40;
    radio.mcs_index = 3;
    radio.short_gi = true;
    AirTransmitter transmitter("127.0.0.1", port, 0, 1, radio, 1, 1, 42);
    const auto receive_header = [&](uint8_t bandwidth, uint8_t mcs, int type, bool control) {
        pollfd pending = {receive_fd, POLLIN, 0};
        assert(poll(&pending, 1, 500) == 1);
        uint8_t packet[4096] = {};
        assert(recv(receive_fd, packet, sizeof(packet), 0) > static_cast<ssize_t>(sizeof(wrxfwd_t)));
        const auto *header = reinterpret_cast<const wrxfwd_t *>(packet);
        assert(header->bandwidth == bandwidth);
        assert(header->mcs_index == mcs);
        const auto *envelope = reinterpret_cast<const wcontrol_envelope_hdr_t *>(packet + sizeof(wrxfwd_t));
        assert(envelope->packet_type == (control ? WFB_PACKET_CONTROL : WFB_PACKET_DATA));
        if (control) assert(envelope->control_type == type);
    };
    const uint8_t payload = 0x45;
    // 普通 TUN 数据（含心跳与 ACK）沿用方向 MCS，调度帧使用 MCS0。
    for (int generation = 0; generation < 2; ++generation)
    {
        transmitter.send_data(&payload, sizeof(payload));
        receive_header(generation == 0 ? 40 : 20, generation == 0 ? 3 : 5, WFB_PACKET_DATA, false);
        transmitter.send_ready(1);
        receive_header(generation == 0 ? 40 : 20, 0, WFB_CONTROL_TYPE_READY, true);
        transmitter.send_grant(1, 9, 7, 3000);
        receive_header(generation == 0 ? 40 : 20, 0, WFB_CONTROL_TYPE_GRANT, true);
        if (generation == 0)
        {
            radio.bandwidth = 20;
            radio.mcs_index = 5;
            radio.short_gi = false;
            transmitter.apply_radio_config(radio);
        }
    }
    close(receive_fd);
}

int main()
{
    V6RadioConfig current = {};
    current.bandwidth = 40;
    current.mcs_index = 3;
    current.short_gi = true;
    V6RadioConfig target = {};

    const cmd_req_t request = make_radio_request(1, 20, 5);
    assert(validate_v6_radio_request(request, current, &target));
    assert(target.bandwidth == 20);
    assert(target.mcs_index == 5);
    assert(target.short_gi);

    cmd_req_t rearm = {};
    rearm.cmd_id = CMD_REARM_UPLINK;
    rearm.u.cmd_rearm_uplink.epoch = htobe64(1);
    // server 不传客户端回调，必须明确拒绝重武装。
    const auto server_rearm = handle_v6_radio_command(
        rearm, 13, current, 8, 14, [](const V6RadioConfig &) {});
    assert(ntohl(server_rearm.response.rc) == ENOTSUP);

    bool applied = false;
    const V6RadioCommandResponse set_response = handle_v6_radio_command(
        request,
        offsetof(cmd_req_t, u) + sizeof(request.u.cmd_set_radio),
        current,
        8,
        14,
        [&](const V6RadioConfig &applied_config) {
            applied = applied_config.bandwidth == 20 && applied_config.mcs_index == 5;
        });
    assert(set_response.response.req_id == request.req_id);
    assert(ntohl(set_response.response.rc) == 0);
    assert(applied);

    cmd_req_t get_radio = {};
    get_radio.req_id = htobe32(8);
    get_radio.cmd_id = CMD_GET_RADIO;
    const V6RadioCommandResponse radio_response = handle_v6_radio_command(
        get_radio, offsetof(cmd_req_t, u), current, 8, 14, [](const V6RadioConfig &) {});
    assert(radio_response.response.req_id == get_radio.req_id);
    assert(ntohl(radio_response.response.rc) == 0);
    assert(radio_response.payload_size == sizeof(radio_response.response.u.cmd_get_radio));
    assert(radio_response.response.u.cmd_get_radio.bandwidth == 40);
    assert(radio_response.response.u.cmd_get_radio.mcs_index == 3);
    assert(radio_response.response.u.cmd_get_radio.short_gi);

    cmd_req_t get_fec = {};
    get_fec.req_id = htobe32(9);
    get_fec.cmd_id = CMD_GET_FEC;
    const V6RadioCommandResponse fec_response = handle_v6_radio_command(
        get_fec, offsetof(cmd_req_t, u), current, 8, 14, [](const V6RadioConfig &) {});
    assert(fec_response.response.req_id == get_fec.req_id);
    assert(ntohl(fec_response.response.rc) == 0);
    assert(fec_response.response.u.cmd_get_fec.k == 8);
    assert(fec_response.response.u.cmd_get_fec.n == 14);

    cmd_req_t set_fec = {};
    set_fec.req_id = htobe32(10);
    set_fec.cmd_id = CMD_SET_FEC;
    set_fec.u.cmd_set_fec.k = 1;
    set_fec.u.cmd_set_fec.n = 1;
    const V6RadioCommandResponse rejected_fec = handle_v6_radio_command(
        set_fec,
        offsetof(cmd_req_t, u) + sizeof(set_fec.u.cmd_set_fec),
        current,
        8,
        14,
        [](const V6RadioConfig &) {});
    assert(rejected_fec.response.req_id == set_fec.req_id);
    assert(ntohl(rejected_fec.response.rc) == ENOTSUP);

    const V6RadioTarget headers = build_v6_radio_target(target);
    assert(headers.data_radiotap[MCS_IDX_OFF] == 5);
    assert(headers.data_radiotap[MCS_FLAGS_OFF] == headers.control_radiotap[MCS_FLAGS_OFF]);
    assert(headers.control_radiotap[MCS_FLAGS_OFF] ==
           (IEEE80211_RADIOTAP_MCS_BW_20 | IEEE80211_RADIOTAP_MCS_SGI));
    assert(headers.control_radiotap[MCS_IDX_OFF] == 0);

    cmd_req_t invalid = request;
    invalid.u.cmd_set_radio.short_gi = false;
    assert(!validate_v6_radio_request(invalid, current, &target));
    invalid = request;
    invalid.u.cmd_set_radio.stbc = 1;
    assert(!validate_v6_radio_request(invalid, current, &target));
    invalid = request;
    invalid.u.cmd_set_radio.ldpc = true;
    assert(!validate_v6_radio_request(invalid, current, &target));
    invalid = request;
    invalid.u.cmd_set_radio.vht_mode = true;
    assert(!validate_v6_radio_request(invalid, current, &target));
    invalid = request;
    invalid.u.cmd_set_radio.bandwidth = 80;
    assert(!validate_v6_radio_request(invalid, current, &target));
    invalid = request;
    invalid.u.cmd_set_radio.mcs_index = 32;
    assert(!validate_v6_radio_request(invalid, current, &target));

    const V6RadioTarget forty_headers = build_v6_radio_target(current);
    assert(forty_headers.data_radiotap[MCS_IDX_OFF] == 3);
    assert(forty_headers.data_radiotap[MCS_FLAGS_OFF] == forty_headers.control_radiotap[MCS_FLAGS_OFF]);
    assert(forty_headers.control_radiotap[MCS_FLAGS_OFF] ==
           (IEEE80211_RADIOTAP_MCS_BW_40 | IEEE80211_RADIOTAP_MCS_SGI));
    assert(forty_headers.control_radiotap[MCS_IDX_OFF] == 0);

    // 控制帧固定 MCS0，带宽与长短 GI 仍跟随目标配置。
    V6RadioConfig long_gi = current;
    long_gi.short_gi = false;
    const V6RadioTarget long_headers = build_v6_radio_target(long_gi);
    assert(long_headers.data_radiotap[MCS_IDX_OFF] == 3);
    assert(long_headers.control_radiotap[MCS_IDX_OFF] == 0);
    assert(long_headers.control_radiotap[MCS_FLAGS_OFF] == IEEE80211_RADIOTAP_MCS_BW_40);
    assert(long_headers.data_radiotap[MCS_FLAGS_OFF] == IEEE80211_RADIOTAP_MCS_BW_40);

    test_udp_debug_radio_update();
    test_udp_control_and_data_mcs();
    test_native_rearm();
    test_native_ready_retry_loss();
    test_native_unauthorized_fec_deadline();
    std::cout << "v6 radio command validation and control/data radiotap sync: PASS\n";
    return 0;
}
