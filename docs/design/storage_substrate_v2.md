# Storage Substrate v2: Design Pass

Status: DESIGN. Not implemented. No code or schema changes land before Drew's review.

Author: baseline design pass (Claude, Opus)
Created: 2026-05-19
Branch (suggested): `design/storage-substrate-v2`
Scope: the storage substrate only. The tier model, planner behavior, verifier
semantics, and merge gates are out of scope except where the substrate touches
them.

Grounded against: `SKILL.md`, `RUNNER_CONTRACT.md`, the three templates
(`card.md`, `project_config.yaml`, `batch_manifest.yaml`), `lib/verifier/`, the
runner chunk 1 code under `runner/` and its handoff
(`docs/handoffs/HANDOFF_2026-05-19_runner-chunk-1.md`), the v1.3 verifier
refactor design, and the dashboard submodule data layer
(`dashboard/backend/src/db/sqlite.ts`, `dashboard/backend/src/fs/cards.ts`).

This pass is the conservative voice. Two paradigm-shift agents are attacking the
same question in parallel and may land somewhere more radical. The job here is
to be the option that is provably correct against the actual repo, so the
radical proposals have something honest to be triangulated against.

---

## 0. Why this doc exists

Drew's question, verbatim: "I know we started off with the subfolder idea and
when it was small and the cards were just a user story but honestly that was
when this was in its infancy it would likely be much more efficient to use a
database wouldnt it? and give us increased flexibility if a team was to utilize
this."

He is right, and the rest of this doc is mostly about being precise: why he is
right, which database story is the correct one, and what it costs to get there
from a v1 that is filesystem-canonical with a runner one chunk into a
four-chunk build.

The short version, stated up front so the reasoning can be checked against a
conclusion rather than wandering toward one: the filesystem substrate was the
right call at the infancy stage and is still right for a solo user planning
seven cards on a laptop. It breaks in three distinct ways at scale, and only
one of those three is the "listing gets slow" problem most people expect. The
recommended v2 substrate is database-canonical with the card file preserved as
a per-run projection, an embedded SQLite default for the solo and single-host
case, and a PostgreSQL path for the distributed and multi-tenant case, both
behind one repository interface. The migration is two phases and should be
folded into runner chunk 2, because chunk 2 is already scheduled to rewrite the
exact code the migration touches.

---

## 1. Current state and where it breaks

### 1.1 The v1 substrate in one paragraph

A card is a Markdown file with a YAML frontmatter block (roughly 40 fields) and
a body of named sections. State is two things kept in sync: the subfolder the
file lives in (`backlog/`, `active/`, `amendments/`, `awaiting_standup_review/`,
`done/`, `blocked/`) and a `status:` field that mirrors it. The subfolder is
canonical; the field is convenience. Concurrency is one primitive: an atomic
file move between sibling subfolders, on NTFS a `MoveFileEx` with
`MOVEFILE_REPLACE_EXISTING`, which Python reaches through `os.replace`. A claim
is a move from `backlog/` to `active/`; the loser of a race gets
`FileNotFoundError`. Batch ids come from a `_batches/.counter` file guarded by a
file lock. The runner holds no durable state: every query is a fresh disk read,
the "cards are state, the orchestrator is stateless" principle that `SKILL.md`
section 8b and `RUNNER_CONTRACT.md` both make load-bearing.

That design is coherent and is not an accident. It should not be discarded
casually. But it has a scale ceiling, and the ceiling is closer than the
"infancy" framing suggests.

### 1.2 Where filesystem listing and scanning start to hurt

Two operations get blamed together and should be separated.

Raw directory enumeration (`iterdir`, `Get-ChildItem`, a glob) is cheap. NTFS
handles a single directory with tens of thousands of entries without real
trouble. The runner's `scan_card_dir` does an `iterdir` plus a `stat` per file
plus an mtime sort on every poll tick, and that stays fast into five figures of
files. If the only cost were listing, this doc would be much shorter.

The cost that bites is open-plus-parse. Every consumer that needs a field opens
the file and runs a YAML parse. The dashboard backend (`fs/cards.ts`) walks
every status subfolder at boot, `readFileSync` plus `parseFrontmatter` per
`.md`, and holds the lot in an in-memory `Map`. The runner re-parses each
backlog card every poll because it is stateless by design (`_can_claim` calls
`parse_card_file` every tick). The verifier parses a card to get the AC block.
None of it has an index.

Thresholds, order-of-magnitude until there is telemetry. Up to a few hundred
cards every operation is effectively instant; this is where the system lives
today. Around 500 to 2,000 cards a full scan-and-parse (`/cards stats`, a
dashboard cold boot, a grep across cards) becomes noticeable, one to a few
seconds, still interactive. Around 5,000 to 10,000 it is painful, multiple
seconds paid on every query because nothing is cached durably, and the
dashboard's in-memory map is hundreds of MB per backend process. `done/` gets
there first, because cards are never deleted and a team doing biweekly 50-card
sprints accumulates thousands of done cards within a year. Past roughly 20,000
cards full-scan questions stop being usable interactively.

Those numbers describe wall-clock degradation, and wall-clock is the less
important half of the problem. The more important half is in 1.4.

### 1.3 Where atomic-move-as-claim fails

The claim primitive is correct on exactly one configuration: a single local
NTFS volume, one machine. Outside that configuration it degrades in ways that
are quiet and therefore dangerous.

It is already degrading on the real hardware. The runner ships an
`atomic_rename_sentinel`: an embedded test that the host's atomic-rename
behavior is sane, run at daemon boot, and if it fails the daemon forces
`max_parallel: 1`. The chunk 1 handoff is blunt about why: "On Drew's Windows
machine the embedded atomic-rename test occasionally returns False (NTFS plus AV
plus indexer can let two concurrent renames succeed in the same round)." The
concurrency primitive the whole parallel-execution story rests on already fails
intermittently on the production machine, and the system's response is to give
up on parallelism and run serially. The sentinel is good engineering. It is
also a confession.

