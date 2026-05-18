#include "token_authorization_ipc.hpp"

#include <unistd.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <string.h>

#include <cerrno>
#include <stdexcept>

#include "wifibroadcast.hpp"

const char * const kDefaultTokenReadySocketBase = "wfb-scheduler";

std::string make_token_authorization_socket_name(const std::string &base_socket_name)
{
    return base_socket_name + ".token";
}

std::string make_token_ready_socket_name(const std::string &base_socket_name)
{
    return base_socket_name + ".ready";
}

TokenAuthorizationDatagramReceiver::TokenAuthorizationDatagramReceiver(const std::string &socket_name, int rcv_buf_size)
    : fd_(open_unix_socket_for_rx(socket_name.c_str(), rcv_buf_size))
{
}

TokenAuthorizationDatagramReceiver::~TokenAuthorizationDatagramReceiver()
{
    if (fd_ >= 0)
    {
        close(fd_);
    }
}

bool TokenAuthorizationDatagramReceiver::recv_event(TokenAuthorizationEvent *event) const
{
    const ssize_t received = recv(fd_, event, sizeof(*event), MSG_DONTWAIT);
    if (received < 0)
    {
        if (errno == EWOULDBLOCK || errno == EAGAIN)
        {
            return false;
        }

        throw std::runtime_error(string_format("Unable to receive token authorization event: %s", strerror(errno)));
    }

    return received == static_cast<ssize_t>(sizeof(*event));
}

TokenAuthorizationDatagramSender::TokenAuthorizationDatagramSender(const std::string &socket_name)
    : dest_socket_name_(socket_name)
{
    fd_ = socket(AF_UNIX, SOCK_DGRAM, 0);
    if (fd_ < 0)
    {
        throw std::runtime_error(std::string("failed to create socket: ") + strerror(errno));
    }
}

TokenAuthorizationDatagramSender::~TokenAuthorizationDatagramSender()
{
    if (fd_ >= 0)
    {
        close(fd_);
    }
}

bool TokenAuthorizationDatagramSender::send_event(const TokenAuthorizationEvent &event) const
{
    struct sockaddr_un addr = {};
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path + 1, dest_socket_name_.c_str(), sizeof(addr.sun_path) - 2);

    const socklen_t addr_len = sizeof(sa_family_t) + strlen(addr.sun_path + 1) + 1;
    ssize_t bytes = sendto(fd_, &event, sizeof(event), MSG_DONTWAIT, (struct sockaddr *)&addr, addr_len);
    return bytes == sizeof(event);
}
