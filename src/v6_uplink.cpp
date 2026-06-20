// -*- C++ -*-
//
// v6 第一切片：trusted_plaintext 上行 TCP per-client 集成式链路层最小守护进程

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <getopt.h>
#include <inttypes.h>
#include <linux/if.h>
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

struct PendingPacket {
    bool valid = false;
    size_t size = 0;
    uint8_t bytes[MAX_PAYLOAD_SIZE] = {};
};

struct AirTransmitter {
    int sockfd = -1;
    sockaddr_in saddr = {};
    uint64_t block_idx = 0;

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

    ~AirTransmitter()
    {
        if (sockfd >= 0)
        {
            close(sockfd);
        }
    }

    void send_air_datagram(const uint8_t *packet, size_t packet_size)
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
            throw runtime_error(string("发送空口报文失败: ") + strerror(errno));
        }
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
    AirTransmitter(const AirTransmitter &) = delete;
    AirTransmitter &operator=(const AirTransmitter &) = delete;
};

struct ClientTarget {
    uint8_t node_id = 0;
    uint32_t tun_ipv4 = 0;
    string host;
    int port = 0;
    unique_ptr<AirTransmitter> transmitter;

    ClientTarget() = default;
    ClientTarget(ClientTarget &&) = default;
    ClientTarget &operator=(ClientTarget &&) = default;

private:
    ClientTarget(const ClientTarget &) = delete;
    ClientTarget &operator=(const ClientTarget &) = delete;
};

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
    uint32_t grant_duration_ms = 100;
    uint32_t guard_interval_ms = 10;
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
    target.port = static_cast<int>(parse_u32(fields[3], "client_target.port"));
    return target;
}

