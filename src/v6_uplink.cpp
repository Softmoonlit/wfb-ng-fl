// -*- C++ -*-
//
// v6 第一切片：trusted_plaintext 上行 TCP per-client 集成式链路层最小守护进程

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <getopt.h>
#include <inttypes.h>
#include <linux/if.h>
#include <linux/if_packet.h>
#include <linux/if_tun.h>
#include <netinet/in.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/poll.h>
#include <sys/socket.h>
#include <unistd.h>

#include <algorithm>
#include <functional>
#include <map>
#include <memory>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

#include "control_envelope.hpp"
#include "rx.hpp"
#include "token_authorization.hpp"
#include "token_scheduler.hpp"
#include "wifibroadcast.hpp"
#include "v6_uplink_queue.hpp"

using namespace std;

namespace {

const uint32_t kIdleSleepMs = 10;
const uint64_t kReadyRedeclareTimeoutMs = 4000;

struct AirReadyState {
    enum Phase {
        UNDECLARED,
        DECLARED_WAITING_FOR_GRANT,
        GRANT_SEEN_WAITING_FOR_NEXT,
    };

    uint8_t node_id = 0;
    bool declaration_in_flight = false;
    uint64_t last_declare_at_ms = 0;
    Phase phase = UNDECLARED;
};

struct FeedbackWindowState {
    bool enabled = false;
    bool active = false;
    uint32_t period_ms = 0;
    uint32_t duration_ms = 0;
    uint64_t next_open_at_ms = 0;
    uint64_t next_grant_at_ms = 0;
    uint64_t window_end_at_ms = 0;
    size_t next_client_index = 0;
    uint8_t current_node_id = 0;
    uint64_t current_grant_sequence = 0;
    uint64_t current_slot_expires_at_ms = 0;
    bool current_slot_hit_recorded = false;
    uint64_t open_count = 0;
    uint64_t close_count = 0;
    map<uint8_t, uint64_t> slot_hit_total_by_node;
};

struct AirTransmitter {
    int sockfd = -1;
    sockaddr_in saddr = {};
    vector<int> raw_sockfds;
    uint64_t block_idx = 0;
    uint32_t channel_id = 0;
    uint16_t ieee80211_seq = 0;

    AirTransmitter(const string &host, int port, int snd_buf)
    {
        sockfd = socket(AF_INET, SOCK_DGRAM, 0);
        if (sockfd < 0)
        {
            throw runtime_error(string("创建发送 socket 失败: ") + strerror(errno));
        }
        if (snd_buf > 0)
        {
            if (setsockopt(sockfd, SOL_SOCKET, SO_SNDBUF, (const void *)&snd_buf, sizeof(snd_buf)) != 0)
            {
                close(sockfd);
                throw runtime_error(string("设置 SO_SNDBUF 失败: ") + strerror(errno));
            }
        }

        memset(&saddr, '\0', sizeof(saddr));
        saddr.sin_family = AF_INET;
        saddr.sin_addr.s_addr = inet_addr(host.c_str());
        saddr.sin_port = htons((unsigned short)port);
    }

    AirTransmitter(const vector<string> &interfaces, uint32_t channel_id)
        : channel_id(channel_id)
    {
        if (interfaces.empty())
        {
            throw runtime_error("raw air 模式至少需要一个无线接口");
        }
        for (size_t i = 0; i < interfaces.size(); ++i)
        {
            raw_sockfds.push_back(open_raw_socket(interfaces[i]));
        }
    }

    ~AirTransmitter()
    {
        if (sockfd >= 0)
        {
            close(sockfd);
        }
        for (size_t i = 0; i < raw_sockfds.size(); ++i)
        {
            close(raw_sockfds[i]);
        }
    }

    void send_air_datagram(const uint8_t *packet, size_t packet_size)
    {
        if (!raw_sockfds.empty())
        {
            send_raw_air_datagram(packet, packet_size);
            return;
        }
        send_udp_air_datagram(packet, packet_size);
    }

    void send_data(const uint8_t *payload, size_t payload_size)
    {
        if (payload_size > MAX_PAYLOAD_SIZE)
        {
            throw runtime_error("数据面 payload 超过 MAX_PAYLOAD_SIZE");
        }

        uint8_t packet[sizeof(wblock_hdr_t) + sizeof(wpacket_hdr_t) + MAX_PAYLOAD_SIZE] = {};
        const uint64_t nonce = (block_idx++ << 8);
        wblock_hdr_t *block_hdr = reinterpret_cast<wblock_hdr_t *>(packet);
        block_hdr->packet_type = WFB_PACKET_DATA;
        block_hdr->data_nonce = htobe64(nonce);

        wpacket_hdr_t *packet_hdr = reinterpret_cast<wpacket_hdr_t *>(packet + sizeof(wblock_hdr_t));
        packet_hdr->flags = 0;
        packet_hdr->packet_size = htobe16(static_cast<uint16_t>(payload_size));
        memcpy(packet + sizeof(wblock_hdr_t) + sizeof(wpacket_hdr_t), payload, payload_size);
        send_air_datagram(packet, sizeof(wblock_hdr_t) + sizeof(wpacket_hdr_t) + payload_size);
    }

    void send_ready(uint8_t source_node)
    {
        uint8_t packet[sizeof(wcontrol_envelope_hdr_t)] = {};
        wcontrol_envelope_hdr_t *envelope = reinterpret_cast<wcontrol_envelope_hdr_t *>(packet);
        envelope->packet_type = WFB_PACKET_CONTROL;
        envelope->magic = htobe16(WFB_CONTROL_MAGIC);
        envelope->version = WFB_CONTROL_VERSION;
        envelope->control_type = WFB_CONTROL_TYPE_READY;
        envelope->source_node = source_node;
        envelope->target_node = 0;
        envelope->reserved = 0;
        envelope->sequence = htobe64(0);
        send_air_datagram(packet, sizeof(packet));
    }

    void send_grant(uint8_t source_node,
                    uint8_t target_node,
                    uint64_t sequence,
                    uint32_t duration_ms)
    {
        uint8_t packet[sizeof(wcontrol_envelope_hdr_t) + sizeof(wcontrol_grant_payload_t)] = {};
        wcontrol_envelope_hdr_t *envelope = reinterpret_cast<wcontrol_envelope_hdr_t *>(packet);
        envelope->packet_type = WFB_PACKET_CONTROL;
        envelope->magic = htobe16(WFB_CONTROL_MAGIC);
        envelope->version = WFB_CONTROL_VERSION;
        envelope->control_type = WFB_CONTROL_TYPE_GRANT;
        envelope->source_node = source_node;
        envelope->target_node = target_node;
        envelope->reserved = 0;
        envelope->sequence = htobe64(sequence);

        wcontrol_grant_payload_t *payload = reinterpret_cast<wcontrol_grant_payload_t *>(packet + sizeof(wcontrol_envelope_hdr_t));
        payload->duration_ms = htobe32(duration_ms);
        send_air_datagram(packet, sizeof(packet));
    }

private:
    static int open_raw_socket(const string &interface)
    {
        int fd = socket(PF_PACKET, SOCK_RAW, 0);
        if (fd < 0)
        {
            throw runtime_error(string("创建 raw air socket 失败: ") + strerror(errno));
        }

        const int optval = 1;
        if (setsockopt(fd, SOL_PACKET, PACKET_QDISC_BYPASS, (const void *)&optval, sizeof(optval)) != 0)
        {
            close(fd);
            throw runtime_error(string("设置 PACKET_QDISC_BYPASS 失败: ") + strerror(errno));
        }

        struct ifreq ifr = {};
        strncpy(ifr.ifr_name, interface.c_str(), sizeof(ifr.ifr_name) - 1);
        if (ioctl(fd, SIOCGIFINDEX, &ifr) < 0)
        {
            close(fd);
            throw runtime_error(string("获取无线接口索引失败: ") + interface + ": " + strerror(errno));
        }

        struct sockaddr_ll sll = {};
        sll.sll_family = AF_PACKET;
        sll.sll_ifindex = ifr.ifr_ifindex;
        sll.sll_protocol = 0;
        if (::bind(fd, reinterpret_cast<struct sockaddr *>(&sll), sizeof(sll)) < 0)
        {
            close(fd);
            throw runtime_error(string("绑定 raw air socket 失败: ") + interface + ": " + strerror(errno));
        }
        return fd;
    }

