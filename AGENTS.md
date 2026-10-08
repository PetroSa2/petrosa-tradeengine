# Agent instructions: petrosa-tradeengine

Production-ready cryptocurrency order execution system with advanced risk management and Binance integration.

Ecosystem rules (data pillars, commit and PR process, wording, memory) are in the umbrella [AGENTS.md](https://github.com/PetroSa2/petrosa/blob/main/AGENTS.md); this file covers this repository only. Only statements that can be checked against the repository are listed as facts.

## Commands (from the Makefile)

| Command | Purpose |
|---|---|
| `make setup` | install dependencies (see the Makefile for details) |
| `make lint` | run the linters (see the Makefile for details) |
| `make format` | format the code (see the Makefile for details) |
| `make type-check` | run the type checker (see the Makefile for details) |
| `make test` | run the test suite (see the Makefile for details) |
| `make unit` | run unit tests (see the Makefile for details) |
| `make integration` | run integration tests (see the Makefile for details) |
| `make e2e` | run end-to-end tests (see the Makefile for details) |
| `make security` | run security scans (see the Makefile for details) |
| `make pipeline` | run the full local CI pipeline (see the Makefile for details) |
| `make pre-commit` | run the pre-commit hooks (see the Makefile for details) |
| `make test-quality` | check that tests contain assertions (see the Makefile for details) |

Run the local pipeline or at least lint and tests before opening a pull request.

## Facts

- Python: `requires-python = ">=3.11"`; `.python-version` is `3.11.9`.
- Lint and format: ruff (config in `ruff.toml`).
- Type checking: mypy (config in `mypy.ini`).
- Tests: pytest, in `tests/`; the coverage floor is 40%.
- `make test-quality` checks that tests contain assertions.
- Container image: built from `Dockerfile`.
- Instrumentation uses the internal `petrosa-otel` package.
- CI workflows: `.github/workflows/ci-checks.yml`, `.github/workflows/deploy.yml`, `.github/workflows/manual-deploy.yml`.

## Layout

Python packages at the top level: `contracts/`, `shared/`, `tradeengine/`. Also `tests/`, `docs/` and `scripts/` where they exist.

## Rules (policy)

- Do not add database drivers or connections to this service. Read and write data through the data-manager API.
- Commits use Conventional Commits; branches are `{type}/{issue-number}-{slug}`; a PR body contains `Closes #N`; never merge with `--admin`.
- Text that leaves the repository (PR titles and bodies, commit messages, code comments) uses generic roles such as Agentic Developer and never names the upstream workflow engine or its personas.
- Do not commit logs, drafts, scratch files or generated working notes. GitHub and the memory server are the record.
