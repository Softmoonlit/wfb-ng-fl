#include "v6_plaintext_fec_tx.hpp"

#include <cassert>
#include <memory>
#include <string>
#include <vector>
#include <net/if.h>
#include <linux/if_packet.h>
#include <sys/ioctl.h>

#include "zfex.h"

using namespace std;

#include "tx.hpp"

class V6PlaintextFecTransmitter::Impl {
public:
    vector<tags_item_t> tags;
    unique_ptr<Transmitter> transmitter;
};

V6PlaintextFecTransmitter::V6PlaintextFecTransmitter(const string &host,
                                                     int port,
                                                     int snd_buf,
                                                     uint8_t local_node_id,
                                                     int fec_k,
                                                     int fec_n)
    : impl_(new Impl())
{
    impl_->transmitter.reset(new UdpTransmitter(fec_k,
                                                fec_n,
                                                WFB_TRUSTED_PLAINTEXT_KEYPAIR,
                                                host,
                                                port,
                                                0,
                                                0,
                                                0,
                                                impl_->tags,
                                                false,
                                                0,
                                                snd_buf,
                                                local_node_id,
                                                true));
}

V6PlaintextFecTransmitter::V6PlaintextFecTransmitter(const vector<string> &interfaces,
                                                     uint32_t channel_id,
                                                     uint8_t local_node_id,
                                                     const V6PlaintextFecRadioConfig &radio_config,
                                                     int fec_k,
                                                     int fec_n)
    : impl_(new Impl())
{
    radiotap_header_t radiotap_header = init_radiotap_header(0,
                                                             false,
                                                             radio_config.short_gi,
                                                             radio_config.bandwidth,
                                                             radio_config.mcs_index,
                                                             false,
                                                             0);
    impl_->transmitter.reset(new RawSocketTransmitter(fec_k,
                                                      fec_n,
                                                      WFB_TRUSTED_PLAINTEXT_KEYPAIR,
                                                      0,
                                                      channel_id,
                                                      0,
                                                      impl_->tags,
                                                      interfaces,
                                                      radiotap_header,
                                                      FRAME_TYPE_DATA,
                                                      false,
                                                      0,
                                                      0,
                                                      0,
                                                      local_node_id,
                                                      true));
}

V6PlaintextFecTransmitter::~V6PlaintextFecTransmitter() = default;

bool V6PlaintextFecTransmitter::send_payload(const uint8_t *payload, size_t payload_size)
{
    return impl_->transmitter->send_packet(payload, payload_size, 0);
}

bool V6PlaintextFecTransmitter::close_pending_block()
{
    return impl_->transmitter->send_packet(NULL, 0, WFB_PACKET_FEC_ONLY);
}
