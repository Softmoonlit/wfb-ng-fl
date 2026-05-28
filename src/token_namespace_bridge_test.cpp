#include <catch2/catch_session.hpp>
#include <catch2/catch_test_macros.hpp>

#include <atomic>
#include <chrono>
#include <string>
#include <thread>

#include <unistd.h>

#include "token_authorization_ipc.hpp"
#include "token_namespace_bridge.hpp"

namespace {

std::string unique_socket_base(const std::string &name)
{
    return "wfb-bridge-test-" + std::to_string(getpid()) + "-" + name;
}

bool wait_for_event(const TokenAuthorizationDatagramReceiver &receiver,
                    TokenAuthorizationEvent *event)
{
    for (int i = 0; i < 50; ++i)
    {
        if (receiver.recv_event(event))
        {
            return true;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    return false;
}

bool wait_until_sent(const TokenAuthorizationDatagramSender &sender,
                     const TokenAuthorizationEvent &event)
{
    for (int i = 0; i < 50; ++i)
    {
        if (sender.send_event(event))
        {
            return true;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    return false;
}

} // namespace

TEST_CASE("parse_token_namespace_bridge_args 解析 server 入口和 node 到 client 入口映射")
{
    char arg0[] = "wfb_token_namespace_bridge";
    char arg1[] = "-S";
    char arg2[] = "server-ns";
    char arg3[] = "-r";
    char arg4[] = "scheduler";
    char arg5[] = "-g";
    char arg6[] = "bridge-grant";
    char arg7[] = "-n";
    char arg8[] = "1:client1:5601";
    char arg9[] = "-n";
    char arg10[] = "2:client2:5602:client2-ready";
    char *argv[] = {arg0, arg1, arg2, arg3, arg4, arg5, arg6, arg7, arg8, arg9, arg10};

    TokenNamespaceBridgeConfig config = parse_token_namespace_bridge_args(11, argv);

    REQUIRE(config.server_ready_entry.network_namespace == "server-ns");
    REQUIRE(config.server_ready_entry.socket_base == "scheduler");
    REQUIRE(config.server_grant_entry.network_namespace == "server-ns");
    REQUIRE(config.server_grant_entry.socket_base == "bridge-grant");
    REQUIRE(config.nodes.size() == 2);
    REQUIRE(config.nodes[0].node_id == 1);
    REQUIRE(config.nodes[0].client_grant_entry.network_namespace == "client1");
    REQUIRE(config.nodes[0].client_grant_entry.socket_base == "5601");
    REQUIRE(config.nodes[0].client_ready_entry.network_namespace == "client1");
    REQUIRE(config.nodes[0].client_ready_entry.socket_base == kDefaultTokenReadySocketBase);
    REQUIRE(config.nodes[1].node_id == 2);
    REQUIRE(config.nodes[1].client_grant_entry.network_namespace == "client2");
    REQUIRE(config.nodes[1].client_grant_entry.socket_base == "5602");
    REQUIRE(config.nodes[1].client_ready_entry.network_namespace == "client2");
    REQUIRE(config.nodes[1].client_ready_entry.socket_base == "client2-ready");
}

TEST_CASE("控制桥把 client ready 声明转发到 server ready 入口")
{
    const std::string server_ready_base = unique_socket_base("server-ready");
    const std::string client_ready_base = unique_socket_base("client-ready");
    const std::string server_grant_base = unique_socket_base("server-grant");
    const std::string client_grant_base = unique_socket_base("client-grant");

    TokenAuthorizationDatagramReceiver server_ready_receiver(make_token_ready_socket_name(server_ready_base));
    TokenAuthorizationDatagramSender client_ready_sender(make_token_ready_socket_name(client_ready_base));

    TokenNamespaceBridgeConfig config = {};
    config.server_ready_entry.socket_base = server_ready_base;
    config.server_grant_entry.socket_base = server_grant_base;
    config.idle_sleep_ms = 1;

    TokenNamespaceBridgeNode node = {};
    node.node_id = 7;
    node.client_grant_entry.socket_base = client_grant_base;
    node.client_ready_entry.socket_base = client_ready_base;
    config.nodes.push_back(node);

    std::atomic_bool stop_requested(false);
    std::thread bridge([&]() {
        run_token_namespace_bridge(config,
                                   stop_requested,
                                   [](uint32_t duration_ms) {
                                       std::this_thread::sleep_for(std::chrono::milliseconds(duration_ms));
                                   });
    });

    TokenAuthorizationEvent ready = {};
    ready.node_id = 7;
    ready.sequence = 42;
    ready.duration_ms = 100;
    ready.expires_at_ms = 12345;

    const bool sent = wait_until_sent(client_ready_sender, ready);
    if (!sent)
    {
        stop_requested.store(true);
        bridge.join();
    }
    REQUIRE(sent);

    TokenAuthorizationEvent forwarded = {};
    const bool received = wait_for_event(server_ready_receiver, &forwarded);
    stop_requested.store(true);
    bridge.join();

    REQUIRE(received);

    REQUIRE(forwarded.node_id == ready.node_id);
    REQUIRE(forwarded.sequence == ready.sequence);
    REQUIRE(forwarded.duration_ms == ready.duration_ms);
    REQUIRE(forwarded.expires_at_ms == ready.expires_at_ms);
}

TEST_CASE("控制桥按 node_id 把 server grant 路由到对应 client grant 入口")
{
    const std::string server_ready_base = unique_socket_base("grant-server-ready");
    const std::string server_grant_base = unique_socket_base("grant-server-grant");
    const std::string client1_ready_base = unique_socket_base("grant-client1-ready");
    const std::string client2_ready_base = unique_socket_base("grant-client2-ready");
    const std::string client1_grant_base = unique_socket_base("grant-client1-grant");
    const std::string client2_grant_base = unique_socket_base("grant-client2-grant");

    TokenAuthorizationDatagramReceiver client1_grant_receiver(make_token_authorization_socket_name(client1_grant_base));
    TokenAuthorizationDatagramReceiver client2_grant_receiver(make_token_authorization_socket_name(client2_grant_base));
    TokenAuthorizationDatagramSender server_grant_sender(make_token_authorization_socket_name(server_grant_base));

    TokenNamespaceBridgeConfig config = {};
    config.server_ready_entry.socket_base = server_ready_base;
    config.server_grant_entry.socket_base = server_grant_base;
    config.idle_sleep_ms = 1;

    TokenNamespaceBridgeNode node1 = {};
    node1.node_id = 1;
    node1.client_ready_entry.socket_base = client1_ready_base;
    node1.client_grant_entry.socket_base = client1_grant_base;
    config.nodes.push_back(node1);

    TokenNamespaceBridgeNode node2 = {};
    node2.node_id = 2;
    node2.client_ready_entry.socket_base = client2_ready_base;
    node2.client_grant_entry.socket_base = client2_grant_base;
    config.nodes.push_back(node2);

    std::atomic_bool stop_requested(false);
    std::thread bridge([&]() {
        run_token_namespace_bridge(config,
                                   stop_requested,
                                   [](uint32_t duration_ms) {
                                       std::this_thread::sleep_for(std::chrono::milliseconds(duration_ms));
                                   });
    });

    TokenAuthorizationEvent grant = {};
    grant.node_id = 2;
    grant.sequence = 99;
    grant.duration_ms = 250;
    grant.expires_at_ms = 54321;

    const bool sent = wait_until_sent(server_grant_sender, grant);
    if (!sent)
    {
        stop_requested.store(true);
        bridge.join();
    }
    REQUIRE(sent);

    TokenAuthorizationEvent forwarded = {};
    const bool received = wait_for_event(client2_grant_receiver, &forwarded);
    stop_requested.store(true);
    bridge.join();

    REQUIRE(received);
    REQUIRE_FALSE(client1_grant_receiver.recv_event(&forwarded));
    REQUIRE(forwarded.node_id == grant.node_id);
    REQUIRE(forwarded.sequence == grant.sequence);
    REQUIRE(forwarded.duration_ms == grant.duration_ms);
    REQUIRE(forwarded.expires_at_ms == grant.expires_at_ms);
}

int main(int argc, char *argv[])
{
    Catch::Session session;
    return session.run(argc, argv);
}
