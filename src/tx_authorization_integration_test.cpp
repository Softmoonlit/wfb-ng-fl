#include <catch2/catch_session.hpp>
#include <catch2/catch_test_macros.hpp>

#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <cstring>
#include <sstream>
#include <string>
#include <thread>

#include "token_authorization.hpp"
#include "token_authorization_ipc.hpp"
#include "token_scheduler.hpp"
#include "tx_token_gate.hpp"

namespace {

class ScopedUnixDatagramSender {
public:
    explicit ScopedUnixDatagramSender(const std::string &path) : fd_(-1)
    {
        fd_ = socket(AF_UNIX, SOCK_DGRAM, 0);
        REQUIRE(fd_ >= 0);

        addr_ = {};
        addr_.sun_family = AF_UNIX;
        REQUIRE(path.size() + 1 < sizeof(addr_.sun_path));
        std::strncpy(addr_.sun_path + 1, path.c_str(), sizeof(addr_.sun_path) - 2);
    }

    ~ScopedUnixDatagramSender()
    {
        if (fd_ >= 0)
        {
            close(fd_);
        }
    }

    void send_event(const TokenAuthorizationEvent &event)
    {
        const ssize_t sent = sendto(fd_,
                                    &event,
                                    sizeof(event),
                                    0,
                                    reinterpret_cast<const sockaddr *>(&addr_),
                                    sizeof(sa_family_t) + std::strlen(addr_.sun_path + 1) + 1);
        REQUIRE(sent == static_cast<ssize_t>(sizeof(event)));
    }

private:
    int fd_;
    sockaddr_un addr_;
};

}

TEST_CASE("sender 授权事件到达后才允许门控发送")
{
    const std::string base_socket = "wfb-tx-gate-it";
    const std::string auth_socket = make_token_authorization_socket_name(base_socket);
    TokenAuthorizationDatagramReceiver receiver(auth_socket);
    ScopedUnixDatagramSender sender(auth_socket);
    TokenAuthorizationState state;

    bool called_before = false;
    REQUIRE_FALSE(run_when_authorized(&state, 1000, [&]() {
        called_before = true;
        return true;
    }));
    REQUIRE_FALSE(called_before);
    REQUIRE(state.counters().denied_sends == 1);

    TokenAuthorizationEvent event = {};
    event.node_id = 7;
    event.sequence = 42;
    event.duration_ms = 150;
    event.expires_at_ms = 1200;
    sender.send_event(event);

    TokenAuthorizationEvent received = {};
    REQUIRE(receiver.recv_event(&received));
    state.apply_event(received);
    REQUIRE(state.counters().accepted_events == 1);
    REQUIRE(state.counters().rejected_events == 0);

    bool called_after = false;
    REQUIRE(run_when_authorized(&state, 1000, [&]() {
        called_after = true;
        return true;
    }));
    REQUIRE(called_after);
    REQUIRE(state.counters().authorized_sends == 1);
}

