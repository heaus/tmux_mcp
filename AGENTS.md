# Repository Guidelines

## Project Structure & Module Organization
Maintain a predictable tree so new agents and tmux adapters are easy to discover. Place production code in `src/tmux_mcp/`, mirrored tests in `tests/`, helper scripts in `scripts/`, and human-facing notes in `docs/`. Persist shared assets (sample tmux layouts, recorded sessions) under `assets/`. A healthy tree looks like:
```
.
├── src/tmux_mcp/agent.py
├── tests/test_agent.py
├── scripts/dev/launch.sh
└── docs/architecture.md
```
Keep agent manifests (`manifest.json`, `capabilities.yaml`) next to the module that serves them.

## Build, Test, and Development Commands
Standardize on a Python virtual environment: `python -m venv .venv` then `source .venv/bin/activate`. Install runtime dependencies from `requirements.txt` (commit it with exact pins): `pip install -r requirements.txt`. Use `pip install -r requirements-dev.txt` when hacking. Run the agent locally with `python -m tmux_mcp.agent --config config/manifest.json`. Smoke-test tmux integration via `scripts/dev/launch.sh session-name`, which should boot a disposable tmux session for manual checks.

## Coding Style & Naming Conventions
Target Python 3.11+. Format code with `black` (88 columns) and lint with `ruff`; wire both into `pre-commit`. Use snake_case for functions and variables, PascalCase for classes, and UPPER_SNAKE for constants. Keep module names short (`pane_router.py`, not `paneRouter.py`). Configuration files should be lowercase with hyphens (`config/default-manifest.json`).

## Testing Guidelines
Adopt `pytest` for unit and integration coverage. Name tests after the behaviours they validate: `test_pane_router_attaches_existing_session`. Mirror the package structure so every module has a peer test file. New features need tests that fail before the change. Aim for 90% branch coverage; add `pytest --cov=tmux_mcp --cov-report=term-missing` to ensure gaps are visible. Record manual tmux walkthroughs in `docs/testing-notes.md` when automation is not yet feasible.

## Commit & Pull Request Guidelines
Write commits in imperative mood (`Add pane attach retry`). Group related edits; avoid unrelated cleanup. Reference issues with `Refs #123` or `Fixes #123` in the footer. Pull requests must include: summary of the change, testing evidence (`pytest`, manual tmux run), screenshots or asciinema cast if UX changes, and clear rollback instructions. Tag another contributor for review before merging.

## Agent-Specific Notes
The MCP host expects deterministic capability discovery. Keep agent metadata (`config/manifest.json`) versioned and update the `README.md` whenever new tools or prompts are added. When introducing tmux bindings or RPC calls, document the tmux version requirements and gate experimental features behind a feature flag in `config/feature-flags.yaml`.
