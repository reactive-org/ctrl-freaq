# Built-in issue templates

Fallback issue-body templates for when the repo provides no issue template of its own
(`facts.repo_context.templates.present == false`). Use the section shapes verbatim; omit any section whose
guidance says "omit if none." Prefer the repo's own template when one exists.

**Anchor rule (every template, repo template included).** Issue bodies cite durable anchors — files by
path, code by symbol, docs by slug ID (`docs/prd.md §R-dry-run-default`), else §heading, or register ID
(`PRD-OQ-05`-style), issues by `#N` — and never an
**authored** `path:line` citation: line numbers rot silently the moment content moves, and the very work
an issue mandates is what invalidates them. Sole carve-out: a line number inside verbatim-quoted tool
output (a stack trace, a failing-test line) — evidence of an observed event, not a claim about current
source.

**Checklist heading rule (every template, repo template included).** An issue's checkable criteria live
under **`## Definition of done`** — the one heading the planner, resolver, and evaluator parse
([`../../_shared/dod-annotations.md`](../../_shared/dod-annotations.md), "Section finder"). A checklist under
any other heading (`## Acceptance criteria`, a repo template's own name for it) reads downstream as "no DoD":
the planner's `closes-dod` indexes nothing the resolver can tick or the evaluator can verify. So when the
repo's template names its checklist otherwise, keep that template's other sections and title the checklist
`## Definition of done`. The bug template carries no checklist, and a question none
([`../../_shared/question-issue.md`](../../_shared/question-issue.md)). The deliverable slice's
`## Acceptance criteria` is the slicer's, not a drafter template.

**Grounding altitude — freeze judgment, not facts.** Capture what took judgment and won't be re-derived:
dispositions, rulings, exemption classes, constraints, the why, an outcome-level Definition of done. Do
**not** freeze mechanically re-derivable facts — grep output, enumerated hit lists, current-state code
forensics: the planner grounds code state just-in-time at current HEAD, so draft-time forensics is stale
by plan time. A DoD sweep bullet states the **falsifiable criterion + its exemption classes** (e.g. "a
repo-wide search returns no reference presenting a retired path as live; dated run-log rows are
historical record — the owning command rules"), **never a frozen enumerated hit list** — enumeration is
the resolver's build-time grounding, re-derived fresh (≤2 examples marked non-exhaustive are fine).

**Grouped follow-up** (a proxy-filed brief naming several related items — `_shared/follow-up-filing.md`
"Grouping"): one issue, never an epic to decompose. Every item gets its own checkable bullet in the
template's checkable section — `## Definition of done`; a bug group (items sharing one root cause) states
the shared cause once and lists each symptom. Dropping or merging an item is the proxy's check (c)
failure.

**Bug template:**

```markdown
## Description
<one-paragraph summary of the bug>

## Steps to reproduce
1. <step>
2. <step>
3. <step>

## Expected behavior
<what should happen>

## Actual behavior
<what actually happens, including any error messages>

## Environment
- <OS / browser / version / etc., if relevant>

## Related issues
<only if user referenced other issues — see the spine's "Related issues" step. Omit section if none.>

## Additional context
<screenshots, logs, anything else — omit section if none>
```

**Incomplete feature template:**

```markdown
## What exists today
<what currently works — the behavior a user or developer observes, not code forensics>

## What's missing
<the specific gap>

## Definition of done
- [ ] <criterion>
- [ ] <criterion>

## Context
<why this was left incomplete, if known>

## Related issues
<only if user referenced other issues. Omit section if none.>
```

**New feature template (user story):**

```markdown
## User story
As a **<persona>**, I want **<capability>** so that **<benefit>**.

## Background
<why this matters — the underlying motivation>

## Definition of done
- [ ] <criterion>
- [ ] <criterion>

## Related issues
<only if user referenced other issues. Omit section if none.>
```

**Epic template:**

```markdown
## Goal
<one paragraph: what this epic delivers, what problem it solves>

## Background
<why now — what prompted this work; reference PRD/architecture sections where relevant>

## Definition of done
- [ ] All stories are closed
- [ ] <any epic-level acceptance bar, e.g. "5 UI flows green in CI">

## Related issues
<only if user referenced other issues. Omit section if none.>

## PRD impact
<only if applicable. Omit otherwise.>
```

**No `## Stories` section.** The story set is GitHub's native parent/sub-issue relation, established
by `create --parent` at filing time ([`../../_shared/epic-story-hierarchy.md`](../../_shared/epic-story-hierarchy.md)) — GitHub renders the panel and the
progress rollup from issue state. A markdown checklist beside it would be a second copy that can't
self-tick, so the `## Definition of done` names the bar ("All stories are closed") without listing
them. An epic filed before the relation existed keeps its `## Stories` section and is read through
the checklist fallback; don't add the section back to a fresh epic.

**Story template:**

```markdown
**Epic:** #<epic-#> — <Epic title>

## What exists today
<what currently works in this area and the limitation prompting this story — behavior-level, not code
forensics; the planner grounds code state just-in-time>

## What's missing
<the specific gap this story closes; reference architecture/PRD sections where relevant>

## Definition of done
- [ ] <criterion>
- [ ] <criterion>

## Context
<optional — dependencies, constraints, why this was deferred>

## Related issues
<only if user referenced other issues. Omit section if none.>
```

**Question template:** the `question`-issue body schema (`## Question / ## Audience / ## Constraints /
## Context / ## References / ## Why this matters / ## Tracked in`), the no-DoD rule, the title convention,
and the audience-label rule are [`../../_shared/question-issue.md`](../../_shared/question-issue.md) — the
single source of truth; don't restate them here.

**Open questions on a build issue.** Any build template above (Bug, Incomplete, New-feature, Epic, Story)
may carry an optional `## Open questions` section when the issue was drafted from a source with unresolved
open questions — placed after `## Related issues`, omit if none. Its per-entry schema, the disposition set,
and the native-`blocked by` rule live in [`../../_shared/open-question-links.md`](../../_shared/open-question-links.md)
(don't restate them here); the drafter writes it from the spine's Step-3.5 dispositions.

**About the "Out of scope" section:** Omit it by default. Only include it when one of these is true:

1. **The user explicitly excluded something** — e.g., "but I don't want to deal with X right now," "let's
   not bother with Y." Capture what they said, don't extrapolate.
2. **The title or capability is genuinely ambiguous about something a reader would reasonably assume is
   included.** For example, a feature titled "Add export to PDF" might reasonably make a reader assume bulk
   export is included; if it's not, that's worth calling out. The bar is high — only flag this when the
   ambiguity is real, not speculative.
3. **A part was `scoped-out` because an open question gates it** (the spine's Step 3.5). Write one line per
   scoped-out OQ, naming the OQ id so it cross-references the `## Open questions` entry — this is what keeps
   the gated part cleanly out-of-scope (and out of the DoD). Only OQ-driven scope-outs are auto-added;
   trigger 1's anti-extrapolation rule still governs everything else.

Default to omitting. The Definition of done already defines what's in scope; don't pad the issue with
speculative exclusions. Inventing out-of-scope items the user never mentioned is a form of hallucination —
resist it.
