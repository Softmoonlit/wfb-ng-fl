#include <catch2/catch_session.hpp>
#include <catch2/catch_test_macros.hpp>

#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <cstring>
#include <string>

#include "token_authorization_ipc.hpp"

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
                                    abstract_addr_len(addr_));
        REQUIRE(sent == static_cast<ssize_t>(sizeof(event)));
    }

private:
    static socklen_t abstract_addr_len(const sockaddr_un &addr)
    {
        return sizeof(sa_family_t) + std::strlen(addr.sun_path + 1) + 1;
    }

    int fd_;
    sockaddr_un addr_;
};

}

TEST_CASE("make_token_authorization_socket_name 为基名追加 token 后缀")
{
    REQUIRE(make_token_authorization_socket_name("wfb.sock") == "wfb.sock.token");
}

TEST_CASE("make_token_ready_socket_name 为基名追加 ready 后缀")
{
    REQUIRE(make_token_ready_socket_name("wfb.sock") == "wfb.sock.ready");
}

TEST_CASE("TokenAuthorizationDatagramReceiver 能接收单条授权事件")
{
    const std::string socket_name = "wfb-token-ipc-test";
    TokenAuthorizationDatagramReceiver receiver(socket_name);
    ScopedUnixDatagramSender sender(socket_name);

    TokenAuthorizationEvent expected = {};
    expected.node_id = 7;
    expected.sequence = 42;
    expected.duration_ms = 150;
    expected.expires_at_ms = 123456;
    sender.send_event(expected);

    TokenAuthorizationEvent actual = {};
    REQUIRE(receiver.recv_event(&actual));
    REQUIRE(actual.node_id == expected.node_id);
    REQUIRE(actual.sequence == expected.sequence);
    REQUIRE(actual.duration_ms == expected.duration_ms);
    REQUIRE(actual.expires_at_ms == expected.expires_at_ms);
}

TEST_CASE("TokenAuthorizationDatagramSender 使用接收端的抽象 socket 地址格式")
{
    const std::string socket_name = "wfb-token-ipc-sender-test";
    TokenAuthorizationDatagramReceiver receiver(socket_name);
    TokenAuthorizationDatagramSender sender(socket_name);

    TokenAuthorizationEvent expected = {};
    expected.node_id = 3;
    expected.expires_at_ms = 9999;

    REQUIRE(sender.send_event(expected));

    TokenAuthorizationEvent actual = {};
    REQUIRE(receiver.recv_event(&actual));
    REQUIRE(actual.node_id == expected.node_id);
    REQUIRE(actual.expires_at_ms == expected.expires_at_ms);
}

int main(int argc, char *argv[])
{
    Catch::Session session;
    return session.run(argc, argv);
}
