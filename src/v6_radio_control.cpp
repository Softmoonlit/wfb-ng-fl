#include <stdio.h>
#include <unistd.h>
#include <poll.h>
#include <time.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <net/if.h>
#include <linux/if_packet.h>
#include <assert.h>
#include <arpa/inet.h>

#include <memory>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

#include "zfex.h"

using namespace std;

#include "wifibroadcast.hpp"
#include "tx.hpp"
#include "v6_radio_control.hpp"

namespace {

void validate_v6_radio_config(const V6RadioConfig &config)
{
    if (config.bandwidth != 20 && config.bandwidth != 40)
    {
        throw std::invalid_argument("v6 radio bandwidth must be HT20 or HT40");
    }
    if (config.mcs_index > 31)
    {
        throw std::invalid_argument("v6 radio MCS must be in 0..31");
    }
}

} // 命名空间

V6RadioTarget build_v6_radio_target(const V6RadioConfig &config)
{
    validate_v6_radio_config(config);

    V6RadioTarget target = {};
    target.config = config;
    radiotap_header_t target_radiotap = init_radiotap_header(0,
                                                               false,
                                                               config.short_gi,
                                                               config.bandwidth,
                                                               config.mcs_index,
                                                               false,
                                                               1);
    target.data_radiotap = target_radiotap.header;
    target.control_radiotap = target.data_radiotap;
    target.control_radiotap[MCS_IDX_OFF] = V6_CONTROL_MCS_INDEX;
    return target;
}

bool validate_v6_radio_request(const cmd_req_t &request,
                               const V6RadioConfig &current,
                               V6RadioConfig *target)
{
    if (request.cmd_id != CMD_SET_RADIO || target == nullptr)
    {
        return false;
    }

    // v6 沿用 tx_cmd 的完整请求体，只允许修改 HT 带宽和 MCS，
    // 其他物理层字段必须保持固定。
    if (request.u.cmd_set_radio.stbc != 0 ||
        request.u.cmd_set_radio.ldpc ||
        request.u.cmd_set_radio.short_gi != current.short_gi ||
        request.u.cmd_set_radio.vht_mode ||
        request.u.cmd_set_radio.vht_nss != 1)
    {
        return false;
    }

    V6RadioConfig candidate = {};
    candidate.bandwidth = request.u.cmd_set_radio.bandwidth;
    candidate.mcs_index = request.u.cmd_set_radio.mcs_index;
    candidate.short_gi = current.short_gi;
    try
    {
        (void)build_v6_radio_target(candidate);
    }
    catch (const std::exception &)
    {
        return false;
    }

    *target = candidate;
    return true;
}

V6RadioCommandResponse handle_v6_radio_command(
    const cmd_req_t &request,
    size_t request_size,
    const V6RadioConfig &current,
    int fec_k,
    int fec_n,
    const function<void(const V6RadioConfig &)> &apply_radio,
    const function<uint32_t(uint64_t)> &rearm_uplink)
{
    V6RadioCommandResponse result = {};
    result.response.req_id = request.req_id;
    result.response.rc = 0;

    switch (request.cmd_id)
    {
    case CMD_REARM_UPLINK:
        if (request_size != offsetof(cmd_req_t, u) + sizeof(request.u.cmd_rearm_uplink) ||
            be64toh(request.u.cmd_rearm_uplink.epoch) == 0)
        {
            result.response.rc = htonl(EINVAL);
            break;
        }
        // server 不提供客户端 READY 重武装入口。
        result.response.rc = htonl(rearm_uplink ?
            rearm_uplink(be64toh(request.u.cmd_rearm_uplink.epoch)) : ENOTSUP);
        break;

    case CMD_SET_RADIO:
    {
        if (request_size != offsetof(cmd_req_t, u) + sizeof(request.u.cmd_set_radio))
        {
            result.response.rc = htonl(EINVAL);
            break;
        }

        V6RadioConfig target = {};
        if (!validate_v6_radio_request(request, current, &target))
        {
            result.response.rc = htonl(EINVAL);
            break;
        }
        try
        {
            apply_radio(target);
        }
        catch (const exception &error)
        {
            WFB_ERR("Rejecting v6 radio update: %s\n", error.what());
            result.response.rc = htonl(EINVAL);
        }
        break;
    }

    case CMD_GET_RADIO:
        if (request_size != offsetof(cmd_req_t, u))
        {
            result.response.rc = htonl(EINVAL);
            break;
        }
        result.response.u.cmd_get_radio.stbc = 0;
        result.response.u.cmd_get_radio.ldpc = false;
        result.response.u.cmd_get_radio.short_gi = current.short_gi;
        result.response.u.cmd_get_radio.bandwidth = current.bandwidth;
        result.response.u.cmd_get_radio.mcs_index = current.mcs_index;
        result.response.u.cmd_get_radio.vht_mode = false;
        result.response.u.cmd_get_radio.vht_nss = 1;
        result.payload_size = sizeof(result.response.u.cmd_get_radio);
        break;

    case CMD_GET_FEC:
        if (request_size != offsetof(cmd_req_t, u))
        {
            result.response.rc = htonl(EINVAL);
            break;
        }
        result.response.u.cmd_get_fec.k = static_cast<uint8_t>(fec_k);
        result.response.u.cmd_get_fec.n = static_cast<uint8_t>(fec_n);
        result.payload_size = sizeof(result.response.u.cmd_get_fec);
        break;

    case CMD_SET_FEC:
        if (request_size != offsetof(cmd_req_t, u) + sizeof(request.u.cmd_set_fec))
        {
            result.response.rc = htonl(EINVAL);
            break;
        }
        // FEC 是 v6 的启动期/会话不变量。修改它会使接收端失去同步，
        // 并重置受信任明文模式的 nonce 语义。
        result.response.rc = htonl(ENOTSUP);
        break;

    default:
        result.response.rc = htonl(ENOTSUP);
        break;
    }

    return result;
}
