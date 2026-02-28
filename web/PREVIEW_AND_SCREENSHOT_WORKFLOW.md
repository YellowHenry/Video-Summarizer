# Frontend Preview And Screenshot Workflow

Last updated: February 28, 2026

## What this gives you

- Local preview server for manual UI checks.
- Fast auto-verify test loop (`@verify`).
- Deterministic screenshot regression checks.
- Request-scoped test runs (`@request`).
- Optional persisted session state between test runs.

## One-time setup

From repo root:

```bash
cd web
npm ci
npx playwright install
```

## Daily commands

From `web/`:

```bash
npm run preview:start
```

- Starts the app at `http://127.0.0.1:5173` for manual interaction.

```bash
npm run preview:verify
```

- Runs fast verification tests tagged `@verify` (screenshots + DOM + interaction checks).

```bash
npm run test:e2e:shots
```

- Runs the full e2e screenshot/flow suite against existing baselines.

```bash
npm run test:e2e:update
```

- Rebuilds screenshot baselines when intentional UI changes are made.

## Request-scoped workflow

Use these when working on a specific request:

```bash
npm run test:e2e:request
```

- Runs only tests tagged `@request`.

```bash
npm run test:e2e:request:update
```

- Updates baselines only for `@request` tests.

## Session persistence

- Tests can write storage state to `web/.auth/storage-state.json`.
- Playwright auto-loads that file when it exists.
- This helps with login/setup-heavy flows.

To reset persisted state:

```bash
npm run preview:session:clear
```

## Where outputs go

- Screenshot baselines: `web/tests/e2e/visual.spec.ts-snapshots/`
- Failure artifacts: `web/test-results/`
- HTML report: `web/playwright-report/`

## Suggested edit loop

1. Start preview: `npm run preview:start`
2. Make frontend changes.
3. Run quick checks: `npm run preview:verify`
4. If visuals changed intentionally: `npm run test:e2e:update`
5. Run full check: `npm run test:e2e:shots`
