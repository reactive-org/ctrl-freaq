# Plan parts — shared contract

An implementation plan is **one document posted as three comments**: the main `<!-- implementation-plan:v1 -->` comment and two **part** comments beside it on the planned issue. This file is the single source of truth for which section lives where, the part format, and who writes and reads them. `planner` writes; every skill that reads a plan reads the whole plan.

## Why it exists

GitHub refuses any comment body over 65,536 characters (`pipelib.limits.BODY_CHAR_LIMIT`), and real plans reach it — a story plan of 39–47k characters is ordinary, an epic plan's decisions alone run to 10k. Until 4.35.0 the plan was one comment, and the only relief was moving shipped phases' entries into per-phase record comments ([`plan-shipped-phases.md`](plan-shipped-phases.md)), which helped only after phases shipped and cost a judgment-heavy procedure on every revise. Splitting the plan by section gives each part its own cap from the first post, mechanically.

## Where each section lives

| Comment | Marker (first line) | Sections |
|---|---|---|
| **main** | `<!-- implementation-plan:v1 -->` | every section not listed below — `## Approach`, `## Architecture decisions`, `## UI decisions`, `## Phases`, the epic sections, `## Epic contract`, `## Predecessor`, `## Deviations from project docs`, `## Risks & watchpoints`, `## Open questions` — plus the header line and the footer |
| **build** | `<!-- implementation-plan-part:v1:build -->` | `## Changes (file-level)`, `## Data model / schema impact`, `## Test plan`, `## Coverage gap` |
| **references** | `<!-- implementation-plan-part:v1:references -->` | `## Doc grounding`, `## External sources consulted` |

The main comment holds what the resolver cites verbatim and what the tracker, the DoD projection and the reviewer's sequencing dimensions parse; the build part holds the work list the evaluator's plan adherence reads; the references part holds the citations. The assignment is `scripts/plan_parts.py`'s `PARTS` table — nothing else decides it.

**The main comment keeps every heading in its schema position.** A moved section's body there is one stub bullet linking to its part — `- In this plan's [build part](<url>).` — so the main comment reads as the plan's outline, and putting the parts back needs no knowledge of section order.

## Format of a part

```
<!-- implementation-plan-part:v1:<slug> -->
**Implementation plan — <slug> part** (<its section names>). Part of the implementation plan on this issue: …

## <section>
<verbatim>
```

**The marker is a separate family, on purpose.** `<!-- implementation-plan-part:` never starts with `<!-- implementation-plan:v1 -->`, so every plan lookup (`startswith`) stays blind to the parts and a part can never make the plan `MARKER_AMBIGUOUS`. A slug matching two comments is a duplicate: `MARKER_AMBIGUOUS` (the gathers stop, as does the review prep, which counts trusted authors' copies only; the evaluator, for whom adherence is one judgment among the merge gates, degrades it to a notice).

## Writing (planner)

The planner authors and reviews **one** body, `<facts.scratch>/plan.md`, exactly as before; the split is mechanical and happens at the post. On every staging (spine S7, beside `parse.py phases`):

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/plan_parts.py split "<facts.scratch>/plan.md" --out-dir "<facts.scratch>"
```

It writes `plan-main.md`, `plan-build.md` and `plan-references.md` there and reports each as `comments[]` with its `size` — the plan's only size fact, because the cap applies **per comment** (plan-schema.md, "Size"); `over_limit` names any comment past it. Then persist (spine S8), **parts first, main last**, so the main comment never links a part that does not exist:

1. **Each part, `build` then `references`.** When `facts.plan.parts` lists the slug, rewrite it in place: `gh_persist.py edit-comment <owner/repo> <its id> "<facts.scratch>/plan-<slug>.md"`. Otherwise — a fresh plan, or a plan posted before parts existed — post it and capture its URL: `gh_persist.py comment <owner/repo> issue <N> "<facts.scratch>/plan-<slug>.md"`.
2. **Cut again with both URLs** (from `facts.plan.parts`, or the post that just returned one) so the main comment's stubs link: `plan_parts.py split "<facts.scratch>/plan.md" --out-dir "<facts.scratch>" --url build=<url> --url references=<url>`. `unlinked` must come back empty.
3. **The main comment**, `<facts.scratch>/plan-main.md`: `gh_persist.py comment` on a fresh plan, the playbook's in-place op on a revise.

If a write fails part-way, the next revise repairs it: a part edited before a failed main edit holds newer sections than the main comment links, which is the state a revise overwrites anyway. A part is never deleted; one left with no sections still gets a body saying so. Never hand-edit a part, for the reason the main comment is never hand-edited: it is reviewer-verified content.

## Reading (every reader)

No reader sees the split. `gh_gather.py`'s plan-marker lookup — which the planner, resolver and drafter preps compose — `prep_evaluator.py`'s own thread scan, and `prep_review.py`'s, which reads the main and part comments of trusted authors only (SEC-1 of #171), put the parts back (`plan_parts.assemble_from_thread`, a thread scan, zero extra `gh` calls), so every staged plan path and body is the **whole** plan. `marker_parts` / `facts.plan.parts` lists each part's `slug`, `id` and `url` for the writer. A sub-agent that needs the plan is handed that staged path; none fetches the plan comment itself, which would read the main comment alone.

A main comment with no parts (a plan posted before 4.35.0) reads byte-for-byte as before. A stub whose part is missing, or a part section with no heading in main, rides in `notices`; the stub is kept, so a reader sees an empty section rather than a silent gap.
