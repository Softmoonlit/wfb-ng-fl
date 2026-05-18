#include "ikcp.h"

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <openssl/sha.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <unistd.h>

#include <chrono>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

namespace {

constexpr IUINT32 kConv = 0x11223344;
constexpr size_t kChunkSize = 1024;
constexpr size_t kMaxPayloadSize = 65536;

struct SenderContext {
    int sock = -1;
    sockaddr_in peer{};
};

IUINT32 clock_ms() {
    auto now = std::chrono::steady_clock::now();
    return static_cast<IUINT32>(std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()).count());
}

int kcp_output(const char* buf, int len, ikcpcb*, void* user) {
    auto* ctx = static_cast<SenderContext*>(user);
    ssize_t sent = sendto(ctx->sock, buf, len, 0, reinterpret_cast<sockaddr*>(&ctx->peer), sizeof(ctx->peer));
    return sent == len ? 0 : -1;
}

bool set_nonblocking(int fd) {
    int flags = fcntl(fd, F_GETFL, 0);
    if (flags < 0) {
        return false;
    }
    return fcntl(fd, F_SETFL, flags | O_NONBLOCK) == 0;
}

std::string sha256_file(const std::string& path) {
    std::ifstream file(path, std::ios::binary);
    if (!file) {
        return "";
    }

    SHA256_CTX ctx;
    SHA256_Init(&ctx);

    char buffer[8192];
    while (file.read(buffer, sizeof(buffer)) || file.gcount() > 0) {
        SHA256_Update(&ctx, buffer, static_cast<size_t>(file.gcount()));
    }

    unsigned char digest[SHA256_DIGEST_LENGTH];
    SHA256_Final(digest, &ctx);

    std::ostringstream out;
    for (unsigned char byte : digest) {
        out << std::hex << std::setw(2) << std::setfill('0') << static_cast<int>(byte);
    }
    return out.str();
}

bool read_file(const std::string& path, std::vector<char>& data) {
    std::ifstream file(path, std::ios::binary | std::ios::ate);
    if (!file) {
        return false;
    }

    std::streamsize size = file.tellg();
    if (size < 0 || static_cast<size_t>(size) > kMaxPayloadSize) {
        return false;
    }

    file.seekg(0, std::ios::beg);
    data.resize(static_cast<size_t>(size));
    if (!data.empty() && !file.read(data.data(), size)) {
        return false;
    }
    return true;
}

void put_u64(std::vector<char>& out, uint64_t value) {
    for (int i = 7; i >= 0; --i) {
        out.push_back(static_cast<char>((value >> (i * 8)) & 0xff));
    }
}

std::vector<char> build_payload(const std::vector<char>& file_data) {
    std::vector<char> payload;
    payload.reserve(8 + file_data.size());
    put_u64(payload, file_data.size());
    payload.insert(payload.end(), file_data.begin(), file_data.end());
    return payload;
}

void usage(const char* argv0) {
    std::cerr << "用法: " << argv0 << " --host <ip> --port <port> --input <file> [--timeout-ms <ms>]\n";
}

} // namespace

int main(int argc, char** argv) {
    std::string host = "127.0.0.1";
    std::string input;
    int port = 5602;
    int timeout_ms = 30000;

    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--host" && i + 1 < argc) {
            host = argv[++i];
        } else if (arg == "--port" && i + 1 < argc) {
            port = std::stoi(argv[++i]);
        } else if (arg == "--input" && i + 1 < argc) {
            input = argv[++i];
        } else if (arg == "--timeout-ms" && i + 1 < argc) {
            timeout_ms = std::stoi(argv[++i]);
        } else if (arg == "--help" || arg == "-h") {
            usage(argv[0]);
            return 0;
        } else {
            usage(argv[0]);
            return 1;
        }
    }

    if (input.empty() || port <= 0 || port > 65535 || timeout_ms <= 0) {
        usage(argv[0]);
        return 1;
    }

    std::vector<char> file_data;
    if (!read_file(input, file_data)) {
        std::cerr << "[FAIL] 无法读取输入文件，或文件超过 " << kMaxPayloadSize << " 字节: " << input << "\n";
        return 1;
    }

    SenderContext ctx;
    ctx.sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (ctx.sock < 0) {
        perror("socket");
        return 1;
    }
    if (!set_nonblocking(ctx.sock)) {
        perror("fcntl");
        close(ctx.sock);
        return 1;
    }

    memset(&ctx.peer, 0, sizeof(ctx.peer));
    ctx.peer.sin_family = AF_INET;
    ctx.peer.sin_port = htons(static_cast<uint16_t>(port));
    if (inet_pton(AF_INET, host.c_str(), &ctx.peer.sin_addr) != 1) {
        std::cerr << "[FAIL] 非法 host: " << host << "\n";
        close(ctx.sock);
        return 1;
    }

    ikcpcb* kcp = ikcp_create(kConv, &ctx);
    if (!kcp) {
        close(ctx.sock);
        return 1;
    }
    kcp->output = kcp_output;
    ikcp_nodelay(kcp, 1, 10, 2, 1);
    ikcp_setmtu(kcp, 1200);
    ikcp_wndsize(kcp, 128, 128);

    std::vector<char> payload = build_payload(file_data);
    size_t offset = 0;
    bool sent_all = false;
    bool acked = false;
    IUINT32 start = clock_ms();
    IUINT32 last_send = 0;
    char recvbuf[1500];

    while (clock_ms() - start < static_cast<IUINT32>(timeout_ms)) {
        IUINT32 now = clock_ms();
        if (!sent_all && now - last_send >= 10) {
            while (offset < payload.size()) {
                size_t len = std::min(kChunkSize, payload.size() - offset);
                if (ikcp_send(kcp, payload.data() + offset, static_cast<int>(len)) < 0) {
                    break;
                }
                offset += len;
            }
            sent_all = offset == payload.size();
            last_send = now;
        }

        while (true) {
            sockaddr_in from{};
            socklen_t from_len = sizeof(from);
            ssize_t n = recvfrom(ctx.sock, recvbuf, sizeof(recvbuf), 0, reinterpret_cast<sockaddr*>(&from), &from_len);
            if (n < 0) {
                if (errno == EAGAIN || errno == EWOULDBLOCK) {
                    break;
                }
                perror("recvfrom");
                ikcp_release(kcp);
                close(ctx.sock);
                return 1;
            }
            ikcp_input(kcp, recvbuf, static_cast<long>(n));
        }

        ikcp_update(kcp, now);

        char appbuf[64];
        int n = ikcp_recv(kcp, appbuf, sizeof(appbuf));
        if (n == 3 && std::memcmp(appbuf, "ACK", 3) == 0) {
            acked = true;
            break;
        }

        usleep(10000);
    }

    std::string sha = sha256_file(input);
    std::cout << "bytes=" << file_data.size() << "\n";
    std::cout << "sha256=" << sha << "\n";

    ikcp_release(kcp);
    close(ctx.sock);

    if (!acked) {
        std::cerr << "[FAIL] KCP 小文件发送未收到 ACK\n";
        return 1;
    }

    std::cout << "[PASS] KCP 小文件发送完成\n";
    return 0;
}
