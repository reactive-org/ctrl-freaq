# Review lenses, honesty rules, severity, and the report shape

The review rubric, force-read by [`../playbooks/review-flow.md`](../playbooks/review-flow.md) before
reviewing. Carried from the frozen v1 `doc-reviewer` (vantage `7bffb90`); the lenses, the honesty
rules, the severity calibration, and the report shape are the v1 content — only the file it lives in
changed (router+one-playbook anatomy, architecture.md §9).

## Guide resolution

- **Match the basename**, wherever the doc sits in the repo, against the router's `## When this
  applies` table → the bundled guide path. The guide always comes from the plugin bundle
  (`${CLAUDE_PLUGIN_ROOT}/docs/guides/<basename>.md`) — the single source of truth, so a consuming
  repo cannot drift the rubric out from under itself.
- The guide is **read-only** — never edited. It is bundled.

## The five review lenses (walk in this order)

Each guide shares the same skeleton. Walk these lenses, in this order, grounding every finding in the
guide text:

1. **Authoring principles** — for each principle the guide states, does the doc honor it? (e.g. the
   constitution guide's "one line per rule", "only inviolable rules", "prescriptive and checkable".)
2. **What belongs here vs. the sibling docs** — flag content the guide says lives elsewhere:
   *why*/rationale prose → `architecture-notes.md`; a deviable default → `architecture.md`; a
   build/test command → a `CLAUDE.md` marker block. Misplaced content is one of the highest-value
   findings because the guides are explicit about the boundaries.
3. **Anti-patterns** — for each anti-pattern the guide lists, does the doc exhibit it?
4. **Authoring checklist** — walk each checklist item and mark it pass/fail with evidence from the
   doc.
5. **Recommended shape** — note structural gaps (a section the guide recommends that the doc is
   missing), but only when the guide actually calls for it.

## Three honesty rules

- **Only review against what the guide says.** Do not import generic doc-writing opinions the guide
  doesn't hold. If the guide is silent on something, it's not a finding.
- **The worked example is an illustration, not a template.** Guides include a full worked sample
  (often Rails). A doc is *not* wrong for omitting an example rule, using a different stack, or
  numbering things differently — judge conformance to the **principles**, not similarity to the
  sample. Faulting a Swift or Python doc for "not looking like the Rails example" is a false
  positive.
- **Credit what's right.** Call out, grounded in the guide, what the doc does well. The output is an
  editor's review, not a lint dump — a doc that nails the testing bar and cross-references deserves
  to hear it, and it tells the user what *not* to touch.

## Severity calibration (to the guide's own stakes, not your taste)

- **🔴 Blocker** — violates a load-bearing guide principle. For the constitution that includes a
  *miscategorized rule* (a deviable default stated as inviolable law, which the guide says belongs in
  `architecture.md`), an *uncheckable rule* a reviewer can't gate a diff against, or *renumbered*
  sections that dangle existing `§N` citations.
- **🟡 Should-fix** — an anti-pattern is present or a checklist item fails (e.g. rationale prose in
  the constitution; a missing testing bar).
- **🟢 Consider** — conciseness, phrasing, or nuance that would sharpen the doc without being wrong
  today.

## Report shape (carried verbatim — box-1 parity target)

Output a single structured report. Keep suggested rewrites **concrete** — show the replacement line
or block, not just "tighten this." Order findings by severity (Blocker → Should-fix → Consider); if a
section has no findings, say so rather than padding it. Use this shape:

```
# Doc review — <doc path>   (guide: <guide basename>)

Verdict: <Aligned | Minor drift | Significant drift> — <one-line rationale>

## What's working
- <strength> — <guide ref>

## Findings
### 🔴 Blocker — <title>    guide: <section/heading>  ·  doc: <§ or lines>
<what's off, why it violates the guide, and the concrete fix/rewrite>

### 🟡 Should-fix — <title>    guide: <…>  ·  doc: <…>
<…>

### 🟢 Consider — <title>    guide: <…>  ·  doc: <…>
<…>

## Guide checklist
- [x] <checklist item> — <evidence it passes>
- [ ] <checklist item> — <what's missing>
```

> This rendering is **not** a prd.md §7 persisted artifact (a doc review is session output, never
> written to GitHub), so there is no frozen `docs/specs/examples/` capture to byte-match; box-1
> parity is measured directly against this carried v1 shape and the v1 vantage render.

## Apply-time discipline

When the operator accepts findings and the flow applies them in the workspace:

- **Preserve stable `§N` anchors** — never renumber sections (renumbering dangles citations already
  posted in plans and reviews; it is itself a guide anti-pattern). Append or edit in place.
- **Don't move content into a sibling doc on your own.** When a finding is "this belongs in
  `architecture-notes.md`", removing it from the reviewed doc is in scope on accept; *writing* it into
  the sibling doc is a separate action — offer it, don't assume it.
- **Re-check that your edits didn't introduce a new anti-pattern** (e.g. trimming a rule into
  something uncheckable).
- **Keep the doc catalogue current.** When an accepted edit materially rewrites the doc — or moves
  content so its catalogue `summary` no longer describes it — update its entry in `docs/README.md`'s
  `<!-- doc-catalogue -->` block in the same workspace, then run
  `${CLAUDE_PLUGIN_ROOT}/scripts/doc_catalogue.py check --root <workspace>` before the landing gate, per
  `skills/_shared/doc-catalogue.md` "Keeping it current": fix a `DOC_CATALOGUE_MALFORMED`, and put a
  non-empty `confirm` (a binding or authority change) to the operator on a consent card. A summary or
  role edit needs no card.
