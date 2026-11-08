# UFTP binary unification

Completed UTC: 2026-11-08T07:56:18.072121+00:00

Verified all 10 hosts: fl00, fl01, fl02, fl04, fl05, fl06, fl07, fl08, fl09, fl10. Both binaries now version 5.0.3 and byte identical to fl00 artifacts.

Replaced both /usr/bin/uftp and /usr/bin/uftpd on fl01, fl02, fl04, fl05, fl10. fl10 was 4.10.2; fl01/02/04/05 were 5.0.3 with different SHA. Other hosts already matched.

Each changed host has its own originals preserved under /var/backups/uftp-nine-unification-20261108T075403Z/uftp and uftpd; deployment JSON records original SHA. Source artifacts are preserved in artifact/.

All hosts audited in parallel for architecture, paths, embedded version, SHA256, ldd, active transfer/test processes and running units. Actual daemon PATH read from /proc/PID/environ resolves both tools to /usr/bin. Immediately before replacement each target rechecked for transfer/test processes; none found. Staged fl00 AArch64 binaries passed ldd -r and executable argument parsing using existing libraries. No package installation or shared library change.

All daemon PIDs unchanged: True. No daemon/native/radio modifications or service restarts. No smoke or transfer test launched; REGISTER recovery remains untested. Initial audit used unsupported -V and was corrected to embedded UFTP version strings. Initial deployment script failed parsing before executing any remote mutation; corrected deployment succeeded.

uftp SHA256: 71d9c15497b3bd4286a2c60c9d37871a50b7032793eae8d4bf7b3b8977e015e3

uftpd SHA256: 0d7a31c6cbf783ad5ecced0bdce6705c30530a258fdf605d555c748827aa85b9

