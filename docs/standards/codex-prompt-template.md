# Pacific Shift Labs Codex Prompt Standard

Version: 1.3
Status: Active
Applies To: CrateSpy, Publisher, Task Runner, Selectr, and future Pacific Shift Labs projects

## 1. Purpose

This document defines the standard structure and operating rules for Codex implementation prompts used within Pacific Shift Labs projects.

The goals are to:

- Reduce implementation drift.
- Reduce scope creep.
- Improve consistency between projects.
- Improve reliability of automated development workflows.
- Ensure Codex works from documented requirements rather than assumptions.

## 2. Core Principles

### 2.1 GitHub Is The Source Of Truth

GitHub issues are authoritative.

If there is a conflict between:

- Prompt instructions
- Existing code
- Issue comments
- Issue acceptance criteria

The GitHub issue is the source of truth.

Codex should not invent requirements.

Codex should not expand scope beyond documented issue requirements.

### 2.2 Work One Issue At A Time

Codex should:

1. Read the issue.
2. Review the issue description, acceptance criteria, labels, comments, and related issues.
3. Inspect existing implementation.
4. Determine whether the issue is already partially or fully implemented.
5. Implement the smallest safe solution that satisfies the issue.
6. Add or update tests.
7. Run tests using Docker-based execution.
8. Commit.
9. Move to the next issue only after validation succeeds.

Avoid batching multiple unrelated issues together.

### 2.3 Smallest Safe Change

Prefer:

- Incremental implementation
- Minimal code changes
- Low-risk modifications
- Existing architecture reuse

Avoid:

- Framework rewrites
- Architecture redesign
- Premature optimization
- Large refactors unrelated to the issue

### 2.4 Milestones Define Scope

Milestones represent approved project scope.

If an idea belongs to another milestone:

- Document it.
- Create or update an issue if necessary.
- Do not implement it during the current milestone.

## 3. Task Runner Architecture Alignment

Task Runner's v1 scope is Codex-only:

```text
v1: Codex-only orchestrator + Codex runner container
        ↓
v2 (not yet scoped): Claude Code runner added to registry
```

Do not pull v2 work (Claude Code runner, multi-runner dispatch) into v1 implementation unless the GitHub issue explicitly requires it.

## 4. Environment Standards

### 4.1 Docker First

Pacific Shift Labs projects are developed and deployed using Docker.

Assume:

| Tool | Availability |
|------|--------------|
| Docker | Available |
| Docker Compose | Not guaranteed |
| pytest on host | Not guaranteed |
| npm on host | Not guaranteed |
| node on host | Not guaranteed |

The host operating system should be treated as an implementation detail.

The container environment is authoritative.

### 4.2 Validation Inside Containers

Whenever practical:

- Run tests inside containers.
- Run migrations inside containers.
- Run validation inside containers.
- Verify deployments using containers.

Do not assume host tooling exists.

### 4.3 Docker Command Preference

Prefer `docker` commands that do not require Docker Compose unless the repository or issue explicitly requires Compose.

Examples:

```bash
docker exec <container> pytest
```

```bash
docker run --rm <image> pytest
```

```bash
docker build -t <image> .
```

```bash
docker run --rm -p 6002:6002 --name <container> <image>
```

Use the repository's established Docker patterns rather than assuming a specific command.

## 5. Standard Codex Prompt Structure

Every substantial Codex prompt should use numbered sections.

Recommended structure:

1. Objective
2. Required Work Items
3. Execution Rules
4. Testing Requirements
5. Deployment Verification
6. Scope Guardrails
7. Success Criteria
8. Final Deliverable

## 6. Standard Codex Workflow

### 6.1 Read Issue

Review:

- Description
- Acceptance criteria
- Labels
- Comments
- Related issues

### 6.2 Inspect Code

Determine:

- Existing implementation
- Partial implementation
- Missing functionality

### 6.3 Implement

Implement only what is required to satisfy the issue.

### 6.4 Test

Run automated tests using the project's Docker-based workflow.

Do not assume `pytest` is installed on the host.

Do not assume Docker Compose is installed on the host.

### 6.5 Commit

Commit using issue references.

Example:

```text
#2 Add task queue and MCP tool endpoints
```

### 6.6 Repeat

Move to the next issue only after:

- Tests pass.
- Validation succeeds.
- Current issue requirements are satisfied.

## 7. Deployment Verification Standard

Before reporting completion:

1. Rebuild application containers.
2. Restart application containers.
3. Verify application startup.
4. Verify no startup exceptions are present in logs.
5. Verify database migrations complete successfully if applicable.
6. Verify core functionality.

Only after deployment verification succeeds should work be reported as complete.

## 8. Final Reporting Standard

Every Codex execution report should include:

- Completed work
- Test results
- Skipped work
- Known limitations
- Deployment verification results
- Container verification
- Database verification if applicable
- Readiness statement

Any failed command, failed test, failed build, failed push, or failed startup check must be disclosed in the final report, even if later resolved. Include the failed command, failure summary, fix applied, and passing retest command.

### 8.1 GitHub as Single Source of Truth for Reports

Write the full Final Report to a local file before using it anywhere (e.g. `/tmp/<issue-number>-report.md`). Do not construct the report as an inline shell string — inline strings containing backticks, quotes, or embedded newlines are a known source of corrupted PR descriptions.

Use that file for both:

- The PR description: `gh pr create --body-file <file>` or `gh pr edit <pr-number> --body-file <file>`
- A comment on the originating GitHub issue: `gh issue comment <issue-number> --body-file <file>`

Post the issue comment every time a task completes, fails, or is blocked — not only on success. GitHub is the single source of truth for what happened on any given task; do not rely on the report reaching the requester through any other channel.

### 8.2 Never Post Secrets to GitHub

Issue comments and PR descriptions are durable, and on public repositories, world-readable. Before writing any report, comment, PR body, or commit message:

- Never include actual token, key, password, or credential values, even partially or truncated.
- If confirming a credential is present or working, state only that fact (e.g. "GITHUB_TOKEN is set and authenticated") — never the value itself.
- Before including raw command output (environment dumps, config file contents, `auth.json` contents, API/curl responses, `gh auth token`, `docker exec ... env`, etc.), scan it for anything resembling a secret — long hex/base64 strings, JWTs, `Bearer ...` headers, or any key containing `token`, `key`, `password`, `secret` — and redact the value, replacing it with `<redacted>`.
- This applies to everything written to GitHub: commit messages, PR descriptions, PR comments, and issue comments — not only the final report.
- When in doubt, redact. A less detailed report is always preferable to one real leaked credential.

## 9. Scope Guardrail Standard

Every milestone execution prompt should explicitly define:

### 9.1 Allowed Work

List the specific categories of work permitted for the milestone.

### 9.2 Not Allowed Work

List later-milestone or out-of-scope categories that Codex must avoid.

### 9.3 Milestone Boundary Rule

If Codex discovers useful work outside the current milestone, it should document it and stop short of implementation.

## 10. Anti-Patterns

Avoid:

- Scope creep
- Architecture redesign during implementation
- Unrequested feature additions
- Rewriting working code without justification
- Implementing future milestone work
- Assuming host tooling exists
- Assuming Docker Compose exists
- Assuming `pytest` exists on the host
- Skipping tests
- Skipping deployment verification
- Posting secrets, tokens, or credential values to GitHub comments, PR bodies, or commit messages
- Constructing PR bodies or issue comments as inline shell strings instead of writing the report to a file first

## 11. Pacific Shift Labs Philosophy

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
