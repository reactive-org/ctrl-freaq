# Follow-up filing — drafter-proxy sub-agent protocol

The single execution path for *creating* a follow-up issue from the **resolver** (its review-loop and
end-of-build checkpoint), the **evaluator** (its post-merge residual-filing step), the **planner**
(a seam disposition's follow-up — "contract only" / "own issue", filed before the plan posts so its
boundary bullets cite real issue numbers), or the **review** tool (a `Thread` card's **File as follow-up**
answer, filed before the decision reply that names it), or the **designer** (its conventions step, after the
approving write) and the resolver's **design-check Review card** (a `Convention` card's **File as follow-up**
answer — "UX conventions" below). All file the same way: no hand-crafted `gh issue create`
bodies — every follow-up routes through the `drafter` (PRD-grounded, sub-agent-reviewed) via a
`general-purpose` sub-agent that proxy-confirms the draft and returns the URL. The caller keeps its own
registry / timing / URL-weaving rules; this file is only the filing round-trip they share.

## Grouping — one issue per related group

Related items file as **one** issue, not one each: several findings about one module get planned and
fixed together anyway, and one issue per item floods the tracker. Before filing, the caller groups the
items it is about to file at this moment:

- **Same type, same seam.** A group is items one PR would naturally fix together — the same module or
  seam, and the same type. Never mix types: each has its own body template and urgency.
- **A `bug` group only on a shared root cause** — the umbrella issue the retry ladder already allows for
  failures sharing one; unrelated bugs stay separate.
- **At most ~5 items**, so the issue stays plannable in one pass. A `revise-existing` group never spans
  two target issues. The one uncapped group is the resolver's **docs lane**: drift in docs the repo routes
  through a named command or process files as one issue per owning process, one item per claim, because
  that process edits them in one pass — and one issue per claim, re-filed per layer, never converged.
- **A single item is a group of one** — grouping never forces unrelated items together.

Who approves the grouping is the caller's: the resolver shows the proposed groups — review-deferred items
included — on its end-of-loop checkpoint card, and states the grouping in the round reply only for the
`file-now` items a commit needs a number for, which file immediately. The **planner's** seam-disposition
follow-ups are not grouped — each boundary bullet cites its own `#M`. Nor are the **review** tool's: each is
one issue for one thread, named in that thread's reply. The **designer's** and the **design-check Review card's**
groups are shown on their `Convention` card, which approves them ("UX conventions" below).

## Protocol — sub-agent proxy-confirms via the drafter

For the groups the caller's own gate has approved at one filing moment (the resolver's user at the
end-of-loop checkpoint or a retry-ladder / review-loop deferral, the evaluator's residual step, the
planner's seam-disposition answer, the review tool's **File as follow-up** answer on its `Thread` card, the designer's or the resolver's **File as
follow-up** answer on its `Convention` card), spawn one `general-purpose` sub-agent per group with this prompt
(substitute the placeholders at call time) — **all in one message**, so the batch runs concurrently.
The groups are independent, and spawning them one after another costs the drafter's full round-trip
each in series (an observed resolver run filed 3 follow-ups in ~30 minutes that way).

**The one exception:** `revise-existing` items that share a target issue go one at a time — they
stage to the same `/tmp/gh-drafter-<N>/revised.md` and would race each other's `edit-body`. Every
other combination is safe to spawn together: a new-mode drafter stages under its own
`/tmp/gh-drafter-new-<pid>/`, so concurrent runs never clobber a staged body.

Wait for the whole batch to return before weaving URLs. An item that returns `error: <reason>` does
not hold up the others — weave the URLs that came back, and report the errored items to the operator
as not filed, with the reason.

```
You are filing one GitHub follow-up issue on behalf of the calling skill
(the resolver, the evaluator, the planner, the review tool, or the designer). The issue covers the group
of related items below — one item, or several that one PR would fix
together. Invoke the `/ctrl-freaq:drafter` skill, proxy-confirm the
draft, and return the filed issue URL.

Group to file:
- Type: <bug | incomplete-feature | feature | deferred-test | revise-existing>
- Title hint: <one-line summary covering the whole group>
- Items (one per line, each with the caller's item id):
  - <id>: <2–5 sentences: the finding, why it matters, and the
    judgment worth freezing — rulings, exemption classes, constraints.
    Durable anchors only (paths, symbols, doc section headings, register
    IDs, #N); never pasted grep output or path:line inventories —
    downstream skills re-derive sites fresh. If the item describes state
    the parent PR introduces that is not yet on the default branch, say so>
- Parent reference: PR <URL>, issue #<N>, epic #<E> (if applicable)
- Repository: <owner/repo>

Steps:

1. Invoke the drafter skill, passing the items above as the informal
   feedback. State the type hint, title hint, and parent reference
   clearly so the drafter has them at classification time. With more
   than one item, state that this is **one grouped follow-up**: one
   issue, with one checkable bullet per item in its checkable section
   (`## Definition of done`, whatever the type) — not an epic to
   decompose. Also state that this is a
   **proxy-filed follow-up (lean review)** — the drafter's lean tier:
   one review pass, suggestions surfaced unapplied at its gate. Also relay the attribution rule: any claim describing
   what the parent PR introduces (state not yet on the default branch)
   must be attributed to the PR in the body ("PR <URL> ships…"), never
   asserted as current-repo truth.

2. The drafter validates the draft with its single lean review pass
   (against the project's PRD, architecture, constitution, and current
   code state). Suggestions it surfaces unapplied at its gate are
   informational — approve on step 3's checks alone (you may note them
   in your return); don't add review rounds of your own.

