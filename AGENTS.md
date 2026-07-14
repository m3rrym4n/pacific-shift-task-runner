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
- Branches: `dev` (auto-deployed, see CI/CD below), `main` (human-promoted only)
- Containers: `pacific-shift-task-runner-dev` (dev, port 6004), `pacific-shift-task-runner` (production/main, port 6002), `codex-runner` (separate container, not part of this branch model — see CRITICAL section below)
- Orchestrator port: 6002 production / 6004 dev (per Pacific Shift MCP Proxy port map)
- Host: ZimaOS (192.168.1.68)

Primary integrations:
- Pacific Shift MCP Proxy `task-runner` route (exposes the 4 MCP tools to Claude)
- Codex CLI, via the runner container's HTTP shim (not called directly by the orchestrator)

---

## CRITICAL: Never Redeploy `codex-runner` From Within a Dispatched Task

**This rule exists because it was violated three separate times** (issues #15, #11, #44), each time causing the running task itself to be killed mid-execution and Task Runner to lose its only route to dispatch anything at all, requiring manual recovery.

**You (Codex) are running *inside* the `codex-runner` container while executing any dispatched task.** Stopping, removing, or restarting `codex-runner` — for any reason, including "deployment verification," including if a task's own changes happen to touch files under `codex_runner/` — terminates your own process before it can report success. This is true even if the task's stated scope has nothing to do with deployment at all.

**`codex-runner` is not part of the dev/main deploy pipeline described below, and is never deployed automatically by anything.** It does NOT apply here, ever, under any circumstances, regardless of what a specific GitHub issue asks for.

If a task changes code under `codex_runner/`:
- Build and push a new image to Zot if the issue's scope calls for it.
- Do **not** stop, remove, restart, or replace the live `codex-runner` container.
- Do **not** attempt to verify the change by redeploying `codex-runner` and observing the result — you cannot observe your own termination. Verify via Docker-internal tests (the existing `Dockerfile.test` pattern) instead.
- State clearly in the final report that `codex-runner` itself was not touched, and that any actual redeployment is a separate, human-executed step.

If you are uncertain whether a planned action would stop or restart `codex-runner`, do not take that action. Report the uncertainty instead.

---

## CI/CD — dev is fully automated, do not deploy `pacific-shift-task-runner`/`-dev` manually

Deployment to `pacific-shift-task-runner-dev` is handled entirely by an automated pipeline, not by you. Once your PR is reviewed and merged into `dev` by a human (see Constraints below — you never merge your own PR), the push itself triggers `.github/workflows/dev-build-deploy.yml`: build via the self-hosted GitHub Actions runner and BuildKit → push to the local Zot registry → deploy `pacific-shift-task-runner-dev` through Dockhand's REST API (the shared `pacific-shift-ci` reusable workflow) → verify → automatic rollback to the previous working container if verification fails.

**Do not run `docker build`, `docker stop`, `docker rm`, or `docker run` against `pacific-shift-task-runner-dev` yourself, and do not use the Docker socket to redeploy it.** The pipeline already does this. Your job for `dev` ends at a clean, tested, merged PR.

Before merging, you may still build the image locally to confirm it builds cleanly and run the test suite inside Docker (see Before Making Changes below) — that verification is still yours. What changed is *deploying* the result: that step now belongs entirely to the pipeline.

If the automated pipeline appears unavailable (e.g. the GitHub Actions run doesn't start, or fails for infrastructure reasons unrelated to your change), do not fall back to a manual Docker-socket deploy of `pacific-shift-task-runner-dev`. Report this in your final summary instead and stop — a human needs to look at the pipeline itself, not have it silently bypassed.

**Production `pacific-shift-task-runner` (on `main`) remains entirely manual, human-promoted only.** There is no automated pipeline for `main`. Never deploy `main` yourself under any circumstances — see Constraints below.

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
7. Run tests inside Docker, and confirm the image still builds cleanly.
8. Commit using the issue reference format: `#NNN Short description`.
9. Create a branch if not already on one (`work/issue-NNN`), push it to origin, and open a pull request against **`dev`** (not `main`) referencing the issue number.
10. Do not merge the PR. Include the Final Reporting Standard content (completed work, test results, skipped work, known limitations, deployment verification, readiness statement) in the PR description, not just in chat/terminal output. Do not deploy `pacific-shift-task-runner-dev` yourself — the merge itself, once a human approves it, is what triggers the automated pipeline. See CI/CD above.
11. If the automated pipeline is unavailable for infrastructure reasons, report this in your final summary and stop — do not fall back to a manual Docker-socket deploy.
12. Never commit or push directly to `dev` or `main`. Every change lands via a reviewed PR, even for solo/manual dispatch runs.

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
- Commit or push directly to `dev` or `main`.
- Merge your own PR.
- Manually deploy, stop, remove, or recreate `pacific-shift-task-runner-dev` for any reason — this is fully automated; see CI/CD above.
- Deploy production `pacific-shift-task-runner` (`main`) under any circumstances — human-only, entirely manual.
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
