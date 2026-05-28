#include <catch2/catch_session.hpp>
#include <catch2/catch_test_macros.hpp>

#include <atomic>
#include <sstream>
#include <string>
#include <vector>

#include <unistd.h>

#include "token_authorization_ipc.hpp"
#include "token_scheduler.hpp"

using namespace std;

TEST_CASE("parse_node_list 解析静态节点列表")
{
    vector<uint8_t> nodes = parse_node_list("1,2,7");

    REQUIRE(nodes.size() == 3);
    REQUIRE(nodes[0] == 1);
    REQUIRE(nodes[1] == 2);
    REQUIRE(nodes[2] == 7);
}

TEST_CASE("parse_node_list 拒绝空项和重复")
{
    REQUIRE_THROWS(parse_node_list("1,,2"));
    REQUIRE_THROWS(parse_node_list("1,2,1"));
}

TEST_CASE("parse_token_scheduler_args 解析 duration 与 guard")
{
    const char *argv[] = {
        "wfb_token_scheduler",
        "-n",
        "2,4,6",
        "-d",
        "25",
        "-g",
        "5"
    };

    TokenSchedulerConfig config = parse_token_scheduler_args(7, const_cast<char **>(argv));

    REQUIRE(config.node_ids.size() == 3);
    REQUIRE(config.node_ids[0] == 2);
    REQUIRE(config.node_ids[1] == 4);
    REQUIRE(config.node_ids[2] == 6);
    REQUIRE(config.duration_ms == 25);
    REQUIRE(config.guard_interval_ms == 5);
}

TEST_CASE("parse_token_scheduler_args 缺少必要参数时报错")
{
    const char *argv[] = {
        "wfb_token_scheduler",
        "-n",
        "2,4,6"
    };

    REQUIRE_THROWS(parse_token_scheduler_args(3, const_cast<char **>(argv)));
}

TEST_CASE("parse_token_scheduler_args 解析 node 到 socket 的映射")
{
    const char *argv[] = {
        "wfb_token_scheduler",
        "-n",
        "1,2",
        "-d",
        "1000",
        "-g",
        "100",
        "-s",
        "1:5602,2:5603"
    };

    TokenSchedulerConfig config = parse_token_scheduler_args(9, const_cast<char **>(argv));

    REQUIRE(config.node_sockets.size() == 2);
    REQUIRE(config.node_sockets[1] == "5602");
    REQUIRE(config.node_sockets[2] == "5603");
}

TEST_CASE("parse_token_scheduler_args 拒绝非法 socket 映射")
{
    const char *missing_socket[] = {
        "wfb_token_scheduler",
        "-n",
        "1,2",
        "-d",
        "1000",
        "-s",
        "1:5602,2"
    };
    REQUIRE_THROWS(parse_token_scheduler_args(7, const_cast<char **>(missing_socket)));

    const char *unknown_node[] = {
        "wfb_token_scheduler",
        "-n",
        "1,2",
        "-d",
        "1000",
        "-s",
        "1:5602,3:5603"
    };
    REQUIRE_THROWS(parse_token_scheduler_args(7, const_cast<char **>(unknown_node)));
}

TEST_CASE("parse_token_scheduler_args 缺少节点 socket 映射时报错")
{
    const char *argv[] = {
        "wfb_token_scheduler",
        "-n",
        "1,2",
        "-d",
        "1000",
        "-s",
        "1:5602"
    };

    REQUIRE_THROWS(parse_token_scheduler_args(7, const_cast<char **>(argv)));
}

TEST_CASE("TokenScheduler 按 round-robin 重复发放")
{
    TokenSchedulerConfig config;
    config.node_ids = {1, 3, 5};
    config.duration_ms = 40;
    config.guard_interval_ms = 10;

    TokenScheduler scheduler(config);
    TokenGrant grant = {};

    REQUIRE_FALSE(scheduler.next_grant(&grant));
    REQUIRE(scheduler.declare_ready(1));
    REQUIRE(scheduler.declare_ready(3));

    REQUIRE(scheduler.next_grant(&grant));
    REQUIRE(grant.node_id == 1);
    REQUIRE(scheduler.next_grant(&grant));
    REQUIRE(grant.node_id == 3);
    REQUIRE(scheduler.next_grant(&grant));
    REQUIRE(grant.node_id == 1);
}

