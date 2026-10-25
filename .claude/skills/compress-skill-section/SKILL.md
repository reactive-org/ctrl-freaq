---
name: compress-skill-section
description: >-
  Compress (densify / token-reduce) one section of a prompt file in this repo — a
  `skills/*/SKILL.md`, an `agents/*.md`, or a `skills/_shared/*.md` fragment — making it shorter,
  denser, or lower-token WITHOUT losing precision, via an adversarial review→fix loop and a
  whole-document coherence check, then proposing the rewrite for you to apply. Typical asks:
  "compress/shrink/tighten/trim/condense this section", "make the github-ops section denser",
  "reduce tokens in the resolver's §10", "cut the filler/repetition", "apply the compression rules
  to X", or calling a named section, heading, or §-anchor "too long", "too verbose", or "bloated".
  Still triggers when the request adds guardrails — keep behaviour unchanged, preserve scope
  qualifiers, don't drop contract/identifier tokens, confirm it still reads right in the whole
  file — since those describe a careful compression, not a different task. Works one section at a
  time and proposes the result (rewritten text, token delta, validator results); it never edits
  the file. Do NOT use for compressing images/binaries, summarizing, translating, code review, or
  adding new sections.
model: opus
---

# Compress a skill section without losing precision

These skill/agent bodies are **Opus instruction prompts**, not chat prompts. Cutting the wrong
token — a scope qualifier, a parsed identifier, a rationale clause — silently changes agent
behaviour, and this repo has **no offline test harness** to catch it. So compression here is
adversarial by design: draft a denser version, then have a panel of skeptics try to prove it lost
precision or broke the surrounding document, fix what they find, and repeat until a clean round.
The win condition is **denser AND precision-preserved AND document-coherent** — never just "fewer
tokens."

## Hard rule: propose-only

**Never `Edit`/`Write` the target file in this skill.** Your deliverable is *text*: the rewritten
section, a token delta, validator results, and a short changelog. The user applies it themselves
as a separate, explicit step. Why: a compression is a judgement call about meaning; the human
owns the decision to merge it, and reviewing a proposal is cheaper than reverting a bad edit. The
reviewer subagents are read-only; the fixer produces text, not file edits.

Compress **one section per run.** Sections interact through cross-references and shared
vocabulary; batching hides coherence regressions.

## Inputs

- **Target file** — e.g. `skills/github-pr-evaluator/SKILL.md`, `agents/github-ops.md`.
- **Section** — a heading (`### Delegating mechanical work to github-ops`) or a §-anchor (`§10`).

If either is missing, ask once via `AskUserQuestion` (one decision per card). Usually the user
names both.

## The rules you compress by

**Read [`references/compression-rules.md`](references/compression-rules.md) before drafting** —
it is the Do / Don't / preserve-verbatim contract, with the vendor evidence behind each rule. If
`CLAUDE.md` has a "Compressing prompts" section, read it too and let it override on any conflict
(repo source of truth).

## Workflow

### 1. Locate and snapshot the section

- `grep -n` the heading; the section runs from its heading line to the **next heading of the same
  or higher level**. Read those lines.
- **Read the whole file too.** Coherence depends on what *other* sections say about this one
  (cross-refs, `per §N`, defined §-anchors others cite).
- Capture the baseline: word/char count, and the contract-token multiset from
  [`references/validators.md`](references/validators.md) (Validator A, "before"). You will compare
  against this at the end.

### 2. Draft v1

Apply `compression-rules.md`: cut filler and duplication, switch to imperative + delimited
structure, and **preserve every contract token, scope qualifier, and rationale clause**. Aim for
density, not minimum length.

### 3. Adversarial panel (parallel, one subagent per lens)

Launch the **four lenses in [`references/review-lenses.md`](references/review-lenses.md) as parallel
foreground `Agent` calls in a single message** (one `Agent` each, `general-purpose`, read-only) and
let all four return before the fix pass. Do **not** run them with `run_in_background`, and do **not**
call `Monitor`/poll to wait — foreground parallel agents return together, so there is no wait step to
manage (a backgrounded panel is what tempts the loop to reach for `Monitor`, which then fails with
`Invalid tool parameters`). Give each: the original section, your current rewrite, the target file
path (so it can grep the rest of the document), and the path to `compression-rules.md`. Each returns
structured findings — `severity` (`blocker` | `major` | `minor`), an evidence quote, and a suggested
fix.

- **L1 — Precision & scope-loss:** what instruction, scope qualifier, or intent does the rewrite
  drop or weaken under a *literal* reading?
- **L2 — Contract-token integrity:** is every parsed identifier preserved verbatim, or was one
  paraphrased?
- **L3 — Whole-document coherence:** reading the full file, do cross-refs to/from this section
  still resolve, and does surrounding prose still hold?
- **L4 — Rule-compliance & readability:** does it actually follow the Do/Don't, and is it
  genuinely denser rather than just reshuffled or newly ambiguous?

A panel beats one reviewer because each lens catches a failure mode the others are blind to (a
beautifully dense rewrite can still silently drop a `§`-anchor another section cites).

### 4. Fix pass

You (main loop) triage the pooled findings and produce the next version. For every `blocker` /
`major`: fix it, or record one line on why it's a false positive. `minor`s are judgement. Keep a
running changelog of what you cut and what you kept verbatim and why.

### 5. Loop

Re-run the panel on the new version. **Exit when a clean round (zero `blocker`/`major`) or after
3 rounds.** If blockers remain at round 3, **stop and report them** — do not ship a rewrite that
lost precision. Surfacing "I couldn't compress this without dropping X" is a valid, useful outcome.

### 6. Final validators

Run all three validators in `references/validators.md`; **all must pass**:

- **A — contract-token superset:** no parsed token dropped vs. the baseline.
- **B — no banned shorthand:** no `w/`, `&`-as-"and", or `->`-as-"leads to" in prose.
- **C — §-anchor integrity:** referenced anchors remain a subset of defined anchors across the
  whole file (the rewrite neither dangles a reference nor deletes a cited anchor).

If a validator fails, fix and re-run. If it cannot pass without losing meaning, report that.

### 7. Propose

Present, in this order:

1. The final rewrite, in a fenced block ready to paste.
2. **Token delta** — before → after (words/chars), percent reduction.
3. **Validator results** — A/B/C, pass or fail each.
4. **Changelog** — what was cut; what was deliberately kept verbatim and why; any finding you
   judged a false positive.

Then offer to apply it. **Applying is a separate step the user authorises** — this skill stops at
the proposal.

## When NOT to compress

- The section is already minimal (the panel finds nothing low-signal to cut) — say so and propose
  nothing rather than reshuffling for its own sake.
- Closed-set vocabulary definitions, exemplars, and worked examples are precision-critical by
  construction (see `compression-rules.md`) — flag them and leave them intact.

## Orchestration notes

- Default to the `Agent` tool for the panel — it is always available. If the user has
  Workflow/ultracode enabled, you *may* run the panel+loop as a workflow, but it is not required.
- **Foreground, not background; never poll.** Run the panel as parallel *foreground* `Agent` calls
  (per §3) and wait for them to return — don't `run_in_background` the lenses or call `Monitor` to
  wait on them. Foreground parallel agents return together; reaching for `Monitor` is what throws the
  `Invalid tool parameters` you may have seen mid-run.
- This skill is **not** part of the published `ctrl-freaq` plugin (it lives in the repo's
  `.claude/skills/`), so it cannot use `subagent_type: "ctrl-freaq:…"`. Use `general-purpose`
  reviewers and reference its own bundled files by relative path.
