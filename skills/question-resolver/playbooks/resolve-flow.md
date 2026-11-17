# resolve flow — read → ground → decide → verify → record → close → fold-back

Prep gave you the facts; run in order. Gates go through `AskUserQuestion` per
[`../../_shared/asking-the-user.md`](../../_shared/asking-the-user.md). If `is_question` is false, the
router already stopped you — do not proceed. On `reentrancy.mode == "revise"`, show the operator what the
`prior_decision` said and reconcile against it before deciding.

## 1. Read the thread's current state (judgment)

Dispatch the question-status reader
([`${CLAUDE_PLUGIN_ROOT}/skills/question-sweep/references/question-status-reader-prompt.md`](../../question-sweep/references/question-status-reader-prompt.md)
— a raw-read cross-skill reference; use the plugin-root path), filling its `<<...>>` from `sections`
(the staged body/thread). It returns `resolved-in-thread` (you'll **formalize + verify** that answer),
`still-open` (you'll **facilitate** a decision), or `AMBIGUOUS` (surface it in the discussion).

## 2. Ground against the project docs (judgment)

Read the docs the question's `## Constraints` / `## References` point at (targeted — not a blind sweep),
plus the `binding` entries of `facts.grounding_docs` it touches — always a `constitution`-role entry (a
`kind: dir` entry's `members`; none, or `DOC_CATALOGUE_ABSENT`: ground on the question's own references
and say so). Extract, with citations, the **binding constraints**
(regulatory/legal/contractual/architectural — `constitution §C-<slug>`, `PRD §R-<slug>`,
`path/to/file:NN`) and the **decision space** (viable options + what each commits the project to).
Anti-fabrication: a constraint you can't cite to a doc section (its slug ID, or its heading in a doc
without IDs) or a file line is not a constraint — don't invent one.

## 3. Present the evaluation and discuss — the operator decides

Present one consolidated view: **current state** (the step-1 reading) · **viable option(s)** each with
cited constraint implications · **coverage gaps** flagged by *topic* (never comment→audience attribution
— a comment carries an author, not an `audience:*` role) · a clearly-marked **recommendation**. Then get
the operator's decision via `AskUserQuestion` or conversation. **Their call is the decision.** If a
constraint rules out every option, or the reading was `AMBIGUOUS`, surface that — don't force one.

## 4. Verify the chosen decision (judgment)

Once the operator settles, verify independently before recording — a constraint missed in discussion is
the highest-cost failure here. Dispatch the constraint audit
([`../references/constraint-audit-prompt.md`](../references/constraint-audit-prompt.md)), filling its
`<<...>>` (the question incl. `## Constraints`, the **chosen decision**, repo root,
`facts.grounding_docs` as `<<catalogue_entries>>`). A **BLOCKER** → return to §3, show the finding,
re-decide (do not record). SUGGESTION/NIT inform, don't gate.

## 5. Record the decision

Compose the decision comment, stage it to `facts.scratch/decision.md`, then post via the single write path
— on revise pass `--delete-marker-id` so the prior comment is **replaced, not duplicated** (gh_persist
posts-new-then-deletes-old):

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py comment <owner/repo> issue <N> <facts.scratch>/decision.md \
  [--delete-marker-id <reentrancy.prior_decision.comment_id>]
```

Comment schema — the `<!-- question-decision:v1 -->` marker is the **first line** (the durable resolution
Tier 1 reads); byte-identical to [`../../../docs/specs/examples/question-decision.md`](../../../docs/specs/examples/question-decision.md):

```markdown
<!-- question-decision:v1 -->
## Decision
<the decision, stated plainly — what was decided>

## Rationale
<why — the reasoning, the option chosen over the alternatives>

## Constraints respected
<the binding constraints the decision honors, each cited — `constitution §N`, `PRD §N`, `path/to/file:NN`>

## Unblocks
<the build issues this answer unblocks (from the native `blocking` list), or "none">

## Caveats
<any coverage gap, provisional edge, or follow-up the decision leaves open — or omit if none>
```

The schema's `constitution §N` / `PRD §N` stand for the cited section's slug ID
(`constitution §C-<slug>`, `PRD §R-<slug>`), or its heading in a doc without IDs.

Attribute the decision to the operator; never author one they didn't approve.

## 6. Offer to close (and reopen)

Offer `${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py close <owner/repo> <N> --reason completed` — gated, not
automatic (some teams keep the question open until the fold-back merges). Closing an already-closed issue
is a no-op, so this is safe on a re-run. In the rare reentrant case where a materially-changed decision
needs the closed issue reopened for visibility, offer `gh_persist.py reopen <owner/repo> <N>` first.

## 7. Doc fold-back proposals — propose-only, never applied

Assess which docs the decision touches and produce a **proposal report** — you do **not** edit docs. Frame
every proposal as the **state now**, not a changelog. Cover the fold-back moves in
[links](../../_shared/open-question-links.md) §"Doc fold-back" (rewrite to the decided state; remove the
`PROVISIONAL`/open-question marker; flip any register + add `tracked in #<N>`) where each applies. Format,
one entry per affected doc/section:

```
### <doc path> §<ID or heading>
- Change: <the state-now edit to fold the decision in>
- Why: <the decision + citation it reflects>
```

## 8. Summary

Close with the plain summary the router §4 describes — not a `## Handoff`.
