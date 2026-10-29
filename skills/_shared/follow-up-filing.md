# Follow-up filing — drafter-proxy sub-agent protocol

The single execution path for *creating* a follow-up issue from the **resolver** (its review-loop and
end-of-build checkpoint), the **evaluator** (its post-merge residual-filing step), the **planner**
(a seam disposition's follow-up — "contract only" / "own issue", filed before the plan posts so its
boundary bullets cite real issue numbers), or the **review** tool (a `Thread` card's **File as follow-up**
answer, filed before the decision reply that names it). All file the same way: no hand-crafted `gh issue create`
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
one issue for one thread, named in that thread's reply.

## Protocol — sub-agent proxy-confirms via the drafter

For the groups the caller's own gate has approved at one filing moment (the resolver's user at the
end-of-loop checkpoint or a retry-ladder / review-loop deferral, the evaluator's residual step, the
planner's seam-disposition answer, the review tool's **File as follow-up** answer on its `Thread` card), spawn one `general-purpose` sub-agent per group with this prompt
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
(the resolver, the evaluator, the planner, or the review tool). The issue covers the group
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
