# Capture Repair

- Parent review required before any new model run.
- Scope: repair passive capture persistence only; no protocol, radio, native, collector schema, or payload changes.
- Existing successful model remains independent at `model-20261110T171130Z/`.
- First action: read-only remote residual/JSON recovery probe for fl01/fl02/fl03/fl05/fl06/fl07/fl08/fl09.
- Repair: copy the reviewed `capture-heartbeats-batch.py` into a remote named measurement directory over management SSH; launch with `sudo -n sh -c 'exec /usr/bin/python3 -I /remote/path ... > /remote/path/raw.json 2> /remote/path/stderr'`, persist the real collector PID and cmdline; terminate only that verified collector PID; retrieve files in a separate SSH read.
- Verification sequence: local unchanged schema tests, then one short passive 5-second capture on all eight clients, each remote JSON readable with schema3 window, STOP/normal termination evidence, PACKET_STATISTICS metadata, and no residual collector PID.
- No model is allowed from this repair agent until parent reviews the short-capture evidence.
