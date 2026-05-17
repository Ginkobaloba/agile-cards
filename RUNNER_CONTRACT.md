# Runner contract

The runner is the harness that polls `C:\dev\todo\backlog\`, spawns
executor agents, and drives cards to terminal state. This file is the
contract surface the runner must honor. The /cards skill produces files
that match this contract; any change here is a breaking change for the
runner.

The runner itself is NOT built by the /cards skill task. This doc is
deliberately a description, not an implementation.

---

## Directory invariants

```
C:\dev\todo\
  backlog\    cards that have not been claimed
  active\     cards an executor is currently working on
  done\       cards whose work merged successfully
  blocked\    cards finished but unmerged, or paused on a dependency
  _batches\
    .counter
    b<NNN>-manifest.yaml
```

The subfolder is canonical. The `status:` field in card frontmatter is a
convenience that the runner keeps in sync. If they disagree, the
subfolder wins; the runner repairs the field.

Cards never live in two subfolders. The runner moves the file
atomically between subfolders (filesystem-level file move; on POSIX
that's `rename(2)`, on Windows that's `MoveFileEx` with
`MOVEFILE_REPLACE_EXISTING`) when transitioning state. The filename
itself does not change; only its parent directory changes.

---

## Card status transitions

```
backlog --claim--> active --finish + merge_status=merged--> done
                       \--finish + merge_status!=merged--> blocked
                       \--dependency unmet (re-check)--> backlog
