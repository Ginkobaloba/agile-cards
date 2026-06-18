# Sprint Plan -- agile-cards (Throughput Ledger + Confidence Gate)

**Status:** LIVING DRAFT. Adjustable, not a frozen contract. Revise as the work moves.
**Created:** 2026-06-01
**Sprint window:** 2026-06-01 -> 2026-06-13 (about two weeks; the auto-fired retro on 2026-06-12 doubles as sprint review)
**Operating model:** Drew in CTO mode. Tier-3 approval only; Tier 1 and Tier 2 auto-merge after agent review. Per `feedback_cto_mode_velocity.md`.
**Author:** planner pass (Claude, Opus 4.8)

---

## 0. TL;DR for the impatient

- Two PRs are queued and green. Merge them bottom-up: **#21 then #22**. Both Tier 1/2, both auto-mergeable. No blocking forks.
- The schema (chunk 1) and the estimator (chunk 3) are already built. The hole in the middle is **chunk 2, the writer** that actually populates `card_metrics` from daemon lifecycle hooks. Without it the estimator reads an empty table.
- Chunk 2 decomposes into one load-bearing contract PR plus five parallel write-site PRs plus one verification PR. Max concurrency once the contract lands.
- The confidence-driven merge gate is a six-PR stack that consumes the ledger. Its first PR (risk-factor schema) is independent and can start in parallel with chunk 2. The gate going **live** is the one genuine Tier-3 decision in this sprint.
- Two real forks need a call: the **chunk 2 write architecture** (recommend a multi-agent comparison) and the **gate open questions** (specs already recommend defaults; Drew confirms or overrides).

---

## 1. Verified current state (as of 2026-06-01)

Checked against git, not the handoff. Handoff was accurate; nothing stale to correct.

| Item | Claimed in handoff | Verified in git | Status |
|---|---|---|---|
| #21 ledger chunk 1 (schema + work_type) | OPEN, Tier 2, mergeable, base `main` | OPEN, `MERGEABLE`, base `main`, not draft | CONFIRMED |
| #22 ledger chunk 3 (estimator) | OPEN, Tier 1, stacked on #21, mergeable | OPEN, `MERGEABLE`, base `feat/ledger-chunk-1/schema-and-work-type`, not draft | CONFIRMED |
| Merge order | #21 then #22, bottom-up | #22 is based on #21's branch; merging #22 first would orphan the base | CONFIRMED |
| chunk 1 delivered the schema | implied | `card_metrics`, `metric_estimates` tables + `work_type` column present in `store/schema.py` for both SQLite and Dolt/MySQL | CONFIRMED |
| chunk 3 reads the schema | implied | `metrics/store.py`, `estimator.py`, `recalibrate.py`, `priors.py` all read `card_metrics` | CONFIRMED |
| chunk 2 (writer) | pending | no writer wired to lifecycle hooks; `metrics_events.jsonl` does not exist | CONFIRMED GAP |

