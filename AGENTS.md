# Repository Guidelines

## Project Structure & Module Organization
Keep runtime code under `src/tmux_mcp/` and mirror unit tests in `tests/`. Place helper scripts in `scripts/` and human-facing docs in `docs/`. Persist reusable assets, recordings, or sample layouts in `assets/`. Store agent manifests beside the modules they describe, e.g. `.cursor/manifest.json`.

## Build, Test, and Development Commands
Create a local environment with `python -m venv .venv` followed by `source .venv/bin/activate`. Install runtime deps using `pip install -r requirements.txt`; add development extras via `pip install -r requirements-dev.txt`. Launch the agent with `python scripts/start_mcp_agent.py --config-dir .cursor`. Use `python scripts/start_mcp_agent.py --session session-name --window agent` for a smoke test that boots a disposable tmux session.

## Coding Style & Naming Conventions
Target Python 3.11+. Format with `black` (88 columns) and lint with `ruff`; wire them through `pre-commit`. Use snake_case for functions and variables, PascalCase for classes, and UPPER_SNAKE for constants. Prefer short, hyphenated config filenames such as `config/default-manifest.json`. Add comments only when they clarify non-obvious logic.

## Testing Guidelines
Adopt `pytest` for both unit and integration coverage. Mirror source modules with tests named after the behavior under test, e.g. `tests/test_pane_router_attaches_existing_session.py`. Run `pytest --cov=tmux_mcp --cov-report=term-missing` and keep branch coverage near 90%. Capture any manual tmux walkthroughs in `docs/testing-notes.md`.

## Commit & Pull Request Guidelines
Write commits in imperative mood, such as "Add pane attach retry". Group related changes and reference issues with `Fixes #123` or `Refs #123` in footers. Pull requests must summarize the change, document test evidence (`pytest`, tmux smoke run), attach relevant screenshots or recordings, and provide rollback notes. Request review from another contributor before merging.

## Security & Configuration Tips
Rely on OpenSSH plus tmux; avoid proprietary dependencies. Keep `.cursor/manifest.json` and `.cursor/feature-flags.yaml` in sync with new capabilities, and document tmux version requirements in `README.md`. Secure any stored credentials via the system keyring, and rotate audit logs that capture agent activity.