void print_usage(const char *progname)
{
    fprintf(stderr,
            "Usage:\n"
            "  %s --role client --tun-name NAME --tun-addr IP/CIDR --node-id N --link-id ID --stream S \\\n"
            "     --air-listen-port PORT --air-target HOST:PORT [--epoch E] [--fec-k K --fec-n N]\n"
            "  %s --role server --tun-name NAME --tun-addr IP/CIDR --node-id N --link-id ID --stream S \\\n"
            "     --air-listen-port PORT --known-clients N1,N2 --client-target N:IP:HOST:PORT [--client-target ...] \\\n"
            "     [--grant-duration-ms MS] [--guard-interval-ms MS] [--epoch E] [--fec-k K --fec-n N]\n",
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
        {"help", no_argument, 0, 'h'},
        {0, 0, 0, 0},
    };

    int opt = 0;
    while ((opt = getopt_long(argc, argv, "r:t:a:q:i:p:e:k:n:R:s:l:u:c:m:x:d:g:h", long_options, NULL)) != -1)
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
    if (config.air_listen_port <= 0)
    {
        throw invalid_argument("air-listen-port 必填");
    }

    if (config.role == "client")
    {
        if (config.air_target_host.empty() || config.air_target_port <= 0)
        {
            throw invalid_argument("client 需要 air-target");
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

    uint32_t addr = 0;
    memcpy(&addr, buf + 16, sizeof(addr));
    *dest_ipv4 = ntohl(addr);
    return true;
}

class TunWriterAggregator : public Aggregator
{
public:
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

protected:
    void send_to_socket(const uint8_t *payload, uint16_t packet_size) override
    {
        const ssize_t written = write(tun_fd_, payload, packet_size);
        if (written != static_cast<ssize_t>(packet_size))
        {
            throw runtime_error(string("写入 TUN 失败: ") + strerror(errno));
        }
    }

private:
    int tun_fd_;
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

void run_client(const Config &config)
{
    const uint32_t channel_id = (config.link_id << 8) + config.stream;
    const string keypair = WFB_TRUSTED_PLAINTEXT_KEYPAIR;
    const int tun_fd = open_tun(config.tun_name, config.tun_addr);
    const int air_fd = open_udp_socket_for_rx(config.air_listen_port, config.rcv_buf);

    AirTransmitter uplink(config.air_target_host, config.air_target_port, config.snd_buf);
    TokenAuthorizationState authorization_state;
    ClientGrantListener grant_listener(authorization_state);
    TunWriterAggregator downlink_aggregator(tun_fd,
                                            keypair,
                                            config.epoch,
                                            channel_id,
                                            config.node_id,
                                            true);
    downlink_aggregator.set_token_control_listener(&grant_listener);

    AirReadyState ready_state = {};
    ready_state.node_id = config.node_id;
    PendingPacket pending_packet = {};

    uint64_t log_send_ts = get_time_ms();
    pollfd fds[2] = {};
    fds[0].fd = tun_fd;
    fds[0].events = POLLIN;
    fds[1].fd = air_fd;
    fds[1].events = POLLIN;

    for (;;)
    {
        uint64_t now_ms = get_time_ms();
        int timeout_ms = static_cast<int>(max<int64_t>(0, static_cast<int64_t>(log_send_ts - now_ms)));
        int rc = poll(fds, 2, timeout_ms);
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
            log_send_ts = now_ms + config.log_interval;
        }

        if (rc > 0 && (fds[1].revents & POLLIN))
        {
            pump_air_rx(air_fd, downlink_aggregator);
        }

        if (!pending_packet.valid && rc > 0 && (fds[0].revents & POLLIN))
        {
            const ssize_t nread = read(tun_fd, pending_packet.bytes, sizeof(pending_packet.bytes));
            if (nread > 0)
            {
                pending_packet.valid = true;
                pending_packet.size = static_cast<size_t>(nread);
            }
            else if (nread < 0 && errno != EAGAIN && errno != EWOULDBLOCK)
            {
                throw runtime_error(string("读取 client TUN 失败: ") + strerror(errno));
            }
        }

        if (!pending_packet.valid)
        {
            continue;
        }

        if (!authorization_state.is_authorized(now_ms))
        {
            authorization_state.counters().denied_sends += 1;
            maybe_send_ready(uplink, now_ms, &ready_state);
            continue;
        }

        uplink.send_data(pending_packet.bytes, pending_packet.size);
        authorization_state.counters().authorized_sends += 1;
        apply_grant_seen(now_ms, &ready_state);
        pending_packet.valid = false;
        pending_packet.size = 0;
    }
}

void run_server(Config config)
{
    const uint32_t channel_id = (config.link_id << 8) + config.stream;
    const string keypair = WFB_TRUSTED_PLAINTEXT_KEYPAIR;
    const int tun_fd = open_tun(config.tun_name, config.tun_addr);
    const int air_fd = open_udp_socket_for_rx(config.air_listen_port, config.rcv_buf);

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

    for (size_t i = 0; i < config.client_targets.size(); ++i)
    {
        config.client_targets[i].transmitter.reset(new AirTransmitter(config.client_targets[i].host,
                                                                      config.client_targets[i].port,
                                                                      config.snd_buf));
    }

    uint64_t log_send_ts = get_time_ms();
    uint64_t next_grant_at_ms = get_time_ms();
    pollfd fds[2] = {};
    fds[0].fd = tun_fd;
    fds[0].events = POLLIN;
    fds[1].fd = air_fd;
    fds[1].events = POLLIN;

    for (;;)
    {
        uint64_t now_ms = get_time_ms();
        uint64_t next_wakeup = min(log_send_ts, next_grant_at_ms);
        int timeout_ms = static_cast<int>(next_wakeup > now_ms ? next_wakeup - now_ms : 0);
        int rc = poll(fds, 2, timeout_ms);
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
            log_send_ts = now_ms + config.log_interval;
        }

        if (rc > 0 && (fds[1].revents & POLLIN))
        {
            pump_air_rx(air_fd, uplink_aggregator);
        }

        if (rc > 0 && (fds[0].revents & POLLIN))
        {
            uint8_t packet[MAX_PAYLOAD_SIZE] = {};
            const ssize_t nread = read(tun_fd, packet, sizeof(packet));
            if (nread > 0)
            {
                uint32_t dest_ipv4 = 0;
                if (parse_ipv4_destination(packet, static_cast<size_t>(nread), &dest_ipv4))
                {
                    ClientTarget *target = find_target_by_tun_ip(config.client_targets, dest_ipv4);
                    if (target != nullptr && target->transmitter)
                    {
                        target->transmitter->send_data(packet, static_cast<size_t>(nread));
                    }
                }
            }
            else if (nread < 0 && errno != EAGAIN && errno != EWOULDBLOCK)
            {
                throw runtime_error(string("读取 server TUN 失败: ") + strerror(errno));
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
        if (target != nullptr && target->transmitter)
        {
            target->transmitter->send_grant(config.node_id,
                                            grant.node_id,
                                            grant.sequence,
                                            grant.duration_ms);
            IPC_MSG("grant seq=%" PRIu64 " node_id=%u duration_ms=%u%s\n",
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
