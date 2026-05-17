---
name: cards
description: |
  Decompose a user story or pasted discussion into independent,
  claimable cards with tier-derived model recommendations, suitable
  for parallel agent execution. Triggers on /cards, "break this into
  cards", "decompose this story into parallel work", "plan this as
  cards", or any request that names cards/backlog/parallel execution
  in a planning context. Produces cards in C:\dev\todo\backlog\ and a
  manifest in C:\dev\todo\_batches\. Does NOT execute the cards; the
  runner does that (see RUNNER_CONTRACT.md).
tools: Read, Write, Edit, Glob, Grep, Bash, Agent
---

# /cards

The /cards skill is a planner. It takes a user story or pasted
discussion, runs a planner + reviewer (or decomposer + estimator +
reviewer) pass, and writes a batch of cards to `C:\dev\todo\backlog\`
with a manifest in `C:\dev\todo\_batches\`. The runner (separate
component, out of scope) picks up the cards from there.

The /cards skill never executes cards. It plans them. Confusing these
two responsibilities is the most common way the system ends up
brittle.

---

## 0. Inputs

- A user story (one paragraph or longer)
- A pasted discussion (chat log, design doc excerpt, meeting notes)
- A path to a markdown doc to ingest

Flags:

- `--project <path>` -- absolute project path. If omitted, infer from
  the current working directory or ask.
- `--deep` -- force the 3-agent planning variant regardless of input
  size.
- `--lean` -- override project config; commit all batch cards to a
  shared `cards/<batch_id>` branch.
- `validate` -- subcommand. Skips planning and runs the
  status-vs-subfolder integrity check across `C:\dev\todo\`.

---

## 1. Resolve context

1. Read `C:\dev\NAMING_CONVENTIONS.md`. If missing, fall back to
   `C:\dev\_meta\NAMING_CONVENTIONS.md`. If both missing, abort.
2. Read `C:\dev\SESSION_PROTOCOL.md` (same fallback).
3. Determine the target project path from `--project` or the current
   working directory.
4. If `<project>\.cards-config.yaml` exists, parse it for mode (full
   or lean), `orphan_timeout_minutes`, `story_source_path`,
   `hot_paths`, and merge-gate overrides.
5. Ensure `C:\dev\todo\` and its subfolders exist. Create
   `backlog/`, `active/`, `done/`, `blocked/`, and `_batches/` if any
   are missing. Touch `_batches\.counter` and seed it to `0` if
   absent.

---

## 2. Planning pass

Default: two agents, both pinned to `claude-opus-4-7`, both with
extended thinking enabled. Both invoked directly via the Agent tool.

- **Planner** -- proposes the decomposition. For each proposed card,
  it sets title, scope, out-of-scope, dependencies, touches, and a
  first-pass stakes/difficulty read.
- **Reviewer** -- adversarial. Looks for missing dependencies,
  ambiguous scope, undersized or oversized cards, and parallel-hazard
  pairs (sibling cards that share files).

Run them in parallel (one Agent call per role, single message with
two tool uses). The reviewer's critique is returned to the planner
for one revision round. Stop after that round. Two rounds is the
budget; further rounds buy little and burn tokens.

Three-agent variant fires when:

- input exceeds the project's `deep_plan_token_threshold` (default
  ~3000 tokens), or
- the user passes `--deep`.

Roles for the deep variant:

- **Decomposer** -- raw breakdown into atomic units
- **Estimator** -- sizes each card on stakes + difficulty, writes
  the sizing_note
- **Reviewer** -- adversarial, same role

Unresolved disagreements between any two planning agents are logged
verbatim in the manifest under `planner_disagreements:`. Never silently
average; Drew triages from the manifest.

---

## 3. Tier assignment

For every card the planning pass produced:

1. Set `stakes` (low / medium / high) and `difficulty` (shallow /
   deep) per the planner's read.
2. Derive `points` (tier 1-6) from the matrix in README.md.
3. Look up `model` and `extended_thinking` in `tier_map_claude.yaml`
   for that tier.
4. Set `model_floor` from stakes (low -> haiku, medium -> sonnet,
   high -> opus).
5. Set `pin_required = (stakes == "high")`.
6. Set `requires_pre_approval = (stakes == "high")` by default; the
   planner may set true for medium-stakes cards if it judged the
   planning itself was risky.
7. Set `cost_cap_usd` if the tier has a documented historical blow-up
   risk; otherwise leave null. The runner enforces this cap by
   tracking actual tokens consumed and converting on demand via
   `tier_pricing.yaml`; the cap is a USD ceiling, the underlying
   measurement is tokens.
8. Generate a v4 uuid for `trace_id`.
9. Compute `sizing_note` -- a one-line read of both axes.

---

## 4. Validation

Before writing anything, run these checks. If any fail, surface the
failure and abort. No half-written state in `C:\dev\todo\`.

1. **DAG cycle detection.** Walk the `depends_on` graph. If there is
   a cycle, refuse to write the batch. Surface the cycle as
   `[card_a -> card_b -> ... -> card_a]`. A cyclic dependency is a
   planning bug; never let it reach the runner.
2. **Earn-its-keep heuristic.** If the planner produced fewer than 5
   cards, or if the dependency graph is fully linear with zero
   parallelism, abort with a one-line explanation. /cards is not
   meant to manufacture ceremony for work that doesn't need it.
3. **Parallel-hazard scan.** For each pair of cards with no
   `depends_on` edge between them, intersect their `touches:` lists
   (expanding globs). Pairs with non-empty intersection are
   parallel-hazardous; record them in the manifest's
   `parallel_hazards:` block. This doesn't block; it informs the
   runner so it can serialize the pair.
4. **Hot-paths cross-check.** For any card whose `touches:` matches a
   project-config `hot_paths:` glob, raise the card's parallel
   sensitivity. Surface in the dry-run summary.

---

## 5. Dry-run summary

Render to the user, no files written yet:

- Card count and points histogram
- Dependency edges (compact list)
- Count of immediately-claimable cards (no unmet deps)
- Parallel-hazard pairs
- Planner-reviewer disagreements (if any)
- The proposed batch id (next from `.counter`)
- Project path and mode (full / lean)

Stop and wait for explicit approval. Approval is a textual "ok",
"go", "approved", "yes". Anything else is a request for revision or
abort.

If the user requests revision, return to step 2 with their feedback
attached as additional planner input. Cap revisions at three. If
revisions don't converge by then, surface the disagreement and ask
Drew to break the tie.

---

## 6. Write phase

On approval:

1. Lock `_batches\.counter` (atomic-file-move based; see
   RUNNER_CONTRACT.md's "Atomic move between subfolders" section).
   Increment, allocate the new batch id, release.
2. For each card, generate the file at
   `C:\dev\todo\backlog\<id>.md`. Frontmatter is the full schema in
   `templates/card.md`, populated from the planning output. Body
   sections are filled by the planner (Context, Scope, Out of scope,
   Acceptance criteria, Pointers).
3. Write the manifest at `C:\dev\todo\_batches\<batch>-manifest.yaml`
   per `templates/batch_manifest.yaml`. Include the full source text,
   `story_hash` (sha256 of source.text), planning agents and models
   used, planner disagreements, summary numbers, all cards, all
   dependency edges, all parallel hazards.
4. If `C:\dev\todo\` is under git (dev-meta typically isn't tracking
   todo/ at the moment), stage and commit with message
   `cards: plan batch b<NNN> (<N> cards) for <project>`.

---

## 7. Failure modes

- **Empty input** -- nothing to plan; explain and exit.
- **Under 5 cards or fully linear graph** -- /cards refuses; suggest
  doing the work directly or reformulating.
- **Cyclic dependency** -- planning bug; refuse and surface the cycle.
- **Missing tier_map** -- explain and abort; do not guess.
- **Missing protocol files (NAMING_CONVENTIONS, SESSION_PROTOCOL,
  both copies)** -- explain and abort; do not guess.
- **Batch id collision** -- recompute from `.counter`; if still
  collides, abort and surface.
- **Write conflict in `backlog/`** -- abort; never overwrite a
  pre-existing card.

In every failure path, `C:\dev\todo\` is left in the same state it
started. No partial batches.

---

## 8. Context discipline and stateless orchestration

Two related rules that, together, prevent the orchestrator (this skill,
a runner, or any future coordinator) from collapsing under its own
weight as a batch grows. Both are OWNED by /cards because the planner
sets the contract everyone downstream honors.

### 8a. Executor context

When the runner spawns an executor agent for a card, the executor's
prompt context MUST contain only the card body, access to the project
repo, and the `trace_id`. The runner MUST NOT forward the batch
manifest, sibling cards (even direct dependencies), the planning
conversation, or planner-reviewer disagreements.

If the executor needs information from a dependency, that information
is in committed code on the dependency's branch (readable normally) or
summarized in the card's Pointers section. Quadratic context explosion
is the canonical multi-agent failure mode; this constraint prevents it
at the spawn site.

The planner is responsible for writing self-contained cards.

### 8b. Cards are state, the orchestrator is stateless

> The card is the durable unit of state. The orchestrator (this skill,
> a runner, or any future coordinator) MUST NOT hold task state in its
> own context window. To answer "what is the state of card X" the
> orchestrator reads the card.

This keeps orchestrator context minimal, makes the system survivable
across orchestrator restarts, and enables future deployment where the
orchestrator is a local or resource-constrained process that simply
cannot afford to remember an entire fleet's worth of in-flight work.

The cost is one filesystem read per query. The benefit is a coordinator
that cannot bloat itself into uselessness mid-batch.

Practical consequences for /cards itself: the planner does not keep a
session of "what cards exist where"; on each invocation it reads
`C:\dev\todo\` to see ground truth. There is no in-memory cache. The
runner has the same constraint: claim, work, write, release the card,
forget. The card's frontmatter and body carry everything needed for
the next pass to pick up.

---

## 9. `/cards validate` subcommand

Scans `C:\dev\todo\` and reports cards whose `status:` frontmatter
field disagrees with their subfolder location. The subfolder is
canonical; the field is convenience. They should agree.

Report format: list each divergent card with `<id>: in <subfolder>,
status field says <status>`. Exit 0 if no divergence, exit 1 if any
found.

`/cards validate` does NOT auto-repair. The right repair depends on
why the divergence happened (executor crash mid-move? manual mv? a
runner with a stale read?). Surface and stop.

---

## 10. Token discipline

`/cards` is a planning tool, not a debate club. Concretely:

- Two rounds of planner-reviewer back-and-forth, max. After that, log
  disagreements and ship.
- One dry-run summary. If the user asks for revision, run up to three
  revision cycles, then surface the tie.
- The skill itself reads only the necessary protocol files, the
  project config, and the user's input. It does not read the project's
  full repo at planning time; that's the executor's job.

When in doubt: prefer fewer rounds, log the uncertainty in the
manifest, let Drew decide.

---

## Reference files in this skill folder

- `README.md` -- human-facing spec
- `tier_map_claude.yaml` -- tier 1-6 to claude model + thinking
- `tier_pricing.yaml` -- per-model token prices (USD per 1M tokens),
  used to derive USD figures from token counts at display / cap-check
  time. Cards never store USD; tokens are immutable, USD is derived.
- `RUNNER_CONTRACT.md` -- contract surface for the runner
- `templates/card.md` -- card frontmatter + body template
- `templates/batch_manifest.yaml` -- manifest template
- `templates/project_config.yaml` -- per-project config template
- `examples/b001-03-add-rate-limit-middleware.md` -- example card
- `tests/atomic_rename_test.ps1` -- verifies that moving a file
  between sibling subfolders on NTFS is atomic under concurrent
  contention (named for the underlying syscall it exercises)