Cross-machine the primitive does not degrade, it breaks. `rename(2)` and
`MoveFileEx` atomicity is a single-volume guarantee; the moment two runners are
on two machines the `todo/` tree has to live on a shared filesystem (SMB, NFS, a
sync client), and none of those preserve the rename atomicity or the
`ENOENT`/`EEXIST` race semantics the claim depends on. Client-side caching means
both runners' `os.replace` can "succeed" against a stale directory view: two
runners claim the same card, two executors work it. The contract says "the
runner is responsible for not double-claiming," and the only mechanism it has
for that responsibility is the atomic move. There is no second line of defense.
The mtime FIFO ordering becomes approximate under cross-host clock skew. Orphan
reclaim compares a card's `last_heartbeat` (written by an executor on host A)
against "now" (read by a runner on host B), so clock skew corrupts the
orphan-timeout decision directly: a live executor's card gets reclaimed, or a
dead one's card sits uncollected. And every coordination file (`.daemon.lock`
singleton, `.runner.lock` worktree mutex, `_batches/.counter`) is a file lock,
and `msvcrt`/`fcntl` advisory locks are not reliable over SMB or NFS.

There is also a same-machine, present-tense symptom worth naming because it is
already in a handoff: the chunk 1 author writes that "the Linux FUSE mount
under `/sessions/.../mnt/agile-cards/` has stale-cache issues against in-flight
Windows edits; rely on PowerShell for git operations." That is the same class
of bug, a second view of the tree disagreeing with the first, inside one
developer's workflow, before any multi-host deployment exists.

### 1.4 What a team cannot query today

This is the break that matters most, and it is not a performance break. It is a
capability break, and it exists at card number two, not card number five
thousand. It is simply survivable while the card count is small.

There is no way to ask a relational question of the card store without writing
a bespoke scanner. Everything below is either impossible or is a full-tree
scan-and-parse with hand-rolled aggregation. Per-actor audit: "every card agent
X claimed," "every card Drew approved at an amendment gate," "which verifier
agent signed off this card and what it said." `claimed_by` is greppable;
`verified_by`, the amendment `amended_by`, and human approval provenance are
scattered through body blocks. Anything time-windowed: throughput per week,
cycle time (`finished_at` minus `started_at`), work-in-progress over time.
Cross-project and fleet rollups: one `todo/` tree can serve many projects, a
team can run several trees, and no query spans them. The estimate-versus-reality
loop: the card carries `estimated_tokens`, `actual_tokens`, and the duration
pair specifically so a future planner can learn from the deltas, and `SKILL.md`
calls `/cards stats` future work, but that feature is a full scan of `done/`
forever unless the substrate changes. Dependency-graph questions ("what is
transitively blocked on card X"). Cost rollups (tokens times `tier_pricing.yaml`
summed over a sprint, project, or tenant). And a safe monotonic counter, which
is what `_batches/.counter` is badly approximating with a file plus a lock.

A team needs these the way a solo user does not, because a team has more than
one person asking "where are we" and more than one actor whose work has to be
attributable after the fact. The filesystem substrate cannot answer any of
them. That is the real ceiling, and Drew's instinct about "flexibility if a
team was to utilize this" is pointing exactly here.

### 1.5 Every place the runner design leans on filesystem semantics

Anything below is a place the migration has to touch or consciously preserve.
The runner is mid-build (chunk 1 of 4 shipped); each item notes the owning
chunk.

- Atomic move as the claim primitive. `daemon/claim.py`, `common/atomic.py`.
  `attempt_claim` calls `atomic_move` (`os.replace`) and treats
  `FileNotFoundError` as a lost race. This is the arbitration mechanism. Chunk
  1, load-bearing for all later chunks.
- Folder-as-state. The daemon scans `backlog/`, counts `active/`, reconciles
  `active/` on boot by iterating the directory. Chunk 1.
- mtime-ordered FIFO. `scan_card_dir` sorts by `st_mtime_ns` then name so the
  oldest queued card claims first. Chunk 1.
- Heartbeat and orphan reclaim. The executor writes `last_heartbeat` into the
  on-disk frontmatter; `daemon/orphan.py` scans `active/` and compares it
  against the orphan timeout. Chunk 1.
- Boot reconciliation. The daemon repairs malformed-claim cards (in `active/`
  but missing `claimed_by`) by re-reading and re-stamping the file, the "killed
  between move and stamp" recovery window. Chunk 1.
- The atomic-rename sentinel. `daemon/atomic_rename_sentinel.py` empirically
  tests rename atomicity and demotes `max_parallel` to 1 on failure. Chunk 1.
  This component exists only because the substrate is the filesystem; under a
  database substrate it disappears.
- Targeted in-place frontmatter rewrite. `common/card_io.py` rewrites a fixed
  allowlist of scalar fields line by line, avoiding `yaml.dump` because it
  reorders keys and produces noisy diffs. The handoff flags that "chunk 2 needs
  to extend this so the worker can write `actual_tokens`," and the list-typed
  history fields (`cascade_history`, `verifier_cascade_history`) cannot be
  written through it at all. Chunk 1 built it; chunk 2 was to grow it.
- `atomic_write_text` uses a same-directory tempfile plus `os.replace`, a
  same-volume assumption. Chunk 1.
- Global worktree-creation mutex. `.runner.lock`, a file lock, serializes
  `git worktree add` to dodge the `.git/config.lock` race. Chunk 1.
- Singleton daemon lock. `.daemon.lock`, file plus PID. Chunk 1.
- The cost-cap sentinel-file halt. A filesystem fallback halt for cost-cap
  enforcement; chunk 2 demotes it behind SDK hooks but it is still an artifact.
- The structured event stream. Chunk 4 is scheduled to add `events.jsonl`, a
  per-tree append-only log: a filesystem-native design for something a database
  does natively. The migration should pre-empt it (section 5).
