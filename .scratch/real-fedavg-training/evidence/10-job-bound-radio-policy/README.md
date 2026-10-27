# Ticket 10 Evidence Slice

Status: implementation slice complete, not committed.

Scope:

- `wfb_ng/fl/algorithm.py`
- `wfb_ng/fl/coordinator.py`
- `wfb_ng/tests/test_fl_algorithm_context.py`
- `wfb_ng/tests/test_fl_coordinator.py`

The slice freezes the job-bound `AlgorithmContext` callback signature and the optional Coordinator policy entry. It does not invoke or implement the initial ServerDaemon policy, radio transactions, liveness lease wiring, client task-link updates, ControlPlane changes, Runtime changes, or 20 MHz calibration.

The round policy hook runs after `round_completion` succeeds and its return fact is recorded, then closes the existing supervisor round with `end_round`; only after that it performs `check_stop -> non-final radio_policy -> check_stop`. The next round is the next `begin_round`, and the final round has no policy call.

The existing `round_timeout_seconds` covers the round through its completion barrier. `end_round` clears that deadline before the inter-round policy hook, so a short previous round budget cannot truncate policy waiting for the host control event. No policy-specific timeout or generic worker thread is added. A non-cooperative policy remains owned by the Coordinator call until it returns; abort still dispatches the existing 06 cancellation callback and terminal state waits for real return. Control transaction deadline/lease/stop remains the transaction owner's responsibility.

See `interface-notes.md`, `focused-tests.txt`, and `typecheck.txt` for the frozen contract and verification.
