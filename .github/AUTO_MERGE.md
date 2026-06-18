# Auto-merge policy -- agile-cards (monorepo)

This repo is opted into agent auto-merge per `C:\dev\PARADIGM_VELOCITY_RULES.md`
(CTO-mode velocity). Drew authorized the opt-in on 2026-06-03. This file is
the repo-local record of the activation (velocity rules section 10 step 2).

As of the 2026-06-18 monorepo merge the repo carries two apps:
`apps/engine/` (the Python runner suite) and `apps/board/` (the Next-style
React board and its Node backend). The policy below applies to both; the
Tier-3 sensitivity list is updated to use the new monorepo paths.

## How auto-merge works here

The agent-review consensus is the gate the agent owns; GitHub's auto-merge
plus the CI check is the gate GitHub owns. Combined flow:

1. Agent opens a PR and classifies its tier.
2. Agent runs the tier-appropriate review:
   - **Tier 1** (single reviewer) -- mechanical / spec-following work.
   - **Tier 2** (two reviewers, different framings) -- substantive but
     reversible work. This is the default for the ledger and gate chunks.
   - **Tier 3** (multi-Opus with explicit opinions) -- high stakes; Drew
     on dissent only.
3. On clean consensus the agent enables GitHub auto-merge
   (`gh pr merge <n> --auto --squash --delete-branch`).
4. The PR merges itself once the required **CI** check is green. CI failing
   means it never merges, regardless of review verdict (safety floor).

Drew is not in the merge path for Tier 1/2. He sees them in the digest and
can `hold PR #N` or `revert PR #N`.

## Safety floors (cannot be bypassed, velocity rules section 4)

- CI on the PR head SHA must be green (the `engine runner battery` and the
  `board frontend battery` checks; the `board backend battery` check is
  added but informational until the board side has a real test suite).
- Full test battery passes with honest signal; no `--force`, `--no-verify`,
  `--skip-tests`, or flag-papering.
- `delete_branch_on_merge` enforced (repo setting on).
- Conventional commits, no BOM. PowerShell for git on the Windows host.
- Bottom-up merge order for stacked PRs.
- No direct push to `main`, no force-push, no repo-visibility change.

## Tier-3 sensitivity list (this repo)

A PR touching any of these auto-promotes to Tier 3 (multi-Opus, Drew on
dissent):

- The card store schema and migrations:
  `apps/engine/runner/src/cards_runner/store/schema.py`,
  `apps/engine/runner/src/cards_runner/store/migrate_v1.py`, anything
  adding or altering a table or promoted column.
- The merge gate and the (future) confidence gate:
  `apps/engine/runner/src/cards_runner/daemon/merge_gate.py`,
  `apps/engine/runner/src/cards_runner/daemon/confidence_gate.py`.
- The verifier decision surface: `apps/engine/runner/src/cards_runner/verifier/**`
  and `apps/engine/lib/verifier/**`.
- `apps/engine/RUNNER_CONTRACT.md`, `apps/engine/DEFINITION_OF_DONE.md`,
  `apps/engine/tier_pricing.yaml`, `apps/engine/tier_map_claude.yaml`
  (the cost / tier contracts).
- The board canonical surfaces: `apps/board/backend/src/**` (the API the
  engine consumes) and `apps/board/frontend/src/state/**` (the view-model
  contract the engine ledger feeds).
- Anything that flips a merge-routing default from review to auto (e.g.
  turning the confidence gate to live mode -- this is Tier 3 / Drew).

## Drew-only carve-out (never agent-decided)

- Repo visibility, package visibility.
- License / copyright / IP posture (`LICENSE`).
- Roadmap-level direction changes.
- Anything with external commitment or irreversible business impact.

## Notes

- The first three auto-merges after activation get an extra "classification
  was right" confirmation in the handoff (velocity rules section 10 step 4).
- mypy is informational in CI today (pre-existing debt in legacy modules);
  ruff + pytest are the gating checks. Clear the debt, then gate mypy too.
