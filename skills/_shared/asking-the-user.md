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
    where the skill gates it, a landing gate, removing a worktree, pushing with reds —
    except the coordinator's routine close of a worktree whose PR merged (or whose
    issue closed), which is not a consent card (the branch, and any PR, stay, and the
    core's own card still gates dirty, unpushed or diverged state);
  - a decision a contract reserves to the human — a tracked open question, a deviation
    from a project doc, overriding a gate or audit blocker, a hard block, deciding
    what shipped (un-ticking or re-attributing a ticked phase
    or DoD row — the progress record the evaluator verifies against), changing what grounds
    the work (a doc-catalogue `confirm` entry: a `binding` entry added or removed, an
    `authority` changed — `doc-catalogue.md` "Keeping it current"), whether a user-facing issue's
    interface is designed before it is planned (the planner's design detour, `header: "Design"`,
    `planner/references/design-detour.md`, and its revise variant when the design a plan cites is
    unapproved or gone — both render at every rigor level). A security dismissal is a `Security` card
    only for a finding no review thread can anchor, or to collect the reason a thread accepted without one
    lacks (resolved, or by an `accept` decision reply);
    dismissing by resolving a review thread is the human's act in GitHub, never a card.
  - a resolver **review** after a non-final phase (`header: "Review"`) — a human review is required
    before the next phase builds on this one, and only the operator's **Reviewed, continue** answer
    continues the session past it, so it renders even when continuing looks like the only sensible
    answer (`resolver/references/phase-continuation.md` §4). It recommends nothing — the card asks for
    the operator's review — so its brief has no trade-off or clear/close-call line, and **Reviewed,
    continue** is listed first as the card's order, never as a recommendation ("Honest options" does
    not apply), and carries no `(Recommended)` suffix. Its four answers are **Reviewed, continue** / **Reviewed, changes required** / **Review with
    Claude** / **End session**, the third only when the pause's review posted and threads wait on a person, labelled with their count: it prints the phase-scoped
    `/ctrl-freaq:review` command and folder (in a coordinator run the coordinator starts that session on the
    operator's click) and re-asks the card without it — not a "more" re-ask, since the options change — and it
    stays off every re-raise until a new pause review posts.
  - the review tool's per-thread card (`header: "Thread"`, `review/playbooks/review-flow.md`) — each
    answer is posted at once as the operator's decision in that thread. It recommends nothing, for the
    resolver Review card's reason, so its brief has no trade-off or clear/close-call line; its options are
    fixed per item kind (no **Accept** on a security finding), the first listed as order, with no
    `(Recommended)` suffix.

  A consent card with one credible action is an honest confirm — that action +
  **Cancel** — never padded with alternatives nobody would pick.
- **A judgment card renders only when the genuine-choice test (below) leaves two or
  more options.** Left with one, announce it in one line and proceed: `Did X because Y;
  say if you'd rather Z`, naming the strongest alternative as Z and any write X made
  (which issue, which heading). The operator can still redirect from that line, and is
  not stopped to rubber-stamp it. An announced choice is **never** recorded as
  `[user decision <date>]` or as the operator's answer — the operator didn't make it.
- **An operator-task card always renders.** It hands the operator something only they
  can do outside the session — a command to run elsewhere, a checkout to move, a failed
  hook to fix — with the command printed in the same message, and offers `Done` plus
  the task's honest alternative. It is neither a consent nor a judgment card.

## The brief — what the operator reads first

It goes in the card's `question`, in every session: the user may see only the question
dialog, and text printed earlier in the turn can be out of view while it is open. Plain
words, no pipeline vocabulary left undecoded (SOFT/HARD, seam, `closes-dod`, plan-settled,
tracker — say what each means here). It covers:

1. **What is being decided**, in one sentence.
2. **Why now** — the one piece of evidence that raised it.
3. **What differs for the end user** (the product's users, not the pipeline) on each
   path — or say "no user-visible difference — this only affects <X>" (the PR, the
   board, the plan's history).
4. **The trade-off** between the recommended path and the strongest alternative; on an
   honest confirm (one action + **Cancel**), between the action and **Cancel** — what
   leaving things as they are costs.
5. **Clear call** or **close call** — so the operator knows whether to deliberate.
6. End with: `Answer "more" in Other for the evidence.`

**Layout** of the `question`'s text. One labelled line per element, a blank line between them, never one run-on
paragraph: the operator scans a card, they don't read it. Wrap any line that runs past
about two sentences into the next element or cut it.

```text
Decide: <what is being decided>

Why now: <the evidence>

For users: <what differs, or "no difference — this only affects <X>">

Trade-off: <recommended path vs the strongest alternative>

Close call. Answer "more" in Other for the evidence.
```

The card's `question` renders as plain text — markdown (`*`, `**`, backticks) shows
literally — so text that goes *in* a card carries no markdown; line breaks are its only
formatting.

Each option's `description` says what that choice does **here**, in this instance — not
the option's generic definition.

**Supporting blocks.** Whether something is too large for the card is decided by its
shape, not its length: the `question` carries the brief's labelled lines, plus any short
plain-text line a card's own definition puts there (the `Review` card's opening line, trigger, phase link, file paths and pause-review link with its status, the `Polish` card's proposal tally, the revise confirm's classification).
Anything else a site shows for the decision — a digest, a table, a diff, a code excerpt, a
list of links, a multi-block confirm (the `Polish` digest, the revise confirm's blocks,
the review-notes block, the slicer's summary table, setup's diffs) — is a **supporting
block**. It prints in the same message as the card, before it, and the `question` names
it ("the Polish digest above"); a card raised again in a later message (a follow-on card, a re-raise, a re-ask after "more" or discussion) prints its block again with it. A block never replaces a brief line: with one, the `question` still carries every
labelled line its card's brief carries, so the decision can be made from the card alone —
the Decide line (the `Thread` card's `What to decide:`), the Trade-off line and the
clear/close-call line included, except on a card that recommends nothing, whose brief has
neither of the last two ("Honest options").

On a card with several questions, each question carries its own brief: each is its own
decision, and the dialog shows one question at a time.

## More context — when the operator asks

When the operator answers "more", "why", "I don't understand" or similar in Other,
render the evidence the brief summarised: the code excerpt, the doc or plan bullet
cited, the reviewer finding verbatim, and what each option does downstream (the next
session it starts, the PR or issue it changes). The evidence prints in the same message as the re-asked card, before it, in every session, after the card's supporting blocks, which print again ("Supporting blocks"). Then re-ask **the same card** — same
header, same options. Never answer it for them, narrow it, or swap in a different
question: the brief was the summary, this is the detail behind it.

## Honest options — the genuine-choice test

- **Each option needs a "pick this when …"** that a reasonable operator could satisfy
  in *this* instance. An option that fails is dropped, not shown — if that leaves one,
  the consent/judgment rule above decides between a confirm and an announce line.
- **The recommendation is reasoned from this instance's evidence**, goes first as
  option 1, and its `label` ends ` (Recommended)`; the in-card Trade-off line says why.
  The test is the brief: on a card offering two or more real actions, a Trade-off line
  means option 1 is marked, no Trade-off line means nothing is. A card that recommends nothing (the resolver's `Review` card, the
  review tool's `Thread` card, setup's `Fidelity` card — how strictly builds are held to a design is the owner's
  preference, #241 — the retry ladder's equally weighted paths) has no Trade-off or
  clear/close-call line in its brief and lists its first option as order only, with no suffix. A consent card's fixed safe action (Cancel, Abort)
  is never marked, and an honest confirm (one action + **Cancel**) carries no suffix:
  there is no alternative to recommend over. The suffix is applied at render time, only
  when this instance recommends — never written into a skill's option spec — and the
  picked label comes back with it, so read an answer with the suffix stripped.
- A fixed per-card default is allowed only as a consent card's safe action (Cancel,
  Abort) — a recommendation that would be the same whatever the evidence is not a
  judgment.

## Card shape

- One decision per question. `header` ≤ 12 chars (e.g. "Post plan", "Merge mode").
- 2–4 options. Each `label` is the action in imperative form ("Post it", "Squash",
  "Approve"); a reasoned recommendation adds its suffix per "Honest options".
- The tool always appends an "Other" free-text choice, so don't pad to four options
  with a catch-all — leave room for the user to type a custom answer, *more* included.
- `multiSelect: true` only when the choices genuinely combine (rare here).
- Ask once, act on the answer. Don't re-state the same gate in prose afterwards.

When the candidate paths aren't fixed (e.g. "which of these issues did you mean?"),
generate the options dynamically from what you found. When the answer is inherently
open-ended (e.g. "paste any external doc URLs"), a prose ask is still fine — don't
force it into options.

## Relayed cards — when a coordinator started the session

A stage session started by the `coordinator` (its opening brief says so), or a review session it started, is not the only place the
operator reads its cards. The host mirrors the card, and the text printed with it, into the
coordinator's chat. The operator may answer in either session, and the card blocks as usual until
they do. The brief already rides in the card, and its supporting blocks and the evidence for "more" print in the same message ("The brief", "More context"), so the mirror carries them. Two things differ here:

- **The identity line.** The `question` opens with `<stage> · #<N> · <step>` (e.g.
  `resolver · #152 · finalisation`; in a review session the coordinator started for a resolver's Review
  card, `review · #<N> · thread <key>`, or `review · #<N> · <step>` for a card no thread owns) on a line
  of its own, then a blank line, before the brief: the operator there has not followed this session.
  Only a session the coordinator started carries it.
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
`decision` as one `AskUserQuestion` card with its brief in its question) for what the main loop does
with one. A script's `decision` carries `summary` and `context`, not a brief: write
the brief from them and the facts block.