backlog --dependency permanently blocked--> blocked
blocked --unblocked--> backlog or active (runner choice)
```

Allowed `merge_status` values:

- `pending` -- card hasn't reached merge step yet (initial state)
- `open` -- PR is open, awaiting review or auto-merge
- `merged` -- merged into base_branch
- `requires_review` -- medium tier needs sibling-agent review pass
- `conflict` -- merge conflict with base_branch, needs human or rebase
- `blocked` -- merge gate held by external dependency

A card with `merge_status: merged` is the only kind that belongs in `done/`.

---

## Claim protocol

The runner claims a card by:

1. Checking `depends_on` -- every dependency must be in `done/` with
   `merge_status: merged`.
2. Reading the card's `model_floor` and `pin_required` to allocate an
   executor at the right model.
3. If the project config sets `story_source_path`, re-hash that file
   and compare against the card's `story_hash`. On mismatch, do not
   claim; flag for re-triage (see "Story drift" below).
4. Setting `claimed_by`, `started_at`, and `last_heartbeat` in
   frontmatter.
5. Moving the file from `backlog/` to `active/`.

The claim is an atomic file move between subfolders (filename
unchanged, parent directory changes from `backlog/` to `active/`),
not a lock file. The runner is responsible for not double-claiming.

---

## Heartbeat and orphan reclaim

While a card is in `active/`, the executor updates `last_heartbeat`
periodically (suggested cadence: every 5 minutes, but the runner can
pick). On every runner pass:

- If a card's `last_heartbeat` is older than the project's
  `orphan_timeout_minutes` (default 120), the runner moves the card
  back to `backlog/` and clears `claimed_by`, `started_at`,
  `last_heartbeat`.
- The card's `branch` is left alone (may contain partial work the next
  executor can salvage, or the runner can prune it; up to runner).

This makes executor crashes recoverable without manual intervention.
The card schema reserves the fields; the runner owns the policy.

---

## Story drift

The /cards skill stamps every card with `story_hash` = sha256 of the
source story text at plan time. The batch manifest preserves the full
text under `source.text`.

If the project config sets `story_source_path`, the runner:

1. Reads the file at that path on every claim attempt.
2. Computes sha256 of its current contents.
3. Compares against the card's `story_hash`.
4. On mismatch, refuses the claim and moves the card to `blocked/`
   with a marker in the merge_status reason field. Cards in this state
   are awaiting re-triage by another `/cards` invocation against the
   updated source.

Without `story_source_path`, the runner skips this check; `story_hash`
is then just a forensic fingerprint linking the card to the manifest.

---

## Branch and worktree protocol

Default (full mode): one branch per card, named `card/<id>`, based off
`base_branch` (defaults to `main`, planner can override). The executor
commits only to its card branch.

Lean mode (project opts in): all cards in a batch share one branch
`cards/<batch_id>`. Cards merge into that branch sequentially in
dependency order. One PR per batch instead of per card.

**Worktree creation reliability.** When the runner spawns a per-card
worktree (the typical pattern for true parallel execution), it MUST:

1. Serialize worktree creation across parallel runners using a global
   mutex. A file lock on `C:\dev\todo\.runner.lock` (or equivalent) is
   sufficient. This avoids the `.git/config.lock` race observed in
   Claude Code issue #34645 (multiple agents writing config
   concurrently corrupt git state).
2. Verify creation succeeded before handing the worktree to an agent.
   Check (a) the worktree directory exists and is non-empty, (b)
   `git worktree list` includes the path, (c) `git status` inside the
   worktree returns cleanly. If any check fails, treat the claim as
   failed and roll back (move card back to backlog).
3. Document any partial-creation failure in `_batches/<batch>-runner.log`
   so subsequent runs can avoid the same path.

Related Claude Code issues for context: #40164 (parallel agent file
contention) and #34645 (.git/config.lock race). Same mitigations apply.

**Atomic move between subfolders.** Card state transitions rely on
atomic file moves across `backlog/`, `active/`, `done/`, `blocked/`.
The filename stays the same; only the parent directory changes. On
NTFS, `MoveFileEx` with `MOVEFILE_REPLACE_EXISTING` is documented
atomic within a volume, but the dev-meta repo includes
`tests/atomic_rename_test.ps1` (named for the syscall it exercises)
which verifies empirically that moving a file between sibling
subfolders is atomic under concurrent contention. Run that script
once per device before trusting parallel runners on that machine. If
the test fails, fall back to `Move-Item` with explicit lock-retry
loop (see test script comments).

---

## Merge gates

Tier-aware. The runner reads `points` (tier 1-6) and routes:

- tier 1, 2: auto-merge if `lint && tests && !conflicts`
- tier 3, 4: auto-merge after a sibling-agent reviewer says ok
- tier 5, 6: open PR, wait for Drew's approval

`pin_required: true` (set from stakes=high) overrides any per-project
relaxation. High-stakes cards always go through human review.

---

## Acceptance check execution

Before a card transitions to `done/`, the runner parses the fenced
`acceptance_checks:` YAML block under the card body's "## Acceptance
criteria" section. Every check in the list must pass.

Check types:

- `shell` -- run `cmd` in the project root; exit code 0 = pass.
- `file_exists` -- `path` exists at repo root (or absolute).
- `file_absent` -- `path` does not exist.
- `grep_match` -- pattern found in `file` (or files matched by glob).
- `grep_absent` -- pattern not found.
- `http_status` -- url returns expected status; only honored when the
  project config explicitly opts into network checks. Disabled by
  default.

Per-check pass/fail is recorded in the runner-appended Completion
notes section. If any check fails, the card moves to `blocked/` and
the failures land in Completion notes for the next executor (or human)
to investigate.

---

## Completion notes

On terminal transition (to `done/` or `blocked/`), the runner appends a
sixth section to the card body titled `## Completion notes` containing:

- what the executor actually did
- workarounds or surprises
- per-check acceptance results
- suggestions for downstream cards

The skill does not write this section; the runner does.

---

## Cost cap enforcement

When a card has `cost_cap_usd` set (non-null), the runner:

1. Tracks cumulative tokens consumed for that card (planner
   attribution, executor model calls, sibling-review pass), broken
   down by model and by input/output. Tokens are the durable
   measurement.
2. On each model-call boundary, converts running tokens to USD by
   reading the current `tier_pricing.yaml` and multiplying. USD is
   derived, never stored on the card.
3. If the derived USD exceeds `cost_cap_usd`, halts execution: move
   card to `blocked/` with a marker in Completion notes recording
   both the token counts and the USD figure at halt. Do not silently
   overspend.

When `cost_cap_usd` is null, no cap is enforced. Per-project caps and
fleet-wide caps are runner concerns and not part of this contract.

---

## Pre-approval gate

