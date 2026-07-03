#pragma once
// -*- C++ -*-
//
// Copyright (C) 2017 - 2024 Vasily Evseenko <svpcom@p2ptech.org>

/*
 *   This program is free software; you can redistribute it and/or modify
 *   it under the terms of the GNU General Public License as published by
 *   the Free Software Foundation; version 3.
 *
 *   This program is distributed in the hope that it will be useful,
 *   but WITHOUT ANY WARRANTY; without even the implied warranty of
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 *   GNU General Public License for more details.
 *
 *   You should have received a copy of the GNU General Public License along
 *   with this program; if not, write to the Free Software Foundation, Inc.,
 *   51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
 */

#include <unordered_map>
#include <stdint.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <stdio.h>
#include <errno.h>
#include <string>
#include <set>
#include <string.h>
#include <stdexcept>

#include "wifibroadcast.hpp"
#include "zfex.h"
#include "control_envelope.hpp"
#include "token_event_ipc.hpp"
#include "token_authorization_ipc.hpp"

// Forward declaration for isolated packet loss notification
class PacketLossListener
{
public:
    virtual ~PacketLossListener() = default;
    virtual void on_packet_loss(uint32_t lost_count, uint32_t last_seq, uint32_t new_seq) = 0;
};

class TokenControlListener
{
public:
    virtual ~TokenControlListener() = default;
    virtual void on_token_control(const ControlEnvelopeView &packet) = 0;
};

class TokenEventDatagramListener : public TokenControlListener
{
public:
    explicit TokenEventDatagramListener(const std::string &socket_path,
                                        const std::string &ready_socket_base = kDefaultTokenReadySocketBase)
        : grant_sender_(make_token_authorization_socket_name(socket_path)),
          ready_sender_(make_token_ready_socket_name(ready_socket_base)),
          last_send_succeeded_(false) {}

    virtual void on_token_control(const ControlEnvelopeView &packet)
    {
        TokenAuthorizationEvent event = {};
        event.sequence = packet.sequence;
        event.duration_ms = packet.grant_duration_ms;
        event.expires_at_ms = packet.grant_expires_at_ms;

        if (packet.control_type == WFB_CONTROL_TYPE_READY)
        {
            event.node_id = packet.source_node;
            last_send_succeeded_ = ready_sender_.send_event(event);
            return;
        }

        event.node_id = packet.target_node;
        last_send_succeeded_ = grant_sender_.send(event);
    }

    bool last_send_succeeded(void) const { return last_send_succeeded_; }

private:
    TokenEventDatagramSender grant_sender_;
    TokenAuthorizationDatagramSender ready_sender_;
    bool last_send_succeeded_;
};
typedef enum {
    LOCAL,
    FORWARDER,
    AGGREGATOR
} rx_mode_t;

class BaseAggregator
{
public:
    virtual ~BaseAggregator(){}
    virtual void process_packet(const uint8_t *buf, size_t size, uint8_t wlan_idx, const uint8_t *antenna,
                                const int8_t *rssi, const int8_t *noise, uint16_t freq, uint8_t mcs_index,
                                uint8_t bandwidth, sockaddr_in *sockaddr) = 0;

    virtual void dump_stats(void) = 0;
};


class Forwarder : public BaseAggregator
{
public:
    Forwarder(const std::string &client_addr, int client_port, int snd_buf_size);
    virtual ~Forwarder();
    virtual void process_packet(const uint8_t *buf, size_t size, uint8_t wlan_idx, const uint8_t *antenna,
                                const int8_t *rssi, const int8_t *noise, uint16_t freq, uint8_t mcs_index,
                                uint8_t bandwidth,sockaddr_in *sockaddr);
    virtual void dump_stats(void) {}
private:
    int sockfd;
    struct sockaddr_in saddr;
};

typedef struct {
    uint64_t block_idx;
    uint8_t** fragments;
    size_t *fragment_map;
    uint8_t fragment_to_send_idx;
    uint8_t has_fragments;
} rx_ring_item_t;


#define RX_RING_SIZE 40

typedef struct {
    uint8_t source_node;
    uint32_t seq;
    rx_ring_item_t rx_ring[RX_RING_SIZE];
    int rx_ring_front;
    int rx_ring_alloc;
    uint64_t last_known_block;
} rx_source_state_t;

