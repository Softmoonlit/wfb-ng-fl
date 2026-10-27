# Native Idle Diagnosis

## Scope and Safety

This is a read-only diagnosis for `08formal-e53de00` / native commit `69a94d8`. No field contact, no daemon restart, no hardware, no TUN, no real UDP control/data ports, no production edits, and no threshold/lease changes were used. The C++ loops use only real source classes and local compilation.

## Passive Evidence

The passive-idle README reports:

- Client heartbeat generation continues; fl01/fl02 TUN-originated packets can sit for tens of seconds and arrive in bursts.
- fl01: `HUNTING=206`, `IDLE=9`; max client send gap `5.23197s`; server-side matched HB burst source span up to `46.509s`.
- fl02: `HUNTING=163`, `IDLE=12`; max client send gap `5.23204s`; server-side matched HB burst source span up to `58.076s`.
- fl04: `IDLE=22`, no observed API offline sample; it is the control comparison.
- Approximate server grant counts in the 110s window: node4 `561`, node2 `186`, node1 `106`. Grant logs mostly show `active_queue=[4]`, `[2]`, or `[1]`, with short mixed transitions.
- Native client counter deltas over each passive window:
  - fl01: `TOKEN_AUTH accepted +59`, `authorized_sends +215`, `denied_sends +823`.
  - fl02: `accepted +96`, `authorized_sends +186`, `denied_sends +577`.
  - fl04: `accepted +361`, `authorized_sends +21`, `denied_sends +82`.

These counters are not a one-to-one packet ledger: `accepted_events` counts grant events, while `authorized_sends` counts data packets sent under authorization. A grant can be accepted with no pending TUN data; one authorization window can cover more than one data send. Server grant anchors have approximately 1s uncertainty.

## Source Findings

### 1. Pending packet / FEC flush seam

In [src/v6_uplink.cpp](/home/fl/projects/wfb-ng-fl/src/v6_uplink.cpp:1824):

- `pending_packet` is read.
- `flush_data_block_if_due(now_ms)` is called only when `pending_packet == NULL` at lines 1826-1829.
- The unauthorized branch at lines 1832-1837 increments `denied_sends`, calls `maybe_send_ready`, and immediately `continue`s.
- `next_flush_deadline_ms()` is included in the outer wakeup calculation at lines 1731-1739. Once due, this drives repeated zero-timeout polls while the pending packet remains unauthorized.
- `AirTransmitter::send_data()` arms the FEC idle deadline for 2ms at [src/v6_uplink.cpp:199](/home/fl/projects/wfb-ng-fl/src/v6_uplink.cpp:199); `flush_data_block_if_due()` clears it only when called at [src/v6_uplink.cpp:221](/home/fl/projects/wfb-ng-fl/src/v6_uplink.cpp:221).

This is a real control-flow defect candidate: pending data can starve a due FEC close and create a busy loop. It is not yet the field root cause of heartbeat offline.

### 2. Receiver batch behavior

[Receiver::Receiver](/home/fl/projects/wfb-ng-fl/src/rx.cpp:145) sets pcap nonblocking at line 163. `Receiver::loop_iter()` then loops `pcap_next()` until it returns null at [src/rx.cpp:201](/home/fl/projects/wfb-ng-fl/src/rx.cpp:201), with no packet-count or elapsed-time budget. `run_client()` calls one `loop_iter()` from the outer poll loop at [src/v6_uplink.cpp:1778](/home/fl/projects/wfb-ng-fl/src/v6_uplink.cpp:1778).

Therefore a queued pcap batch can delay return to the outer client loop. The source has no bound proving how long that can take. Passive data shows bursts and FEC loss/recovery, but does not measure per-batch processing duration, so this is a ranked hypothesis, not a finding.

### 3. Grant expiry clock semantics

`parse_control_envelope()` receives `now_ms` from the receiver and computes `grant_expires_at_ms = now_ms + duration` at [src/control_envelope.cpp:29](/home/fl/projects/wfb-ng-fl/src/control_envelope.cpp:29) and lines 88-93. `ClientGrantListener` then passes the same local `get_time_ms()` to `TokenAuthorizationState::apply_event()` at [src/v6_uplink.cpp:1225](/home/fl/projects/wfb-ng-fl/src/v6_uplink.cpp:1225).

There is no sender timestamp or cross-board monotonic comparison in this path. A grant held in a pcap batch for 121ms is not rejected as expired by current code; its expiry starts at processing time.