    void send_udp_air_datagram(const uint8_t *packet, size_t packet_size)
    {
        uint8_t buffer[sizeof(wrxfwd_t) + MAX_FORWARDER_PACKET_SIZE] = {};
        if (packet_size > MAX_FORWARDER_PACKET_SIZE)
        {
            throw runtime_error("空口报文过大");
        }

        wrxfwd_t *header = reinterpret_cast<wrxfwd_t *>(buffer);
        memset(header->antenna, 0xff, sizeof(header->antenna));
        memset(header->rssi, SCHAR_MIN, sizeof(header->rssi));
        memset(header->noise, SCHAR_MAX, sizeof(header->noise));
        header->wlan_idx = 0;
        header->mcs_index = 1;
        header->bandwidth = 20;
        header->freq = htons(4321);
        header->antenna[0] = 0;
        header->rssi[0] = -42;
        header->noise[0] = -70;
        memcpy(buffer + sizeof(wrxfwd_t), packet, packet_size);

        const ssize_t sent = sendto(sockfd,
                                    buffer,
                                    sizeof(wrxfwd_t) + packet_size,
                                    MSG_DONTWAIT,
                                    reinterpret_cast<const sockaddr *>(&saddr),
                                    sizeof(saddr));
        if (sent != static_cast<ssize_t>(sizeof(wrxfwd_t) + packet_size))
        {
            throw runtime_error(string("发送空口 UDP 报文失败: ") + strerror(errno));
        }
    }

    void send_raw_air_datagram(const uint8_t *packet, size_t packet_size)
    {
        if (packet_size > MAX_FORWARDER_PACKET_SIZE)
        {
            throw runtime_error("空口报文过大");
        }

        uint8_t ieee_hdr[sizeof(ieee80211_header)] = {};
        memcpy(ieee_hdr, ieee80211_header, sizeof(ieee_hdr));
        const uint32_t channel_id_be = htobe32(channel_id);
        memcpy(ieee_hdr + SRC_MAC_THIRD_BYTE, &channel_id_be, sizeof(channel_id_be));
        memcpy(ieee_hdr + DST_MAC_THIRD_BYTE, &channel_id_be, sizeof(channel_id_be));
        ieee_hdr[FRAME_SEQ_LB] = ieee80211_seq & 0xff;
        ieee_hdr[FRAME_SEQ_HB] = (ieee80211_seq >> 8) & 0xff;
        ieee80211_seq += 16;

        uint8_t buffer[sizeof(radiotap_header_ht) + sizeof(ieee80211_header) + MAX_FORWARDER_PACKET_SIZE] = {};
        memcpy(buffer, radiotap_header_ht, sizeof(radiotap_header_ht));
        memcpy(buffer + sizeof(radiotap_header_ht), ieee_hdr, sizeof(ieee_hdr));
        memcpy(buffer + sizeof(radiotap_header_ht) + sizeof(ieee_hdr), packet, packet_size);
        const size_t frame_size = sizeof(radiotap_header_ht) + sizeof(ieee_hdr) + packet_size;

        for (size_t i = 0; i < raw_sockfds.size(); ++i)
        {
            const ssize_t sent = send(raw_sockfds[i], buffer, frame_size, 0);
            if (sent != static_cast<ssize_t>(frame_size))
            {
                throw runtime_error(string("发送 raw air 报文失败: ") + strerror(errno));
            }
        }
    }

    AirTransmitter(const AirTransmitter &) = delete;
    AirTransmitter &operator=(const AirTransmitter &) = delete;
};

struct ClientTarget {
    uint8_t node_id = 0;
    uint32_t tun_ipv4 = 0;
    uint32_t host_ipv4 = 0;
    string host;
    int port = 0;
    shared_ptr<AirTransmitter> transmitter;

