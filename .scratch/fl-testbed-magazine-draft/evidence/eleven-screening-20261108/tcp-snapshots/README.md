# TCP snapshot recovery

This directory contains only the `ss` sections already returned by the earlier tool calls.
No remote command was rerun for this recovery.

Provenance:
- Source: prior tool output in this conversation.
- Recovery mode: copied visible `ss` stdout sections without recomputing or normalizing metrics.
- The tool display did not expose independent remote stdout/stderr streams. The `.ss.stderr.recovered` files are empty because no `ss`-specific stderr line was visible; this is not a claim that an unavailable stream was independently captured.
- Shell-wrapper stderr visible in the same tool output is preserved separately as `*.wrapper.stderr.recovered`.
- `fl00` is local on this machine (`fl00` resolved to loopback); the later fl00 snapshot was run locally after the upload had ended and had no active port-18080 socket.

Commands represented:
- Clients: `ss -tinop dst 10.80.0.1 dport = :18080`
- fl00 direct: `ss -tinop src 10.80.0.1 sport = :18080`
- fl00 follow-up: unfiltered `ss -tinop`, plus `ss -ltnp 'sport = :18080'`

The `.ss.stdout.recovered` files are the auditable source for `rto`, `rtt`, `cwnd`, `ssthresh`, `bytes_retrans`, `retrans`, `lost`, `unacked`, `Send-Q`, and `notsent`.
