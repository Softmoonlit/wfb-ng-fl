#include "token_scheduler.hpp"

#include <signal.h>
#include <stdio.h>

#include <atomic>
#include <chrono>
#include <exception>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <thread>

using namespace std;

#include <time.h>

namespace {

atomic_bool g_stop_requested(false);

void stop_handler(int)
{
    g_stop_requested.store(true);
}

void sleep_ms_default(uint32_t duration_ms)
{
    this_thread::sleep_for(chrono::milliseconds(duration_ms));
}

uint64_t get_time_ms_default(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}

} // namespace

int main(int argc, char **argv)
{
    try
    {
        TokenSchedulerConfig config = parse_token_scheduler_args(argc, argv);
        signal(SIGINT, stop_handler);
        signal(SIGTERM, stop_handler);
        return run_token_scheduler(config, cout, g_stop_requested, sleep_ms_default, get_time_ms_default);
    }
    catch (const invalid_argument &e)
    {
        if (string(e.what()) != "help requested")
        {
            fprintf(stderr, "Error: %s\n", e.what());
        }
        print_token_scheduler_usage(argv[0]);
        return 1;
    }
    catch (const exception &e)
    {
        fprintf(stderr, "Error: %s\n", e.what());
        return 1;
    }
}
