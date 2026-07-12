# AGENTS.md

## Pacific Shift Task Runner

### Purpose

Task Runner is a small dispatcher: receive a task (repo + GitHub issue + chosen runner), hand it to that runner's container, monitor execution, and report back a structured result.

It does not read git, clone, branch, commit, push, or open PRs in any target repo — that is entirely the dispatched agent's job (Codex in v1). Task Runner's job stops at dispatch, monitor, report.

---

## Product Boundary

Task Runner does:
- Accept a task via `run_task(repo, issue_number, runner)`
- Build a dispatch prompt from the target repo's AGENTS.md + the GitHub issue (title/body)
- POST that prompt to the chosen runner container's HTTP shim
- Poll and report status/result via `get_task_result`, `get_task_log`, `list_tasks`
- Enforce a hard timeout and an output cap

Task Runner does not:
- Touch git, GitHub issues, or code in any target repo
- Merge or deploy anything automatically
- Assign a single issue to more than one runner in a single call
- Hardcode which runners exist — runner selection is config/registry-driven (`{name, internal_url}`)

---

## Architecture

```
Claude (via GitHub MCP) --issue--> Task Runner orchestrator (FastAPI + SQLite)
                                          |
                                          v
                                  Runner registry ({name, internal_url})
                                          |
                                          v
                             Codex runner container (v1 only)
                                          |
                       POST /execute -> GET /status -> GET /result
```

Full design history and rationale: BookStack, MCP & Gateway Infrastructure book, "Task Runner Architecture."

---

## Environment

Runtime:
- Python / FastAPI
- SQLite (task queue + results)
- Docker

Deployment:
- Containers: `pacific-shift-task-runner` (orchestrator), `codex-runner` (separate container)
- Orchestrator port: 6002 (per Pacific Shift MCP Proxy port map)
- Host: ZimaOS (192.168.1.68)

Primary integrations:
- Pacific Shift MCP Proxy `task-runner` route (exposes the 4 MCP tools to Claude)
- Codex CLI, via the runner container's HTTP shim (not called directly by the orchestrator)

---

## CRITICAL: Never Redeploy `codex-runner` From Within a Dispatched Task