TEST_CASE("双客户端声明后按顺序轮换授权并各自完成一次发送")
{
    const std::string socket_prefix = std::string("issue01_it_") + std::to_string(getpid()) + "_";
    const std::string auth_base_1 = socket_prefix + "5601";
    const std::string auth_base_2 = socket_prefix + "5602";
    const std::string auth_socket_1 = make_token_authorization_socket_name(auth_base_1);
    const std::string auth_socket_2 = make_token_authorization_socket_name(auth_base_2);
    const std::string ready_socket = make_token_ready_socket_name(kDefaultTokenReadySocketBase);

    TokenAuthorizationDatagramReceiver grant_receiver_1(auth_socket_1);
    TokenAuthorizationDatagramReceiver grant_receiver_2(auth_socket_2);
    TokenAuthorizationDatagramSender ready_sender(ready_socket);

    TokenSchedulerConfig config;
    config.node_ids = {1, 2};
    config.duration_ms = 100;
    config.guard_interval_ms = 10;
    config.node_sockets[1] = auth_base_1;
    config.node_sockets[2] = auth_base_2;

    std::atomic_bool stop_requested(false);
    std::ostringstream output;
    size_t sleep_calls = 0;
    bool ready_sent = false;

    int rc = run_token_scheduler(
        config,
        output,
        stop_requested,
        [&](uint32_t) {
            sleep_calls += 1;
            if (!ready_sent && sleep_calls == 1)
            {
                TokenAuthorizationEvent first_ready = {};
                first_ready.node_id = 1;
                REQUIRE(ready_sender.send_event(first_ready));

                TokenAuthorizationEvent second_ready = {};
                second_ready.node_id = 2;
                REQUIRE(ready_sender.send_event(second_ready));
                ready_sent = true;
            }

            if (sleep_calls >= 4)
            {
                stop_requested.store(true);
            }
        },
        []() {
            return static_cast<uint64_t>(1000);
        });

    REQUIRE(rc == 0);

    TokenAuthorizationEvent grant_1 = {};
    TokenAuthorizationEvent grant_2 = {};
    REQUIRE(grant_receiver_1.recv_event(&grant_1));
    REQUIRE(grant_receiver_2.recv_event(&grant_2));
    REQUIRE(grant_1.node_id == 1);
    REQUIRE(grant_2.node_id == 2);
    REQUIRE(grant_1.expires_at_ms == 1100);
    REQUIRE(grant_2.expires_at_ms == 1100);

    TokenAuthorizationState state_1;
    TokenAuthorizationState state_2;
    state_1.apply_event(grant_1);
    state_2.apply_event(grant_2);

    bool sender_1_sent = false;
    bool sender_2_sent = false;
    REQUIRE(run_when_authorized(&state_1, 1050, [&]() {
        sender_1_sent = true;
        return true;
    }));
    REQUIRE(run_when_authorized(&state_2, 1050, [&]() {
        sender_2_sent = true;
        return true;
    }));
    REQUIRE(sender_1_sent);
    REQUIRE(sender_2_sent);
    REQUIRE(state_1.counters().authorized_sends == 1);
    REQUIRE(state_2.counters().authorized_sends == 1);

    const std::string log_text = output.str();
    REQUIRE(log_text.find("join/rejoin node_id=1 active_queue=[1] cursor=0 cursor_node_id=1") != std::string::npos);
    REQUIRE(log_text.find("join/rejoin node_id=2 active_queue=[1,2] cursor=0 cursor_node_id=1") != std::string::npos);
    REQUIRE(log_text.find("grant seq=0 node_id=1 duration_ms=100 guard_interval_ms=10 window_end_offset_ms=100") != std::string::npos);
    REQUIRE(log_text.find("grant seq=1 node_id=2 duration_ms=100 guard_interval_ms=10 window_end_offset_ms=100") != std::string::npos);
}

