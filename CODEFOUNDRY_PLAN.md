# CodeFoundry — Implementation Plan

> Revision 1 (2026-11-11). This is a build order, not a capability inventory.
> The repository currently holds only this document and a README; every claim
> below is planned, not implemented.

## 0. Mission and authority

Turn an approved `MigrationRequest` into a verified destination repository and
exactly one `MigrationReport`. CodeFoundry is the only component in WatchCodes
allowed to create destination repositories.

It never decides **what** to migrate or **what to call it** (RepoAtlas), never
monitors upstream drift (SourceSentinel), never records lineage (CodeLineage),
and never declares readiness (NastechBridge).

## 1. Non-negotiables

1. **One branding engine.** CodeFoundry invokes the pinned engine from
   `nastechresearch/100Ways` (`hundredways/rules.py`, `brandscan.py`) as a
   versioned dependency. It never reimplements token rules, path mapping or
   brand scanning. Divergence is a build failure.
2. **Clone, never fork.** Fresh snapshot at a pinned SHA. No preserved
   branches, tags or history — preserved tags fabricate lineage the update
   model cannot use.
3. **Private first.** Flip public only after human approval.
4. **Licence gate.** No detectable licence → reject. Copyleft → `REQUIRES_REVIEW`.
   `LICENSE` stays verbatim; one attribution line per upstream in `CREDITS.md`.
5. **No delete. No force-push** over a reviewed branch. Tokens carry no
   `delete_repo`; design assumes that forever.
6. **Git push, not the contents API**, for tree transfer (rate-limit budget).
7. **Idempotency receipt.** `batch_id + source` is the key. The created
   destination repo ID is recorded *before* branding. A retry updates, never
   duplicates.
8. **Resumable.** Checkpoints are committed files, not in-memory state. A run
   that dies at step N resumes at step N.
9. **`--dry-run`** performs the full scan and plan and performs zero GitHub
   writes.
10. **Fail-closed everywhere.** Ambiguity → `REQUIRES_REVIEW`, never a guess.

## 2. Rejected from earlier proposals (and why)

| Rejected | Reason |
|---|---|
| Own branding / gate / licence modules | Duplicate of the pinned engine; drift recreates a three-day outage class |
| Migration history database | Three sources of truth; committed report files are the store |
| Branch/tag/history preservation | Breaks the re-clone update model; See 1.2 |
| Homegrown secret detection | GitHub push protection + gitleaks exist |
| Autonomous unattended creation | Mass-creates wrong repos irreversibly; autonomy arrives last, behind gates |
| CodeFoundry opening the final PR | Publication is human-approved; authorship of that PR stays unresolved until decided |

## 3. Architecture (v1: five modules)

| Module | Responsibility |
|---|---|
| `intake` | Read, validate, deduplicate a `MigrationRequest`; fail-closed on any missing/invalid field |
| `scan` | Upstream metadata, structure, licence detection (GitHub `license` field first, evidence fallback), framework/dependency inventory |
| `clone` | Full clone at pinned SHA; extract; LFS-aware; integrity report |
| `ship` | Create private destination, `git push`, record the receipt, record name/owner evidence |
| `brand` | Apply the pinned engine; run the zero-brand gate; assemble the report |

`report` is a function of `brand`, not a module: one `MigrationReport.json`
per repository, written to the location NastechBridge expects.

Later phases add: `recovery` (J), `security` (M, wrapping gitleaks/push
protection), `link` (N — Bridge/Lineage/Sentinel handshakes), `autopilot` (O).

## 4. Contracts

JSON Schema, draft 2020-12, live in [`schemas/`](schemas/). `schema_version`
is required on every document. The schema is the contract; prose is not.

| Schema | Consumed by | Produced by |
|---|---|---|
| `discovery-manifest.schema.json` | RepoAtlas | 100ways |
| `migration-request.schema.json` | CodeFoundry | RepoAtlas |
| `repository-mapping.schema.json` | SourceSentinel, 100ways | CodeLineage |
| `migration-report.schema.json` | NastechBridge | CodeFoundry |
| `upstream-report.schema.json` | NastechBridge | SourceSentinel |
| `batch-readiness-report.schema.json` | 100ways | NastechBridge |

CodeFoundry's own obligations: validate `migration-request`, emit
`migration-report`. Extensions allowed only as optional properties, never by
removing a mandatory field.

## 5. Pipeline

```
MigrationRequest
  → validate (intake)
  → scan upstream (licence, structure, provenance)      [dry-run ends here]
  → clone at SHA (integrity report)
  → create private destination + idempotency receipt
  → git push branded tree
  → brand + zero-brand gate (pinned engine)
  → verify name / owner / licence / attribution
  → MigrationReport.json
  → [human approval] → flip public → PR → hand over to SourceSentinel
```

## 6. Status ladder (authoritative, unchanged)

`CREATING → CREATED → VERIFYING → VERIFIED`

`FAILED`, `REQUIRES_REVIEW`. `VERIFIED` = right name and owner; it is **not**
`READY`. A record non-terminal for 24h becomes `REQUIRES_REVIEW`.

## 7. Build order

| Phase | Deliverable | Done when |
|---|---|---|
| 1 | Six schemas + `intake` | An invalid request cannot proceed |
| 2 | `scan` | Licence + structure evidence for a real repo |
| 3 | `clone` | Byte-extract at SHA, integrity report |
| 4 | `ship` | Private repo exists, receipt committed, push verified |
| 5 | `brand` | Pinned engine runs; gate passes on a real tree |
| 6 | Report | One `MigrationReport.json` accepted by NastechBridge |
| 7 | Recovery + security | Retry/timeout/secret paths covered |
| 8 | Link + autonomy | Only after gates below hold |

## 8. Completion gates

1. Cannot process an invalid request.
2. Cannot create a destination with an undetected licence.
3. Cannot publish during creation.
4. Cannot pass verification with prohibited brand remnants.
5. Cannot lose required upstream attribution.
6. Cannot silently ignore a failed migration.
7. Cannot declare readiness itself.
8. Cannot produce two reports for one `batch_id + source`.
9. Cannot bypass human approval for publication.
10. Dry-run performs zero writes.

## 9. Test laboratory

Twenty scenarios, kept from the earlier proposal: permissive / no-licence /
copyleft; `hermes` in filenames; `nous` in docs; lockfiles; submodules; LFS;
binaries; broken URLs; missing attribution; duplicate destination; invalid
owner; invalid SHA; rate limit; interrupted clone; interrupted push; failed
branding gate; created-but-unverified; stuck 24h; dry-run equivalence.

Each is a committed fixture + expected `MigrationReport`.

## 10. Known risks

- **Reputation.** Republishing third-party MIT repos under WatchCodes is legal
  with attribution but can read as clone-farming. Mitigation: a visible
  `Derived from <upstream> at <sha>, MIT` banner in every README; attribution
  louder than branding; never bulk-seeding.
- **Token model.** `nastechresearch` is a user account, `WatchCodes` an org;
  creation rights differ per destination owner. Per-owner credential mapping is
  a prerequisite of `ship`.
- **Submodule policy** (recursive vs drop-with-note) is undecided and must be
  before phase 3.
