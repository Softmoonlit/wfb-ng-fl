#include "ikcp.h"

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <openssl/sha.h>
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
constexpr size_t kMaxPayloadSize = 65536;

struct ReceiverContext {
    int sock = -1;
    sockaddr_in peer{};
    socklen_t peer_len = sizeof(peer);
    bool has_peer = false;
};

IUINT32 clock_ms() {
    auto now = std::chrono::steady_clock::now();
    return static_cast<IUINT32>(std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()).count());
}

int kcp_output(const char* buf, int len, ikcpcb*, void* user) {
    auto* ctx = static_cast<ReceiverContext*>(user);
    if (!ctx->has_peer) {
        return -1;
    }
    ssize_t sent = sendto(ctx->sock, buf, len, 0, reinterpret_cast<sockaddr*>(&ctx->peer), ctx->peer_len);
    return sent == len ? 0 : -1;
}

bool set_nonblocking(int fd) {
    int flags = fcntl(fd, F_GETFL, 0);
    if (flags < 0) {
        return false;
    }
    return fcntl(fd, F_SETFL, flags | O_NONBLOCK) == 0;
}

uint64_t read_u64(const std::vector<char>& data) {
    uint64_t value = 0;
    for (size_t i = 0; i < 8; ++i) {
        value = (value << 8) | static_cast<unsigned char>(data[i]);
    }
    return value;
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

bool write_file(const std::string& path, const std::vector<char>& data) {
    std::ofstream out(path, std::ios::binary);
    if (!out) {
        return false;
    }
    if (!data.empty()) {
        out.write(data.data(), static_cast<std::streamsize>(data.size()));
    }
    return static_cast<bool>(out);
}

void usage(const char* argv0) {
    std::cerr << "用法: " << argv0 << " --port <port> --output <file> [--timeout-ms <ms>]\n";
}

} // namespace

int main(int argc, char** argv) {
    std::string output;
    int port = 5600;
    int timeout_ms = 30000;

    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--port" && i + 1 < argc) {
            port = std::stoi(argv[++i]);
        } else if (arg == "--output" && i + 1 < argc) {
            output = argv[++i];
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

    if (output.empty() || port <= 0 || port > 65535 || timeout_ms <= 0) {
        usage(argv[0]);
        return 1;
    }

    ReceiverContext ctx;
    ctx.sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (ctx.sock < 0) {
        perror("socket");
        return 1;
    }

    sockaddr_in local{};
    local.sin_family = AF_INET;
    local.sin_addr.s_addr = htonl(INADDR_ANY);
    local.sin_port = htons(static_cast<uint16_t>(port));
    if (bind(ctx.sock, reinterpret_cast<sockaddr*>(&local), sizeof(local)) < 0) {
        perror("bind");
        close(ctx.sock);
        return 1;
    }

    if (!set_nonblocking(ctx.sock)) {
        perror("fcntl");
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

    std::vector<char> payload;
    char udpbuf[1500];
    char appbuf[2048];
    IUINT32 start = clock_ms();
    bool done = false;

    std::cout << "listening_port=" << port << "\n";

    while (clock_ms() - start < static_cast<IUINT32>(timeout_ms)) {
        while (true) {
            sockaddr_in from{};
            socklen_t from_len = sizeof(from);
            ssize_t n = recvfrom(ctx.sock, udpbuf, sizeof(udpbuf), 0, reinterpret_cast<sockaddr*>(&from), &from_len);
            if (n < 0) {
                if (errno == EAGAIN || errno == EWOULDBLOCK) {
                    break;
                }
                perror("recvfrom");
                ikcp_release(kcp);
                close(ctx.sock);
                return 1;
            }
            ctx.peer = from;
            ctx.peer_len = from_len;
            ctx.has_peer = true;
            ikcp_input(kcp, udpbuf, static_cast<long>(n));
        }

        IUINT32 now = clock_ms();
        ikcp_update(kcp, now);

        while (true) {
            int n = ikcp_recv(kcp, appbuf, sizeof(appbuf));
            if (n < 0) {
                break;
            }
            payload.insert(payload.end(), appbuf, appbuf + n);
            if (payload.size() >= 8) {
                uint64_t expected = read_u64(payload);
                if (expected <= kMaxPayloadSize && payload.size() >= 8 + expected) {
                    std::vector<char> file_data(payload.begin() + 8, payload.begin() + 8 + static_cast<std::ptrdiff_t>(expected));
                    if (!write_file(output, file_data)) {
                        std::cerr << "[FAIL] 无法写入输出文件: " << output << "\n";
                        ikcp_release(kcp);
                        close(ctx.sock);
                        return 1;
                    }
                    ikcp_send(kcp, "ACK", 3);
                    for (int i = 0; i < 20; ++i) {
                        ikcp_update(kcp, clock_ms());
                        usleep(10000);
                    }
                    std::string sha = sha256_file(output);
                    std::cout << "output=" << output << "\n";
                    std::cout << "bytes=" << file_data.size() << "\n";
                    std::cout << "sha256=" << sha << "\n";
                    std::cout << "[PASS] KCP 小文件接收完成\n";
                    done = true;
                    break;
                }
            }
        }

        if (done) {
            break;
        }
        usleep(10000);
    }

    ikcp_release(kcp);
    close(ctx.sock);

    if (!done) {
        std::cerr << "[FAIL] KCP 小文件接收超时\n";
        return 1;
    }
    return 0;
}