    ClientTarget() = default;
    ClientTarget(ClientTarget &&) = default;
    ClientTarget &operator=(ClientTarget &&) = default;

private:
    ClientTarget(const ClientTarget &) = delete;
    ClientTarget &operator=(const ClientTarget &) = delete;
};

typedef function<shared_ptr<AirTransmitter>()> RawAirTransmitterFactory;
typedef function<shared_ptr<AirTransmitter>(const ClientTarget &)> UdpAirTransmitterFactory;

void initialize_client_target_transmitters(vector<ClientTarget> &targets,
                                          bool raw_air_mode,
                                          const RawAirTransmitterFactory &raw_factory,
                                          const UdpAirTransmitterFactory &udp_factory)
{
    if (raw_air_mode)
    {
        shared_ptr<AirTransmitter> shared_raw_transmitter = raw_factory();
        for (size_t i = 0; i < targets.size(); ++i)
        {
            targets[i].transmitter = shared_raw_transmitter;
        }
        return;
    }

    for (size_t i = 0; i < targets.size(); ++i)
    {
        targets[i].transmitter = udp_factory(targets[i]);
    }
}

struct Config {
    string role;
    string tun_name;
    string tun_addr;
    uint8_t node_id = 0;
    uint32_t link_id = 0;
    uint8_t stream = 0;
    uint64_t epoch = 0;
    int plaintext_fec_k = 1;
    int plaintext_fec_n = 1;
    int rcv_buf = 0;
    int snd_buf = 0;
    int log_interval = 1000;
    int air_listen_port = 0;
    string air_target_host;
    int air_target_port = 0;
    vector<string> air_interfaces;
    uint32_t grant_duration_ms = 100;
    uint32_t guard_interval_ms = 10;
    uint32_t feedback_window_period_ms = 0;
    uint32_t feedback_window_duration_ms = 0;
    uint32_t uplink_pause_threshold_bytes = 131072;
    uint32_t uplink_resume_threshold_bytes = 65536;
    uint32_t uplink_queue_packets_limit = 64;
    uint32_t downlink_pause_threshold_bytes = 131072;
    uint32_t downlink_resume_threshold_bytes = 65536;
    uint32_t downlink_queue_packets_limit = 64;
    string queue_summary_file;
    vector<ClientTarget> client_targets;
    vector<uint8_t> known_clients;
};

const char *ready_phase_name(AirReadyState::Phase phase)
{
    switch (phase)
    {
    case AirReadyState::UNDECLARED:
        return "undeclared";
    case AirReadyState::DECLARED_WAITING_FOR_GRANT:
        return "declared_waiting_for_grant";
    case AirReadyState::GRANT_SEEN_WAITING_FOR_NEXT:
        return "grant_seen_waiting_for_next";
    }

    return "unknown";
}

void log_ready_state_transition(uint8_t node_id,
                                AirReadyState::Phase before,
                                AirReadyState::Phase after,
                                const char *event_name,
                                uint64_t now_ms)
{
    IPC_MSG("%s node_id=%u state=%s->%s at_ms=%" PRIu64 "\n",
            event_name,
            static_cast<unsigned>(node_id),
            ready_phase_name(before),
            ready_phase_name(after),
            now_ms);
    IPC_MSG_SEND();
}

uint32_t parse_u32(const string &value, const char *name, bool allow_zero = false)
{
    if (value.empty())
    {
        throw invalid_argument(string(name) + " 不能为空");
    }

    char *end = NULL;
    errno = 0;
    unsigned long parsed = strtoul(value.c_str(), &end, 10);
    if (errno != 0 || end == NULL || *end != '\0' || parsed > UINT32_MAX)
    {
        throw invalid_argument(string(name) + " 非法: " + value);
    }

    if (!allow_zero && parsed == 0)
    {
        throw invalid_argument(string(name) + " 必须大于 0");
    }

    return static_cast<uint32_t>(parsed);
}

uint8_t parse_node_id(const string &value, const char *name = "node_id")
{
    uint32_t parsed = parse_u32(value, name);
    if (parsed > 255)
    {
        throw invalid_argument(string(name) + " 超出范围: " + value);
    }
    return static_cast<uint8_t>(parsed);
}

uint32_t parse_ipv4(const string &value, const char *name)
{
    in_addr addr = {};
    if (inet_aton(value.c_str(), &addr) == 0)
    {
        throw invalid_argument(string(name) + " 非法 IPv4: " + value);
    }
    return ntohl(addr.s_addr);
}
bool is_ipv4_multicast(uint32_t ipv4)
{
    return (ipv4 & 0xf0000000u) == 0xe0000000u;
}

bool is_ipv4_limited_broadcast(uint32_t ipv4)
{
    return ipv4 == 0xffffffffu;
}

bool parse_ipv4_endpoints(const uint8_t *buf, size_t size, uint32_t *src_ipv4, uint32_t *dest_ipv4)
{
    if (size < 20)
    {
        return false;
    }

    const uint8_t version = buf[0] >> 4;
    const uint8_t ihl = (buf[0] & 0x0f) * 4;
    if (version != 4 || ihl < 20 || size < ihl)
    {
        return false;
    }

    uint32_t src = 0;
    uint32_t dest = 0;
    memcpy(&src, buf + 12, sizeof(src));
    memcpy(&dest, buf + 16, sizeof(dest));
    if (src_ipv4 != NULL)
    {
        *src_ipv4 = ntohl(src);
    }
    if (dest_ipv4 != NULL)
    {
        *dest_ipv4 = ntohl(dest);
    }
    return true;
}

vector<uint8_t> parse_known_clients(const string &value)
{
    if (value.empty())
    {
        throw invalid_argument("known_clients 不能为空");
    }

    vector<uint8_t> nodes;
    set<uint8_t> seen;
    size_t start = 0;
    while (start <= value.size())
    {
        size_t end = value.find(',', start);
        string item = value.substr(start, end == string::npos ? string::npos : end - start);
        if (item.empty())
        {
            throw invalid_argument("known_clients 存在空项");
        }
        uint8_t node_id = parse_node_id(item, "known_client");
        if (!seen.insert(node_id).second)
        {
            throw invalid_argument("known_clients 存在重复 node_id");
        }
        nodes.push_back(node_id);
        if (end == string::npos)
        {
            break;
        }
        start = end + 1;
    }

    return nodes;
}

ClientTarget parse_client_target(const string &value)
{
    vector<string> fields;
    size_t start = 0;
    while (start <= value.size())
    {
        size_t end = value.find(':', start);
        fields.push_back(value.substr(start, end == string::npos ? string::npos : end - start));
        if (end == string::npos)
        {
            break;
        }
        start = end + 1;
    }

    if (fields.size() != 4)
    {
        throw invalid_argument("client_target 格式必须是 node_id:tun_ip:host:port");
    }

    ClientTarget target;
    target.node_id = parse_node_id(fields[0], "client_target.node_id");
    target.tun_ipv4 = parse_ipv4(fields[1], "client_target.tun_ip");
    target.host = fields[2];
    target.host_ipv4 = parse_ipv4(fields[2], "client_target.host");
    target.port = static_cast<int>(parse_u32(fields[3], "client_target.port"));
    return target;
}

vector<string> parse_air_interfaces(const string &value)
{
    if (value.empty())
    {
        throw invalid_argument("air_interface 不能为空");
    }

    vector<string> interfaces;
    size_t start = 0;
    while (start <= value.size())
    {
        size_t end = value.find(',', start);
        string item = value.substr(start, end == string::npos ? string::npos : end - start);
        if (item.empty())
        {
            throw invalid_argument("air_interface 存在空项");
        }
        interfaces.push_back(item);
        if (end == string::npos)
        {
            break;
        }
        start = end + 1;
    }
    return interfaces;
}

void print_usage(const char *progname)
{
    fprintf(stderr,
            "Usage:\n"
            "  %s --role client --tun-name NAME --tun-addr IP/CIDR --node-id N --link-id ID --stream S \\\n"
            "     { --air-listen-port PORT --air-target HOST:PORT | --air-interface IFACE[,IFACE...] } \\\n"
            "     [--uplink-pause-threshold-bytes BYTES] [--uplink-resume-threshold-bytes BYTES] \\\n"
            "     [--uplink-queue-packets-limit N] [--queue-summary-file PATH] [--epoch E] [--fec-k K --fec-n N]\n"
            "  %s --role server --tun-name NAME --tun-addr IP/CIDR --node-id N --link-id ID --stream S \\\n"
            "     { --air-listen-port PORT | --air-interface IFACE[,IFACE...] } --known-clients N1,N2 \\\n"
            "     --client-target N:IP:HOST:PORT [--client-target ...] [--grant-duration-ms MS] \\\n"
            "     [--guard-interval-ms MS] [--feedback-window-period-ms MS] [--feedback-window-duration-ms MS] \\\n"
            "     [--downlink-pause-threshold-bytes BYTES] [--downlink-resume-threshold-bytes BYTES] \\\n"
            "     [--downlink-queue-packets-limit N] [--queue-summary-file PATH] [--epoch E] [--fec-k K --fec-n N]\n",
            progname,
            progname);
}

Config parse_args(int argc, char **argv)
{
    Config config;

    static const option long_options[] = {
        {"role", required_argument, 0, 'r'},
        {"tun-name", required_argument, 0, 't'},
        {"tun-addr", required_argument, 0, 'a'},
        {"node-id", required_argument, 0, 'q'},
        {"link-id", required_argument, 0, 'i'},
        {"stream", required_argument, 0, 'p'},
        {"epoch", required_argument, 0, 'e'},
        {"fec-k", required_argument, 0, 'k'},
        {"fec-n", required_argument, 0, 'n'},
        {"rcv-buf", required_argument, 0, 'R'},
        {"snd-buf", required_argument, 0, 's'},
        {"log-interval", required_argument, 0, 'l'},
        {"air-listen-port", required_argument, 0, 'u'},
        {"air-target", required_argument, 0, 'c'},
        {"known-clients", required_argument, 0, 'm'},
        {"client-target", required_argument, 0, 'x'},
        {"grant-duration-ms", required_argument, 0, 'd'},
        {"guard-interval-ms", required_argument, 0, 'g'},
        {"feedback-window-period-ms", required_argument, 0, 'f'},
        {"feedback-window-duration-ms", required_argument, 0, 'F'},
        {"uplink-pause-threshold-bytes", required_argument, 0, 'b'},
        {"uplink-resume-threshold-bytes", required_argument, 0, 'j'},
        {"uplink-queue-packets-limit", required_argument, 0, 'z'},
        {"downlink-pause-threshold-bytes", required_argument, 0, 'B'},
        {"downlink-resume-threshold-bytes", required_argument, 0, 'J'},
        {"downlink-queue-packets-limit", required_argument, 0, 'Z'},
        {"queue-summary-file", required_argument, 0, 'y'},
        {"air-interface", required_argument, 0, 'W'},
        {"help", no_argument, 0, 'h'},
        {0, 0, 0, 0},
    };

    int opt = 0;
    while ((opt = getopt_long(argc, argv, "r:t:a:q:i:p:e:k:n:R:s:l:u:c:m:x:d:g:f:F:b:j:z:B:J:Z:y:W:h", long_options, NULL)) != -1)
    {
        switch (opt)
        {
        case 'r':
            config.role = optarg;
            break;
        case 't':
            config.tun_name = optarg;
            break;
        case 'a':
            config.tun_addr = optarg;
            break;
        case 'q':
            config.node_id = parse_node_id(optarg);
            break;
        case 'i':
            config.link_id = parse_u32(optarg, "link_id", true) & 0xffffff;
            break;
        case 'p':
            config.stream = static_cast<uint8_t>(parse_u32(optarg, "stream", true) & 0xff);
            break;
        case 'e':
            config.epoch = strtoull(optarg, NULL, 10);
            break;
        case 'k':
            config.plaintext_fec_k = static_cast<int>(parse_u32(optarg, "fec_k"));
            break;
        case 'n':
            config.plaintext_fec_n = static_cast<int>(parse_u32(optarg, "fec_n"));
            break;
        case 'R':
            config.rcv_buf = static_cast<int>(parse_u32(optarg, "rcv_buf", true));
            break;
        case 's':
            config.snd_buf = static_cast<int>(parse_u32(optarg, "snd_buf", true));
            break;
        case 'l':
            config.log_interval = static_cast<int>(parse_u32(optarg, "log_interval"));
            break;
        case 'u':
            config.air_listen_port = static_cast<int>(parse_u32(optarg, "air_listen_port"));
            break;
        case 'c':
        {
            string value = optarg;
            const size_t separator = value.rfind(':');
            if (separator == string::npos || separator == 0 || separator + 1 >= value.size())
            {
                throw invalid_argument("air_target 格式必须是 host:port");
            }
            config.air_target_host = value.substr(0, separator);
            config.air_target_port = static_cast<int>(parse_u32(value.substr(separator + 1), "air_target.port"));
            break;
        }
        case 'm':
            config.known_clients = parse_known_clients(optarg);
            break;
        case 'x':
            config.client_targets.push_back(parse_client_target(optarg));
            break;
        case 'd':
            config.grant_duration_ms = parse_u32(optarg, "grant_duration_ms");
            break;
        case 'g':
            config.guard_interval_ms = parse_u32(optarg, "guard_interval_ms", true);
            break;
        case 'f':
            config.feedback_window_period_ms = parse_u32(optarg, "feedback_window_period_ms");
            break;
        case 'F':
            config.feedback_window_duration_ms = parse_u32(optarg, "feedback_window_duration_ms");
            break;
        case 'b':
            config.uplink_pause_threshold_bytes = parse_u32(optarg, "uplink_pause_threshold_bytes");
            break;
        case 'j':
            config.uplink_resume_threshold_bytes = parse_u32(optarg, "uplink_resume_threshold_bytes");
            break;
        case 'z':
            config.uplink_queue_packets_limit = parse_u32(optarg, "uplink_queue_packets_limit");
            break;
        case 'B':
            config.downlink_pause_threshold_bytes = parse_u32(optarg, "downlink_pause_threshold_bytes");
            break;
        case 'J':
            config.downlink_resume_threshold_bytes = parse_u32(optarg, "downlink_resume_threshold_bytes");
            break;
        case 'Z':
            config.downlink_queue_packets_limit = parse_u32(optarg, "downlink_queue_packets_limit");
            break;
        case 'y':
            config.queue_summary_file = optarg;
            break;
        case 'W':
        {
            vector<string> interfaces = parse_air_interfaces(optarg);
            config.air_interfaces.insert(config.air_interfaces.end(), interfaces.begin(), interfaces.end());
            break;
        }
        case 'h':
            print_usage(argv[0]);
            exit(0);
        default:
            throw invalid_argument("参数非法");
        }
    }

    if (optind != argc)
    {
        throw invalid_argument("存在未识别的位置参数");
    }

    if (config.role != "client" && config.role != "server")
    {
        throw invalid_argument("role 必须是 client 或 server");
    }
    if (config.tun_name.empty() || config.tun_addr.empty())
    {
        throw invalid_argument("tun-name 与 tun-addr 必填");
    }
    if (config.node_id == 0)
    {
        throw invalid_argument("node-id 必须是 1-255");
    }
    if (config.plaintext_fec_k != 1 || config.plaintext_fec_n != 1)
    {
        throw invalid_argument("当前最小实现只支持 trusted_plaintext FEC 1/1");
    }
    const bool raw_air_mode = !config.air_interfaces.empty();
    if (!raw_air_mode && config.air_listen_port <= 0)
    {
        throw invalid_argument("air-listen-port 必填");
    }

    if (config.role == "client")
    {
        if (!raw_air_mode && (config.air_target_host.empty() || config.air_target_port <= 0))
        {
            throw invalid_argument("client 需要 air-target 或 air-interface");
        }
        if (config.uplink_pause_threshold_bytes < config.uplink_resume_threshold_bytes)
        {
            throw invalid_argument("uplink pause 阈值不能小于 resume 阈值");
        }
        if (config.uplink_queue_packets_limit == 0)
        {
            throw invalid_argument("uplink_queue_packets_limit 必须大于 0");
        }
    }
    else
    {
        if (config.known_clients.empty())
        {
            throw invalid_argument("server 需要非空 known-clients");
        }
        if (config.client_targets.empty())
        {
            throw invalid_argument("server 至少需要一个 client-target");
        }
        const bool feedback_window_enabled = config.feedback_window_period_ms > 0 || config.feedback_window_duration_ms > 0;
        if (feedback_window_enabled)
        {
            if (config.feedback_window_period_ms == 0 || config.feedback_window_duration_ms == 0)
            {
                throw invalid_argument("feedback_window_period_ms 与 feedback_window_duration_ms 必须同时配置");
            }
            if (config.feedback_window_duration_ms >= config.feedback_window_period_ms)
            {
                throw invalid_argument("feedback_window_duration_ms 必须小于 feedback_window_period_ms");
            }
        }
        if (config.downlink_pause_threshold_bytes < config.downlink_resume_threshold_bytes)
        {
            throw invalid_argument("downlink pause 阈值不能小于 resume 阈值");
        }
        if (config.downlink_queue_packets_limit == 0)
        {
            throw invalid_argument("downlink_queue_packets_limit 必须大于 0");
        }

        set<uint8_t> expected(config.known_clients.begin(), config.known_clients.end());
        set<uint8_t> actual;
        set<uint32_t> tun_ips;
        for (size_t i = 0; i < config.client_targets.size(); ++i)
        {
            if (!expected.count(config.client_targets[i].node_id))
            {
                throw invalid_argument("client-target 包含未知 node_id");
            }
            if (!actual.insert(config.client_targets[i].node_id).second)
            {
                throw invalid_argument("client-target 存在重复 node_id");
            }
            if (!tun_ips.insert(config.client_targets[i].tun_ipv4).second)
            {
                throw invalid_argument("client-target 存在重复 tun_ip");
            }
        }
        if (actual != expected)
        {
            throw invalid_argument("client-target 必须完整覆盖 known-clients");
        }
    }

    return config;
}

int open_tun(const string &dev_name, const string &dev_addr)
{
    struct ifreq ifr = {};
    int fd = open("/dev/net/tun", O_RDWR | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0)
    {
        throw runtime_error(string("open /dev/net/tun failed: ") + strerror(errno));
    }

    ifr.ifr_flags = IFF_TUN | IFF_NO_PI;
    strncpy(ifr.ifr_name, dev_name.c_str(), IFNAMSIZ - 1);
    if (ioctl(fd, TUNSETIFF, (void *)&ifr) < 0)
    {
        const string error = strerror(errno);
        close(fd);
        throw runtime_error("TUNSETIFF failed: " + error);
    }

    const size_t mtu = MAX_PAYLOAD_SIZE;
    const string ifname = ifr.ifr_name;
    const string up_cmd = string("ip link set up mtu ") + to_string(mtu) + " dev " + ifname;
    if (system(up_cmd.c_str()) != 0)
    {
        close(fd);
        throw runtime_error("配置 TUN mtu 失败: " + ifname);
    }

    const string addr_cmd = string("ip addr add ") + dev_addr + " dev " + ifname;
    if (system(addr_cmd.c_str()) != 0)
    {
        close(fd);
        throw runtime_error("配置 TUN 地址失败: " + dev_addr);
    }

    return fd;
}

bool parse_ipv4_destination(const uint8_t *buf, size_t size, uint32_t *dest_ipv4)
{
    return parse_ipv4_endpoints(buf, size, NULL, dest_ipv4);
}

class TunWriterAggregator : public Aggregator
{
public:
    typedef function<void(const uint8_t *, uint16_t)> PayloadObserver;

