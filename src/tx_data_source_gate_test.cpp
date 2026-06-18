#include <catch2/catch_session.hpp>
#include <catch2/catch_test_macros.hpp>

#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <cstdint>
#include <cstring>
#include <functional>
#include <memory>
#include <sstream>
#include <string>
#include <vector>

#define __WFB_TX_SHARED_LIBRARY__
#include "tx.cpp"

namespace {

std::vector<tags_item_t> &empty_tags()
{
    static std::vector<tags_item_t> tags;
    return tags;
}

class ScopedFd {
public:
    explicit ScopedFd(int fd = -1) : fd_(fd) {}

    ~ScopedFd()
    {
        if (fd_ >= 0)
        {
            close(fd_);
        }
    }

    int get() const { return fd_; }

private:
    int fd_;
};

class ScopedAbstractDatagramSender {
public:
    explicit ScopedAbstractDatagramSender(const std::string &path) : fd_(socket(AF_UNIX, SOCK_DGRAM, 0))
    {
        REQUIRE(fd_.get() >= 0);
        addr_ = {};
        addr_.sun_family = AF_UNIX;
        REQUIRE(path.size() + 1 < sizeof(addr_.sun_path));
        std::strncpy(addr_.sun_path + 1, path.c_str(), sizeof(addr_.sun_path) - 2);
    }

    void send_event(const TokenAuthorizationEvent &event)
    {
        const ssize_t sent = sendto(fd_.get(),
                                    &event,
                                    sizeof(event),
                                    0,
                                    reinterpret_cast<const sockaddr *>(&addr_),
                                    sizeof(sa_family_t) + std::strlen(addr_.sun_path + 1) + 1);
        REQUIRE(sent == static_cast<ssize_t>(sizeof(event)));
    }

private:
    ScopedFd fd_;
    sockaddr_un addr_;
};

class RecordingTransmitter : public Transmitter {
public:
    RecordingTransmitter() : Transmitter(1, 1, WFB_TRUSTED_PLAINTEXT_KEYPAIR, 1, 1, 0, empty_tags(), true) {}
    void select_output(int idx) override
    {
        selected_outputs.push_back(idx);
    }

    void dump_stats(uint64_t, uint32_t &, uint32_t &, uint32_t &) override {}

    void update_radiotap_header(radiotap_header_t &radiotap_header) override
    {
        radiotap_header_ = radiotap_header;
    }

    radiotap_header_t get_radiotap_header(void) override
    {
        return radiotap_header_;
    }

    int inject_count() const
    {
        return static_cast<int>(injected_payloads.size());
    }

    std::vector<std::vector<uint8_t>> injected_payloads;
    std::vector<int> selected_outputs;

private:
    void inject_packet(const uint8_t *buf, size_t size) override
    {
        injected_payloads.emplace_back(buf, buf + size);
    }

    void set_mark(uint32_t) override {}

    radiotap_header_t radiotap_header_ = {};
};

std::string format_authorization_counters_for_test(const TokenAuthorizationState *authorization_state)
{
    std::ostringstream out;
    append_token_authorization_stats(out, authorization_state);
    return out.str();
}

std::string capture_stdout_for_test(const std::function<void()> &fn)
{
    int pipe_fds[2] = {-1, -1};
    REQUIRE(pipe(pipe_fds) == 0);

    ScopedFd read_end(pipe_fds[0]);
    ScopedFd write_end(pipe_fds[1]);
    const int stdout_copy = dup(STDOUT_FILENO);
    REQUIRE(stdout_copy >= 0);
    ScopedFd stdout_guard(stdout_copy);

    fflush(stdout);
    REQUIRE(dup2(write_end.get(), STDOUT_FILENO) >= 0);

    fn();

    fflush(stdout);
    REQUIRE(dup2(stdout_guard.get(), STDOUT_FILENO) >= 0);

    close(pipe_fds[1]);

    std::string captured;
    char buffer[512];
    for (;;)
    {
        const ssize_t count = read(read_end.get(), buffer, sizeof(buffer));
        REQUIRE(count >= 0);
        if (count == 0)
        {
            break;
        }
        captured.append(buffer, static_cast<size_t>(count));
    }

    return captured;
}

void send_authorization_event(const std::string &socket_name, const TokenAuthorizationEvent &event)
{
    ScopedAbstractDatagramSender sender(socket_name);
    sender.send_event(event);
}

}

