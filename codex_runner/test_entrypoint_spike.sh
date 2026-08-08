#!/bin/sh
set -eu

ENTRYPOINT="$(dirname "$0")/entrypoint.spawn-time-spike.sh"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

mkdir -p "$TMP_ROOT/bin" "$TMP_ROOT/cache"
cat > "$TMP_ROOT/bin/npm" <<'EOF'
#!/bin/sh
case "$1 $2" in
    "view @openai/codex@latest") echo "9.8.7" ;;
    "install --global") printf '%s\n' "$3" > "${TEST_INSTALL_LOG:?}" ;;
    *) exit 2 ;;
esac
EOF
cat > "$TMP_ROOT/bin/codex" <<'EOF'
#!/bin/sh
echo "codex-cli 9.8.7"
EOF
chmod +x "$TMP_ROOT/bin/npm" "$TMP_ROOT/bin/codex"

PATH="$TMP_ROOT/bin:$PATH" \
CODEX_VERSION_CACHE_DIR="$TMP_ROOT/cache" \
TEST_INSTALL_LOG="$TMP_ROOT/install-1" \
    "$ENTRYPOINT" sh -c 'test "$CODEX_RESOLVED_VERSION" = 9.8.7'

test "$(cat "$TMP_ROOT/cache/latest-version")" = "9.8.7"
test "$(cat "$TMP_ROOT/install-1")" = "@openai/codex@9.8.7"

# A fresh cache hit must not need npm view. Replace npm with install-only behavior.
cat > "$TMP_ROOT/bin/npm" <<'EOF'
#!/bin/sh
test "$1 $2" = "install --global"
printf '%s\n' "$3" > "${TEST_INSTALL_LOG:?}"
EOF
chmod +x "$TMP_ROOT/bin/npm"
PATH="$TMP_ROOT/bin:$PATH" \
CODEX_VERSION_CACHE_DIR="$TMP_ROOT/cache" \
TEST_INSTALL_LOG="$TMP_ROOT/install-2" \
    "$ENTRYPOINT" true
test "$(cat "$TMP_ROOT/install-2")" = "@openai/codex@9.8.7"