    TunWriterAggregator(int tun_fd,
                        const string &keypair,
                        uint64_t epoch,
                        uint32_t channel_id,
                        uint8_t local_node_id,
                        bool trusted_plaintext)
        : Aggregator(keypair, epoch, channel_id, local_node_id, trusted_plaintext, 1, 1),
          tun_fd_(tun_fd)
    {
    }

    void set_payload_observer(const PayloadObserver &observer)
    {
        payload_observer_ = observer;
    }

protected:
    void send_to_socket(const uint8_t *payload, uint16_t packet_size) override
    {
        if (payload_observer_)
        {
            payload_observer_(payload, packet_size);
        }

        const ssize_t written = write(tun_fd_, payload, packet_size);
        if (written != static_cast<ssize_t>(packet_size))
        {
            throw runtime_error(string("写入 TUN 失败: ") + strerror(errno));
        }
    }

private:
    int tun_fd_;
    PayloadObserver payload_observer_;
};

class ClientGrantListener : public TokenControlListener
{
public:
    explicit ClientGrantListener(TokenAuthorizationState &authorization_state)
        : authorization_state_(authorization_state)
    {
    }

    void on_token_control(const ControlEnvelopeView &packet) override
    {
        if (packet.control_type != WFB_CONTROL_TYPE_GRANT)
        {
            return;
        }

        TokenAuthorizationEvent event = {};
        event.node_id = packet.target_node;
        event.sequence = packet.sequence;
        event.duration_ms = packet.grant_duration_ms;
        event.expires_at_ms = packet.grant_expires_at_ms;
        authorization_state_.apply_event(event);

        IPC_MSG("grant_accept node_id=%u sequence=%" PRIu64 " duration_ms=%u expires_at_ms=%" PRIu64 "\n",
                static_cast<unsigned>(event.node_id),
                event.sequence,
                event.duration_ms,
                event.expires_at_ms);
        IPC_MSG_SEND();
    }

private:
    TokenAuthorizationState &authorization_state_;
};

class ServerReadyListener : public TokenControlListener
{
public:
    explicit ServerReadyListener(TokenScheduler &scheduler)
        : scheduler_(scheduler)
    {
    }