- "Cards are state, the runner is stateless." Every claim, status check, and
  dependency resolution is a fresh disk read. This is a principle, not a
  filesystem feature, and section 2 argues it survives the migration intact. It
  is listed here because it is easy to assume it is filesystem-bound. It is not.
- The verifier is already substrate-agnostic. `lib/verifier/runner.py`
  `verify_card` takes already-parsed `ac_items`, `card_body`, and
  `subjective_evidence`, does no disk I/O and no file moves. Chunk 3 wires it.

The honest read: chunks 1 and 2 are saturated with filesystem assumptions,
chunk 3 (verifier) is nearly clean, and chunk 4 is about to add a
filesystem-native event log for something a database does better. The migration
timing in section 5 falls out of this directly.

---

## 2. What the filesystem model gets right

A v2 substrate has to preserve or deliberately replace each of these. Each is a
real property something downstream depends on.

Human-debuggable with no tooling. You can `cat` a card and see its whole state,
`ls backlog/` is a status report, and a wrong card gets fixed in `vim`. The v2
substrate has to answer "how do I look at one card and fix it" with something
that is not "write a SQL query."

AI-readable with no ORM. This is the load-bearing one. When the runner spawns an
executor, `RUNNER_CONTRACT.md` "Context discipline" gives the executor exactly
the card body, the project repo, and the `trace_id`. The card is the interface.
The executor needs no driver, no connection string, no schema knowledge, no
query language. A Markdown file is the most portable possible contract for an
amnesiac agent. Any v2 substrate that asks the executor to talk to a database
has broken the single cleanest boundary in the system. The recommendation in
3.2 exists largely to protect this.

Offline and zero-ops. No server, no connection to keep alive. The planner works
on a plane. Nothing to provision, secure, back up, or migrate. For a solo user
this is most of the value.

Portable and vendor-neutral. The `todo/` folder is the whole system state. Copy
it, it works on the next machine. The cards outlive the tool.

Git-native audit, with one honest caveat. This property is real but weaker than
the framing assumes, and the migration calculus depends on the precision. The
skill repo (`agile-cards`) is git-tracked. The runtime card data in
`C:\dev\todo\` is, per `README.md`, "intentionally a separate directory and not
part of this repo," and `SKILL.md` section 6 commits cards only "If
`C:\dev\todo\` is under git (dev-meta typically isn't tracking todo/ at the
moment)." So git-diff-of-cards is opt-in and usually off. The audit trail that
exists unconditionally is inside the card: the append-only body blocks
(`cascade_history`, `verifier_cascade_history`, `verifier_history`, completion
notes, the amended-item `original:` blocks, `change_request:` blocks) plus the
batch manifest. There is less git-native audit to "lose" than a naive reading
suggests, and what exists is a hand-rolled append-only log, which 3.3 picks up.

Crash tolerance. The runner can die mid-tick, restart, and reconstruct
everything from disk because disk is the truth. The boot reconcile depends on
this and it works.

Free atomicity for the common case. On one local volume the move primitive
gives correct claim arbitration with zero extra machinery. For the solo
single-host user this is genuinely all they need, and the v2 substrate must not
make that user's life worse to serve the team case.

The "cards are state, orchestrator is stateless" principle. It is the property
that makes the runner survivable across restarts and horizontally scalable in
principle, and it is not a filesystem feature. It says the substrate is the
single source of truth and the orchestrator holds no in-memory mirror. Any
substrate that is itself the single source of truth honors it. A database
honors it better than the filesystem, because a database can serve the
stateless reads concurrently and transactionally where the filesystem cannot.

---

## 3. The candidate models

Four models, each analyzed against the same eight axes: concurrency, audit,
multi-machine, querying, the runner's needs, the dashboard's needs, offline
behavior, and migration cost.

### 3.1 Model A: filesystem-canonical plus database index

The Markdown files stay the source of truth. A database is added as a derived,
rebuildable index for fast queries. A watcher (the dashboard's chokidar layer,
or a runner hook) keeps the index current. `/cards reindex` rebuilds it from
the tree. If the index and the disk disagree, the disk wins and the index gets
rebuilt.

The dashboard already ships a weak version of this. Its in-memory `Map` is an
index; its SQLite database (`db/sqlite.ts`) holds tokens, sprints, and retros
but pointedly not cards, with the comment that "the disk is the source of truth
and any mismatch would create a which-one's-right problem with no good answer."
Model A is that instinct formalized: promote the in-memory map to a real on-disk
index table with columns and indexes.

Concurrency: unchanged, still the atomic move; the index observes claims, it
does not arbitrate them, so every cross-machine and sentinel problem from 1.3
remains. Audit: unchanged, body blocks plus optional git. Multi-machine: reads
fixed if the index is on a shared database, writes and claims not fixed at all.
Querying: fixed, which is the entire point and it delivers. Runner's needs: the
runner keeps moving files and may read the index for hints, but cannot trust it
for the claim decision because the index lags disk. Dashboard's needs: large
win, no more multi-thousand-file boot walk. Offline: fully preserved, the index
is a cache. Migration cost: lowest of the four; nothing about the card, runner,
verifier, or planner changes.

Honest verdict: Model A is a half-measure and it is important to say so. It buys
query speed and nothing else. The concurrency ceiling, the cross-machine break,
the sentinel demotion, the lock-over-SMB problem are all exactly where they
were. As a destination it leaves you back in this design doc within a year. Its
real value is as a stepping stone, which section 5 uses it for.

### 3.2 Model B: database-canonical plus file projection

The database is the source of truth. The Markdown card file becomes a
projection: a view generated from the database, written to disk when and where
something needs to read or edit it as a file, not kept as the master copy.

The critical design move, the one that makes this model viable rather than a
betrayal of section 2, is when the projection happens. When the runner spawns an
executor it already creates a per-card worktree; it writes the card's `.md`
projection into that worktree at the same time. The executor reads and writes
that file exactly as in v1: same body sections, same `change_request:`
mechanism, same completion notes, byte-identical interface. When the executor
exits, the runner parses the projection's completion notes, `change_request:`
block, and `actual_tokens` and writes those back to the database. The executor
never knows the database exists. The AI-readable, ORM-free contract from section
2 is preserved completely. The projection is ephemeral and per-run, not a second
source of truth, so the dashboard's "which one's right" objection does not
apply: the database is unambiguously right and the projection is unambiguously a
copy.

Concurrency: fixed properly. The claim becomes a conditional update in a
transaction. Illustratively:

```
UPDATE cards
   SET status = 'active', claimed_by = :runner,
       started_at = :now, last_heartbeat = :now
 WHERE id = :card_id AND status = 'backlog';
