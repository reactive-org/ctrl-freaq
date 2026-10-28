# Open-question links — shared contract

The **`## Open questions`** section is a body section a **build** issue (bug / feature / incomplete /
epic / story) carries when it was drafted from a source that still has **unresolved open questions**
(OQs). OQs aren't centralized — they surface inline in *any* project doc (often as
`PROVISIONAL — <oq-id>` / `TBD` markers, e.g. a Rails/healthcare repo's `PRD-OQ-##` in `docs/prd.md` or
a Swift repo's `OQ-##` / `DESIGN-Q-#` in a design doc); the **registry of record is the set of GitHub
`question`-type issues** (the tracker), with docs as the *sources* that seed it. Detecting and matching
an OQ is [`open-question-detection.md`](open-question-detection.md); the `question`-issue body schema is
[`question-issue.md`](question-issue.md). This section records, per OQ, *what scope the OQ gates* and
*how the build issue disposes of it*, and links to the `question`-type issue that routes the decision to
a human. It turns a doc-internal OQ into a **tracker-visible dependency**.

Two linkage layers, doing different jobs:
- **This section** = the human-readable metadata registry (OQ id, source, gated scope, disposition,
  audience, companion question) that GitHub's native relationships can't carry.
- **A native `blocked by` dependency** = the structured relationship, set only for the `in-scope
  (blocked)` disposition (§Dispositions), so GitHub itself, the resolver, and the evaluator can gate on
  it. Capability-gated: when a repo/gh lacks native dependencies, everything degrades to prose (a
  `Related to #N` line in `## Related issues` plus this section) — the relationship is never lost, only
  its native form.

This file is the single source of truth for the section's format, its dispositions, and the ownership
split. Every consumer cites it rather than restating the schema.

## Ownership

