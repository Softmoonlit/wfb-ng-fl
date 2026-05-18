#include <catch2/catch_session.hpp>
#include <catch2/catch_test_macros.hpp>

#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <cerrno>
#include <cstring>
#include <string>

#include "token_event_ipc.hpp"

namespace {

class ScopedUnixDatagramReceiver {
public:
    explicit ScopedUnixDatagramReceiver(const std::string &path) : fd_(-1), path_(path)
    {
        fd_ = socket(AF_UNIX, SOCK_DGRAM, 0);
        REQUIRE(fd_ >= 0);

        sockaddr_un addr = {};
        addr.sun_family = AF_UNIX;
        REQUIRE(path.size() < sizeof(addr.sun_path));
        std::strncpy(addr.sun_path, path.c_str(), sizeof(addr.sun_path) - 1);

        unlink(path.c_str());
        REQUIRE(bind(fd_, reinterpret_cast<const sockaddr *>(&addr), sizeof(addr)) == 0);
    }

    ~ScopedUnixDatagramReceiver()
    {
        if (fd_ >= 0)
        {
            close(fd_);
        }
        unlink(path_.c_str());
    }

    TokenAuthorizationEvent recv_event()
    {
        TokenAuthorizationEvent event = {};
        const ssize_t received = recv(fd_, &event, sizeof(event), 0);
        REQUIRE(received == static_cast<ssize_t>(sizeof(event)));
        return event;
    }

private:
    int fd_;
    std::string path_;
};

std::string make_socket_path()
{
    char path[] = "/tmp/wfb-token-ipc-test-XXXXXX";
    int fd = mkstemp(path);
    REQUIRE(fd >= 0);
    close(fd);
    unlink(path);
    return std::string(path);
}

}

TEST_CASE("Unix datagram IPC 能完整传递单条授权事件")
{
    const std::string socket_path = make_socket_path();
    ScopedUnixDatagramReceiver receiver(socket_path);
    TokenEventDatagramSender sender(socket_path);

    TokenAuthorizationEvent expected = {};
    expected.node_id = 7;
    expected.sequence = 42;
    expected.duration_ms = 150;
    expected.expires_at_ms = 123456;

    REQUIRE(sender.send(expected));

    const TokenAuthorizationEvent actual = receiver.recv_event();
    REQUIRE(actual.node_id == expected.node_id);
    REQUIRE(actual.sequence == expected.sequence);
    REQUIRE(actual.duration_ms == expected.duration_ms);
    REQUIRE(actual.expires_at_ms == expected.expires_at_ms);
}

TEST_CASE("Unix datagram IPC 保持多条授权事件的消息边界")
{
    const std::string socket_path = make_socket_path();
    ScopedUnixDatagramReceiver receiver(socket_path);
    TokenEventDatagramSender sender(socket_path);

    TokenAuthorizationEvent first = {};
    first.node_id = 7;
    first.sequence = 42;
    first.duration_ms = 150;
    first.expires_at_ms = 123456;

    TokenAuthorizationEvent second = {};
    second.node_id = 7;
    second.sequence = 43;
    second.duration_ms = 200;
    second.expires_at_ms = 123656;

    REQUIRE(sender.send(first));
    REQUIRE(sender.send(second));

    const TokenAuthorizationEvent actual_first = receiver.recv_event();
    const TokenAuthorizationEvent actual_second = receiver.recv_event();

    REQUIRE(actual_first.sequence == first.sequence);
    REQUIRE(actual_first.duration_ms == first.duration_ms);
    REQUIRE(actual_second.sequence == second.sequence);
    REQUIRE(actual_second.duration_ms == second.duration_ms);
}

TEST_CASE("sender 未启动时 Unix datagram IPC 发送失败且 fail-closed")
{
    const std::string socket_path = make_socket_path();
    TokenEventDatagramSender sender(socket_path);

    TokenAuthorizationEvent event = {};
    event.node_id = 7;
    event.sequence = 44;
    event.duration_ms = 250;
    event.expires_at_ms = 123999;

    errno = 0;
    REQUIRE_FALSE(sender.send(event));
    REQUIRE((errno == ENOENT || errno == ECONNREFUSED));
}

