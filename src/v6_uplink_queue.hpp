#pragma once

#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include <string>
#include <vector>

#include "wifibroadcast.hpp"

struct QueuedTunPacket {
    size_t size = 0;
    uint8_t bytes[MAX_PAYLOAD_SIZE] = {};
};

enum TunReadPauseReason {
    TUN_READ_PAUSE_NONE = 0,
    TUN_READ_PAUSE_QUEUED_BYTES_THRESHOLD,
    TUN_READ_PAUSE_QUEUED_PACKETS_LIMIT,
};

inline const char *tun_read_pause_reason_name(TunReadPauseReason reason)
{
    switch (reason)
    {
    case TUN_READ_PAUSE_NONE:
        return "none";
    case TUN_READ_PAUSE_QUEUED_BYTES_THRESHOLD:
        return "queued_bytes_threshold";
    case TUN_READ_PAUSE_QUEUED_PACKETS_LIMIT:
        return "queued_packets_limit";
    }

    return "unknown";
}

struct TunReadBackpressureCounters {
    uint64_t tun_read_pause_total = 0;
    uint64_t tun_read_resume_total = 0;
    uint64_t tun_read_pause_total_by_queued_bytes_threshold = 0;
    uint64_t tun_read_pause_total_by_queued_packets_limit = 0;
};

class FixedCapacityTunReadQueue {
public:
    FixedCapacityTunReadQueue(uint32_t pause_threshold_bytes,
                              uint32_t resume_threshold_bytes,
                              size_t queued_packets_limit)
        : slots_(queued_packets_limit),
          pause_threshold_bytes_(pause_threshold_bytes),
          resume_threshold_bytes_(resume_threshold_bytes)
    {
    }

    bool empty() const
    {
        return count_ == 0;
    }

    bool full() const
    {
        return count_ == slots_.size();
    }

    bool tun_read_enabled() const
    {
        return !tun_read_paused_;
    }

    size_t queued_packets() const
    {
        return count_;
    }

    size_t queued_bytes() const
    {
        return queued_bytes_;
    }

    size_t queued_packets_limit() const
    {
        return slots_.size();
    }

    uint32_t pause_threshold_bytes() const
    {
        return pause_threshold_bytes_;
    }

    uint32_t resume_threshold_bytes() const
    {
        return resume_threshold_bytes_;
    }

    TunReadPauseReason current_pause_reason() const
    {
        return current_pause_reason_;
    }

    const TunReadBackpressureCounters &counters() const
    {
        return counters_;
    }

    bool push(const uint8_t *data, size_t size)
    {
        if (data == NULL || size > MAX_PAYLOAD_SIZE || full())
        {
            return false;
        }

        const size_t tail = (head_ + count_) % slots_.size();
        slots_[tail].size = size;
        memcpy(slots_[tail].bytes, data, size);
        count_ += 1;
        queued_bytes_ += size;
        refresh_bytes_pause_latch();
        update_pause_state();
        return true;
    }

    const QueuedTunPacket *front() const
    {
        if (empty())
        {
            return NULL;
        }

        return &slots_[head_];
    }

    bool pop_front()
    {
        if (empty())
        {
            return false;
        }

        queued_bytes_ -= slots_[head_].size;
        slots_[head_].size = 0;
        head_ = (head_ + 1) % slots_.size();
        count_ -= 1;
        refresh_bytes_pause_latch();
        update_pause_state();
        return true;
    }