TEST_CASE("drain_authorization_events 按顺序吸收多条授权事件")
{
    const std::string base_socket = "wfb-tx-gate-drain-events";
    const std::string auth_socket = make_token_authorization_socket_name(base_socket);
    TokenAuthorizationDatagramReceiver receiver(auth_socket);
    TokenAuthorizationState auth_state;

    TokenAuthorizationEvent first = {};
    first.node_id = 7;
    first.sequence = 41;
    first.duration_ms = 100;
    first.expires_at_ms = 1100;

    TokenAuthorizationEvent second = {};
    second.node_id = 7;
    second.sequence = 42;
    second.duration_ms = 200;
    second.expires_at_ms = 1500;

    send_authorization_event(auth_socket, first);
    send_authorization_event(auth_socket, second);

    const std::string captured = capture_stdout_for_test([&]() {
        drain_authorization_events(&receiver, &auth_state);
    });

    REQUIRE(auth_state.counters().accepted_events == 2);
    REQUIRE(auth_state.counters().rejected_events == 0);
    REQUIRE(auth_state.is_authorized(1499));
    REQUIRE_FALSE(auth_state.is_authorized(1500));
    REQUIRE(captured.find("token_received node_id=7 expires_at_ms=1100") != std::string::npos);
    REQUIRE(captured.find("token_received node_id=7 expires_at_ms=1500") != std::string::npos);
}

TEST_CASE("drain_authorization_events 忽略空指针输入")
{
    TokenAuthorizationState auth_state;
    drain_authorization_events(nullptr, &auth_state);
    REQUIRE(auth_state.counters().accepted_events == 0);
    REQUIRE(auth_state.counters().rejected_events == 0);

    const std::string base_socket = "wfb-tx-gate-drain-null-state";
    const std::string auth_socket = make_token_authorization_socket_name(base_socket);
    TokenAuthorizationDatagramReceiver receiver(auth_socket);
    drain_authorization_events(&receiver, nullptr);
}

TEST_CASE("process_data_packet 在未授权时不调用发送注入")
{
    std::unique_ptr<Transmitter> transmitter(new RecordingTransmitter());
    TokenAuthorizationState auth_state;
    const std::vector<uint8_t> payload = {0x10, 0x20, 0x30, 0x40};

    REQUIRE_FALSE(process_data_packet(*transmitter, payload.data(), payload.size(), 1000, &auth_state));

    RecordingTransmitter *recording = static_cast<RecordingTransmitter *>(transmitter.get());
    REQUIRE(recording->inject_count() == 0);
    REQUIRE(auth_state.counters().authorized_sends == 0);
    REQUIRE(auth_state.counters().denied_sends == 1);
}

TEST_CASE("process_data_packet 在首次未授权时发送 ready 声明")
{
    const std::string ready_socket = make_token_ready_socket_name(kDefaultTokenReadySocketBase);
    TokenAuthorizationDatagramReceiver receiver(ready_socket);
    std::unique_ptr<Transmitter> transmitter(new RecordingTransmitter());
    TokenAuthorizationState auth_state;
    ReadyDeclarationState ready_state = {};
    ready_state.node_id = 7;
    ready_state.declaration_in_flight = false;
    ready_state.last_declare_at_ms = 0;
    ready_state.redeclare_timeout_ms = 4000;
    ready_state.phase = ReadyDeclarationState::UNDECLARED;
    ready_state.sender.reset(new TokenAuthorizationDatagramSender(ready_socket));
    const std::vector<uint8_t> payload = {0x10, 0x20, 0x30, 0x40};

    const std::string captured = capture_stdout_for_test([&]() {
        REQUIRE_FALSE(process_data_packet(*transmitter,
                                          payload.data(),
                                          payload.size(),
                                          1000,
                                          &auth_state,
                                          &ready_state));
    });

    TokenAuthorizationEvent event = {};
    REQUIRE(receiver.recv_event(&event));
    REQUIRE(event.node_id == 7);
    REQUIRE(ready_state.declaration_in_flight);
    REQUIRE(captured.find("first_declare node_id=7 state=undeclared->declared_waiting_for_token at_ms=1000") != std::string::npos);
}

