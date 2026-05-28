#include "token_scheduler.hpp"

#include <errno.h>
#include <getopt.h>
#include <limits.h>
#include <signal.h>
#include <stdlib.h>
#include <string.h>

#include <chrono>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <thread>

using namespace std;

#include <map>
#include <memory>
#include "token_authorization_ipc.hpp"

namespace {

const uint32_t kSilentGrantRemovalThreshold = 2;

string format_node_list(const vector<uint8_t> &nodes)
{
    ostringstream stream;
    stream << '[';
    for (size_t i = 0; i < nodes.size(); ++i)
    {
        if (i != 0)
        {
            stream << ',';
        }
        stream << static_cast<unsigned>(nodes[i]);
    }
    stream << ']';
    return stream.str();
}

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

map<uint8_t, string> parse_socket_mapping(const string &value)
{
    if (value.empty())
    {
        throw invalid_argument("socket 映射不能为空");
    }

    map<uint8_t, string> sockets;
    istringstream stream(value);
    string item;

    while (getline(stream, item, ','))
    {
        size_t separator = item.find(':');
        if (separator == string::npos || separator == 0 || separator + 1 >= item.size())
        {
            throw invalid_argument("socket 映射非法: " + item);
        }

        uint8_t node_id = parse_node_id(item.substr(0, separator));
        string socket_path = item.substr(separator + 1);
        if (sockets.count(node_id) != 0)
        {
            throw invalid_argument("socket 映射存在重复 node_id");
        }
        sockets[node_id] = socket_path;
    }

    return sockets;
}

bool node_exists(const vector<uint8_t> &nodes, uint8_t node_id)
{
    for (size_t i = 0; i < nodes.size(); ++i)
    {
        if (nodes[i] == node_id)
        {
            return true;
        }
    }
    return false;
}

vector<uint8_t> drain_ready_declarations(TokenAuthorizationDatagramReceiver *receiver)
{
    vector<uint8_t> ready_nodes;
    if (receiver == NULL)
    {
        return ready_nodes;
    }

    for (;;)
    {
        TokenAuthorizationEvent event = {};
        if (!receiver->recv_event(&event))
        {
            break;
        }
        ready_nodes.push_back(event.node_id);
    }

    return ready_nodes;
}

} // namespace

vector<uint8_t> parse_node_list(const string &value)
{
    if (value.empty())
    {
        throw invalid_argument("节点列表不能为空");
    }

    vector<uint8_t> nodes;
    istringstream stream(value);
    string item;

    while (getline(stream, item, ','))
    {
        if (item.empty())
        {
            throw invalid_argument("节点列表存在空项");
        }

        uint8_t node_id = parse_node_id(item);
        for (size_t i = 0; i < nodes.size(); ++i)
        {
            if (nodes[i] == node_id)
            {
                throw invalid_argument("节点列表存在重复 node_id");
            }
        }
        nodes.push_back(node_id);
    }

    if (nodes.empty())
    {
        throw invalid_argument("节点列表不能为空");
    }

    return nodes;
}

