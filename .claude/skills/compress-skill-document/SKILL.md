---
name: compress-skill-document
description: >-
  Compress (densify / token-reduce) an ENTIRE prompt file in this repo — a `skills/*/SKILL.md`, an
  `agents/*.md`, or a `skills/_shared/*.md` — section by section, WITHOUT losing precision, then
  propose the full rewritten document for you to apply. This is the whole-file orchestrator built on
  top of `compress-skill-section`: it chunks the document adaptively, compresses each chunk
  sequentially (each chunk aware of the already-compressed earlier ones, so cross-section repetition
  can be deduped), runs the full adversarial review→fix panel per chunk, then does a whole-document
  reconciliation pass. Use whenever the user wants to compress, shrink, densify, tighten, trim, or
  token-optimize a WHOLE file or MULTIPLE sections — phrasings like "compress the whole resolver
  skill", "densify the entire SKILL.md", "token-optimize agents/github-ops.md end to end", "shrink
  the whole document", "run the compression rules across the whole file", or "the whole file is too
  long" all qualify. For a SINGLE named section or §-anchor, use `compress-skill-section` instead.
  Propose-only: it never edits the target file — it assembles the proposed document to a scratch
  file and reports per-chunk + overall token deltas, coherence findings, and validator results. Do
  NOT use for compressing images/binaries, summarizing, translating, code review, or adding sections.
model: opus
---

# Compress a whole skill document without losing precision

The whole-file orchestrator on top of [`compress-skill-section`](../compress-skill-section/SKILL.md).
It chunks the document, compresses each chunk **sequentially** (so a later chunk can lean on — and
dedupe against — the already-compressed earlier ones), runs the **full 4-lens adversarial panel per
chunk**, then a **whole-document reconciliation pass** that catches what per-chunk work can't:
forward-reference drift, cross-section duplication, broken transitions. The win condition is the
sibling's, scaled to a file: **denser AND precision-preserved AND document-coherent** — never just
fewer tokens.

## Hard rules

- **Propose-only.** Never `Edit`/`Write` the target file. Assemble the proposed document to a
  scratch file and report deltas/findings; the user applies it as a separate, explicit step. Why: a
  whole-file rewrite is high-stakes; the human owns the merge, and reviewing a proposal is cheaper
  than reverting a bad sweep.
- **Single source of truth — reuse, don't fork.** The compression rules, the four review lenses,
  and the validators live in the sibling skill. Read them; do not restate or diverge from them:
  - Rules: `.claude/skills/compress-skill-section/references/compression-rules.md`
  - Lenses: `.claude/skills/compress-skill-section/references/review-lenses.md`
  - Validators: `.claude/skills/compress-skill-section/references/validators.md`
- **Foreground parallel panel; no polling.** Within a chunk, launch the four lenses as **parallel
  foreground `Agent` calls in one message** and let all four return before the fix pass. Do **not**
  run them with `run_in_background`, and do **not** call `Monitor`/poll — foreground parallel agents
  return together. (A backgrounded panel is what makes the loop reach for `Monitor` and fail.)
- **This skill isn't in the published plugin** (it lives in `.claude/skills/`), so use
  `general-purpose` reviewers, not `subagent_type: "ctrl-freaq:…"`.

## Inputs

- **Target file** — a `skills/*/SKILL.md`, `agents/*.md`, or `skills/_shared/*.md`.
- **Optional** — an overall token-reduction target ("~30%"), and any sections to force-include or
  force-skip.

## Workflow

### 1. Build the chunk plan, then get approval

- Read the whole file. Compute the chunk plan per
  [`references/chunking.md`](references/chunking.md): descend oversized top-level sections into their
  `###`/§N subsections, skip or batch tiny ones, and skip precision-critical ones (closed-set vocab,
  worked examples, pure-data tables, code blocks). Target ~40–120 line units.
- **Present the plan as a table and wait for approval** before compressing anything: `# | heading
  path | lines | size | action (compress | skip:<reason> | batch)`. Why: chunking is judgement,
  it's cheap to confirm, and it lets the user trim scope before a long run and veto compressing a
  section that must stay verbatim.

### 2. Compress each chunk — sequential, full panel

Keep a **working copy** of the document on disk (e.g. `/tmp/csd-<name>/working.md`), starting as the
original. Compress chunks in document order; after each finalizes, splice it into the working copy so
the next chunk sees it.

For each `compress` chunk:

1. **Draft** a denser version per `compression-rules.md`, using the **working copy** as dedup
   context — if this chunk restates something an *already-compressed* earlier chunk now states
   tersely, lean on that (e.g. "per §N") instead of repeating it.
2. **Panel** — launch the full four lenses from `review-lenses.md` as parallel foreground agents.
   Pass each: the chunk's original text, your rewrite, **the working-copy path** (so L3 coherence
   sees the evolving document), and the rules path.
3. **Fix** per pooled findings; **loop** until a clean round (zero blocker/major) or 3 rounds — same
   exit as the sibling. If blockers remain at round 3, leave that chunk uncompressed and note it.
4. **Validate** the chunk (validators A + B) and record its token delta + a one-line changelog.
5. **Splice** the finalized chunk into the working copy.

If a token target was given, track the running cumulative delta and report progress as you go.

### 3. Whole-document reconciliation

After every chunk, run the reconciliation pass per
[`references/reconciliation.md`](references/reconciliation.md) over the fully assembled working copy
vs. the original: cross-section coherence (every cross-reference/§-anchor still resolves, shared
setup/definitions intact, transitions read right), cross-section dedup the sequential pass missed
(forward-references), and the **document-level validators** (A + B + C over the whole file). Apply
targeted fixes for any blocker/major and re-run until clean.

### 4. Propose

- The reconciled working copy **is** the proposed document — leave it at its scratch path (e.g.
  `/tmp/csd-<name>/proposed.md`); do not write the original.
- Report: **overall token delta** (whole file, %), a **per-chunk summary table** (heading,
  before→after, %, skipped/why), **reconciliation findings**, and **validator results** (A/B/C
  pass). Then offer to apply — the user authorises that as a separate step.

## Cost — say it plainly

This configuration (adaptive chunks × strict sequential × full panel per chunk) is the most thorough
and the most expensive setting. A large file is ~15–25 chunks, and each chunk is draft + 4 lenses ×
up to 3 rounds + fix, run **sequentially** — easily 100+ agent calls and a long wall-clock. The
step-1 approval gate is where you cut scope: skip low-value sections, or compress only the few that
dominate the token count. Surface the estimated chunk count and rough agent budget at the plan stage
so the user can decide before it spends.

## When NOT to run

- A single named section or §-anchor → use `compress-skill-section` (this skill is for whole/
  multi-section files).
- An already-minimal file, or one that's mostly closed-set vocab / examples / tables — say so and
  propose nothing rather than churning.