TEST_CASE("TokenScheduler 保留 duration 与 guard 配置")
{
    TokenSchedulerConfig config;
    config.node_ids = {9};
    config.duration_ms = 80;
    config.guard_interval_ms = 12;

    TokenScheduler scheduler(config);
    TokenGrant grant = {};

    REQUIRE(scheduler.declare_ready(9));
    REQUIRE(scheduler.next_grant(&grant));

    REQUIRE(grant.node_id == 9);
    REQUIRE(grant.sequence == 0);
    REQUIRE(grant.duration_ms == 80);
    REQUIRE(grant.guard_interval_ms == 12);
}

TEST_CASE("TokenScheduler 仅轮换已声明的白名单节点")
{
    TokenSchedulerConfig config;
    config.node_ids = {1, 2};
    config.duration_ms = 40;
    config.guard_interval_ms = 10;

    TokenScheduler scheduler(config);
    TokenGrant grant = {};

    REQUIRE(scheduler.declare_ready(1));
    REQUIRE(scheduler.next_grant(&grant));
    REQUIRE(grant.node_id == 1);
    REQUIRE(scheduler.next_grant(&grant));
    REQUIRE(grant.node_id == 1);
    REQUIRE(scheduler.next_grant(&grant));
    REQUIRE(grant.node_id == 1);
}

TEST_CASE("TokenScheduler 拒绝未知节点声明且不污染调度状态")
{
    TokenSchedulerConfig config;
    config.node_ids = {1, 2};
    config.duration_ms = 40;
    config.guard_interval_ms = 10;

    TokenScheduler scheduler(config);
    TokenGrant grant = {};

    REQUIRE_FALSE(scheduler.declare_ready(9));
    REQUIRE_FALSE(scheduler.next_grant(&grant));
    REQUIRE(scheduler.declare_ready(1));
    REQUIRE(scheduler.next_grant(&grant));
    REQUIRE(grant.node_id == 1);
}

TEST_CASE("TokenScheduler 忽略重复声明且不改变轮换顺序")
{
    TokenSchedulerConfig config;
    config.node_ids = {1, 2};
    config.duration_ms = 40;
    config.guard_interval_ms = 10;

    TokenScheduler scheduler(config);
    TokenGrant grant = {};

    REQUIRE(scheduler.declare_ready(1));
    REQUIRE(scheduler.declare_ready(2));
    REQUIRE(scheduler.next_grant(&grant));
    REQUIRE(grant.node_id == 1);
    REQUIRE_FALSE(scheduler.declare_ready(1));
    REQUIRE(scheduler.next_grant(&grant));
    REQUIRE(grant.node_id == 2);
    REQUIRE(scheduler.next_grant(&grant));
    REQUIRE(grant.node_id == 1);
}

TEST_CASE("TokenScheduler 运行中动态加入的节点排到队尾并自然轮到")
{
    TokenSchedulerConfig config;
    config.node_ids = {1, 2};
    config.duration_ms = 40;
    config.guard_interval_ms = 10;

    TokenScheduler scheduler(config);
    TokenGrant grant = {};

    REQUIRE(scheduler.declare_ready(1));
    REQUIRE(scheduler.next_grant(&grant));
    REQUIRE(grant.node_id == 1);

    REQUIRE(scheduler.declare_ready(2));

    REQUIRE(scheduler.next_grant(&grant));
    REQUIRE(grant.node_id == 1);

    REQUIRE(scheduler.next_grant(&grant));
    REQUIRE(grant.node_id == 2);

    REQUIRE(scheduler.next_grant(&grant));
    REQUIRE(grant.node_id == 1);
}