TEST_CASE("process_data_packet 在 ready 已 in-flight 时不重复发送声明")
{
    const std::string ready_socket = make_token_ready_socket_name(kDefaultTokenReadySocketBase);
    TokenAuthorizationDatagramReceiver receiver(ready_socket);
    std::unique_ptr<Transmitter> transmitter(new RecordingTransmitter());
    TokenAuthorizationState auth_state;
    ReadyDeclarationState ready_state = {};
    ready_state.node_id = 7;
    ready_state.declaration_in_flight = false;
    ready_state.last_declare_at_ms = 0;
    ready_state.redeclare_timeout_ms = 4000;
    ready_state.phase = ReadyDeclarationState::UNDECLARED;
    ready_state.sender.reset(new TokenAuthorizationDatagramSender(ready_socket));
    const std::vector<uint8_t> payload = {0x99};

    REQUIRE_FALSE(process_data_packet(*transmitter, payload.data(), payload.size(), 1000, &auth_state, &ready_state));
    REQUIRE(ready_state.declaration_in_flight);
    REQUIRE_FALSE(process_data_packet(*transmitter, payload.data(), payload.size(), 1001, &auth_state, &ready_state));

    TokenAuthorizationEvent event = {};
    REQUIRE(receiver.recv_event(&event));
    REQUIRE(event.node_id == 7);
    REQUIRE_FALSE(receiver.recv_event(&event));
}

TEST_CASE("process_data_packet 在长时间无 Token 时重发 ready 声明但不会短时间连发")
{
    const std::string ready_socket = make_token_ready_socket_name(kDefaultTokenReadySocketBase);
    TokenAuthorizationDatagramReceiver receiver(ready_socket);
    std::unique_ptr<Transmitter> transmitter(new RecordingTransmitter());
    TokenAuthorizationState auth_state;
    ReadyDeclarationState ready_state = {};
    ready_state.node_id = 7;
    ready_state.declaration_in_flight = false;
    ready_state.last_declare_at_ms = 0;
    ready_state.redeclare_timeout_ms = 4000;
    ready_state.phase = ReadyDeclarationState::UNDECLARED;
    ready_state.sender.reset(new TokenAuthorizationDatagramSender(ready_socket));
    const std::vector<uint8_t> payload = {0x42};

    const std::string captured = capture_stdout_for_test([&]() {
        REQUIRE_FALSE(process_data_packet(*transmitter, payload.data(), payload.size(), 1000, &auth_state, &ready_state));
        REQUIRE_FALSE(process_data_packet(*transmitter, payload.data(), payload.size(), 1001, &auth_state, &ready_state));
        REQUIRE_FALSE(process_data_packet(*transmitter, payload.data(), payload.size(), 5001, &auth_state, &ready_state));
    });

    TokenAuthorizationEvent first_event = {};
    TokenAuthorizationEvent second_event = {};
    REQUIRE(receiver.recv_event(&first_event));
    REQUIRE(receiver.recv_event(&second_event));
    REQUIRE(first_event.node_id == 7);
    REQUIRE(second_event.node_id == 7);
    REQUIRE_FALSE(receiver.recv_event(&second_event));
    REQUIRE(captured.find("first_declare node_id=7 state=undeclared->declared_waiting_for_token at_ms=1000") != std::string::npos);
    REQUIRE(captured.find("redeclare node_id=7 state=declared_waiting_for_token->declared_waiting_for_token at_ms=5001") != std::string::npos);
}

TEST_CASE("process_data_packet 在授权事件到达后调用发送注入")
{
    std::unique_ptr<Transmitter> transmitter(new RecordingTransmitter());
    TokenAuthorizationState auth_state;
    const std::vector<uint8_t> payload = {0x55, 0x66, 0x77};

    TokenAuthorizationEvent event = {};
    event.node_id = 7;
    event.sequence = 42;
    event.duration_ms = 200;
    event.expires_at_ms = 1200;
    auth_state.apply_event(event);

    REQUIRE(process_data_packet(*transmitter, payload.data(), payload.size(), 1000, &auth_state));

    RecordingTransmitter *recording = static_cast<RecordingTransmitter *>(transmitter.get());
    REQUIRE(recording->inject_count() == 1);
    REQUIRE_FALSE(recording->injected_payloads.empty());
    REQUIRE_FALSE(recording->injected_payloads.front().empty());
    REQUIRE(auth_state.counters().accepted_events == 1);
    REQUIRE(auth_state.counters().authorized_sends == 1);
    REQUIRE(auth_state.counters().denied_sends == 0);
}

