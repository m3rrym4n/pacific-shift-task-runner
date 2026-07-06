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

## Deployment Pattern

After all validation passes, rebuild and restart the live production container(s):

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

The Codex runner container is built and deployed the same way, under its own name and internal port, and registered in the orchestrator's runner registry.

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

GitHub issues are authoritative. If there is a conflict between prompt instructions and the GitHub issue, the issue wins.

---

## Before Making Changes

1. Read AGENTS.md (this file).
2. Read `docs/standards/codex-prompt-template.md`.
3. Identify the active GitHub milestone.
4. Review the relevant GitHub issue — description, acceptance criteria, labels, comments, and any parent/sub-issue links.
5. Inspect existing implementation before writing any code.
6. Implement the smallest safe change that satisfies the issue.
7. Run tests inside Docker.
8. Rebuild and redeploy using the deployment pattern above.
9. Verify startup and core functionality.
10. Commit using the issue reference format: `#NNN Short description`

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

Prefer:
- Environment variables for configuration.
- Docker-native deployment and validation.
- Existing architecture patterns over new abstractions.
- Backward-compatible changes.
- Incremental implementation over large refactors.

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