TEST_CASE("运行中动态加入的客户端排到队尾并在自然轮到时获得授权")
{
    const std::string socket_prefix = std::string("issue02_it_") + std::to_string(getpid()) + "_";
    const std::string auth_base_1 = socket_prefix + "5701";
    const std::string auth_base_2 = socket_prefix + "5702";
    const std::string auth_socket_1 = make_token_authorization_socket_name(auth_base_1);
    const std::string auth_socket_2 = make_token_authorization_socket_name(auth_base_2);
    const std::string ready_socket = make_token_ready_socket_name(kDefaultTokenReadySocketBase);

    TokenAuthorizationDatagramReceiver grant_receiver_1(auth_socket_1);
    TokenAuthorizationDatagramReceiver grant_receiver_2(auth_socket_2);
    TokenAuthorizationDatagramSender ready_sender(ready_socket);

    TokenSchedulerConfig config;
    config.node_ids = {1, 2};
    config.duration_ms = 100;
    config.guard_interval_ms = 10;
    config.node_sockets[1] = auth_base_1;
    config.node_sockets[2] = auth_base_2;

    std::atomic_bool stop_requested(false);
    std::ostringstream output;
    size_t sleep_calls = 0;
    bool first_ready_sent = false;
    bool second_ready_sent = false;

    int rc = run_token_scheduler(
        config,
        output,
        stop_requested,
        [&](uint32_t) {
            sleep_calls += 1;
            if (!first_ready_sent && sleep_calls == 1)
            {
                TokenAuthorizationEvent first_ready = {};
                first_ready.node_id = 1;
                REQUIRE(ready_sender.send_event(first_ready));
                first_ready_sent = true;
            }

            if (!second_ready_sent && sleep_calls == 2)
            {
                TokenAuthorizationEvent second_ready = {};
                second_ready.node_id = 2;
                REQUIRE(ready_sender.send_event(second_ready));
                second_ready_sent = true;
            }

            if (sleep_calls >= 7)
            {
                stop_requested.store(true);
            }
        },
        []() {
            return static_cast<uint64_t>(1000);
        });

    REQUIRE(rc == 0);

    TokenAuthorizationEvent first_grant_1 = {};
    TokenAuthorizationEvent second_grant_1 = {};
    TokenAuthorizationEvent grant_2 = {};
    REQUIRE(grant_receiver_1.recv_event(&first_grant_1));
    REQUIRE(grant_receiver_1.recv_event(&second_grant_1));
    REQUIRE(grant_receiver_2.recv_event(&grant_2));
    REQUIRE(first_grant_1.node_id == 1);
    REQUIRE(second_grant_1.node_id == 1);
    REQUIRE(grant_2.node_id == 2);
    REQUIRE(first_grant_1.expires_at_ms == 1100);
    REQUIRE(second_grant_1.expires_at_ms == 1100);
    REQUIRE(grant_2.expires_at_ms == 1100);

    TokenAuthorizationState state_1;
    TokenAuthorizationState state_2;
    state_1.apply_event(second_grant_1);
    state_2.apply_event(grant_2);

    bool sender_1_sent = false;
    bool sender_2_sent = false;
    REQUIRE(run_when_authorized(&state_1, 1050, [&]() {
        sender_1_sent = true;
        return true;
    }));
    REQUIRE(run_when_authorized(&state_2, 1050, [&]() {
        sender_2_sent = true;
        return true;
    }));
    REQUIRE(sender_1_sent);
    REQUIRE(sender_2_sent);

    const std::string log_text = output.str();
    REQUIRE(log_text.find("join/rejoin node_id=1 active_queue=[1] cursor=0 cursor_node_id=1") != std::string::npos);
    REQUIRE(log_text.find("join/rejoin node_id=2 active_queue=[1,2] cursor=0 cursor_node_id=1") != std::string::npos);
    REQUIRE(log_text.find("grant seq=0 node_id=1 duration_ms=100 guard_interval_ms=10 window_end_offset_ms=100") != std::string::npos);
    REQUIRE(log_text.find("grant seq=1 node_id=1 duration_ms=100 guard_interval_ms=10 window_end_offset_ms=100") != std::string::npos);
    REQUIRE(log_text.find("grant seq=2 node_id=2 duration_ms=100 guard_interval_ms=10 window_end_offset_ms=100") != std::string::npos);
}

TEST_CASE("客户端长时间无 Token 后重声明不会抢占更靠前的授权顺序")
{
    const std::string socket_prefix = std::string("issue03_it_") + std::to_string(getpid()) + "_";
    const std::string auth_base_1 = socket_prefix + "5801";
    const std::string auth_base_2 = socket_prefix + "5802";
    const std::string auth_socket_1 = make_token_authorization_socket_name(auth_base_1);
    const std::string auth_socket_2 = make_token_authorization_socket_name(auth_base_2);

    TokenAuthorizationDatagramReceiver grant_receiver_1(auth_socket_1);
    TokenAuthorizationDatagramReceiver grant_receiver_2(auth_socket_2);

    TokenSchedulerConfig config;
    config.node_ids = {1, 2};
    config.duration_ms = 100;
    config.guard_interval_ms = 10;
    config.node_sockets[1] = auth_base_1;
    config.node_sockets[2] = auth_base_2;

    std::atomic_bool stop_requested(false);
    std::ostringstream output;
    size_t sleep_calls = 0;
    size_t poll_calls = 0;

    int rc = run_token_scheduler(
        config,
        output,
        stop_requested,
        [&](uint32_t) {
            sleep_calls += 1;
            if (sleep_calls >= 5)
            {
                stop_requested.store(true);
            }
        },
        []() {
            return static_cast<uint64_t>(1000);
        },
        [&]() {
            std::vector<uint8_t> ready_nodes;
            poll_calls += 1;
            if (poll_calls == 1)
            {
                ready_nodes.push_back(1);
            }
            else if (poll_calls == 2)
            {
                ready_nodes.push_back(2);
            }
            else if (poll_calls == 3)
            {
                ready_nodes.push_back(1);
            }
            return ready_nodes;
        },
        [&]() {
            std::vector<uint8_t> observed_uplink_nodes;
            if (poll_calls >= 2 && poll_calls <= 5)
            {
                observed_uplink_nodes.push_back(2);
            }
            return observed_uplink_nodes;
        });

    REQUIRE(rc == 0);

    TokenAuthorizationEvent first_grant_1 = {};
    TokenAuthorizationEvent grant_2 = {};
    TokenAuthorizationEvent second_grant_1 = {};
    REQUIRE(grant_receiver_1.recv_event(&first_grant_1));
    REQUIRE(grant_receiver_2.recv_event(&grant_2));
    REQUIRE(grant_receiver_1.recv_event(&second_grant_1));

    REQUIRE(first_grant_1.node_id == 1);
    REQUIRE(grant_2.node_id == 2);
    REQUIRE(second_grant_1.node_id == 1);
}