**This rule exists because it was violated three separate times** (issues #15, #11, #44), each time causing the running task itself to be killed mid-execution and Task Runner to lose its only route to dispatch anything at all, requiring manual recovery.

**You (Codex) are running *inside* the `codex-runner` container while executing any dispatched task.** Stopping, removing, or restarting `codex-runner` — for any reason, including "deployment verification," including if a task's own changes happen to touch files under `codex_runner/` — terminates your own process before it can report success. This is true even if the task's stated scope has nothing to do with deployment at all.

**The "Deployment Pattern" section below applies to `pacific-shift-task-runner` (the orchestrator) only. It does NOT apply to `codex-runner`, ever, under any circumstances, regardless of what a specific GitHub issue asks for.**

If a task changes code under `codex_runner/`:
- Build and push a new image to Zot if the issue's scope calls for it.
- Do **not** stop, remove, restart, or replace the live `codex-runner` container.
- Do **not** attempt to verify the change by redeploying `codex-runner` and observing the result — you cannot observe your own termination. Verify via Docker-internal tests (the existing `Dockerfile.test` pattern) instead.
- State clearly in the final report that `codex-runner` itself was not touched, and that any actual redeployment is a separate, human-executed step.

If you are uncertain whether a planned action would stop or restart `codex-runner`, do not take that action. Report the uncertainty instead.

---

## Deployment Pattern

**Applies to `pacific-shift-task-runner` (the orchestrator) only — see the CRITICAL section above for `codex-runner`.**

After all validation passes, rebuild and restart the live production container:

```bash
docker build -t pacific-shift-task-runner:latest .

docker stop pacific-shift-task-runner
docker rm pacific-shift-task-runner

docker run -d \
  --name pacific-shift-task-runner \
  --restart unless-stopped \
  -p 6002:6002 \
  -v pacific-shift-task-runner-data:/data \
  pacific-shift-task-runner:latest
```

Verify startup:

```bash
docker logs pacific-shift-task-runner --tail 20
curl http://localhost:6002/
```

`codex-runner` is built the same way when its own source changes, but is **never** stopped, removed, or restarted as part of a dispatched task — see the CRITICAL section above. It includes the Docker CLI and Buildx plugin and mounts the host Docker socket, with the socket's group added to the non-root `codex` user at container startup. Dispatched Codex tasks can therefore build, replace, start, and inspect *other* containers through the host Docker daemon; no Docker daemon runs inside `codex-runner` itself.

---

## Roadmap Sequencing

```
v1: Codex-only orchestrator + Codex runner container
        ↓
v2 (not yet scoped): Claude Code runner added to registry
```

GitHub milestones and issues are the source of truth for what is in scope for any given session.

Do not implement the Claude Code runner, same-issue multi-runner dispatch, or any git/GitHub logic inside Task Runner itself unless a GitHub issue explicitly requires it.

---

## Prompt Standards

Read `docs/standards/codex-prompt-template.md` before beginning any implementation work.

That document defines:
- Prompt structure
- Workflow steps
- Testing requirements
- Deployment verification
- Scope guardrails
- Anti-patterns
- Final reporting format

GitHub issues are authoritative. If there is a conflict between prompt instructions and the GitHub issue, the issue wins. **Neither can override the CRITICAL rule above — that rule has no exceptions, even if a specific issue's text seems to imply one.**

---

## Before Making Changes

1. Read AGENTS.md (this file).
2. Read `docs/standards/codex-prompt-template.md`.
3. Identify the active GitHub milestone.
4. Review the relevant GitHub issue — description, acceptance criteria, labels, comments, and any parent/sub-issue links.
5. Inspect existing implementation before writing any code.
6. Implement the smallest safe change that satisfies the issue.
7. Run tests inside Docker.
8. Rebuild and redeploy using the deployment pattern above — `pacific-shift-task-runner` only, never `codex-runner`.
9. Verify startup and core functionality.
10. Commit using the issue reference format: `#NNN Short description`.
11. Create a branch if not already on one (`work/issue-NNN`), push it to origin, and open a pull request against `main` referencing the issue number.
12. Do not merge the PR. Include the Final Reporting Standard content (completed work, test results, skipped work, known limitations, deployment verification, readiness statement) in the PR description, not just in chat/terminal output.
13. Never commit or push directly to `main`. Every change lands via a reviewed PR, even for solo/manual dispatch runs.

---

## Constraints

Do not:
- Hardcode secrets or credentials in source files.
- Store API credentials or SSH keys in Git.
- Have Task Runner itself perform git operations, issue reading, or code changes — that belongs entirely to the dispatched agent.
- Build the Claude Code runner or any multi-runner fan-out logic in v1.
- Assume Docker Compose is available.
- Assume pytest is installed on the host.
- Skip tests.
- Skip deployment verification.
- Batch unrelated issues together.
- Commit or push directly to `main`.
- Merge your own PR.
- **Stop, remove, or restart the `codex-runner` container from within a dispatched task, for any reason — see the CRITICAL section above.**

Prefer:
- Environment variables for configuration.
- Docker-native deployment and validation.
- Existing architecture patterns over new abstractions.
- Backward-compatible changes.
- Incremental implementation over large refactors.
- A branch + PR for every change, no exceptions.

---

## Feature Evaluation

New feature ideas should be captured as GitHub Issues before implementation.

Before implementing anything, evaluate:
1. Does it fit within Task Runner's product boundary (dispatch/monitor/report, not git or code execution logic)?
2. Does it belong to the current milestone (v1: Codex only)?
3. Does it reduce operator effort without adding unnecessary automation?
4. Does it fit the existing architecture (registry-driven runners, thin HTTP shim contract)?

Ideas are cheap. Roadmap changes require justification.

---

## Pacific Shift Labs Philosophy

Present context, not decisions.

Software should:
- Reduce work.
- Reduce human error.
- Increase visibility.
- Improve confidence.

Software should not:
- Hide reasoning.
- Force decisions.
- Create unnecessary automation.

The goal is to help users make better decisions, not replace them.