-- rowcount 1: we won the claim. rowcount 0: someone else did.
```

Two runners racing the same card both run this; the database serializes them
and exactly one sees `rowcount == 1`. Correct on one machine and correct across
machines against one database, with no sentinel, no atomic-rename test, no
`max_parallel` demotion.

Audit: this is the cost, paid deliberately. Git-diff-of-card goes away as the
live mechanism, replaced by a `card_events` table: an append-only row per
transition, claim, escalation, verification, amendment, and merge, each carrying
an actor and a timestamp. That is a better structured audit trail than
git-blame across reordering YAML and it is directly queryable, which git history
never was. What you lose is "clone the repo and read history with standard
tools." You can buy it back: project cards and event history to Markdown on a
schedule and commit that to a git archive. Whether to do that is a genuine fork
(section 7, decisions list), partly a portfolio question.

Multi-machine: fixed, one database, many runners and dashboard processes.
Querying: fixed natively, every question in 1.4 becomes a normal query.
Runner's needs: the runner stops moving files, the claim is the conditional
update, folder-as-state becomes a `status` column. The stateless-orchestrator
principle is preserved and arguably strengthened, since a conditional update is
a cleaner stateless claim than a file move and cannot half-succeed the way
"move then stamp" can; the malformed-claim boot-reconcile window from 1.5 simply
stops existing. Dashboard's needs: ideal, it already has a backend, SQLite, and
an SSE event bus; the chokidar watcher gets replaced by database change feeds or
a subscription to the runner's event writes. Offline: depends on the database
technology (section 4); with embedded SQLite the offline and zero-ops property
is fully kept, with Postgres it is not. Migration cost: real but bounded, walked
in section 5, and lower than it looks because it replaces scheduled chunk 2 work
rather than adding to it.

Honest verdict: Model B fixes all three breaks from section 1, preserves every
section 2 property that can be preserved, and replaces the one it cannot
(git-diff audit) with something measurably better for a team. It is the
recommended model.

### 3.3 Model C: event-sourced

Cards are not a row that gets updated. They are an append-only log of events
(`CardCreated`, `CardClaimed`, `CardExecuted`, `CardVerified`,
`AmendmentRequested`, `CardMerged`). The current state of a card is a
projection: fold its events and get its state. The filesystem and a queryable
`cards` table are both projections; neither is the master.

This model deserves real respect, because the v1 card schema is already drifting
toward it and nobody planned that. The card body is full of hand-rolled
append-only logs: `cascade_history`, `verifier_cascade_history`,
`verifier_history`, accumulating completion notes, the amended-item `original:`
block that preserves prior state forever. `RUNNER_CONTRACT.md` calls
`cascade_history` "forensic across the card's entire run." A v1 card today is,
quite literally, current state in the frontmatter plus a pile of append-only
event logs in the body. Event sourcing just names that and makes it queryable,
and chunk 4's planned `events.jsonl` is an event log by another name.

Concurrency: the append to the log is the arbitration; a claim is "append a
`CardClaimed` event," conflicts resolve by log order, first claim wins. Correct
by construction. But the log needs a total order, which is itself a database
table with a sequence or a real log system, so event sourcing does not remove
the need for a database, it changes the schema's shape. Audit: best of the four
by a wide margin, the log is the audit trail natively, no separate table
needed. Multi-machine: fixed, the log is the serialization point. Querying: you
query projections, and you can build a new projection retroactively, which is
genuinely powerful. Runner's needs: a larger conceptual shift, the runner
appends events instead of moving files or updating rows, still stateless-
compatible. Dashboard's needs: it becomes a projection consumer and its SSE feed
becomes "tail the event stream," more natural than chokidar. Offline: a
single-writer embedded log (an append-only SQLite table) is fine offline;
distributed offline, two machines appending while partitioned and reconciling
later, is the genuinely hard mode and complexity the project does not need yet.
Migration cost: highest, you are re-conceiving the model, not moving storage.

Honest verdict: Model C is the most correct long-horizon model, and the fact
that the card schema invented a worse version of it on its own is strong
evidence for that. But adopting full event sourcing now is over-building for
where the project is (runner one chunk into four, zero paying tenants). The
baseline position (section 7) is Model B with a `card_events` table from day
one, which captures most of C's value because that table is a proto event log,
leaving C a refactor away rather than a rewrite away. If a paradigm-shift agent
argues for C, the question to put to it is concrete: what does full event
sourcing buy in the next six months that a `card_events` table does not, and is
that worth the highest migration cost on the board while the runner is still
being built.

### 3.4 Model D: hybrid that promotes

Filesystem-canonical for solo and small. When a deployment crosses a team
threshold, it transparently promotes to database-canonical.

This is the model that most directly matches Drew's "it was fine when small"
framing, so it has to be taken seriously and then taken apart. The problem is
the word "transparently." A substrate that is sometimes filesystem-canonical and
sometimes database-canonical has two claim primitives, two audit models, two
sets of failure modes, and a migration cliff sitting in the middle of the
product. Every component (runner, verifier, dashboard, planner) has to handle
both modes forever, including the half-migrated state. And the promotion is a
data migration that fires exactly when a team is busiest, because crossing the
team threshold is the same event as the team getting busy. The maintenance cost
of Model D is not the average of A and B, it is the sum, plus the switch.

As an architecture, Model D is rejected. But its instinct, do not make the solo
user carry team-sized machinery, is correct and must be honored. The honest way
to honor it is not a runtime canonical switch. It is to pick Model B as the
single canonical model and make the database technology the thing that varies:
embedded SQLite for solo (a single file, offline, zero-ops, portable, behaves
almost exactly like the filesystem operationally), PostgreSQL for the
distributed team and the SaaS, both behind one repository interface so the
codebase has exactly one canonical model and one claim primitive. "Promotion"
becomes a deployment choice, a connection string and a driver, not an
architecture change. That is the correct version of D, and it is folded into
the recommendation rather than standing as a separate model. One model, two
deployments.

### 3.5 Side-by-side

A summary, not the argument. The argument is in 3.1 through 3.4.

| Axis              | A: FS + index        | B: DB-canonical        | C: event-sourced       | D: promote            |
|-------------------|----------------------|------------------------|------------------------|-----------------------|
| Concurrency       | unchanged, broken    | fixed (txn claim)      | fixed (log append)     | two primitives        |
| Audit             | body blocks + git    | `card_events` table    | the log itself (best)  | both, inconsistently  |
| Multi-machine     | reads only           | fixed                  | fixed                  | only after promotion  |
| Querying          | fixed                | fixed                  | fixed + retroactive    | mode-dependent        |
| Runner impact     | none                 | claim rewrite          | model rewrite          | must handle both      |
| Dashboard impact  | large win            | ideal fit              | ideal fit              | must handle both      |
| Offline           | preserved            | preserved (SQLite)     | preserved (SQLite)     | preserved then not    |
| Migration cost    | lowest               | moderate               | highest                | sum of A and B        |

---

## 4. Database technology, if a database is in the answer

A database is in the answer. This section is the honest tradeoff for the two
serious candidates, for both the solo case and the team case.

### 4.1 SQLite

SQLite is already in the stack. The dashboard backend uses `better-sqlite3` in
WAL mode with `foreign_keys` on and `synchronous = NORMAL`, and the team knows
it.

For the solo case SQLite is strictly better than the filesystem. It is still a
single file, still zero-ops, still offline, still portable (copy the file, or
commit a SQL dump for backup), and on top of that it gives real transactions
and real queries. There is no solo-case downside. The solo user gets the
correct claim primitive and a fast `/cards stats` and loses nothing.

For a single-host team SQLite still works. WAL mode allows many concurrent
readers plus one writer. The writers here are the runner daemon, the planner,
and the dashboard's drag-drop moves; on one host that is one SQLite file and the
writes serialize inside the binding in well under a millisecond, and a claim
transaction is tiny. A fleet of executors on one host is fine too, because
executors do not write the database directly: they write completion notes into
their projected card file and the runner does the database write. The dashboard
is already designed to run on a single host (`BROOKFIELD_PC:4070` behind a
Cloudflare tunnel per its README), so "a team" in the current product topology
is a single-host deployment, and SQLite covers it.

SQLite has two hard ceilings, and they are the same ceiling. It must not live on
a network filesystem (its own docs say so; its locking relies on filesystem
locks that SMB and NFS implement incorrectly), so runners on multiple machines
sharing one SQLite file is not an option. And it allows one writer at a time,
fine at this system's write rate on one host, not fine for genuinely concurrent
multi-host writers. SQLite is a single-host substrate.

### 4.2 PostgreSQL

PostgreSQL is the multi-host answer. It gives true MVCC concurrency, multiple
simultaneous writers, and `SELECT ... FOR UPDATE SKIP LOCKED`, the textbook
primitive for exactly this workload: several runners pulling distinct cards off
a backlog with zero lock contention and no double-claim. Illustratively:

```
SELECT id FROM cards
 WHERE tenant_id = :tenant AND status = 'backlog' AND deps_satisfied
 ORDER BY created
 FOR UPDATE SKIP LOCKED
 LIMIT 1;
