# Guide: writing `docs/prd.md`

One of a set of authoring guides for the documents the **ctrl-freaq** skills read
(`prd.md`, `architecture.md`, `architecture-notes.md`, `ui-design.md`, `constitution.md`).
This one covers the PRD (product requirements doc).

The principles here are **stack-neutral** — a PRD is product truth, not technology, so it's the
most stack-agnostic doc in the set. Where a worked example needs a stack, it uses **Ruby on
Rails 8.1**, mostly to show the *what (PRD) vs how (architecture.md)* boundary.

## Why this guide exists: the PRD is the pipeline's upstream anchor

Every other doc in the set is consulted *while building*. The PRD is different on two axes, and
both shape how you write it.

**1. It's where the pipeline starts.** The `drafter` reads the PRD *before drafting
any issue* and grounds the issue's framing, language, and personas in it — so the PRD's wording
propagates into every issue, every epic, every story. Epic bodies trace back to it by design
(`## Goal` / `## Background` / `## Stories` / `## PRD impact` exist for traceability from
`docs/prd.md`). Get the PRD's terminology and scope right and the whole downstream narrative
stays coherent; get it vague and the drift starts at issue #1.

**2. It's re-checked at every stage, as the product source of truth.** It's cited in the
planner's `## Doc grounding`, in the resolver's grounding statement and PR body, and the
`evaluator` **soft-rejects a PR whose diff contradicts a PRD requirement**. So the PRD
isn't write-once context — it's the spec the finished work is measured against.

> **The PRD answers *what* the product does and *why* — never *how* to build it.** "Users export
> their journal to CSV" is a PRD requirement; "export runs in a Solid Queue job" is an
> `architecture.md` decision. Keep the how out and the PRD stays durable across implementation
> changes.

**Authoritative, but mutable.** Unlike the constitution (a hard blocker) or even `architecture.md`
(a deviable default), the PRD is treated as authoritative *and possibly out of date*. When
incoming feedback conflicts with it, the skills don't silently override it or hard-block — they
**surface the tension** and route to a human gate (update the PRD, file the feature anyway, or
flag for discussion). Write the PRD knowing it will be revised through that gate, not worked
around.

## Know your reader: how the pipeline consumes the doc

- **`drafter` (heaviest reader).** Uses the PRD three ways: (1) ground language —
  reuse its persona and feature names rather than inventing new ones; (2) detect tension between
  feedback and the PRD in three patterns — **contradicts / extends / gap**; (3) add a
  `## PRD impact` note (and fire a `PRD conflict` gate) when tension exists.
- **`planner`.** Cites PRD sections in `## Doc grounding`; an accepted product
  trade-off can be recorded as a watchpoint with a PRD citation (e.g. *"acceptable per PRD §10.3"*).
- **`resolver`.** Cites the PRD in its grounding statement and PR `## Doc grounding`;
  reframes "feature X is incomplete" as "feature X doesn't match **PRD §Y**"; treats a documented
  **PRD scope exclusion** as a citable reason to defer out-of-scope work to a follow-up; stops on
  a `Doc conflict` gate if the issue requests something the PRD rules out.
- **`evaluator`.** Verifies the cited PRD sections actually say what the work claims and
  soft-rejects on a clear contradiction of a PRD requirement.
- **Cited by stable section anchor (`PRD §N`), read per-issue.** The PRD is *not* `@`-included by
  `CLAUDE.md` (the constitution is), so it isn't carried in context on every run — it can be
  fuller than the constitution, but every requirement must stay individually citable.

## Authoring principles

Each principle is stated neutrally, then illustrated.

1. **Write for citation.** Stable, numbered anchors; one requirement (or product decision) per
   section. The drafter flags "contradicts **PRD §4**"; the planner cites "**PRD §3**" — both need
   a section that resolves to a single claim.

2. **State scope on *both* sides — in and out.** Explicit exclusions and non-goals are
   first-class content, not omissions. They're what the resolver cites to defer out-of-scope work,
   and what lets the drafter recognize feedback that *extends* the PRD into new territory. An
   unstated exclusion can't be cited and can't be detected.
   *Example:* "**Out of scope:** offline editing; multi-user shared journals." Now a request for
   either is correctly framed as a PRD extension, not silently built.

3. **What and why, not how.** Keep implementation, layers, and tech choices out — they live in
   `architecture.md`. Baking "how" into the PRD makes it go stale on every refactor and
   manufactures false contradictions.
   *Rails:* PRD — "entries are searchable by date and tag." `architecture.md` — "search via an
   Active Record scope with a composite index", not the PRD's concern.

4. **Be definite and falsifiable about product behavior.** The drafter's contradicts/extends/gap
   detection and the evaluator's soft-reject both depend on requirements a reader can *disagree
   with*. Vague requirements detect no tension, so contradictions ship.
   *Example:* "Entries are immutable after submit" — falsifiable. "Editing should be handled
   appropriately" — not.