    bool write_summary_file(const std::string &path, const char *role, uint8_t node_id) const
    {
        if (path.empty())
        {
            return true;
        }
        if (role == NULL || role[0] == '\0')
        {
            return false;
        }

        const std::string tmp_path = path + ".tmp";
        FILE *fp = fopen(tmp_path.c_str(), "w");
        if (fp == NULL)
        {
            return false;
        }

        const int written = fprintf(fp,
                                    "{\n"
                                    "  \"role\": \"%s\",\n"
                                    "  \"link_security_mode\": \"trusted_plaintext\",\n"
                                    "  \"node_id\": %u,\n"
                                    "  \"queued_bytes\": %zu,\n"
                                    "  \"queued_packets\": %zu,\n"
                                    "  \"queued_packets_limit\": %zu,\n"
                                    "  \"pause_threshold_bytes\": %u,\n"
                                    "  \"resume_threshold_bytes\": %u,\n"
                                    "  \"tun_read_paused\": %s,\n"
                                    "  \"current_pause_reason\": \"%s\",\n"
                                    "  \"tun_read_pause_total\": %" PRIu64 ",\n"
                                    "  \"tun_read_resume_total\": %" PRIu64 ",\n"
                                    "  \"tun_read_pause_total_by_reason\": {\n"
                                    "    \"queued_bytes_threshold\": %" PRIu64 ",\n"
                                    "    \"queued_packets_limit\": %" PRIu64 "\n"
                                    "  }\n"
                                    "}\n",
                                    role,
                                    static_cast<unsigned>(node_id),
                                    queued_bytes_,
                                    count_,
                                    slots_.size(),
                                    pause_threshold_bytes_,
                                    resume_threshold_bytes_,
                                    tun_read_paused_ ? "true" : "false",
                                    tun_read_pause_reason_name(current_pause_reason_),
                                    counters_.tun_read_pause_total,
                                    counters_.tun_read_resume_total,
                                    counters_.tun_read_pause_total_by_queued_bytes_threshold,
                                    counters_.tun_read_pause_total_by_queued_packets_limit);
        const bool ok = written > 0 && fclose(fp) == 0 && rename(tmp_path.c_str(), path.c_str()) == 0;
        if (!ok)
        {
            remove(tmp_path.c_str());
        }
        return ok;
    }

    bool write_summary_file(const std::string &path, uint8_t node_id) const
    {
        return write_summary_file(path, "client", node_id);
    }

private:
    void refresh_bytes_pause_latch()
    {
        if (!bytes_pause_latched_ && queued_bytes_ >= pause_threshold_bytes_)
        {
            bytes_pause_latched_ = true;
        }
        else if (bytes_pause_latched_ && queued_bytes_ <= resume_threshold_bytes_)
        {
            bytes_pause_latched_ = false;
        }
    }

    void update_pause_state()
    {
        TunReadPauseReason next_reason = TUN_READ_PAUSE_NONE;
        if (full())
        {
            next_reason = TUN_READ_PAUSE_QUEUED_PACKETS_LIMIT;
        }
        else if (bytes_pause_latched_)
        {
            next_reason = TUN_READ_PAUSE_QUEUED_BYTES_THRESHOLD;
        }

        const bool next_paused = next_reason != TUN_READ_PAUSE_NONE;
        if (!tun_read_paused_ && next_paused)
        {
            tun_read_paused_ = true;
            current_pause_reason_ = next_reason;
            counters_.tun_read_pause_total += 1;
            if (next_reason == TUN_READ_PAUSE_QUEUED_BYTES_THRESHOLD)
            {
                counters_.tun_read_pause_total_by_queued_bytes_threshold += 1;
            }
            else if (next_reason == TUN_READ_PAUSE_QUEUED_PACKETS_LIMIT)
            {
                counters_.tun_read_pause_total_by_queued_packets_limit += 1;
            }
            return;
        }

        if (tun_read_paused_ && !next_paused)
        {
            tun_read_paused_ = false;
            current_pause_reason_ = TUN_READ_PAUSE_NONE;
            counters_.tun_read_resume_total += 1;
            return;
        }

        current_pause_reason_ = next_reason;
    }

    std::vector<QueuedTunPacket> slots_;
    size_t head_ = 0;
    size_t count_ = 0;
    size_t queued_bytes_ = 0;
    uint32_t pause_threshold_bytes_ = 0;
    uint32_t resume_threshold_bytes_ = 0;
    bool bytes_pause_latched_ = false;
    bool tun_read_paused_ = false;
    TunReadPauseReason current_pause_reason_ = TUN_READ_PAUSE_NONE;
    TunReadBackpressureCounters counters_ = {};
};
