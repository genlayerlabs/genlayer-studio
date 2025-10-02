# Repository Guidelines

## Project Structure & Module Organization
- `backend/`: Python services for consensus, rollup, and RPC endpoints; FastAPI bootstraps in `asgi.py` and `uvicorn_config.py`.
- `frontend/`: Vite + Vue 3 client with TypeScript sources in `src/`, assets in `public/`, and Vitest specs under `test/`.
- `tests/`: Pytest suites split by scope (`unit/`, `integration/`, `e2e/`, `load/`) with fixtures and factories in `common/`.
- `hardhat/`: Solidity scripts used when simulating L1/L2 interactions through the Hardhat profile.
- `docker/`, `docker-compose*.yml`, and `scripts/`: container orchestrators and developer utilities used in CI pipelines.

## Build, Test, and Development Commands
- `cp .env.example .env && docker compose up`: launches the full stack; add `-d` for background runs.
- `docker compose up jsonrpc webrequest ollama database-migration postgres`: backend services only, ideal when pairing with a hot-reload frontend.
- `cd frontend && npm install && npm run dev`: start the Vue dev server on port 5173.
- `cd frontend && npm run build`: perform type-checking and emit production assets in `dist/`.
- `pytest`: execute Python suites; layer filters with `-k` or `-m` to target specific modules.

## Coding Style & Naming Conventions
- Target Python 3.12, use 4-space indentation, and rely on Black via pre-commit for formatting consistency.
- Vue/TypeScript code follows the ESLint + Prettier toolchain; prefer PascalCase components, camelCase stores and utilities, and kebab-case Vue file names.
- Align filenames with their behavior (`validators/llm_validator.py`, `frontend/src/modules/network/useNetworkStore.ts`) and mirror that pattern in tests.

## Testing Guidelines
- Place new backend tests in the closest scope folder and name them `test_<feature>.py` so Pytest auto-discovers them.
- Frontend unit tests live in `frontend/test/unit`; run `npm run test` during development and `npm run coverage` for V8 reports.
- End-to-end coverage relies on Mocha + Selenium; run `npm run test:e2e` against a `npm run preview` instance before opening PRs.
- Share fixtures or sample payloads through `tests/common` to keep validator and RPC scenarios deterministic.

## Commit & Pull Request Guidelines
- Write Conventional Commits (`feat:`, `fix:`, `refactor:`); the commit-msg hook validates the prefix and scope.
- Install hooks with `pre-commit install`, then run `pre-commit run --all-files` before pushing to catch formatting and lint issues early.
- Open PRs from topic branches, link the related GitHub issue, and fill the PR template, including screenshots for UI adjustments and manual test notes.
- Ensure CI checks succeed, respond to reviewer feedback promptly, and prefer squash merges to keep history tidy.
