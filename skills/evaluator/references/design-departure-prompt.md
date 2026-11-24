# Departure classification sub-agent prompt (is each design difference one the build already explained?)

Dispatched by the one design-check procedure ([`../../_shared/design-check.md`](../../_shared/design-check.md) step
3.2, #241) right after the design comparison ([`design-comparison-prompt.md`](design-comparison-prompt.md)), by
whichever caller ran it: the resolver, at the plan's build-check phase
([`../../resolver/references/phase-continuation.md`](../../resolver/references/phase-continuation.md) §2 and §2.2), and
the evaluator, at the gated head ([`review-threads.md`](review-threads.md) §1 step 7). It lives in the evaluator's
references beside the comparison, because the gate is the evaluator's. This `Explore`-type judgment sub-agent
(architecture.md §8) reads each difference the comparison reported and says whether one of the explanations clearly
describes it. `scripts/parse.py` `scan_design_classification` reads its verdict, and `review_threads.py` posts every
explained difference on one summary thread the operator approves once and every other as a gating design thread.

The orchestrator fills the two `<<...>>` placeholders with the files `review_threads.py explain` wrote (design-check
step 3.1): `<<differences_path>>` with its `design-differences.md` and `<<explanations_path>>` with its
`design-explanations.md`, as absolute paths. **Pass nothing else**: not the captures, the design record, the plan, the
diff, the build session's notes on what it changed, the comparison's reasoning, earlier verdicts, review threads or
replies, or any conversation turns. The explanations file already holds every explanation this run may use, each
from a source the script trusted; a classifier shown the build's intent would read every difference as intended.
It runs only when both files list at least one entry: otherwise `explain` writes the all-unexplained verdict itself. The sub-agent is context-blind, cannot call `AskUserQuestion`,
and never writes to GitHub or the workspace.

---

You are deciding, for each way a built product differs from its approved design, whether a recorded explanation
already accounts for it. You do not judge whether the build or the design is right, and you do not look at the
product. Do not edit anything.

## Inputs

- **Differences**: `<<differences_path>>` — under `## Differences`, one line per difference:
  `` - `<state-id>#<n>` · <aspect> — <what the build shows differently> ``. The aspect is `flow`, `state`, `copy`,
  `layout`, or `unknown` when the comparison named none.
- **Explanations**: `<<explanations_path>>` — under `## Explanations`, one line per explanation:
  `- <id> · <states> · <aspect> — <what it says>[ — reason: <why>]`. The id is `D<n>` (a departure the build session
  declared, with the reason found after the design), `U<n>` (a deviation from the design the operator agreed in the
  plan) or `X<n>` (a deviation from the plan the operator decided during the build). `<states>` lists the state ids it
  concerns, or `any`; `<aspect>` is the aspect it concerns, or `any`.

Read the two files with the `Read` tool. Do not read any other file, and run nothing.

## What to classify

If either file is missing, empty or unreadable, return the single line `code: AMBIGUOUS` under `## Classification`
plus one sentence naming what was missing, and stop.

Otherwise classify **every** difference, by its id exactly as listed, as one of:

- **explained** — one or more explanations clearly describe this difference: what the explanation says the build does
  is what the difference says the build shows. Cite each explanation that does, by its id.
- **unexplained** — anything else: no explanation describes it, one only touches the same state or the same area,
  one describes a different change, or you cannot tell.

Doubt is unexplained. An unexplained difference gets its own thread, which costs the operator one look; a wrong
`explained` puts it in a summary the operator approves with others, where it can pass unexamined. Judge each
difference on its own: an explanation that covers one difference in a state does not cover the others there.

You need not check whether an explanation's states or aspect match the difference: the script that reads your verdict
does that for each one you cite (a `D` must name the difference's state and aspect, a `U` its state, and an `X` counts
by existing), and reads a citation that fails it as unexplained. Your judgment is only whether the explanation's
text describes the difference.

## Output format

Return a markdown verdict, nothing else:

```
## Classification
- `<state-id>#<n>` · explained — <id>[, <id>…]
- `<state-id>#<n>` · unexplained
```

- One line per listed difference, its id exactly as listed and backticked.
- An `explained` line ends with the ids it cites, comma-separated, and nothing after them: no evidence, no prose.
- An `unexplained` line ends at `unexplained`, citing nothing.
- A difference you leave out, classify twice with different classes, or write in any other shape is read as
  unexplained.
- Or, when an input was missing, `code: AMBIGUOUS` and one sentence; then every difference is unexplained.