TEST_CASE("TokenScheduler 仅在静默时间与连续静默 grant 次数同时达阈值时移除节点")
{
    TokenSchedulerConfig config;
    config.node_ids = {1, 2};
    config.duration_ms = 40;
    config.guard_interval_ms = 10;

    TokenScheduler scheduler(config);
    TokenGrant grant = {};
    std::vector<TokenScheduler::SilentNodeRemoval> removed_nodes;

    REQUIRE(scheduler.declare_ready_with_result(1, 100) == TokenScheduler::DECLARE_ENQUEUED);
    REQUIRE(scheduler.declare_ready_with_result(2, 100) == TokenScheduler::DECLARE_ENQUEUED);

    REQUIRE(scheduler.next_grant(&grant, 120));
    REQUIRE(grant.node_id == 1);
    scheduler.collect_silent_node_removals(140, &removed_nodes);
    REQUIRE(removed_nodes.empty());

    REQUIRE(scheduler.next_grant(&grant, 170));
    REQUIRE(grant.node_id == 2);
    scheduler.collect_silent_node_removals(190, &removed_nodes);
    REQUIRE(removed_nodes.empty());

    REQUIRE(scheduler.next_grant(&grant, 220));
    REQUIRE(grant.node_id == 1);
    scheduler.collect_silent_node_removals(220, &removed_nodes);
    REQUIRE(removed_nodes.size() == 1);
    REQUIRE(removed_nodes[0].node_id == 1);
    REQUIRE(removed_nodes[0].consecutive_silent_grants == 2);

    REQUIRE(scheduler.next_grant(&grant, 230));
    REQUIRE(grant.node_id == 2);
}

TEST_CASE("TokenScheduler 观察到链路层数据包后清零连续静默 grant 次数")
{
    TokenSchedulerConfig config;
    config.node_ids = {1, 2};
    config.duration_ms = 40;
    config.guard_interval_ms = 10;

    TokenScheduler scheduler(config);
    TokenGrant grant = {};
    std::vector<TokenScheduler::SilentNodeRemoval> removed_nodes;

    REQUIRE(scheduler.declare_ready_with_result(1, 100) == TokenScheduler::DECLARE_ENQUEUED);
    REQUIRE(scheduler.declare_ready_with_result(2, 100) == TokenScheduler::DECLARE_ENQUEUED);

    REQUIRE(scheduler.next_grant(&grant, 120));
    REQUIRE(grant.node_id == 1);
    scheduler.observe_uplink_data(1, 150);

    REQUIRE(scheduler.next_grant(&grant, 170));
    REQUIRE(grant.node_id == 2);
    REQUIRE(scheduler.next_grant(&grant, 220));
    REQUIRE(grant.node_id == 1);

    scheduler.collect_silent_node_removals(220, &removed_nodes);
    REQUIRE(removed_nodes.empty());
}

TEST_CASE("run_token_scheduler 输出日志并可干净退出")
{
    TokenSchedulerConfig config;
    config.node_ids = {1, 2};
    config.duration_ms = 10;
    config.guard_interval_ms = 5;

    atomic_bool stop_requested(false);
    ostringstream output;
    size_t sleep_calls = 0;

    int rc = run_token_scheduler(
        config,
        output,
        stop_requested,
        [&](uint32_t) {
            sleep_calls += 1;
            if (sleep_calls >= 3)
            {
                stop_requested.store(true);
            }
        },
        []() {
            return static_cast<uint64_t>(1000);
        },
        []() {
            vector<uint8_t> ready_nodes;
            ready_nodes.push_back(1);
            ready_nodes.push_back(2);
            return ready_nodes;
        });

    REQUIRE(rc == 0);

    string text = output.str();
    REQUIRE(text.find("join/rejoin node_id=1 active_queue=[1] cursor=0 cursor_node_id=1") != string::npos);
    REQUIRE(text.find("join/rejoin node_id=2 active_queue=[1,2] cursor=0 cursor_node_id=1") != string::npos);
    REQUIRE(text.find("grant seq=0 node_id=1 duration_ms=10 guard_interval_ms=5 window_end_offset_ms=10") != string::npos);
    REQUIRE(text.find("guard seq=0 guard_interval_ms=5 next_seq=1") != string::npos);
    REQUIRE(text.find("grant seq=1 node_id=2 duration_ms=10 guard_interval_ms=5 window_end_offset_ms=10") != string::npos);
}

