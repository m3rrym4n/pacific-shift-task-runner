# Issue #84: spawn-time Codex CLI resolution

## Measurement

Measured on 2026-08-08 UTC with three disposable `node:22-bookworm-slim`
containers. Each container had a fresh writable layer and ran the same global
install that the proposed ephemeral entrypoint runs:

```sh
for run in 1 2 3; do
  docker run --rm -e RUN_NUMBER="$run" node:22-bookworm-slim sh -c '
    start=$(date +%s%N)
    npm install -g @openai/codex@latest >/tmp/npm.log 2>&1
    status=$?
    end=$(date +%s%N)
    version=$(codex --version 2>/dev/null || true)
    elapsed_ms=$(((end-start)/1000000))
    printf "run=%s elapsed_ms=%s exit=%s version=%s\n" \
      "$RUN_NUMBER" "$elapsed_ms" "$status" "$version"
  '
done
```

| Run | Install latency | Exit | Resolved version |
| --- | ---: | ---: | --- |
| 1 | 2,518 ms | 0 | 0.147.0 |
| 2 | 2,511 ms | 0 | 0.147.0 |
| 3 | 2,416 ms | 0 | 0.147.0 |

Mean: 2,482 ms. Minimum: 2,416 ms. Maximum: 2,518 ms. The measured cost is
consistently in the expected low-single-digit-second range and is negligible
relative to the task runtimes observed by the runner.

An earlier exploratory invocation placed `--env` after the image name, so its
run labels were empty. It still produced successful installs (2,781 ms,
2,512 ms, and 9,749 ms), but the correctly labelled reproduction above is the
authoritative result. The outlier is useful operational context: npm/network
latency is not guaranteed even though the repeatable sample was stable.

## Prototype

`codex_runner/entrypoint.spawn-time-spike.sh` is deliberately not connected to
the current long-lived image. It resolves a version before executing its runner
startup command, which guarantees the install precedes MCP registration and any
task invocation performed by that runner. It uses the unprivileged runner user's
`$HOME/.local` npm prefix, so spawn-time installation does not require changing
the image's `USER codex` security boundary.

The runner captures `CODEX_RESOLVED_VERSION` when an execution is created and
returns it as `codex_version` from `GET /result/{execution_id}`. This proves the
runner-result contract without changing Variflex's SQLite schema. During the
ephemeral-runner implementation, Variflex should persist this response field in
task metadata under the same `codex_version` name before removing the container.

## TTL proposal

Use a 24-hour TTL. Store a one-line resolved version in a small shared cache
mount at `/var/cache/codex-runner/latest-version`; do not mix operational cache
data into the Codex auth volume. Variflex/Dockhand should mount a dedicated
`pacific-shift-codex-version-cache` volume at `/var/cache/codex-runner` for each
ephemeral spawn.

On a cache hit younger than 86,400 seconds, install the exact cached version.
On a miss or expiry, resolve `npm view @openai/codex@latest version`, atomically
replace the cache file, and install that exact version. Always verify
`codex --version` matches before starting the runner. Atomic rename makes
concurrent reads safe; two simultaneous expiry refreshes may duplicate the npm
query but converge on a complete value, which is preferable to adding a lock
service for a daily operation.

This is simpler than the current drift pipeline: one small entrypoint, one
version file, no issue creation, image build, registry tag/prune, deployment,
or rollback machinery. It also limits fleet-wide version resolution to one
daily selection. It does **not** prove a newly published version is good; a bad
version can remain selected for the TTL. For implementation, retain the prior
cache value as `previous-version` and allow an operator to restore it (or set a
`CODEX_VERSION_OVERRIDE`) without rebuilding an image. Automatic promotion or
rollback is a follow-up policy decision, not part of this spike.
