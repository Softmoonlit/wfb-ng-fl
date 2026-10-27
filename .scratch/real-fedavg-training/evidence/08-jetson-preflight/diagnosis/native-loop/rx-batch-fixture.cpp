#include <cassert>
#include <cstdint>
#include <iostream>

#include "rx.hpp"

class CaptureAggregator : public Aggregator {
public:
    CaptureAggregator() : Aggregator("", 0, 0, 255, true, 1, 1) {}

protected:
    void send_to_socket(const uint8_t *, uint16_t) override {}
};

int main() {
    CaptureAggregator aggregator;
    aggregator.observe_receiver_batch(3, 10);
    aggregator.observe_receiver_batch(2, 40);
    const ReceiverBatchStats &stats = aggregator.receiver_batch_stats;
    assert(stats.batches == 2);
    assert(stats.packets == 5);
    assert(stats.elapsed_us == 50);
    assert(stats.max_elapsed_us == 40);
    assert(stats.max_packets == 3);
    aggregator.clear_stats();
    assert(aggregator.receiver_batch_stats.batches == 0);
    assert(aggregator.receiver_batch_stats.packets == 0);
    std::cout << "receiver_batch_stats: PASS\n";
}