TEST_CASE("run_token_scheduler 对活跃节点重复声明时刷新活性但不改变轮换顺序")
{
    TokenSchedulerConfig config;
    config.node_ids = {1, 2};
    config.duration_ms = 10;
    config.guard_interval_ms = 5;

    atomic_bool stop_requested(false);
    ostringstream output;
    size_t poll_calls = 0;
    size_t sleep_calls = 0;

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
            poll_calls += 1;
            vector<uint8_t> ready_nodes;
            if (poll_calls == 1)
            {
                ready_nodes.push_back(1);
                ready_nodes.push_back(2);
            }
            else if (poll_calls == 2)
            {
                ready_nodes.push_back(1);
            }
            return ready_nodes;
        });

    REQUIRE(rc == 0);

    string text = output.str();
    REQUIRE(text.find("grant seq=0 node_id=1 duration_ms=10 guard_interval_ms=5 window_end_offset_ms=10") != string::npos);
    REQUIRE(text.find("refresh node_id=1 active_queue=[1,2] cursor=1 cursor_node_id=2") != string::npos);
    REQUIRE(text.find("grant seq=1 node_id=2 duration_ms=10 guard_interval_ms=5 window_end_offset_ms=10") != string::npos);
    REQUIRE(text.find("grant seq=2 node_id=1 duration_ms=10 guard_interval_ms=5 window_end_offset_ms=10") != string::npos);
}

TEST_CASE("run_token_scheduler 粗粒度移除静默节点并允许其重声明后按队尾重入")
{
    TokenSchedulerConfig config;
    config.node_ids = {1, 2};
    config.duration_ms = 10;
    config.guard_interval_ms = 5;

    atomic_bool stop_requested(false);
    ostringstream output;
    size_t poll_calls = 0;
    uint64_t now_ms = 1000;

    int rc = run_token_scheduler(
        config,
        output,
        stop_requested,
        [&](uint32_t duration) {
            now_ms += duration;
            if (now_ms >= 1130)
            {
                stop_requested.store(true);
            }
        },
        [&]() {
            return now_ms;
        },
        [&]() {
            poll_calls += 1;
            vector<uint8_t> ready_nodes;
            if (poll_calls == 1)
            {
                ready_nodes.push_back(1);
                ready_nodes.push_back(2);
            }
            else if (poll_calls == 8)
            {
                ready_nodes.push_back(2);
            }
            return ready_nodes;
        });

    REQUIRE(rc == 0);

    string text = output.str();
    const size_t remove_pos = text.find("remove node_id=2 silence_ms=");
    const size_t rejoin_pos = text.find("join/rejoin node_id=2 active_queue=[1,2]", remove_pos);
    const size_t rejoin_grant_pos = text.find("node_id=2 duration_ms=10 guard_interval_ms=5 window_end_offset_ms=10 active_queue=[1,2]", rejoin_pos);

    REQUIRE(text.find("grant seq=0 node_id=1 duration_ms=10 guard_interval_ms=5 window_end_offset_ms=10") != string::npos);
    REQUIRE(text.find("grant seq=1 node_id=2 duration_ms=10 guard_interval_ms=5 window_end_offset_ms=10") != string::npos);
    REQUIRE(text.find("join/rejoin node_id=1 active_queue=[1] cursor=0 cursor_node_id=1") != string::npos);
    REQUIRE(remove_pos != string::npos);
    REQUIRE(rejoin_pos != string::npos);
    REQUIRE(rejoin_grant_pos != string::npos);
    REQUIRE(remove_pos < rejoin_pos);
    REQUIRE(rejoin_pos < rejoin_grant_pos);
}

TEST_CASE("run_token_scheduler 运行中动态加入的新节点在移除旧静默节点前自然轮到")
{
    TokenSchedulerConfig config;
    config.node_ids = {1, 2};
    config.duration_ms = 10;
    config.guard_interval_ms = 5;

    atomic_bool stop_requested(false);
    ostringstream output;
    size_t poll_calls = 0;
    uint64_t now_ms = 1000;

    int rc = run_token_scheduler(
        config,
        output,
        stop_requested,
        [&](uint32_t duration) {
            now_ms += duration;
            if (now_ms >= 1060)
            {
                stop_requested.store(true);
            }
        },
        [&]() {
            return now_ms;
        },
        [&]() {
            poll_calls += 1;
            vector<uint8_t> ready_nodes;
            if (poll_calls == 1)
            {
                ready_nodes.push_back(1);
            }
            else if (poll_calls == 3)
            {
                ready_nodes.push_back(2);
            }
            return ready_nodes;
        });

    REQUIRE(rc == 0);

    string text = output.str();
    const size_t join_pos = text.find("join/rejoin node_id=2 active_queue=[1,2]");
    const size_t grant_pos = text.find("grant seq=3 node_id=2 duration_ms=10 guard_interval_ms=5 window_end_offset_ms=10 active_queue=[1,2]");
    const size_t remove_pos = text.find("remove node_id=1 silence_ms=");

    REQUIRE(join_pos != string::npos);
    REQUIRE(grant_pos != string::npos);
    REQUIRE(remove_pos != string::npos);
    REQUIRE(join_pos < grant_pos);
    REQUIRE(grant_pos < remove_pos);
}

