# Selected Validation Final Two

Independent batch root: `selected-validation-final-two-after-all-powered`.

The fixed topology is `fl00` plus `fl01`, `fl02`, `fl03`, `fl05`, `fl06`, `fl07`, `fl08`, `fl09`, and `fl10`; `fl04` remains excluded. A read-only full-ten management/actual audit and readiness gate passed before this batch. A separate read-only SSH attempt to `fl04` returned `No route to host`; no service, management-network, configuration, install, or measurement action was made there.

The batch is frozen to source commit `1cd6264d5d6be660998ce60f3a349063009ecfa7`, release root `/srv/wfb-ng-fl/releases/heartbeat-1cd6264d5d6b-20261109T083151Z`, native SHA `44d6bbf500f51df4d0b3ae8cd7a53c4a45cb55b09ca66cb5217e7ac75674feb2`, communication bundle SHA `ab8697fe4573ac3c1088dd11022a1fd75a25cf13752d2717de02e6aa2e625e9e`, and model size/SHA `44770302` / `4b55cee2f0bdcfbbf610167a505efee0e2c50e3306fed02bda3613807c234e96`.

The owner performs a bounded all-ten new-boot admission first. Boot IDs are captured from the current restart and checked for per-host continuity; no historical boot ID is required. USB device path, MAC, and interface remain hardware evidence, while link speed is recorded rather than treated as a standalone failure. Only an initial `iw` baseline mismatch can enter the existing one-time resident-service recovery path.

Cache maintenance, if needed, is limited to the two exact regular cache files on `fl02`. PackageKit transaction output is accepted only for a correctly parsed `ao 0`; APT/dpkg locks use POSIX `fcntl.lockf` with `O_RDWR`. The service is stopped only while idle and restored to its original active/enabled state in `finally`.

The worker precreates `setup/`, configures FEC 8/12 with server grant/guard `30/5` once, then performs two independent model-only runs at channel 157, HT40, DL3/UL4, UFTP 12000, restoring channel 149 after each run. Each run requires the runner's original transfer, cleanup, and restore flags, independent nine-file size/SHA evidence, exact cleanup, and full-ten/nine-READY restoration. A first failure stops the batch.
