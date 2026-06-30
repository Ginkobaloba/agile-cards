---
AC: AC-CARDS-001
Phase: v1
Status: PENDING
Verifier: Claude (K2)
Verified at: 2026-06-29
Evidence: >
  Chunk: K2. AC text: "Repo exists at the org's GitHub org under the name
  paradigm-agilecards. Verification: Audit -- repo URL recorded in DECISIONS.md."

  State at write time (branch chore/k2-paradigm-agilecards-structure):
  - GitHub repo rename agile-cards -> paradigm-agilecards: see commands log below.
  - Repo URL recorded in DECISIONS.md at repo root:
    https://github.com/Ginkobaloba/paradigm-agilecards
  - DECISIONS.md here is a local stub; the canonical platform DECISIONS.md is
    owned by K18 (AC-COMP-001/002/003).

  PENDING -> PASS flips when: (1) `gh repo view Ginkobaloba/paradigm-agilecards`
  returns the renamed repo, and (2) DECISIONS.md with the URL is on `main`
  (this PR merged). Audit is performed on the branch head, which squash-merges
  verbatim.
---

# AC-CARDS-001 -- Repo exists as `paradigm-agilecards`, URL recorded in DECISIONS.md

## Audit steps

```bash
gh repo view Ginkobaloba/paradigm-agilecards --json name,url,visibility,deleteBranchOnMerge
grep -n "paradigm-agilecards" DECISIONS.md
```

Expected: repo name `paradigm-agilecards`, URL
`https://github.com/Ginkobaloba/paradigm-agilecards`, and the same URL present
in `DECISIONS.md`.

## Result

PENDING -- see frontmatter Evidence. Flips to PASS on merge to `main`.
