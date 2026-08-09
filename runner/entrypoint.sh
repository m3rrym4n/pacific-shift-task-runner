#!/bin/sh
set -eu

python3 -m codex_runner.configure_mcp
exec "$@"
