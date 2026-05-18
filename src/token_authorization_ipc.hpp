#pragma once

#include <stdint.h>
#include <string>

#include "token_event_ipc.hpp"

extern const char * const kDefaultTokenReadySocketBase;

std::string make_token_authorization_socket_name(const std::string &base_socket_name);
std::string make_token_ready_socket_name(const std::string &base_socket_name);

class TokenAuthorizationDatagramReceiver
{
public:
    explicit TokenAuthorizationDatagramReceiver(const std::string &socket_name, int rcv_buf_size = 0);
    ~TokenAuthorizationDatagramReceiver();

    int fd(void) const { return fd_; }
    bool recv_event(TokenAuthorizationEvent *event) const;

private:
    int fd_;
};

class TokenAuthorizationDatagramSender
{
public:
    explicit TokenAuthorizationDatagramSender(const std::string &socket_name);
    ~TokenAuthorizationDatagramSender();

    bool send_event(const TokenAuthorizationEvent &event) const;

private:
    int fd_;
    std::string dest_socket_name_;
};