    void on_token_control(const ControlEnvelopeView &packet) override
    {
        if (packet.control_type != WFB_CONTROL_TYPE_READY)
        {
            return;
        }

        const uint64_t now_ms = get_time_ms();
        TokenScheduler::DeclareReadyResult result = scheduler_.declare_ready_with_result(packet.source_node, now_ms);
        if (result == TokenScheduler::DECLARE_ENQUEUED)
        {
            IPC_MSG("ready_accept node_id=%u%s\n",
                    static_cast<unsigned>(packet.source_node),
                    scheduler_.describe_active_state().c_str());
            IPC_MSG_SEND();
        }
        else if (result == TokenScheduler::DECLARE_REFRESHED)
        {
            IPC_MSG("ready_refresh node_id=%u%s\n",
                    static_cast<unsigned>(packet.source_node),
                    scheduler_.describe_active_state().c_str());
            IPC_MSG_SEND();
        }
    }

private:
    TokenScheduler &scheduler_;
};

void maybe_send_ready(AirTransmitter &transmitter,
                      uint64_t now_ms,
                      AirReadyState *ready_state)
{
    if (ready_state == nullptr)
    {
        return;
    }

    const bool should_declare = !ready_state->declaration_in_flight ||
        (ready_state->last_declare_at_ms > 0 && now_ms >= ready_state->last_declare_at_ms + kReadyRedeclareTimeoutMs);
    if (!should_declare)
    {
        return;
    }

    transmitter.send_ready(ready_state->node_id);

    const AirReadyState::Phase before = ready_state->phase;
    ready_state->declaration_in_flight = true;
    ready_state->last_declare_at_ms = now_ms;
    if (ready_state->phase == AirReadyState::UNDECLARED)
    {
        ready_state->phase = AirReadyState::DECLARED_WAITING_FOR_GRANT;
        log_ready_state_transition(ready_state->node_id, before, ready_state->phase, "first_declare", now_ms);
    }
    else
    {
        log_ready_state_transition(ready_state->node_id, before, ready_state->phase, "redeclare", now_ms);
    }
}

void apply_grant_seen(uint64_t now_ms, AirReadyState *ready_state)
{
    if (ready_state == nullptr)
    {
        return;
    }

    const AirReadyState::Phase before = ready_state->phase;
    ready_state->declaration_in_flight = false;
    ready_state->phase = AirReadyState::GRANT_SEEN_WAITING_FOR_NEXT;
    if (before != ready_state->phase)
    {
        log_ready_state_transition(ready_state->node_id, before, ready_state->phase, "state_transition", now_ms);
    }
}

void sync_queue_summary_or_throw(const FixedCapacityTunReadQueue &queue,
                                 const string &summary_file,
                                 const char *role,
                                 uint8_t node_id)
{
    if (!queue.write_summary_file(summary_file, role, node_id))
    {
        throw runtime_error(string("写入 ") + role + " queue summary 失败: " + summary_file);
    }
}

void maybe_log_tun_read_transition(uint8_t node_id,
                                   bool before_enabled,
                                   TunReadPauseReason before_reason,
                                   const FixedCapacityTunReadQueue &queue)
{
    if (before_enabled == queue.tun_read_enabled() && before_reason == queue.current_pause_reason())
    {
        return;
    }

    if (!queue.tun_read_enabled())
    {
        IPC_MSG("tun_read_pause node_id=%u reason=%s queued_bytes=%zu queued_packets=%zu pause_threshold_bytes=%u resume_threshold_bytes=%u queued_packets_limit=%zu pause_total=%" PRIu64 "\n",
                static_cast<unsigned>(node_id),
                tun_read_pause_reason_name(queue.current_pause_reason()),
                queue.queued_bytes(),
                queue.queued_packets(),
                queue.pause_threshold_bytes(),
                queue.resume_threshold_bytes(),
                queue.queued_packets_limit(),
                queue.counters().tun_read_pause_total);
    }
    else
    {
        IPC_MSG("tun_read_resume node_id=%u queued_bytes=%zu queued_packets=%zu resume_total=%" PRIu64 "\n",
                static_cast<unsigned>(node_id),
                queue.queued_bytes(),
                queue.queued_packets(),
                queue.counters().tun_read_resume_total);
    }
    IPC_MSG_SEND();
}

void pump_air_rx(int air_fd, Aggregator &aggregator)
{
    uint8_t buffer[sizeof(wrxfwd_t) + MAX_FORWARDER_PACKET_SIZE] = {};
    for (;;)
    {
        sockaddr_in sockaddr = {};
        socklen_t addr_size = sizeof(sockaddr);
        const ssize_t received = recvfrom(air_fd,
                                          buffer,
                                          sizeof(buffer),
                                          MSG_DONTWAIT,
                                          reinterpret_cast<struct sockaddr *>(&sockaddr),
                                          &addr_size);
        if (received < 0)
        {
            if (errno == EWOULDBLOCK || errno == EAGAIN)
            {
                return;
            }
            throw runtime_error(string("接收空口仿真帧失败: ") + strerror(errno));
        }
        if (received < static_cast<ssize_t>(sizeof(wrxfwd_t)))
        {
            continue;
        }

        const wrxfwd_t *header = reinterpret_cast<const wrxfwd_t *>(buffer);
        aggregator.process_packet(buffer + sizeof(wrxfwd_t),
                                  static_cast<size_t>(received - sizeof(wrxfwd_t)),
                                  header->wlan_idx,
                                  header->antenna,
                                  header->rssi,
                                  header->noise,
                                  ntohs(header->freq),
                                  header->mcs_index,
                                  header->bandwidth,
                                  &sockaddr);
    }
}

ClientTarget *find_target_by_node_id(vector<ClientTarget> &targets, uint8_t node_id)
{
    for (size_t i = 0; i < targets.size(); ++i)
    {
        if (targets[i].node_id == node_id)
        {
            return &targets[i];
        }
    }
    return nullptr;
}

ClientTarget *find_target_by_tun_ip(vector<ClientTarget> &targets, uint32_t tun_ipv4)
{
    for (size_t i = 0; i < targets.size(); ++i)
    {
        if (targets[i].tun_ipv4 == tun_ipv4)
        {
            return &targets[i];
        }
    }
    return nullptr;
}
void send_payload_to_target(ClientTarget &target, const uint8_t *packet, size_t packet_size)
{
    if (target.transmitter)
    {
        target.transmitter->send_data(packet, packet_size);
    }
}

void send_downlink_payload(vector<ClientTarget> &targets,
                           const uint8_t *packet,
                           size_t packet_size,
                           uint32_t dest_ipv4)
{
    if (is_ipv4_multicast(dest_ipv4) || is_ipv4_limited_broadcast(dest_ipv4))
    {
        for (size_t i = 0; i < targets.size(); ++i)
        {
            send_payload_to_target(targets[i], packet, packet_size);
        }
        return;
    }

    ClientTarget *target = find_target_by_tun_ip(targets, dest_ipv4);
    if (target != NULL)
    {
        send_payload_to_target(*target, packet, packet_size);
    }
}

void open_feedback_window(FeedbackWindowState *state,
                          uint64_t now_ms,
                          size_t known_clients_count)
{
    if (state == NULL || !state->enabled)
    {
        return;
    }

    state->active = true;
    state->next_client_index = 0;
    state->next_grant_at_ms = now_ms;
    state->window_end_at_ms = now_ms;
    state->current_node_id = 0;
    state->current_grant_sequence = 0;
    state->current_slot_expires_at_ms = 0;
    state->current_slot_hit_recorded = false;
    state->open_count += 1;
    IPC_MSG("feedback_window_open count=%" PRIu64 " period_ms=%u duration_ms=%u known_clients=%zu\n",
            state->open_count,
            state->period_ms,
            state->duration_ms,
            known_clients_count);
    IPC_MSG_SEND();
}

void close_feedback_window(FeedbackWindowState *state,
                           uint64_t now_ms)
{
    if (state == NULL || !state->active)
    {
        return;
    }

    state->active = false;
    state->next_open_at_ms = now_ms + state->period_ms;
    state->current_node_id = 0;
    state->current_grant_sequence = 0;
    state->current_slot_expires_at_ms = 0;
    state->current_slot_hit_recorded = false;
    state->close_count += 1;
    IPC_MSG("feedback_window_close count=%" PRIu64 " next_open_at_ms=%" PRIu64 "\n",
            state->close_count,
            state->next_open_at_ms);
    IPC_MSG_SEND();
}

void maybe_record_feedback_uplink_hit(FeedbackWindowState *state,
                                      TokenScheduler *scheduler,
                                      vector<ClientTarget> &targets,
                                      const uint8_t *payload,
                                      uint16_t packet_size)
{
    uint32_t source_ipv4 = 0;
    if (!parse_ipv4_endpoints(payload, packet_size, &source_ipv4, NULL))
    {
        return;
    }

    ClientTarget *target = find_target_by_tun_ip(targets, source_ipv4);
    if (target == NULL)
    {
        return;
    }

    const uint64_t now_ms = get_time_ms();
    if (scheduler != NULL)
    {
        scheduler->observe_uplink_data(target->node_id, now_ms);
    }
    if (state == NULL || !state->active)
    {
        return;
    }
    if (state->current_node_id != target->node_id || state->current_slot_hit_recorded)
    {
        return;
    }
    if (state->current_slot_expires_at_ms > 0 && now_ms > state->current_slot_expires_at_ms)
    {
        return;
    }

    state->current_slot_hit_recorded = true;
    uint64_t &total = state->slot_hit_total_by_node[target->node_id];
    total += 1;
    IPC_MSG("feedback_uplink_hit node_id=%u sequence=%" PRIu64 " total=%" PRIu64 "\n",
            static_cast<unsigned>(target->node_id),
            state->current_grant_sequence,
            total);
    IPC_MSG_SEND();
}

bool maybe_send_feedback_grant(FeedbackWindowState *state,
                               TokenScheduler *scheduler,
                               const Config &config,
                               vector<ClientTarget> &targets,
                               uint64_t now_ms)
{
    if (state == NULL || scheduler == NULL || !state->active || now_ms < state->next_grant_at_ms)
    {
        return false;
    }
    if (state->next_client_index >= config.known_clients.size())
    {
        close_feedback_window(state, now_ms);
        return false;
    }

    const uint8_t node_id = config.known_clients[state->next_client_index];
    ClientTarget *target = find_target_by_node_id(targets, node_id);
    const uint64_t sequence = scheduler->allocate_sequence();
    state->current_node_id = node_id;
    state->current_grant_sequence = sequence;
    state->current_slot_expires_at_ms = now_ms + state->duration_ms;
    state->current_slot_hit_recorded = false;
    state->window_end_at_ms = now_ms + state->duration_ms + config.guard_interval_ms;
    if (target != NULL && target->transmitter)
    {
        target->transmitter->send_grant(config.node_id, node_id, sequence, state->duration_ms);
    }
    IPC_MSG("grant seq=%" PRIu64 " node_id=%u duration_ms=%u kind=feedback slot=%zu/%zu\n",
            sequence,
            static_cast<unsigned>(node_id),
            state->duration_ms,
            state->next_client_index + 1,
            config.known_clients.size());
    IPC_MSG_SEND();
    state->next_client_index += 1;
    state->next_grant_at_ms = now_ms + state->duration_ms + config.guard_interval_ms;
    return true;
}

void run_client(const Config &config)
{
    const uint32_t channel_id = (config.link_id << 8) + config.stream;
    const string keypair = WFB_TRUSTED_PLAINTEXT_KEYPAIR;
    const bool raw_air_mode = !config.air_interfaces.empty();
    const int tun_fd = open_tun(config.tun_name, config.tun_addr);
    const int air_fd = raw_air_mode ? -1 : open_udp_socket_for_rx(config.air_listen_port, config.rcv_buf);

    unique_ptr<AirTransmitter> uplink;
    if (raw_air_mode)
    {
        uplink.reset(new AirTransmitter(config.air_interfaces, channel_id));
    }
    else
    {
        uplink.reset(new AirTransmitter(config.air_target_host, config.air_target_port, config.snd_buf));
    }

    TokenAuthorizationState authorization_state;
    ClientGrantListener grant_listener(authorization_state);
    TunWriterAggregator downlink_aggregator(tun_fd,
                                            keypair,
                                            config.epoch,
                                            channel_id,
                                            config.node_id,
                                            true);
    downlink_aggregator.set_token_control_listener(&grant_listener);

    vector<unique_ptr<Receiver>> raw_receivers;
    vector<pollfd> fds(1 + (raw_air_mode ? config.air_interfaces.size() : 1));
    fds[0].fd = tun_fd;
    if (raw_air_mode)
    {
        for (size_t i = 0; i < config.air_interfaces.size(); ++i)
        {
            raw_receivers.push_back(unique_ptr<Receiver>(new Receiver(config.air_interfaces[i].c_str(),
                                                                      static_cast<int>(i),
                                                                      channel_id,
                                                                      &downlink_aggregator,
                                                                      config.rcv_buf)));
            fds[1 + i].fd = raw_receivers[i]->getfd();
            fds[1 + i].events = POLLIN;
        }
    }
    else
    {
        fds[1].fd = air_fd;
        fds[1].events = POLLIN;
    }

    AirReadyState ready_state = {};
    ready_state.node_id = config.node_id;
    FixedCapacityTunReadQueue uplink_queue(config.uplink_pause_threshold_bytes,
                                           config.uplink_resume_threshold_bytes,
                                           config.uplink_queue_packets_limit);
    sync_queue_summary_or_throw(uplink_queue, config.queue_summary_file, "client", config.node_id);

    uint64_t log_send_ts = get_time_ms();

    for (;;)
    {
        fds[0].events = uplink_queue.tun_read_enabled() ? POLLIN : 0;

        uint64_t now_ms = get_time_ms();
        int timeout_ms = static_cast<int>(max<int64_t>(0, static_cast<int64_t>(log_send_ts - now_ms)));
        int rc = poll(fds.data(), fds.size(), timeout_ms);
        if (rc < 0)
        {
            if (errno == EINTR || errno == EAGAIN)
            {
                continue;
            }
            throw runtime_error(string("client poll 失败: ") + strerror(errno));
        }

        now_ms = get_time_ms();
        if (now_ms >= log_send_ts)
        {
            downlink_aggregator.dump_stats();
            IPC_MSG("%" PRIu64 "\tTOKEN_AUTH\t%u:%u:%u:%u\n",
                    now_ms,
                    authorization_state.counters().accepted_events,
                    authorization_state.counters().rejected_events,
                    authorization_state.counters().authorized_sends,
                    authorization_state.counters().denied_sends);
            IPC_MSG_SEND();
            sync_queue_summary_or_throw(uplink_queue, config.queue_summary_file, "client", config.node_id);
            log_send_ts = now_ms + config.log_interval;
        }

        if (rc > 0)
        {
            if (raw_air_mode)
            {
                for (size_t i = 0; i < raw_receivers.size(); ++i)
                {
                    if (fds[1 + i].revents & (POLLERR | POLLNVAL))
                    {
                        throw runtime_error("raw air client socket error");
                    }
                    if (fds[1 + i].revents & POLLIN)
                    {
                        raw_receivers[i]->loop_iter();
                    }
                }
            }
            else if (fds[1].revents & POLLIN)
            {
                pump_air_rx(air_fd, downlink_aggregator);
            }
        }

        if (rc > 0 && (fds[0].revents & POLLIN))
        {
            uint8_t tun_packet[MAX_PAYLOAD_SIZE] = {};
            const ssize_t nread = read(tun_fd, tun_packet, sizeof(tun_packet));
            if (nread > 0)
            {
                const bool before_enabled = uplink_queue.tun_read_enabled();
                const TunReadPauseReason before_reason = uplink_queue.current_pause_reason();
                if (!uplink_queue.push(tun_packet, static_cast<size_t>(nread)))
                {
                    throw runtime_error("uplink queue 入队失败");
                }
                maybe_log_tun_read_transition(config.node_id, before_enabled, before_reason, uplink_queue);
                if (before_enabled != uplink_queue.tun_read_enabled() || before_reason != uplink_queue.current_pause_reason())
                {
                    sync_queue_summary_or_throw(uplink_queue, config.queue_summary_file, "client", config.node_id);
                }
            }
            else if (nread < 0 && errno != EAGAIN && errno != EWOULDBLOCK)
            {
                throw runtime_error(string("读取 client TUN 失败: ") + strerror(errno));
            }
        }

        const QueuedTunPacket *pending_packet = uplink_queue.front();
        if (pending_packet == NULL)
        {
            continue;
        }

        if (!authorization_state.is_authorized(now_ms))
        {
            authorization_state.counters().denied_sends += 1;
            maybe_send_ready(*uplink, now_ms, &ready_state);
            continue;
        }

        uplink->send_data(pending_packet->bytes, pending_packet->size);
        authorization_state.counters().authorized_sends += 1;
        apply_grant_seen(now_ms, &ready_state);

        const bool before_enabled = uplink_queue.tun_read_enabled();
        const TunReadPauseReason before_reason = uplink_queue.current_pause_reason();
        if (!uplink_queue.pop_front())
        {
            throw runtime_error("uplink queue 出队失败");
        }
        maybe_log_tun_read_transition(config.node_id, before_enabled, before_reason, uplink_queue);
        if (before_enabled != uplink_queue.tun_read_enabled() || before_reason != uplink_queue.current_pause_reason())
        {
            sync_queue_summary_or_throw(uplink_queue, config.queue_summary_file, "client", config.node_id);
        }
    }
}

void run_server(Config config)
{
    const uint32_t channel_id = (config.link_id << 8) + config.stream;
    const string keypair = WFB_TRUSTED_PLAINTEXT_KEYPAIR;
    const bool raw_air_mode = !config.air_interfaces.empty();
    const int tun_fd = open_tun(config.tun_name, config.tun_addr);
    const int air_fd = raw_air_mode ? -1 : open_udp_socket_for_rx(config.air_listen_port, config.rcv_buf);

    TokenSchedulerConfig scheduler_config = {};
    scheduler_config.node_ids = config.known_clients;
    scheduler_config.duration_ms = config.grant_duration_ms;
    scheduler_config.guard_interval_ms = config.guard_interval_ms;
    TokenScheduler scheduler(scheduler_config);
    ServerReadyListener ready_listener(scheduler);

    TunWriterAggregator uplink_aggregator(tun_fd,
                                          keypair,
                                          config.epoch,
                                          channel_id,
                                          config.node_id,
                                          true);
    uplink_aggregator.set_token_control_listener(&ready_listener);
    uplink_aggregator.set_known_client_node_ids(set<uint8_t>(config.known_clients.begin(), config.known_clients.end()));

    vector<unique_ptr<Receiver>> raw_receivers;
    vector<pollfd> fds(1 + (raw_air_mode ? config.air_interfaces.size() : 1));
    fds[0].fd = tun_fd;
    if (raw_air_mode)
    {
        for (size_t i = 0; i < config.air_interfaces.size(); ++i)
        {
            raw_receivers.push_back(unique_ptr<Receiver>(new Receiver(config.air_interfaces[i].c_str(),
                                                                      static_cast<int>(i),
                                                                      channel_id,
                                                                      &uplink_aggregator,
                                                                      config.rcv_buf)));
            fds[1 + i].fd = raw_receivers[i]->getfd();
            fds[1 + i].events = POLLIN;
        }
    }
    else
    {
        fds[1].fd = air_fd;
        fds[1].events = POLLIN;
    }

    initialize_client_target_transmitters(
        config.client_targets,
        raw_air_mode,
        [&]() {
            return shared_ptr<AirTransmitter>(new AirTransmitter(config.air_interfaces, channel_id));
        },
        [&](const ClientTarget &target) {
            return shared_ptr<AirTransmitter>(new AirTransmitter(target.host,
                                                                target.port,
                                                                config.snd_buf));
        });

    FeedbackWindowState feedback_state = {};
    feedback_state.enabled = config.feedback_window_period_ms > 0 && config.feedback_window_duration_ms > 0;
    feedback_state.period_ms = config.feedback_window_period_ms;
    feedback_state.duration_ms = config.feedback_window_duration_ms;
    if (feedback_state.enabled)
    {
        feedback_state.next_open_at_ms = get_time_ms() + feedback_state.period_ms;
    }

    FixedCapacityTunReadQueue downlink_queue(config.downlink_pause_threshold_bytes,
                                             config.downlink_resume_threshold_bytes,
                                             config.downlink_queue_packets_limit);
    sync_queue_summary_or_throw(downlink_queue, config.queue_summary_file, "server", config.node_id);
    uplink_aggregator.set_payload_observer([&](const uint8_t *payload, uint16_t packet_size) {
        maybe_record_feedback_uplink_hit(&feedback_state, &scheduler, config.client_targets, payload, packet_size);
    });

    uint64_t log_send_ts = get_time_ms();
    uint64_t next_grant_at_ms = get_time_ms();

    for (;;)
    {
        fds[0].events = downlink_queue.tun_read_enabled() ? POLLIN : 0;

        uint64_t now_ms = get_time_ms();
        uint64_t next_wakeup = min(log_send_ts, next_grant_at_ms);
        if (feedback_state.enabled)
        {
            if (feedback_state.active)
            {
                next_wakeup = min(next_wakeup, feedback_state.next_grant_at_ms);
            }
            else
            {
                next_wakeup = min(next_wakeup, feedback_state.next_open_at_ms);
            }
        }
        int timeout_ms = static_cast<int>(next_wakeup > now_ms ? next_wakeup - now_ms : 0);
        int rc = poll(fds.data(), fds.size(), timeout_ms);
        if (rc < 0)
        {
            if (errno == EINTR || errno == EAGAIN)
            {
                continue;
            }
            throw runtime_error(string("server poll 失败: ") + strerror(errno));
        }

        now_ms = get_time_ms();
        if (now_ms >= log_send_ts)
        {
            uplink_aggregator.dump_stats();
            sync_queue_summary_or_throw(downlink_queue, config.queue_summary_file, "server", config.node_id);
            log_send_ts = now_ms + config.log_interval;
        }

        if (rc > 0)
        {
            if (raw_air_mode)
            {
                for (size_t i = 0; i < raw_receivers.size(); ++i)
                {
                    if (fds[1 + i].revents & (POLLERR | POLLNVAL))
                    {
                        throw runtime_error("raw air server socket error");
                    }
                    if (fds[1 + i].revents & POLLIN)
                    {
                        raw_receivers[i]->loop_iter();
                    }
                }
            }
            else if (fds[1].revents & POLLIN)
            {
                pump_air_rx(air_fd, uplink_aggregator);
            }
        }

        if (rc > 0 && (fds[0].revents & POLLIN))
        {
            uint8_t packet[MAX_PAYLOAD_SIZE] = {};
            const ssize_t nread = read(tun_fd, packet, sizeof(packet));
            if (nread > 0)
            {
                const bool before_enabled = downlink_queue.tun_read_enabled();
                const TunReadPauseReason before_reason = downlink_queue.current_pause_reason();
                if (!downlink_queue.push(packet, static_cast<size_t>(nread)))
                {
                    throw runtime_error("downlink queue 入队失败");
                }
                maybe_log_tun_read_transition(config.node_id, before_enabled, before_reason, downlink_queue);
                if (before_enabled != downlink_queue.tun_read_enabled() || before_reason != downlink_queue.current_pause_reason())
                {
                    sync_queue_summary_or_throw(downlink_queue, config.queue_summary_file, "server", config.node_id);
                }
            }
            else if (nread < 0 && errno != EAGAIN && errno != EWOULDBLOCK)
            {
                throw runtime_error(string("读取 server TUN 失败: ") + strerror(errno));
            }
        }

        now_ms = get_time_ms();
        if (feedback_state.enabled && !feedback_state.active && now_ms >= feedback_state.next_open_at_ms)
        {
            open_feedback_window(&feedback_state, now_ms, config.known_clients.size());
        }
        if (feedback_state.active)
        {
            maybe_send_feedback_grant(&feedback_state, &scheduler, config, config.client_targets, now_ms);
            continue;
        }

        const QueuedTunPacket *pending_packet = downlink_queue.front();
        if (pending_packet != NULL)
        {
            uint32_t dest_ipv4 = 0;
            if (parse_ipv4_destination(pending_packet->bytes, pending_packet->size, &dest_ipv4))
            {
                send_downlink_payload(config.client_targets, pending_packet->bytes, pending_packet->size, dest_ipv4);
            }

            const bool before_enabled = downlink_queue.tun_read_enabled();
            const TunReadPauseReason before_reason = downlink_queue.current_pause_reason();
            if (!downlink_queue.pop_front())
            {
                throw runtime_error("downlink queue 出队失败");
            }
            maybe_log_tun_read_transition(config.node_id, before_enabled, before_reason, downlink_queue);
            if (before_enabled != downlink_queue.tun_read_enabled() || before_reason != downlink_queue.current_pause_reason())
            {
                sync_queue_summary_or_throw(downlink_queue, config.queue_summary_file, "server", config.node_id);
            }
        }

        now_ms = get_time_ms();
        if (now_ms < next_grant_at_ms)
        {
            continue;
        }

        TokenGrant grant = {};
        if (!scheduler.next_grant(&grant, now_ms))
        {
            next_grant_at_ms = now_ms + kIdleSleepMs;
            continue;
        }

        ClientTarget *target = find_target_by_node_id(config.client_targets, grant.node_id);
        if (target != NULL && target->transmitter)
        {
            target->transmitter->send_grant(config.node_id,
                                            grant.node_id,
                                            grant.sequence,
                                            grant.duration_ms);
            IPC_MSG("grant seq=%" PRIu64 " node_id=%u duration_ms=%u kind=normal%s\n",
                    grant.sequence,
                    static_cast<unsigned>(grant.node_id),
                    grant.duration_ms,
                    scheduler.describe_active_state().c_str());
            IPC_MSG_SEND();
        }
        next_grant_at_ms = now_ms + grant.duration_ms + grant.guard_interval_ms;
    }
}

} // namespace

#ifndef __WFB_V6_UPLINK_TEST__
int main(int argc, char **argv)
{
    if (sodium_init() < 0)
    {
        WFB_ERR("Libsodium init failed\n");
        return 1;
    }

    try
    {
        Config config = parse_args(argc, argv);
        WFB_INFO("v6_uplink role=%s node_id=%u link_id=0x%06x stream=%u trusted_plaintext risk=受信任环境/无链路机密性\n",
                 config.role.c_str(),
                 static_cast<unsigned>(config.node_id),
                 config.link_id,
                 static_cast<unsigned>(config.stream));

        if (config.role == "client")
        {
            run_client(config);
        }
        else
        {
            run_server(std::move(config));
        }
    }
    catch (const exception &e)
    {
        WFB_ERR("Error: %s\n", e.what());
        return 1;
    }

    return 0;
}
#endif
