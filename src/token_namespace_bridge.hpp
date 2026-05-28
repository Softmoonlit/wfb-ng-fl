#pragma once

#include <stdint.h>

#include <atomic>
#include <functional>
#include <string>
#include <vector>

struct TokenNamespaceBridgeEndpoint {
    std::string network_namespace;
    std::string socket_base;
};

struct TokenNamespaceBridgeNode {
    uint8_t node_id;
    TokenNamespaceBridgeEndpoint client_grant_entry;
    TokenNamespaceBridgeEndpoint client_ready_entry;
};

struct TokenNamespaceBridgeConfig {
    TokenNamespaceBridgeEndpoint server_ready_entry;
    TokenNamespaceBridgeEndpoint server_grant_entry;
    std::vector<TokenNamespaceBridgeNode> nodes;
    uint32_t idle_sleep_ms;
};

TokenNamespaceBridgeConfig parse_token_namespace_bridge_args(int argc, char **argv);
void print_token_namespace_bridge_usage(const char *progname);

int run_token_namespace_bridge(const TokenNamespaceBridgeConfig &config,
                               std::atomic_bool &stop_requested,
                               const std::function<void(uint32_t)> &sleep_ms);
