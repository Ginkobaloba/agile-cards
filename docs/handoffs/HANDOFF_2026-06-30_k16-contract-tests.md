# HANDOFF 2026-06-30 -- K16: consumer contract tests (CARDS-013, CARDS-014)

Executed **CHUNK K16** (T2; owns CARDS-013, CARDS-014) per
`C:\dev\PARADIGM_INTEGRATION_ROADMAP.md`. Consumer contract tests in
`paradigm-agilecards` against the `@paradigm/*` platform packages, run on every
PR and every Renovate `@paradigm/*` bump.

## What this session did

- **CARDS-013 -- `@paradigm/auth` (Python, `backend/contracts/`).**
  `@paradigm/auth` is TypeScript-only; the roadmap has no Python `@paradigm/auth`
  in v1 (the FastAPI backend verifies IdP JWTs directly with PyJWT). So CARDS-013
  is an executable contract pinning what any Paradigm Python consumer must
  enforce. `backend/contracts/test_paradigm_auth_contract.py` -- 9 tests, all
  green: RS256+kid+JWKS resolution; JWKS refresh on unknown kid (fetched exactly
  once); expired / tampered / HS256-confusion / `alg:none` / missing-kid /
  malformed rejection. Self-contained (in-memory RSA + JWKS + reference
  verifier). Deps in `backend/contracts/requirements.txt`, decoupled from
  `backend/pyproject.toml` on purpose (the backend app deps are owned by K11).
- **CARDS-014 -- `@paradigm/llm-client` (TS, `frontend/contracts/`).** Scaffold
  per the chunk. Contract mirrored from the **real** `@paradigm/llm-client@0.1.0`
  source (`paradigm-platform/packages/llm-client`): five-method frozen interface,
  9-field telemetry payload (8 required + optional `error_code`), provider
  selection rule, stable error codes. 4 active checks pass; a `describe.skip`
  LIVE conformance block (real package + mock fetch) is dormant until K11b. New
  `vitest.contracts.config.ts` + `test:contracts` script (node env, scoped to
  `contracts/`, independent of the src/ Boards suite).
- **CI:** new `contracts` job in `.github/workflows/ci.yml` runs both suites
  (self-installing deps) on every PR. Non-required context for now.
- **renovate.json:** `@paradigm/*` bumps run the contracts job and **never
  auto-merge** (Tier-3; K17 reshapes the full review gate).
- **verification/cards/CARDS-013.md + CARDS-014.md = PASS.**
- **PR #48** opened; **CI fully green** (contracts 22s; engine-runner, both
  backend batteries, board frontend, Socket all pass; Quick/Deep Verify skip as
  designed). https://github.com/Ginkobaloba/paradigm-agilecards/pull/48

## What is currently broken or incomplete

- **PR #48 not merged.** CI is green; merge is Drew's call (T2 app repo is
  CI-gated, required-review 0). I did not auto-merge.
- **CARDS-014 live conformance is intentionally dormant** (`describe.skip`),
  wired by K11b when the Node BFF + `@paradigm/llm-client` dependency land.

## !!! Out-of-band finding: an uncommitted K11 spike in the working tree

During this session a **complete K11 spike was found uncommitted** in
`C:\dev\paradigm-agilecards` -- on **no branch and no stash** (`git log --all`
and `git stash list` both empty for it). It was being actively written between
~11:18 and ~11:26 (file mtimes) by a concurrent process, in parallel with this
K16 session:

- `backend/cards_api/` -- `auth.py`, `config.py`, `deps.py`, `main.py`,
  `store.py`, `__init__.py` (a full FastAPI Cards API + JWKS verifier)
- modified (tracked) `backend/app.py`, `backend/pyproject.toml` (version bump to
  1.0.0, `pyjwt[crypto]` runtime dep, `cards_api` packaging, `infisical` extra),
  `.gitignore`
- `backend/.env.example`, plus `backend/.venv/` and caches
- test drafts: `backend/tests/{conftest.py, test_auth_verify.py,
  test_config_secrets.py, test_endpoint_auth.py, test_org_isolation.py}`
  (these import `cards_api.auth`; scoped to **AC-CARDS-003**, i.e. **K11**, not
  K16). Their auth tests REJECT on unknown kid; they do not implement the
  refresh-on-unknown-kid behavior CARDS-013 requires.

**This K16 work deliberately did not touch any of it.** K16 sidesteps the shared
`backend/pyproject.toml` by housing its deps in `backend/contracts/requirements.txt`,
and everything K16 committed was staged by explicit path. The K11 spike is still
sitting dirty in the working tree and **will be lost** if someone runs `vend`,
`git checkout`, or `git stash drop`. **Decide its home before doing any blanket
git operation in this repo.**

## What the next session should do first

1. **Resolve the K11 spike (above) before anything else.** Either commit it to a
   `feat/k11-*` branch (it's substantial, looks mostly built) or discard it
   deliberately. Do NOT run `vend`/`git add -A` until it's resolved -- a blanket
   commit would entangle K11 into the wrong PR.
2. **Merge PR #48** if approved (CI is green).
3. **K11 / K11b:** when the real verifier lands it must satisfy
   `backend/contracts/test_paradigm_auth_contract.py`. When the Node BFF +
   `@paradigm/llm-client` land, wire the CARDS-014 live block (flip
   `describe.skip` -> `describe`; add the package). See `frontend/contracts/README.md`.
4. **Promote `contracts` to a required status check** in branch protection once
   it has baked (it is additive/non-required today).

## Open questions for Drew

- The K11 spike: commit to a branch, or discard? (It's uncommitted and at risk.)
- Were K11 and K16 meant to run in the same working tree? If chunks run in
  parallel, separate git worktrees would avoid this collision entirely.

## Pointers

- Roadmap: `C:\dev\PARADIGM_INTEGRATION_ROADMAP.md` (K16 = line ~300)
- Contract sources of truth: `C:\dev\paradigm-platform\packages\llm-client\src\{types,errors}.ts`
- PR #48: https://github.com/Ginkobaloba/paradigm-agilecards/pull/48
- Verification: `verification/cards/CARDS-013.md`, `CARDS-014.md`
- Prior handoff: `docs/handoffs/HANDOFF_2026-06-30_k2-paradigm-agilecards-rename.md`

## Next Session Onboarding

Future sessions: read `C:\dev\SESSION_PROTOCOL.md`, then `CLAUDE.md` in this
project (there is none at repo root yet -- consider adding one), then this file,
then run `vstart`. First action: resolve the uncommitted K11 spike described
above before any blanket git operation.