Test counts from the handoff (511/511 on #21, 558/558 on #22) were not re-run in this planning pass. Re-run before merge; see section 3 pre-merge checklist.

**Branch hygiene note (not blocking, worth a cleanup pass):** the local repo carries a pile of stale branches whose work already merged -- `feature/runner-chunk-3/4/5`, `feature/runner-chunk-6a/6b/6c/6d`, the three `recover/runner-chunk-6*` strand-recovery branches, `chore/gitignore-gaps-2026-05-29` (merged as #20). `delete_branch_on_merge` handles the remote side; the local copies linger. A `git fetch --prune` plus deleting local branches whose upstream is `[gone]` would clear it. The `commit-commands:clean_gone` skill does exactly this. Queued as a Tier-1 housekeeping item (section 6).

---

## 2. How the pieces fit (so the sequencing makes sense)

The throughput-metrics ledger spec (`docs/design/throughput_metrics_ledger.md`, section 14) decomposes into seven chunks. Three are spoken for:

```
chunk 1  schema + work_type            -> PR #21  (OPEN, this sprint merges it)
chunk 2  write surface in the runner   -> NOT BUILT  (the core of this sprint)
chunk 3  the estimator                 -> PR #22  (OPEN, this sprint merges it)
chunk 4  quote read API + CLI          -> partially in #22's stats CLI; gap analysis needed
chunk 5  contract-survival             -> not built
chunk 6  trust signal                  -> not built
chunk 7  backfill + verification       -> partially covered by chunk 2's verification PR
```

The critical insight from `project_throughput_economics.md`: apparently-sequential work is not truly sequential. The dependency is on the prior chunk's **contract/interface**, not its implementation. Define the contracts up front and the implementations parallelize; only final integration is strictly sequential. The irreducible sequential spine is design uncertainty.

For chunk 2 the contract is the `LedgerWriter` interface plus the `metrics_events.jsonl` event shape plus the idempotency rules. Pin those down in one PR and the five write-site wirings parallelize cleanly, because each write site depends only on the writer interface, not on the other write sites.

The confidence gate (`docs/design/confidence_driven_merge_gate.md`, section 13) is a separate six-PR stack that **consumes** the ledger. It writes gate decisions into `card_metrics` (new columns) and reads `bucket_history` from the estimator. So most of the gate stack depends on chunk 2 landing first. The exception is gate-PR-1 (the risk-factor schema), which only touches the verifier and can start immediately.

---

## 3. Workstream A: merge #21 and #22

**Priority:** highest. Everything else stacks on these landing. Target: day 1-2 of the sprint.

### Pre-merge checklist (do for each, in order)

1. `git fetch --prune` so local view matches origin.
2. Re-run the test battery on each branch and confirm the claimed counts (511 on #21, 558 on #22). Do not merge on a stale green; re-run.
3. Confirm `delete_branch_on_merge` is enabled on the repo: `gh repo view Ginkobaloba/agile-cards --json deleteBranchOnMerge`. Enable if not.
4. Confirm each PR is still `MERGEABLE` (no drift since this plan was written).

### Merge sequence (bottom-up, non-negotiable)

1. **Merge #21 first.** It bases on `main`. This is the load-bearing schema. Tier 2 (schema migration), but it has been reviewed and is mechanical to land. Squash-merge.
2. **Re-target or confirm #22's base.** Once #21 merges and its branch auto-deletes, GitHub normally re-points #22's base to `main` automatically. Verify it did. If not, re-base #22 onto `main` locally (PowerShell) and force-update the PR branch.
3. **Merge #22 second.** Tier 1 (pure additive estimator, no behavior change to the daemon). Squash-merge.

Merging out of order, or with `delete_branch_on_merge` off, reproduces the stacked-PR-strand bug from Drew's global rules. Bottom-up plus auto-delete is the combination that keeps it clean.

### Tier classification

- #21 merge action: **Tier 2** (touches schema). Multi-agent consensus already effectively satisfied by prior review; auto-merge on green.
- #22 merge action: **Tier 1.** Single reviewer, auto-merge on green.
- Neither is Tier 3. Neither needs Drew.

### Pre-merge cleanup needed

None blocking. The optional branch-prune (section 1) can happen any time and is not a gate on these merges.

---

## 4. Workstream B: ledger chunk 2 (the writer)

**Priority:** high. This is the reason the sprint exists. Target: contract PR by day 3, parallel write-sites by day 6, verification by day 8.

### 4.1 Scope

Wire each author named in ledger spec section 5.2 to actually write its `card_metrics` field on its lifecycle trigger, and append the corresponding event to `signals/metrics_events.jsonl`. The schema columns already exist (chunk 1); this PR set fills them.

**In scope:**
- A `LedgerWriter` module (`runner/src/cards_runner/metrics/writer.py` or similar) that owns all `card_metrics` writes and the `metrics_events.jsonl` append.
- Wiring at each lifecycle hook (the hooks all exist already; this is connection, not new lifecycle).
- Idempotency: `INSERT OR REPLACE` keyed on `(card_id, tenant_id)`; cumulative fields rebuilt from the audit log, not from the previous row value.
- The audit-log replay verification (spec section 12.3 check 1).

**Out of scope (deferred to later chunks):**
- The quote read API beyond what #22 already shipped (chunk 4).
- Contract-survival read API (chunk 5).
- Trust signal aggregation (chunk 6).
- Backfill of pre-ledger cards (chunk 7). Chunk 2 only handles cards created after it lands; old cards stay `incomplete_metrics=True`.

### 4.2 Interfaces with daemon lifecycle hooks

Each write site and its existing hook (verified present in the codebase):

| Write site | Existing hook | Fields written |
|---|---|---|
| Card creation | planner projection (`store/projection.py`, `card_text_to_record`) | `card_id`, `tenant_id`, `work_type`, `tier`, `pin_required`, `contract_authored_at` |
| Executor exit | `daemon._post_worker_exit` (daemon.py:773) | `started_at`, `finished_at`, `agent_wall_seconds`, `agent_attempts`, `executor_tokens_total`, `executor_cost_usd` |
| Verifier decision | `daemon._dispatch_verifier` (daemon.py:1106) | `verifier_tokens_total`, `rework_cycles` increment on FAIL |
| Reviewer spend | `sibling_reviewer.py` + `amendment_reviewer.py` (chunk 6b attribution path) | `reviewer_tokens_total` |
| Merge gate | `daemon/merge_gate.py` | `merge_gate` outcome |
| PR merged | `daemon/unblocker.py` | `diff_lines_added`, `diff_lines_removed`, `merged_at`, `human_review_wall_seconds` |
| Contract survival | `daemon._route_approve_edited` (chunk 6a path) | `contract_survived` |
| Regression tag | bugfix-card planner (`regresses:` frontmatter) | `regression_card_ids` on the parent row |

All writes follow the chunk 6b best-effort-with-log convention: a write failure logs WARNING and does not abort the calling sweep. The JSONL audit log is authoritative; the row is a denormalization.

### 4.3 PR decomposition (max parallelism)

```
2.0  CONTRACT (sequential spine)
     LedgerWriter interface + metrics_events.jsonl shape + idempotency helper
     (cumulative-from-log). Defines what 2a-2e call into.
        |
        +--- 2a  card-creation writes        (parallel)
        +--- 2b  executor-exit writes         (parallel)
        +--- 2c  verifier + reviewer tokens   (parallel)
        +--- 2d  merge + PR-merged writes     (parallel)
        +--- 2e  rework + contract_survived + regression tag  (parallel)
        |
     2f  VERIFICATION (integration)
         audit-log replay test + quote-sanity harness (spec 12.3)
```

- **2.0** is the only strictly sequential piece. Everything depends on its interface, nothing else.
- **2a-2e** each depend solely on 2.0's interface, not on each other. Five agents can build them concurrently. This is the contract-first concurrency the throughput model is built around.
- **2f** is the final integration: rebuild `card_metrics` from `metrics_events.jsonl` and assert the rebuilt rows match the live table modulo idempotent re-writes; plus the quote-sanity check (P50 within 20% of actual on a sample of completed cards).

### 4.4 Test plan

- **2.0:** unit tests on the writer -- idempotent re-write produces the same row; cumulative fields computed from a synthetic event log; a write failure logs and does not raise.
- **2a-2e:** each wiring PR adds a test asserting its field(s) land in `card_metrics` and the matching `metrics_events.jsonl` event appends, given a simulated hook firing.
- **2f:** the replay test (spec 12.3.1) and the quote-sanity test (spec 12.3.2). This is the correctness gate for the whole ledger; do not declare chunk 2 done until 2f is green.

### 4.5 Tier classification

- 2.0 contract: **Tier 2.** Load-bearing interface; the design has a real fork (section 7.1). The PR itself is Tier 2; the design decision behind it may warrant a multi-agent pass first.
- 2a-2e: **Tier 1 each.** Mechanical wiring against a defined contract, each with its own test. Prime auto-merge candidates.
- 2f verification: **Tier 2.** It is the correctness gate; worth two reviewers.

---

## 5. Workstream C: confidence-driven merge gate (six-PR stack)

**Priority:** medium. Mostly depends on chunk 2 landing. Gate-PR-1 starts in parallel now. Target: gate-1 in week 1, gate-2 and gate-3 in week 2, gate-4 held for Drew.

### 5.1 The stack (from `confidence_driven_merge_gate.md` section 13)

| PR | Scope | Depends on | Tier |
|---|---|---|---|
| **gate-1** | Risk-factor schema + verifier shim. `RiskFactor` dataclass, `VerifierResult.risk_factors` field (backward-compatible default empty), subjective-evaluator prompt + parser update. No gate code. | Nothing. Independent of the ledger. **Can start now.** | Tier 1-2 |
| **gate-2** | Confidence-gate skeleton + shadow mode. `ConfidenceGate` module, `DiffStats` helper, `BucketHistory` reader, ledger writes for shadow decisions. Default mode `shadow`; no routing change. | Chunk 2 (writes gate decisions to ledger) + #22 estimator (reads bucket history) | Tier 2 |
| **gate-3** | Calibration loop + CLI. `metrics/calibration.py`, `metrics/ramp.py`, `cards-runner stats calibration`, `stats ramp`. | gate-2 (reads its shadow data) | Tier 2 |
| **gate-4** | Live-mode wiring + chunk-4 fallback + kill-switch. `merge_gate.apply_with_decision`, `_dispatch_verifier` uses the gate when live, chunk-4 `decide_chunk4` is the fallback. | gate-2, gate-3 | **Tier 3** |
| **gate-5** | `expected_files:` planner field + scope soft signal. Additive. | gate-2 | Tier 1 |
| **gate-6** | Fitted-logistic-regression replacement for the linear formula. Needs n=300 per-bucket data. | Real ledger data (phase 3) | Deferred; Tier 2 when it lands |

### 5.2 Sequencing

- **Now / week 1:** gate-1 (independent). Land it alongside chunk 2.
- **Week 2 (after chunk 2 lands):** gate-2 (shadow mode), then gate-3 (calibration). Shadow mode is non-negotiable as the default per spec section 9.1 -- the gate runs and records what it *would* decide without changing routing, so we collect calibration data before flipping any switch.
- **gate-4 (live mode) is held.** It does not ship in this sprint by default. Shadow mode needs to accrue data first (spec wants n>=30 shadow decisions per active bucket with monotonic calibration before advancing to phase 2). Realistically that is next-sprint territory. Flipping to live is the Tier-3 decision (section 8).
- **gate-5** is additive quality-of-signal; land opportunistically once gate-2 is in.
- **gate-6** is out of scope this sprint (no data yet).

### 5.3 Why gate-4 is Tier 3 and the rest are not

gate-1 through gate-3 and gate-5 change **nothing** about who reviews a merge. Shadow mode is pure measurement. gate-4 is the PR that actually hands merge-routing authority to the confidence signal instead of static tier. That is the "iffy merge" decision in Drew's own words from the spec: *"I should only need to PR the most intense merges, where Opus is feeling iffy."* Changing the merge gate's authority is exactly the class of change Drew should look at. Tier 3, escalate on the decision itself, not just on dissent.

---

## 6. Workstream D: other backlog worth queueing

Pulled from the running handoff and the repo. Ranked by leverage.

| Item | What | Tier | Recommendation |
|---|---|---|---|
| Branch prune | Delete merged local branches (`runner-chunk-3/4/5/6*`, `recover/*`, `chore/gitignore-gaps`). Use `commit-commands:clean_gone`. | Tier 1 | Do early; clears noise. Not a merge gate. |
| Ledger chunk 4 gap analysis | #22 shipped a `stats` CLI. Confirm how much of the chunk-4 quote read API it covers vs the spec's section 11 surface. May be partial. | Tier 1 | One agent reads #22's CLI against spec section 11, reports the delta. Cheap, do it early so chunk 4 scope is known. |
| Ledger chunk 5 (contract-survival) | Detector + read API + CLI. Needs chunk 2 data flowing. | Tier 2 | Queue for next sprint; depends on chunk 2 producing data. |
| Ledger chunk 6 (trust signal) | Rolling-window regression-rate aggregator + threshold event + CLI. | Tier 2 | Queue for next sprint; depends on chunk 2 + regression tagging (2e). |
| Ledger chunk 7 (backfill) | One-shot CLI to stamp `work_type` and derive metrics for pre-ledger cards. | Tier 1 | Low priority; the estimator already excludes `incomplete_metrics` rows so old cards do not poison it. Do whenever. |

Not in this repo but adjacent (tracked elsewhere, noted so they do not get lost): the agile-cards-**board** grid view (backend done 76/76, frontend pending, no PR yet) and **career-repo** outcomes tracker. Those belong to their own sprint slices, not this one.

---

## 7. Real forks that need a decision

Per `feedback_voice_rules.md` and the multi-agent default: where the design genuinely branches, surface it rather than pick blind.

### 7.1 FORK: chunk 2 write architecture (recommend multi-agent comparison)

The spec (section 5.4) recommends computing cumulative fields (`rework_cycles`, `agent_attempts`, `executor_tokens_total`) from the audit log rather than from the previous row value, specifically to avoid the chunk 6b stale-read bug where the reviewer and editor trampled each other's writes. That is a sound recommendation, but it is a real architectural fork with a second axis:

- **Axis 1 (source of truth for cumulative fields):** rebuild-from-log (spec recommendation, slower per-write, immune to stale reads) vs read-modify-write the row (faster, but reintroduces the trampling risk).
- **Axis 2 (when to write):** synchronous in the lifecycle hot path (simple, but adds I/O to every worker exit) vs deferred/batched (a sweep that drains pending events, keeps the hot path clean, adds latency to when metrics become visible).

These two axes interact. This is the kind of "technically disputed, multiple viable designs" call where Drew's standing rule is multi-agent cross-check. **Recommendation:** before building 2.0, run a 2-or-3-agent comparison (e.g. one argues rebuild-from-log + synchronous, one argues rebuild-from-log + deferred sweep, one argues read-modify-write + synchronous with a lock) and score them on hot-path cost, correctness under crash/replay, and implementation surface. The output picks the 2.0 contract. This is a planning-time fork; it does not block merging #21/#22 or starting gate-1.

### 7.2 FORK: gate open questions (specs already recommend defaults)

The confidence-gate spec section 12 lists seven open questions for Drew. The ones that matter for this sprint, with the spec's own recommendation as the proposed default:

- **12.2 skipped-verifier cards:** spec recommends **B** (force at least sibling_review; never auto-merge a card the verifier did not examine). Proposed default: accept B.
- **12.3 haiku-only subjective cards:** spec recommends **C** (separate soft-signal weight, already in the formula) and notes **B** (cap at phase 3) is also reasonable. Proposed default: accept C, revisit B with data.
- **12.4 `diff_within_planner_declared_scope`:** spec recommends **A** (planner writes `expected_files:`; missing = signal contributes 0). This is gate-5. Proposed default: accept A.
- **Ledger spec 13.1 (does auto_edit_ac count as contract drift?):** spec recommends **counting it as False** (drift). Proposed default: accept.

These are Tier-3 in the sense that they are Drew's calls, but the specs carry defensible recommendations. **Recommendation:** treat the spec recommendations as the working defaults, note them as confirm-or-override items, and do not block gate-1/gate-2 on them (they only bind at gate-4 live mode, which is held anyway).

---

## 8. Tier-3 decisions for Drew (explicit)

Short list. Everything else auto-merges after agent review.

1. **Flip the confidence gate to live mode (gate-4).** Changes merge-routing authority from static tier to confidence signal. Held this sprint regardless; the decision lands next sprint once shadow data exists. This is the primary Drew escalation in this body of work.
2. **Opt agile-cards into auto-merge at all.** Per `feedback_cto_mode_velocity.md`, auto-merge is per-repo, per-branch-pattern opt-in; until a repo is explicitly opted in, treat it as "Drew merges." **Question for Drew:** is agile-cards opted into Tier-1/Tier-2 auto-merge for this sprint, or does he want to merge #21/#22 and the chunk-2 PRs himself? This determines whether sections 3-5 auto-merge or queue for him.
3. **Resolve (or ratify the defaults for) the gate open questions** in section 7.2. Low urgency; only binds at gate-4.
4. **Ratify the chunk 2 write-architecture** once the multi-agent comparison (section 7.1) reports. The comparison narrows it; Drew confirms the pick if it is contested.

---

## 9. Sprint timeline and target ship dates

Two-week sprint, 2026-06-01 -> 2026-06-13. Dates are targets, not commitments (per the living-draft rule).

| Day | Target | Tier |
|---|---|---|
| 06-01 / 06-02 | Merge #21, then #22, bottom-up. Branch prune. Chunk-4 gap analysis. Kick off multi-agent comparison for chunk 2 write architecture (7.1). | T1/T2 |
| 06-02 / 06-03 | Chunk 2.0 contract PR (after the comparison reports). gate-1 risk-factor schema starts in parallel. | T2 / T1 |
| 06-03 / 06-06 | Chunk 2a-2e in parallel (five write-site PRs). gate-1 lands. | T1 |
| 06-08 / 06-10 | Chunk 2f verification (replay + quote-sanity). Chunk 2 declared done only when 2f is green. gate-2 shadow-mode PR starts. | T2 |
| 06-10 / 06-12 | gate-2 lands (shadow). gate-3 calibration. gate-5 expected_files opportunistically. Shadow data begins accruing. | T2 / T1 |
| 06-12 | Retro auto-fires. Use it as sprint review: did chunk 2 land clean, is shadow data accruing, is gate-4 ready to discuss for next sprint. | -- |

**Held to next sprint:** gate-4 (live mode, Tier 3 / needs shadow data), ledger chunks 5/6 (need chunk 2 data flowing), gate-6 (needs n=300).

**Realistic risk on the timeline:** the chunk 2 multi-agent comparison could surface that the deferred-sweep architecture is meaningfully better, which adds a small amount of build surface to 2.0 (a drain sweep) and could push 2a-2e by a day. That is acceptable; correctness of the writer is the load-bearing thing and worth the day.

---

## 10. Definition of done for this sprint

- #21 and #22 merged to `main`, branches auto-deleted, `main` green.
- Chunk 2 writer populating `card_metrics` from live lifecycle hooks, with `metrics_events.jsonl` appending, and 2f verification (replay + quote-sanity) green.
- gate-1 risk-factor schema merged (verifier emits risk factors; backward compatible).
- gate-2 shadow mode merged and recording decisions (no routing change).
- The chunk 2 write-architecture fork resolved (multi-agent comparison done, pick ratified).
- This doc updated to reflect what actually shipped vs planned.

A stretch-but-plausible add is gate-3 calibration. Anything past that is next sprint.

---

## 11. Open adjustments (fill in as the sprint runs)

- [ ] Did the multi-agent comparison (7.1) change the 2.0 contract? Record the pick and why.
- [ ] Chunk-4 gap analysis result: how much of the quote read API did #22 already ship?
- [ ] Drew's call on auto-merge opt-in for agile-cards (section 8.2).
- [ ] Any chunk 2 write-site that turned out to depend on another (would break the parallelism assumption -- flag it).
- [ ] Actual test counts after merge.

---

*Living draft. Update in place; do not freeze.*
