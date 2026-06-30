---
AC: AC-CARDS-002
Phase: v1
Status: PENDING
Verifier: Claude (K2)
Verified at: 2026-06-29
Evidence: >
  Chunk: K2. AC text: "Repo structure includes backend/ (Python or C#),
  frontend/ (Boards UI), tests/, docs/, and CI config. Verification: Audit -- ls check."

  State at write time (branch chore/k2-paradigm-agilecards-structure):
  - backend/  -> FastAPI scaffold (app.py, pyproject.toml, tests/, README.md). Python.
  - frontend/ -> React/Vite Boards UI (moved from apps/board/frontend).
  - tests/    -> repo-level integration/e2e placeholder (README.md).
  - docs/     -> repo docs (adr/, handoffs/, board/).
  - CI config -> .github/workflows/ci.yml (4 jobs incl. backend fastapi scaffold).

  PENDING -> PASS flips when the `ls` check passes on `main` (this PR merged).
  Audit is performed on the branch head, which squash-merges verbatim.
---

# AC-CARDS-002 -- Repo structure: backend/ frontend/ tests/ docs/ + CI

## Audit steps

```bash
ls -d backend frontend tests docs .github/workflows/ci.yml
ls backend/app.py backend/pyproject.toml
```

Expected: all paths present. `backend/` is Python (FastAPI), `frontend/` is the
Boards UI, `tests/` and `docs/` exist, and CI config is present.

## Result

PENDING -- see frontmatter Evidence. Flips to PASS on merge to `main`.
