#include "token_event_ipc.hpp"

#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <cerrno>
#include <cstring>
#include <stdexcept>

#include "wifibroadcast.hpp"

namespace {

sockaddr_un make_sockaddr(const std::string &socket_path)
{
    sockaddr_un addr = {};
    addr.sun_family = AF_UNIX;

    if (socket_path.size() >= sizeof(addr.sun_path))
    {
        throw std::runtime_error("Unix socket path is too long");
    }

    std::strncpy(addr.sun_path, socket_path.c_str(), sizeof(addr.sun_path) - 1);
    return addr;
}

}

TokenEventDatagramSender::TokenEventDatagramSender(const std::string &socket_path) : socket_path_(socket_path)
{
}

bool TokenEventDatagramSender::send(const TokenAuthorizationEvent &event) const
{
    const int fd = socket(AF_UNIX, SOCK_DGRAM, 0);
    if (fd < 0)
    {
        throw std::runtime_error(string_format("Unable to open Unix datagram socket: %s", strerror(errno)));
    }

    const sockaddr_un addr = make_sockaddr(socket_path_);
    const ssize_t sent = sendto(fd,
                                &event,
                                sizeof(event),
                                0,
                                reinterpret_cast<const sockaddr *>(&addr),
                                sizeof(addr));
    const int saved_errno = errno;
    close(fd);

    if (sent != static_cast<ssize_t>(sizeof(event)))
    {
        errno = saved_errno;
        return false;
    }

    return true;
}
