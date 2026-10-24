#include <stdexcept>
#include <cassert>
#include <vector>
#include <string>
#include <sys/ioctl.h>
#include <net/if.h>
#include <linux/if_packet.h>
#include <unistd.h>
#include "zfex.h"
using namespace std;
#include "tx.hpp"
#define LOCAL RX_LOCAL
#include "rx.hpp"
#undef LOCAL
#include "token_scheduler.hpp"
#include "token_authorization.hpp"
#include <cassert>
#include <endian.h>
#include <unistd.h>
#include <iostream>

static std::vector<tags_item_t> test_tags;
class CaptureTx : public Transmitter {
public:
    explicit CaptureTx(uint8_t node) : Transmitter(2, 3, "", 0, 0, 0, test_tags, node, true) {}
    std::vector<std::vector<uint8_t>> frames;
    void select_output(int) override {}
    void dump_stats(uint64_t, uint32_t &, uint32_t &, uint32_t &) override {}
    void update_radiotap_header(radiotap_header_t &) override {}
    radiotap_header_t get_radiotap_header() override { return {}; }
    void send(char value) { assert(send_packet(reinterpret_cast<const uint8_t *>(&value), 1, 0)); }
protected:
    void inject_packet(const uint8_t *p, size_t n) override { frames.emplace_back(p, p+n); }
    void set_mark(uint32_t) override {}
};
class CaptureRx : public Aggregator {
public:
    CaptureRx() : Aggregator("", 0, 0, 9, true, 2, 3) {}
    std::string output;
    void feed(const std::vector<uint8_t> &frame) {
        uint8_t ant[RX_ANT_MAX]; memset(ant, 0xff, sizeof(ant));
        int8_t signal[RX_ANT_MAX] = {};
        process_packet(frame.data(), frame.size(), 0, ant, signal, signal, 0, 0, 0, nullptr);
    }
protected:
    void send_to_socket(const uint8_t *p, uint16_t n) override { output.append(reinterpret_cast<const char *>(p), n); }
};
static uint64_t session(const std::vector<uint8_t> &frame) {
    return be64toh(reinterpret_cast<const wblock_hdr_t *>(frame.data())->session_id);
}
static void data_regression() {
    CaptureRx rx;
    CaptureTx old(1), peer(2), restarted(1);
    for (int i=0; i<80; ++i) old.send('a');
    for (const auto &f : old.frames) rx.feed(f);
    assert(rx.output == std::string(80, 'a'));
    old.send('p'); // leave an unfinished old-session FEC block
    rx.feed(old.frames.back());
    peer.send('x'); peer.send('y');
    rx.feed(peer.frames[1]); // peer block waits for fragment zero
    RxSourceStats before = {}; assert(rx.get_source_stats(1, &before));
    restarted.send('b'); restarted.send('c');
    assert(session(old.frames[0]) != 0);
    assert(session(restarted.frames[0]) != 0);
    assert(session(old.frames[0]) != session(restarted.frames[0]));
    const auto unique_before_restart = rx.count_p_uniq.size();
    rx.feed(restarted.frames[1]); // must not combine with old-session fragment zero
    assert(rx.output == std::string(80, 'a') + "p");
    rx.feed(restarted.frames[2]); // reconstruct fragment zero using real TX parity
    assert(rx.output == std::string(80, 'a') + "pbc");
    assert(rx.count_p_uniq.size() == unique_before_restart + 2);
    rx.feed(restarted.frames[1]);
    assert(rx.count_p_uniq.size() == unique_before_restart + 2);
    auto output = rx.output;
    for (const auto &f : old.frames) rx.feed(f); // retired session cannot reactivate
    assert(rx.output == output);
    rx.feed(peer.frames[0]);
    assert(rx.output == output + "xy"); // source 2 reassembly survived source 1 restart
    RxSourceStats after = {}; assert(rx.get_source_stats(1, &after));
    assert(after.count_p_raw >= before.count_p_raw + 2);
    assert(after.count_p_outgoing == before.count_p_outgoing + 2);
    assert(after.count_p_fec_recovered == before.count_p_fec_recovered + 1);
    for (int kind=0; kind<4; ++kind) {
        auto invalid = restarted.frames[0];
        auto *h = reinterpret_cast<wblock_hdr_t *>(invalid.data());
        if (kind==0) h->magic = 0;
        if (kind==1) h->version = 0;
        if (kind==2) h->session_id = 0;
        if (kind==3) { // pre-session DATA layout: packet type followed directly by nonce
            invalid.erase(invalid.begin()+1, invalid.begin()+12);
            invalid.resize(40, 0);
        }
        auto bad = rx.count_p_bad;
        rx.feed(invalid);
        assert(rx.count_p_bad == bad+1);
        assert(rx.output == output + "xy");
    }
    std::cout << "DATA TX->RX restart, FEC isolation, retired/legacy rejection: PASS\n";
}
static std::vector<uint8_t> grant(uint64_t sid, uint64_t sequence) {
    std::vector<uint8_t> frame(sizeof(wcontrol_envelope_hdr_t)+sizeof(wcontrol_grant_payload_t), 0);
    auto *h = reinterpret_cast<wcontrol_envelope_hdr_t *>(frame.data());
    h->packet_type = WFB_PACKET_CONTROL; h->magic = htobe16(WFB_CONTROL_MAGIC);
    h->version = WFB_CONTROL_VERSION; h->control_type = WFB_CONTROL_TYPE_GRANT;
    h->source_node = 1; h->target_node = 9; h->session_id = htobe64(sid); h->sequence = htobe64(sequence);
    reinterpret_cast<wcontrol_grant_payload_t *>(frame.data()+sizeof(*h))->duration_ms = htobe32(120);
    return frame;
}
class FileEventReceiver {
public:
    explicit FileEventReceiver(const std::string &name) : name_(name), fd_(socket(AF_UNIX, SOCK_DGRAM, 0)) {
        assert(fd_ >= 0); sockaddr_un addr = {}; addr.sun_family = AF_UNIX;
        assert(name.size() < sizeof(addr.sun_path)); strcpy(addr.sun_path, name.c_str());
        assert(bind(fd_, reinterpret_cast<sockaddr *>(&addr), sizeof(addr)) == 0);
    }
    ~FileEventReceiver() { close(fd_); unlink(name_.c_str()); }
    bool recv_event(TokenAuthorizationEvent *event) {
        return recv(fd_, event, sizeof(*event), MSG_DONTWAIT) == sizeof(*event);
    }
private:
    std::string name_; int fd_;
};
static void grant_regression() {
    const std::string base = "/tmp/session-regression-" + std::to_string(getpid());
    FileEventReceiver ipc(make_token_authorization_socket_name(base));
    TokenEventDatagramListener listener(base);
    CaptureRx rx; rx.set_token_control_listener(&listener);
    TokenAuthorizationState authorization;
    for (auto identity : {std::make_pair(101ULL, 90ULL), std::make_pair(202ULL, 0ULL)}) {
        rx.feed(grant(identity.first, identity.second));
        TokenAuthorizationEvent event = {};
        assert(ipc.recv_event(&event));
        assert(event.session_id == identity.first && event.sequence == identity.second && event.node_id == 9);
        assert(authorization.apply_event(event, event.expires_at_ms - 1));
        assert(authorization.is_authorized(event.expires_at_ms-1));
    }
    rx.feed(grant(101, 91)); rx.feed(grant(202, 0));
    rx.feed(grant(202, 2));
    TokenAuthorizationEvent event = {}; assert(ipc.recv_event(&event));
    assert(event.session_id == 202 && event.sequence == 2);
    assert(authorization.apply_event(event, event.expires_at_ms - 1));
    rx.feed(grant(202, 1));
    auto invalid = grant(303, 0);
    reinterpret_cast<wcontrol_envelope_hdr_t *>(invalid.data())->version = 1;
    rx.feed(invalid); rx.feed(grant(0, 0));
    assert(!ipc.recv_event(&event));
    assert(rx.grant_filter_counters_.accepted == 3);
    assert(rx.grant_filter_counters_.ignored_retired_session == 1);
    assert(rx.grant_filter_counters_.ignored_duplicate_sequence == 1);
    assert(rx.grant_filter_counters_.ignored_stale_sequence == 1);
    event.session_id = 101; event.sequence = 99; assert(!authorization.apply_event(event, event.expires_at_ms - 1));
    event.session_id = 202; event.sequence = 1; assert(!authorization.apply_event(event, event.expires_at_ms - 1));
    event.session_id = 303; event.sequence = 0; assert(authorization.apply_event(event, event.expires_at_ms - 1));
    ControlEnvelopeView invalid_session = {}; invalid_session.source_node = 1;
    invalid_session.target_node = 9; invalid_session.grant_expires_at_ms = 1000;
    GrantFilterState filter_state = {};
    assert(filter_grant(invalid_session, true, 9, 500, &filter_state, &rx.grant_filter_counters_) ==
           GrantDecision::ignore_invalid_session);
    fflush(stdout); FILE *capture = tmpfile(); assert(capture != nullptr);
    int saved_stdout = dup(STDOUT_FILENO); assert(saved_stdout >= 0);
    assert(dup2(fileno(capture), STDOUT_FILENO) >= 0);
    rx.dump_stats(); fflush(stdout);
    assert(dup2(saved_stdout, STDOUT_FILENO) >= 0); close(saved_stdout);
    rewind(capture); std::string telemetry; char buffer[512];
    while (fgets(buffer, sizeof(buffer), capture)) telemetry += buffer;
    fclose(capture);
    assert(telemetry.find("\tGRANT_FILTER\t7:3:0:0:1:1:0:1:1\n") != std::string::npos);
    std::cout << "GRANT RX->IPC->authorization restart, replay rejection and session telemetry: PASS\n";
}
static void standalone_regression() {
    const std::string base = "standalone-session-" + std::to_string(getpid());
    TokenAuthorizationDatagramReceiver receiver(make_token_authorization_socket_name(base));
    TokenSchedulerConfig config = {}; config.socket_path = base;
    TokenGrantDispatcher old(config), restarted(config);
    TokenGrant grant = {}; grant.node_id = 9; grant.duration_ms = 120; grant.sequence = 90;
    TokenAuthorizationEvent event = {}; TokenAuthorizationState authorization;
    assert(old.send_grant(grant, 100)); assert(receiver.recv_event(&event));
    const uint64_t old_id = event.session_id;
    assert(old_id != 0 && event.sequence == 90 && event.duration_ms == 120);
    assert(authorization.apply_event(event, event.expires_at_ms - 1));
    grant.sequence = 0;
    assert(restarted.send_grant(grant, 200)); assert(receiver.recv_event(&event));
    assert(event.session_id != 0 && event.session_id != old_id && event.sequence == 0);
    assert(authorization.apply_event(event, event.expires_at_ms - 1));
    grant.sequence = 91;
    assert(old.send_grant(grant, 300)); assert(receiver.recv_event(&event));
    assert(!authorization.apply_event(event, event.expires_at_ms - 1));
    TokenAuthorizationDatagramReceiver ready_receiver(make_token_ready_socket_name(base));
    ReadyDeclarationState ready = {}; ready.node_id = 9;
    ready.sender.reset(new TokenAuthorizationDatagramSender(make_token_ready_socket_name(base)));
    TokenAuthorizationState denied;
    CaptureTx tx(9); const uint8_t value = 'q';
    assert(!process_data_packet(tx, &value, 1, 100, &denied, &ready));
    assert(ready_receiver.recv_event(&event));
    assert(event.node_id == 9 && event.session_id == tx.session_id() && event.session_id != 0);
    std::cout << "Standalone scheduler GRANT and TX READY session propagation: PASS\n";
}
static void expired_session_regression() {
    TokenAuthorizationState authorization;
    TokenAuthorizationEvent event = {};
    event.session_id = 100; event.sequence = 8; event.expires_at_ms = 1000;
    assert(authorization.apply_event(event, 500));
    event.session_id = 200; event.sequence = 0; event.expires_at_ms = 500;
    assert(!authorization.apply_event(event, 500)); // expiry boundary rejects before switching session
    assert(authorization.is_authorized(500));
    event.session_id = 100; event.sequence = 9; event.expires_at_ms = 1000;
    assert(authorization.apply_event(event, 500)); // current session was not retired
    event.session_id = 200; event.sequence = 0; event.expires_at_ms = 499;
    assert(!authorization.apply_event(event, 500));
    event.session_id = 100; event.sequence = 10; event.expires_at_ms = 1000;
    assert(authorization.apply_event(event, 500));
    std::cout << "Expired novel session preserves current authorization: PASS\n";
}
int main() {
    assert(sodium_init() >= 0);
    data_regression(); grant_regression(); standalone_regression();
    expired_session_regression();
}
