#include "token_namespace_bridge.hpp"

#include <signal.h>
#include <stdio.h>

#include <atomic>
#include <chrono>
#include <exception>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>

using namespace std;

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

} // namespace

int main(int argc, char **argv)
{
    try
    {
        TokenNamespaceBridgeConfig config = parse_token_namespace_bridge_args(argc, argv);
        signal(SIGINT, stop_handler);
        signal(SIGTERM, stop_handler);
        return run_token_namespace_bridge(config, g_stop_requested, sleep_ms_default);
    }
    catch (const invalid_argument &e)
    {
        if (string(e.what()) != "help requested")
        {
            fprintf(stderr, "Error: %s\n", e.what());
        }
        print_token_namespace_bridge_usage(argv[0]);
        return 1;
    }
    catch (const exception &e)
    {
        fprintf(stderr, "Error: %s\n", e.what());
        return 1;
    }
}
