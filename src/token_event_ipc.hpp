#pragma once

#include <stdint.h>
#include <string>

struct TokenAuthorizationEvent {
    uint8_t node_id;
    uint8_t reserved[7];
    uint64_t sequence;
    uint32_t duration_ms;
    uint32_t reserved2;
    uint64_t expires_at_ms;
};

class TokenEventDatagramSender
{
public:
    explicit TokenEventDatagramSender(const std::string &socket_path);
    bool send(const TokenAuthorizationEvent &event) const;

private:
    std::string socket_path_;
};