TEST_CASE("process_data_packet 在授权过期后停止发送注入")
{
    std::unique_ptr<Transmitter> transmitter(new RecordingTransmitter());
    TokenAuthorizationState auth_state;
    const std::vector<uint8_t> payload = {0xaa, 0xbb};

    TokenAuthorizationEvent event = {};
    event.node_id = 7;
    event.sequence = 43;
    event.duration_ms = 200;
    event.expires_at_ms = 1100;
    auth_state.apply_event(event);

    REQUIRE_FALSE(process_data_packet(*transmitter, payload.data(), payload.size(), 1200, &auth_state));

    RecordingTransmitter *recording = static_cast<RecordingTransmitter *>(transmitter.get());
    REQUIRE(recording->inject_count() == 0);
    REQUIRE(auth_state.counters().authorized_sends == 0);
    REQUIRE(auth_state.counters().denied_sends == 1);
}

TEST_CASE("process_data_packet 在双客户端场景下仅 token holder 可发送")
{
    std::unique_ptr<Transmitter> first_transmitter(new RecordingTransmitter());
    std::unique_ptr<Transmitter> second_transmitter(new RecordingTransmitter());
    TokenAuthorizationState first_auth_state;
    TokenAuthorizationState second_auth_state;
    const std::vector<uint8_t> first_payload = {0x01, 0x02, 0x03};
    const std::vector<uint8_t> second_payload = {0x04, 0x05, 0x06};

    TokenAuthorizationEvent first_event = {};
    first_event.node_id = 7;
    first_event.sequence = 42;
    first_event.duration_ms = 200;
    first_event.expires_at_ms = 1200;
    first_auth_state.apply_event(first_event);

    REQUIRE(process_data_packet(*first_transmitter,
                                first_payload.data(),
                                first_payload.size(),
                                1000,
                                &first_auth_state));
    REQUIRE_FALSE(process_data_packet(*second_transmitter,
                                      second_payload.data(),
                                      second_payload.size(),
                                      1000,
                                      &second_auth_state));

    RecordingTransmitter *first_recording = static_cast<RecordingTransmitter *>(first_transmitter.get());
    RecordingTransmitter *second_recording = static_cast<RecordingTransmitter *>(second_transmitter.get());

    REQUIRE(first_recording->inject_count() == 1);
    REQUIRE(second_recording->inject_count() == 0);
    REQUIRE(first_auth_state.counters().authorized_sends == 1);
    REQUIRE(first_auth_state.counters().denied_sends == 0);
    REQUIRE(second_auth_state.counters().authorized_sends == 0);
    REQUIRE(second_auth_state.counters().denied_sends == 1);
}

TEST_CASE("append_token_authorization_stats 输出 sender Token counters")
{
    TokenAuthorizationState auth_state;

    TokenAuthorizationEvent accepted = {};
    accepted.node_id = 7;
    accepted.sequence = 42;
    accepted.duration_ms = 200;
    accepted.expires_at_ms = 1200;
    auth_state.apply_event(accepted);

    TokenAuthorizationEvent rejected = {};
    rejected.node_id = 7;
    rejected.sequence = 43;
    rejected.duration_ms = 200;
    rejected.expires_at_ms = 0;
    auth_state.apply_event(rejected);

    REQUIRE_FALSE(process_data_packet(*std::unique_ptr<Transmitter>(new RecordingTransmitter()), nullptr, 0, 1300, &auth_state));

    std::unique_ptr<Transmitter> transmitter(new RecordingTransmitter());
    const std::vector<uint8_t> payload = {0x01};
    REQUIRE(process_data_packet(*transmitter, payload.data(), payload.size(), 1000, &auth_state));

    const std::string text = format_authorization_counters_for_test(&auth_state);
    REQUIRE(text.find("TOKEN_AUTH\t1:1:1:1") != std::string::npos);
}

int main(int argc, char *argv[])
{
    Catch::Session session;
    return session.run(argc, argv);
}
