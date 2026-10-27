# service-start-order diagnosis

## Scope

- Evidence collected for `08formal-e53de00`, runtime commit `e53de0076c6ed118d824a2d31261e75de3223333`.
- This directory contains only temporary diagnostic loops and their outputs. This diagnostic did not write production files.
- The worktree now contains a concurrent uncommitted `wfb_ng/fl/service.py` reordering and its untracked regression test. The red outputs below were captured against the pre-reordering baseline and are retained as evidence; they are not a result from the current modified file.
- Loops use a fake `RoleService`; they do not start `wfb_v6_uplink`, create TUN devices, touch radio hardware, or bind real control/data ports.

## Field Timing Boundary

The valid server-side timing evidence is the server's own monotonic clock:

```text
last heartbeat: 57333.316
offline edge:   57343.316
silence:        10.000 s
```

The field measurements supplied for fl02 are:

```text
native handoff空窗: 2.444 s
wfb_ng.fl.mnist import: 2.203 s
fl02 wall clock lead vs server: 3.064 s
NTP: unavailable
```

Do not compare the server wall timestamp `03:30:40.061` with the fl02 wall timestamp `03:30:40.224`. The 3.064-second clock offset makes that cross-board ordering invalid. This archive therefore makes no claim that the server offline edge preceded or followed the client PREPARING log.

The only valid threshold comparison here is server-local: a 2.444-second native gap is below the server's 10-second heartbeat silence threshold by 7.556 seconds. The pure-idle 61-second sample is a separate baseline signal: it can cross the threshold without a job handoff.

## Loop A: Synthetic Slow Import

`service-import-loop.py` invokes the public `wfb_ng.fl.service.main("client")`, dynamically imports a temporary entry that sleeps 250ms, and records fake task-link startup. It asserts that task link startup must precede heavy import and must meet a 50ms stop deadline.

Observed red output is in `red-output.txt`:

```text
algorithm_import_started=0.003s
stop_requested=0.030s
task_link_started=0.254s
AssertionError: RED: service.main started the task link after load_algorithm/import
```

## Loop B: Real Import

`service-real-import-loop.py` uses the real `wfb_ng.fl.mnist:client_entry` dynamic entry while keeping transport fake. On the repository host it observed import completion and fake task-link startup at approximately `2.280s`, then failed the same ordering assertion. This is a real Python import-order reproduction, not a fl02 timing measurement.

The field value that remains authoritative for fl02 is `2.203s`; the local loop must not replace it.

## Loop C: Monotonic Liveness Control

`handoff-liveness-loop.py` uses the real in-memory `ControlPlaneServer` liveness watcher with a scale factor (`10s -> 0.1s`, `2.444s -> 0.02444s`):

- a heartbeat restored after the scaled native gap produces no offline event;
- silence beyond the scaled 10-second threshold produces `heartbeat_timeout` and `offline`.

This proves the threshold behavior only. It does not replay the field radio path.

## What Is Proven

- `service.main` calls `load_algorithm()` before `load_role_service()` and `service.start()`.
- A stop signal received during dynamic import does not cancel Python import. After import returns, the current flow can still create/start the task resource before cleanup.
- The controlled loop can therefore prove an import-before-link ordering defect and the missing post-import stop checkpoint.
- The server liveness implementation treats a true 10-second monotonic heartbeat silence as offline; a shorter 2.444-second gap alone does not satisfy that condition.

## What Is Not Proven

- The loop does not prove that the import ordering caused the server's 10-second offline edge.
- It does not prove that GPU initialization blocked the parent client control thread or the server liveness thread. GPU preparation is after `service.start()` in the algorithm path.
- It does not justify changing the global heartbeat deadline.
- It does not justify an automatic task-start liveness lease or any other offline exemption. The field specification does not grant that exemption; first stabilize idle passive receive behavior and use preflight08 passive diagnostics to determine whether a handoff exception is needed at all.

## Stop Boundary

The safe contract for the later transport06 work is narrow: dynamic Python import need not be forcibly cancellable. Once `load_algorithm()` returns, check the stop state before creating or starting `RoleService` resources. No production implementation is included in this evidence directory.
