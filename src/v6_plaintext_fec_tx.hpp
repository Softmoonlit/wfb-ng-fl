#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

struct V6PlaintextFecRadioConfig {
    uint8_t bandwidth = 20;
    uint8_t mcs_index = 0;
    bool short_gi = false;
};

class V6PlaintextFecTransmitter {
public:
    V6PlaintextFecTransmitter(const std::string &host,
                              int port,
                              int snd_buf,
                              uint8_t local_node_id,
                              int fec_k,
                              int fec_n);
    V6PlaintextFecTransmitter(const std::vector<std::string> &interfaces,
                              uint32_t channel_id,
                              uint8_t local_node_id,
                              const V6PlaintextFecRadioConfig &radio_config,
                              int fec_k,
                              int fec_n);
    ~V6PlaintextFecTransmitter();

    bool send_payload(const uint8_t *payload, size_t payload_size);
    bool close_pending_block();

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};