The offline loop [grant-batch-expiry-loop.cpp](grant-batch-expiry-loop.cpp) exercises the real parser/filter and outputs `decision=accept` for a 121ms delayed processing case. This falsifies the narrow hypothesis “pcap batch > 120ms directly expires the grant.” It does not falsify that an unbounded batch delays all other work or that a grant can be lost before parsing.

### 4. READY / HUNTING behavior

Native READY redeclaration is 4s, not 1s: `kReadyRedeclareTimeoutMs=4000` at [src/v6_uplink.cpp:45](/home/fl/projects/wfb-ng-fl/src/v6_uplink.cpp:45), with the branch at lines 1301-1321. The Python control plane separately changes HUNTING after two missed ACKs. A HUNTING client changes channels while the server grant scheduler continues to operate on ready declarations; this can explain why the server logs grants for a node that accepts few of them, but the passive evidence does not establish the first loss direction.

The client grant path accepts only source/target/session/sequence-valid grants in [src/control_envelope.cpp:99](/home/fl/projects/wfb-ng-fl/src/control_envelope.cpp:99). The passive counters show accepted grant events for fl01/fl02, so a universal replay/session rejection is not supported.

### 5. ACK budget / fastscan loop

The production values are distinct:

- `HUNTING_ATTEMPT_TIMEOUT_SECONDS=0.23` and two attempts per channel: `0.46s` channel attempt budget.
- Idle heartbeat interval is `5s`; two missing ACKs transition IDLE to HUNTING, nominally `10s` before the state transition.
- Native READY redeclaration is `4000ms`.
- Real scheduler slots are `120ms + 10ms guard = 130ms`; three active nodes rotate in `390ms`.

[hunt-delay-loop.py](hunt-delay-loop.py) uses the real public `ControlPlaneClient`, a real ephemeral localhost UDP socket, and a fake server that delays ACKs. It does not use field ports, TUN, radio, or hardware. Output is in [hunt-delay-output.txt](hunt-delay-output.txt). The quantity input for the later phase comparison is [formal69-quantity-diff.json](formal69-quantity-diff.json).

Observed:

```text
ACK delay 0.30s: two HUNTING packets, one ACK, result IDLE
ACK delay 0.35s: two HUNTING packets, one ACK, result IDLE
ACK delay 0.40s: two HUNTING packets, one ACK, result IDLE
ACK delay 0.47s: one/two attempt window exhausted, result HUNTING
idle two misses: missed_acks=2, state=HUNTING, nominal interval=10s
```

The 0.30-0.40s cases prove a late ACK can cross the attempt boundary because the ACK has no request/attempt identity. They do not prove self-oscillation: in this loop the late ACK closes the hunt. The 0.47s case demonstrates the boundary where both 230ms waits are missed.

The idle control case in the same loop invokes two real `send_heartbeat_once()` calls with dropped ACKs: `missed_acks=2` and `state=HUNTING`, while the production supervisory interval remains 5s, giving a nominal 10s two-miss budget. This is separate from the 0.46s per-channel fastscan attempt budget.

[real-scheduler-loop.cpp](real-scheduler-loop.cpp) uses the actual `TokenScheduler` implementation. With nodes `[1,2,4]`, duration `120ms`, guard `10ms`, it emits the expected `130ms` slot schedule. With no observed uplink refresh, it removes node2 after `650ms` and node4 after `780ms`; this is a scheduler input/refresh experiment, not a field replay. Output is in [real-scheduler-output.txt](real-scheduler-output.txt).

The minimal conclusion is therefore narrower than “3-client rotation causes HUNTING”: a 0.30-0.40s ACK delay does not inherently prevent the current fastscan loop from reaching IDLE, but it does make the ACK attribution cross-attempt. Self-oscillation remains falsifiable only with a phase-aware model that includes native READY delivery, current channel, scheduler active membership, and observed uplink refresh.

## Formal 69 Quantity Cross-Check

The newer `formal-69a94d8` captures are not interchangeable with the earlier passive window and are compared only by each capture's local counts, never cross-board wall time:

- `all3`: fl01 `HUNTING 13/28`, fl02 `26/42`, fl04 `13/29`.
- `only-fl02`: fl02 `IDLE 16/16` on channel 157.
- `fl01-fl02`: fl01 `HUNTING 38/53`, fl02 `HUNTING 14/30`.
- `restore-all`: fl01/fl02 `IDLE 15/15` each; fl04 `HUNTING 12/27`.

These phase differences show a reproducible interaction/condition sensitivity, but they do not isolate the 230ms ACK budget, native grants, MCS, or the FEC flush branch. The formal69 before/after status files also show no uniform persistent client failure. They are useful quantity evidence, not a root-cause proof.
## Red Loop and Control