TEST_CASE("客户端被粗粒度移除后可通过重声明按队尾重入并再次获得授权")
{
    const std::string socket_prefix = std::string("issue04_it_") + std::to_string(getpid()) + "_";
    const std::string auth_base_1 = socket_prefix + "5901";
    const std::string auth_base_2 = socket_prefix + "5902";
    const std::string auth_socket_1 = make_token_authorization_socket_name(auth_base_1);
    const std::string auth_socket_2 = make_token_authorization_socket_name(auth_base_2);

    TokenAuthorizationDatagramReceiver grant_receiver_1(auth_socket_1);
    TokenAuthorizationDatagramReceiver grant_receiver_2(auth_socket_2);

    TokenSchedulerConfig config;
    config.node_ids = {1, 2};
    config.duration_ms = 100;
    config.guard_interval_ms = 10;
    config.node_sockets[1] = auth_base_1;
    config.node_sockets[2] = auth_base_2;

    std::atomic_bool stop_requested(false);
    std::ostringstream output;
    size_t sleep_calls = 0;
    size_t poll_calls = 0;
    uint64_t now_ms = 1000;

    int rc = run_token_scheduler(
        config,
        output,
        stop_requested,
        [&](uint32_t duration) {
            sleep_calls += 1;
            now_ms += duration;
            if (sleep_calls >= 15)
            {
                stop_requested.store(true);
            }
        },
        [&]() {
            return now_ms;
        },
        [&]() {
            std::vector<uint8_t> ready_nodes;
            poll_calls += 1;
            if (poll_calls == 1)
            {
                ready_nodes.push_back(1);
                ready_nodes.push_back(2);
            }
            else if (poll_calls == 7)
            {
                ready_nodes.push_back(1);
            }
            return ready_nodes;
        },
        [&]() {
            std::vector<uint8_t> observed_uplink_nodes;
            if (poll_calls >= 2 && poll_calls <= 6)
            {
                observed_uplink_nodes.push_back(2);
            }
            return observed_uplink_nodes;
        });

    REQUIRE(rc == 0);

    TokenAuthorizationEvent first_grant_1 = {};
    TokenAuthorizationEvent first_grant_2 = {};
    TokenAuthorizationEvent second_grant_1 = {};
    REQUIRE(grant_receiver_1.recv_event(&first_grant_1));
    REQUIRE(grant_receiver_2.recv_event(&first_grant_2));
    REQUIRE(grant_receiver_1.recv_event(&second_grant_1));

    REQUIRE(first_grant_1.node_id == 1);
    REQUIRE(first_grant_2.node_id == 2);
    REQUIRE(second_grant_1.node_id == 1);

    const std::string log_text = output.str();
    REQUIRE(log_text.find("join/rejoin node_id=1 active_queue=[1] cursor=0 cursor_node_id=1") != std::string::npos);
    REQUIRE(log_text.find("join/rejoin node_id=2 active_queue=[1,2] cursor=0 cursor_node_id=1") != std::string::npos);
    REQUIRE(log_text.find("remove node_id=1 silence_ms=550 consecutive_silent_grants=2 active_queue=[2] cursor=0 cursor_node_id=2") != std::string::npos);
    REQUIRE(log_text.find("join/rejoin node_id=1") != std::string::npos);
    REQUIRE(log_text.find("grant seq=5 node_id=2 duration_ms=100 guard_interval_ms=10 window_end_offset_ms=100") != std::string::npos);
    REQUIRE(log_text.find("grant seq=6 node_id=2 duration_ms=100 guard_interval_ms=10 window_end_offset_ms=100") != std::string::npos);
    REQUIRE(log_text.find("grant seq=7 node_id=1 duration_ms=100 guard_interval_ms=10 window_end_offset_ms=100") != std::string::npos);
}

int main(int argc, char *argv[])
{
    Catch::Session session;
    return session.run(argc, argv);
}
