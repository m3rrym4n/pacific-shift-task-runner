#!/bin/sh
set -eu

eval "$(python3 -m codex_runner.configure_git)"
python3 -m codex_runner.configure_mcp
exec "$@"