5. **Fix terminology and personas once.** Name the users and features explicitly and use those
   names everywhere; the drafter mirrors them into issues, which keeps the backlog searchable and
   consistent. Don't let "creator" / "author" / "user" drift across sections.

6. **Make requirements traceable and acceptance-shaped.** Phrase requirements so an issue's
   Definition of Done and the planner's goal-coherence check can map back to a PRD section. A
   requirement nobody can write an acceptance criterion against is too vague.

7. **Author it to be updated.** Treat the PRD as authoritative-but-mutable: keep transient
   detail (current counts, dated rollout specifics) out of requirement statements so a product
   change is a small, localized PRD edit through the conflict gate — not a rewrite.

## What belongs here vs. the sibling docs

| Content | Put it in | Why |
|---|---|---|
| Product behavior, scope, requirements, personas, **non-goals** | `prd.md` | The product source of truth; cited and re-checked pipeline-wide |
| How to build it — layers, APIs, stack, file structure | `architecture.md` | The PRD says *what*; this says *how* |
| Inviolable engineering rules | `constitution.md` | Hard blocker; the PRD is authoritative-but-mutable |
| UI components, sizes, visual patterns | `ui-design.md` | Planner cites `ui-design §X` for UI decisions |
| *Why* an engineering decision was made | `architecture-notes.md` | Rationale, not requirements |

The recurring boundary: the PRD names a capability ("export entries to CSV"); `architecture.md`
decides the mechanism ("a Solid Queue job that streams the CSV").

## Recommended outline

The **purpose** of each section is universal; numbering is yours and must be stable.

- **§1 Product overview & goals.** What the product is, who it's for, and the outcomes it exists
  to produce. One or two paragraphs.
- **§2 Personas / users.** The named user types the rest of the doc (and every issue) refers to.
- **§3 Scope.** In scope *and* **out of scope / non-goals** — the explicit exclusions downstream
  skills cite.
- **§4…N Feature requirements.** One capability per numbered section: what the user can do, the
  expected behavior, and the boundary conditions. Definite and acceptance-shaped (principle 4/6).
- **Constraints & assumptions.** Product-level constraints (regulatory, platform, data-retention)
  — not technical design.
- **Success metrics.** How you'll know the product is working, where applicable.

A PRD is inherently stack-neutral; resist the urge to specify implementation in any section. When
you feel the pull to write "how", that content belongs in `architecture.md`.

## A worked example, end to end

The PRD's signature is that one requirement is exercised at every stage. Take:

> **PRD §4 — Entries.** An entry is immutable after submit. Out of scope: editing or deleting a
> submitted entry.

How the pipeline uses it:

- **Drafter** — feedback arrives: "let users fix typos in submitted entries." The drafter detects
  the contradiction, doesn't silently file it, and adds:
  > `## PRD impact`
  > This contradicts the PRD (§4 — entries are immutable after submit). The PRD may need to be
  > updated to allow post-submit edits.

  …then fires the `PRD conflict` gate so a human chooses *update PRD / file feature / flag*.
- **Planner** — a plan for an adjacent feature cites it: `## Doc grounding — PRD §4 (entry
  immutability)`, and designs around it rather than against it.
- **Evaluator** — a PR whose diff adds an `update` action on submitted entries is **soft-rejected**,
  quoting PRD §4 and the diverging hunk.

One definite, citable requirement governs framing upstream and acceptance downstream. That is what
a PRD section is for.

## Authoring checklist

- [ ] Every requirement has a stable number and states a single, falsifiable product behavior.
- [ ] Scope lists both **in** and **out** (explicit exclusions / non-goals).
- [ ] No implementation/"how" — that's in `architecture.md`.
- [ ] Personas and feature names are fixed and used consistently throughout.
- [ ] Each requirement is acceptance-shaped (a DoD can be written against it).
- [ ] No transient detail baked into requirement statements.

**Validate against the real consumer:** run `drafter` on a piece of feedback that
brushes the PRD. Success looks like the drafter grounding the issue's framing in a cited **PRD §N**
and — when the feedback strains the spec — surfacing it as a `## PRD impact` note or `PRD conflict`
gate, rather than silently absorbing or contradicting it. If the drafter invents new persona names
or misses an obvious contradiction, the PRD wasn't definite or consistent enough.

## Anti-patterns

- **Implementation detail in the PRD** → goes stale on every refactor and manufactures false
  contradictions; move "how" to `architecture.md`.
- **Missing explicit exclusions** → the resolver can't cite a deferral and the drafter can't
  recognize feedback that *extends* scope.
- **Vague / unfalsifiable requirements** → no tension is detected, so contradictions ship.
- **Inconsistent terminology / personas** → issues drift, the backlog stops being searchable.
- **One giant requirements blob** → nothing resolves to a citable `PRD §N`.
- **Renumbering sections** → dangles `PRD §N` citations already posted in issues, plans, and reviews.
- **Treating the PRD as immutable law** (constitution-style) → real product changes get worked
  around instead of routed through the conflict gate the skills expect.
