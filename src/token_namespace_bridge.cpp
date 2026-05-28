#include "token_namespace_bridge.hpp"

#include <errno.h>
#include <fcntl.h>
#include <getopt.h>
#include <limits.h>
#include <sched.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <iostream>
#include <map>
#include <memory>
#include <set>
#include <sstream>
#include <stdexcept>

#include "token_authorization_ipc.hpp"

using namespace std;

namespace {

struct BridgeNodeRuntime {
    uint8_t node_id;
    unique_ptr<TokenAuthorizationDatagramReceiver> ready_receiver;
    unique_ptr<TokenAuthorizationDatagramSender> grant_sender;
};

uint32_t parse_u32(const string &value, const char *name, bool allow_zero)
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

uint8_t parse_node_id(const string &value)
{
    uint32_t parsed = parse_u32(value, "node_id", false);
    if (parsed > 255)
    {
        throw invalid_argument("node_id 超出范围: " + value);
    }
    return static_cast<uint8_t>(parsed);
}

vector<string> split_colon_fields(const string &value)
{
    vector<string> fields;
    istringstream stream(value);
    string field;
    while (getline(stream, field, ':'))
    {
        fields.push_back(field);
    }
    return fields;
}

string resolve_namespace_path(const string &network_namespace)
{
    if (network_namespace.empty() || network_namespace == "-")
    {
        return "";
    }
    if (network_namespace.find('/') != string::npos)
    {
        return network_namespace;
    }
    return "/var/run/netns/" + network_namespace;
}

class ScopedNetworkNamespace {
public:
    explicit ScopedNetworkNamespace(const string &network_namespace)
        : original_fd_(-1)
    {
        const string target_path = resolve_namespace_path(network_namespace);
        if (target_path.empty())
        {
            return;
        }

        original_fd_ = open("/proc/self/ns/net", O_RDONLY | O_CLOEXEC);
        if (original_fd_ < 0)
        {
            throw runtime_error(string("无法打开当前 network namespace: ") + strerror(errno));
        }

        const int target_fd = open(target_path.c_str(), O_RDONLY | O_CLOEXEC);
        if (target_fd < 0)
        {
            const string message = string("无法打开目标 network namespace ") + target_path + ": " + strerror(errno);
            close(original_fd_);
            original_fd_ = -1;
            throw runtime_error(message);
        }

        if (setns(target_fd, CLONE_NEWNET) != 0)
        {
            const string message = string("无法切换 network namespace ") + target_path + ": " + strerror(errno);
            close(target_fd);
            close(original_fd_);
            original_fd_ = -1;
            throw runtime_error(message);
        }
        close(target_fd);
    }

    ~ScopedNetworkNamespace()
    {
        if (original_fd_ >= 0)
        {
            setns(original_fd_, CLONE_NEWNET);
            close(original_fd_);
        }
    }

private:
    int original_fd_;
};

unique_ptr<TokenAuthorizationDatagramReceiver> make_receiver_in_namespace(const TokenNamespaceBridgeEndpoint &endpoint,
                                                                          const string &socket_name)
{
    ScopedNetworkNamespace scope(endpoint.network_namespace);
    return unique_ptr<TokenAuthorizationDatagramReceiver>(new TokenAuthorizationDatagramReceiver(socket_name));
}

unique_ptr<TokenAuthorizationDatagramSender> make_sender_in_namespace(const TokenNamespaceBridgeEndpoint &endpoint,
                                                                      const string &socket_name)
{
    ScopedNetworkNamespace scope(endpoint.network_namespace);
    return unique_ptr<TokenAuthorizationDatagramSender>(new TokenAuthorizationDatagramSender(socket_name));
}

void drain_receiver_to_sender(const TokenAuthorizationDatagramReceiver &receiver,
                              const TokenAuthorizationDatagramSender &sender,
                              bool *forwarded)
{
    for (;;)
    {
        TokenAuthorizationEvent event = {};
        if (!receiver.recv_event(&event))
        {
            break;
        }

        sender.send_event(event);
        *forwarded = true;
    }
}

void drain_grants_to_mapped_clients(const TokenAuthorizationDatagramReceiver &receiver,
                                    const map<uint8_t, TokenAuthorizationDatagramSender *> &senders,
                                    bool *forwarded)
{
    for (;;)
    {
        TokenAuthorizationEvent event = {};
        if (!receiver.recv_event(&event))
        {
            break;
        }

        map<uint8_t, TokenAuthorizationDatagramSender *>::const_iterator sender = senders.find(event.node_id);
        if (sender != senders.end())
        {
            sender->second->send_event(event);
        }
        *forwarded = true;
    }
}

} // namespace