3. The drafter will reach its user-confirmation gate ("Show the draft
   and wait for confirmation" — nothing is filed before it). You act as
   the user at this gate.
   Run three checks:

   a. Type — does the drafter's chosen type match the hint? If the drafter
      decided differently (e.g., classified as `incomplete-feature` when
      you hinted `bug`), accept the drafter's call IF its rationale is
      sound. The drafter sees the description directly and may classify
      better than the hint; only override if the drafter has clearly
      misread the description.

   b. Parent reference — is the parent PR/issue/epic preserved in the
      body's Related-issues section? The drafter's bug, story, feature,
      and incomplete-feature formats all have this section. Without it
      the filed issue is orphaned. If missing, reply to the drafter:
      "Please add the parent reference (PR <URL>, parent issue #<N>) to
      the Related-issues section."

   c. Substance — does the body's What's-wrong / What's-missing /
      Definition-of-done content match the items? For a group, every
      item has its own checkable bullet — none dropped, none merged into
      another. If the drafter hallucinated detail the items don't
      support, or folded items together, reply with a one-sentence
      correction.

4. Approve if all three checks pass. If any check fails, reply with the
   correction and let the drafter iterate. Cap at 2 correction rounds —
   if the third draft still fails any check, stop and return an error to
   the parent with the latest draft inline so the parent can decide.

5. After approval, the drafter files through its staged write path
   (`gh_persist.py create`, or `edit-body` in revise mode) and returns the
   URL. Capture that URL.

Return only:
- The filed URL (or "error: <reason>" if you stopped at step 4's cap),
  with the item ids it covers
- The drafter's final type (in case it overrode the hint)
- A one-line note if you raised any correction before approving

Do NOT file an issue yourself with `gh issue create`. The drafter does
this inside its own flow. Your role is to invoke, proxy-confirm, return.
```

The sub-agents isolate the drafter's verbose work (PRD reading, classification questioning, nested
sub-agent review loop) from the caller's main context. The caller sees one batched round-trip: one
brief per group in, one result per group out — each result naming the item ids it covers, which is
what the caller's URL weaving keys on.

## UX conventions — the ui-design doc follow-up

The designer session (its spine's D9) and the resolver's design-check Review card
(`skills/resolver/references/phase-continuation.md` §4.4) are where an operator reacts to a user interface. A
**reusable UX convention** the operator states there files as a follow-up that updates the repo's
`role: ui-design` doc. Each caller renders this section by link and adds only when it asks and where the card sits.

- **What counts.** Something the operator says about what users see or do that they word as holding beyond
  this issue — every screen, all lists, "we always", "never". Its subject is a flow, a state, copy or
  layout: what a design record binds ([`design-record.md`](design-record.md) "What binds"). A one-off change
  to this issue's design is not a convention. When it is unclear whether the operator meant a rule or a
  one-off, propose it: the operator rejects it on the card, and the card is cheap where an unrecorded
  convention is lost. This is the model's judgment — no script reads intent from prose.
- **The card.** A consent card (filing an issue is an outward act), `header: "Convention"`, answers
  **File as follow-up** / **Don't file**. Its brief quotes each convention as worded for its item and names
  the target doc, or says the repo declares none. The card's notes may reword or drop items. In a session a
  coordinator started, its question opens `<skill> · #<N> · conventions`. A convention already offered in the
  session is never offered again on a later card.
- **One group per card.** Every convention one card approves files as **one** group — one issue, so the
  doc is updated in few passes — split into groups of at most five when there are more. The designer raises
  the card once a session; the resolver files at each design-check card, since a later checkpoint may never
  come, and never offers a convention twice. Each convention is
  one item and one checkable bullet. Type hint `incomplete-feature`: the doc exists, or should, and lacks
  the convention.
- **The item.** The operator's words, quoted; where they said it — `design session` or
  `phase <N> review`; and the target doc. The target is the first `role: ui-design` entry of the repo's
  `<!-- doc-catalogue -->` in declaration order (a `kind: dir` entry names the directory, and the
  follow-up's own plan picks the member file) — the designer reads it from `facts.ui_design`, the resolver
  from `facts.grounding_docs`. An entry the catalogue declares but the repo has not committed (`present:
  false`) still names its declared path, which the follow-up's PR creates. With no such entry, the item says the repo declares none, and that its PR
  creates the doc and adds its catalogue line, whose authority the operator confirms there
  ([`doc-catalogue.md`](doc-catalogue.md) "Keeping it current"). Never name a path the catalogue does
  not declare. The item names no stack of its own; it quotes the operator. Before filing, search the open
  issues for one that already carries the convention for the same doc, in the shape
  [`../resolver/references/follow-up-tracking.md`](../resolver/references/follow-up-tracking.md) "Before
  filing" uses (an open-issue search on the convention's distinctive words and the doc path, a plausible match
  read through `gh_gather.py`): a match is named on the card and in the caller's output, never filed again.
- **Parent reference.** The issue, and its epic when it has one; the PR too when the resolver files.

Two invariants hold for every caller:

- **The doc edit lands through the follow-up's own PR** — its own resolver run, planned like any issue.
- **The calling session never commits it.** The session that hears a convention never edits the ui-design
  doc or the doc catalogue for it, so the edit is never on the issue's PR. A resolver fix round may build
  the convention into this issue's own code when the operator asks for that change; the doc edit still
  waits for the follow-up.
