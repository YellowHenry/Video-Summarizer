# Frontend Screenshot Plan (Immediate, Repo-Specific)

Last updated: February 28, 2026

## 1) Goal

Add a fast way to capture before/after screenshots of the web UI for frontend change review, then iterate frontend edits until the request-specific screenshot test passes.

Also add a local preview and auto-verification workflow with equivalent capabilities:
- easy server start/stop
- fast manual browser preview
- automated interaction checks after edits
- optional persisted session state between runs

This is not a custom "agent runs" project. No new backend services, no queue features, no cloud executor.

## 2) Scope

### In scope
- Deterministic screenshot capture using the existing Playwright setup in `web/`.
- Optional baseline-vs-current compare flow for visual diff review.
- Local-first workflow that can run immediately.
- Local preview workflow:
  - start/stop dev servers through repo scripts
  - open browser preview quickly for interactive checks
  - run automated verify passes after edits (screenshots + DOM + interactions)
  - persist cookies/local storage between runs when needed
  - keep startup commands/ports in repo config and scripts

### Out of scope
- Video recording pipeline.
- New backend API endpoints.
- New DB models or migrations.
- New Cloud Run/VM services.
- Building a custom in-app preview panel.
- Building custom session persistence infrastructure for preview state.

## 3) MVP (Do This First, Same Day)

### Files to edit
- `web/tests/e2e/app.spec.ts`
  - Add one or more stable screenshot tests (or a dedicated new spec file if cleaner).
  - Use `page.goto("/")` and `expect(page).toHaveScreenshot(...)`.
  - Stabilize dynamic areas where needed (time-based text/loading states) before snapshot.

- `web/playwright.config.ts`
  - Ensure screenshot snapshots are stable across local runs:
    - fixed viewport
    - fixed device scale factor (if needed)
    - consistent headless mode
  - Add/confirm `webServer` config for automated start during verification runs.
  - Add separate projects/tags if needed for quick verify vs full suite.
  - Add optional `storageState` wiring for persisted session flows.
  - Keep config minimal; do not add heavy trace/video defaults.

- `web/package.json`
  - Add lightweight scripts:
    - `test:e2e:update` for baseline refresh
    - `test:e2e:shots` for comparison runs
    - `preview:start` to run local dev server for preview
    - `preview:verify` to run fast verify checks after edits
    - `preview:session:clear` to clear persisted local session state

- `.gitignore` (optional, decision-based)
  - If you do not want local result artifacts committed, ignore:
    - `web/test-results/`
    - `web/playwright-report/`
    - `web/.auth/` (if session state files are local-only)
  - Keep Playwright snapshot baselines committed if using screenshot assertions as regression tests.

### MVP acceptance criteria
- One command updates screenshot baselines.
- One command re-runs checks and flags visual changes.
- Results are easy to inspect in Playwright output.
- One command starts preview server(s), and app can be opened for manual interaction.
- One command runs fast auto-verification checks after edits (screenshots + interaction checks).
- Session persistence behavior (and reset path) is documented for login-heavy flows.

## 4) Phase 2 (Optional, Short Follow-Up)

If MVP is useful, add a small CI job that runs screenshot checks and uploads artifacts on failure.

### Files to edit (optional)
- `.github/workflows/*` (or your existing CI workflow file)
  - Run `npm ci` + `npm run test:e2e:shots`
  - Upload `web/playwright-report/` + `web/test-results/` on failure

### Phase 2 acceptance criteria
- PRs show visual failures with attached artifacts.
- No required cloud infra changes.

## 5) Recommended Commands

From repo root:

```bash
cd web
npm run test:e2e:update   # refresh baseline screenshots
npm run test:e2e:shots    # compare against baseline
npm run preview:start     # run local preview server
npm run preview:verify    # quick verify checks after edits
```

If your setup still uses only `test:e2e`, wire scripts like this:

```json
{
  "scripts": {
    "test:e2e": "playwright test",
    "test:e2e:update": "playwright test --update-snapshots",
    "test:e2e:shots": "playwright test"
  }
}
```

## 6) Cost and Runtime Impact

- Near-zero new cloud cost.
- No always-on services added.
- Runtime cost is local dev/CI execution time only.

## 7) Feasibility Verdict

Highly feasible right now for this repository because Playwright already exists in `web/`.

Estimated implementation time:
- MVP: about 30 to 90 minutes.
- Optional CI follow-up: about 30 to 60 minutes.

## 8) Explicit Non-Goals (This Plan)

- No custom autonomous coding agent infrastructure.
- No task orchestration backend.
- No long rollout with multi-month phases.

This plan is intentionally narrow: screenshot testing plus local preview/auto-verify workflow only.

## 9) Request-Driven Iteration Loop (Required)

Use this loop for each frontend request so changes are validated against what the user asked for, not just a generic screenshot diff.

1. Translate the request into explicit acceptance points.
   - Example format:
     - page/view to open
     - state/data needed
     - exact UI expectation (text/layout/spacing/visibility)
2. Add or update a targeted Playwright test for that request.
   - Include at least one deterministic screenshot assertion.
   - Add focused DOM assertions for key requirements where possible.
3. Run only the targeted request test first.
   - `npm run test:e2e:shots -- --grep @request`
4. If it fails, inspect the visual diff, patch frontend code, and re-run.
5. Repeat until the request test is green.
6. Run the broader screenshot suite to catch collateral regressions.
   - `npm run test:e2e:shots`
7. Only mark complete when:
   - request-targeted test passes
   - no unintended visual regressions in related screens

## 10) Concrete Additions for This Loop

### `web/tests/e2e/app.spec.ts` (or dedicated request specs)
- Use tagged tests for request-scoped runs, e.g. `@request`.
- Keep scenarios deterministic: fixed viewport, stable seeded state, avoid time-sensitive UI.

### `web/package.json`
- Keep these scripts:
  - `test:e2e:update`
  - `test:e2e:shots`
- Add optional request-scoped helpers:
  - `test:e2e:request`: `playwright test --grep @request`
  - `test:e2e:request:update`: `playwright test --grep @request --update-snapshots`

### Optional guardrail
- Set a practical iteration cap per request (for example, 5-10 edit/test cycles) before escalating for requirement clarification.

## 11) Local Preview + Auto-Verify Workflow (Added)

Use this flow in addition to screenshot assertions:

1. Start and stop preview server(s) with repo scripts.
   - Define `preview:start` (and optional `preview:stop`) in `web/package.json`.
   - Keep commands and ports explicit in scripts/config.
2. Open the running app for manual interaction checks.
   - Use normal browser preview for navigation, form fills, and state checks.
   - Validate request-specific acceptance points from Section 9 while server is running.
3. Run automatic verify checks after edits.
   - Add a fast Playwright subset (for example, tagged `@verify`) that:
     - takes deterministic screenshots
     - asserts key DOM/text expectations
     - clicks/fills critical form paths
   - Run this subset with `npm run preview:verify` before broader suite runs.
4. Persist session state across restarts when needed.
   - Use Playwright `storageState` for logged-in or setup-heavy flows.
   - Keep auth/session artifacts local by default (for example, `web/.auth/`).
   - Provide a reset script (`preview:session:clear`) for clean-room reproductions.
5. Keep configuration lightweight and editable.
   - Use `web/playwright.config.ts` + `web/package.json` as the source of truth for preview/verify commands.
   - Avoid extra infrastructure; this remains local + CI executable.

### Added acceptance criteria for local preview
- A teammate can run preview and verify commands without manual command discovery.
- The verify loop covers screenshots plus interaction and DOM assertions.
- Session persistence is optional, documented, and easy to reset.
