# Asking the user a decision — shared reference

When you need a decision from the user — an approval gate, a choice between named
paths, or a confirmation before a GitHub write — ask it through the `AskUserQuestion`
tool, not as freeform prose. The tool renders the same multiple-choice card every
time, so the user pattern-matches the decision at a glance instead of re-parsing a
differently-worded question on each run.

A card is only as good as the operator's grasp of it. Cards asked with no context drew
"I need more context", "I don't understand the question" and "how does this affect the
end user?", and cards whose recommended option was the only real one gave the
appearance of choice without any. The four rules below exist to stop both.

## Consent vs judgment — whether a card renders at all

- **A consent card always renders.** It is one of a closed set:
  - an irreversible or outward-facing act — a merge, closing a PR, filing or posting
    where the skill gates it, a landing gate, removing a worktree, pushing with reds;
  - a decision a contract reserves to the human — a tracked open question, a deviation
    from a project doc, overriding a gate or audit blocker, a hard block, deciding
    what shipped (un-ticking or re-attributing a ticked phase
    or DoD row — the progress record the evaluator verifies against), changing what grounds
    the work (a doc-catalogue `confirm` entry: a `binding` entry added or removed, an
    `authority` changed — `doc-catalogue.md` "Keeping it current").
  - a resolver **look** after a non-final phase (`header: "Look"`) — only the operator's
    answer continues the session past a pause, so it renders even when continuing looks like the
    only sensible answer (`resolver/references/phase-continuation.md` §4).

  A consent card with one credible action is an honest confirm — that action +
  **Cancel** — never padded with alternatives nobody would pick.
- **A judgment card renders only when the genuine-choice test (below) leaves two or
  more options.** Left with one, announce it in one line and proceed: `Did X because Y;
  say if you'd rather Z`, naming the strongest alternative as Z and any write X made
  (which issue, which heading). The operator can still redirect from that line, and is
  not stopped to rubber-stamp it. An announced choice is **never** recorded as
  `[user decision <date>]` or as the operator's answer — the operator didn't make it.

## The brief — what the operator reads first

Print it in the message that carries the card, before the card: about five lines, plain
words, no pipeline vocabulary left undecoded (SOFT/HARD, seam, `closes-dod`,
plan-settled, tracker — say what each means here). It covers:

1. **What is being decided**, in one sentence.
2. **Why now** — the one piece of evidence that raised it.
3. **What differs for the end user** (the product's users, not the pipeline) on each
   path — or say "no user-visible difference — this only affects <X>" (the PR, the
   board, the plan's history).
4. **The trade-off** between the recommended path and the strongest alternative.
5. **Clear call** or **close call** — so the operator knows whether to deliberate.
6. End with: `Answer *more* (in Other) for the evidence.`

The card's `question` points at the brief and never restates it. Each option's
`description` says what that choice does **here**, in this instance — not the option's
generic definition. A site that already prints a richer block before its card (the
`Polish` digest, the revise confirm's blocks, the review-notes block, the slicer's
summary table, setup's diffs) counts as the brief: add only what it lacks — the
end-user line, clear/close call, the *more* hint.

## More context — when the operator asks

When the operator answers "more", "why", "I don't understand" or similar in Other,
render the evidence the brief summarised: the code excerpt, the doc or plan bullet
cited, the reviewer finding verbatim, and what each option does downstream (the next
session it starts, the PR or issue it changes). Then re-ask **the same card** — same
header, same options. Never answer it for them, narrow it, or swap in a different
question: the brief was the summary, this is the detail behind it.

## Honest options — the genuine-choice test

- **Each option needs a "pick this when …"** that a reasonable operator could satisfy
  in *this* instance. An option that fails is dropped, not shown — if that leaves one,
  the consent/judgment rule above decides between a confirm and an announce line.
- **The recommendation is reasoned from this instance's evidence**, goes first as
  option 1, and the brief's trade-off line says why. A fixed per-card default is
  allowed only as a consent card's safe action (Cancel, Abort) — a
  recommendation that would be the same whatever the evidence is not a judgment.

## Card shape

- One decision per question. `header` ≤ 12 chars (e.g. "Post plan", "Merge mode").
- 2–4 options. Each `label` is the action in imperative form ("Post it", "Squash",
  "Approve") — no "(recommended)" suffix; the brief carries the recommendation.
- The tool always appends an "Other" free-text choice, so don't pad to four options
  with a catch-all — leave room for the user to type a custom answer, *more* included.
- `multiSelect: true` only when the choices genuinely combine (rare here).
- Ask once, act on the answer. Don't re-state the same gate in prose afterwards.

When the candidate paths aren't fixed (e.g. "which of these issues did you mean?"),
generate the options dynamically from what you found. When the answer is inherently
open-ended (e.g. "paste any external doc URLs"), a prose ask is still fine — don't
force it into options.

## Relayed cards — when a coordinator started the session

A stage session started by the `coordinator` (its opening brief says so) is not the only place the
operator reads its cards. The host mirrors the card, and the text printed with it, into the
coordinator's chat. The operator may answer in either session, and the card blocks as usual until
they do. The operator there has not followed this session, so the card must carry its own context:

- **The card is self-contained.** Its `question` opens with an identity line —
  `<stage> · #<N> · <step>` (e.g. `resolver · #152 · finalisation`) — and then the brief. This
  overrides "the card's `question` points at the brief" for this session only: here the brief goes
  *in* the card, not just before it.
- **A block that counts as the brief** (the `Polish` digest, the revise confirm's blocks, the slicer's
  summary table, setup's diffs) is printed in the **same message** as the card, so the mirror carries
  it. The card's question names it ("the Polish digest above").
- **"more"** prints the evidence in the same message as the re-asked card — same header, same
  options — for the same reason.
- **An announced choice** (`Did X because Y; say if you'd rather Z`) raises no card, so nothing
  mirrors it. Put it in the next milestone update to the coordinator, word for word, so the operator
  can still redirect it.

The coordinator never answers a mirrored card itself. An answer typed in either session is the
operator's, and is recorded as `[user decision <date>]` like any other.

## Sub-agents never ask

`AskUserQuestion` is not available inside a sub-agent spawned via the `Agent` tool.
Any gate that arises during sub-agent work must be surfaced by the sub-agent
returning a structured "decision needed" signal to this main loop, which asks the
user and re-dispatches with the answer. Never tell a sub-agent to call
`AskUserQuestion` itself. One closed vocabulary spans scripts and sub-agents:
[architecture.md §3](../../docs/architecture.md)'s decision-code set — the single
source of truth for the typed codes, and the router's universal rule (render a
`decision` as one `AskUserQuestion` card with its brief) for what the main loop does
with one. A script's `decision` carries `summary` and `context`, not a brief: write
the brief from them and the facts block.
