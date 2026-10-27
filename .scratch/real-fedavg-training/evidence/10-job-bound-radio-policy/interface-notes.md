# Ticket 10 interface slice

This evidence belongs only to the job-bound radio-policy context and Coordinator sequencing slice. It does not include ServerDaemon, ClientDaemon, ControlPlane, Runtime, radio-table, or hardware changes.

## Frozen interfaces

```python
AlgorithmContext.radio_configure_callback: Optional[
    Callable[[Mapping[str, Any]], Mapping[str, Any]]
]
AlgorithmContext.configure_radio(patch: Mapping[str, Any]) -> Mapping[str, Any]
FLCoordinator(..., radio_policy_fn: Optional[
    Callable[[Mapping[str, Any], AlgorithmContext], None]
] = None)
```

The host injects a job-bound callback while constructing the base `AlgorithmContext`. `for_phase("radio_policy", ...)` retains that callback; every other phase clears it. The callback receives only a recursive read-only patch mapping and returns a finite JSON mapping. It does not expose ControlPlane, Runtime, radio adapters, target selection, or channel ownership.

The patch allowlist is exactly `radio_txpower_dbm`, `downlink_mcs`, `uplink_mcs`, `bandwidth_mhz`, and `uftp_rate_kbps`. `channel`, `target_nodes`, aliases, unknown fields, booleans, and empty patches are rejected. No rate table, physical range, target membership, transaction state, or effective configuration is synthesized here.

The patch validator first requires every object key to be a string. Unknown fields then fail with the bounded, fixed reason `radio patch contains unsupported fields`; it does not sort or echo unknown keys. Only after those gates does it perform the finite JSON budget/value validation. Mixed key types and a 10 KB unknown key both remain `invalid_radio_patch`.

The policy callable is invoked once after `round_completion(round_index)` succeeds and its submission-return fact is recorded. The Coordinator then calls the existing supervisor `end_round(round_index)`, checks stop, invokes the policy only when `round_index < job.rounds`, checks stop again, and begins the next round. The final round never invokes policy.

The existing `round_timeout_seconds` covers the completed round through the completion barrier only. It is cleared by `end_round` before the inter-round policy hook, so no old round deadline truncates a policy waiting on the host control transaction. No policy-specific timeout or generic worker thread is added. The host ControlPlane transaction owns its own finite deadline/lease/stop behavior; pure policy code is responsible for cooperative `context.check_stop()` calls. A non-cooperative policy is not declared exited: Coordinator cancellation calls the existing 06 cancel callback, then waits for the policy call to return before producing terminal state.

`configure_radio` checks stop before and after the synchronous host callback. The finite result is an opaque read-only report; this context never promotes any field to an effective configuration.

Initial policy invocation remains a later ServerDaemon-owned preparation-budget slice. This implementation does not add that call.