---
name: doc-reviewer
disable-model-invocation: true
description: Review one of the five pipeline docs — `docs/constitution.md`, `docs/prd.md`, `docs/architecture.md`, `docs/architecture-notes.md`, or `docs/ui-design.md` — against its bundled authoring guide and report concrete, guide-cited suggestions to align the doc with the guide. Reports findings first (propose-only), then offers to apply the accepted ones — staged in a workspace and landed via an offered PR (prd.md §8.2), never edited in the read-only root. Explicit-invocation only: run it as `/ctrl-freaq:doc-reviewer <doc-path>` (add `--guide <type>` for an oddly-named doc). Not for code or pull requests.
---

# doc-reviewer — router

Review one of the five pipeline docs against its **authoring guide** and report concrete,
guide-grounded suggestions to align the doc with it. Every finding cites *where in the guide* the
expectation comes from and *where in the doc* it applies. **Report first, apply only on request** — a
doc like the constitution loads into context on every pipeline run and bad edits are expensive, so
the reasoning is shown before anything touches the file. One interactive session; it ends with a
plain summary, never a `## Handoff`.

## When this applies

The doc must be one of the five guided docs (matched by **basename**, wherever it sits in the repo);
the guide always comes from the plugin bundle, never the consuming repo, so a repo cannot drift the
rubric out from under itself:

| Doc | Bundled guide |
|---|---|
| `prd.md` | `${CLAUDE_PLUGIN_ROOT}/docs/guides/prd.md` |
| `architecture.md` | `${CLAUDE_PLUGIN_ROOT}/docs/guides/architecture.md` |
| `architecture-notes.md` | `${CLAUDE_PLUGIN_ROOT}/docs/guides/architecture-notes.md` |
| `ui-design.md` | `${CLAUDE_PLUGIN_ROOT}/docs/guides/ui-design.md` |
| `constitution.md` | `${CLAUDE_PLUGIN_ROOT}/docs/guides/constitution.md` |

## 1. Prep — none (the inputs are working-tree paths the operator names)

**No prep script — nothing to gather.** The two inputs are working-tree paths: the doc under review
(named by the operator) and its bundled guide (resolved from the table above). No `gh` state, no
issue/PR, no registry — so there is no `prep_doc_reviewer.py` and no gather round-trip at all. This
absence is **deliberate**, not an omission: the review reads local files, and the apply-mode landing
writes them back through a workspace. A future editor adding a prep call here should first find a real
remote fact to assemble.

## 2. Route — one linear flow

**No mode fork:** identify → read → review → report → [apply mode:] stage-in-workspace → land →
summary. Apply mode and the §8.2 landing approve/decline are **runtime gates inside** the flow, never
routes. One playbook — read it and run it end to end:

| Facts | Playbook |
|---|---|
| any review run | [`playbooks/review-flow.md`](playbooks/review-flow.md) |

Before reviewing, the flow forces a `Read` of
[`references/review-lenses.md`](references/review-lenses.md) — the guide-resolution rule, the five
review lenses in order, the three honesty rules, severity calibration, and the fixed report shape.

## 3. Invariants

- **Reports first, applies only on request.** The structured report is shown before anything touches
  the file; apply is offered after, on the findings the operator accepts — never a silent rewrite.
- **The guide always comes from the plugin bundle, never the consuming repo.** It is the single
  source of truth, so a repo cannot drift the rubric to always pass its own doc. The guide is
  read-only — never edited.
- **Tracked-file edits land via an operator-gated PR (prd.md §8.2).** Apply mode never edits the
  read-only root: accepted edits stage in a **work workspace**, and the landing (commit + push + a PR
  summarizing the doc changes) is **one explicit final gate**. On decline: **no git actions** — the
  summary reports the workspace path + ready-to-run landing commands.
- **Apply-time discipline** (in [`references/review-lenses.md`](references/review-lenses.md)): stable
  `§N` anchors are never renumbered (renumbering dangles posted citations — itself a guide
  anti-pattern); moving content into a sibling doc is a separate offer, never bundled with an accept.
- **Stack-agnostic.** A finding must trace to a guide principle, anti-pattern, or checklist item —
  never to "this isn't how the example does it."

## 4. Summary — not a `## Handoff`

doc-reviewer is **not** a pipeline stage: no cross-session handoff, no GitHub state beyond the
optional landing PR. It ends with a plain **summary** (the flow's last step): the verdict, the
findings applied / declined, and the landing outcome (PR opened with the doc-change summary, **or**
the workspace path + ready-to-run commands on decline, **or** report-only when nothing was applied).
