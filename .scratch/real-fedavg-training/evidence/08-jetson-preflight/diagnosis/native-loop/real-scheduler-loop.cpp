#include <cstdint>
#include <iostream>
#include <vector>
#include "token_scheduler.hpp"

int main() {
    TokenSchedulerConfig config = {};
    config.node_ids = {1, 2, 4};
    config.duration_ms = 120;
    config.guard_interval_ms = 10;
    TokenScheduler scheduler(config);
    scheduler.declare_ready_with_result(1, 1000);
    scheduler.declare_ready_with_result(2, 1000);
    scheduler.declare_ready_with_result(4, 1000);
    for (uint64_t now = 1000; now < 5000; now += 130) {
        TokenGrant grant = {};
        if (scheduler.next_grant(&grant, now)) {
            std::cout << now << " " << static_cast<unsigned>(grant.node_id)
                      << " " << grant.duration_ms << " " << grant.guard_interval_ms
                      << " active=" << scheduler.describe_active_state() << "\n";
            std::vector<TokenScheduler::SilentNodeRemoval> removed;
            scheduler.collect_silent_node_removals(now, &removed, grant.node_id);
            for (const auto &item : removed)
                std::cout << "remove " << static_cast<unsigned>(item.node_id)
                          << " silence_ms=" << item.silence_ms
                          << " consecutive=" << item.consecutive_silent_grants << "\n";
        }
    }
}
