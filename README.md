# CodeFoundry

**Role:** clone, create, brand and verify every destination repository.

This repository is the durable specification and memory for the CodeFoundry role in the
WatchCodes factory. Read this file before doing anything here.

> CodeFoundry occupies the repository previously named `WatchCodes/nas` (empty, one
> commit). It was reused rather than redeclared.

---

## Responsibility

CodeFoundry executes a migration. It is the only component allowed to make a repository
appear in the destination account, and the only one that writes a per-repository report.

1. Consume a `MigrationRequest` from RepoAtlas.
2. Clone the upstream at the **pinned SHA**. Never fork.
3. Create the destination **private first**.
4. Apply the branding rules and the ownership rules below.
5. Verify the destination: name correct, owner correct, zero-brand gate passed, licence
   preserved, attribution present.
6. Emit exactly one `MigrationReport`.
7. Flip the repository public **only after approval** — creation never publishes.

## What it never does

- Never publishes `nastech-agent`, never merges, never tags, never releases.
- Never decides ownership, naming or queue order — that is RepoAtlas.
- Never creates a repository with no detectable licence.
- Never copies copyleft code automatically; copyleft → `REQUIRES_REVIEW`.
- Never auto-merges. The final PR is always human-approved.

## Branding rules

- Zero `hermes` / `nous` in the shipped tree, except `LICENSE`, `NOTICE`, `CREDITS.md`
  and generated lockfiles.
- `LICENSE` stays **verbatim**. `NOTICE` and `CREDITS.md` carry **one attribution line**
  per upstream.
- Lockfiles are residual: preserved bytes, not a branding failure.
- A branded name that would point at a repository nobody owns is a 404. Order is always:
  destination verified → mapping recorded → URL rewritten → gate. Never rewrite a URL
  before its destination exists.

## Contracts

| Direction | Contract | Minimum fields |
|---|---|---|
| consumes | `MigrationRequest` | `batch_id`, `source`, `destination_owner`, `target_name`, `sha`, `license`, `action`, `subtree_path`, `provenance` |
| produces | `MigrationReport` | `batch_id`, `repo`, `status`, `checks{created,named,owned,gated,license_kept,credits}`, `pr_url`, `error` |

One report per repository. One writer per file.

## Status it owns

`CREATING → CREATED → VERIFYING → VERIFIED`

- `VERIFIED` means the repository exists with the right name and owner.
- `VERIFIED` is **not** `READY`. Ready needs the brand gate too, and only NastechBridge
  may say so.
- A record that stays non-terminal for 24h becomes `REQUIRES_REVIEW` and notifies. A
  stuck repository must never block the gate silently.

## Related repositories

- `WatchCodes/RepoAtlas` — produces the queue this consumes
- `WatchCodes/SourceSentinel` — takes over monitoring after verification
- `WatchCodes/CodeLineage` — records the lineage this proves
- `WatchCodes/NastechBridge` — collects these reports and decides readiness
- `NastechResearch/100Ways` — discovers and brands `nastech-agent` only; never creates repos
