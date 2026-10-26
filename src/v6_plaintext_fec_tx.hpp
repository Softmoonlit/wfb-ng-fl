#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

#include "v6_radio_control.hpp"

using V6PlaintextFecRadioConfig = V6RadioConfig;

class V6PlaintextFecTransmitter {
public:
    V6PlaintextFecTransmitter(const std::string &host,
                              int port,
                              int snd_buf,
                              uint8_t local_node_id,
                              const V6PlaintextFecRadioConfig &radio_config,
                              int fec_k,
                              int fec_n,
                              uint64_t session_id);
    V6PlaintextFecTransmitter(const std::vector<std::string> &interfaces,
                              uint32_t channel_id,
                              uint8_t local_node_id,
                              const V6PlaintextFecRadioConfig &radio_config,
                              int fec_k,
                              int fec_n,
                              uint64_t session_id);
    ~V6PlaintextFecTransmitter();

    void update_radio_config(const V6PlaintextFecRadioConfig &radio_config);

    bool send_payload(const uint8_t *payload, size_t payload_size);
    bool close_pending_block();

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};
