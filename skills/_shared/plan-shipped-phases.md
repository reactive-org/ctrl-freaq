# Shipped-phase records — shared contract (legacy: read, then folded back)

A **shipped-phase record** holds one shipped phase's `## Changes (file-level)`, `## Data model / schema impact` and `## Test plan` entries, moved **verbatim** out of the `<!-- implementation-plan:v1 -->` plan comment by a revise between 4.20.0 and 4.35.0. It lives as one comment per shipped phase on the planned issue, next to the plan.

**Nothing writes a record any more.** Records existed because a growing plan had nowhere else to go under the per-comment cap: a story or single-issue plan gains a phase on most revises and cannot retire a shipped phase the way an epic retires a merged story (#690's seven-phase plan went 53.6k → 63.9k characters on one revise). Since 4.35.0 those three sections live in the plan's own **build part** comment ([`plan-parts.md`](plan-parts.md)) with a cap of their own, so the per-phase move — and its attribution judgment, shared-entry rule and verbatim check on every revise — is retired. What remains is reading the records that exist until a revise folds them back. This file is the single source of truth for that; `resolver` and `evaluator` read, `planner` folds back.

## Format

The marker is the **first line**, and line 2 carries the rest of the key:

```
<!-- implementation-plan-shipped:v1:phase:<N> -->
**Shipped on:** #<PR> · Phase <N> — <phase title, as in ## Phases>

## Changes (file-level)
- <entries moved verbatim from the main plan>

## Data model / schema impact
- <entries moved verbatim — omit the section when the phase moved none>

## Test plan
- <entries moved verbatim>
```

**The marker is a separate family.** `<!-- implementation-plan-shipped:` never starts with `<!-- implementation-plan:v1 -->`, so no plan lookup matches a record. `:phase:1 -->` is not a prefix of `:phase:10 -->` — the trailing ` -->` terminates the match.

**Key = (PR, phase).** A record belongs to the PR its phase shipped on. Two records with the same (PR, phase) are a genuine duplicate: `MARKER_AMBIGUOUS`.

**The pointer.** A plan that relocated entries carries, in each section that lost some, one bullet in their place:

```
- Phases 1–6 shipped on #903: entries in the shipped-phase records.
```

Readers match it leniently (any bullet saying `shipped on #<PR>` and naming shipped-phase records), and read the records on **every** PR the plan's pointers name — normally the open PR, but after a HARD Start-fresh or a hand-closed PR, that PR's records are still the only copy of their entries. With no pointer, a reader reads the open PR's records.

## Reading (resolver, evaluator)

Prep does the read — a thread scan by `scripts/plan_shipped.py`'s `read_pointed`, zero extra `gh` calls — and stages the records as one path. A pointer naming a PR other than the reader's own rides in `attention` / `notices`; so does a record whose line 2 cannot be keyed (`unkeyed`), which no reader can place:

- **resolver** (any mode): `facts.plan.shipped.body_path`, which is also `distiller_bundle.plan_shipped_path`. The distiller's `locked decisions` summary includes the record entries, labelled by phase.
- **evaluator**: `facts.plans[<issue>].shipped.body_path`. Plan adherence reads a shipped phase's `## Changes` / `## Data model / schema impact` / `## Test plan` from its record. A duplicated plan or record degrades to a notice, never a stop: adherence is one judgment among the merge gates.

Only a trusted author's record counts (`OWNER` / `MEMBER` / `COLLABORATOR`, `scripts/pipelib/trust.py`, #179): any other record-marked comment is skipped before the duplicate count, so it neither supplies a phase's entries nor makes a phase ambiguous, and it rides in `notices` as `MARKER_UNTRUSTED`.

A main-plan pointer bullet is never itself a locked entry; the records it names are.

## Folding back (planner)

The next revise of a plan that still points at records (`facts.plan.shipped.present`) puts every record's entries back into the plan, verbatim, and deletes every pointer bullet; `plan_shipped.py check --restored` proves both before the post. The procedure is [`../planner/references/shipped-phase-foldback.md`](../planner/references/shipped-phase-foldback.md). The record comments are then inert — nothing points at them — and are never edited or deleted. There is no backfill: a plan nobody revises keeps its records, and its readers keep reading them.

**The known trade.** A record told the plan reviewer exactly which entries had shipped. A folded-back entry carries no phase number, so the reviewer is handed the shipped phase numbers (`<<shipped_phases>>`) and attributes an entry by the phase's `ships` / `deliverable`; an entry it cannot attribute is not flagged as a symbol collision or a drifted anchor, which can let a genuine collision in an unshipped entry through.