TokenSchedulerConfig parse_token_scheduler_args(int argc, char **argv)
{
    TokenSchedulerConfig config;
    config.duration_ms = 0;
    config.guard_interval_ms = 0;

    optind = 1;
    opterr = 0;

    int opt = 0;
    while ((opt = getopt(argc, argv, "n:d:g:s:h")) != -1)
    {
        switch (opt)
        {
        case 'n':
            config.node_ids = parse_node_list(optarg);
            break;
        case 'd':
            config.duration_ms = parse_u32(optarg, "duration_ms", false);
            break;
        case 'g':
            config.guard_interval_ms = parse_u32(optarg, "guard_interval_ms", true);
            break;
        case 's':
            config.socket_path = optarg;
            if (string(optarg).find(':') != string::npos)
            {
                config.node_sockets = parse_socket_mapping(optarg);
                config.socket_path.clear();
            }
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

    if (config.node_ids.empty())
    {
        throw invalid_argument("必须通过 -n 提供静态节点列表");
    }

    if (config.duration_ms == 0)
    {
        throw invalid_argument("必须通过 -d 提供 duration_ms");
    }

    if (!config.node_sockets.empty())
    {
        for (map<uint8_t, string>::const_iterator it = config.node_sockets.begin(); it != config.node_sockets.end(); ++it)
        {
            if (!node_exists(config.node_ids, it->first))
            {
                throw invalid_argument("socket 映射包含未知 node_id");
            }
        }
        for (size_t i = 0; i < config.node_ids.size(); ++i)
        {
            if (config.node_sockets.count(config.node_ids[i]) == 0)
            {
                throw invalid_argument("socket 映射缺少 node_id");
            }
        }
    }

    return config;
}

void print_token_scheduler_usage(const char *progname)
{
    fprintf(stderr,
            "Usage: %s -n node1,node2,... -d duration_ms [-g guard_interval_ms] [-s base_socket|node:base_socket,...]\n",
            progname);
    fprintf(stderr,
            "Default: guard_interval_ms=%u, ready socket=%s.ready\n",
            0U,
            kDefaultTokenReadySocketBase);
}

TokenScheduler::TokenScheduler(const TokenSchedulerConfig &config) :
    next_index(0),
    sequence(0),
    duration_ms(config.duration_ms),
    guard_interval_ms(config.guard_interval_ms)
{
    if (config.node_ids.empty())
    {
        throw invalid_argument("节点列表不能为空");
    }

    for (size_t i = 0; i < config.node_ids.size(); ++i)
    {
        known_node_ids.insert(config.node_ids[i]);
        NodeActivityState activity = {};
        activity.last_activity_ms = 0;
        activity.consecutive_silent_grants = 0;
        node_activity[config.node_ids[i]] = activity;
    }

    if (duration_ms == 0)
    {
        throw invalid_argument("duration_ms 必须大于 0");
    }
}

bool TokenScheduler::declare_ready(uint8_t node_id)
{
    return declare_ready_with_result(node_id) == DECLARE_ENQUEUED;
}

TokenScheduler::DeclareReadyResult TokenScheduler::declare_ready_with_result(uint8_t node_id)
{
    return declare_ready_with_result(node_id, 0);
}

TokenScheduler::DeclareReadyResult TokenScheduler::declare_ready_with_result(uint8_t node_id, uint64_t now_ms)
{
    if (known_node_ids.count(node_id) == 0)
    {
        return DECLARE_REJECTED;
    }

    record_node_activity(node_id, now_ms);

    for (size_t i = 0; i < active_node_ids.size(); ++i)
    {
        if (active_node_ids[i] == node_id)
        {
            return DECLARE_REFRESHED;
        }
    }

    active_node_ids.push_back(node_id);
    return DECLARE_ENQUEUED;
}

void TokenScheduler::observe_uplink_data(uint8_t node_id)
{
    observe_uplink_data(node_id, 0);
}

void TokenScheduler::observe_uplink_data(uint8_t node_id, uint64_t now_ms)
{
    if (known_node_ids.count(node_id) == 0)
    {
        return;
    }

    record_node_activity(node_id, now_ms);
}

bool TokenScheduler::next_grant(TokenGrant *grant)
{
    return next_grant(grant, 0);
}

bool TokenScheduler::next_grant(TokenGrant *grant, uint64_t now_ms)
{
    if (grant == NULL)
    {
        throw invalid_argument("grant 指针不能为空");
    }

    if (active_node_ids.empty())
    {
        return false;
    }

    uint8_t node_id = active_node_ids[next_index];
    NodeActivityState &activity = node_activity[node_id];
    if (activity.last_activity_ms == 0 || now_ms <= activity.last_activity_ms)
    {
        activity.consecutive_silent_grants = 0;
    }
    else
    {
        activity.consecutive_silent_grants += 1;
    }

    grant->node_id = node_id;
    grant->sequence = sequence;
    grant->duration_ms = duration_ms;
    grant->guard_interval_ms = guard_interval_ms;

    sequence += 1;
    next_index = (next_index + 1) % active_node_ids.size();

    return true;
}

void TokenScheduler::collect_silent_node_removals(uint64_t now_ms,
                                                  vector<SilentNodeRemoval> *removed_nodes,
                                                  uint8_t protected_node_id)
{
    if (removed_nodes == NULL)
    {
        throw invalid_argument("removed_nodes 指针不能为空");
    }

    const uint64_t silence_threshold_ms = static_cast<uint64_t>(duration_ms + guard_interval_ms) * kSilentGrantRemovalThreshold;

    for (size_t index = 0; index < active_node_ids.size();)
    {
        uint8_t node_id = active_node_ids[index];
        if (node_id == protected_node_id)
        {
            index += 1;
            continue;
        }

        NodeActivityState &activity = node_activity[node_id];
        uint64_t silence_ms = 0;
        if (activity.last_activity_ms > 0 && now_ms > activity.last_activity_ms)
        {
            silence_ms = now_ms - activity.last_activity_ms;
        }

        if (silence_ms >= silence_threshold_ms && activity.consecutive_silent_grants >= kSilentGrantRemovalThreshold)
        {
            SilentNodeRemoval removal = {};
            removal.node_id = node_id;
            removal.silence_ms = silence_ms;
            removal.consecutive_silent_grants = activity.consecutive_silent_grants;
            removed_nodes->push_back(removal);
            remove_active_node_at(index);
            continue;
        }

        index += 1;
    }
}

string TokenScheduler::describe_active_state() const
{
    ostringstream stream;
    stream << " active_queue=" << format_node_list(active_node_ids)
           << " cursor=" << next_index;
    if (!active_node_ids.empty())
    {
        stream << " cursor_node_id=" << static_cast<unsigned>(active_node_ids[next_index]);
    }
    return stream.str();
}

void TokenScheduler::record_node_activity(uint8_t node_id, uint64_t now_ms)
{
    NodeActivityState &activity = node_activity[node_id];
    activity.last_activity_ms = now_ms;
    activity.consecutive_silent_grants = 0;
}

void TokenScheduler::remove_active_node_at(size_t index)
{
    if (index >= active_node_ids.size())
    {
        return;
    }

    active_node_ids.erase(active_node_ids.begin() + index);
    if (active_node_ids.empty())
    {
        next_index = 0;
        return;
    }

    if (index < next_index)
    {
        next_index -= 1;
    }
    else if (index == next_index && next_index == active_node_ids.size())
    {
        next_index = 0;
    }

    next_index %= active_node_ids.size();
}

TokenGrantDispatcher::TokenGrantDispatcher(const TokenSchedulerConfig &config)
{
    if (!config.socket_path.empty())
    {
        single_sender.reset(new TokenAuthorizationDatagramSender(make_token_authorization_socket_name(config.socket_path)));
    }

    for (map<uint8_t, string>::const_iterator it = config.node_sockets.begin(); it != config.node_sockets.end(); ++it)
    {
        node_senders[it->first].reset(new TokenAuthorizationDatagramSender(make_token_authorization_socket_name(it->second)));
    }
}

bool TokenGrantDispatcher::send_grant(const TokenGrant &grant, uint64_t now_ms) const
{
    TokenAuthorizationEvent event = {};
    event.node_id = grant.node_id;
    event.expires_at_ms = now_ms + grant.duration_ms;

    map<uint8_t, unique_ptr<TokenAuthorizationDatagramSender> >::const_iterator sender = node_senders.find(grant.node_id);
    if (sender != node_senders.end())
    {
        return sender->second->send_event(event);
    }

    if (single_sender)
    {
        return single_sender->send_event(event);
    }

    return true;
}

int run_token_scheduler(const TokenSchedulerConfig &config,
                        ostream &out,
                        atomic_bool &stop_requested,
                        const function<void(uint32_t)> &sleep_ms,
                        const function<uint64_t(void)> &get_time_ms,
                        const function<vector<uint8_t>(void)> &poll_ready_declarations,
                        const function<vector<uint8_t>(void)> &poll_observed_uplink_nodes)
{
    TokenScheduler scheduler(config);
    unique_ptr<TokenGrantDispatcher> dispatcher;
    unique_ptr<TokenAuthorizationDatagramReceiver> ready_receiver;

    if (!config.socket_path.empty() || !config.node_sockets.empty()) {
        try {
            dispatcher.reset(new TokenGrantDispatcher(config));
        } catch (const exception& e) {
            fprintf(stderr, "Failed to create IPC sender: %s\n", e.what());
        }
    }

    try {
        ready_receiver.reset(new TokenAuthorizationDatagramReceiver(make_token_ready_socket_name(kDefaultTokenReadySocketBase)));
    } catch (const exception& e) {
        fprintf(stderr, "Failed to create ready receiver: %s\n", e.what());
    }

    while (!stop_requested.load())
    {
        uint64_t now_ms = get_time_ms();
        vector<uint8_t> ready_nodes;
        if (poll_ready_declarations)
        {
            ready_nodes = poll_ready_declarations();
        }
        else if (ready_receiver)
        {
            ready_nodes = drain_ready_declarations(ready_receiver.get());
        }

        for (size_t i = 0; i < ready_nodes.size(); ++i)
        {
            TokenScheduler::DeclareReadyResult declare_result = scheduler.declare_ready_with_result(ready_nodes[i], now_ms);
            if (declare_result == TokenScheduler::DECLARE_ENQUEUED)
            {
                out << "join/rejoin node_id=" << static_cast<unsigned>(ready_nodes[i])
                    << scheduler.describe_active_state()
                    << '\n';
                out.flush();
            }
            else if (declare_result == TokenScheduler::DECLARE_REFRESHED)
            {
                out << "refresh node_id=" << static_cast<unsigned>(ready_nodes[i])
                    << scheduler.describe_active_state()
                    << '\n';
                out.flush();
            }
        }

        vector<uint8_t> observed_uplink_nodes;
        if (poll_observed_uplink_nodes)
        {
            observed_uplink_nodes = poll_observed_uplink_nodes();
        }

        for (size_t i = 0; i < observed_uplink_nodes.size(); ++i)
        {
            scheduler.observe_uplink_data(observed_uplink_nodes[i], now_ms);
        }

        TokenGrant grant = {};
        if (!scheduler.next_grant(&grant, now_ms))
        {
            sleep_ms(10);
            continue;
        }

        out << "grant seq=" << grant.sequence
            << " node_id=" << static_cast<unsigned>(grant.node_id)
            << " duration_ms=" << grant.duration_ms
            << " guard_interval_ms=" << grant.guard_interval_ms
            << " window_end_offset_ms=" << grant.duration_ms
            << scheduler.describe_active_state()
            << '\n';
        out.flush();

        if (dispatcher) {
            dispatcher->send_grant(grant, get_time_ms());
        }

        vector<TokenScheduler::SilentNodeRemoval> removed_nodes;
        scheduler.collect_silent_node_removals(now_ms, &removed_nodes, grant.node_id);
        for (size_t i = 0; i < removed_nodes.size(); ++i)
        {
            out << "evict node_id=" << static_cast<unsigned>(removed_nodes[i].node_id)
                << " silence_ms=" << removed_nodes[i].silence_ms
                << " consecutive_silent_grants=" << removed_nodes[i].consecutive_silent_grants
                << scheduler.describe_active_state()
                << '\n';
            out.flush();

            out << "remove node_id=" << static_cast<unsigned>(removed_nodes[i].node_id)
                << " silence_ms=" << removed_nodes[i].silence_ms
                << " consecutive_silent_grants=" << removed_nodes[i].consecutive_silent_grants
                << scheduler.describe_active_state()
                << '\n';
            out.flush();
        }

        sleep_ms(grant.duration_ms);
        if (stop_requested.load())
        {
            break;
        }

        if (grant.guard_interval_ms > 0)
        {
            out << "guard seq=" << grant.sequence
                << " guard_interval_ms=" << grant.guard_interval_ms
                << " next_seq=" << (grant.sequence + 1)
                << '\n';
            out.flush();
            sleep_ms(grant.guard_interval_ms);
        }
    }

    return 0;
}