static inline int modN(int x, int base)
{
    return (base + (x % base)) % base;
}

class rxAntennaItem
{
public:
    rxAntennaItem(void) : count_all(0),
                          rssi_sum(0), rssi_min(0), rssi_max(0),
                          snr_sum(0), snr_min(0), snr_max(0) {}

    void log_rssi(int8_t rssi, int8_t noise){
        int8_t snr = (noise != SCHAR_MAX) ? rssi - noise : 0;

        if(count_all == 0){
            rssi_min = rssi;
            rssi_max = rssi;
            snr_min = snr;
            snr_max = snr;
        } else {
            rssi_min = std::min(rssi, rssi_min);
            rssi_max = std::max(rssi, rssi_max);
            snr_min = std::min(snr, snr_min);
            snr_max = std::max(snr, snr_max);
        }
        rssi_sum += rssi;
        snr_sum += snr;
        count_all += 1;
    }

    int32_t count_all;
    int32_t rssi_sum;
    int8_t rssi_min;
    int8_t rssi_max;
    int32_t snr_sum;
    int8_t snr_min;
    int8_t snr_max;
};

struct rxAntennaKey
{
    uint16_t freq;
    uint64_t antenna_id;
    uint8_t mcs_index;
    uint8_t bandwidth;

    bool operator==(const rxAntennaKey &other) const
    {
        return (freq == other.freq && \
                antenna_id == other.antenna_id && \
                mcs_index == other.mcs_index && \
                bandwidth == other.bandwidth);
    }
};


template <typename T>
void hash_combine(std::size_t& seed, const T& v)
{
    seed ^= std::hash<T>{}(v) + 0x9e3779b9 + (seed << 6) + (seed >> 2);
}


template<>
struct std::hash<rxAntennaKey>
{
    std::size_t operator()(const rxAntennaKey& k) const noexcept
    {
        std::size_t h = 0;
        hash_combine(h, k.freq);
        hash_combine(h, k.antenna_id);
        hash_combine(h, k.mcs_index);
        hash_combine(h, k.bandwidth);
        return h;
    }
};

typedef std::unordered_map<rxAntennaKey, rxAntennaItem> rx_antenna_stat_t;

class Aggregator : public BaseAggregator
{
public:
    Aggregator(const std::string &keypair, uint64_t epoch, uint32_t channel_id, uint8_t local_node_id = 0,
               bool trusted_plaintext = false, int plaintext_fec_k = -1, int plaintext_fec_n = -1);
    virtual ~Aggregator();
    virtual void process_packet(const uint8_t *buf, size_t size, uint8_t wlan_idx, const uint8_t *antenna,
                                const int8_t *rssi, const int8_t *noise, uint16_t freq, uint8_t mcs_index,
                                uint8_t bandwidth, sockaddr_in *sockaddr);
    virtual void dump_stats(void);

    // Packet loss listener for immediate notifications
    void set_packet_loss_listener(PacketLossListener* listener) { packet_loss_listener_ = listener; }
    void set_token_control_listener(TokenControlListener* listener) { token_control_listener_ = listener; }
    void set_known_client_node_ids(const std::set<uint8_t> &node_ids) { known_client_node_ids_ = node_ids; }
    uint64_t reassembly_overflow_evict_total(void) const { return reassembly_overflow_evict_total_; }
    uint32_t unfinished_block_limit(void) const { return RX_RING_SIZE; }

    // Make stats public for android userspace receiver
    void clear_stats(void)
    {
        antenna_stat.clear();
        count_p_all = 0;
        count_b_all = 0;
        count_p_dec_err = 0;
        count_p_session = 0;
        count_p_data = 0;
        count_p_uniq.clear();
        count_p_fec_recovered = 0;
        count_p_lost = 0;
        count_p_bad = 0;
        count_p_override = 0;
        count_p_outgoing = 0;
        count_b_outgoing = 0;
    }

    rx_antenna_stat_t antenna_stat;
    uint32_t count_p_all;
    uint32_t count_b_all;
    uint32_t count_p_dec_err;
    uint32_t count_p_session;
    uint32_t count_p_data;
    std::set<uint64_t> count_p_uniq;
    uint32_t count_p_fec_recovered;
    uint32_t count_p_lost;
    uint32_t count_p_bad;
    uint32_t count_p_override;
    uint32_t count_p_outgoing;
    uint32_t count_b_outgoing;
    GrantFilterCounters grant_filter_counters_;
    ReadyFilterCounters ready_filter_counters_;

protected:
    virtual void send_to_socket(const uint8_t *payload, uint16_t packet_size) = 0;

private:
    Aggregator(const Aggregator&);
    Aggregator& operator=(const Aggregator&);

