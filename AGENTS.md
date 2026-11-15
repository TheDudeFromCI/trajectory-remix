# Repository guidance for agents

For codebase maintenance sweeps, read [docs/MAINTENANCE.md](docs/MAINTENANCE.md).
It contains the review checklist, subsystem invariants, validation commands, and
handoff/PR expectations. Use [docs/TESTING.md](docs/TESTING.md) for test coverage
details.

## Code quality and formatting

- **Prefer small modules.** Put new functionality in new, focused modules (one
  clear responsibility each) rather than extending the large existing ones such
  as `compiler.py`, `authoring.py`, `validation.py` or `server.py`. Keep new
  functions short and single-purpose. Existing code is not to be refactored or
  split as part of unrelated work; only extract when a change genuinely needs it.
- **Lint must pass.** Run `ruff check .` before committing (`pip install -e ".[lint]"`).
  Rules are configured in `pyproject.toml`. Fix findings rather than adding
  ignores; if an ignore is unavoidable, scope it to the line or file and explain why.
- **Formatting.** The codebase is formatted with `ruff format` (100 columns) and
  imports are sorted by ruff. Run `ruff format .` and `ruff check --fix .` before
  committing; CI enforces `ruff format --check .`. Keep formatting-only changes
  out of feature commits.
- **Tests accompany changes.** Add or adapt a regression test with each fix or
  feature, in a focused test file rather than appending to very large ones.
- **CI.** `.github/workflows/ci.yml` runs lint, format, Python tests on
  3.11–3.13, graph DOM tests, browser workflow tests, and a wheel build/install check
  (`scripts/check_package.py`). Keep these green and mirror any new validation
  command in `docs/MAINTENANCE.md`.
