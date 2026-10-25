# Plan summary — shared reference

The **`## Plan summary`** block is the human-readable digest of an implementation plan, written for the
operator rather than for a parser. Two skills render it from this one contract:

- the **planner**, on every clean exit where **this session posted or refreshed a plan** — immediately
  before its `## Handoff`, closing with the invitation below;
- the **resolver**, at session start, before any work begins — focused on the part of the plan this run
  serves, and with no invitation.

It is a *rendering of the plan comment*, never a second plan, and it is **never part of `## Handoff`**
(see [`handoff-format.md`](handoff-format.md) — that schema, its fields, its ordering, and its
closed-set vocabulary are untouched by this block). The skill's main loop authors it: no script writes
it, and no judgment sub-agent emits it.

## Block schema

Bold-labelled sections, in this fixed order. Each maps onto a heading of the implementation-plan schema
([`../planner/references/plan-schema.md`](../planner/references/plan-schema.md)), so the summary
compresses what the plan already says and adds nothing:

```
## Plan summary

**Approach:** <2–4 sentences in plain words: what gets built and why this shape>

**What changed:**                         (planner revise mode only)
- <what moved> — <why it moved>

**Architecture decisions:**
- <decision> — <why, in plain words> [<attribution, when the plan carries one>]

**Seams & integration points:**
- <boundary this plan defines or consumes> — <what is pinned, and what is deliberately not>

**Phases:**                               (omit when single-phase)
1. <title> — <one line>
2. <title> — <one line>

**Watch for:**
- <the invariant, trap, or unresolved question the reader most needs>
```

Sources, and what each section owes:

| Section | Source | Obligation |
|---|---|---|
| `**Approach:**` | `## Approach` | plain words — no schema vocabulary the operator would have to decode |
| `**What changed:**` | the revise reconciliation the planner already computed | what moved and **why**; carried, never re-derived. Covers every story or phase added or retired, every decision added, reversed or dropped, and every new deviation; an untouched section gets no bullet. At most 6 bullets — changes of one kind share a bullet. The planner renders this same block first at its revise confirm, before the plan posts (`../planner/references/revise-reconciliation.md`, "What the confirm shows") — the operator approves the change list, not a classification |
| `**Architecture decisions:**` | `## Architecture decisions` | at most 5 bullets. Carry a `[user decision <date>]` or `DEVIATION (agreed <date>)` attribution whenever the plan's entry has one — a decision the operator made, or one that departs from a project doc, is exactly what they are re-reading for |
| `**Seams & integration points:**` | the boundary bullets in `## Architecture decisions`, the layer/module assignments in `## Changes (file-level)`, `## Epic contract` (a story), `## Story contracts` + `## Integration strategy` (an epic) | at most 5 bullets. A seam whose body was deliberately cut keeps the cut visible — "signature pinned; body deferred to #M" |
| `**Phases:**` | `## Phases` | one line per phase, in plan order; a phase whose `checkpoint` is `pause` (or absent on a non-last `code-shipping` phase) ends its line `· pauses after` — where the resolver will stop and ask, which the operator reads before the handoff, not after |
| `**Watch for:**` | `## Risks & watchpoints` + `## Open questions` | at most 3 bullets. A `recorded-blocked` open question always renders — it is in-scope work the plan could not specify |

Which altitude a plan is at changes only the **source** the seams section reads, never the block's
shape: one rendering, no per-type branch.

## Rules

- **Concise.** The whole block reads in well under a screen. It earns its place by being shorter than
  the plan; a digest as long as its source is not a digest.
- **Omit, never pad.** A section with nothing to say is dropped — the same rule the plan schema states
  for its own headings.
- **Compress, never extend.** The summary introduces no decision, seam, or phase the plan does not
  carry, and it never resolves something the plan left open. Where the plan hedged, the summary says
  the plan hedged.
- **Contract tokens survive compression.** Issue and PR numbers, `#<N>` references, symbol and file
  names, and the open-question treatment vocabulary (`planned-around` / `recorded-blocked` /
  `provisional-default`) are quoted as the plan spells them.

## The `focus` parameter

The one value that varies between callers — which is why there is one rendering and no branch:

- **planner** — no focus: the whole plan, at the altitude it was authored.
- **resolver, code-shipping routes** — the phase this session will ship. Every phase still renders in
  `**Phases:**`; the current one is marked and expands to its `closes-dod`, `deliverable`, and
  `depends-on` values. The other sections narrow to what bears on that phase, with anything a later
  phase owns named in one line rather than dropped.
- **resolver, epic-as-target** — the integration state: the story set with what each delivers, and
  `## Integration strategy`. An epic plan carries no `## Phases`, so that section is omitted.

## The invitation (planner only)

The block closes with a short prose offer to walk through the plan. Prose, not `AskUserQuestion` — this
is not a gate, and [`asking-the-user.md`](asking-the-user.md) reserves the card for a decision with
named options. It says three things:

1. any part of the plan can be explored or questioned now;
2. answers come from the plan and the grounding already in hand;
3. a **change** to the plan means re-running `/ctrl-freaq:planner #<N>`, which re-enters in revise
   mode because the plan is present.

**Discussion after the handoff is read-only.** Answer from what is in hand; perform no GitHub write, no
re-draft, and no reviewer re-run — not even to correct something the discussion surfaced. This is the
load-bearing half of the offer, because nothing else prevents it: the in-place plan-comment update op is
already named in the planner's own playbooks, and its "only write surface is the plan comment" invariant
*permits* the write. A posted plan is reviewer-verified at a recorded ref; an in-session edit would
replace it with an unverified one and leave the footer's ref and the review-pass count both lying. When
the operator wants a change, name the re-run command and stop there.