`pending-flush-loop.cpp` uses the real `TokenAuthorizationState` and `FixedCapacityTunReadQueue`, with a minimal FEC deadline probe and the production branch order. It runs red:

```text
pending=1 denied=100 authorized=0 zero_timeout_polls=100 fec_flushes=0
Assertion ... RED: pending unauthorized packet starves due FEC flush
```

`pending-flush-control-output.txt` is a /tmp-only control run that invokes the flush before the authorization `continue`; it produces `fec_flushes=1`. This confirms the red signal is load-bearing for the suspected branch. Because `run_client()` has no dependency-injection seam and requires TUN/pcap/radio setup, this is a semantic seam, not an end-to-end `run_client()` replay. That seam absence is itself a testability finding.

## Ranked Falsifiable Hypotheses

1. **Native grant delivery / channel-state feedback is the primary field candidate.** fl01/fl02 spend most observed heartbeats in HUNTING and cycle 157/149/153; server grants concentrate in active queues that change as READY declarations arrive, while only a fraction of server grant lines can be matched to client accepted grant events. Prediction: a passive synchronized window with per-grant sequence at server send, client RX accept, and channel at both ends will show missing/late control frames before any queue flush symptom; if every grant reaches the client promptly while data remains stalled, demote this hypothesis.
2. **Unbounded pcap batch processing delays the client outer loop.** `loop_iter()` drains until empty, so a large radio backlog can delay grant processing and TUN servicing. Prediction: a bounded offline fixture that feeds N frames and measures `loop_iter` duration will show processing time scaling with N; a real passive window needs a native per-iteration duration or batch counter to connect it to the observed 40-58s user-data delay. Current evidence does not provide that measurement.
3. **Pending unauthorized packet starves FEC block close and spins poll.** The red loop proves the exact branch behavior and the control run removes it. Prediction: adding only a temporary counter around the two branch edges would show `fec_flush_deadline_due && pending_packet && !authorized` during the same interval; if no such overlap exists in a field capture, this is not the cause of that episode even though the code defect remains.
4. **Scheduler active-queue/silent-removal behavior explains grant skew.** `declare_ready_with_result()` enqueues only nodes that send READY at [src/token_scheduler.cpp:292](/home/fl/projects/wfb-ng-fl/src/token_scheduler.cpp:292); `next_grant()` rotates active nodes at lines 338-372; `collect_silent_node_removals()` can remove after two silent grants at lines 381-420. Prediction: replaying the observed READY/observed-uplink event sequence should reproduce the active queue transitions; if it does, the grant skew is scheduler input/state, not a grant loop starvation bug.
5. **Replay/stale/expiry rejection is the primary cause.** Current passive evidence weakens this: grant filter counters show accepted events and no stale/expired increments in the selected tails; the parser loop falsifies the delayed-expiry subcase. Prediction: a synchronized capture showing `GRANT_FILTER accepted` flat while `received` rises with stale/expired increments would restore this hypothesis.

## Observation Log Schema

The added native lines are emitted only from the existing periodic stats cadence:

- Existing `GRANT_FILTER` remains the receiver aggregate `received:accepted:invalid_source:wrong_target:duplicate:stale:expired:invalid_session:retired_session` line; `GRANT_SEQ` complements it with this client’s accepted count and last global sequence only.
- `GRANT_SEQ`: `timestamp_ms<TAB>GRANT_SEQ<TAB>accepted_count:last_accepted_sequence`. The sequence is globally allocated by the server; gaps are expected when grants for other nodes occur and are not a loss estimate.
- `CLIENT_LOOP`: `timestamp_ms<TAB>CLIENT_LOOP<TAB>pending_unauthorized_fec_due=<count> zero_timeout_polls=<count>`.
- `RX_BATCH`: `timestamp_ms<TAB>RX_BATCH<TAB>batches:packets:elapsed_us:max_elapsed_us:max_packets`, accumulated from completed pcap drain calls.

These counters are observational and reset with the existing periodic stats reset where applicable. No authorization, flush, READY, scheduler, or packet handling behavior is changed.
## Review Boundary

The three source files contain an uncommitted observation-only patch for review:
`src/rx.hpp`, `src/rx.cpp`, and `src/v6_uplink.cpp`. It was not deployed,
started, or connected to field hardware. No authorization, FEC flush, scheduler,
READY, poll timeout, or packet dispatch behavior was changed.

The next evidence needed from preflight08 is passive only: per-iteration pcap batch duration/count, grant sequence send/receive/accept correlation, and whether `fec_flush_deadline_due + pending_packet + !authorized` overlaps the long queue interval. Do not infer that from wall clocks or change behavior to make the observation pass.
