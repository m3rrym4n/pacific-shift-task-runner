# Variflex

Variflex dispatches GitHub issue work to isolated coding-agent runners and reports structured results. This repository is the single source of truth for the product:

- [`orchestrator/`](orchestrator/) — FastAPI/SQLite dispatch, queue, monitoring, and reporting service.
- [`runner/`](runner/) — independently built Codex execution shim used by dispatched containers.

Each directory owns its Dockerfile, dependencies, tests, and build context. Changes are filtered by path in CI so one image does not rebuild when only the other component changes.

## Compatibility policy

The product, repository, images, and containers use the Variflex name. Existing `TASK_RUNNER_*` orchestrator environment variables and `CODEX_RUNNER_*` runner variables remain supported unchanged to avoid breaking deployed configuration. They are compatibility API names, not separate product names; a future removal would require an explicit migration issue.

See the component READMEs for configuration and build instructions.