TEST_CASE("TokenGrantDispatcher 按 grant node_id 发送到对应 socket")
{
    string base = string("token_scheduler_test_") + to_string(getpid()) + "_";
    TokenAuthorizationDatagramReceiver receiver1(make_token_authorization_socket_name(base + "5602"));
    TokenAuthorizationDatagramReceiver receiver2(make_token_authorization_socket_name(base + "5603"));

    TokenSchedulerConfig config;
    config.node_ids = {1, 2};
    config.duration_ms = 1000;
    config.guard_interval_ms = 100;
    config.node_sockets[1] = base + "5602";
    config.node_sockets[2] = base + "5603";

    TokenGrantDispatcher dispatcher(config);
    TokenGrant first = {1, 10, 1000, 100};
    TokenGrant second = {2, 11, 1000, 100};

    REQUIRE(dispatcher.send_grant(first, 5000));
    REQUIRE(dispatcher.send_grant(second, 6000));

    TokenAuthorizationEvent event = {};
    REQUIRE(receiver1.recv_event(&event));
    REQUIRE(event.node_id == 1);
    REQUIRE(event.expires_at_ms == 6000);
    REQUIRE(receiver2.recv_event(&event));
    REQUIRE(event.node_id == 2);
    REQUIRE(event.expires_at_ms == 7000);
}

TEST_CASE("run_token_scheduler 在收到声明后才开始按顺序发 grant")
{
    TokenSchedulerConfig config;
    config.node_ids = {1, 2};
    config.duration_ms = 10;
    config.guard_interval_ms = 5;

    atomic_bool stop_requested(false);
    ostringstream output;
    size_t sleep_calls = 0;

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
            vector<uint8_t> ready_nodes;
            if (sleep_calls == 2)
            {
                ready_nodes.push_back(1);
                ready_nodes.push_back(2);
            }
            return ready_nodes;
        });

    REQUIRE(rc == 0);

    string text = output.str();
    REQUIRE(text.find("grant seq=0 node_id=1 duration_ms=10 guard_interval_ms=5 window_end_offset_ms=10") != string::npos);
    REQUIRE(text.find("grant seq=1 node_id=2 duration_ms=10 guard_interval_ms=5 window_end_offset_ms=10") != string::npos);
}

TEST_CASE("run_token_scheduler 能从 ready socket 接收真实声明")
{
    const string ready_socket = make_token_ready_socket_name(kDefaultTokenReadySocketBase);
    TokenAuthorizationDatagramSender sender(ready_socket);

    TokenSchedulerConfig config;
    config.node_ids = {1, 2};
    config.duration_ms = 10;
    config.guard_interval_ms = 5;
    atomic_bool stop_requested(false);
    ostringstream output;
    size_t sleep_calls = 0;
    bool sent_ready = false;

    int rc = run_token_scheduler(
        config,
        output,
        stop_requested,
        [&](uint32_t) {
            sleep_calls += 1;
            if (!sent_ready && sleep_calls == 2)
            {
                TokenAuthorizationEvent first = {};
                first.node_id = 1;
                REQUIRE(sender.send_event(first));

                TokenAuthorizationEvent second = {};
                second.node_id = 2;
                REQUIRE(sender.send_event(second));
                sent_ready = true;
            }
            if (sleep_calls >= 5)
            {
                stop_requested.store(true);
            }
        },
        []() {
            return static_cast<uint64_t>(1000);
        });

    REQUIRE(rc == 0);

    string text = output.str();
    REQUIRE(text.find("grant seq=0 node_id=1 duration_ms=10 guard_interval_ms=5 window_end_offset_ms=10") != string::npos);
    REQUIRE(text.find("grant seq=1 node_id=2 duration_ms=10 guard_interval_ms=5 window_end_offset_ms=10") != string::npos);
}

int main(int argc, char *argv[])
{
    Catch::Session session;
    return session.run(argc, argv);
}
