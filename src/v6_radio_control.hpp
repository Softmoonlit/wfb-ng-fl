#pragma once

#include <cstddef>
#include <cstdint>
#include <functional>
#include <vector>

#include "tx_cmd.h"
#include "wifibroadcast.hpp"

// v6 在线无线状态。FEC 不属于可变状态：v6 接收端和受信任明文发送端
// 必须继续使用进程启动时确定的 FEC 参数。
struct V6RadioConfig {
    uint8_t bandwidth = 20;
    uint8_t mcs_index = 0;
    bool short_gi = false;
};

struct V6RadioTarget {
    V6RadioConfig config;
    std::vector<uint8_t> data_radiotap;
    std::vector<uint8_t> control_radiotap;
};

struct V6RadioCommandResponse {
    cmd_resp_t response;
    size_t payload_size;
};

// 从同一目标配置生成两条独立 TX 路径的描述。两个字节向量必须相同，
// 以保证控制帧和数据帧使用一致的 HT/GI/MCS。
V6RadioTarget build_v6_radio_target(const V6RadioConfig &config);

// 校验完整的 tx_cmd CMD_SET_RADIO 请求，保持当前 v6 不可变字段不变。
// 请求只允许修改 HT20/HT40 带宽和 MCS。
bool validate_v6_radio_request(const cmd_req_t &request,
                               const V6RadioConfig &current,
                               V6RadioConfig *target);

V6RadioCommandResponse handle_v6_radio_command(
    const cmd_req_t &request,
    size_t request_size,
    const V6RadioConfig &current,
    int fec_k,
    int fec_n,
    const std::function<void(const V6RadioConfig &)> &apply_radio,
    const std::function<uint32_t(uint64_t)> &rearm_uplink = {});