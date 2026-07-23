#!/bin/bash
# -*- coding: utf-8 -*-
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$SCRIPT_DIR/issue55_57_terminal_demo.sh" server "$@"
