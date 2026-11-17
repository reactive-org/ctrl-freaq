# Question-issue contract — shared

The stable schema for a `question`-type issue — a request for a decision or answer from a human, not a
unit of work to build. Both the **drafter** (filing a question directly or a companion to a build
issue) and the **`question-sweep`** (filing a companion for an untracked OQ) file identical
question issues, so the body template, the audience-label rule, and the `## Tracked in` bridge live
here. This file is the single source of truth for the *schema*; each skill keeps its own
**orchestration** (review loop, approval gate, paste-ready snippet, handoff).

## Body template

```markdown
## Question
<the decision or answer needed, phrased for the target audience(s) — a business stakeholder, an architect, and a developer should each be able to read it in their own terms>

## Audience
<who needs to answer. When more than one audience is named, add a one-line "what we need from you" per audience so each knows their part.>

## Constraints
<hard limits the answer must respect that are outside the audience's control — regulation, legal/compliance, insurance, contractual/SLA obligations, or third-party-platform limits. State each as *what is fixed* + *the external force that fixes it*, so the reader sees why it's non-negotiable without inferring it from the references; cite the doc. Example: "Consult must be real-time (phone or video both qualify) — AHPRA/Medical Board telehealth guidance forbids prescribing off a questionnaire-only path (`PRD-OQ-20`/`PRD-TH-01`)." This separates what's already fixed from the part that's genuinely the audience's call. Omit the section when the decision is unconstrained; don't pad it with product preferences or scope choices — those are the decision itself, not an external constraint on it.>

## Context
<enough background that the reader can answer without the conversation that produced the question — the same cold-read bar the rest of the skill applies>

## References
<the docs (with §/heading), code paths, epics, or issues that ground the question. Omit a row that doesn't apply; omit the section if the question stands alone.>

## Why this matters
<what this question gates — the work, decision, or doc that's blocked until it's answered>

## Tracked in
<external tracker id + location when the question comes from a tracked-questions list, e.g. `docs/prd.md → Open Questions → PRD-OQ-06b`. When this question was spun off as a companion to a build issue, ALSO name the build issue # it gates — both pointers, so the loop closes from either end. Omit if neither applies.>
```

A question is filed for a human to answer, not for the pipeline to build, so it carries **no**
Definition of done or Acceptance criteria — `## Context` + `## References` exist to make the question
*answerable*, which is the only bar that matters. Don't invent context, options, or a recommended
answer the user didn't give; a thin-but-honest question beats a confidently-framed wrong one.
In `## References` (and `## Constraints`), cite a doc section by its slug ID (`docs/prd.md
§R-dry-run-default`) or, in a doc without IDs, by its heading; the schema's `§/heading` means that.

## Title

`<tracker-id> — <question topic>` when a tracker id exists — e.g. `PRD-OQ-06b — Which billing model
for v1?`; otherwise just the question topic phrased as a question. The id in the title makes the issue
findable by the tracker reference both ways.

## Audience labels

A question routes to the people who can answer it, so its target audience(s) become labels — one per
audience, namespaced: `audience:business`, `audience:architect`, `audience:developer`, `audience:ux`,
and so on. The namespace keeps them filterable (`label:audience:*`) and stops them colliding with an
existing repo label that shares the name. Apply one per audience named — and you may add a label for
an audience the question *clearly implicates* even if unnamed (a "is this worth building?" call also
wants a developer's effort read; a data-retention question also wants legal). Surface any audience you
inferred so the user can drop it. **Audience labels don't count against the three-label cap** — they're
functional routing, not noise, so a question can carry `question` + `audience:business` +
`audience:architect` without trimming.

**Priority on a question is not default.** Unlike buildable work, a question waiting on a human answer
doesn't carry default-medium triage, and minting a `priority:*` label per question is noise. Omit
priority unless the user signals the question is **blocking or urgent**; when they do, apply it per the
filing skill's priority scale.

Audience labels rarely pre-exist, and `gh issue create --label audience:business` fails if the label is
absent — so the filing skill **offers to create a missing audience label as part of filing** (unlike
type/priority labels, which it only ever suggests). Flag any audience label that doesn't exist yet at
the approval gate (`audience:business (will be created)`); approving "File it" approves the create.
Create each missing one in the main loop right before filing (the same place the skill runs `gh label
list` inline — label management has no bundled script, so it is a sanctioned raw-`gh` call):

```bash
gh label create "audience:business" --description "Question for business stakeholders" --color BFD4F2 2>/dev/null || true
```

The label is the whole point of an audience question — one filed without it can't be found by the
people meant to answer it — which is why creation is offered, not merely suggested. If the user
declines the create at the gate, file without the audience label and tell them the question won't
surface in an audience filter until they add it.

## The `## Tracked in` bridge

`## Tracked in` is the doc↔tracker bridge: it points at **both** the source-doc location
(`docs/prd.md → Open questions → PRD-OQ-05`) **and**, for a companion, the build issue `#` it gates —
so the loop is closeable from either end. See the doc→tracker bridge and closing protocol in
[`open-question-links.md`](open-question-links.md).
