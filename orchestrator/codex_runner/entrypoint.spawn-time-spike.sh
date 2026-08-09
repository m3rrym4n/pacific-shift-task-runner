#!/bin/sh
set -eu

# Issue #84 spike only: this is a copy/prototype for ephemeral runner startup.
# It is intentionally not wired into the current long-lived runner image.
CACHE_DIR="${CODEX_VERSION_CACHE_DIR:-/var/cache/codex-runner}"
CACHE_FILE="${CACHE_DIR}/latest-version"
CACHE_TTL_SECONDS="${CODEX_VERSION_CACHE_TTL_SECONDS:-86400}"
INSTALL_PREFIX="${CODEX_INSTALL_PREFIX:-${HOME}/.local}"

mkdir -p "$CACHE_DIR" "$INSTALL_PREFIX"
export NPM_CONFIG_PREFIX="$INSTALL_PREFIX"
export PATH="$INSTALL_PREFIX/bin:$PATH"
now="$(date +%s)"
resolved_version=""

if [ -f "$CACHE_FILE" ]; then
    cache_mtime="$(stat -c %Y "$CACHE_FILE")"
    cache_age="$((now - cache_mtime))"
    if [ "$cache_age" -ge 0 ] && [ "$cache_age" -lt "$CACHE_TTL_SECONDS" ]; then
        resolved_version="$(sed -n '1p' "$CACHE_FILE")"
    fi
fi

if [ -z "$resolved_version" ]; then
    resolved_version="$(npm view @openai/codex@latest version)"
    cache_tmp="${CACHE_FILE}.$$"
    printf '%s\n' "$resolved_version" > "$cache_tmp"
    mv "$cache_tmp" "$CACHE_FILE"
fi

install_started="$(date +%s)"
npm install --global "@openai/codex@${resolved_version}"
install_finished="$(date +%s)"

actual_version="$(codex --version | awk '{print $NF}')"
if [ "$actual_version" != "$resolved_version" ]; then
    echo "resolved Codex version ${resolved_version}, but installed ${actual_version}" >&2
    exit 1
fi

export CODEX_RESOLVED_VERSION="$actual_version"
echo "Codex CLI ${actual_version} ready in $((install_finished - install_started))s" >&2

# In the ephemeral image this command is the runner startup. Its subsequent
# MCP registration and task invocation therefore cannot run before resolution.
exec "$@"
