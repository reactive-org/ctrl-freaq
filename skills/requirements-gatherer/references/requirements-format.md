# Requirements format — the id scheme, the presentation shape, and the DoD bullet grammar

How requirements are identified, how the candidate set is shown at the elicitation loop, and the
exact form each approved requirement takes on the issue's `## Definition of done`. The annotation
contract this grammar must coexist with is owned by
[`../../_shared/dod-annotations.md`](../../_shared/dod-annotations.md); this file never restates
its parser.

## The stable id — `REQ-<issue>-<seq>`

Every requirement this skill lands carries an issue-minted id: `REQ-103-3` is issue #103's third
gathered requirement. It is the durable name other skills cite — a plan phase's deliverable line,
a slice's `## Grounding`, a thread discussion — so it must be unambiguous **out of context**,
which is why the issue number is embedded (the slice-title `<parent#>/S<K>` precedent). Rules:

- **Issue-minted, never doc-derived.** Identity must outlive provenance: a source doc's own
  register id never becomes part of the REQ id (doc renumbering must not break citations; two
  requirements may derive from one doc section; operator-elicited requirements have no doc id).
  The doc's id stays visible in the same bullet's provenance tail.
- **Assigned up front, from `facts.dod.next_req_seq`.** Ids are given at drafting (step 3) so the
  set discussed at G2 lands verbatim — the id the operator approved is the id on the issue.
- **Append-only.** The sequence continues from the highest existing own-issue id; ids are never
  renumbered, never reused, and a dropped candidate's id is simply skipped. A requirement removed
  from scope later is a body edit by a human — its id retires with it.
- **No `#`.** `REQ-103-3`, never `REQ-#103-3` — a `#103` inside the id would make GitHub
  auto-link every mention, spamming the issue's timeline with backlink references.

## The presentation shape (gate G2)

One block per requirement, in proposed order, existing DoD bullets listed first as current state:

```
Current DoD (already on #103):
  1. <existing bullet text, verbatim>                      [ticked/unticked as-is]

Proposed:
  REQ-103-3 · <one-line falsifiable criterion>
              provenance: docs/prd.md §R-session-timeout
  REQ-103-4 · <one-line falsifiable criterion>
              provenance: operator elicited
              detail: <acceptance notes that will ride as sub-bullets>

Contested (will NOT land — recorded in the summary):
  C1 · <the point, who disagrees / what's unanswerable>
```

`C<n>` labels are session-local — contested points never land, so they never get a REQ id.

## The bullet grammar

One bullet per requirement, appended after the existing top-level bullets:

```
- [ ] **REQ-<issue>-<seq>** — <one-line falsifiable criterion> — <provenance>
```

- The bold id prefix is the slicer's `**AC-<n>**` form; like the provenance tail it is **bullet
  text** to the annotation parser, so it changes nothing downstream.
- The criterion states the **falsifiable outcome plus its exemption classes** in one line — the
  drafter's DoD altitude: freeze judgment, never a re-derivable hit list.
- `<provenance>` is exactly one of:
  - a **durable doc anchor** — path + the section's slug ID, else its §heading (a doc without
    IDs, `docs/architecture.md §Data retention`), or a register id (`docs/prd.md §R-session-timeout`,
    `docs/requirements.md §PRD-OQ-05`); never an authored `path:line`, which rots the moment the
    doc moves;
  - `operator elicited <YYYY-MM-DD>` — no document records this requirement; the issue is now its
    source of record.
- The provenance tail is joined by an em-dash (` — `) and is **part of the bullet text**. It is
  **never wrapped in parentheses**: a trailing parenthetical is the position the annotation
  grammar owns, and the parser treats an attribution-lookalike there as `DOD_MALFORMED`. The
  resolver's later annotation appends after the tail untouched:
- **Sanitize `(` / `)` inside a cited anchor.** The tail ends the line, so a cited heading whose
  own text ends in a parenthetical — `§Rollout (phase 3)`, `§Register (see ab12cd3)` — puts an
  attribution-lookalike in exactly the position the parser polices, and one such landed bullet
  blocks every downstream DoD parse until hand-repair. Rewrite parentheses in cited anchor text
  to `[` / `]` (the evaluator-reason precedent): `— docs/plan.md §Rollout [phase 3]`. The flow's
  pre-write `parse.py dod` validation is the backstop; this rule is what keeps it green.

  ```
  - [ ] **REQ-42-1** — Sessions expire after 30 minutes of inactivity, kiosk accounts exempt — docs/prd.md §R-session-timeout (closed by phase 2, commit ab12cd3)
  ```

## Detail placement — cite, never restate

- **Doc-grounded requirement** → the bullet is one line, full stop. Given/When/Then detail,
  rationale, and edge cases live in the cited doc; copying them onto the issue creates the drift
  the citation exists to prevent. A planner or resolver needing the detail reads the cited
  section.
- **Operator-elicited requirement** → acceptance detail may ride as **indented sub-bullets**
  under the criterion (the annotation contract's own rule: sub-bullets are detail, not DoD
  items — they don't shift indexes and nothing ticks them):

  ```
  - [ ] **REQ-42-2** — A user holds at most 3 concurrent sessions; a 4th evicts the oldest — operator elicited 2026-08-07
    - Eviction is silent on the evicted device until its next request, which lands on sign-in
    - The active-sessions list shows device and last-seen for each live session
  ```

- A requirement the operator grounds in a doc **section that doesn't exist yet** is
  operator-elicited — cite what is, not what should be; the gap is a Contested-list entry worth
  folding back into the doc (the summary's drafter breadcrumb).

## Worked examples

Domain examples only — the grammar is stack-agnostic; nothing here assumes a language, framework,
or platform.

```markdown
## Definition of done

- [ ] **REQ-57-1** — An expired card at renewal produces a dunning notice and a 7-day grace period, comped accounts exempt — docs/prd.md §R-billing-lifecycle
- [ ] **REQ-57-2** — Data-export requests complete within 30 days and cover every store the account touched — docs/architecture.md §Data retention
- [ ] **REQ-57-3** — Bulk import rejects a malformed row with a per-row reason and imports the rest — operator elicited 2026-08-07
  - Partial success reports imported/rejected counts; a rerun of the same file imports nothing twice
```

The first two cite the documents that record them — one line each. The third exists only because
the operator said so in this session; its detail rides with it because there is nowhere else for
it to live. Downstream, any skill can now say "phase 2 delivers `REQ-57-1`, `REQ-57-3`" and the
reference survives every body edit that doesn't delete the bullet itself.
