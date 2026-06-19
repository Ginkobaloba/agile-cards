# ADR-2026-06-19: Rulesets v2 Branch Protection Migration

**Status:** Accepted
**Date:** 2026-06-19
**Author:** Drew Mattick (via automation agent)

---

## Context

The `main` branch on `Ginkobaloba/agile-cards` was protected by a GitHub v1 Branch Protection rule that required a status check named `runner battery (lint + tests)`.

When PR #37 (`feat/monorepo-merge-with-board`) was opened, it introduced a monorepo restructure that renamed the CI workflow job from `runner battery (lint + tests)` to `engine runner battery (lint + tests)` and added two new jobs:

- `board frontend battery (lint + vitest)`
- `board backend battery (build + tests)`

The v1 rule was never updated. Because GitHub matches required check names exactly, the old context name never appeared in PR #37's check suite, and `mergeStateStatus` was permanently BLOCKED even though all five checks (including two Socket Security checks) passed.

This is a silent failure mode inherent to v1 Branch Protection: there is no warning when a required context name drifts out of sync with the actual workflow output.

GitHub Rulesets v2 is the modern replacement. It supports:
- More granular control per rule type
- Bypass actors (not used here, mirrors `enforce_admins=true`)
- Better UI discoverability
- The same `required_status_checks` semantics but with cleaner management

---

## Decision

Migrate from legacy v1 Branch Protection to a single GitHub Ruleset named `main-branch-protection` on the `Ginkobaloba/agile-cards` repository.

The Ruleset enforces:
1. No branch deletion
2. No force push (non-fast-forward commits blocked)
3. Pull request required before merge (0 required reviewers, matching current v1 which had 0)
4. Three specific status checks must pass (the three battery jobs as they exist on PR #37 and will exist post-merge on main)

No bypass actors are configured. This mirrors the v1 `enforce_admins=true` setting, meaning admins cannot bypass the rules.

### Why `~DEFAULT_BRANCH` instead of `refs/heads/main`

The Ruleset targets `~DEFAULT_BRANCH` pattern. This is more robust than a hardcoded `refs/heads/main`: if the default branch is ever renamed, the Ruleset stays correct without manual edits.

### Why three battery checks, not five

The two Socket Security checks (`Socket Security: Project Report`, `Socket Security: Pull Request Alerts`) are emitted by the Socket GitHub App, a third-party service. Gating merges on a third-party app creates an availability dependency outside our control. If Socket is down or their app is temporarily de-authorized, no PR could merge. The three battery checks cover all first-party CI assertions.

### Why `strict: false` (not strict required status checks)

The v1 protection had `strict: false`, meaning the branch does not need to be up to date with main before merging. Keeping this consistent prevents unnecessary re-runs on older PRs.

### Why `required_approving_review_count: 0`

The v1 protection did not configure required reviewers. Setting this to 0 preserves that behavior.

---

## Intended Ruleset Payload

```json
{
  "name": "main-branch-protection",
  "target": "branch",
  "enforcement": "active",
  "conditions": {
    "ref_name": {
      "include": ["~DEFAULT_BRANCH"],
      "exclude": []
    }
  },
  "bypass_actors": [],
  "rules": [
    {
      "type": "deletion"
    },
    {
      "type": "non_fast_forward"
    },
    {
      "type": "pull_request",
      "parameters": {
        "required_approving_review_count": 0,
        "dismiss_stale_reviews_on_push": false,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_review_thread_resolution": false
      }
    },
    {
      "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": false,
        "required_status_checks": [
          {
            "context": "engine runner battery (lint + tests)"
          },
          {
            "context": "board frontend battery (lint + vitest)"
          },
          {
            "context": "board backend battery (build + tests)"
          }
        ]
      }
    }
  ]
}
```

### Note on `integration_id`

Omitting `integration_id` (rather than setting it to the GitHub Actions app id `15698`) tells GitHub to accept this check from any app. This is more portable and avoids breakage if the app id ever changes.

---

## Consequences

**Positive:**
- PR #37 unblocked immediately after the Ruleset became active and v1 was deleted.
- Future workflow renames are visible: when a job name changes, the required check name in the Ruleset must be updated (intentional gating, not silent drift).
- Single source of truth for branch protection going forward.

**Negative / Maintenance:**
- Adding a new required check in the future requires a Ruleset PATCH via the API or GitHub UI. The Ruleset id and UI link are documented below for discoverability.

**Adding a new required check:**
1. Find the exact check name from the workflow `name:` field on the job.
2. PATCH `repos/Ginkobaloba/agile-cards/rulesets/17880692` adding the new context to `required_status_checks.required_status_checks`.
3. Or edit via: `https://github.com/Ginkobaloba/agile-cards/settings/rules`

---

## Alternatives Considered

### Fix the v1 context name in-place

Update the v1 required context to `engine runner battery (lint + tests)` and add the two new checks. This unblocks PR #37 without a migration.

Rejected. v1 Branch Protection is the legacy API. It has worse UI, no bypass actor model, and is not the direction GitHub is investing in. Fixing it in v1 would require doing this migration later anyway when v1 is deprecated.

### Require all five checks (including Socket)

Gate on Socket Security checks in addition to the three battery checks.

Rejected. Introduces a hard availability dependency on a third-party GitHub App. Socket being down means no merges. The battery checks are sufficient.

### Require branch to be up to date before merge (`strict: true`)

Would force PR authors to rebase or merge main before merging. More safety, more friction.

Deferred. The v1 rule had strict=false. This can be enabled separately once the team decides the workflow friction is acceptable.

---

## Ruleset Tracking

- Ruleset name: `main-branch-protection`
- Ruleset ID: `17880692`
- UI: `https://github.com/Ginkobaloba/agile-cards/rules/17880692`
- Settings page: `https://github.com/Ginkobaloba/agile-cards/settings/rules`