```

Each runner that runs this gets a different card or no card, never the same card
as another runner. That is the distributed claim, solved, with a built-in
language feature. Postgres is also the only honest option if the multi-tenant
SaaS play is real, because it has the isolation tools (schemas, row-level
security, real roles) that SQLite lacks; section 6 depends on this.

The cost is that Postgres is a server: it has to be run, secured, backed up,
version-upgraded, and connection-pooled, and it is not offline unless a local
instance is running, which a solo user on a laptop should not have to care
about. For the person planning seven cards on a plane, Postgres is pure
overhead. Drew has separately approved PostgreSQL for resume and portfolio
purposes, so the skill is not a blocker and he wants the credential on the
board. That argues for building the Postgres path deliberately rather than
treating it as a someday-maybe. It does not argue for forcing Postgres onto the
solo user.

### 4.3 Other options, briefly

A document database (MongoDB and similar) is tempting because a card looks like
a document, but it is the wrong call: you lose relational queries (dependency
joins, cross-card aggregates, group-by) and gain nothing a JSON column in SQLite
or Postgres does not already give you. DuckDB is excellent for the analytical
side (`/cards stats`, cost rollups, estimate-versus-actual) but wrong as the
primary transactional store; it is a reasonable future read-replica for heavy
analytics, not the substrate. A key-value store or Redis is wrong: the workload
needs durability and relational queries. A real log system (Kafka and the like)
only enters if Model C is chosen and the deployment is genuinely distributed,
which is many bridges too far for where this project is.

### 4.4 The physical schema shape

A sketch, not DDL, and it applies whether the engine is SQLite or Postgres
because both support a JSON column type.

A `cards` table promotes the hot, queried fields to typed columns with indexes:
`id`, `tenant_id`, `project`, `batch`, `status`, `points`, `stakes`,
`difficulty`, `claimed_by`, `created`, `started_at`, `finished_at`,
`last_heartbeat`, `merge_status`, `verified_at`, `verified_by`,
`estimated_tokens`, `actual_tokens`, `story_hash`, `trace_id`. The long tail of
the roughly 40-field frontmatter that nobody filters or joins on lives in a
`frontmatter_json` column. The card body prose lives verbatim in a `body_md`
text column so the projection in 3.2 can reproduce the file byte-for-byte. This
hybrid (typed columns for what you query, JSON for the rest) keeps the schema
flexible against a frontmatter that has changed every minor version while still
giving real indexes where queries need them.

A `card_events` table is append-only: `event_id`, `card_id`, `tenant_id`,
`seq`, `type`, `actor_id`, `actor_type`, `at`, `payload_json`. Every transition,
claim, escalation, verification attempt, amendment, and merge writes one row.
This table is the audit trail that replaces git-diff-of-cards, it is the home
for what chunk 4 was going to put in `events.jsonl`, and it is the proto event
log that keeps Model C one refactor away instead of one rewrite away. Supporting
tables: `batches` (replacing `_batches/.counter` with a real sequence and the
manifest contents), `dependencies` as explicit `card_id` to `depends_on_id`
edges so the graph is queryable, and the dashboard's existing `tokens`,
`sprints`, `sprint_cards`, `retros` tables, which gain a `tenant_id` column.

### 4.5 Recommendation for section 4

Program against one repository interface, a thin data-access layer exposing
operations like `claim_card`, `transition`, `append_event`, `get_card`,
`query_cards`. Ship two implementations behind it. The default, and the only one
a solo user ever touches, is embedded SQLite: offline, zero-ops, single file,
already in the stack. The second, built deliberately because the SaaS and
distributed ambition is real, is PostgreSQL: multi-writer, `SKIP LOCKED` claims,
the isolation primitives section 6 needs. SQLite is honestly enough for
everything Drew is doing today and for a single-host team; Postgres is the
unlock for distributed runners and multi-tenancy. The discipline that makes this
cheap is building the repository interface now, so the day Postgres is needed is
a configuration change and a second implementation, not a rewrite. Do not run
Postgres before there is a reason to. Do build the seam now.

---

## 5. Migration path from v1

v1 is filesystem-canonical with a runner one chunk into a four-chunk build. The
central claim of this section is that the migration should be folded into runner
chunk 2 rather than run separately, because chunk 2 is already scheduled to
rewrite the exact code the migration touches.

### 5.1 Constraints the migration has to respect

The executor interface does not change: the agent that does the work keeps
reading and writing a Markdown card file, same sections, same `change_request:`
escape valve, same completion notes, protected by the projection mechanism from
3.2. The verifier barely changes: `lib/verifier/runner.py` already takes parsed
data, so the runner sources that data from the repository instead of from a
`parse_card_file` call and the verifier does not notice. No half-migrated
canonical state: at every step there is exactly one source of truth, and the
cutover happens at a single defined moment. And v1 cards migrate losslessly,
proven not asserted (5.6).

### 5.2 Phase 0: the index, no canonical change

Adopt Model A first, as a stepping stone, because it is risk-free and builds the
thing every later phase reuses. The filesystem stays canonical. A SQLite index
is added, reusing the dashboard's existing database file and chokidar watcher to
keep it current. Nothing about cards, the runner, the verifier, or the planner
changes. The dashboard immediately stops doing a multi-thousand-file boot walk
and `/cards stats` gets something real to query. The work done here is not
throwaway: the index schema is the draft of the 4.4 `cards` table, and a
`/cards reindex` rebuild-from-disk command becomes the importer's verification
harness and a permanent operational tool. Phase 0 is reversible, ships value on
its own, and commits the project to nothing. It is worth doing regardless of
what the paradigm-shift agents conclude.

### 5.3 Phase 1: schema, repository interface, importer

Define the canonical schema from 4.4 (`cards`, `card_events`, `batches`,
`dependencies`, plus the dashboard tables gaining `tenant_id`). Build the
repository interface from 4.5. Build the importer: it reads every v1 card file,
parses the frontmatter and body sections, and writes rows. Frontmatter scalars
go to typed columns or `frontmatter_json`; the body prose goes verbatim to
`body_md`; the append-only body blocks (`cascade_history`,
`verifier_cascade_history`, `verifier_history`, completion notes,
`change_request:` blocks, amended-item `original:` blocks) each become
`card_events` rows, the moment the hand-rolled in-body event logs finally become
a real one. Phase 1 still does not flip canonical: the database is built,
populated, and verified alongside a filesystem that is still the truth.

### 5.4 Phase 2: flip canonical

This is the single cutover moment, sequenced to land with runner chunk 2. The
runner's claim stops being `atomic_move` plus an in-place stamp and becomes the
repository `claim_card` call, the conditional update from 3.2; folder-as-state
becomes the `status` column; the atomic-rename sentinel, the `max_parallel`
demotion, and the malformed-claim boot-reconcile window are deleted, not ported,
because the conditions that made them necessary no longer exist. The planner's
write phase changes from "write `.md` files to `backlog/`" to "insert `cards`,
`card_events`, and `batches` rows"; the dry-run, the validation, the DAG cycle
check, the planning passes are unchanged, only the final write target moves. The
executor still gets a file: the runner writes the card projection into the
worktree it already creates, the executor works the file, and on exit the runner
parses the projection's completion notes, `change_request:` block, and
`actual_tokens` and writes them back, one extra read plus one parse the runner
was substantially already doing. The dashboard's `fs/cards.ts` chokidar layer is
replaced by repository queries and a subscription to the runner's event writes.
After Phase 2 the database is canonical and the filesystem `todo/` tree, if kept
at all, is a frozen pre-migration archive.

### 5.5 Does the runner's chunk 2 and later have to change

Yes, and this is the part most likely to be misjudged. The change is a
substitution, not an addition, and on net it removes work. Chunk 2 was already
scheduled to touch the claim path, the worktree path, the cost-cap path, and
`common/card_io.py`. The handoff says in plain words that "chunk 2 needs to
extend" the in-place frontmatter rewriter so the worker can write
`actual_tokens`, and that rewriter cannot currently write the list-typed history
fields at all. Under the migration that rewriter is not extended, it is deleted,
because the repository owns writes now: the most fragile component in the runner
never has to learn to write `cascade_history`. The atomic-rename sentinel is
deleted; the cross-machine caveats stop being caveats. So chunk 2 with the
migration folded in is roughly net-neutral on effort against chunk 2 as planned,
and it ends with fewer fragile components. Chunk 3 (verifier dispatch) gets
easier because the verifier already speaks parsed data, and chunk 4 gets easier
because its planned `events.jsonl` is simply the `card_events` table that
already exists, so chunk 4 stops needing to design a filesystem event log at
all. The one sequencing mistake to avoid: do not ship chunk 2 on the filesystem
substrate first and migrate afterward, because that builds the `actual_tokens`
rewriter extension and then throws it away and ships a runner that has to be
partly rewritten one chunk later. Folding the migration into chunk 2 is cheaper
than doing them in series. Finishing the whole runner on the filesystem first
and migrating as a clean v2 afterward is the other coherent option, slower and
more wasteful but lower-risk in that the runner reaches a known-good state
before anything moves; that genuine tradeoff is in the decisions list.

### 5.6 Lossless migration of v1 cards

Lossless, and provable rather than asserted. Every frontmatter field has a home,
a typed column or `frontmatter_json`; the body prose is stored verbatim in
`body_md`; the append-only body blocks become `card_events` rows. Nothing in a
v1 card has nowhere to go. The proof is the projection: Phase 1's importer is
paired with a projector that regenerates a card's `.md` from its database rows,
and the verification harness runs the importer over every v1 card, runs the
projector back, and diffs the result against the original file. The migration is
lossless when that diff is empty across the whole corpus, which is also why
`/cards reindex` from Phase 0 is not throwaway, it is the same walk the harness
needs. One deliberate preservation: if a project was tracking `todo/` in git,
that git history is the pre-migration audit trail and should be kept as a frozen
archive repository, not deleted. Post-migration audit lives in `card_events`,
pre-migration audit lives in that archive, both are reachable, neither is
overwritten.

---

## 6. Multi-tenancy

This section is the one that matters most for the product and consulting play
(Paradigm Coding Solutions), and the one the filesystem substrate cannot do at
all. The README already shows the ambition: a hosted instance at
`app.projectnexuscode.org`, Cloudflare Access gating, per-user bearer tokens, a
marketing site, and a PolyForm Noncommercial license whose whole point is that
"commercial use requires a separate arrangement." The substrate has to be ready
for that even though no tenant is paying yet.

### 6.1 The three tenancy models

Row-level tenancy: one database, a `tenant_id` column on every row, every query
filtered by it, and in Postgres a row-level-security policy that enforces the
filter at the database layer regardless of application bugs. Cheapest
operations, one database to run, back up, and migrate. The risk is a query that
forgets its `WHERE tenant_id =`, which RLS exists precisely to neutralize, since
RLS makes the database refuse to return another tenant's rows even when the
application asks wrong. Noisy-neighbor effects are real but manageable. Best fit
for many small tenants.

Schema-per-tenant: one Postgres database, one schema per tenant. Stronger
isolation than a shared table, per-tenant migration is possible, still one
server. The pain is migration fan-out: a schema change has to be applied N
times, unpleasant past roughly a hundred tenants. The awkward middle option.

Database-per-tenant: one database per tenant. The strongest isolation,
per-tenant backup, restore, encryption, and a trivial answer to "delete this
tenant's data." The heaviest operations and the most connection-pool sprawl.
Best fit for a few large tenants who contractually demand isolation, which is
exactly what an enterprise consulting client tends to demand.

### 6.2 Permissions, actor identity, and audit

Every actor gets an identity: a human user, a runner daemon, an executor agent,
a verifier agent. The dashboard already has the seed of this in its `tokens`
table (a SHA-256 hash, a label, timestamps); it gains a `tenant_id` and a role.
Roles gate the things `SKILL.md` and `RUNNER_CONTRACT.md` already say need
gating: who can approve an AC amendment, who can satisfy a `requires_pre_approval`
card, who can clear a high-stakes merge. Those gates exist in v1 as
human-in-the-loop conventions; multi-tenancy is what forces them to become
enforced permissions tied to an identity.

Per-actor audit is where the substrate change pays off most directly. The
`card_events` table carries `actor_id`, `actor_type`, and `tenant_id` on every
row, so "everything agent X did," "every card a given human approved," and "the
full action history for one tenant" all become ordinary queries. The filesystem
substrate cannot answer any of them (1.4). For a consulting business that has to
show a client exactly what happened in that client's workspace, per-actor audit
is part of the product, and it falls out of Model B for free.

### 6.3 Data isolation and the day-one decision

Row-level security is the backstop: an application bug cannot leak across
tenants because the database itself enforces the boundary. The card projection
files from 3.2 are written into per-card worktrees, and `RUNNER_CONTRACT.md`
"Worktree isolation and cross-contamination defense" already specifies six
isolation requirements for those worktrees (clean env blocks, no shared
credential variables, per-worktree git config); that story was written for
parallel executors on one tenant and carries over directly to executors across
tenants, one of the few pieces of v1 that needs no rework for multi-tenancy.

One schema decision should be made now and not deferred: `tenant_id` belongs in
the schema from the very first migration, including in the solo SQLite
deployment, where it simply always equals a single default tenant the solo user
never sees. Retrofitting a tenant key into a populated multi-table schema later
is among the most painful changes a project can choose to defer. Putting the
column in on day one costs nothing and is invisible to the solo user. This is a
recommendation, not an open fork, because it is not genuinely a fork; it is just
correct.

### 6.4 Recommendation for section 6

For a consulting shop with a self-serve tier and an enterprise tier, the
pragmatic split is row-level tenancy with RLS as the default for the self-serve
many-small-tenants tier, and database-per-tenant offered as the enterprise
upsell for clients who contractually require hard isolation. Skip
schema-per-tenant; it carries the migration pain of the row-level model and most
of the ops weight of the per-database model without being best at either. The
tenancy model is a genuine fork because it depends on the go-to-market shape,
and it is in the decisions list. The day-one `tenant_id` column is not a fork;
do it.

---

## 7. Baseline recommendation

Adopt Model B, database-canonical with the card file preserved as a per-run
projection. Default the database to embedded SQLite for the solo and
single-host case. Build a PostgreSQL implementation behind the same repository
interface for the distributed-fleet and multi-tenant case. Reach Model B through
the section 5 path: Phase 0 the index now, Phases 1 and 2 folded into runner
chunk 2.

Drew is right that scale wants a database, and the precise reason changes the
recommendation. The popular reason, "listing files gets slow," is the least
important of the three breaks; directory enumeration stays fine into five
figures. The two breaks that decide this are that the concurrency primitive is
already flaky on the real production hardware and cannot cross a machine
boundary at all, and that there is no way to ask a relational question of the
card store. A query index (Model A) fixes only the third, which is why A is a
stepping stone and not a destination. Model B fixes all three.

Model B preserves the section 2 properties that matter. "Cards are state,
orchestrator stateless" is preserved and arguably strengthened, since a
conditional-update claim cannot half-succeed the way "move then stamp" can. The
executor's ORM-free Markdown interface is preserved exactly through the per-run
projection, the single most important compatibility move in the design and the
reason B is not a betrayal of the thing that makes the system clean. Offline,
zero-ops, and portable are preserved by defaulting to embedded SQLite. The one
property B cannot keep, git-diff-of-cards as the live audit mechanism, is
replaced deliberately by the `card_events` table, a better and queryable
per-actor trail, and can be partially bought back with a scheduled git archive
projection if Drew wants it.

Event sourcing (Model C) is the most correct long-horizon model, and the
strongest evidence is that the v1 card schema reinvented a worse version of it
on its own. But adopting full event sourcing now is over-building for a project
whose runner is one chunk into four and whose tenant count is zero. The baseline
move is Model B with a `card_events` table from day one. That table is a proto
event log, so if the fleet later needs event sourcing the path is a refactor and
not a rewrite. B is not a detour away from C; it is the first correct step
toward it, and it delivers value at every phase.

Model D is rejected as an architecture, because two canonical models with a
runtime switch costs the sum of both models' maintenance plus a migration cliff
that fires when the team is busiest. Its correct instinct, do not burden the
solo user, is honored instead by the SQLite-default, Postgres-path split: one
canonical model, one claim primitive, two deployments.

On timing: do not migrate ahead of need and do not stand up Postgres before a
tenant needs it, but build the repository interface now and fold the canonical
flip into runner chunk 2. Chunk 2 already has to rewrite the claim path, the
cost-cap path, and the frontmatter rewriter; folding the migration in means the
fragile in-place YAML rewriter is deleted instead of extended, the atomic-rename
sentinel is deleted instead of maintained, and chunk 4's `events.jsonl` is
absorbed into a table that already exists. Migrating after chunk 2 ships builds
throwaway code and then partly rewrites a runner. Here the cheap path and the
correct path are the same path, which does not happen often, and the project
should take it.

This is the baseline, deliberately the option provable against the actual
repository rather than the most ambitious one imaginable. The paradigm-shift
agents may land somewhere more radical, most plausibly on full event sourcing or
a distributed log. The useful comparison is not radical versus safe, it is
specific: what does the more radical substrate buy in the next two runner chunks
that Model B with a `card_events` table does not, what does it cost in migration
risk while the runner is still being built, and does it still preserve the
executor's ORM-free Markdown interface, because anything that breaks that
boundary pays a price this baseline does not.

---

## DECISIONS FOR DREW

These are the genuine forks. Calls that are clear from the repo (preserve the
executor's Markdown interface via projection, put `tenant_id` in the schema on
day one, do Phase 0 because it is free) are recommendations in the body above
and are not relitigated here.

1. Postgres now, or Postgres-shaped later. Is the distributed-fleet and
   multi-tenant SaaS a near-term commitment or a someday-maybe? If someday-maybe,
   ship SQLite plus the repository interface and do not write the PostgreSQL
   implementation until a tenant is paying. If near-term, build both
   implementations now so the distributed path is exercised early. The
   recommendation leans toward the interface now and the Postgres implementation
   on first real need, but the "near-term commitment" answer is a business call
   only Drew can give.

2. The audit trail and the portfolio question. Is "git diff of card files" a
   property worth keeping by projecting cards and their event history back to
   Markdown on a schedule and committing that to a git archive? Or is
   `card_events` in the database the audit trail, full stop? This is partly
   technical and partly a portfolio-and-identity question, since
   version-controlled work as a portfolio artifact is one of Drew's standing
   preferences and a database is a less legible portfolio object than a git log.

3. Migration timing against the runner. Fold the canonical flip into runner
   chunk 2, or finish the runner v1 on the filesystem (chunks 2 through 4) and
   migrate as a clean v2 afterward? Folding in is cheaper and avoids throwaway
   code, and the recommendation argues for it. Finishing first is slower and
   more wasteful but lets the runner reach a known-good state before the
   substrate moves under it. A real cost-versus-risk call, not a correctness
   call.

4. How far toward event sourcing, now. Plain `card_events` audit table inside
   Model B (the baseline recommendation), versus committing to event sourcing as
   the model (Model C). The two paradigm-shift agents may well push for C. The
   baseline position is B with a `card_events` table that keeps C a refactor
   away. The useful input for this decision is the specific question in section
   7: what does full event sourcing buy in the next six months that the table
   does not.

5. The multi-tenancy model, when the SaaS play activates. Row-level tenancy with
   row-level security as the default self-serve tier, versus database-per-tenant,
   versus offering both as tiers. The recommendation is row-level-plus-RLS for
   self-serve and database-per-tenant as the enterprise upsell, skipping
   schema-per-tenant entirely. This depends on the go-to-market shape (many
   small tenants, a few large ones, or both), which is Drew's call.
