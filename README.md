&lt;img width=&quot;1536&quot; height=&quot;1024&quot; alt=&quot;ChatGPT Image Aug 10, 2026, 01_29_08 PM&quot; src=&quot;https://github.com/user-attachments/assets/22c04ebb-7591-4c45-bf90-92fb9812351f&quot; /&gt;

# Variflex

Variflex is a self-hosted orchestrator that dispatches GitHub issues to isolated, ephemeral AI coding-agent runners (Codex today) and reports structured results back. It's built for sustained, unattended runs — not just one-off dispatches — which means it treats things like rate limits, restarts, and partial failures as first-class cases rather than edge cases to work around later.

## Why this exists

A survey of the existing open-source AI coding agent orchestrator landscape — [Bernstein](https://github.com/chernistry/bernstein), [OpenHands](https://github.com/All-Hands-AI/OpenHands), [Microsoft Conductor](https://github.com/microsoft/conductor), and others — found that none of them document handling coding-agent rate-limit exhaustion as a first-class case. In practice, that's the first thing that actually happens on any sustained run: an agent burns through its quota mid-task, and most tooling either loses the work, requires a manual restart, or has no structured way to know when it's safe to resume.

Variflex exists to solve that specific gap, and everything else follows from it:

- **Per-repository FIFO queues** — one task container per repository at a time, with a configurable global container cap across all repositories. Shown in the diagram above as the three colored queues feeding into the dispatcher.
- **Quota-exhaustion detection and resume** — a structured rate-limit response with an ISO 8601 reset time returns the interrupted task to the head of its queue and automatically resumes the *same session* once the quota clock allows. No lost work, no manual restart.
- **Session-durable queues** — pending order, halt state, and the active task reference all survive an orchestrator restart. A crash or redeploy doesn't lose track of what was running or what's next.
- **Human-controlled promotion** — every dispatch traces back to a GitHub issue. Work lands on `dev` automatically; `main`/stable only moves forward on an explicit human decision. Dev and main run as isolated environments, never sharing state.
- **Ephemeral, isolated runners** — every admitted task gets a freshly created runner container (via Dockhand) sharing only a persistent Codex auth volume, then is torn down after the task completes, fails, times out, or hits quota. No long-lived runner accumulating state or drift between tasks.

## Repository layout

- [`orchestrator/`](orchestrator/) — FastAPI/SQLite dispatch, queue, monitoring, and reporting service. Exposes the MCP tools used to actually drive dispatch (`run_task`, `list_tasks`, `clear_runner_halt`, `cancel_queued_task`) and the REST endpoints (`/api/tasks`, `/api/queues`, `/api/repos`) used by internal tooling and dashboards.
- [`runner/`](runner/) — independently built Codex execution shim used by dispatched containers. Implements the runner contract (`/execute`, `/resume`, `/status/{id}`, `/result/{id}`) the orchestrator dispatches against.

Each directory owns its Dockerfile, dependencies, tests, and build context. Changes are filtered by path in CI so one image does not rebuild when only the other component changes.

## Building

Both components build independently from their own directory:

```bash
# Orchestrator
docker build \
  --build-arg "TASK_RUNNER_SOURCE_SHA=$(git rev-parse --short=7 HEAD)" \
  -t variflex:latest .

# Runner
docker build -t variflex-runner:latest runner/
```

Each has its own test image too:

```bash
docker build -t variflex:test -f Dockerfile.test . && docker run --rm variflex:test
docker build -t variflex-runner:test -f runner/Dockerfile.test runner/ && docker run --rm variflex-runner:test
```

Full run instructions — required environment variables, the repository registry, scheduled tasks, Ops Image checks, and the real production compose file — are in [`orchestrator/README.md`](orchestrator/README.md). Runner-side per-container configuration (model selection, MCP server registration) is in [`runner/README.md`](runner/README.md).

## Compatibility policy

The product, repository, images, and containers use the Variflex name. Existing `TASK_RUNNER_*` orchestrator environment variables and `CODEX_RUNNER_*` runner variables remain supported unchanged to avoid breaking deployed configuration. They are compatibility API names, not separate product names; a future removal would require an explicit migration issue.

See the component READMEs for configuration and build instructions.
