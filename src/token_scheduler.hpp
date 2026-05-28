#pragma once

#include <stdint.h>
#include <atomic>
#include <functional>
#include <iosfwd>
#include <map>
#include <memory>
#include <set>
#include <string>
#include <vector>

#include "token_authorization_ipc.hpp"

struct TokenSchedulerConfig {
    std::vector<uint8_t> node_ids;
    uint32_t duration_ms;
    uint32_t guard_interval_ms;
    std::string socket_path;
    std::map<uint8_t, std::string> node_sockets;
};

struct TokenGrant {
    uint8_t node_id;
    uint64_t sequence;
    uint32_t duration_ms;
    uint32_t guard_interval_ms;
};

std::vector<uint8_t> parse_node_list(const std::string &value);
TokenSchedulerConfig parse_token_scheduler_args(int argc, char **argv);
void print_token_scheduler_usage(const char *progname);

class TokenScheduler
{
public:
    struct SilentNodeRemoval {
        uint8_t node_id;
        uint64_t silence_ms;
        uint32_t consecutive_silent_grants;
    };

    enum DeclareReadyResult {
        DECLARE_REJECTED,
        DECLARE_ENQUEUED,
        DECLARE_REFRESHED
    };

    explicit TokenScheduler(const TokenSchedulerConfig &config);
    bool declare_ready(uint8_t node_id);
    DeclareReadyResult declare_ready_with_result(uint8_t node_id);
    DeclareReadyResult declare_ready_with_result(uint8_t node_id, uint64_t now_ms);
    void observe_uplink_data(uint8_t node_id);
    void observe_uplink_data(uint8_t node_id, uint64_t now_ms);
    bool next_grant(TokenGrant *grant);
    bool next_grant(TokenGrant *grant, uint64_t now_ms);
    void collect_silent_node_removals(uint64_t now_ms,
                                      std::vector<SilentNodeRemoval> *removed_nodes,
                                      uint8_t protected_node_id = 0);
    std::string describe_active_state() const;

private:
    struct NodeActivityState {
        uint64_t last_activity_ms;
        uint32_t consecutive_silent_grants;
    };

    void record_node_activity(uint8_t node_id, uint64_t now_ms);
    void remove_active_node_at(size_t index);

    std::set<uint8_t> known_node_ids;
    std::map<uint8_t, NodeActivityState> node_activity;
    std::vector<uint8_t> active_node_ids;
    size_t next_index;
    uint64_t sequence;
    uint32_t duration_ms;
    uint32_t guard_interval_ms;
};

class TokenGrantDispatcher
{
public:
    explicit TokenGrantDispatcher(const TokenSchedulerConfig &config);
    bool send_grant(const TokenGrant &grant, uint64_t now_ms) const;

private:
    std::unique_ptr<TokenAuthorizationDatagramSender> single_sender;
    std::map<uint8_t, std::unique_ptr<TokenAuthorizationDatagramSender> > node_senders;
};

int run_token_scheduler(const TokenSchedulerConfig &config,
                        std::ostream &out,
                        std::atomic_bool &stop_requested,
                        const std::function<void(uint32_t)> &sleep_ms,
                        const std::function<uint64_t(void)> &get_time_ms,
                        const std::function<std::vector<uint8_t>(void)> &poll_ready_declarations = std::function<std::vector<uint8_t>(void)>(),
                        const std::function<std::vector<uint8_t>(void)> &poll_observed_uplink_nodes = std::function<std::vector<uint8_t>(void)>());