TokenNamespaceBridgeConfig parse_token_namespace_bridge_args(int argc, char **argv)
{
    TokenNamespaceBridgeConfig config = {};
    string server_namespace;
    set<uint8_t> node_ids;

    config.server_ready_entry.socket_base = kDefaultTokenReadySocketBase;
    config.idle_sleep_ms = 10;

    optind = 1;
    opterr = 0;

    int opt = 0;
    while ((opt = getopt(argc, argv, "S:r:g:n:i:h")) != -1)
    {
        switch (opt)
        {
        case 'S':
            server_namespace = optarg;
            break;
        case 'r':
            config.server_ready_entry.socket_base = optarg;
            break;
        case 'g':
            config.server_grant_entry.socket_base = optarg;
            break;
        case 'n':
        {
            const vector<string> fields = split_colon_fields(optarg);
            if (fields.size() != 3 && fields.size() != 4)
            {
                throw invalid_argument("node 映射格式必须为 node_id:client_namespace:client_grant_base[:client_ready_base]");
            }
            for (size_t i = 0; i < fields.size(); ++i)
            {
                if (fields[i].empty())
                {
                    throw invalid_argument("node 映射存在空字段");
                }
            }

            TokenNamespaceBridgeNode node = {};
            node.node_id = parse_node_id(fields[0]);
            if (node_ids.count(node.node_id) != 0)
            {
                throw invalid_argument("node 映射存在重复 node_id");
            }
            node_ids.insert(node.node_id);
            node.client_grant_entry.network_namespace = fields[1];
            node.client_grant_entry.socket_base = fields[2];
            node.client_ready_entry.network_namespace = fields[1];
            node.client_ready_entry.socket_base = fields.size() == 4 ? fields[3] : kDefaultTokenReadySocketBase;
            config.nodes.push_back(node);
            break;
        }
        case 'i':
            config.idle_sleep_ms = parse_u32(optarg, "idle_sleep_ms", false);
            break;
        case 'h':
            throw invalid_argument("help requested");
        default:
            throw invalid_argument("参数非法");
        }
    }

    if (optind != argc)
    {
        throw invalid_argument("存在未识别的位置参数");
    }
    if (config.server_grant_entry.socket_base.empty())
    {
        throw invalid_argument("必须通过 -g 提供 server grant 入口基名");
    }
    if (config.nodes.empty())
    {
        throw invalid_argument("必须至少通过 -n 提供一个 node 映射");
    }

    config.server_ready_entry.network_namespace = server_namespace;
    config.server_grant_entry.network_namespace = server_namespace;
    return config;
}

void print_token_namespace_bridge_usage(const char *progname)
{
    cerr << "Usage: " << progname << " -S SERVER_NS -g SERVER_GRANT_BASE [-r SERVER_READY_BASE] "
         << "-n NODE_ID:CLIENT_NS:CLIENT_GRANT_BASE[:CLIENT_READY_BASE]...\n";
}

int run_token_namespace_bridge(const TokenNamespaceBridgeConfig &config,
                               atomic_bool &stop_requested,
                               const function<void(uint32_t)> &sleep_ms)
{
    unique_ptr<TokenAuthorizationDatagramSender> ready_sender = make_sender_in_namespace(
        config.server_ready_entry,
        make_token_ready_socket_name(config.server_ready_entry.socket_base));
    unique_ptr<TokenAuthorizationDatagramReceiver> grant_receiver = make_receiver_in_namespace(
        config.server_grant_entry,
        make_token_authorization_socket_name(config.server_grant_entry.socket_base));
    vector<unique_ptr<BridgeNodeRuntime> > nodes;
    map<uint8_t, TokenAuthorizationDatagramSender *> grant_senders;

    for (size_t i = 0; i < config.nodes.size(); ++i)
    {
        unique_ptr<BridgeNodeRuntime> runtime(new BridgeNodeRuntime());
        runtime->node_id = config.nodes[i].node_id;
        runtime->ready_receiver = make_receiver_in_namespace(
            config.nodes[i].client_ready_entry,
            make_token_ready_socket_name(config.nodes[i].client_ready_entry.socket_base));
        runtime->grant_sender = make_sender_in_namespace(
            config.nodes[i].client_grant_entry,
            make_token_authorization_socket_name(config.nodes[i].client_grant_entry.socket_base));
        grant_senders[runtime->node_id] = runtime->grant_sender.get();
        nodes.push_back(std::move(runtime));
    }

    while (!stop_requested.load())
    {
        bool forwarded = false;
        for (size_t i = 0; i < nodes.size(); ++i)
        {
            drain_receiver_to_sender(*nodes[i]->ready_receiver, *ready_sender, &forwarded);
        }
        drain_grants_to_mapped_clients(*grant_receiver, grant_senders, &forwarded);

        if (!forwarded)
        {
            sleep_ms(config.idle_sleep_ms == 0 ? 10 : config.idle_sleep_ms);
        }
    }

    return 0;
}