    void init_fec(int k, int n);
    void deinit_fec(void);
    void init_source_state(rx_source_state_t *state, uint8_t source_node);
    void deinit_source_state(rx_source_state_t *state);
    void clear_source_states(void);
    rx_source_state_t *get_source_state(uint8_t source_node, bool create_if_missing);
    void send_packet(rx_source_state_t *state, int ring_idx, int fragment_idx);
    void apply_fec(rx_source_state_t *state, int ring_idx);
    void log_rssi(const sockaddr_in *sockaddr, uint8_t wlan_idx, const uint8_t *ant, const int8_t *rssi,
                  const int8_t *noise, uint16_t freq, uint8_t mcs_index, uint8_t bandwidth);
    int get_block_ring_idx(rx_source_state_t *state, uint64_t block_idx);
    int rx_ring_push(rx_source_state_t *state);
    // cppcheck-suppress unusedPrivateFunction
    static int get_tag(const void *buf, size_t size, uint8_t tag_id, void *value, size_t value_size);

    fec_t* fec_p;
    int fec_k;  // RS number of primary fragments in block
    int fec_n;  // RS total number of fragments in block
    uint8_t session_hash[crypto_generichash_BYTES];

    rx_source_state_t *source_states_[256];
    uint64_t epoch; // current epoch
    const uint32_t channel_id; // (link_id << 8) + port_number
    const uint8_t local_node_id;
    const bool trusted_plaintext;

    // rx->tx keypair；trusted_plaintext 下仅保留成员布局，不读取旧 key 文件
    uint8_t rx_secretkey[crypto_box_SECRETKEYBYTES];
    uint8_t tx_publickey[crypto_box_PUBLICKEYBYTES];
    uint8_t session_key[crypto_aead_chacha20poly1305_KEYBYTES];

    // Packet loss listener for immediate notifications
    PacketLossListener* packet_loss_listener_ = nullptr;
    TokenControlListener* token_control_listener_ = nullptr;
    GrantFilterState grant_filter_state_ = {};
    uint64_t reassembly_overflow_evict_total_ = 0;
    std::set<uint8_t> known_client_node_ids_;
};


class AggregatorUDPv4 : public Aggregator
{
public:
    AggregatorUDPv4(const std::string &client_addr, int client_port, const std::string &keypair, uint64_t epoch, uint32_t channel_id, int snd_buf_size,
                    uint8_t local_node_id = 0, bool trusted_plaintext = false, int plaintext_fec_k = -1, int plaintext_fec_n = -1);
    virtual ~AggregatorUDPv4();

protected:
    virtual void send_to_socket(const uint8_t *payload, uint16_t packet_size);

private:
    AggregatorUDPv4(const AggregatorUDPv4&);
    AggregatorUDPv4& operator=(const AggregatorUDPv4&);

    int sockfd;
    struct sockaddr_in saddr;
};


class AggregatorUNIX : public Aggregator
{
public:
    AggregatorUNIX(const std::string &unix_socket, const std::string &keypair, uint64_t epoch, uint32_t channel_id, int snd_buf_size,
                   uint8_t local_node_id = 0, bool trusted_plaintext = false, int plaintext_fec_k = -1, int plaintext_fec_n = -1);
    virtual ~AggregatorUNIX();

protected:
    virtual void send_to_socket(const uint8_t *payload, uint16_t packet_size);

private:
    AggregatorUNIX(const AggregatorUNIX&);
    AggregatorUNIX& operator=(const AggregatorUNIX&);

    int sockfd;
    struct sockaddr_un saddr;
};


class Receiver
{
public:
    Receiver(const char* wlan, int wlan_idx, uint32_t channel_id, BaseAggregator* agg, int rcv_buf_size);
    ~Receiver();
    void loop_iter(void);
    int getfd(void){ return fd; }
private:
    int wlan_idx;
    BaseAggregator *agg;
    int fd;
    pcap_t *ppcap;
};