| Skill | Role |
|---|---|
| `question-sweep` | **Registry owner.** Detects OQs across all project docs, reconciles them against the tracker — files missing companion questions, flags stale docs, and maintains the doc↔issue back-links. Explicit-invocation only (a hygiene sweep, not a pipeline stage). |
| `question-resolver` | **Assisted closer.** Evaluates a `question` issue against the docs, records the operator's decision as the `<!-- question-decision:v1 -->` comment, optionally closes the issue, and **proposes** (never applies) the doc fold-back. Never decides — the operator does. Explicit-invocation only. |
| `drafter` | **Thin writer.** On a build issue whose scope an OQ gates: writes this section's entry, sets the native `blocked by` for `in-scope (blocked)`, and links the companion question — reusing an existing one, or filing when untracked (safety net). Reconciles in revise mode via the tiered read (§Status). Does **not** own cross-doc detection, registry reconciliation, or doc back-linking — those are the sweep's. |
| `planner` | **Reader + extender.** Reads this section and the issue's native `blocked_by` to learn which scope is gated, copies the live entries into the *plan's* own `## Open questions` section, and **never silently resolves an OQ** (a tracked OQ is not a hedge — see the planner's open-question carve-out). Does not write this body section. |
| `resolver` | **Reads `blocked_by`; hard-gates.** If the issue is natively `blocked by` an **open** question, routes to its `blocked` classification (surface "can't build until #N answers") instead of building the gated criterion. Treats this section as a tracked-dependency registry — **not buildable scope, not a DoD source**. |
| `evaluator` | **Reads `blocked_by`; hard-gates.** Holds / soft-rejects the merge while an in-scope blocker (open question) is unresolved, citing it. Does **not** read this section's bullets as acceptance criteria or as DoD gaps. |

The section stays out of the verified `<!-- implementation-plan:v1 -->` plan on purpose: the plan is
immutable, while this dependency record changes as OQs open and resolve.

## Format

The `## Open questions` heading is the human anchor; the `<!-- open-question-links:v1 -->` marker is the
**first line inside the section** (readers locate the registry with a `startswith` match on that line).
Omit the whole section when no OQ gates the issue. One bullet per gated OQ:

```
## Open questions
<!-- open-question-links:v1 -->
- OQ: `<oq-id>` (<source-doc> <§/register-location>) — gates: <one line of the scope this OQ blocks>
  — disposition: scoped-out | in-scope (blocked) | provisional-default
  — question: #<N> | (not filed) — audience: <comma-separated audience:* labels, or (unknown)>
  — [provisional-default only] default: <the provisional choice built on> — retires-when: <#N answered>
```

`disposition` is a closed set of exactly three values. `question: #<N>` is the companion `question`-type
issue that routes the decision — either one the drafter filed or an existing question it reused after a
de-dup search (the drafter always searches for an existing companion before proposing to file a new one);
`(not filed)` when the user declined at the drafter's gate and none existed.

**Stack-neutral examples** (never present one repo's id syntax as canonical):
```
## Open questions
<!-- open-question-links:v1 -->
- OQ: `PRD-OQ-05` (docs/prd.md §12 Open questions) — gates: which payment methods ship at launch
  — disposition: scoped-out — question: #211 — audience: audience:business, audience:developer
- OQ: `OQ-08` (docs/ui-design.md §5 register) — gates: consult-modality copy on the next-consult tile
  — disposition: in-scope (blocked) — question: #212 — audience: audience:clinical
- OQ: `DESIGN-Q-3` (docs/design/notes.md "Open decisions") — gates: default sort order of the results list
  — disposition: provisional-default — question: #213 — audience: audience:ux
  — default: newest-first — retires-when: #213 answered
```

## Dispositions

- **`scoped-out`** (the drafter's default) — the gated part is removed from *this* build issue's scope.
  It **MUST** have a matching line in the issue's `## Out of scope` naming the same OQ. Because the part
  is out-of-scope, it never enters the `## Definition of done`, so the resolver/evaluator hard-block on
  in-scope documented-constraint deferral is **not** triggered (this is out-of-scope, not an in-scope
  deferral). No native dependency (the issue ships its decided scope and isn't blocked); a `Related to
  #<question>` line in `## Related issues` cross-links the companion. When the question is answered, the
  scoped-out work is re-filed as a fresh build issue (see Closing protocol).
- **`in-scope (blocked)`** — the part stays in the issue's DoD but cannot be completed until the question
  answers. The build issue **MUST** be set natively `blocked by` the companion question (the structured
  gate the resolver/evaluator read) — so this disposition **requires a companion question to exist**. If
  none is filed (`question: (not filed)`) there is nothing to block on: the drafter must not emit a
  dangling `blocked_by`, so it either falls back to `scoped-out` or records a **prose-only** blocker (no
  native dep) and warns that the resolver/evaluator can't hard-gate it until a question exists. The
  drafter warns the user this holds the resolver/evaluator until the question is answered. "(blocked)"
  here is a human qualifier — unrelated to the resolver's `blocked` issue *classification*, though a
  natively-blocked issue does route through that path.
- **`provisional-default`** — the part is built now on a stated provisional choice; the issue is **not**
  blocked. The entry **MUST** carry `default:` (the choice built on) and `retires-when:` (`#<N>
  answered`), and the planner records it as a named watchpoint. It stays in-scope: if the provisional
  choice later contradicts a grounding doc, the evaluator's in-scope hard-block correctly fires — the
  disposition is opt-in and the risk was accepted.

## Status is the tracker's

An OQ's resolution is read from the **tracker**, not a doc register field — a doc marker can lag a
decision made in the question's thread. The read is **tiered** so the deterministic half stays in the
prep script (`gh_gather.py` returns the issue `state`, the full verbatim `thread`, and — when passed
`--marker-prefix '<!-- question-decision:v1 -->'` — whether a recorded-decision comment exists):

- **Tier 1 — deterministic (zero-cost).** The question is **resolved** if either the issue is `closed`
  **or** it carries a `<!-- question-decision:v1 -->` comment (the durable decision `question-resolver`
  records — that comment *is* the answer), written by a trusted author: `gh_gather.py` counts a marked
  comment only when its `authorAssociation` is `OWNER` / `MEMBER` / `COLLABORATOR`
  (`scripts/pipelib/trust.py`, #179); the question-sweep and question-resolver preps report a skipped one as
  a `MARKER_UNTRUSTED` notice, while `oq_tracker`'s gate read skips it silently.
  `state` is always in the gather envelope; the marker
  half needs the fetch to pass `--marker-prefix '<!-- question-decision:v1 -->'` so `marker_comment_present`
  populates — a consumer that doesn't (checking a single companion by `state`) still gets the marker via
  Tier 2's reader, which checks for it first, by the same author rule. No sub-agent. This is the common, leanest path.
- **Tier 2 — thread reading (judgment, only when `open` AND no decision marker).** Dispatch the
  question-status reader `Explore` sub-agent (`${CLAUDE_PLUGIN_ROOT}/skills/question-sweep/references/question-status-reader-prompt.md`),
  sibling to the resolver's state-distiller. It reads the question's body + thread and returns a typed
  reading — `resolved-in-thread` / `still-open`, with evidence (quote + author + date) and, when
  resolved, a one-line answer summary; on a thread it can't read confidently it raises `AMBIGUOUS` per
  [architecture.md §3](../../docs/architecture.md)'s closed decision-code set. This catches the
  **answered-in-thread-but-still-open** staleness case (a stakeholder answered; nobody recorded a
  decision, closed the issue, or folded it to docs).

Consumers: `question-resolver` (its own reentrancy) and the sweep (stale-doc reconciliation) realize
the full Tier 1 by fetching with the decision `marker_prefix`; the planner (Dimension 10 / revise) and
drafter (revise) check a single companion by `state` and fall through to Tier 2, whose reader catches
the marker anyway. Escalate to Tier 2 only when a question is still `open` and no decision marker was
found.

## The doc→tracker bridge

The companion `question` issue's own `## Tracked in` section points at **both** the source-doc register
location (`docs/prd.md → Open questions → PRD-OQ-05`) **and** the build issue `#` that depends on it, so
the loop is closeable from either end.

## Doc fold-back

When a question resolves, the docs that raised it are updated to the **decided state** — framed as the
state *now*, not a changelog (version control carries the history). Three moves, each where it applies:

1. Rewrite the affected prose to the decided state.
2. Remove the now-obsolete `PROVISIONAL` / open-question marker.
3. If the repo keeps an open-question register, flip its status (Open → Decided) and add the
   `tracked in #<N>` back-link.

`question-resolver` **proposes** these at decision time (never applies them); the `question-sweep`
proposes them for a `stale-doc` question resolved elsewhere. Both cite this section rather than
restating the moves.

## Closing protocol

No skill auto-reopens work when a question is answered (consistent with session-per-skill and the
no-auto-cross-boundary rule). The decision is the human's. The **assisted-closing path** is
`/ctrl-freaq:question-resolver <issue>`: it evaluates the question against the docs, records the
operator's decision as the `<!-- question-decision:v1 -->` comment (the machine-readable resolution
Tier 1 reads), offers to close the issue, and **proposes** (never applies) the doc fold-back. It does
not decide — the operator does; it does not run the downstream flows either.

Then, per the build issue's disposition: a `scoped-out` follow-up is a fresh `drafter` run
(breadcrumbed by the question's `## Tracked in` build back-link); an `in-scope (blocked)` dependency is
cleared by removing the native `blocked by` (`gh_persist.py link … --remove-blocked-by`) and folding the
now-decided scope in via the drafter/planner revise flow; a `provisional-default` is confirmed or
corrected the same way, retiring the watchpoint.
