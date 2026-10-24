#include <cassert>
#include <stdexcept>
#include <vector>
#include <string>
#include <sys/ioctl.h>
#include <net/if.h>
#include <linux/if_packet.h>
#include <unistd.h>
#include <endian.h>
#include <iostream>
#include "zfex.h"
using namespace std;
#include "tx.hpp"
#define LOCAL RX_LOCAL
#include "rx.hpp"
#undef LOCAL

static vector<tags_item_t> test_tags;
class EncryptedTx : public Transmitter {
public:
    explicit EncryptedTx(const string &key) : Transmitter(2, 3, key, 7, 42, 0, test_tags, 1, false) {}
    vector<vector<uint8_t>> frames;
    void select_output(int) override {}
    void dump_stats(uint64_t, uint32_t &, uint32_t &, uint32_t &) override {}
    void update_radiotap_header(radiotap_header_t &) override {}
    radiotap_header_t get_radiotap_header() override { return {}; }
    void send(char value) { assert(send_packet(reinterpret_cast<const uint8_t *>(&value), 1, 0)); }
protected:
    void inject_packet(const uint8_t *p, size_t n) override { frames.emplace_back(p, p+n); }
    void set_mark(uint32_t) override {}
};
class EncryptedRx : public Aggregator {
public:
    explicit EncryptedRx(const string &key) : Aggregator(key, 7, 42, 9, false) {}
    string output;
    void feed(const vector<uint8_t> &frame) {
        uint8_t ant[RX_ANT_MAX]; memset(ant, 0xff, sizeof(ant));
        int8_t signal[RX_ANT_MAX] = {};
        process_packet(frame.data(), frame.size(), 0, ant, signal, signal, 0, 0, 0, nullptr);
    }
protected:
    void send_to_socket(const uint8_t *p, uint16_t n) override { output.append(reinterpret_cast<const char *>(p), n); }
};
class KeyFiles {
public:
    char tx_path[40] = "/tmp/session-tx-key-XXXXXX";
    char rx_path[40] = "/tmp/session-rx-key-XXXXXX";
    KeyFiles() {
        uint8_t tx_public[crypto_box_PUBLICKEYBYTES], tx_secret[crypto_box_SECRETKEYBYTES];
        uint8_t rx_public[crypto_box_PUBLICKEYBYTES], rx_secret[crypto_box_SECRETKEYBYTES];
        assert(crypto_box_keypair(tx_public, tx_secret) == 0);
        assert(crypto_box_keypair(rx_public, rx_secret) == 0);
        int tx_fd = mkstemp(tx_path), rx_fd = mkstemp(rx_path); assert(tx_fd >= 0 && rx_fd >= 0);
        assert(write(tx_fd, tx_secret, sizeof(tx_secret)) == sizeof(tx_secret));
        assert(write(tx_fd, rx_public, sizeof(rx_public)) == sizeof(rx_public));
        assert(write(rx_fd, rx_secret, sizeof(rx_secret)) == sizeof(rx_secret));
        assert(write(rx_fd, tx_public, sizeof(tx_public)) == sizeof(tx_public));
        close(tx_fd); close(rx_fd);
    }
    ~KeyFiles() { unlink(tx_path); unlink(rx_path); }
};
int main() {
    assert(sodium_init() >= 0);
    KeyFiles keys;
    EncryptedTx tx(keys.tx_path); EncryptedRx rx(keys.rx_path);
    tx.send_session_key(); tx.send('d'); tx.send('e');
    assert(tx.frames.size() == 4 && tx.frames[0][0] == WFB_PACKET_SESSION);
    const auto *header = reinterpret_cast<const wblock_hdr_t *>(tx.frames[1].data());
    assert(be16toh(header->magic) == WFB_DATA_MAGIC && header->version == WFB_DATA_VERSION);
    assert(be64toh(header->session_id) != 0);
    rx.feed(tx.frames[0]); assert(rx.count_p_session == 1);
    rx.feed(tx.frames[2]); rx.feed(tx.frames[3]); // encrypted primary plus parity reconstruct missing fragment
    assert(rx.output == "de" && rx.count_p_fec_recovered == 1 && rx.count_p_dec_err == 0);
    auto tampered = tx.frames[1];
    reinterpret_cast<wblock_hdr_t *>(tampered.data())->session_id ^= htobe64(1ULL << 63);
    rx.feed(tampered);
    assert(rx.count_p_dec_err == 1 && rx.output == "de"); // upgraded header is authenticated
    tampered = tx.frames[1]; tampered.back() ^= 1;
    rx.feed(tampered); assert(rx.count_p_dec_err == 2 && rx.output == "de");
    rx.feed(tx.frames[1]); assert(rx.output == "de"); // duplicate does not re-deliver
    tx.send('f'); tx.send('g');
    rx.feed(tx.frames[4]); rx.feed(tx.frames[5]);
    assert(rx.output == "defg"); // ordinary encrypted primary delivery
    EncryptedTx restarted(keys.tx_path);
    restarted.send_session_key(); restarted.send('h'); restarted.send('i');
    rx.feed(restarted.frames[0]);
    assert(rx.count_p_session == 2);
    rx.feed(restarted.frames[1]); rx.feed(restarted.frames[2]);
    assert(rx.output == "defghi"); // real new cryptographic session restarts block zero
    std::cout << "Encrypted keypair/session-key/DATA/FEC roundtrip and header authentication: PASS\n";
}
