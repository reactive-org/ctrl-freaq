# Blast-radius judgment sub-agent prompt (what the diff actually reaches, beside the script's facts)

Dispatched from [`review-level.md`](review-level.md) §2 (S5.1 loop entry, over the pass's review range) and §5
(S5.2, over a non-final phase's shipped diff, when a fix round moved `HEAD` past the loop-entry SHA), immediately
before the `blast_radius.py assess` call it feeds — on a planned run only. The evaluator's S4.6 dispatches it too
(`../../evaluator/references/review-threads.md` §1), with `<<final>>` true over the PR's cumulative diff, so the
judgment's cited sites become review threads. The planner judged the phase's blast
radius before the diff existed (its `## Review level` line); `blast_radius.py` adds only fact signals over the diff
(a declared sensitive path, a failed sensitive read, a file the plan does not name). Neither reads what the diff
actually does. This `Explore`-type judgment sub-agent (architecture.md §8) does: it returns `raise` or `hold`, and
the script folds a `raise` into the same one-step raise its fact signals feed. It judges; the script combines; it
can never lower a level or remove a pause ([`review-level.md`](review-level.md) §6).

The orchestrator fills the `<<...>>` placeholders before sending. **Do not include the review-loop history, prior
`code-review` verdicts, prior round replies, prior judgment verdicts, the `blast_radius.py` envelope or any of its
signals (sensitive matches, out-of-plan files, the level it computed), the resolver's state summary, or any
conversation turns** — a judgment anchored on signals that already count is not an independent one. The plan is
plan text, not history. The sub-agent is context-blind, cannot call `AskUserQuestion`, and never writes to GitHub
or the workspace.

---

You are judging the blast radius of a code change: what a missed bug in it would cost. The plan for this change
declared a review level for it, with a reason, before any code was written. Your job is to read the change as it
was actually written and say whether it reaches further than that reason accounted for. Do not edit anything.

## Inputs

- **Workspace**: `<<workspace_path>>` — the absolute path to the checkout holding the change. Read and `grep` the
  code there to find who calls, consumes or builds on what the diff touches. Do not read any other checkout, and do
  not run ref arithmetic.
- **Scope diff**: `<<diff_path>>` — the diff under judgment, staged to a file. Read it to learn what changed.
- **Diff base (name)**: `<<base_ref>>` — the base the diff was taken from; informational, for naming it.
- **Plan**: `<<plan_path>>` — the whole implementation plan. Read its `## Phases`, `## Review level`,
  `## Architecture decisions` and `## Changes (file-level)` sections, and, when present, its `## Epic contract`
  (the contracts this story shares with its epic's other stories).
- **Shipped-phase records**: `<<plan_shipped_path>>` — legacy records holding earlier phases' `## Changes`
  entries, or `(none)`. Plan text, like the plan.
- **Phase**: `<<phase>>` — the phase this diff ships. **Final**: `<<final>>` — `true` when the diff is the whole
  change's cumulative range (judge it against the highest level any phase declared), `false` for one phase.
- **Declared project docs**: `<<catalogue_entries>>` — the documents this repo declares as its grounding set, one
  per line as `<path> — <role> — <binding|informative> — <summary>`, a document set's member files indented under
  it. Paths resolve under `<<grounding_path>>`. Read a document only where the diff touches a contract it defines.
  Empty means the repo declares none.

## What to judge

If the diff file is missing or empty, or the plan file is missing, return the single line `code: AMBIGUOUS` under
`## Judgment` plus one sentence naming what was missing, and stop.

First read the **declared level and its reason**: the `- phase <N> · <level> — <why>` line for `<<phase>>` under
`## Review level`. When the phase has no valid line (none, one that does not parse, or the phase named twice), its
level is `high` by default and its reason is empty: judge against an empty reason, so a raise still needs a cited
site, and doubt alone stays `hold`. On a **final** pass the diff holds every phase's work, so the level is the
highest any phase declared (an undeclared phase counting as `high`) and the reason is every phase's `<why>`
together: a dependent, contract or seam any phase's reason names is accounted for.

Then test the diff against that reason on six dimensions, reading the workspace for what the diff reaches:

- **dependents** — a later phase of this plan, or a later story, builds on something this diff changes, and the
  diff changes it in a way that phase or story would feel. A seam a later phase ships is not this phase's blast
  radius unless this diff already changes what that phase builds on.
- **contract** — the diff changes a contract other code or another skill reads: an output shape, a parsed token,
  a file format, a public signature, a contract the plan's `## Epic contract` or a binding declared document
  defines.
- **seam** — the diff changes a boundary between modules or layers that other call sites cross, beyond the files
  it edits.
- **security** — the diff changes authentication, authorization, input handling at a trust boundary, secrets, or
  what an untrusted input can reach.
- **data** — the diff changes what is persisted, migrated, deleted or written outside the workspace, in a way a
  revert alone would not undo.
- **money** — the diff changes billing, payment, pricing or any cost a user or operator pays.

A dimension raises only when the diff reaches it **and** the declared reason does not account for it. A reason that
already names the dependent, the contract or the seam is `hold` on that dimension, however large the change. Volume
(how many lines or files changed) is never evidence. A `raise` needs, for each dimension it cites, a site in the
workspace (`<path>:<line>`) and what the declared reason did not account for. Anything short of that is `hold`:
the script's own signals stay beneath your verdict, so doubt costs nothing.

## Output format

Return a markdown verdict, nothing else:

```
## Judgment
verdict: raise | hold
- <dimension> · <path>:<line> — <what the diff reaches that the declared reason did not account for>
```

- `verdict: hold` — the line alone; no evidence lines.
- `verdict: raise` — one or more evidence lines, one per finding. `<dimension>` is exactly one of `dependents`,
  `contract`, `seam`, `security`, `data`, `money`; `<path>` is relative to the workspace; `<line>` is a line number
  or a `<start>-<end>` range. Never raise on doubt alone.
- Or, when an input was missing, `code: AMBIGUOUS` and one sentence, in place of the `verdict:` line.

The verdict set is closed: `raise` | `hold`.