When a card has `requires_pre_approval: true` (default for high-stakes
cards), the runner MUST NOT claim the card without an explicit human
approval recorded somewhere the runner can verify (a marker file, a
manifest annotation, a webhook callback -- runner picks the mechanism).

This is distinct from `pin_required`, which only forces the merge gate.
A card can be pre-approved to run but still require human approval at
merge time.

---

## Trace id propagation

Every card carries `trace_id` (uuid). The runner MUST propagate this id
to:

- every sub-agent invocation the executor makes
- every log event the runner emits for this card
- the merge gate (PR title or commit trailer suggested)
- any cost-tracking ledger entry

This is the only practical way to correlate logs across a fleet of
parallel executors after the fact.

---

## Context discipline (hard constraint)

The executor's prompt context, when the runner spawns it, MUST include
only:

- the card body (in full)
- the project repo (working tree)
- the `trace_id`

The runner MUST NOT forward:

- the batch manifest
- sibling cards (even cards in `depends_on`)
- planning-pass conversation
- planner-reviewer disagreements

If the executor needs information from a dependency, that information
is either (a) committed code from the dependency's branch that the
executor can read normally, or (b) explicitly summarized in the card's
Pointers section. Quadratic context explosion is a known failure mode
in multi-agent systems; this constraint stops it at the spawn site.

---

## Cards are state, the runner is stateless

The card is the durable unit of state. The runner MUST NOT hold task
state in its own context window or in-memory caches. To answer "what
is the state of card X" the runner reads the card.

Concretely:

- No in-memory map of "card id -> state."
- No long-lived process holding the active backlog in working set.
- Every claim attempt re-reads the card frontmatter from disk.
- Every status check re-reads the subfolder location from disk.
- Every dependency resolution re-reads the dependency card from disk.

Cost: one filesystem read per query (cheap on local NTFS, cheap enough
on a network share). Benefit: the runner can crash, restart, scale out
horizontally, or be killed and resumed by another process without
losing or corrupting any card's state. Cards survive runners.

This is also what makes the planner-vs-reality feedback loop possible
across sessions: the actual_* fields on a card are durable evidence
that any future planner can read, regardless of which orchestrator
recorded them.

---

## What the skill commits to

- Frontmatter schema as defined in `templates/card.md`. Field renames or
  removals are breaking changes.
- Status subfolder names: exactly `backlog`, `active`, `done`, `blocked`,
  `_batches`.
- Tier semantics: `points` is 1-6, mapping to the matrix in `README.md`.
- Manifest schema as defined in `templates/batch_manifest.yaml`.
- Batch id format: `b<NNN>` zero-padded, counter in `_batches\.counter`.
- `story_hash` is sha256 hex of `source.text` from the manifest, stamped
  identically on every card in the batch.
- `acceptance_checks:` YAML block lives inside the body's "## Acceptance
  criteria" section, fenced as `yaml`. The schema for individual checks
  is fixed (see template + this doc).
- `last_heartbeat` is ISO 8601 UTC, nullable.
- `trace_id` is a v4 uuid stamped at card creation.
- `cost_cap_usd` is nullable float or null.
- `requires_pre_approval` is bool, defaults true when stakes is high.
- `estimated_tokens` and `estimated_duration_minutes` are set by the
  planner at card creation.
- `actual_tokens` is set by the executor at completion;
  `actual_duration_minutes` is derived from `started_at` and
  `finished_at`. Cards must carry these fields so the planner-vs-
  reality feedback loop can read them post-hoc.
- Cards do NOT store USD. USD is derived from tokens at display or
  cap-check time using the current `tier_pricing.yaml`. The only
  USD field on a card is `cost_cap_usd` (the budget ceiling),
  because budgets are how humans think about spend.
- DAG of `depends_on` edges is acyclic. The skill refuses to write a
  batch with a cycle.
- Cards are the system's state. Any orchestrator (this skill, a
  runner, future coordinators) reads card state from disk per query
  and holds no in-memory mirror.

---

## What the skill does not commit to

- How the runner spawns executors, polls, or scales.
- How sibling-agent review is implemented.
- Whether PRs go through GitHub, gitea, or local-only.
- Retry policy on executor failure.

The runner owns those choices.
