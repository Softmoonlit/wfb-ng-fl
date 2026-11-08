# 45-second passive heartbeat diagnosis

Server fl00 active, configured channel 149 / 40 MHz; no radio, service, heartbeat or registry writes. Four captures completed successfully. All matches use boot_id + echoed timestamp; request session and ACK session are reported separately (challenge legitimately changes session). Client RTT and server processing use only the respective host monotonic clock. No host wall-clock subtraction.

|node|emitted|server received|server ACK|client ACK seen|confirmed + session match <=10s|liveness|actual monitor channel before→after|
|---|---:|---:|---:|---:|---:|---|---|
|fl01|83|78|78|76|15|RED|157 → 157|
|fl05|60|57|57|57|19|RED|149 → 149|
|fl04|82|67|67|66|15|RED|153 → 149|

fl05 is no longer a healthy control: its daemon also logs ten-second confirmation failures and its emitted request channel cycles through 149/153/157. Native actual channel snapshots remain 149 at both endpoints; this does not establish continuous channel stability. fl01 was actually on 157 at both snapshots; fl04 changed from 153 to 149.

Loss counts are in verdict.json and per-request chains in flXX-chains.json. Request→server missing: fl01 5, fl05 3, fl04 15; server received→ACK missing: all 0; server ACK→client missing: fl01 2, fl05 1, fl04 1. Unconfirmed challenge responses: 58/35/48; confirmed ACK with request session mismatch: 3/3/3. These are observed capture-stage categories, not proof of application acceptance or exact RF loss. A received confirmed matching ACK can still be rejected by daemon logic.

Capture boundaries differ by SSH startup latency: unmatched packets near either boundary are censored, and may explain a missing server observation or a client ACK with no server observation. No additional sampling was started. journal is a bounded trailing 65-second window and therefore includes some pre-capture warnings. Deadline is 10 seconds from daemon warning; per-request within_10s uses local RTT and does not by itself establish ten-second liveness.

All UDP9000/9001 on each TUN was observed; incoming ACKs for other clients are expected on this shared downlink and excluded from own-client ACK counts. No air pcap: tcpdump/scapy were absent on the server; existing inspected Python AF_PACKET method was used. Full thread snapshots, native stdout/stderr tails and service ExecStart are embedded in each host JSON. Native tails may contain records predating capture; compare before/after.

Automatic verdicts: liveness RED on observed ten-second daemon warning; actual channel RED if either monitor-channel snapshot differs from server 149; otherwise GREEN_AT_SNAPSHOTS only. Further application-level root cause is not established by this read-only capture.

## Deadline correction

230ms is the strict per-attempt ACK budget; 10s is the no-valid-ACK threshold before hunting. The earlier <=10s column is delivery timing only, not valid ACK confirmation. Strict wire-level candidates are in preliminary-230ms.json; daemon acceptance still requires state/deadline validation. Existing server native tail contains 287 records with active_queue [1,2,6,5,9,8,7,10,4] and 8 records with [1,2,6,5,9,8,7,10]; thus nine-node retention is frequent but not uninterrupted. READY counts from this tail are not full-window frequencies and READY presence does not prove the client remains on channel 149.
